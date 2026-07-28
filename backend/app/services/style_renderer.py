from __future__ import annotations

import math
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np

from app.services.opencv_processor import (
    apply_opencv_global_adjustments,
    read_opencv_image,
    resolve_opencv_parameters,
    write_opencv_image,
)
from app.services.style_registry import (
    STYLE_RENDERER_VERSION,
    StyleAssetError,
    StyleCatalogError,
    StyleDefinition,
)


_OCIO_CACHE_LOCK = threading.Lock()
_OCIO_PROCESSORS: dict[tuple[str, str], Any] = {}


def create_opencv_style_result(
    *,
    original_path: Path,
    result_path: Path,
    style: StyleDefinition,
    parameters: dict[str, Any],
    strength: float,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    read_started = time.perf_counter()
    original = read_opencv_image(original_path, "style anchor")
    image_read_ms = _elapsed_ms(read_started)

    resolve_started = time.perf_counter()
    resolved = resolve_opencv_parameters(parameters)
    if resolved["region"] != "all" or resolved["mask_type"] != "none":
        raise StyleCatalogError(
            "Style renderer v1 only supports whole-image styles"
        )
    normalized_strength = style.validate_strength(strength)
    parameter_resolution_ms = _elapsed_ms(resolve_started)

    render_started = time.perf_counter()
    if normalized_strength == 0.0:
        styled = original.copy()
        full_style = original
    else:
        full_style = apply_opencv_global_adjustments(
            original,
            resolved,
            reference=None,
        )
        if style.renderer.get("kind") == "recipe":
            full_style = _apply_recipe(full_style, style)
        styled = _blend_style(original, full_style, normalized_strength)
    style_render_ms = _elapsed_ms(render_started)

    write_started = time.perf_counter()
    write_opencv_image(result_path, styled)
    image_write_ms = _elapsed_ms(write_started)

    metadata = {
        "style_id": style.style_id,
        "version": style.version,
        "strength": normalized_strength,
        "family": style.family,
        "display_name": {
            "zh": style.display_name_zh,
            "en": style.display_name_en,
        },
        "recipe_hash": style.recipe_hash,
        "asset_hash": style.asset_hash,
        "renderer_version": STYLE_RENDERER_VERSION,
        "working_color_space": style.renderer["working_color_space"],
        "source_url": style.source["source_url"],
        "license": style.source["license"],
        "review_status": style.review["status"],
    }
    return {
        "engine": "opencv",
        "parameters": resolved,
        "style": metadata,
        "mask_info": None,
        "timings_ms": {
            "image_read": round(image_read_ms, 3),
            "parameter_resolution": round(parameter_resolution_ms, 3),
            "style_render": round(style_render_ms, 3),
            "image_write": round(image_write_ms, 3),
            "total": round(_elapsed_ms(total_started), 3),
        },
        "explanation": (
            f"OpenCV 已套用風格「{style.display_name_zh}」"
            f"（{style.style_id}@{style.version}，"
            f"strength={normalized_strength:g}）。"
        ),
    }


def _apply_recipe(
    image: np.ndarray,
    style: StyleDefinition,
) -> np.ndarray:
    renderer = style.renderer
    adjusted = image
    if renderer.get("lut") is not None:
        adjusted = _apply_lut(adjusted, style)
    if renderer.get("tone_curve") is not None:
        adjusted = _apply_tone_curve(
            adjusted,
            renderer["tone_curve"],
        )
    if renderer.get("color_matrix") is not None:
        adjusted = _apply_color_matrix(
            adjusted,
            renderer["color_matrix"],
        )
    if renderer.get("selective_hsl") is not None:
        adjusted = _apply_selective_hsl(
            adjusted,
            renderer["selective_hsl"],
        )
    if renderer.get("split_tone") is not None:
        adjusted = _apply_split_tone(
            adjusted,
            renderer["split_tone"],
        )
    if renderer.get("monochrome") is not None:
        adjusted = _apply_monochrome(
            adjusted,
            renderer["monochrome"],
        )
    if renderer.get("fade") is not None:
        adjusted = _apply_fade(adjusted, renderer["fade"])
    if renderer.get("grain") is not None:
        adjusted = _apply_grain(adjusted, renderer["grain"])
    return adjusted


def _apply_lut(image: np.ndarray, style: StyleDefinition) -> np.ndarray:
    path = style.asset_path
    if path is None:
        raise StyleAssetError(
            f"Style {style.key} declares a LUT without a valid asset path"
        )
    cpu = _ocio_processor(path, style.asset_hash)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    working = rgb.astype(np.float32) / 255.0
    cpu.applyRGB(working)
    if not np.all(np.isfinite(working)):
        raise StyleAssetError(
            f"Style LUT emitted NaN or Infinity: {style.key}"
        )
    rendered = np.round(np.clip(working, 0.0, 1.0) * 255.0).astype(np.uint8)
    return cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR)


def _ocio_processor(path: Path, asset_hash: str) -> Any:
    key = (str(path.resolve()), asset_hash)
    with _OCIO_CACHE_LOCK:
        cached = _OCIO_PROCESSORS.get(key)
        if cached is not None:
            return cached
        try:
            import PyOpenColorIO as ocio
        except ImportError as exc:
            raise StyleAssetError(
                "OpenColorIO is required to render approved LUT styles"
            ) from exc
        config = ocio.Config.CreateRaw()
        transform = ocio.FileTransform(
            src=str(path.resolve()),
            interpolation=ocio.INTERP_TETRAHEDRAL,
        )
        try:
            processor = config.getProcessor(transform).getDefaultCPUProcessor()
        except Exception as exc:
            raise StyleAssetError(
                f"Unable to compile style LUT {path.name}: {exc}"
            ) from exc
        _OCIO_PROCESSORS[key] = processor
        return processor


def _apply_tone_curve(image: np.ndarray, value: object) -> np.ndarray:
    points = _curve_points(value)
    source = np.array([point[0] for point in points], dtype=np.float32)
    target = np.array([point[1] for point in points], dtype=np.float32)
    samples = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    lookup = np.round(np.interp(samples, source, target) * 255.0)
    return cv2.LUT(image, np.clip(lookup, 0, 255).astype(np.uint8))


def _apply_color_matrix(image: np.ndarray, value: object) -> np.ndarray:
    matrix = np.asarray(value, dtype=np.float32)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise StyleCatalogError("Style color_matrix must be finite 3x3 RGB")
    if np.max(np.abs(matrix)) > 2.0:
        raise StyleCatalogError("Style color_matrix coefficient exceeds 2.0")
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    rendered = rgb @ matrix.T
    rendered = np.round(np.clip(rendered, 0.0, 1.0) * 255.0).astype(np.uint8)
    return cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR)


def _apply_selective_hsl(image: np.ndarray, value: object) -> np.ndarray:
    if not isinstance(value, list):
        raise StyleCatalogError("Style selective_hsl must be a list")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue_samples = np.arange(180, dtype=np.float32) * 2.0
    hue_shift_lookup = np.zeros(180, dtype=np.float32)
    saturation_scale_lookup = np.ones(180, dtype=np.float32)
    value_shift_lookup = np.zeros(180, dtype=np.float32)
    for raw in value:
        if not isinstance(raw, Mapping):
            raise StyleCatalogError(
                "Each selective_hsl operation must be an object"
            )
        center = _bounded(raw.get("hue_center"), 0.0, 360.0, "hue_center")
        width = _bounded(raw.get("width"), 1.0, 180.0, "width")
        hue_shift = _bounded(
            raw.get("hue_shift", 0.0),
            -90.0,
            90.0,
            "hue_shift",
        )
        saturation_scale = _bounded(
            raw.get("saturation_scale", 1.0),
            0.0,
            2.0,
            "saturation_scale",
        )
        value_shift = _bounded(
            raw.get("value_shift", 0.0),
            -0.35,
            0.35,
            "value_shift",
        )
        distance = np.abs(
            ((hue_samples - center + 180.0) % 360.0) - 180.0
        )
        weight = np.clip(1.0 - distance / width, 0.0, 1.0)
        weight = weight * weight * (3.0 - 2.0 * weight)
        hue_shift_lookup += hue_shift * weight
        saturation_scale_lookup *= (
            1.0 + (saturation_scale - 1.0) * weight
        )
        value_shift_lookup += value_shift * weight

    hue_index = hsv[:, :, 0].copy()
    output_hue = np.mod(
        hue_samples + hue_shift_lookup,
        360.0,
    ) / 2.0
    hsv[:, :, 0] = np.round(output_hue[hue_index]).astype(np.uint8)
    hsv[:, :, 1] = np.clip(
        hsv[:, :, 1].astype(np.float32)
        * saturation_scale_lookup[hue_index],
        0.0,
        255.0,
    ).astype(np.uint8)
    hsv[:, :, 2] = np.clip(
        hsv[:, :, 2].astype(np.float32)
        + value_shift_lookup[hue_index] * 255.0,
        0.0,
        255.0,
    ).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def _apply_split_tone(image: np.ndarray, value: object) -> np.ndarray:
    if not isinstance(value, Mapping):
        raise StyleCatalogError("Style split_tone must be an object")
    shadow_color = _rgb_triplet(
        value.get("shadow_color"),
        "split_tone.shadow_color",
    )
    highlight_color = _rgb_triplet(
        value.get("highlight_color"),
        "split_tone.highlight_color",
    )
    shadow_strength = _bounded(
        value.get("shadow_strength", 0.0),
        0.0,
        0.5,
        "split_tone.shadow_strength",
    )
    highlight_strength = _bounded(
        value.get("highlight_strength", 0.0),
        0.0,
        0.5,
        "split_tone.highlight_strength",
    )
    balance = _bounded(
        value.get("balance", 0.5),
        0.2,
        0.8,
        "split_tone.balance",
    )
    luminance_samples = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    shadow_weight = np.clip(
        (balance - luminance_samples) / balance,
        0.0,
        1.0,
    )
    highlight_weight = np.clip(
        (luminance_samples - balance) / (1.0 - balance),
        0.0,
        1.0,
    )
    shadow_delta = shadow_color - np.mean(shadow_color)
    highlight_delta = highlight_color - np.mean(highlight_color)
    delta_lookup = (
        shadow_delta[np.newaxis, :]
        * shadow_weight[:, np.newaxis]
        * shadow_strength
        + highlight_delta[np.newaxis, :]
        * highlight_weight[:, np.newaxis]
        * highlight_strength
    )
    delta_lookup = np.round(delta_lookup * 255.0).astype(np.int16)
    luminance = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    rendered = (
        rgb.astype(np.int16)
        + delta_lookup[luminance]
    )
    rendered = np.clip(rendered, 0, 255).astype(np.uint8)
    return cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR)


def _apply_monochrome(image: np.ndarray, value: object) -> np.ndarray:
    if not isinstance(value, Mapping):
        raise StyleCatalogError("Style monochrome must be an object")
    weights = np.asarray(value.get("weights"), dtype=np.float32)
    if (
        weights.shape != (3,)
        or not np.all(np.isfinite(weights))
        or np.any(weights < 0.0)
        or float(weights.sum()) <= 0.0
    ):
        raise StyleCatalogError(
            "Style monochrome.weights must be three non-negative RGB values"
        )
    weights /= weights.sum()
    tint = _rgb_triplet(value.get("tint", [1.0, 1.0, 1.0]), "monochrome.tint")
    tint_strength = _bounded(
        value.get("tint_strength", 0.0),
        0.0,
        0.35,
        "monochrome.tint_strength",
    )
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    grayscale = np.sum(rgb * weights[np.newaxis, np.newaxis, :], axis=2)
    neutral = np.repeat(grayscale[:, :, np.newaxis], 3, axis=2)
    colorized = neutral * (
        1.0
        + (tint - np.mean(tint))[np.newaxis, np.newaxis, :]
        * tint_strength
    )
    rendered = np.round(np.clip(colorized, 0.0, 1.0) * 255.0).astype(np.uint8)
    return cv2.cvtColor(rendered, cv2.COLOR_RGB2BGR)


def _apply_fade(image: np.ndarray, value: object) -> np.ndarray:
    amount = _bounded(value, 0.0, 0.35, "fade")
    if amount == 0.0:
        return image
    working = image.astype(np.float32) / 255.0
    black_lift = amount * 0.28
    highlight_compression = amount * 0.08
    rendered = working * (1.0 - black_lift - highlight_compression) + black_lift
    return np.round(np.clip(rendered, 0.0, 1.0) * 255.0).astype(np.uint8)


def _apply_grain(image: np.ndarray, value: object) -> np.ndarray:
    if not isinstance(value, Mapping):
        raise StyleCatalogError("Style grain must be an object")
    amount = _bounded(value.get("amount"), 0.0, 0.12, "grain.amount")
    seed_value = value.get("seed")
    if isinstance(seed_value, bool):
        raise StyleCatalogError("grain.seed must be an integer")
    try:
        seed = int(seed_value)
    except (TypeError, ValueError) as exc:
        raise StyleCatalogError("grain.seed must be an integer") from exc
    if amount == 0.0:
        return image
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(image.shape[:2], dtype=np.float32)
    luminance = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    midtone_weight = 0.45 + 0.55 * (1.0 - np.abs(luminance * 2.0 - 1.0))
    delta = noise * (amount * 255.0) * midtone_weight
    rendered = image.astype(np.float32) + delta[:, :, np.newaxis]
    return np.clip(np.round(rendered), 0, 255).astype(np.uint8)


def _blend_style(
    original: np.ndarray,
    styled: np.ndarray,
    strength: float,
) -> np.ndarray:
    if strength <= 0.0:
        return original.copy()
    if strength >= 1.0:
        return styled
    blended = (
        original.astype(np.float32) * (1.0 - strength)
        + styled.astype(np.float32) * strength
    )
    return np.clip(np.round(blended), 0, 255).astype(np.uint8)


def _curve_points(value: object) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list) or not 2 <= len(value) <= 16:
        raise StyleCatalogError("tone_curve must contain 2 to 16 points")
    points: list[tuple[float, float]] = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise StyleCatalogError(
                f"tone_curve point {index} must be [input, output]"
            )
        x = _bounded(point[0], 0.0, 1.0, f"tone_curve[{index}].input")
        y = _bounded(point[1], 0.0, 1.0, f"tone_curve[{index}].output")
        if points and x <= points[-1][0]:
            raise StyleCatalogError(
                "tone_curve inputs must be strictly increasing"
            )
        points.append((x, y))
    if points[0][0] != 0.0 or points[-1][0] != 1.0:
        raise StyleCatalogError("tone_curve must begin at 0 and end at 1")
    return tuple(points)


def _rgb_triplet(value: object, field: str) -> np.ndarray:
    color = np.asarray(value, dtype=np.float32)
    if (
        color.shape != (3,)
        or not np.all(np.isfinite(color))
        or np.any(color < 0.0)
        or np.any(color > 1.0)
    ):
        raise StyleCatalogError(
            f"{field} must be three RGB values in the range 0..1"
        )
    return color


def _bounded(
    value: object,
    minimum: float,
    maximum: float,
    field: str,
) -> float:
    if isinstance(value, bool):
        raise StyleCatalogError(f"{field} must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise StyleCatalogError(f"{field} must be numeric") from exc
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        raise StyleCatalogError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return numeric


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


__all__ = ["create_opencv_style_result"]
