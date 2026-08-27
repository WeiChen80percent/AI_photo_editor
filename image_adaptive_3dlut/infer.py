"""Apply a trained Expert C Image-Adaptive 3D LUT checkpoint to one image."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from PIL import Image
from torchvision.transforms import functional as TF

from .checkpoints import load_model_checkpoint
from .data import load_rgb
from .metrics import tensor_to_uint8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cpu", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    model, checkpoint = load_model_checkpoint(args.checkpoint, device)
    model.eval()
    image = load_rgb(args.input)
    source = TF.to_tensor(image).unsqueeze(0).to(device)
    with torch.inference_mode():
        prediction, weights = model(source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(tensor_to_uint8(prediction)).save(output, quality=95)
    metadata = {
        "input": str(args.input),
        "output": str(output),
        "checkpoint": str(args.checkpoint),
        "checkpoint_epoch": int(checkpoint.get("epoch", -1)),
        "weights": [float(value) for value in weights[0].cpu()],
        "size": list(image.size),
    }
    output.with_suffix(output.suffix + ".json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

