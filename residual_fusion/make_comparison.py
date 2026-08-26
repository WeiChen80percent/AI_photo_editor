from __future__ import annotations

import argparse
import io
import math
from pathlib import Path
from typing import Any

from PIL import (
    Image,
    ImageCms,
    ImageDraw,
    ImageFont,
    ImageOps,
    UnidentifiedImageError,
)


FINAL_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = FINAL_ROOT / "comparisons"
OUTPUT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msjhbd.ttc"),
    Path(r"C:\Windows\Fonts\msjh.ttc"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
)
SRGB_PROFILE = ImageCms.createProfile("sRGB")
SRGB_PROFILE_BYTES = ImageCms.ImageCmsProfile(SRGB_PROFILE).tobytes()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine two to four images into one labeled comparison."
    )
    parser.add_argument("images", nargs="+", type=Path, metavar="IMAGE")
    parser.add_argument(
        "--labels",
        nargs="+",
        help="Optional labels in the same order as the images.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--layout",
        choices=("auto", "horizontal", "vertical", "grid"),
        default="auto",
    )
    parser.add_argument(
        "--fit",
        choices=("contain", "crop"),
        default="contain",
        help=(
            "Fit images into equal-size frames without cropping (contain) "
            "or center-crop them (crop)."
        ),
    )
    parser.add_argument(
        "--max-side",
        type=int,
        default=1600,
        help="Maximum width or height of each displayed image.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_paths = [path.resolve() for path in args.images]
    _validate_inputs(image_paths, labels=args.labels, max_side=args.max_side)

    labels = args.labels or [path.name for path in image_paths]
    output_path = _resolve_output_path(
        args.output,
        overwrite=args.overwrite,
    )
    if any(_path_key(output_path) == _path_key(path) for path in image_paths):
        raise ValueError(f"Output would overwrite an input image: {output_path}")

    loaded = [
        _load_image(path, max_side=args.max_side)
        for path in image_paths
    ]
    comparison, resolved_layout = build_comparison(
        loaded,
        labels=labels,
        layout=args.layout,
        fit=args.fit,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _save_image(comparison, output_path)
    print(f"comparison={output_path}")
    print(f"images={len(image_paths)}")
    print(f"layout={resolved_layout}")
    print(f"fit={args.fit}")
    print("status=PASS")


def build_comparison(
    loaded: list[dict[str, Any]],
    *,
    labels: list[str],
    layout: str,
    fit: str,
) -> tuple[Image.Image, str]:
    if fit not in {"contain", "crop"}:
        raise ValueError("Fit mode must be contain or crop.")

    rows, columns, resolved_layout = _resolve_layout(len(loaded), layout)
    frame_size = (
        min(item["image"].width for item in loaded),
        min(item["image"].height for item in loaded),
    )
    display_images = [
        _fit_to_frame(item["image"], frame_size, fit=fit)
        for item in loaded
    ]
    image_width = max(320, frame_size[0])
    image_height = max(200, frame_size[1])
    padding = max(18, min(32, image_width // 50))
    gap = max(16, padding)
    title_size = max(24, min(44, image_width // 25))
    subtitle_size = max(16, min(26, title_size * 2 // 3))
    title_font = _load_font(title_size, bold=True)
    subtitle_font = _load_font(subtitle_size, bold=False)
    header_height = title_size + subtitle_size + 3 * padding
    cell_width = image_width + 2 * padding
    cell_height = header_height + image_height + 2 * padding
    canvas_width = columns * cell_width + (columns + 1) * gap
    canvas_height = rows * cell_height + (rows + 1) * gap

    canvas = Image.new("RGB", (canvas_width, canvas_height), "#ffffff")
    draw = ImageDraw.Draw(canvas)
    for index, (item, label) in enumerate(zip(loaded, labels, strict=True)):
        row = index // columns
        index_in_row = index % columns
        row_count = min(columns, len(loaded) - row * columns)
        row_width = row_count * cell_width + (row_count - 1) * gap
        row_start = (canvas_width - row_width) // 2
        left = row_start + index_in_row * (cell_width + gap)
        top = gap + row * (cell_height + gap)
        right = left + cell_width
        bottom = top + cell_height

        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=8,
            fill="#ffffff",
            outline="#c9d0d6",
            width=2,
        )
        draw.rounded_rectangle(
            (left, top, right, top + header_height),
            radius=8,
            fill="#f3f5f7",
        )
        draw.rectangle(
            (left, top + header_height - 8, right, top + header_height),
            fill="#f3f5f7",
        )
        draw.rectangle(
            (left, top + header_height - 3, right, top + header_height),
            fill="#287b67",
        )

        text_left = left + padding
        text_width = cell_width - 2 * padding
        title = _fit_text(
            draw,
            f"{index + 1}. {label}",
            title_font,
            text_width,
        )
        filename = item["path"].name
        resolution = f"{item['source_size'][0]} x {item['source_size'][1]}"
        subtitle = filename if label != filename else resolution
        if label != filename:
            subtitle = f"{filename} | {resolution}"
        subtitle = _fit_text(draw, subtitle, subtitle_font, text_width)
        draw.text(
            (text_left, top + padding),
            title,
            font=title_font,
            fill="#17212b",
        )
        draw.text(
            (text_left, top + 2 * padding + title_size),
            subtitle,
            font=subtitle_font,
            fill="#58636f",
        )

        image = display_images[index]
        image_left = left + padding + (image_width - image.width) // 2
        image_top = (
            top
            + header_height
            + padding
            + (image_height - image.height) // 2
        )
        canvas.paste(image, (image_left, image_top))
        draw.rectangle(
            (
                image_left - 1,
                image_top - 1,
                image_left + image.width,
                image_top + image.height,
            ),
            outline="#d7dde2",
            width=1,
        )

    return canvas, resolved_layout


def _validate_inputs(
    image_paths: list[Path],
    *,
    labels: list[str] | None,
    max_side: int,
) -> None:
    if not 2 <= len(image_paths) <= 4:
        raise ValueError("Provide between two and four input images.")
    if labels is not None and len(labels) != len(image_paths):
        raise ValueError("The number of labels must match the images.")
    if max_side < 128 or max_side > 4096:
        raise ValueError("--max-side must be between 128 and 4096.")
    missing = [path for path in image_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Input image does not exist: {missing[0]}")


def _load_image(path: Path, *, max_side: int) -> dict[str, Any]:
    try:
        with Image.open(path) as source:
            icc_profile = source.info.get("icc_profile")
            transposed = ImageOps.exif_transpose(source)
            source_size = transposed.size
            has_alpha = (
                "A" in transposed.getbands()
                or "transparency" in transposed.info
            )
            alpha = (
                transposed.convert("RGBA").getchannel("A")
                if has_alpha
                else None
            )
            color_source = (
                transposed.convert("RGB") if has_alpha else transposed
            )
            image = _convert_to_srgb(color_source, icc_profile)
            if alpha is not None:
                rgba = image.convert("RGBA")
                rgba.putalpha(alpha)
                background = Image.new("RGBA", rgba.size, "#ffffff")
                image = Image.alpha_composite(background, rgba).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"Unable to read image: {path}") from exc

    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return {
        "path": path,
        "source_size": source_size,
        "image": image,
    }


def _convert_to_srgb(
    image: Image.Image,
    icc_profile: bytes | None,
) -> Image.Image:
    if icc_profile:
        try:
            source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
            return ImageCms.profileToProfile(
                image,
                source_profile,
                SRGB_PROFILE,
                outputMode="RGB",
            )
        except (ImageCms.PyCMSError, OSError, ValueError):
            pass
    return image.convert("RGB")


def _fit_to_frame(
    image: Image.Image,
    frame_size: tuple[int, int],
    *,
    fit: str,
) -> Image.Image:
    if fit == "crop":
        return ImageOps.fit(
            image,
            frame_size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    contained = ImageOps.contain(
        image,
        frame_size,
        method=Image.Resampling.LANCZOS,
    )
    frame = Image.new("RGB", frame_size, "#ffffff")
    frame.paste(
        contained,
        (
            (frame_size[0] - contained.width) // 2,
            (frame_size[1] - contained.height) // 2,
        ),
    )
    return frame


def _resolve_layout(count: int, layout: str) -> tuple[int, int, str]:
    if layout == "horizontal":
        return 1, count, "horizontal"
    if layout == "vertical":
        return count, 1, "vertical"
    if layout == "grid":
        return math.ceil(count / 2), 2, "grid"
    if count <= 3:
        return 1, count, "horizontal"
    return 2, 2, "grid"


def _load_font(size: int, *, bold: bool) -> ImageFont.FreeTypeFont:
    candidates = (
        FONT_CANDIDATES
        if bold
        else (FONT_CANDIDATES[1], FONT_CANDIDATES[0], FONT_CANDIDATES[2])
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    suffix = "..."
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle].rstrip() + suffix
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + suffix


def _resolve_output_path(
    requested: Path | None,
    *,
    overwrite: bool,
) -> Path:
    uses_default = requested is None
    output = (
        DEFAULT_OUTPUT_ROOT / "comparison.png"
        if requested is None
        else requested.resolve()
    )
    if not output.suffix:
        output = output.with_suffix(".png")
    if output.suffix.lower() not in OUTPUT_EXTENSIONS:
        raise ValueError(
            "Output extension must be PNG, JPG, JPEG, or WebP."
        )
    if output.exists() and not overwrite:
        if uses_default:
            output = _next_available_path(output)
        else:
            raise FileExistsError(
                f"Output exists; use --overwrite or another path: {output}"
            )
    return output


def _next_available_path(base: Path) -> Path:
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(
            f"{base.stem}({suffix}){base.suffix}"
        )
        suffix += 1
    return candidate


def _save_image(image: Image.Image, output_path: Path) -> None:
    suffix = output_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        image.save(
            output_path,
            quality=95,
            subsampling=0,
            icc_profile=SRGB_PROFILE_BYTES,
        )
    elif suffix == ".webp":
        image.save(
            output_path,
            quality=95,
            method=6,
            icc_profile=SRGB_PROFILE_BYTES,
        )
    else:
        image.save(
            output_path,
            compress_level=3,
            icc_profile=SRGB_PROFILE_BYTES,
        )


def _path_key(path: Path) -> str:
    return str(path.resolve()).casefold()


if __name__ == "__main__":
    try:
        main()
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"ERROR: {exc}") from None
