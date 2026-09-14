"""Fairly compare baseline and global-histogram LUT checkpoints."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
from PIL import Image, ImageDraw
from torch.utils.data import DataLoader

from image_adaptive_3dlut.checkpoints import load_model_checkpoint as load_baseline
from image_adaptive_3dlut.data import FiveKExpertCDataset
from image_adaptive_3dlut.metrics import MetricAccumulator, calculate_metrics, tensor_to_uint8

from .checkpoints import load_model_checkpoint as load_candidate


def timed(model: torch.nn.Module, source: torch.Tensor, device: torch.device) -> tuple[torch.Tensor, float]:
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    started = time.perf_counter()
    output, _ = model(source)
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    return output, (time.perf_counter() - started) * 1000


def save_panel(path: Path, tensors: tuple[torch.Tensor, ...]) -> None:
    images = [Image.fromarray(tensor_to_uint8(tensor)) for tensor in tensors]
    width, height = images[0].size
    bar = max(28, round(height * 0.035))
    canvas = Image.new("RGB", (width * 4, height + bar), "white")
    draw = ImageDraw.Draw(canvas)
    for index, (image, label) in enumerate(
        zip(images, ("RAW", "Baseline", "Global Histogram", "Expert C"), strict=True)
    ):
        canvas.paste(image, (index * width, bar))
        draw.text((index * width + 8, 7), label, fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, quality=92, subsampling=0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-checkpoint", default="image_adaptive_3dlut/trained_model/best.pt")
    parser.add_argument("--candidate-checkpoint", required=True)
    parser.add_argument("--manifest", default="image_adaptive_3dlut/manifests/public_dev.jsonl")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-dir", default="histogram_global_3dlut/runs/comparison")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--comparison-count", type=int, default=30)
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    if "hidden" in str(args.manifest).lower():
        raise ValueError("Architecture comparison must use public development data")
    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    baseline, baseline_payload = load_baseline(args.baseline_checkpoint, device)
    candidate, candidate_payload = load_candidate(args.candidate_checkpoint, device)
    baseline.eval()
    candidate.eval()
    dataset = FiveKExpertCDataset(args.manifest, args.data_root, train=False, resolution="480p", limit=args.limit)
    loader = DataLoader(dataset, batch_size=1, shuffle=False, num_workers=0)
    first_metrics, second_metrics = MetricAccumulator(), MetricAccumulator()
    first_ms, second_ms = 0.0, 0.0
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with torch.inference_mode():
        for index, batch in enumerate(loader):
            source, target = batch["input"].to(device), batch["target"].to(device)
            if index == 0:
                baseline(source)
                candidate(source)
                if device.type == "cuda":
                    torch.cuda.synchronize(device)
            baseline_output, baseline_time = timed(baseline, source, device)
            candidate_output, candidate_time = timed(candidate, source, device)
            first_metrics.update(calculate_metrics(baseline_output, target))
            second_metrics.update(calculate_metrics(candidate_output, target))
            first_ms += baseline_time
            second_ms += candidate_time
            if index < args.comparison_count:
                safe_id = Path(str(batch["id"][0])).stem
                save_panel(
                    output_dir / "comparisons" / f"{index + 1:04d}_{safe_id}.jpg",
                    (source, baseline_output, candidate_output, target),
                )
    baseline_summary, candidate_summary = first_metrics.averages(), second_metrics.averages()
    count = max(len(dataset), 1)
    baseline_summary["mean_inference_ms"] = first_ms / count
    candidate_summary["mean_inference_ms"] = second_ms / count
    summary = {
        "count": len(dataset),
        "device": str(device),
        "baseline_epoch": int(baseline_payload.get("epoch", -1)),
        "candidate_epoch": int(candidate_payload.get("epoch", -1)),
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "candidate_minus_baseline": {
            "psnr": candidate_summary["psnr"] - baseline_summary["psnr"],
            "ssim": candidate_summary["ssim"] - baseline_summary["ssim"],
            "delta_e_improvement": baseline_summary["delta_e"] - candidate_summary["delta_e"],
            "inference_ms": candidate_summary["mean_inference_ms"] - baseline_summary["mean_inference_ms"],
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

