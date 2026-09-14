"""Enhance one full-resolution image using the global histogram LUT model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from PIL import Image
from torchvision.transforms import functional as TF

from image_adaptive_3dlut.data import load_rgb
from image_adaptive_3dlut.metrics import tensor_to_uint8

from .checkpoints import load_model_checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    device = torch.device("cpu" if args.cpu or not torch.cuda.is_available() else "cuda")
    model, checkpoint = load_model_checkpoint(args.checkpoint, device)
    model.eval()
    image = load_rgb(args.input)
    source = TF.to_tensor(image).unsqueeze(0).to(device)
    with torch.inference_mode():
        prediction, auxiliary = model(source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(tensor_to_uint8(prediction)).save(output, quality=95, subsampling=0)
    metadata = {
        "input": str(args.input),
        "output": str(output),
        "checkpoint": str(args.checkpoint),
        "checkpoint_epoch": int(checkpoint.get("epoch", -1)),
        "device": str(device),
        "input_size": [image.width, image.height],
        "strength": float(auxiliary["strength"][0, 0]),
        "weight_delta": auxiliary["weight_delta"][0].float().cpu().tolist(),
        "weights": auxiliary["weights"][0].float().cpu().tolist(),
    }
    Path(f"{output}.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

