import hashlib
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.edit_schema import (
    EDIT_PARAMETER_RANGES,
    require_region_mask_pair,
    validate_edit_parameters,
)
from app.services.local_edit_safety import (
    blend_local_candidate,
    local_edit_safety_guard_enabled,
    select_safe_local_adjustment,
)
from app.services.render_contract import OPENCV_RENDER_CONTRACT
from app.services.semantic_mask_service import get_semantic_region_mask


DEFAULT_OPENCV_PARAMETERS: dict[str, float] = {
    "exposure": 0.0,
    "brightness": 12.0,
    "contrast": 1.08,
    "highlights": 0.0,
    "shadows": 0.0,
    "whites": 0.0,
    "blacks": 0.0,
    "saturation": 1.12,
    "vibrance": 0.0,
    "temperature": 6.0,
    "white_balance_tint": 0.0,
    "sharpen": 0.25,
    "clarity": 0.0,
    "dehaze": 0.0,
    "vignette": 0.08,
    "reference_tint": 0.12,
}

if frozenset(DEFAULT_OPENCV_PARAMETERS) != (
    OPENCV_RENDER_CONTRACT.all_parameter_keys
):
    raise RuntimeError(
        "OpenCV defaults do not match the executable render contract"
    )

PARAMETER_RANGES = EDIT_PARAMETER_RANGES
MASK_ARTIFACT_ROOT = Path(__file__).resolve().parents[2] / "storage" / "masks"


def create_opencv_result(
    original_path: Path,
    reference_path: Path | None,
    result_path: Path,
    parameters: dict[str, Any] | None = None,
    mask_source_path: Path | None = None,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    read_started = time.perf_counter()
    original = _read_image(original_path, "original")
    reference = _read_image(reference_path, "reference") if reference_path else None
    image_read_ms = _elapsed_ms(read_started)
    resolve_started = time.perf_counter()
    resolved = resolve_opencv_parameters(parameters)
    if reference is None:
        resolved["reference_tint"] = 0.0
    parameter_resolution_ms = _elapsed_ms(resolve_started)

    adjustments_started = time.perf_counter()
    requested_adjusted = apply_opencv_global_adjustments(
        original,
        resolved,
        reference=reference,
    )
    adjustments_ms = _elapsed_ms(adjustments_started)
    mask_started = time.perf_counter()
    resolved_mask_source_path = mask_source_path or original_path
    mask_source_image = original
    if (
        resolved["region"] in {"highlights", "shadows"}
        or resolved["mask_type"] in {
            "luminance_highlights",
            "luminance_shadows",
        }
    ) and resolved_mask_source_path.resolve() != original_path.resolve():
        mask_source_image = _read_image(
            resolved_mask_source_path,
            "mask source",
        )
    adjusted, mask_info, local_mask = _apply_region_mask(
        original=original,
        adjusted=requested_adjusted,
        region=resolved["region"],
        mask_type=resolved["mask_type"],
        mask_source_path=resolved_mask_source_path,
        mask_source_image=mask_source_image,
    )
    render_safety = None
    render_variant = None
    if local_mask is not None:
        requested_parameters = dict(resolved)
        selected_adjusted, resolved, render_safety = select_safe_local_adjustment(
            original=original,
            requested_parameters=requested_parameters,
            mask=local_mask,
            initial_adjusted=requested_adjusted,
            render_adjustment=lambda candidate_parameters: (
                apply_opencv_global_adjustments(
                    original,
                    candidate_parameters,
                    reference=reference,
                )
            ),
        )
        adjusted = blend_local_candidate(original, selected_adjusted, local_mask)
        render_safety.update(
            {
                "region": resolved["region"],
                "requested_parameters": requested_parameters,
                "effective_parameters": dict(resolved),
            }
        )
        render_variant = "local_safe_strength_v1"
    mask_ms = _elapsed_ms(mask_started)

    write_started = time.perf_counter()
    _write_image(result_path, adjusted)
    image_write_ms = _elapsed_ms(write_started)

    return {
        "engine": "opencv",
        "parameters": resolved,
        "mask_info": mask_info,
        "render_variant": render_variant,
        "render_safety": render_safety,
        "timings_ms": {
            "image_read": round(image_read_ms, 3),
            "parameter_resolution": round(parameter_resolution_ms, 3),
            "adjustments": round(adjustments_ms, 3),
            "mask": round(mask_ms, 3),
            "image_write": round(image_write_ms, 3),
            "total": round(_elapsed_ms(total_started), 3),
        },
        "explanation": _build_explanation(resolved),
    }


def create_compound_local_opencv_result(
    *,
    original_path: Path,
    result_path: Path,
    operation_parameters: list[dict[str, Any]],
    mask_source_path: Path | None = None,
) -> dict[str, Any]:
    """Render disjoint local operations from one base and quantize once."""

    if not 2 <= len(operation_parameters) <= 4:
        raise ValueError("Compound local rendering requires two to four operations")
    total_started = time.perf_counter()
    original = _read_image(original_path, "original")
    resolved_operations = [
        resolve_opencv_parameters(parameters)
        for parameters in operation_parameters
    ]
    allowed_regions = {"background", "sky", "person"}
    regions = [str(parameters["region"]) for parameters in resolved_operations]
    if any(region not in allowed_regions for region in regions):
        raise ValueError("Compound local rendering supports sky, person, and background")
    if len(set(regions)) != len(regions):
        raise ValueError("Compound local rendering requires unique regions")

    source_path = mask_source_path or original_path
    priority = {"background": 0, "sky": 1, "person": 2}
    guard_enabled = local_edit_safety_guard_enabled()
    layers: list[
        tuple[
            dict[str, Any],
            np.ndarray,
            dict[str, Any],
            np.ndarray,
            dict[str, Any],
        ]
    ] = []
    for parameters in sorted(
        resolved_operations,
        key=lambda item: priority[str(item["region"])],
    ):
        parameters["reference_tint"] = 0.0
        requested_parameters = dict(parameters)
        mask, mask_info = _build_region_mask(
            original,
            region=str(parameters["region"]),
            mask_type=str(parameters["mask_type"]),
            mask_source_path=source_path,
            mask_source_image=original,
        )
        if mask is None or mask_info is None:
            raise ValueError("Compound local operation produced no semantic mask")
        requested_adjusted = apply_opencv_global_adjustments(
            original,
            requested_parameters,
        )
        selected_adjusted, effective_parameters, safety = (
            select_safe_local_adjustment(
                original=original,
                requested_parameters=requested_parameters,
                mask=mask,
                initial_adjusted=requested_adjusted,
                render_adjustment=lambda candidate_parameters: (
                    apply_opencv_global_adjustments(
                        original,
                        candidate_parameters,
                    )
                ),
                enabled=guard_enabled,
            )
        )
        safety.update(
            {
                "region": effective_parameters["region"],
                "requested_parameters": requested_parameters,
                "effective_parameters": dict(effective_parameters),
            }
        )
        effective_mask_info = {
            **mask_info,
            "effective_strength": safety["effective_strength"],
        }
        layers.append(
            (
                effective_parameters,
                mask,
                effective_mask_info,
                selected_adjusted,
                safety,
            )
        )

    composed = original.astype(np.float32)
    mask_records: list[dict[str, Any]] = []
    safety_records: list[dict[str, Any]] = []
    for parameters, mask, layer_info, adjusted, safety in layers:
        alpha = np.clip(mask, 0.0, 1.0)[:, :, np.newaxis]
        composed = composed * (1.0 - alpha) + adjusted.astype(np.float32) * alpha
        mask_records.append(layer_info)
        safety_records.append(safety)

    output = np.clip(composed, 0.0, 255.0).astype(np.uint8)
    _write_image(result_path, output)
    ordered_parameters = [item[0] for item in layers]
    reduced_count = sum(bool(item["triggered"]) for item in safety_records)
    render_safety = {
        "policy": safety_records[0]["policy"],
        "enabled": guard_enabled,
        "triggered": reduced_count > 0,
        "action": "per_region_selection" if guard_enabled else "disabled",
        "reduced_operation_count": reduced_count,
        "operations": safety_records,
    }
    return {
        "engine": "opencv",
        "parameters": {
            "type": "compound_local",
            "operations": ordered_parameters,
        },
        "mask_info": {
            "type": "compound_local",
            "composition_order": [item["region"] for item in ordered_parameters],
            "masks": mask_records,
        },
        "render_variant": "compound_local_safe_strength_v1",
        "render_safety": render_safety,
        "timings_ms": {
            "total": round(_elapsed_ms(total_started), 3),
        },
        "explanation": (
            f"OpenCV atomically composited {len(layers)} disjoint local operations; "
            f"the safety selector reduced {reduced_count} operation(s)."
        ),
    }

def _read_image(path: Path, label: str) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read {label} image: {path}")
    return image


def _write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(path.suffix or ".png", image)
    if not success:
        raise RuntimeError(f"Failed to encode OpenCV result for {path}")
    encoded.tofile(path)


def read_opencv_image(path: Path, label: str = "image") -> np.ndarray:
    return _read_image(path, label)


def write_opencv_image(path: Path, image: np.ndarray) -> None:
    _write_image(path, image)


def resolve_opencv_parameters(
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve one complete, validated OpenCV parameter snapshot."""

    return _resolve_parameters(parameters)


def apply_opencv_global_adjustments(
    image: np.ndarray,
    parameters: dict[str, Any],
    *,
    reference: np.ndarray | None = None,
) -> np.ndarray:
    """Apply the executable global OpenCV pipeline to an in-memory image.

    Region masks intentionally remain in ``create_opencv_result``.  The style
    renderer uses this function only for whole-image recipes so parameter
    behavior stays identical instead of being reimplemented in two places.
    """

    adjusted = _apply_exposure(image, float(parameters["exposure"]))
    adjusted = _apply_brightness_contrast(
        adjusted,
        brightness=float(parameters["brightness"]),
        contrast=float(parameters["contrast"]),
    )
    adjusted = _apply_tonal_controls(
        adjusted,
        highlights=float(parameters["highlights"]),
        shadows=float(parameters["shadows"]),
    )
    adjusted = _apply_white_black_controls(
        adjusted,
        whites=float(parameters["whites"]),
        blacks=float(parameters["blacks"]),
    )
    adjusted = _apply_saturation(adjusted, float(parameters["saturation"]))
    adjusted = _apply_vibrance(adjusted, float(parameters["vibrance"]))
    adjusted = _apply_temperature(adjusted, float(parameters["temperature"]))
    adjusted = _apply_white_balance_tint(
        adjusted,
        float(parameters["white_balance_tint"]),
    )
    adjusted = _apply_dehaze(adjusted, float(parameters["dehaze"]))
    adjusted = _apply_clarity(adjusted, float(parameters["clarity"]))
    if reference is not None:
        adjusted = _apply_reference_tint(
            adjusted,
            reference,
            float(parameters["reference_tint"]),
        )
    adjusted = _apply_sharpen(adjusted, float(parameters["sharpen"]))
    adjusted = _apply_vignette(adjusted, float(parameters["vignette"]))
    return adjusted


def _resolve_parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    resolved = DEFAULT_OPENCV_PARAMETERS.copy()
    resolved.update(validate_edit_parameters(parameters))

    for key, value in resolved.items():
        low, high = PARAMETER_RANGES[key]
        resolved[key] = round(float(np.clip(value, low, high)), 4)

    region, mask_type = require_region_mask_pair(
        (parameters or {}).get("region"),
        (parameters or {}).get("mask_type"),
    )
    resolved["region"] = region
    resolved["mask_type"] = mask_type

    return resolved


def _apply_brightness_contrast(
    image: np.ndarray,
    brightness: float,
    contrast: float,
) -> np.ndarray:
    midpoint = 127.5
    adjusted = (
        (image.astype(np.float32) - midpoint) * contrast
        + midpoint
        + brightness
    )
    return np.clip(adjusted, 0, 255).astype(np.uint8)


def _apply_exposure(image: np.ndarray, exposure: float) -> np.ndarray:
    if exposure == 0:
        return image

    encoded = np.arange(256, dtype=np.float32) / 255.0
    linear = np.where(
        encoded <= 0.04045,
        encoded / 12.92,
        np.power((encoded + 0.055) / 1.055, 2.4),
    )
    adjusted_linear = np.clip(linear * (2.0 ** exposure), 0.0, 1.0)
    adjusted_encoded = np.where(
        adjusted_linear <= 0.0031308,
        adjusted_linear * 12.92,
        1.055 * np.power(adjusted_linear, 1.0 / 2.4) - 0.055,
    )
    lookup = np.round(np.clip(adjusted_encoded, 0.0, 1.0) * 255.0).astype(
        np.uint8
    )
    return cv2.LUT(image, lookup)


def _apply_tonal_controls(
    image: np.ndarray,
    *,
    highlights: float,
    shadows: float,
) -> np.ndarray:
    if highlights == 0 and shadows == 0:
        return image

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    luminance = lab[:, :, 0]
    adjusted = luminance.copy()

    if highlights != 0:
        weight = _smoothstep(118.0, 235.0, luminance)
        adjusted = _apply_weighted_luminance_delta(
            adjusted,
            weight=weight,
            amount=highlights / 100.0,
            positive_strength=0.55,
            negative_strength=0.62,
        )

    if shadows != 0:
        weight = 1.0 - _smoothstep(20.0, 145.0, luminance)
        adjusted = _apply_weighted_luminance_delta(
            adjusted,
            weight=weight,
            amount=shadows / 100.0,
            positive_strength=0.62,
            negative_strength=0.52,
        )

    lab[:, :, 0] = np.clip(adjusted, 0.0, 255.0)
    return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)


def _apply_weighted_luminance_delta(
    luminance: np.ndarray,
    *,
    weight: np.ndarray,
    amount: float,
    positive_strength: float,
    negative_strength: float,
) -> np.ndarray:
    if amount > 0:
        delta = amount * positive_strength * weight * (255.0 - luminance)
    else:
        delta = amount * negative_strength * weight * luminance
    return np.clip(luminance + delta, 0.0, 255.0)


def _smoothstep(edge0: float, edge1: float, values: np.ndarray) -> np.ndarray:
    normalized = np.clip((values - edge0) / (edge1 - edge0), 0.0, 1.0)
    return normalized * normalized * (3.0 - 2.0 * normalized)


def _apply_saturation(image: np.ndarray, saturation: float) -> np.ndarray:
    if saturation == 1.0:
        return image

    image_float = image.astype(np.float32)
    grayscale = (
        image_float[:, :, 0] * 0.0722
        + image_float[:, :, 1] * 0.7152
        + image_float[:, :, 2] * 0.2126
    )
    grayscale_bgr = np.repeat(grayscale[:, :, np.newaxis], 3, axis=2)
    adjusted = grayscale_bgr + saturation * (
        image_float - grayscale_bgr
    )
    return np.clip(adjusted, 0, 255).astype(np.uint8)


def _apply_vibrance(image: np.ndarray, vibrance: float) -> np.ndarray:
    if vibrance == 0:
        return image

    hsv = cv2.cvtColor(
        image.astype(np.float32) / 255.0,
        cv2.COLOR_BGR2HSV,
    )
    saturation = hsv[:, :, 1]
    if vibrance > 0:
        chroma_guard = _smoothstep(0.025, 0.22, saturation)
        headroom = (1.0 - saturation) ** 1.35
        saturation = saturation + vibrance * 0.78 * chroma_guard * headroom
    else:
        saturation = saturation * (1.0 + vibrance * 0.72)
    hsv[:, :, 1] = np.clip(saturation, 0.0, 1.0)
    rendered = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return np.round(np.clip(rendered, 0.0, 1.0) * 255.0).astype(np.uint8)


def _apply_temperature(image: np.ndarray, temperature: float) -> np.ndarray:
    if temperature == 0:
        return image

    adjusted = image.astype(np.float32)
    adjusted[:, :, 0] = np.clip(adjusted[:, :, 0] - temperature, 0, 255)
    adjusted[:, :, 2] = np.clip(adjusted[:, :, 2] + temperature, 0, 255)
    return adjusted.astype(np.uint8)


def _apply_white_balance_tint(
    image: np.ndarray,
    tint: float,
) -> np.ndarray:
    if tint == 0:
        return image

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[:, :, 1] = np.clip(lab[:, :, 1] + tint * 0.72, 0.0, 255.0)
    return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)


def _apply_white_black_controls(
    image: np.ndarray,
    *,
    whites: float,
    blacks: float,
) -> np.ndarray:
    if whites == 0 and blacks == 0:
        return image

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB).astype(np.float32)
    luminance = lab[:, :, 0]
    adjusted = luminance.copy()
    if whites != 0:
        weight = _smoothstep(178.0, 248.0, luminance)
        adjusted = _apply_weighted_luminance_delta(
            adjusted,
            weight=weight,
            amount=whites / 100.0,
            positive_strength=0.72,
            negative_strength=0.32,
        )
    if blacks != 0:
        weight = 1.0 - _smoothstep(8.0, 82.0, luminance)
        adjusted = _apply_weighted_luminance_delta(
            adjusted,
            weight=weight,
            amount=blacks / 100.0,
            positive_strength=0.34,
            negative_strength=0.62,
        )
    lab[:, :, 0] = np.clip(adjusted, 0.0, 255.0)
    return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)


def _apply_reference_tint(
    image: np.ndarray,
    reference: np.ndarray,
    strength: float,
) -> np.ndarray:
    if strength == 0:
        return image

    mean_bgr = np.array(cv2.mean(reference)[:3], dtype=np.float32)
    overlay = np.full_like(image, mean_bgr, dtype=np.float32)
    blended = cv2.addWeighted(
        image.astype(np.float32),
        1.0 - strength,
        overlay,
        strength,
        0,
    )
    return np.clip(blended, 0, 255).astype(np.uint8)


def _apply_sharpen(image: np.ndarray, amount: float) -> np.ndarray:
    if amount == 0:
        return image

    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=1.0)
    sharpened = cv2.addWeighted(image, 1.0 + amount, blurred, -amount, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def _apply_clarity(image: np.ndarray, amount: float) -> np.ndarray:
    if amount == 0:
        return image

    strength = amount * 0.85
    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=3.0)
    clarified = cv2.addWeighted(image, 1.0 + strength, blurred, -strength, 0)
    return np.clip(clarified, 0, 255).astype(np.uint8)


def _apply_dehaze(image: np.ndarray, amount: float) -> np.ndarray:
    if amount == 0:
        return image

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=1.0 + amount * 2.0,
        tileGridSize=(8, 8),
    )
    enhanced_l = clahe.apply(l_channel)
    blended_l = cv2.addWeighted(
        l_channel,
        1.0 - amount * 0.75,
        enhanced_l,
        amount * 0.75,
        0,
    )
    enhanced_lab = cv2.merge((blended_l, a_channel, b_channel))
    enhanced = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)
    alpha = 1.0 + amount * 0.12
    beta = -amount * 4.0
    adjusted = enhanced.astype(np.float32) * alpha + beta
    return np.clip(adjusted, 0, 255).astype(np.uint8)


def _apply_vignette(image: np.ndarray, amount: float) -> np.ndarray:
    if amount == 0:
        return image

    height, width = image.shape[:2]
    y = np.linspace(-1.0, 1.0, height, dtype=np.float32)
    x = np.linspace(-1.0, 1.0, width, dtype=np.float32)
    xv, yv = np.meshgrid(x, y)
    distance = np.sqrt(xv * xv + yv * yv)
    mask = 1.0 - amount * np.clip(distance, 0.0, 1.0)
    vignetted = image.astype(np.float32) * mask[:, :, np.newaxis]
    return np.clip(vignetted, 0, 255).astype(np.uint8)


def _apply_region_mask(
    *,
    original: np.ndarray,
    adjusted: np.ndarray,
    region: str,
    mask_type: str,
    mask_source_path: Path,
    mask_source_image: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any] | None, np.ndarray | None]:
    if region == "all" or mask_type == "none":
        return adjusted, None, None

    mask, mask_info = _build_region_mask(
        original,
        region=region,
        mask_type=mask_type,
        mask_source_path=mask_source_path,
        mask_source_image=mask_source_image,
    )
    if mask is None:
        raise ValueError(
            "A validated local edit produced no mask; refusing to widen the "
            f"edit to the full image (region={region!r}, "
            f"mask_type={mask_type!r})."
        )

    blended = blend_local_candidate(original, adjusted, mask)
    return blended, mask_info, mask

def _build_region_mask(
    image: np.ndarray,
    *,
    region: str,
    mask_type: str,
    mask_source_path: Path,
    mask_source_image: np.ndarray,
) -> tuple[np.ndarray | None, dict[str, Any] | None]:
    semantic_target = _semantic_target_for_mask(region=region, mask_type=mask_type)
    if semantic_target is not None:
        semantic_result = get_semantic_region_mask(mask_source_path, semantic_target)
        semantic_mask = semantic_result.feathered_mask
        if semantic_mask.shape != image.shape[:2]:
            semantic_mask = cv2.resize(
                semantic_mask,
                (image.shape[1], image.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        return np.clip(semantic_mask, 0.0, 1.0), semantic_result.info

    if region == "shadows" or mask_type == "luminance_shadows":
        gray = cv2.cvtColor(
            mask_source_image,
            cv2.COLOR_BGR2GRAY,
        ).astype(np.float32)
        mask = np.clip((130.0 - gray) / 90.0, 0.0, 1.0)
        feathered = _feather_mask(mask)
        mask_info = _local_mask_info(
            target="shadows",
            source="opencv_luminance",
            image=mask_source_image,
            raw_mask=mask,
            feathered_mask=feathered,
        )
        if feathered.shape != image.shape[:2]:
            feathered = cv2.resize(
                feathered,
                (image.shape[1], image.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        return np.clip(feathered, 0.0, 1.0), mask_info

    if region == "highlights" or mask_type == "luminance_highlights":
        gray = cv2.cvtColor(
            mask_source_image,
            cv2.COLOR_BGR2GRAY,
        ).astype(np.float32)
        mask = np.clip((gray - 150.0) / 80.0, 0.0, 1.0)
        feathered = _feather_mask(mask)
        mask_info = _local_mask_info(
            target="highlights",
            source="opencv_luminance",
            image=mask_source_image,
            raw_mask=mask,
            feathered_mask=feathered,
        )
        if feathered.shape != image.shape[:2]:
            feathered = cv2.resize(
                feathered,
                (image.shape[1], image.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        return np.clip(feathered, 0.0, 1.0), mask_info

    if region == "center" or mask_type == "center_ellipse":
        mask = _center_mask(image.shape[:2])
        feathered = _feather_mask(mask)
        return feathered, _local_mask_info(
            target="center",
            source="opencv_geometry",
            image=image,
            raw_mask=mask,
            feathered_mask=feathered,
        )

    if region == "edges" or mask_type == "edge_vignette":
        mask = 1.0 - _center_mask(image.shape[:2])
        feathered = _feather_mask(mask)
        return feathered, _local_mask_info(
            target="edges",
            source="opencv_geometry",
            image=image,
            raw_mask=mask,
            feathered_mask=feathered,
        )

    raise ValueError(
        "No mask implementation exists for the validated region/mask pair: "
        f"region={region!r}, mask_type={mask_type!r}"
    )


def _semantic_target_for_mask(*, region: str, mask_type: str) -> str | None:
    mapping = {
        "semantic_sky": "sky",
        "semantic_person": "person",
        "semantic_background": "background",
    }
    if mask_type in mapping:
        return mapping[mask_type]
    if region in {"sky", "person", "background"}:
        return region
    return None


def _local_mask_info(
    *,
    target: str,
    source: str,
    image: np.ndarray,
    raw_mask: np.ndarray,
    feathered_mask: np.ndarray,
) -> dict[str, Any]:
    digest = hashlib.sha256()
    digest.update(f"opencv_mask_v1|{target}|{source}|".encode("utf-8"))
    digest.update(np.ascontiguousarray(image).data)
    cache_id = f"opencv_{digest.hexdigest()[:24]}"
    cache_dir = MASK_ARTIFACT_ROOT / cache_id
    raw_path = cache_dir / f"{target}_raw.png"
    feathered_path = cache_dir / f"{target}_feathered.png"
    overlay_path = cache_dir / f"{target}_overlay.jpg"
    cache_hit = all(path.is_file() for path in (raw_path, feathered_path, overlay_path))
    if not cache_hit:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _write_image(raw_path, np.round(np.clip(raw_mask, 0.0, 1.0) * 255).astype(np.uint8))
        _write_image(
            feathered_path,
            np.round(np.clip(feathered_mask, 0.0, 1.0) * 255).astype(np.uint8),
        )
        _write_image(
            overlay_path,
            _mask_overlay(image, feathered_mask, target=target),
        )
    return {
        "target": target,
        "source": source,
        "cache_id": cache_id,
        "cache_hit": cache_hit,
        "raw_mask_path": str(raw_path.resolve()),
        "feathered_mask_path": str(feathered_path.resolve()),
        "overlay_path": str(overlay_path.resolve()),
        "coverage": round(float(np.mean(feathered_mask > 0.05)), 6),
        "confidence": None,
        "found": True,
        "failure_reason": None,
    }


def _mask_overlay(
    image: np.ndarray,
    feathered_mask: np.ndarray,
    *,
    target: str,
) -> np.ndarray:
    colors = {
        "shadows": np.array([40, 125, 255], dtype=np.float32),
        "highlights": np.array([40, 220, 255], dtype=np.float32),
        "center": np.array([220, 80, 170], dtype=np.float32),
        "edges": np.array([120, 190, 60], dtype=np.float32),
    }
    color = colors.get(target, np.array([80, 180, 255], dtype=np.float32))
    alpha = np.clip(feathered_mask, 0.0, 1.0)[:, :, np.newaxis] * 0.55
    overlay = image.astype(np.float32) * (1.0 - alpha) + color * alpha
    return np.clip(overlay, 0, 255).astype(np.uint8)


def _center_mask(shape: tuple[int, int]) -> np.ndarray:
    height, width = shape
    y = np.linspace(-1.0, 1.0, height, dtype=np.float32)
    x = np.linspace(-1.0, 1.0, width, dtype=np.float32)
    xv, yv = np.meshgrid(x, y)
    distance = np.sqrt((xv / 0.9) ** 2 + (yv / 0.75) ** 2)
    return np.clip(1.0 - distance, 0.0, 1.0)


def _feather_mask(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape[:2]
    kernel = max(3, (min(height, width) // 10) * 2 + 1)
    feathered = cv2.GaussianBlur(mask.astype(np.float32), (kernel, kernel), 0)
    return np.clip(feathered, 0.0, 1.0)


def _build_explanation(parameters: dict[str, Any]) -> str:
    return (
        "OpenCV 已套用參數："
        f"exposure={parameters['exposure']}, "
        f"brightness={parameters['brightness']}, "
        f"contrast={parameters['contrast']}, "
        f"highlights={parameters['highlights']}, "
        f"shadows={parameters['shadows']}, "
        f"whites={parameters['whites']}, "
        f"blacks={parameters['blacks']}, "
        f"saturation={parameters['saturation']}, "
        f"vibrance={parameters['vibrance']}, "
        f"temperature={parameters['temperature']}, "
        f"white_balance_tint={parameters['white_balance_tint']}, "
        f"sharpen={parameters['sharpen']}, "
        f"clarity={parameters['clarity']}, "
        f"dehaze={parameters['dehaze']}, "
        f"vignette={parameters['vignette']}, "
        f"reference_tint={parameters['reference_tint']}, "
        f"region={parameters['region']}, "
        f"mask_type={parameters['mask_type']}."
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
