import hashlib
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.edit_schema import (
    EDIT_PARAMETER_RANGES,
    default_mask_type_for_region,
    validate_edit_mask_type,
    validate_edit_parameters,
    validate_edit_region,
)
from app.services.semantic_mask_service import get_semantic_region_mask


DEFAULT_OPENCV_PARAMETERS: dict[str, float] = {
    "brightness": 12.0,
    "contrast": 1.08,
    "saturation": 1.12,
    "temperature": 6.0,
    "sharpen": 0.25,
    "clarity": 0.0,
    "dehaze": 0.0,
    "vignette": 0.08,
    "reference_tint": 0.12,
}

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
    resolved = _resolve_parameters(parameters)
    if reference is None:
        resolved["reference_tint"] = 0.0
    parameter_resolution_ms = _elapsed_ms(resolve_started)

    adjustments_started = time.perf_counter()
    adjusted = _apply_brightness_contrast(
        original,
        brightness=resolved["brightness"],
        contrast=resolved["contrast"],
    )
    adjusted = _apply_saturation(adjusted, resolved["saturation"])
    adjusted = _apply_temperature(adjusted, resolved["temperature"])
    adjusted = _apply_dehaze(adjusted, resolved["dehaze"])
    adjusted = _apply_clarity(adjusted, resolved["clarity"])
    if reference is not None:
        adjusted = _apply_reference_tint(adjusted, reference, resolved["reference_tint"])
    adjusted = _apply_sharpen(adjusted, resolved["sharpen"])
    adjusted = _apply_vignette(adjusted, resolved["vignette"])
    adjustments_ms = _elapsed_ms(adjustments_started)
    mask_started = time.perf_counter()
    adjusted, mask_info = _apply_region_mask(
        original=original,
        adjusted=adjusted,
        region=resolved["region"],
        mask_type=resolved["mask_type"],
        mask_source_path=mask_source_path or original_path,
    )
    mask_ms = _elapsed_ms(mask_started)

    write_started = time.perf_counter()
    _write_image(result_path, adjusted)
    image_write_ms = _elapsed_ms(write_started)

    return {
        "engine": "opencv",
        "parameters": resolved,
        "mask_info": mask_info,
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


def _resolve_parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    resolved = DEFAULT_OPENCV_PARAMETERS.copy()
    resolved.update(validate_edit_parameters(parameters))

    for key, value in resolved.items():
        low, high = PARAMETER_RANGES[key]
        resolved[key] = round(float(np.clip(value, low, high)), 4)

    region = validate_edit_region((parameters or {}).get("region"))
    mask_type = validate_edit_mask_type((parameters or {}).get("mask_type"))
    if mask_type == "none":
        mask_type = default_mask_type_for_region(region)
    resolved["region"] = region
    resolved["mask_type"] = mask_type

    return resolved


def _apply_brightness_contrast(
    image: np.ndarray,
    *,
    brightness: float,
    contrast: float,
) -> np.ndarray:
    return cv2.convertScaleAbs(image, alpha=contrast, beta=brightness)


def _apply_saturation(image: np.ndarray, saturation: float) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _apply_temperature(image: np.ndarray, temperature: float) -> np.ndarray:
    if temperature == 0:
        return image

    adjusted = image.astype(np.float32)
    adjusted[:, :, 0] = np.clip(adjusted[:, :, 0] - temperature, 0, 255)
    adjusted[:, :, 2] = np.clip(adjusted[:, :, 2] + temperature, 0, 255)
    return adjusted.astype(np.uint8)


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
) -> tuple[np.ndarray, dict[str, Any] | None]:
    if region == "all" or mask_type == "none":
        return adjusted, None

    mask, mask_info = _build_region_mask(
        original,
        region=region,
        mask_type=mask_type,
        mask_source_path=mask_source_path,
    )
    if mask is None:
        return adjusted, mask_info

    blended = (
        original.astype(np.float32) * (1.0 - mask[:, :, np.newaxis])
        + adjusted.astype(np.float32) * mask[:, :, np.newaxis]
    )
    return np.clip(blended, 0, 255).astype(np.uint8), mask_info


def _build_region_mask(
    image: np.ndarray,
    *,
    region: str,
    mask_type: str,
    mask_source_path: Path,
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
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
        mask = np.clip((130.0 - gray) / 90.0, 0.0, 1.0)
        feathered = _feather_mask(mask)
        return feathered, _local_mask_info(
            target="shadows",
            source="opencv_luminance",
            image=image,
            raw_mask=mask,
            feathered_mask=feathered,
        )

    if region == "highlights" or mask_type == "luminance_highlights":
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
        mask = np.clip((gray - 150.0) / 80.0, 0.0, 1.0)
        feathered = _feather_mask(mask)
        return feathered, _local_mask_info(
            target="highlights",
            source="opencv_luminance",
            image=image,
            raw_mask=mask,
            feathered_mask=feathered,
        )

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

    return None, None


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
        f"brightness={parameters['brightness']}, "
        f"contrast={parameters['contrast']}, "
        f"saturation={parameters['saturation']}, "
        f"temperature={parameters['temperature']}, "
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
