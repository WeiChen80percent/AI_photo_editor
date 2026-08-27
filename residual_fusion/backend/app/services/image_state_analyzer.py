from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import cv2
import numpy as np


IMAGE_STATE_VERSION = "opencv_stats_v1"

MODEL_FEATURE_NAMES = (
    "mean_luma",
    "std_luma",
    "luma_p05",
    "luma_p50",
    "luma_p95",
    "luma_dynamic_range",
    "shadow_clip_ratio",
    "highlight_clip_ratio",
    "mean_saturation",
    "std_saturation",
    "mean_red",
    "mean_green",
    "mean_blue",
    "lab_a_mean_norm",
    "lab_b_mean_norm",
    "temperature_signal",
    "tint_signal",
    "sharpness_laplacian_log",
    "luma_entropy_norm",
)


def analyze_image_path(
    path: Path,
    *,
    max_side: int = 256,
) -> dict[str, Any]:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read image for state analysis: {path}")
    return analyze_image_state(image, max_side=max_side)


def analyze_image_state(
    image: np.ndarray,
    *,
    max_side: int = 256,
) -> dict[str, Any]:
    """Extract lightweight input-only statistics for parameter prediction."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Expected a BGR image with shape H x W x 3.")
    if max_side < 1:
        raise ValueError("max_side must be at least 1.")

    source_height, source_width = image.shape[:2]
    sample = _resize_max_side(image, max_side)
    sample_height, sample_width = sample.shape[:2]

    gray_u8 = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    luma = gray_u8.astype(np.float32) / 255.0
    hsv = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1].astype(np.float32) / 255.0

    bgr = sample.astype(np.float32) / 255.0
    mean_blue, mean_green, mean_red = np.mean(bgr, axis=(0, 1))
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    luma_p05, luma_p50, luma_p95 = np.percentile(luma, (5, 50, 95))

    laplacian = cv2.Laplacian(gray_u8, cv2.CV_32F)
    laplacian_variance = float(np.var(laplacian))

    state: dict[str, Any] = {
        "version": IMAGE_STATE_VERSION,
        "source_width": int(source_width),
        "source_height": int(source_height),
        "sample_width": int(sample_width),
        "sample_height": int(sample_height),
        "mean_luma": float(np.mean(luma)),
        "std_luma": float(np.std(luma)),
        "luma_p05": float(luma_p05),
        "luma_p50": float(luma_p50),
        "luma_p95": float(luma_p95),
        "luma_dynamic_range": float(luma_p95 - luma_p05),
        "shadow_clip_ratio": float(np.mean(luma <= 0.02)),
        "highlight_clip_ratio": float(np.mean(luma >= 0.98)),
        "mean_saturation": float(np.mean(saturation)),
        "std_saturation": float(np.std(saturation)),
        "mean_red": float(mean_red),
        "mean_green": float(mean_green),
        "mean_blue": float(mean_blue),
        "lab_a_mean_norm": float(np.mean(lab[:, :, 1]) / 128.0),
        "lab_b_mean_norm": float(np.mean(lab[:, :, 2]) / 128.0),
        "temperature_signal": float(mean_red - mean_blue),
        "tint_signal": float((mean_red + mean_blue) * 0.5 - mean_green),
        "sharpness_laplacian_log": float(np.log1p(laplacian_variance)),
        "luma_entropy_norm": _normalized_entropy(gray_u8),
    }
    return {
        key: round(value, 6) if isinstance(value, float) else value
        for key, value in state.items()
    }


def select_model_features(state: dict[str, Any]) -> dict[str, float]:
    return {
        name: float(state[name])
        for name in MODEL_FEATURE_NAMES
    }


def format_image_state_for_prompt(state: dict[str, Any]) -> str:
    return json.dumps(
        select_model_features(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _resize_max_side(image: np.ndarray, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image

    scale = max_side / float(longest)
    target = (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )
    return cv2.resize(image, target, interpolation=cv2.INTER_AREA)


def _normalized_entropy(gray_u8: np.ndarray) -> float:
    histogram, _ = np.histogram(gray_u8, bins=64, range=(0, 256))
    probabilities = histogram.astype(np.float64)
    probabilities /= max(float(np.sum(probabilities)), 1.0)
    probabilities = probabilities[probabilities > 0]
    entropy = -float(np.sum(probabilities * np.log2(probabilities)))
    return entropy / 6.0
