"""Train the independent hybrid parametric model on Adobe FiveK Expert C."""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image, ImageDraw
from torch.nn.utils import clip_grad_norm_
from torch.utils.data import DataLoader
from torchvision.transforms import functional as TF

from .data import PairedFiveKDataset
from .losses import RetouchLoss
from .model import HybridParametricRetouchNet, ModelConfig


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _autocast(device: torch.device, enabled: bool):
    if enabled and device.type == "cuda":
        return torch.amp.autocast("cuda", dtype=torch.float16)
    return nullcontext()


def _finite_gradients(model: torch.nn.Module) -> bool:
    gradients = [parameter.grad for parameter in model.parameters() if parameter.grad is not None]
    return bool(gradients) and all(torch.isfinite(gradient).all() for gradient in gradients)


def _move_optimizer_state(optimizer: torch.optim.Optimizer, device: torch.device) -> None:
    """Move tensor-valued Adam state restored with map_location='cpu'."""
    for state in optimizer.state.values():
        for key, value in state.items():
            if isinstance(value, torch.Tensor):
                state[key] = value.to(device)


def _make_checkpoint(
    model: HybridParametricRetouchNet,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.ReduceLROnPlateau,
    scaler: torch.amp.GradScaler,
    *,
    epoch: int,
    global_step: int,
    best_validation_loss: float,
    args: argparse.Namespace,
    epoch_complete: bool,
) -> dict[str, Any]:
    return {
        "architecture_version": model.architecture_version,
        "model_config": model.config.to_dict(),
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "scheduler_state_dict": scheduler.state_dict(),
        "scaler_state_dict": scaler.state_dict(),
        "epoch": epoch,
        "global_step": global_step,
        "best_validation_loss": best_validation_loss,
        "epoch_complete": epoch_complete,
        "training_config": vars(args),
    }


def _save_preview(
    model: HybridParametricRetouchNet,
    dataset: PairedFiveKDataset,
    path: Path,
    device: torch.device,
    *,
    count: int = 4,
) -> None:
    model.eval()
    rows: list[Image.Image] = []
    indices = np.linspace(0, len(dataset) - 1, min(count, len(dataset)), dtype=int)
    with torch.inference_mode():
        for index in indices:
            sample = dataset[int(index)]
            input_tensor = sample["input"]
            target = sample["target"]
            prediction = model(input_tensor.unsqueeze(0).to(device)).squeeze(0).cpu()
            images = [TF.to_pil_image(value.clamp(0, 1)) for value in (input_tensor, prediction, target)]
            row = Image.new("RGB", (sum(image.width for image in images), images[0].height + 24), "white")
            draw = ImageDraw.Draw(row)
            x = 0
            for label, image in zip(("RAW", "PREDICTION", "EXPERT C"), images):
                row.paste(image, (x, 24))
                draw.text((x + 4, 4), label, fill="black")
                x += image.width
            rows.append(row)
    sheet = Image.new("RGB", (max(row.width for row in rows), sum(row.height for row in rows)), "white")
    y = 0
    for row in rows:
        sheet.paste(row, (0, y))
        y += row.height
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, quality=92)
    model.train()


@torch.inference_mode()
def validate(
    model: HybridParametricRetouchNet,
    loader: DataLoader,
    criterion: RetouchLoss,
    device: torch.device,
) -> dict[str, float]:
    model.eval()
    totals: dict[str, float] = {}
    sample_count = 0
    for batch in loader:
        input_tensor = batch["input"].to(device, non_blocking=True)
        target = batch["target"].to(device, non_blocking=True)
        prediction, aux = model.forward_with_aux(input_tensor)
        loss, terms = criterion(prediction.float(), target.float(), aux)
        if not torch.isfinite(loss):
            raise FloatingPointError(f"Non-finite validation loss: {batch['filename'][0]}")
        values = {"loss": loss, **terms}
        mse = (prediction.float() - target.float()).square().mean().clamp_min(1e-12)
        values["psnr"] = -10.0 * torch.log10(mse)
        for name, value in values.items():
            totals[name] = totals.get(name, 0.0) + float(value)
        sample_count += 1
    model.train()
    return {name: value / max(1, sample_count) for name, value in totals.items()}


def run(args: argparse.Namespace) -> dict[str, Any]:
    seed_everything(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats()
    train_dataset = PairedFiveKDataset(
        args.train_manifest, args.data_root, image_size=args.image_size, augment=True,
        limit=args.train_limit, subset_seed=args.seed,
    )
    validation_dataset = PairedFiveKDataset(
        args.validation_manifest, args.data_root, image_size=args.image_size, augment=False,
        limit=args.val_limit, subset_seed=args.seed + 1,
    )
    loader_options = {
        "batch_size": args.batch_size,
        "num_workers": args.num_workers,
        "pin_memory": device.type == "cuda",
    }
    train_loader = DataLoader(train_dataset, shuffle=True, **loader_options)
    validation_loader = DataLoader(validation_dataset, shuffle=False, **loader_options)
    artifact_dir = Path(args.artifact_root) / args.run_name
    checkpoint_dir = artifact_dir / "checkpoints"
    preview_dir = artifact_dir / "previews"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    best_checkpoint_path = checkpoint_dir / "best.pt"

    if args.resume:
        payload = torch.load(args.resume, map_location="cpu", weights_only=True)
        model = HybridParametricRetouchNet(ModelConfig(**payload["model_config"]))
        model.load_state_dict(payload["model_state_dict"], strict=True)
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
        optimizer.load_state_dict(payload["optimizer_state_dict"])
        for group in optimizer.param_groups:
            group["lr"] = args.learning_rate
        start_epoch = int(payload["epoch"]) + (1 if payload.get("epoch_complete", True) else 0)
        global_step = int(payload["global_step"])
        best_validation_loss = float(payload.get("best_validation_loss", math.inf))
        if not best_checkpoint_path.is_file():
            previous_best = Path(args.resume).with_name("best.pt")
            best_checkpoint_path = previous_best if previous_best.is_file() else Path(args.resume)
    else:
        config = ModelConfig(
            curve_bins=args.curve_bins,
            analysis_size=args.analysis_size,
            base_channels=args.base_channels,
            disable_curves=args.disable_curves,
            disable_affine=args.disable_affine,
            disable_local=args.disable_local,
        )
        model = HybridParametricRetouchNet(config)
        optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
        start_epoch, global_step, best_validation_loss = 1, 0, math.inf
    model.to(device)
    if args.resume:
        _move_optimizer_state(optimizer, device)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=3, min_lr=1e-6
    )
    if args.resume and payload.get("scheduler_state_dict"):
        scheduler.load_state_dict(payload["scheduler_state_dict"])
    scaler = torch.amp.GradScaler("cuda", enabled=args.amp and device.type == "cuda")
    if args.resume and payload.get("scaler_state_dict"):
        scaler.load_state_dict(payload["scaler_state_dict"])
    criterion = RetouchLoss().to(device)
    (artifact_dir / "run_config.json").write_text(
        json.dumps({**vars(args), "device": str(device), "model_config": model.config.to_dict()}, indent=2),
        encoding="utf-8",
    )
    log_path = artifact_dir / "metrics.jsonl"
    started = time.perf_counter()
    stop = False

    for epoch in range(start_epoch, args.epochs + 1):
        model.train()
        for batch in train_loader:
            input_tensor = batch["input"].to(device, non_blocking=True)
            target = batch["target"].to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            try:
                with _autocast(device, args.amp):
                    prediction, aux = model.forward_with_aux(input_tensor)
                loss, terms = criterion(prediction.float(), target.float(), aux)
                if not torch.isfinite(prediction).all() or not torch.isfinite(loss):
                    raise FloatingPointError("Prediction or loss contains NaN/Inf")
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                if not _finite_gradients(model):
                    raise FloatingPointError("Gradient contains NaN/Inf")
                gradient_norm = float(clip_grad_norm_(model.parameters(), args.gradient_clip))
                scaler.step(optimizer)
                scaler.update()
            except Exception:
                recovery = _make_checkpoint(
                    model, optimizer, scheduler, scaler, epoch=epoch, global_step=global_step,
                    best_validation_loss=best_validation_loss, args=args, epoch_complete=False,
                )
                torch.save(recovery, checkpoint_dir / "recovery.pt")
                raise
            global_step += 1
            record = {
                "step": global_step,
                "epoch": epoch,
                "filename": batch["filename"][0],
                "train_loss": float(loss.detach()),
                "gradient_norm": gradient_norm,
                "learning_rate": optimizer.param_groups[0]["lr"],
                "elapsed_seconds": time.perf_counter() - started,
                "peak_cuda_memory_mb": (
                    torch.cuda.max_memory_allocated() / 1024**2 if device.type == "cuda" else 0.0
                ),
                **{f"loss_{name}": float(value.detach()) for name, value in terms.items()},
            }
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            if args.log_every > 0 and global_step % args.log_every == 0:
                print(json.dumps(record))
            if args.checkpoint_every > 0 and global_step % args.checkpoint_every == 0:
                torch.save(
                    _make_checkpoint(
                        model, optimizer, scheduler, scaler, epoch=epoch, global_step=global_step,
                        best_validation_loss=best_validation_loss, args=args, epoch_complete=False,
                    ),
                    checkpoint_dir / "latest.pt",
                )
            if args.max_steps > 0 and global_step >= args.max_steps:
                stop = True
                break

        metrics = validate(model, validation_loader, criterion, device)
        scheduler.step(metrics["loss"])
        summary = {"epoch": epoch, "step": global_step, **{f"validation_{k}": v for k, v in metrics.items()}}
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary) + "\n")
        _save_preview(model, validation_dataset, preview_dir / f"epoch_{epoch:03d}.jpg", device)
        checkpoint = _make_checkpoint(
            model, optimizer, scheduler, scaler, epoch=epoch, global_step=global_step,
            best_validation_loss=min(best_validation_loss, metrics["loss"]), args=args, epoch_complete=True,
        )
        torch.save(checkpoint, checkpoint_dir / "latest.pt")
        if metrics["loss"] < best_validation_loss:
            best_validation_loss = metrics["loss"]
            checkpoint["best_validation_loss"] = best_validation_loss
            best_checkpoint_path = checkpoint_dir / "best.pt"
            torch.save(checkpoint, best_checkpoint_path)
        print(json.dumps(summary))
        if stop:
            break

    result = {
        "architecture_version": model.architecture_version,
        "run_name": args.run_name,
        "global_step": global_step,
        "best_validation_loss": best_validation_loss,
        "best_checkpoint": str(best_checkpoint_path),
        "latest_checkpoint": str(checkpoint_dir / "latest.pt"),
    }
    (artifact_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="../data")
    parser.add_argument("--train-manifest", default="../manifests/train.jsonl")
    parser.add_argument("--validation-manifest", default="../manifests/public_dev.jsonl")
    parser.add_argument("--artifact-root", default="artifacts")
    parser.add_argument("--run-name", default="hybrid_full")
    parser.add_argument("--resume")
    parser.add_argument("--image-size", type=int, default=512)
    parser.add_argument("--analysis-size", type=int, default=256)
    parser.add_argument("--curve-bins", type=int, default=16)
    parser.add_argument("--base-channels", type=int, default=24)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--gradient-clip", type=float, default=1.0)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-limit", type=int, default=0)
    parser.add_argument("--val-limit", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=0)
    parser.add_argument("--log-every", type=int, default=50)
    parser.add_argument("--checkpoint-every", type=int, default=250)
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--disable-curves", action="store_true")
    parser.add_argument("--disable-affine", action="store_true")
    parser.add_argument("--disable-local", action="store_true")
    return parser


def main() -> None:
    result = run(build_parser().parse_args())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
