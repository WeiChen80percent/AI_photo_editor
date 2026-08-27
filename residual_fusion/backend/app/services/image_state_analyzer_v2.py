from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import cv2
import numpy as np

from app.services.image_state_analyzer import (
    MODEL_FEATURE_NAMES as BASE_MODEL_FEATURE_NAMES,
    analyze_image_state as analyze_image_state_v1,
)


IMAGE_STATE_VERSION = "opencv_stats_v2"
WHITE_BALANCE_FEATURE_NAMES = (
    "temperature_p25",
    "temperature_p50",
    "temperature_p75",
    "tint_p25",
    "tint_p50",
    "tint_p75",
    "neutral_pixel_ratio",
    "neutral_temperature_signal",
    "neutral_tint_signal",
    "lab_a_median_norm",
    "lab_b_median_norm",
    "lab_a_std_norm",
    "lab_b_std_norm",
)
MODEL_FEATURE_NAMES = BASE_MODEL_FEATURE_NAMES + WHITE_BALANCE_FEATURE_NAMES


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
    """Extend v1 global statistics with robust color-distribution signals."""
    state = analyze_image_state_v1(image, max_side=max_side)
    sample = _resize_max_side(image, max_side)
    bgr = sample.astype(np.float32) / 255.0
    blue, green, red = (bgr[:, :, index] for index in range(3))
    gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    saturation = (
        cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)[:, :, 1].astype(np.float32)
        / 255.0
    )
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lab_a = lab[:, :, 1].astype(np.float32) / 128.0
    lab_b = lab[:, :, 2].astype(np.float32) / 128.0

    temperature = red - blue
    tint = (red + blue) * 0.5 - green
    neutral_mask = (
        (saturation <= 0.25)
        & (gray >= 0.10)
        & (gray <= 0.90)
    )
    neutral_ratio = float(np.mean(neutral_mask))
    if np.any(neutral_mask):
        neutral_temperature = float(np.median(temperature[neutral_mask]))
        neutral_tint = float(np.median(tint[neutral_mask]))
    else:
        neutral_temperature = float(np.median(temperature))
        neutral_tint = float(np.median(tint))

    temperature_p25, temperature_p50, temperature_p75 = np.percentile(
        temperature,
        (25, 50, 75),
    )
    tint_p25, tint_p50, tint_p75 = np.percentile(tint, (25, 50, 75))
    state.update(
        {
            "version": IMAGE_STATE_VERSION,
            "temperature_p25": float(temperature_p25),
            "temperature_p50": float(temperature_p50),
            "temperature_p75": float(temperature_p75),
            "tint_p25": float(tint_p25),
            "tint_p50": float(tint_p50),
            "tint_p75": float(tint_p75),
            "neutral_pixel_ratio": neutral_ratio,
            "neutral_temperature_signal": neutral_temperature,
            "neutral_tint_signal": neutral_tint,
            "lab_a_median_norm": float(np.median(lab_a)),
            "lab_b_median_norm": float(np.median(lab_b)),
            "lab_a_std_norm": float(np.std(lab_a)),
            "lab_b_std_norm": float(np.std(lab_b)),
        }
    )
    return {
        key: round(value, 6) if isinstance(value, float) else value
        for key, value in state.items()
    }


def select_model_features(state: dict[str, Any]) -> dict[str, float]:
    return {name: float(state[name]) for name in MODEL_FEATURE_NAMES}


def format_image_state_for_prompt(state: dict[str, Any]) -> str:
    return json.dumps(
        select_model_features(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _resize_max_side(image: np.ndarray, max_side: int) -> np.ndarray:
    if max_side < 1:
        raise ValueError("max_side must be at least 1.")
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
