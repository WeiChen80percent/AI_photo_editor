from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

import cv2
import numpy as np

from app.services.edit_schema import EDIT_PARAMETER_SPECS
from app.services.supervised_opencv_processor import assess_smooth_gradient_banding


LOCAL_EDIT_SAFETY_POLICY: dict[str, Any] = {
    "name": "local_clip_chroma_banding_guard_v1",
    "active_mask_threshold": 0.05,
    "maximum_clip_increase": 0.02,
    "maximum_saturation_clip_increase": 0.05,
    "fallback_strengths": (1.0, 0.7, 0.4, 0.2, 0.0),
}


def local_edit_safety_guard_enabled() -> bool:
    return os.environ.get("AI_PHOTO_LOCAL_SAFETY_GUARD", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def select_safe_local_adjustment(
    *,
    original: np.ndarray,
    requested_parameters: Mapping[str, Any],
    mask: np.ndarray,
    initial_adjusted: np.ndarray,
    render_adjustment: Callable[[dict[str, Any]], np.ndarray],
    enabled: bool | None = None,
) -> tuple[np.ndarray, dict[str, Any], dict[str, Any]]:
    """Choose the strongest local render that passes clipping and banding gates."""

    guard_enabled = local_edit_safety_guard_enabled() if enabled is None else enabled
    requested = dict(requested_parameters)
    if not guard_enabled:
        return initial_adjusted, requested, {
            "policy": LOCAL_EDIT_SAFETY_POLICY["name"],
            "enabled": False,
            "triggered": False,
            "action": "disabled",
            "requested_strength": 1.0,
            "effective_strength": 1.0,
            "attempts": [],
        }

    attempts: list[dict[str, Any]] = []
    selected_image = original
    selected_parameters = _scale_local_parameters(requested, 0.0)
    selected_strength = 0.0
    for index, strength in enumerate(LOCAL_EDIT_SAFETY_POLICY["fallback_strengths"]):
        strength = float(strength)
        parameters = requested if strength == 1.0 else _scale_local_parameters(
            requested,
            strength,
        )
        adjusted = initial_adjusted if index == 0 else render_adjustment(parameters)
        candidate = _blend_local_candidate(original, adjusted, mask)
        assessment = _assess_local_edit_safety(original, candidate, mask)
        attempts.append({"strength": strength, **assessment})
        if assessment["safe"]:
            selected_image = adjusted
            selected_parameters = dict(parameters)
            selected_strength = strength
            break

    reduced = selected_strength < 1.0
    return selected_image, selected_parameters, {
        "policy": LOCAL_EDIT_SAFETY_POLICY["name"],
        "enabled": True,
        "triggered": reduced,
        "action": "reduce_strength" if reduced else "keep_requested",
        "requested_strength": 1.0,
        "effective_strength": selected_strength,
        "attempts": attempts,
    }


def blend_local_candidate(
    original: np.ndarray,
    adjusted: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    return _blend_local_candidate(original, adjusted, mask)


def _scale_local_parameters(
    parameters: Mapping[str, Any],
    strength: float,
) -> dict[str, Any]:
    scaled = dict(parameters)
    for key, spec in EDIT_PARAMETER_SPECS.items():
        value = parameters.get(key)
        if value is None:
            continue
        neutral = float(spec["neutral"])
        numeric = neutral + (float(value) - neutral) * strength
        numeric = min(max(numeric, float(spec["minimum"])), float(spec["maximum"]))
        scaled[key] = round(numeric, 4)
    return scaled


def _blend_local_candidate(
    original: np.ndarray,
    adjusted: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    alpha = np.clip(mask, 0.0, 1.0)[:, :, np.newaxis]
    blended = original.astype(np.float32) * (1.0 - alpha) + adjusted.astype(
        np.float32
    ) * alpha
    return np.clip(blended, 0.0, 255.0).astype(np.uint8)


def _assess_local_edit_safety(
    original: np.ndarray,
    candidate: np.ndarray,
    mask: np.ndarray,
) -> dict[str, Any]:
    active = mask > float(LOCAL_EDIT_SAFETY_POLICY["active_mask_threshold"])
    if not np.any(active):
        return {
            "safe": False,
            "reasons": ["empty_mask"],
            "clip_increase": 0.0,
            "saturation_clip_increase": 0.0,
            "banding_triggered": False,
        }

    source_pixels = original[active]
    candidate_pixels = candidate[active]
    source_clip = _clipped_pixel_fraction(source_pixels)
    candidate_clip = _clipped_pixel_fraction(candidate_pixels)
    source_saturation_clip = _saturation_clip_fraction(source_pixels)
    candidate_saturation_clip = _saturation_clip_fraction(candidate_pixels)
    clip_increase = candidate_clip - source_clip
    saturation_clip_increase = candidate_saturation_clip - source_saturation_clip

    y, x = np.where(active)
    y0, y1 = int(y.min()), int(y.max()) + 1
    x0, x1 = int(x.min()), int(x.max()) + 1
    banding = assess_smooth_gradient_banding(
        original[y0:y1, x0:x1],
        candidate[y0:y1, x0:x1],
    )
    reasons: list[str] = []
    if clip_increase > float(LOCAL_EDIT_SAFETY_POLICY["maximum_clip_increase"]):
        reasons.append("clipping")
    if saturation_clip_increase > float(
        LOCAL_EDIT_SAFETY_POLICY["maximum_saturation_clip_increase"]
    ):
        reasons.append("saturation_clipping")
    if banding["triggered"]:
        reasons.append("smooth_gradient_banding")
    return {
        "safe": not reasons,
        "reasons": reasons,
        "clip_increase": round(float(clip_increase), 6),
        "saturation_clip_increase": round(float(saturation_clip_increase), 6),
        "banding_triggered": bool(banding["triggered"]),
        "banding": banding,
    }


def _clipped_pixel_fraction(pixels: np.ndarray) -> float:
    clipped = np.any((pixels <= 2) | (pixels >= 253), axis=1)
    return float(np.mean(clipped))


def _saturation_clip_fraction(pixels: np.ndarray) -> float:
    sample = pixels.reshape((-1, 1, 3)).astype(np.uint8)
    saturation = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)[:, 0, 1]
    return float(np.mean(saturation >= 250))
