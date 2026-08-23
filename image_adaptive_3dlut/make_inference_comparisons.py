"""Pair inference outputs with their recorded inputs and build comparison images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB").copy()


def label_font(size: int) -> ImageFont.ImageFont:
    for candidate in ("arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def centered_panel(image: Image.Image, width: int, height: int) -> Image.Image:
    panel = Image.new("RGB", (width, height), (18, 18, 18))
    fitted = ImageOps.contain(image, (width, height), Image.Resampling.LANCZOS)
    panel.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return panel


def make_comparison(raw: Path, enhanced: Path, output: Path) -> None:
    source = load_rgb(raw)
    result = load_rgb(enhanced)
    panel_width = max(source.width, result.width)
    panel_height = max(source.height, result.height)
    label_height = max(72, round(panel_height * 0.035))
    font = label_font(max(28, round(label_height * 0.52)))
    canvas = Image.new("RGB", (panel_width * 2, panel_height + label_height), (18, 18, 18))
    canvas.paste(centered_panel(source, panel_width, panel_height), (0, label_height))
    canvas.paste(centered_panel(result, panel_width, panel_height), (panel_width, label_height))
    draw = ImageDraw.Draw(canvas)
    draw.text((panel_width // 2, label_height // 2), "RAW", fill="white", font=font, anchor="mm")
    draw.text(
        (panel_width + panel_width // 2, label_height // 2),
        "Enhanced",
        fill="white",
        font=font,
        anchor="mm",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.stem}.tmp{output.suffix}")
    canvas.save(temporary, quality=95, subsampling=0, optimize=True)
    with Image.open(temporary) as check:
        check.verify()
    temporary.replace(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inference-dir",
        default="image_adaptive_3dlut/runs/inference",
    )
    parser.add_argument("--delete-singles", action="store_true")
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parent.parent
    inference_dir = Path(args.inference_dir)
    enhanced_files = sorted(
        path
        for path in inference_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and path.stem.endswith("_enhanced")
    )
    if not enhanced_files:
        raise ValueError(f"No *_enhanced images found in {inference_dir}")

    pairs: list[tuple[Path, Path, Path]] = []
    for enhanced in enhanced_files:
        metadata_path = Path(f"{enhanced}.json")
        if not metadata_path.is_file():
            raise FileNotFoundError(f"Missing inference metadata: {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        raw = Path(str(metadata["input"]))
        if not raw.is_absolute():
            raw = repository_root / raw
        if not raw.is_file():
            raise FileNotFoundError(f"Recorded input does not exist: {raw}")
        base_name = enhanced.stem.removesuffix("_enhanced")
        output = inference_dir / f"{base_name}_raw_vs_enhanced.jpg"
        pairs.append((raw, enhanced, output))

    for raw, enhanced, output in pairs:
        make_comparison(raw, enhanced, output)
        print(f"Created: {output.name} <- {raw.name} + {enhanced.name}")

    for _, _, output in pairs:
        with Image.open(output) as image:
            image.verify()

    if args.delete_singles:
        for _, enhanced, _ in pairs:
            enhanced.unlink()
            print(f"Deleted single: {enhanced.name}")

    print(f"Completed {len(pairs)} comparisons in {inference_dir}")


if __name__ == "__main__":
    main()

