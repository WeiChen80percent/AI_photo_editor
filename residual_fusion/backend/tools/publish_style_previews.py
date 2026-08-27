from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.opencv_parameter_mapper import (
    build_opencv_parameters_for_style,
)
from app.services.opencv_processor import write_opencv_image
from app.services.style_registry import StyleRegistry
from app.services.style_renderer import create_opencv_style_result


PREVIEW_ANCHOR_SOURCE = (
    BACKEND_DIR
    / "app"
    / "style_catalog"
    / "assets"
    / "preview_anchor_v2.png"
)


def build_project_authored_preview_anchor() -> np.ndarray:
    image = cv2.imread(str(PREVIEW_ANCHOR_SOURCE), cv2.IMREAD_COLOR)
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise RuntimeError(
            f"Unable to load project preview anchor: {PREVIEW_ANCHOR_SOURCE}"
        )
    return cv2.resize(image, (640, 420), interpolation=cv2.INTER_AREA)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=BACKEND_DIR / "app" / "style_catalog" / "catalog.lock.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_DIR / "app" / "style_catalog" / "previews",
    )
    args = parser.parse_args()

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    anchor_path = output_dir / "_preview_anchor.png"
    write_opencv_image(anchor_path, build_project_authored_preview_anchor())

    registry = StyleRegistry(args.catalog.resolve())
    rendered = 0
    for style in registry.list_styles(approved_only=False):
        result_path = output_dir / f"{style.style_id}.jpg"
        create_opencv_style_result(
            original_path=anchor_path,
            result_path=result_path,
            style=style,
            parameters=build_opencv_parameters_for_style(style),
            strength=style.strength_default,
        )
        rendered += 1
    print(
        {
            "catalog_version": registry.catalog_version,
            "preview_count": rendered,
            "output": str(output_dir),
        }
    )


if __name__ == "__main__":
    main()
