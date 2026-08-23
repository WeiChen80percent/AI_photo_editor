"""Convert the authors' official sRGB pretrained files into this checkpoint format."""

from __future__ import annotations

import argparse
from pathlib import Path

import torch

from .checkpoints import atomic_torch_save
from .model import ImageAdaptive3DLUT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--luts", required=True, help="Official pretrained_models/sRGB/LUTs.pth")
    parser.add_argument("--classifier", required=True, help="Official classifier.pth")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    official_luts = torch.load(args.luts, map_location="cpu", weights_only=True)
    official_classifier = torch.load(args.classifier, map_location="cpu", weights_only=True)
    model = ImageAdaptive3DLUT()
    with torch.no_grad():
        for index in range(3):
            state = official_luts[str(index)]
            model.luts[index].copy_(state["LUT"])
    model.classifier.load_state_dict(official_classifier, strict=True)
    atomic_torch_save(
        {
            "format_version": 1,
            "source": "HuiZeng/Image-Adaptive-3DLUT official pretrained sRGB paired weights",
            "model": model.state_dict(),
            "epoch": -1,
            "global_step": 0,
            "best_psnr": float("nan"),
            "num_luts": 3,
            "lut_dim": 33,
            "args": {},
        },
        Path(args.output),
    )
    print(f"Converted official checkpoint: {args.output}")


if __name__ == "__main__":
    main()

