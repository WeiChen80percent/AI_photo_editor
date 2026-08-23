"""Evaluate a trained checkpoint with the paper's PSNR, SSIM and Delta E metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from .checkpoints import load_model_checkpoint
from .data import FiveKExpertCDataset
from .metrics import MetricAccumulator, calculate_metrics, save_comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument(
        "--manifest", default="image_adaptive_3dlut/manifests/public_dev.jsonl"
    )
    parser.add_argument("--target-manifest", default="")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-dir", default="image_adaptive_3dlut/runs/evaluation")
    parser.add_argument("--resolution", choices=("480p", "original"), default="480p")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--comparison-count", type=int, default=30)
    parser.add_argument("--allow-hidden", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if "hidden" in str(args.manifest).lower() and not args.allow_hidden:
        raise ValueError("Pass --allow-hidden only for the one-time final hidden evaluation")
    if "hidden" in str(args.manifest).lower() and not args.target_manifest:
        raise ValueError(
            "Hidden evaluation requires --target-manifest "
            "image_adaptive_3dlut/manifests/hidden_targets.jsonl"
        )
    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    model, checkpoint = load_model_checkpoint(args.checkpoint, device)
    model.eval()
    dataset = FiveKExpertCDataset(
        args.manifest,
        args.data_root,
        target_manifest=args.target_manifest or None,
        train=False,
        resolution=args.resolution,
        limit=args.limit,
    )
    loader = DataLoader(dataset, batch_size=1, shuffle=False, num_workers=0)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    accumulator = MetricAccumulator()
    metrics_path = output_dir / "per_image.jsonl"
    with metrics_path.open("w", encoding="utf-8") as handle, torch.inference_mode():
        for index, batch in enumerate(loader):
            source = batch["input"].to(device)
            target = batch["target"].to(device)
            prediction, weights = model(source)
            metrics = calculate_metrics(prediction, target)
            accumulator.update(metrics)
            item = {
                "id": batch["id"][0],
                "psnr": metrics.psnr,
                "ssim": metrics.ssim,
                "delta_e": metrics.delta_e,
                "weights": [float(value) for value in weights[0].cpu()],
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            if index < args.comparison_count:
                safe_id = Path(str(batch["id"][0])).stem
                save_comparison(
                    output_dir / "comparisons" / f"{index + 1:04d}_{safe_id}.jpg",
                    source,
                    prediction,
                    target,
                )
    summary = {
        **accumulator.averages(),
        "checkpoint": str(args.checkpoint),
        "checkpoint_epoch": int(checkpoint.get("epoch", -1)),
        "manifest": str(args.manifest),
        "target_manifest": str(args.target_manifest),
        "resolution": args.resolution,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
