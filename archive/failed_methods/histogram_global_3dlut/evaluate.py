"""Evaluate a histogram-conditioned global LUT checkpoint."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from image_adaptive_3dlut.data import FiveKExpertCDataset
from image_adaptive_3dlut.metrics import MetricAccumulator, calculate_metrics, save_comparison

from .checkpoints import load_model_checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--manifest", default="image_adaptive_3dlut/manifests/public_dev.jsonl")
    parser.add_argument("--target-manifest", default="")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-dir", default="histogram_global_3dlut/runs/evaluation")
    parser.add_argument("--resolution", choices=("480p", "original"), default="480p")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--comparison-count", type=int, default=30)
    parser.add_argument("--allow-hidden", action="store_true")
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    is_hidden = "hidden" in str(args.manifest).lower()
    if is_hidden and not args.allow_hidden:
        raise ValueError("Hidden evaluation requires explicit --allow-hidden")
    if is_hidden and not args.target_manifest:
        raise ValueError("Hidden evaluation requires --target-manifest")

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
    strength_sum = 0.0
    delta_sum = torch.zeros(3, dtype=torch.float64)
    per_image = output_dir / "per_image.jsonl"
    with per_image.open("w", encoding="utf-8") as handle, torch.inference_mode():
        for index, batch in enumerate(loader):
            source = batch["input"].to(device)
            target = batch["target"].to(device)
            prediction, auxiliary = model(source)
            metrics = calculate_metrics(prediction, target)
            accumulator.update(metrics)
            strength_sum += float(auxiliary["strength"][0, 0])
            delta_sum += auxiliary["weight_delta"][0].double().cpu()
            handle.write(
                json.dumps(
                    {
                        "id": batch["id"][0],
                        "psnr": metrics.psnr,
                        "ssim": metrics.ssim,
                        "delta_e": metrics.delta_e,
                        "strength": float(auxiliary["strength"][0, 0]),
                        "weight_delta": auxiliary["weight_delta"][0].float().cpu().tolist(),
                        "weights": auxiliary["weights"][0].float().cpu().tolist(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            if index < args.comparison_count:
                safe_id = Path(str(batch["id"][0])).stem
                save_comparison(
                    output_dir / "comparisons" / f"{index + 1:04d}_{safe_id}.jpg",
                    source,
                    prediction,
                    target,
                )
    count = max(len(dataset), 1)
    summary = {
        **accumulator.averages(),
        "checkpoint": str(args.checkpoint),
        "checkpoint_epoch": int(checkpoint.get("epoch", -1)),
        "mean_strength": strength_sum / count,
        "mean_weight_delta": (delta_sum / count).tolist(),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

