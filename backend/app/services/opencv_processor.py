from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.edit_schema import EDIT_PARAMETER_RANGES, validate_edit_parameters


DEFAULT_OPENCV_PARAMETERS: dict[str, float] = {
    "brightness": 12.0,
    "contrast": 1.08,
    "saturation": 1.12,
    "temperature": 6.0,
    "sharpen": 0.25,
    "vignette": 0.08,
    "reference_tint": 0.12,
}

PARAMETER_RANGES = EDIT_PARAMETER_RANGES


def create_opencv_result(
    original_path: Path,
    reference_path: Path | None,
    result_path: Path,
    parameters: dict[str, float] | None = None,
) -> dict[str, Any]:
    original = _read_image(original_path, "original")
    reference = _read_image(reference_path, "reference") if reference_path else None
    resolved = _resolve_parameters(parameters)
    if reference is None:
        resolved["reference_tint"] = 0.0

    adjusted = _apply_brightness_contrast(
        original,
        brightness=resolved["brightness"],
        contrast=resolved["contrast"],
    )
    adjusted = _apply_saturation(adjusted, resolved["saturation"])
    adjusted = _apply_temperature(adjusted, resolved["temperature"])
    if reference is not None:
        adjusted = _apply_reference_tint(adjusted, reference, resolved["reference_tint"])
    adjusted = _apply_sharpen(adjusted, resolved["sharpen"])
    adjusted = _apply_vignette(adjusted, resolved["vignette"])

    _write_image(result_path, adjusted)

    return {
        "engine": "opencv",
        "parameters": resolved,
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


def _resolve_parameters(parameters: dict[str, float] | None) -> dict[str, float]:
    resolved = DEFAULT_OPENCV_PARAMETERS.copy()
    resolved.update(validate_edit_parameters(parameters))

    for key, value in resolved.items():
        low, high = PARAMETER_RANGES[key]
        resolved[key] = round(float(np.clip(value, low, high)), 4)

    return resolved


def _apply_brightness_contrast(
    image: np.ndarray,
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


def _build_explanation(parameters: dict[str, float]) -> str:
    return (
        "OpenCV 已套用參數："
        f"brightness={parameters['brightness']}, "
        f"contrast={parameters['contrast']}, "
        f"saturation={parameters['saturation']}, "
        f"temperature={parameters['temperature']}, "
        f"sharpen={parameters['sharpen']}, "
        f"vignette={parameters['vignette']}, "
        f"reference_tint={parameters['reference_tint']}."
    )
