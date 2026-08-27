from __future__ import annotations

import math
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import cv2
import numpy as np


SUPERVISED_RENDER_PROFILE = "expert_c_legacy_v1"
SUPERVISED_RENDER_PROFILE_VERSION = "1.1"

BANDING_GUARD_POLICY: dict[str, Any] = {
    "name": "smooth_chroma_banding_guard_v1",
    "analysis_max_side": 512,
    "minimum_smooth_coverage": 0.08,
    "minimum_neighbor_pairs": 4096,
    "minimum_candidate_jump_ratio": 0.02,
    "minimum_jump_ratio": 2.5,
    "minimum_flat_delta": 0.025,
    "source_jump_ratio_floor": 0.002,
    "fallback_strengths": (1.0, 0.7, 0.4, 0.1, 0.0),
}

SUPERVISED_PARAMETER_RANGES: dict[str, tuple[float, float]] = {
    "brightness": (-80.0, 80.0),
    "contrast": (0.5, 1.8),
    "gamma": (0.5, 2.0),
    "saturation": (0.0, 2.0),
    "temperature": (-50.0, 50.0),
    "tint": (-50.0, 50.0),
    "sharpen": (0.0, 1.0),
    "vignette": (-0.5, 0.8),
    "reference_tint": (0.0, 0.5),
}

SUPERVISED_DEFAULT_PARAMETERS: dict[str, float] = {
    "brightness": 12.0,
    "contrast": 1.08,
    "gamma": 1.0,
    "saturation": 1.12,
    "temperature": 6.0,
    "tint": 0.0,
    "sharpen": 0.25,
    "vignette": 0.08,
    "reference_tint": 0.12,
}

SUPERVISED_IDENTITY_PARAMETERS: dict[str, float] = {
    "brightness": 0.0,
    "contrast": 1.0,
    "gamma": 1.0,
    "saturation": 1.0,
    "temperature": 0.0,
    "tint": 0.0,
    "sharpen": 0.0,
    "vignette": 0.0,
    "reference_tint": 0.0,
}


def is_supervised_render_plan(edit_plan: Mapping[str, Any] | None) -> bool:
    return bool(
        isinstance(edit_plan, Mapping)
        and str(edit_plan.get("render_profile") or "") == SUPERVISED_RENDER_PROFILE
    )


def create_supervised_opencv_result(
    *,
    original_path: Path,
    result_path: Path,
    parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    total_started = time.perf_counter()
    read_started = time.perf_counter()
    original = _read_image(original_path)
    image_read_ms = _elapsed_ms(read_started)

    adjustment_started = time.perf_counter()
    adjusted, resolved, render_safety = apply_supervised_opencv_parameters_guarded(
        original,
        parameters,
        enabled=supervised_banding_guard_enabled(),
    )
    adjustments_ms = _elapsed_ms(adjustment_started)

    write_started = time.perf_counter()
    _write_image(result_path, adjusted)
    image_write_ms = _elapsed_ms(write_started)

    return {
        "engine": "opencv",
        "parameters": resolved,
        "mask_info": None,
        "render_profile": SUPERVISED_RENDER_PROFILE,
        "render_variant": render_safety["render_variant"],
        "render_safety": render_safety,
        "timings_ms": {
            "image_read": round(image_read_ms, 3),
            "parameter_resolution": 0.0,
            "adjustments": round(adjustments_ms, 3),
            "mask": 0.0,
            "image_write": round(image_write_ms, 3),
            "total": round(_elapsed_ms(total_started), 3),
        },
        "explanation": _build_explanation(resolved, render_safety=render_safety),
    }


def supervised_banding_guard_enabled() -> bool:
    value = os.getenv("AI_PHOTO_BANDING_GUARD", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def apply_supervised_opencv_parameters_guarded(
    image: np.ndarray,
    parameters: Mapping[str, Any] | None,
    *,
    enabled: bool = True,
) -> tuple[np.ndarray, dict[str, float], dict[str, Any]]:
    """Keep the frozen renderer unless it visibly quantizes smooth color."""

    legacy, resolved = apply_supervised_opencv_parameters(image, parameters)
    legacy_assessment = assess_smooth_gradient_banding(image, legacy)
    base_safety: dict[str, Any] = {
        "policy": BANDING_GUARD_POLICY["name"],
        "enabled": bool(enabled),
        "triggered": bool(legacy_assessment["triggered"]),
        "legacy_assessment": legacy_assessment,
        "attempts": [],
    }
    if not enabled:
        return legacy, resolved, {
            **base_safety,
            "action": "disabled",
            "render_variant": "legacy_uint8",
            "effective_strength": 1.0,
            "parameters_adjusted": False,
            "final_assessment": legacy_assessment,
        }
    if not legacy_assessment["triggered"]:
        return legacy, resolved, {
            **base_safety,
            "action": "keep_legacy",
            "render_variant": "legacy_uint8",
            "effective_strength": 1.0,
            "parameters_adjusted": False,
            "final_assessment": legacy_assessment,
        }

    best: tuple[
        float,
        float,
        np.ndarray,
        dict[str, float],
        dict[str, Any],
    ] | None = None
    attempts: list[dict[str, Any]] = []
    for strength_value in BANDING_GUARD_POLICY["fallback_strengths"]:
        strength = float(strength_value)
        adjusted_parameters = _scale_supervised_parameters(resolved, strength)
        candidate, candidate_resolved = apply_supervised_opencv_parameters_float32(
            image,
            adjusted_parameters,
        )
        assessment = assess_smooth_gradient_banding(image, candidate)
        score = _banding_artifact_score(assessment)
        attempts.append(
            {
                "render_variant": "float32_single_quantization",
                "strength": strength,
                "triggered": bool(assessment["triggered"]),
                "artifact_score": round(score, 6),
            }
        )
        candidate_row = (
            score,
            strength,
            candidate,
            candidate_resolved,
            assessment,
        )
        if best is None or score < best[0]:
            best = candidate_row
        if not assessment["triggered"]:
            best = candidate_row
            break

    if best is None:
        raise RuntimeError("Banding guard did not evaluate any fallback candidate")
    _, selected_strength, selected, selected_parameters, final_assessment = best
    if selected_strength == 1.0:
        action = "float32_single_quantization"
    elif selected_strength == 0.0:
        action = "identity_fallback"
    else:
        action = "float32_strength_reduction"
    return selected, selected_parameters, {
        **base_safety,
        "attempts": attempts,
        "action": action,
        "render_variant": "float32_single_quantization",
        "effective_strength": selected_strength,
        "parameters_adjusted": selected_parameters != resolved,
        "final_assessment": final_assessment,
    }


def apply_supervised_opencv_parameters(
    image: np.ndarray,
    parameters: Mapping[str, Any] | None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Render the exact eight-parameter pipeline used to train the model."""

    resolved = resolve_supervised_parameters(parameters)
    resolved["reference_tint"] = 0.0

    adjusted = _apply_gamma(image, resolved["gamma"])
    adjusted = _apply_brightness_contrast(
        adjusted,
        brightness=resolved["brightness"],
        contrast=resolved["contrast"],
    )
    adjusted = _apply_saturation(adjusted, resolved["saturation"])
    adjusted = _apply_white_balance(
        adjusted,
        temperature=resolved["temperature"],
        tint=resolved["tint"],
    )
    adjusted = _apply_sharpen(adjusted, resolved["sharpen"])
    adjusted = _apply_vignette(adjusted, resolved["vignette"])
    return adjusted, resolved


def apply_supervised_opencv_parameters_float32(
    image: np.ndarray,
    parameters: Mapping[str, Any] | None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Apply all eight operations in float32 and quantize only once."""

    resolved = resolve_supervised_parameters(parameters)
    resolved["reference_tint"] = 0.0
    working = image.astype(np.float32) / 255.0

    gamma = float(resolved["gamma"])
    if gamma != 1.0:
        working = np.power(np.clip(working, 0.0, 1.0), 1.0 / gamma)
    working = np.clip(
        working * float(resolved["contrast"])
        + float(resolved["brightness"]) / 255.0,
        0.0,
        1.0,
    )

    saturation = float(resolved["saturation"])
    if saturation != 1.0:
        hsv = cv2.cvtColor(working, cv2.COLOR_BGR2HSV)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation, 0.0, 1.0)
        working = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    temperature_scale = float(resolved["temperature"]) / 100.0
    tint_scale = float(resolved["tint"]) / 100.0
    if temperature_scale != 0.0 or tint_scale != 0.0:
        magenta_gain = 1.0 + tint_scale * 0.5
        gains_bgr = np.array(
            [
                (1.0 - temperature_scale) * magenta_gain,
                1.0 - tint_scale,
                (1.0 + temperature_scale) * magenta_gain,
            ],
            dtype=np.float32,
        )
        working = np.clip(
            working * gains_bgr.reshape(1, 1, 3),
            0.0,
            1.0,
        )

    sharpen = float(resolved["sharpen"])
    if sharpen != 0.0:
        blurred = cv2.GaussianBlur(working, (0, 0), sigmaX=1.0)
        working = np.clip(
            working * (1.0 + sharpen) - blurred * sharpen,
            0.0,
            1.0,
        )

    vignette = float(resolved["vignette"])
    if vignette != 0.0:
        height, width = working.shape[:2]
        y = np.linspace(-1.0, 1.0, height, dtype=np.float32)
        x = np.linspace(-1.0, 1.0, width, dtype=np.float32)
        xv, yv = np.meshgrid(x, y)
        distance = np.clip(np.sqrt(xv * xv + yv * yv), 0.0, 1.0)
        mask = (
            1.0 - vignette * distance
            if vignette >= 0.0
            else 1.0 / (1.0 + vignette * distance)
        )
        working = np.clip(
            working * mask[:, :, np.newaxis],
            0.0,
            1.0,
        )

    return np.rint(working * 255.0).astype(np.uint8), resolved


def assess_smooth_gradient_banding(
    source: np.ndarray,
    candidate: np.ndarray,
) -> dict[str, Any]:
    if source.shape != candidate.shape:
        raise ValueError("Banding assessment images must have identical shapes")
    max_side = int(BANDING_GUARD_POLICY["analysis_max_side"])
    source_sample = _resize_max_side(source, max_side)
    candidate_sample = cv2.resize(
        candidate,
        (source_sample.shape[1], source_sample.shape[0]),
        interpolation=cv2.INTER_AREA,
    )

    hsv = cv2.cvtColor(source_sample, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(source_sample, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient = np.sqrt(gradient_x * gradient_x + gradient_y * gradient_y)
    smooth_chroma = (
        (gradient <= 10.0)
        & (hsv[:, :, 1] >= 64)
        & (hsv[:, :, 2] >= 48)
    )
    source_metrics = _neighbor_transition_metrics(source_sample, smooth_chroma)
    candidate_metrics = _neighbor_transition_metrics(
        candidate_sample,
        smooth_chroma,
    )
    coverage = float(np.mean(smooth_chroma))
    jump_floor = float(BANDING_GUARD_POLICY["source_jump_ratio_floor"])
    jump_ratio = candidate_metrics["jump_ratio"] / max(
        source_metrics["jump_ratio"],
        jump_floor,
    )
    flat_delta = candidate_metrics["flat_ratio"] - source_metrics["flat_ratio"]
    triggered = bool(
        coverage >= float(BANDING_GUARD_POLICY["minimum_smooth_coverage"])
        and source_metrics["neighbor_pairs"]
        >= int(BANDING_GUARD_POLICY["minimum_neighbor_pairs"])
        and candidate_metrics["jump_ratio"]
        >= float(BANDING_GUARD_POLICY["minimum_candidate_jump_ratio"])
        and jump_ratio >= float(BANDING_GUARD_POLICY["minimum_jump_ratio"])
        and flat_delta >= float(BANDING_GUARD_POLICY["minimum_flat_delta"])
    )
    return {
        "triggered": triggered,
        "smooth_chroma_coverage": round(coverage, 6),
        "jump_ratio_multiplier": round(jump_ratio, 6),
        "flat_ratio_delta": round(flat_delta, 6),
        "source": source_metrics,
        "candidate": candidate_metrics,
    }


def resolve_supervised_parameters(
    parameters: Mapping[str, Any] | None,
) -> dict[str, float]:
    resolved = SUPERVISED_DEFAULT_PARAMETERS.copy()
    if parameters:
        for key, value in parameters.items():
            if key not in SUPERVISED_PARAMETER_RANGES:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(numeric):
                continue
            low, high = SUPERVISED_PARAMETER_RANGES[key]
            resolved[key] = round(min(max(numeric, low), high), 4)
    return resolved


def _read_image(path: Path) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to read supervised input image: {path}")
    return image


def _write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(path.suffix or ".png", image)
    if not success:
        raise RuntimeError(f"Failed to encode supervised result for {path}")
    encoded.tofile(path)


def _apply_brightness_contrast(
    image: np.ndarray,
    *,
    brightness: float,
    contrast: float,
) -> np.ndarray:
    if brightness == 0 and contrast == 1:
        return image
    adjusted = image.astype(np.float32) * contrast + brightness
    return np.rint(np.clip(adjusted, 0, 255)).astype(np.uint8)


def _apply_gamma(image: np.ndarray, gamma: float) -> np.ndarray:
    if gamma == 1:
        return image
    values = np.arange(256, dtype=np.float32) / 255.0
    lookup = np.rint(np.power(values, 1.0 / gamma) * 255.0).astype(np.uint8)
    return cv2.LUT(image, lookup)


def _apply_saturation(image: np.ndarray, saturation: float) -> np.ndarray:
    if saturation == 1:
        return image
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _apply_white_balance(
    image: np.ndarray,
    *,
    temperature: float,
    tint: float,
) -> np.ndarray:
    if temperature == 0 and tint == 0:
        return image

    temperature_scale = temperature / 100.0
    tint_scale = tint / 100.0
    magenta_gain = 1.0 + tint_scale * 0.5
    gains_bgr = np.array(
        [
            (1.0 - temperature_scale) * magenta_gain,
            1.0 - tint_scale,
            (1.0 + temperature_scale) * magenta_gain,
        ],
        dtype=np.float32,
    )
    adjusted = image.astype(np.float32)
    adjusted *= gains_bgr.reshape(1, 1, 3)
    return np.rint(np.clip(adjusted, 0, 255)).astype(np.uint8)


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
    distance = np.clip(np.sqrt(xv * xv + yv * yv), 0.0, 1.0)
    mask = (
        1.0 - amount * distance
        if amount >= 0
        else 1.0 / (1.0 + amount * distance)
    )
    vignetted = image.astype(np.float32) * mask[:, :, np.newaxis]
    return np.rint(np.clip(vignetted, 0, 255)).astype(np.uint8)


def _scale_supervised_parameters(
    parameters: Mapping[str, float],
    strength: float,
) -> dict[str, float]:
    if not 0.0 <= strength <= 1.0:
        raise ValueError("Banding fallback strength must be between 0 and 1")
    scaled: dict[str, float] = {}
    for name, neutral in SUPERVISED_IDENTITY_PARAMETERS.items():
        value = float(parameters.get(name, neutral))
        scaled[name] = round(neutral + strength * (value - neutral), 4)
    scaled["reference_tint"] = 0.0
    return scaled


def _resize_max_side(image: np.ndarray, max_side: int) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image
    scale = max_side / float(longest)
    size = (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def _neighbor_transition_metrics(
    image: np.ndarray,
    smooth_mask: np.ndarray,
) -> dict[str, Any]:
    luminance = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.int16)
    horizontal_mask = smooth_mask[:, 1:] & smooth_mask[:, :-1]
    vertical_mask = smooth_mask[1:, :] & smooth_mask[:-1, :]
    transitions = np.concatenate(
        [
            np.abs(luminance[:, 1:] - luminance[:, :-1])[horizontal_mask],
            np.abs(luminance[1:, :] - luminance[:-1, :])[vertical_mask],
        ]
    )
    if transitions.size == 0:
        return {
            "neighbor_pairs": 0,
            "flat_ratio": 0.0,
            "jump_ratio": 0.0,
            "large_jump_ratio": 0.0,
        }
    return {
        "neighbor_pairs": int(transitions.size),
        "flat_ratio": round(float(np.mean(transitions == 0)), 6),
        "jump_ratio": round(float(np.mean(transitions >= 2)), 6),
        "large_jump_ratio": round(float(np.mean(transitions >= 4)), 6),
    }


def _banding_artifact_score(assessment: Mapping[str, Any]) -> float:
    candidate = assessment.get("candidate")
    candidate_jump = (
        float(candidate.get("jump_ratio", 0.0))
        if isinstance(candidate, Mapping)
        else 0.0
    )
    return (
        max(0.0, float(assessment.get("jump_ratio_multiplier", 0.0)) - 1.0)
        + 20.0 * max(0.0, float(assessment.get("flat_ratio_delta", 0.0)))
        + 10.0 * candidate_jump
    )


def _build_explanation(
    parameters: Mapping[str, float],
    *,
    render_safety: Mapping[str, Any] | None = None,
) -> str:
    values = ", ".join(
        f"{key}={parameters[key]}"
        for key in (
            "brightness",
            "contrast",
            "gamma",
            "saturation",
            "temperature",
            "tint",
            "sharpen",
            "vignette",
        )
    )
    safety_text = ""
    if render_safety and render_safety.get("action") not in {
        None,
        "keep_legacy",
        "disabled",
    }:
        safety_text = (
            " Banding guard action="
            f"{render_safety['action']}, "
            f"strength={render_safety['effective_strength']}."
        )
    return (
        f"Supervised Expert C renderer ({SUPERVISED_RENDER_PROFILE}) "
        f"applied: {values}.{safety_text}"
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0