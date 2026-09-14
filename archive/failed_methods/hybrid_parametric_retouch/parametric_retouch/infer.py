"""Apply a trained hybrid parametric retouching checkpoint to one image."""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from PIL import ImageOps
from torchvision.transforms import functional as TF

from .data import load_rgb
from .model import load_checkpoint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--long-edge", type=int, default=2048, help="0 keeps original resolution")
    parser.add_argument("--cpu", action="store_true")
    args = parser.parse_args()
    device = torch.device("cuda" if torch.cuda.is_available() and not args.cpu else "cpu")
    image = ImageOps.exif_transpose(load_rgb(args.input)).convert("RGB")
    if args.long_edge > 0 and max(image.size) > args.long_edge:
        scale = args.long_edge / max(image.size)
        image = image.resize((round(image.width * scale), round(image.height * scale)))
    tensor = TF.to_tensor(image).unsqueeze(0).to(device)
    model = load_checkpoint(args.checkpoint, device)
    with torch.inference_mode():
        prediction = model(tensor).squeeze(0).cpu().clamp(0, 1)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    TF.to_pil_image(prediction).save(output, quality=95)
    print(f"Saved: {output.resolve()}")


if __name__ == "__main__":
    main()
