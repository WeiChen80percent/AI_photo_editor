from __future__ import annotations

import argparse
import io
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageCms, ImageDraw, ImageOps


DEFAULT_SOURCE_DIR = Path(r"C:\Users\user\Desktop\FiveK\downloads\FiveK_C")
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class DegradationResult:
    image: Image.Image
    degradation_type: str
    degradation_ops: list[dict[str, Any]]
    target_prompt: str
    target_action: list[dict[str, Any]]
    stats: dict[str, Any]
    qa_pass: bool


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a small reverse-degradation pilot dataset from FiveK Expert C TIFF files."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-side", type=int, default=1024)
    parser.add_argument("--quality", type=int, default=95)
    parser.add_argument("--contact-sheet-count", type=int, default=20)
    parser.add_argument(
        "--selection",
        choices=["random", "sorted"],
        default="random",
        help="Use a deterministic random sample or the first N sorted TIFFs.",
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    output_dir = args.output_dir.resolve()
    gt_dir = output_dir / "gt"
    degraded_dir = output_dir / "degraded"
    contact_dir = output_dir / "contact_sheets"
    for directory in (gt_dir, degraded_dir, contact_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source_paths = discover_tiffs(args.source_dir)
    if not source_paths:
        raise FileNotFoundError(f"No TIFF files found in {args.source_dir}")

    selected = select_sources(source_paths, args.limit, rng, args.selection)
    write_selected_sources(output_dir / "selected_sources.jsonl", selected)

    metadata_records: list[dict[str, Any]] = []
    sheet_rows: list[tuple[Path, list[Path]]] = []

    for index, source_path in enumerate(selected, start=1):
        image_id = source_path.stem
        gt_image = load_gt_image(source_path, args.max_side)
        gt_path = gt_dir / f"{image_id}.jpg"
        save_jpeg(gt_image, gt_path, args.quality)

        variant_paths: list[Path] = []
        for degradation_fn in (
            degrade_exposure_contrast,
            degrade_white_balance_cast,
            degrade_compound_bad_grade,
        ):
            result = build_degradation_with_retries(gt_image, degradation_fn, rng)
            degraded_id = f"{image_id}_{result.degradation_type}"
            degraded_path = degraded_dir / f"{degraded_id}.jpg"
            save_jpeg(result.image, degraded_path, args.quality)
            variant_paths.append(degraded_path)

            metadata_records.append(
                {
                    "id": degraded_id,
                    "source_path": str(source_path),
                    "gt_path": relative_posix(gt_path, output_dir),
                    "degraded_path": relative_posix(degraded_path, output_dir),
                    "degradation_type": result.degradation_type,
                    "degradation_ops": result.degradation_ops,
                    "target_prompt": result.target_prompt,
                    "target_action": result.target_action,
                    "stats": result.stats,
                    "qa_pass": result.qa_pass,
                }
            )

        if len(sheet_rows) < args.contact_sheet_count:
            sheet_rows.append((gt_path, variant_paths))

        if index % 25 == 0 or index == len(selected):
            print(f"Generated {index}/{len(selected)} GT images, {len(metadata_records)} degraded pairs.")

    write_jsonl(output_dir / "metadata.jsonl", metadata_records)
    if sheet_rows:
        build_contact_sheet(sheet_rows, contact_dir / "contact_sheet_001.jpg")

    print("Done.")
    print(f"Selected sources: {output_dir / 'selected_sources.jsonl'}")
    print(f"Metadata: {output_dir / 'metadata.jsonl'}")
    print(f"GT images: {gt_dir}")
    print(f"Degraded images: {degraded_dir}")
    print(f"Contact sheet: {contact_dir / 'contact_sheet_001.jpg'}")


def discover_tiffs(source_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for pattern in ("*.tif", "*.tiff")
            for path in source_dir.glob(pattern)
            if path.is_file()
        ]
    )


def select_sources(
    source_paths: list[Path],
    limit: int,
    rng: random.Random,
    selection: str,
) -> list[Path]:
    if limit <= 0 or limit >= len(source_paths):
        return source_paths
    if selection == "sorted":
        return source_paths[:limit]
    selected = source_paths[:]
    rng.shuffle(selected)
    return sorted(selected[:limit])


def write_selected_sources(path: Path, selected: list[Path]) -> None:
    records = [
        {
            "index": index,
            "image_id": source_path.stem,
            "source_path": str(source_path),
        }
        for index, source_path in enumerate(selected, start=1)
    ]
    write_jsonl(path, records)


def load_gt_image(source_path: Path, max_side: int) -> Image.Image:
    with Image.open(source_path) as raw:
        image = ImageOps.exif_transpose(raw)
        image.load()
        image = convert_to_srgb(image)
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return image.copy()


def convert_to_srgb(image: Image.Image) -> Image.Image:
    icc_profile = image.info.get("icc_profile")
    if icc_profile:
        try:
            src_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc_profile))
            dst_profile = ImageCms.createProfile("sRGB")
            return ImageCms.profileToProfile(
                image,
                src_profile,
                dst_profile,
                outputMode="RGB",
            )
        except Exception as exc:
            print(f"Warning: ICC conversion failed; falling back to RGB conversion ({exc}).")
    return image.convert("RGB")


def build_degradation_with_retries(
    image: Image.Image,
    degradation_fn,
    rng: random.Random,
    attempts: int = 8,
) -> DegradationResult:
    last_result: DegradationResult | None = None
    for _ in range(attempts):
        result = degradation_fn(image, rng)
        if result.qa_pass:
            return result
        last_result = result
    if last_result is None:
        raise RuntimeError("Degradation did not produce a result.")
    return last_result


def degrade_exposure_contrast(image: Image.Image, rng: random.Random) -> DegradationResult:
    arr = image_to_float(image)
    exposure_ev = rng.choice([-1, 1]) * rng.uniform(0.45, 1.15)
    contrast_scale = rng.uniform(0.62, 0.86)
    black_lift = rng.uniform(0.0, 0.08)
    gamma = rng.uniform(0.9, 1.18)

    damaged = np.clip(arr * (2.0**exposure_ev), 0.0, 1.0)
    damaged = apply_contrast(damaged, contrast_scale)
    damaged = np.clip(damaged + black_lift, 0.0, 1.0)
    damaged = apply_gamma(damaged, gamma)

    target_action = [
        {
            "op": "exposure",
            "direction": "decrease" if exposure_ev > 0 else "increase",
            "reason": "reverse the generated exposure shift",
        },
        {
            "op": "contrast",
            "direction": "increase",
            "reason": "restore contrast lost during degradation",
        },
        {
            "op": "black_level",
            "direction": "decrease" if black_lift > 0.02 else "neutral",
            "reason": "recover clean black point",
        },
    ]
    return package_result(
        damaged,
        degradation_type="exposure_contrast",
        degradation_ops=[
            {"op": "exposure_ev", "value": round(exposure_ev, 4)},
            {"op": "contrast_scale", "value": round(contrast_scale, 4)},
            {"op": "black_lift", "value": round(black_lift, 4)},
            {"op": "gamma", "value": round(gamma, 4)},
        ],
        target_prompt="把照片修回自然曝光、乾淨黑位與適中對比。",
        target_action=target_action,
    )


def degrade_white_balance_cast(image: Image.Image, rng: random.Random) -> DegradationResult:
    arr = image_to_float(image)
    cast_name = rng.choice(["warm_yellow", "cool_blue", "green_cast", "magenta_cast"])
    if cast_name == "warm_yellow":
        multipliers = np.array([rng.uniform(1.08, 1.22), rng.uniform(1.0, 1.07), rng.uniform(0.78, 0.92)])
        target_direction = "cooler"
    elif cast_name == "cool_blue":
        multipliers = np.array([rng.uniform(0.78, 0.92), rng.uniform(0.98, 1.04), rng.uniform(1.08, 1.24)])
        target_direction = "warmer"
    elif cast_name == "green_cast":
        multipliers = np.array([rng.uniform(0.88, 0.98), rng.uniform(1.08, 1.22), rng.uniform(0.88, 0.98)])
        target_direction = "more_magenta"
    else:
        multipliers = np.array([rng.uniform(1.02, 1.12), rng.uniform(0.78, 0.9), rng.uniform(1.02, 1.12)])
        target_direction = "more_green"

    damaged = np.clip(arr * multipliers.reshape(1, 1, 3), 0.0, 1.0)
    saturation_scale = rng.uniform(0.92, 1.12)
    damaged = apply_saturation(damaged, saturation_scale)

    return package_result(
        damaged,
        degradation_type=f"white_balance_{cast_name}",
        degradation_ops=[
            {
                "op": "channel_multipliers_rgb",
                "value": [round(float(value), 4) for value in multipliers],
            },
            {"op": "saturation_scale", "value": round(saturation_scale, 4)},
        ],
        target_prompt="修正照片偏色，讓白平衡回到自然、專業的色調。",
        target_action=[
            {
                "op": "temperature_tint",
                "direction": target_direction,
                "reason": f"reverse generated {cast_name} white-balance cast",
            },
            {
                "op": "saturation",
                "direction": "normalize",
                "reason": "keep color intensity believable after white-balance correction",
            },
        ],
    )


def degrade_compound_bad_grade(image: Image.Image, rng: random.Random) -> DegradationResult:
    arr = image_to_float(image)
    exposure_ev = rng.uniform(-0.55, 0.55)
    contrast_scale = rng.uniform(0.68, 0.92)
    saturation_scale = rng.uniform(0.68, 1.32)
    gamma = rng.uniform(0.82, 1.28)
    shadow_tint = np.array(
        [
            rng.uniform(-0.08, 0.08),
            rng.uniform(-0.08, 0.08),
            rng.uniform(-0.08, 0.08),
        ],
        dtype=np.float32,
    )
    highlight_tint = np.array(
        [
            rng.uniform(-0.06, 0.06),
            rng.uniform(-0.06, 0.06),
            rng.uniform(-0.06, 0.06),
        ],
        dtype=np.float32,
    )
    vignette_amount = rng.uniform(0.06, 0.18)

    damaged = np.clip(arr * (2.0**exposure_ev), 0.0, 1.0)
    damaged = apply_contrast(damaged, contrast_scale)
    damaged = apply_saturation(damaged, saturation_scale)
    damaged = apply_gamma(damaged, gamma)
    damaged = apply_split_tone(damaged, shadow_tint, highlight_tint)
    damaged = apply_vignette(damaged, vignette_amount)

    return package_result(
        damaged,
        degradation_type="compound_bad_grade",
        degradation_ops=[
            {"op": "exposure_ev", "value": round(exposure_ev, 4)},
            {"op": "contrast_scale", "value": round(contrast_scale, 4)},
            {"op": "saturation_scale", "value": round(saturation_scale, 4)},
            {"op": "gamma", "value": round(gamma, 4)},
            {
                "op": "shadow_tint_rgb",
                "value": [round(float(value), 4) for value in shadow_tint],
            },
            {
                "op": "highlight_tint_rgb",
                "value": [round(float(value), 4) for value in highlight_tint],
            },
            {"op": "vignette_amount", "value": round(vignette_amount, 4)},
        ],
        target_prompt="把被亂調過的照片修回自然、乾淨、專業且不誇張的色調。",
        target_action=[
            {"op": "exposure", "direction": "normalize"},
            {"op": "contrast", "direction": "increase_or_normalize"},
            {"op": "saturation", "direction": "normalize"},
            {"op": "color_balance", "direction": "neutralize_shadow_highlight_cast"},
            {"op": "vignette", "direction": "reduce"},
        ],
    )


def image_to_float(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0


def float_to_image(arr: np.ndarray) -> Image.Image:
    arr_u8 = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(arr_u8, mode="RGB")


def apply_contrast(arr: np.ndarray, contrast_scale: float) -> np.ndarray:
    return np.clip((arr - 0.5) * contrast_scale + 0.5, 0.0, 1.0)


def apply_gamma(arr: np.ndarray, gamma: float) -> np.ndarray:
    return np.clip(arr, 0.0, 1.0) ** gamma


def apply_saturation(arr: np.ndarray, saturation_scale: float) -> np.ndarray:
    luma = luminance(arr)[..., None]
    return np.clip(luma + (arr - luma) * saturation_scale, 0.0, 1.0)


def apply_split_tone(
    arr: np.ndarray,
    shadow_tint: np.ndarray,
    highlight_tint: np.ndarray,
) -> np.ndarray:
    luma = luminance(arr)[..., None]
    shadow_mask = np.clip((0.55 - luma) / 0.55, 0.0, 1.0)
    highlight_mask = np.clip((luma - 0.45) / 0.55, 0.0, 1.0)
    toned = arr + shadow_mask * shadow_tint.reshape(1, 1, 3)
    toned = toned + highlight_mask * highlight_tint.reshape(1, 1, 3)
    return np.clip(toned, 0.0, 1.0)


def apply_vignette(arr: np.ndarray, amount: float) -> np.ndarray:
    height, width = arr.shape[:2]
    y = np.linspace(-1.0, 1.0, height, dtype=np.float32)
    x = np.linspace(-1.0, 1.0, width, dtype=np.float32)
    xv, yv = np.meshgrid(x, y)
    distance = np.sqrt(xv * xv + yv * yv)
    mask = 1.0 - amount * np.clip(distance, 0.0, 1.0)
    return np.clip(arr * mask[..., None], 0.0, 1.0)


def luminance(arr: np.ndarray) -> np.ndarray:
    return arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722


def package_result(
    arr: np.ndarray,
    *,
    degradation_type: str,
    degradation_ops: list[dict[str, Any]],
    target_prompt: str,
    target_action: list[dict[str, Any]],
) -> DegradationResult:
    stats = compute_stats(arr)
    qa_pass = bool(
        20.0 <= stats["mean_luma"] <= 235.0
        and stats["clip_ratio"] <= 0.18
        and stats["mean_saturation"] >= 0.025
    )
    return DegradationResult(
        image=float_to_image(arr),
        degradation_type=degradation_type,
        degradation_ops=degradation_ops,
        target_prompt=target_prompt,
        target_action=target_action,
        stats=stats,
        qa_pass=qa_pass,
    )


def compute_stats(arr: np.ndarray) -> dict[str, Any]:
    luma = luminance(arr)
    max_rgb = np.max(arr, axis=2)
    min_rgb = np.min(arr, axis=2)
    saturation = max_rgb - min_rgb
    clipped = np.any((arr <= 1.0 / 255.0) | (arr >= 254.0 / 255.0), axis=2)
    return {
        "mean_luma": round(float(np.mean(luma) * 255.0), 4),
        "std_luma": round(float(np.std(luma) * 255.0), 4),
        "mean_saturation": round(float(np.mean(saturation)), 6),
        "clip_ratio": round(float(np.mean(clipped)), 6),
    }


def save_jpeg(image: Image.Image, path: Path, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(
        path,
        format="JPEG",
        quality=quality,
        subsampling=0,
        optimize=True,
    )


def build_contact_sheet(rows: list[tuple[Path, list[Path]]], output_path: Path) -> None:
    thumb_w, thumb_h = 220, 160
    label_h = 26
    padding = 10
    columns = 4
    width = columns * thumb_w + (columns + 1) * padding
    height = len(rows) * (thumb_h + label_h + padding) + padding
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    headers = ["GT", "Exposure/Contrast", "White Balance", "Compound"]

    for row_index, (gt_path, degraded_paths) in enumerate(rows):
        image_paths = [gt_path, *degraded_paths]
        y = padding + row_index * (thumb_h + label_h + padding)
        for col_index, image_path in enumerate(image_paths):
            x = padding + col_index * (thumb_w + padding)
            with Image.open(image_path) as img:
                thumb = ImageOps.contain(img.convert("RGB"), (thumb_w, thumb_h), Image.Resampling.LANCZOS)
            tile = Image.new("RGB", (thumb_w, thumb_h), (242, 242, 242))
            tile.paste(thumb, ((thumb_w - thumb.width) // 2, (thumb_h - thumb.height) // 2))
            sheet.paste(tile, (x, y + label_h))
            draw.text((x, y), headers[col_index], fill=(20, 20, 20))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="JPEG", quality=92)


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def relative_posix(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    main()
