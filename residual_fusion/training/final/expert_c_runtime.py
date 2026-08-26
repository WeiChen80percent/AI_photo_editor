from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import math

import cv2
import numpy as np


EDIT_PARAMETER_RANGES: dict[str, tuple[float, float]] = {
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
PARAMETER_NAMES = tuple(
    name for name in EDIT_PARAMETER_RANGES if name != "reference_tint"
)
IDENTITY_PARAMETERS = {
    "brightness": 0.0,
    "contrast": 1.0,
    "gamma": 1.0,
    "saturation": 1.0,
    "temperature": 0.0,
    "tint": 0.0,
    "sharpen": 0.0,
    "vignette": 0.0,
}
BASE_MODEL_FEATURE_NAMES = (
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
COLOR_OOD_FEATURE_NAMES = (
    "mean_red",
    "mean_green",
    "mean_blue",
    "lab_a_mean_norm",
    "lab_b_mean_norm",
    "temperature_signal",
    "tint_signal",
    "temperature_p25",
    "temperature_p50",
    "temperature_p75",
    "tint_p25",
    "tint_p50",
    "tint_p75",
    "neutral_temperature_signal",
    "neutral_tint_signal",
    "lab_a_median_norm",
    "lab_b_median_norm",
    "lab_a_std_norm",
    "lab_b_std_norm",
)
COLOR_OOD_FEATURE_INDICES = tuple(
    MODEL_FEATURE_NAMES.index(name) for name in COLOR_OOD_FEATURE_NAMES
)
OOD_AUTO_SAFE_POLICY = {
    "name": "expert_c_v2_1_auto_safe_v2",
    "calibration_manifest_sha256": (
        "02C7EFC1132CD1BB180EBBA6F50631FEE061A6E4C73F15041A9DC853D6B49F9A"
    ),
    "calibration_train_count": 320,
    "validation_count": 40,
    "minimum_evidence_groups": 2,
    "output_change_max_side": 256,
    "minimum_output_change_groups": 2,
    "warning_thresholds": {
        "max_abs_z": 7.2408,
        "rms_z": 2.1895,
        "outlier_count": 7,
        "color_max_abs_z": 5.8906,
        "color_outlier_count": 6,
        "temperature_disagreement": 0.2922,
        "tint_disagreement": 0.1011,
    },
    "training_envelope": {
        "max_abs_z": 9.8918,
        "rms_z": 2.6208,
        "outlier_count": 10,
        "color_max_abs_z": 7.3411,
        "color_outlier_count": 9,
        "temperature_disagreement": 0.3697,
        "tint_disagreement": 0.1861,
    },
    "output_change_envelope": {
        "rgb_mae": 0.159,
        "channel_mean_max": 0.218,
        "luma_mean_shift": 0.142,
        "delta_e_mean": 39.2,
        "delta_e_p95": 57.2,
        "clip_change": 0.338,
    },
    "parameter_risk": {
        "brightness_abs": 45.0,
        "contrast_low": 0.75,
        "contrast_high": 1.5,
        "gamma_low": 0.7,
        "gamma_high": 1.7,
        "saturation_low": 0.7,
        "saturation_high": 1.4,
        "temperature_abs": 15.0,
        "tint_abs": 15.0,
        "sharpen_high": 0.6,
        "vignette_abs": 0.2,
    },
    "actions": {
        "severe_strength": 0.4,
        "emergency_strength": 0.1,
        "identity_strength": 0.0,
        "temperature_abs_cap": 15.0,
        "tint_abs_cap": 15.0,
        "saturation_low_cap": 0.75,
        "saturation_high_cap": 1.35,
    },
}
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
DEFAULT_OPENCV_PARAMETERS = {
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


def analyze_image_state(
    image: np.ndarray,
    *,
    max_side: int = 256,
) -> dict[str, Any]:
    state = _analyze_image_state_v1(image, max_side=max_side)
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
            "version": "opencv_stats_v2",
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


def predict_parameters_from_state(
    state: dict[str, Any],
    *,
    model: Mapping[str, np.ndarray],
    strength: float,
) -> dict[str, float]:
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be between 0 and 1")

    vector = np.asarray(
        [[float(state[name]) for name in MODEL_FEATURE_NAMES]],
        dtype=np.float64,
    )
    standardized = (
        vector - np.asarray(model["feature_mean"], dtype=np.float64)
    ) / np.asarray(model["feature_std"], dtype=np.float64)
    design = np.column_stack([np.ones(len(standardized)), standardized])
    normalized = np.clip(
        design @ np.asarray(model["weights"], dtype=np.float64),
        0.0,
        1.0,
    )
    lows = np.asarray(
        [EDIT_PARAMETER_RANGES[name][0] for name in PARAMETER_NAMES],
        dtype=np.float64,
    )
    highs = np.asarray(
        [EDIT_PARAMETER_RANGES[name][1] for name in PARAMETER_NAMES],
        dtype=np.float64,
    )
    row = lows + np.clip(normalized, 0.0, 1.0) * (highs - lows)
    prediction = {
        name: float(row[0, index])
        for index, name in enumerate(PARAMETER_NAMES)
    }
    return {
        name: round(
            neutral + strength * (prediction[name] - neutral),
            4,
        )
        for name, neutral in IDENTITY_PARAMETERS.items()
    }


def predict_parameters_auto_safe(
    state: dict[str, Any],
    *,
    image: np.ndarray,
    model: Mapping[str, np.ndarray],
) -> tuple[dict[str, float], dict[str, float], dict[str, Any]]:
    raw_parameters = predict_parameters_from_state(
        state,
        model=model,
        strength=1.0,
    )
    assessment = assess_ood_safety(
        state,
        model=model,
        raw_parameters=raw_parameters,
    )
    output_change_safety = assess_output_change_safety(
        image,
        parameters=raw_parameters,
        candidate_strength=1.0,
    )
    assessment["output_change_safety"] = output_change_safety

    triggered_by: list[str] = []
    if assessment["action"] == "conservative_strength":
        triggered_by.append("ood_parameter_risk")
    if output_change_safety["triggered"]:
        triggered_by.append("output_change")
        if assessment["action"] != "conservative_strength":
            assessment["action"] = "conservative_output_change"

    if triggered_by:
        actions = OOD_AUTO_SAFE_POLICY["actions"]
        fallback_strengths = (
            float(actions["severe_strength"]),
            float(actions["emergency_strength"]),
            float(actions["identity_strength"]),
        )
        for effective_strength in fallback_strengths:
            adjusted_parameters, caps_applied = (
                _bounded_parameters_from_state(
                    state,
                    model=model,
                    strength=effective_strength,
                )
            )
            final_output_change_safety = assess_output_change_safety(
                image,
                parameters=adjusted_parameters,
                candidate_strength=effective_strength,
            )
            if not final_output_change_safety["triggered"]:
                break
        if effective_strength < float(actions["severe_strength"]):
            triggered_by.append("post_conservative_output_change")
            assessment["action"] = (
                "identity_fallback"
                if effective_strength == 0.0
                else "emergency_output_change"
            )
    else:
        adjusted_parameters = raw_parameters.copy()
        effective_strength = 1.0
        caps_applied = []
        final_output_change_safety = output_change_safety

    assessment.update(
        {
            "triggered_by": triggered_by,
            "effective_strength": effective_strength,
            "parameters_adjusted": adjusted_parameters != raw_parameters,
            "caps_applied": caps_applied,
            "final_output_change_safety": final_output_change_safety,
        }
    )
    return adjusted_parameters, raw_parameters, assessment


def assess_ood_safety(
    state: dict[str, Any],
    *,
    model: Mapping[str, np.ndarray],
    raw_parameters: Mapping[str, float],
) -> dict[str, Any]:
    vector = np.asarray(
        [float(state[name]) for name in MODEL_FEATURE_NAMES],
        dtype=np.float64,
    )
    mean = np.asarray(model["feature_mean"], dtype=np.float64)
    std = np.asarray(model["feature_std"], dtype=np.float64)
    absolute_z = np.abs((vector - mean) / np.maximum(std, 1e-12))
    color_z = absolute_z[np.asarray(COLOR_OOD_FEATURE_INDICES)]
    metrics: dict[str, float | int] = {
        "max_abs_z": round(float(np.max(absolute_z)), 6),
        "rms_z": round(float(np.sqrt(np.mean(absolute_z**2))), 6),
        "outlier_count": int(np.sum(absolute_z > 3.0)),
        "color_max_abs_z": round(float(np.max(color_z)), 6),
        "color_outlier_count": int(np.sum(color_z > 3.0)),
        "temperature_disagreement": round(
            abs(
                float(state["temperature_signal"])
                - float(state["neutral_temperature_signal"])
            ),
            6,
        ),
        "tint_disagreement": round(
            abs(
                float(state["tint_signal"])
                - float(state["neutral_tint_signal"])
            ),
            6,
        ),
        "neutral_pixel_ratio": round(
            float(state["neutral_pixel_ratio"]),
            6,
        ),
    }
    warning_groups = _ood_evidence_groups(
        metrics,
        OOD_AUTO_SAFE_POLICY["warning_thresholds"],
        strict=False,
    )
    envelope_groups = _ood_evidence_groups(
        metrics,
        OOD_AUTO_SAFE_POLICY["training_envelope"],
        strict=True,
    )
    high_risk_parameters = _high_risk_parameters(raw_parameters)
    minimum_groups = int(OOD_AUTO_SAFE_POLICY["minimum_evidence_groups"])

    if len(envelope_groups) >= minimum_groups:
        level = "severe"
    elif envelope_groups or len(warning_groups) >= minimum_groups:
        level = "moderate"
    else:
        level = "normal"

    if level == "severe" and high_risk_parameters:
        action = "conservative_strength"
    elif level == "normal":
        action = "none"
    else:
        action = "warn_only"

    return {
        "policy": OOD_AUTO_SAFE_POLICY["name"],
        "level": level,
        "action": action,
        "metrics": metrics,
        "warning_evidence_groups": warning_groups,
        "training_envelope_breaches": envelope_groups,
        "high_risk_parameters": high_risk_parameters,
    }


def assess_output_change_safety(
    image: np.ndarray,
    *,
    parameters: Mapping[str, float],
    candidate_strength: float = 1.0,
) -> dict[str, Any]:
    max_side = int(OOD_AUTO_SAFE_POLICY["output_change_max_side"])
    sample = _resize_max_side(image, max_side)
    candidate, _ = apply_opencv_parameters(sample, parameters)

    sample_float = sample.astype(np.float32)
    candidate_float = candidate.astype(np.float32)
    sample_gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY).astype(np.float32)
    candidate_gray = cv2.cvtColor(
        candidate,
        cv2.COLOR_BGR2GRAY,
    ).astype(np.float32)
    sample_lab = cv2.cvtColor(
        sample_float / 255.0,
        cv2.COLOR_BGR2LAB,
    )
    candidate_lab = cv2.cvtColor(
        candidate_float / 255.0,
        cv2.COLOR_BGR2LAB,
    )
    delta_e = np.linalg.norm(candidate_lab - sample_lab, axis=2)
    sample_clipped = (sample_gray <= 5.0) | (sample_gray >= 250.0)
    candidate_clipped = (
        (candidate_gray <= 5.0) | (candidate_gray >= 250.0)
    )
    raw_metrics = {
        "rgb_mae": float(
            np.mean(np.abs(candidate_float - sample_float)) / 255.0
        ),
        "channel_mean_max": float(
            np.max(
                np.abs(
                    np.mean(
                        candidate_float - sample_float,
                        axis=(0, 1),
                    )
                )
            )
            / 255.0
        ),
        "luma_mean_shift": float(
            abs(np.mean(candidate_gray) - np.mean(sample_gray)) / 255.0
        ),
        "delta_e_mean": float(np.mean(delta_e)),
        "delta_e_p95": float(np.percentile(delta_e, 95)),
        "clip_change": float(
            abs(np.mean(candidate_clipped) - np.mean(sample_clipped))
        ),
    }
    breaches = _output_change_groups(
        raw_metrics,
        OOD_AUTO_SAFE_POLICY["output_change_envelope"],
    )
    minimum_groups = int(
        OOD_AUTO_SAFE_POLICY["minimum_output_change_groups"]
    )
    return {
        "candidate_strength": candidate_strength,
        "metrics": {
            name: round(value, 6)
            for name, value in raw_metrics.items()
        },
        "training_envelope_breaches": breaches,
        "triggered": len(breaches) >= minimum_groups,
    }


def _ood_evidence_groups(
    metrics: Mapping[str, float | int],
    thresholds: Mapping[str, Any],
    *,
    strict: bool,
) -> list[str]:
    def crossed(name: str) -> bool:
        value = float(metrics[name])
        threshold = float(thresholds[name])
        return value > threshold if strict else value >= threshold

    groups: list[str] = []
    if crossed("max_abs_z") or crossed("rms_z"):
        groups.append("distribution_shape")
    if crossed("outlier_count"):
        groups.append("multi_feature_outliers")
    if crossed("color_max_abs_z") or crossed("color_outlier_count"):
        groups.append("color_distribution")
    if crossed("temperature_disagreement") or crossed("tint_disagreement"):
        groups.append("white_balance_conflict")
    return groups


def _output_change_groups(
    metrics: Mapping[str, float],
    thresholds: Mapping[str, Any],
) -> list[str]:
    def crossed(name: str) -> bool:
        return float(metrics[name]) > float(thresholds[name])

    groups: list[str] = []
    if crossed("rgb_mae") or crossed("delta_e_mean"):
        groups.append("average_change")
    if crossed("channel_mean_max") or crossed("luma_mean_shift"):
        groups.append("global_shift")
    if crossed("delta_e_p95"):
        groups.append("local_extremes")
    if crossed("clip_change"):
        groups.append("clipping")
    return groups


def _high_risk_parameters(
    parameters: Mapping[str, float],
) -> list[str]:
    risk = OOD_AUTO_SAFE_POLICY["parameter_risk"]
    checks = {
        "brightness": abs(float(parameters["brightness"]))
        >= float(risk["brightness_abs"]),
        "contrast": (
            float(parameters["contrast"]) <= float(risk["contrast_low"])
            or float(parameters["contrast"])
            >= float(risk["contrast_high"])
        ),
        "gamma": (
            float(parameters["gamma"]) <= float(risk["gamma_low"])
            or float(parameters["gamma"]) >= float(risk["gamma_high"])
        ),
        "saturation": (
            float(parameters["saturation"])
            <= float(risk["saturation_low"])
            or float(parameters["saturation"])
            >= float(risk["saturation_high"])
        ),
        "temperature": abs(float(parameters["temperature"]))
        >= float(risk["temperature_abs"]),
        "tint": abs(float(parameters["tint"]))
        >= float(risk["tint_abs"]),
        "sharpen": float(parameters["sharpen"])
        >= float(risk["sharpen_high"]),
        "vignette": abs(float(parameters["vignette"]))
        >= float(risk["vignette_abs"]),
    }
    return [name for name, is_risky in checks.items() if is_risky]


def _bounded_parameters_from_state(
    state: dict[str, Any],
    *,
    model: Mapping[str, np.ndarray],
    strength: float,
) -> tuple[dict[str, float], list[str]]:
    actions = OOD_AUTO_SAFE_POLICY["actions"]
    parameters = predict_parameters_from_state(
        state,
        model=model,
        strength=strength,
    )
    caps = {
        "temperature": (
            -float(actions["temperature_abs_cap"]),
            float(actions["temperature_abs_cap"]),
        ),
        "tint": (
            -float(actions["tint_abs_cap"]),
            float(actions["tint_abs_cap"]),
        ),
        "saturation": (
            float(actions["saturation_low_cap"]),
            float(actions["saturation_high_cap"]),
        ),
    }
    caps_applied: list[str] = []
    for name, (low, high) in caps.items():
        original = float(parameters[name])
        clipped = round(float(np.clip(original, low, high)), 4)
        parameters[name] = clipped
        if clipped != original:
            caps_applied.append(name)
    return parameters, caps_applied


def apply_opencv_parameters(
    image: np.ndarray,
    parameters: Mapping[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    resolved = _resolve_parameters(parameters)
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


def apply_opencv_parameters_banding_safe(
    image: np.ndarray,
    parameters: Mapping[str, Any] | None,
    *,
    enabled: bool = True,
) -> tuple[np.ndarray, dict[str, float], dict[str, Any]]:
    """Keep the frozen renderer unless it visibly quantizes smooth color."""

    legacy, resolved = apply_opencv_parameters(image, parameters)
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
        adjusted_parameters = _scale_parameters_for_banding_guard(resolved, strength)
        candidate, candidate_resolved = apply_opencv_parameters_float32(
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


def apply_opencv_parameters_float32(
    image: np.ndarray,
    parameters: Mapping[str, Any] | None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Apply all eight operations in float32 and quantize only once."""

    resolved = _resolve_parameters(parameters)
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


def _scale_parameters_for_banding_guard(
    parameters: Mapping[str, float],
    strength: float,
) -> dict[str, float]:
    if not 0.0 <= strength <= 1.0:
        raise ValueError("Banding fallback strength must be between 0 and 1")
    scaled: dict[str, float] = {}
    for name, neutral in IDENTITY_PARAMETERS.items():
        value = float(parameters.get(name, neutral))
        scaled[name] = round(neutral + strength * (value - neutral), 4)
    scaled["reference_tint"] = 0.0
    return scaled


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


def _analyze_image_state_v1(
    image: np.ndarray,
    *,
    max_side: int,
) -> dict[str, Any]:
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
        "version": "opencv_stats_v1",
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


def _normalized_entropy(gray_u8: np.ndarray) -> float:
    histogram, _ = np.histogram(gray_u8, bins=64, range=(0, 256))
    probabilities = histogram.astype(np.float64)
    probabilities /= max(float(np.sum(probabilities)), 1.0)
    probabilities = probabilities[probabilities > 0]
    entropy = -float(np.sum(probabilities * np.log2(probabilities)))
    return entropy / 6.0


def _resolve_parameters(
    parameters: Mapping[str, Any] | None,
) -> dict[str, float]:
    resolved = DEFAULT_OPENCV_PARAMETERS.copy()
    resolved.update(_validate_edit_parameters(parameters))
    for key, value in resolved.items():
        low, high = EDIT_PARAMETER_RANGES[key]
        resolved[key] = round(float(np.clip(value, low, high)), 4)
    return resolved


def _validate_edit_parameters(
    parameters: Mapping[str, Any] | None,
) -> dict[str, float]:
    if not parameters:
        return {}

    validated: dict[str, float] = {}
    for key, value in parameters.items():
        if key not in EDIT_PARAMETER_RANGES:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric_value):
            continue
        low, high = EDIT_PARAMETER_RANGES[key]
        validated[key] = round(min(max(numeric_value, low), high), 4)
    return validated


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
    lookup = np.rint(
        np.power(values, 1.0 / gamma) * 255.0
    ).astype(np.uint8)
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
    distance = np.sqrt(xv * xv + yv * yv)
    distance = np.clip(distance, 0.0, 1.0)
    if amount >= 0:
        mask = 1.0 - amount * distance
    else:
        mask = 1.0 / (1.0 + amount * distance)
    vignetted = image.astype(np.float32) * mask[:, :, np.newaxis]
    return np.rint(np.clip(vignetted, 0, 255)).astype(np.uint8)
