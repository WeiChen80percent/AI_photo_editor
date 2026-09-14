"""Conservatively fine-tune the histogram-conditioned global LUT model."""

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

from image_adaptive_3dlut.data import FiveKExpertCDataset, seed_worker
from image_adaptive_3dlut.metrics import MetricAccumulator, calculate_metrics, save_comparison

from .checkpoints import atomic_torch_save
from .losses import GlobalHistogramLoss
from .model import HistogramGlobal3DLUT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-manifest", default="image_adaptive_3dlut/manifests/train.jsonl")
    parser.add_argument("--val-manifest", default="image_adaptive_3dlut/manifests/public_dev.jsonl")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-dir", default="histogram_global_3dlut/runs/expert_c")
    parser.add_argument(
        "--baseline-checkpoint",
        default="image_adaptive_3dlut/trained_model/best.pt",
    )
    parser.add_argument("--resume", default="")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--head-only-epochs", type=int, default=5)
    parser.add_argument("--early-stopping-patience", type=int, default=10)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--lut-lr", type=float, default=2e-6)
    parser.add_argument("--final-classifier-lr", type=float, default=5e-6)
    parser.add_argument("--lambda-smooth", type=float, default=1e-4)
    parser.add_argument("--lambda-monotonicity", type=float, default=10.0)
    parser.add_argument("--lambda-ssim", type=float, default=0.01)
    parser.add_argument("--lambda-delta-e", type=float, default=0.01)
    parser.add_argument("--lambda-weight-delta", type=float, default=1e-3)
    parser.add_argument("--lambda-strength-anchor", type=float, default=1e-3)
    parser.add_argument("--gradient-clip", type=float, default=1.0)
    parser.add_argument("--short-side", type=int, default=480)
    parser.add_argument("--num-workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-limit", type=int, default=0)
    parser.add_argument("--val-limit", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--eval-every", type=int, default=1)
    parser.add_argument("--save-every", type=int, default=10)
    parser.add_argument("--cpu", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.epochs < 1 or not 0 <= args.head_only_epochs < args.epochs:
        raise ValueError("Require 0 <= head-only-epochs < epochs")
    if args.early_stopping_patience < 1:
        raise ValueError("early-stopping-patience must be positive")
    if min(args.head_lr, args.lut_lr, args.final_classifier_lr) <= 0:
        raise ValueError("learning rates must be positive")
    if args.short_side != 480:
        raise ValueError("The controlled experiment requires 480p training")
    if "hidden" in str(args.val_manifest).lower():
        raise ValueError("Hidden targets cannot be used for model selection")


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


def selection_score(metrics: dict[str, float | int]) -> float:
    """Balanced validation score; higher is better."""

    return (
        float(metrics["psnr"])
        + 5.0 * float(metrics["ssim"])
        - 0.05 * float(metrics["delta_e"])
    )


@torch.no_grad()
def evaluate_model(
    model: HistogramGlobal3DLUT,
    loader: DataLoader,
    device: torch.device,
    preview: Path | None,
) -> dict[str, float | int]:
    model.eval()
    accumulator = MetricAccumulator()
    strength_sum = 0.0
    delta_sum = torch.zeros(3, dtype=torch.float64)
    count = 0
    for index, batch in enumerate(loader):
        source = batch["input"].to(device, non_blocking=True)
        target = batch["target"].to(device, non_blocking=True)
        prediction, auxiliary = model(source)
        accumulator.update(calculate_metrics(prediction, target))
        strength_sum += float(auxiliary["strength"].mean())
        delta_sum += auxiliary["weight_delta"].mean(dim=0).double().cpu()
        count += 1
        if index == 0 and preview is not None:
            save_comparison(preview, source, prediction, target)
    result: dict[str, float | int] = dict(accumulator.averages())
    result["strength"] = strength_sum / max(count, 1)
    for index in range(3):
        result[f"mean_weight_delta_{index}"] = float(delta_sum[index] / max(count, 1))
    result["selection_score"] = selection_score(result)
    return result


def make_payload(
    model: HistogramGlobal3DLUT,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LRScheduler,
    args: argparse.Namespace,
    bootstrap: dict[str, Any],
    *,
    epoch: int,
    global_step: int,
    best_psnr: float,
    best_score: float,
    epochs_without_improvement: int,
) -> dict[str, Any]:
    return {
        "format_version": 1,
        "model_type": "histogram_global_3dlut",
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "epoch": epoch,
        "global_step": global_step,
        "best_psnr": best_psnr,
        "best_selection_score": best_score,
        "epochs_without_improvement": epochs_without_improvement,
        "bootstrap": bootstrap,
        "model_config": {
            "lut_dim": model.lut_dim,
            "histogram_bins": model.histogram_bins,
            "weight_delta_limit": model.weight_delta_limit,
        },
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

    model = HistogramGlobal3DLUT()
    resume_payload: dict[str, Any] | None = None
    if args.resume:
        resume_payload = torch.load(args.resume, map_location="cpu", weights_only=False)
        if resume_payload.get("model_type") != "histogram_global_3dlut":
            raise ValueError("Resume checkpoint belongs to another model")
        model.load_state_dict(resume_payload["model"], strict=True)
        bootstrap = dict(resume_payload.get("bootstrap", {}))
    else:
        bootstrap = model.initialize_from_baseline(args.baseline_checkpoint)
    model = model.to(device)
    model.set_training_stage(head_only=True)

    criterion = GlobalHistogramLoss(
        lambda_smooth=args.lambda_smooth,
        lambda_monotonicity=args.lambda_monotonicity,
        lambda_ssim=args.lambda_ssim,
        lambda_delta_e=args.lambda_delta_e,
        lambda_weight_delta=args.lambda_weight_delta,
        lambda_strength_anchor=args.lambda_strength_anchor,
    ).to(device)
    optimizer = torch.optim.Adam(
        [
            {"params": model.head_parameters(), "lr": args.head_lr, "name": "histogram_head"},
            {"params": [model.luts], "lr": args.lut_lr, "name": "luts"},
            {
                "params": model.final_classifier_parameters(),
                "lr": args.final_classifier_lr,
                "name": "classifier_final",
            },
        ],
        betas=(0.9, 0.999),
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer,
        T_max=args.epochs,
        eta_min=min(args.head_lr, args.lut_lr, args.final_classifier_lr) * 0.05,
    )
    start_epoch, global_step = 1, 0
    best_psnr, best_score = -math.inf, -math.inf
    epochs_without_improvement = 0
    if resume_payload is not None:
        optimizer.load_state_dict(resume_payload["optimizer"])
        scheduler.load_state_dict(resume_payload["scheduler"])
        start_epoch = int(resume_payload["epoch"]) + 1
        global_step = int(resume_payload.get("global_step", 0))
        best_psnr = float(resume_payload.get("best_psnr", -math.inf))
        best_score = float(resume_payload.get("best_selection_score", -math.inf))
        epochs_without_improvement = int(resume_payload.get("epochs_without_improvement", 0))
    if start_epoch > args.epochs:
        raise ValueError(f"Checkpoint already reached epoch {start_epoch - 1}")

    config = {
        **vars(args),
        "device": str(device),
        "train_count": len(train_dataset),
        "val_count": len(val_dataset),
        "parameter_count": model.parameter_count,
        "bootstrap": bootstrap,
        "torch_version": torch.__version__,
    }
    (output_dir / "run_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(config, indent=2, ensure_ascii=False))

    stop_requested = False
    loss_names = (
        "total",
        "mse",
        "ssim",
        "delta_e",
        "smoothness",
        "monotonicity",
        "weight_delta_norm",
        "strength_anchor",
    )
    for epoch in range(start_epoch, args.epochs + 1):
        head_only = epoch <= args.head_only_epochs
        model.set_training_stage(head_only=head_only)
        model.train()
        if head_only:
            model.classifier.eval()
        sums = {name: 0.0 for name in loss_names}
        strength_sum, samples = 0.0, 0
        started = time.perf_counter()
        for batch in train_loader:
            source = batch["input"].to(device, non_blocking=True)
            target = batch["target"].to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            prediction, auxiliary = model(source)
            values = criterion(prediction, target, model.luts, auxiliary)
            values.total.backward()
            torch.nn.utils.clip_grad_norm_(
                [parameter for parameter in model.parameters() if parameter.requires_grad],
                args.gradient_clip,
            )
            optimizer.step()
            samples += 1
            global_step += 1
            strength_sum += float(auxiliary["strength"].mean().detach())
            for name in loss_names:
                sums[name] += float(getattr(values, name).detach())
            if args.max_steps > 0 and global_step >= args.max_steps:
                stop_requested = True
                break
        scheduler.step()

        train_metrics = {name: value / max(samples, 1) for name, value in sums.items()}
        train_metrics["strength"] = strength_sum / max(samples, 1)
        record: dict[str, Any] = {
            "epoch": epoch,
            "stage": "histogram_head_only" if head_only else "conservative_joint_finetune",
            "global_step": global_step,
            "seconds": time.perf_counter() - started,
            "learning_rates": [group["lr"] for group in optimizer.param_groups],
            "train": train_metrics,
        }
        if epoch % args.eval_every == 0 or stop_requested or epoch == args.epochs:
            validation = evaluate_model(
                model,
                val_loader,
                device,
                previews_dir / f"epoch_{epoch:04d}.jpg",
            )
            record["validation"] = validation
            current_psnr = float(validation["psnr"])
            current_score = float(validation["selection_score"])
            improved_score = current_score > best_score
            if current_psnr > best_psnr:
                best_psnr = current_psnr
                payload = make_payload(
                    model,
                    optimizer,
                    scheduler,
                    args,
                    bootstrap,
                    epoch=epoch,
                    global_step=global_step,
                    best_psnr=best_psnr,
                    best_score=max(best_score, current_score),
                    epochs_without_improvement=epochs_without_improvement,
                )
                atomic_torch_save(payload, checkpoints_dir / "best_psnr.pt")
            if improved_score:
                best_score = current_score
                epochs_without_improvement = 0
                payload = make_payload(
                    model,
                    optimizer,
                    scheduler,
                    args,
                    bootstrap,
                    epoch=epoch,
                    global_step=global_step,
                    best_psnr=best_psnr,
                    best_score=best_score,
                    epochs_without_improvement=0,
                )
                atomic_torch_save(payload, checkpoints_dir / "best.pt")
            else:
                epochs_without_improvement += 1

        payload = make_payload(
            model,
            optimizer,
            scheduler,
            args,
            bootstrap,
            epoch=epoch,
            global_step=global_step,
            best_psnr=best_psnr,
            best_score=best_score,
            epochs_without_improvement=epochs_without_improvement,
        )
        atomic_torch_save(payload, checkpoints_dir / "latest.pt")
        if epoch % args.save_every == 0:
            atomic_torch_save(payload, checkpoints_dir / f"epoch_{epoch:04d}.pt")
        append_jsonl(output_dir / "metrics.jsonl", record)
        print(json.dumps(record, ensure_ascii=False))
        if stop_requested or epochs_without_improvement >= args.early_stopping_patience:
            break

    result = {
        "completed_epoch": epoch,
        "global_step": global_step,
        "best_validation_psnr": best_psnr,
        "best_selection_score": best_score,
        "stopped_early": epochs_without_improvement >= args.early_stopping_patience,
        "best_checkpoint": str(checkpoints_dir / "best.pt"),
        "best_psnr_checkpoint": str(checkpoints_dir / "best_psnr.pt"),
    }
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

