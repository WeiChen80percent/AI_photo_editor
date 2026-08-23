"""Train the paired sRGB Expert C model with the paper's exact hyperparameters."""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader

from .checkpoints import atomic_torch_save
from .data import FiveKExpertCDataset, seed_worker
from .losses import PairedLUTLoss
from .metrics import MetricAccumulator, calculate_metrics, save_comparison
from .model import ImageAdaptive3DLUT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--train-manifest", default="image_adaptive_3dlut/manifests/train.jsonl"
    )
    parser.add_argument(
        "--val-manifest", default="image_adaptive_3dlut/manifests/public_dev.jsonl"
    )
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-dir", default="image_adaptive_3dlut/runs/paper_expert_c")
    parser.add_argument("--epochs", type=int, default=400)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--beta1", type=float, default=0.9)
    parser.add_argument("--beta2", type=float, default=0.999)
    parser.add_argument("--lambda-smooth", type=float, default=1e-4)
    parser.add_argument("--lambda-monotonicity", type=float, default=10.0)
    parser.add_argument("--short-side", type=int, default=480)
    parser.add_argument("--num-workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", default="")
    parser.add_argument("--train-limit", type=int, default=0)
    parser.add_argument("--val-limit", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--eval-every", type=int, default=1)
    # The official code saves every epoch. Ten preserves periodic history while
    # avoiding ~2.7 GB of duplicate checkpoints on this storage-constrained PC.
    # latest.pt and best.pt are still updated every epoch.
    parser.add_argument("--save-every", type=int, default=10)
    parser.add_argument("--cpu", action="store_true")
    return parser


def set_reproducibility(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def validate_args(args: argparse.Namespace) -> None:
    if args.batch_size != 1:
        raise ValueError("Paper-faithful training requires --batch-size 1")
    if args.epochs < 1 or args.learning_rate <= 0:
        raise ValueError("epochs and learning rate must be positive")
    if args.eval_every < 1 or args.save_every < 1:
        raise ValueError("eval-every and save-every must be positive")
    if args.short_side != 480:
        raise ValueError("Paper-faithful FiveK training requires --short-side 480")
    if "hidden" in str(args.val_manifest).lower():
        raise ValueError("Hidden data cannot be used for validation or checkpoint selection")


@torch.no_grad()
def evaluate(
    model: ImageAdaptive3DLUT,
    loader: DataLoader,
    device: torch.device,
    preview_path: Path | None = None,
) -> dict[str, float | int]:
    model.eval()
    accumulator = MetricAccumulator()
    for index, batch in enumerate(loader):
        source = batch["input"].to(device, non_blocking=True)
        target = batch["target"].to(device, non_blocking=True)
        prediction, _ = model(source)
        accumulator.update(calculate_metrics(prediction, target))
        if index == 0 and preview_path is not None:
            save_comparison(preview_path, source, prediction, target)
    return accumulator.averages()


def checkpoint_payload(
    model: ImageAdaptive3DLUT,
    optimizer: torch.optim.Optimizer,
    args: argparse.Namespace,
    *,
    epoch: int,
    global_step: int,
    best_psnr: float,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "paper": "Learning Image-adaptive 3D Lookup Tables for High Performance Photo Enhancement in Real-time",
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "epoch": epoch,
        "global_step": global_step,
        "best_psnr": best_psnr,
        "num_luts": model.num_luts,
        "lut_dim": model.lut_dim,
        "args": vars(args),
    }


def main() -> None:
    args = build_parser().parse_args()
    validate_args(args)
    set_reproducibility(args.seed)
    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    output_dir = Path(args.output_dir)
    checkpoints_dir = output_dir / "checkpoints"
    previews_dir = output_dir / "previews"
    output_dir.mkdir(parents=True, exist_ok=True)

    train_dataset = FiveKExpertCDataset(
        args.train_manifest,
        args.data_root,
        train=True,
        resolution="480p",
        short_side=args.short_side,
        limit=args.train_limit,
        subset_seed=args.seed,
    )
    val_dataset = FiveKExpertCDataset(
        args.val_manifest,
        args.data_root,
        train=False,
        resolution="480p",
        short_side=args.short_side,
        limit=args.val_limit,
        subset_seed=args.seed,
    )
    generator = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(
        train_dataset,
        batch_size=1,
        shuffle=True,
        num_workers=args.num_workers,
        pin_memory=device.type == "cuda",
        worker_init_fn=seed_worker,
        generator=generator,
        persistent_workers=args.num_workers > 0,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=1,
        shuffle=False,
        num_workers=args.num_workers,
        pin_memory=device.type == "cuda",
        worker_init_fn=seed_worker,
        persistent_workers=args.num_workers > 0,
    )

    model = ImageAdaptive3DLUT().to(device)
    criterion = PairedLUTLoss(
        lambda_smooth=args.lambda_smooth,
        lambda_monotonicity=args.lambda_monotonicity,
    ).to(device)
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=args.learning_rate,
        betas=(args.beta1, args.beta2),
    )
    start_epoch = 1
    global_step = 0
    best_psnr = -math.inf
    if args.resume:
        payload = torch.load(args.resume, map_location=device, weights_only=False)
        saved_args = payload.get("args", {})
        critical_keys = (
            "train_manifest",
            "val_manifest",
            "data_root",
            "batch_size",
            "learning_rate",
            "beta1",
            "beta2",
            "lambda_smooth",
            "lambda_monotonicity",
            "short_side",
            "seed",
            "train_limit",
            "val_limit",
        )
        mismatches = {
            key: (saved_args[key], getattr(args, key))
            for key in critical_keys
            if key in saved_args and saved_args[key] != getattr(args, key)
        }
        if mismatches:
            raise ValueError(f"Resume configuration differs from checkpoint: {mismatches}")
        model.load_state_dict(payload["model"], strict=True)
        optimizer.load_state_dict(payload["optimizer"])
        start_epoch = int(payload["epoch"]) + 1
        global_step = int(payload.get("global_step", 0))
        best_psnr = float(payload.get("best_psnr", -math.inf))
    if start_epoch > args.epochs:
        raise ValueError(
            f"Checkpoint already completed epoch {start_epoch - 1}; "
            f"increase --epochs above {start_epoch - 1}"
        )

    run_config = {
        **vars(args),
        "device": str(device),
        "train_count": len(train_dataset),
        "val_count": len(val_dataset),
        "parameter_count": model.parameter_count,
        "torch_version": torch.__version__,
    }
    (output_dir / "run_config.json").write_text(
        json.dumps(run_config, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(run_config, indent=2, ensure_ascii=False))

    stop_requested = False
    for epoch in range(start_epoch, args.epochs + 1):
        model.train()
        sums = {"total": 0.0, "mse": 0.0, "smoothness": 0.0, "monotonicity": 0.0}
        samples = 0
        started = time.perf_counter()
        for batch in train_loader:
            source = batch["input"].to(device, non_blocking=True)
            target = batch["target"].to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            prediction, weights = model(source)
            values = criterion(prediction, target, model.luts, weights)
            values.total.backward()
            optimizer.step()
            global_step += 1
            samples += 1
            sums["total"] += float(values.total.detach())
            sums["mse"] += float(values.mse.detach())
            sums["smoothness"] += float(values.smoothness.detach())
            sums["monotonicity"] += float(values.monotonicity.detach())
            if args.max_steps > 0 and global_step >= args.max_steps:
                stop_requested = True
                break

        train_metrics = {name: value / max(samples, 1) for name, value in sums.items()}
        train_metrics["psnr_from_mse"] = (
            float("inf") if train_metrics["mse"] == 0 else -10.0 * math.log10(train_metrics["mse"])
        )
        record: dict[str, Any] = {
            "epoch": epoch,
            "global_step": global_step,
            "seconds": time.perf_counter() - started,
            "train": train_metrics,
        }

        val_metrics: dict[str, float | int] | None = None
        if epoch % args.eval_every == 0 or stop_requested or epoch == args.epochs:
            val_metrics = evaluate(
                model,
                val_loader,
                device,
                previews_dir / f"epoch_{epoch:04d}.jpg",
            )
            record["validation"] = val_metrics
            current_psnr = float(val_metrics["psnr"])
            if current_psnr > best_psnr:
                best_psnr = current_psnr
                atomic_torch_save(
                    checkpoint_payload(
                        model,
                        optimizer,
                        args,
                        epoch=epoch,
                        global_step=global_step,
                        best_psnr=best_psnr,
                    ),
                    checkpoints_dir / "best.pt",
                )

        payload = checkpoint_payload(
            model,
            optimizer,
            args,
            epoch=epoch,
            global_step=global_step,
            best_psnr=best_psnr,
        )
        atomic_torch_save(payload, checkpoints_dir / "latest.pt")
        if epoch % args.save_every == 0:
            atomic_torch_save(payload, checkpoints_dir / f"epoch_{epoch:04d}.pt")
        append_jsonl(output_dir / "metrics.jsonl", record)
        print(json.dumps(record, ensure_ascii=False))
        if stop_requested:
            break

    summary = {
        "completed_epoch": epoch,
        "global_step": global_step,
        "best_validation_psnr": best_psnr,
        "best_checkpoint": str(checkpoints_dir / "best.pt"),
        "latest_checkpoint": str(checkpoints_dir / "latest.pt"),
    }
    (output_dir / "result.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
