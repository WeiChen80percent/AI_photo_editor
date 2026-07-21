from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.edit_intent_templates import (
    normalize_edit_intent,
    normalize_edit_strength,
    normalize_preset_name,
)
from app.services.edit_schema import validate_edit_parameters
from app.services.edit_schema import (
    default_mask_type_for_region,
    validate_edit_mask_type,
    validate_edit_region,
)


NEUTRAL_OPENCV_PARAMETERS: dict[str, float] = {
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "temperature": 0.0,
    "sharpen": 0.0,
    "clarity": 0.0,
    "dehaze": 0.0,
    "vignette": 0.0,
    "reference_tint": 0.0,
}

_OPENCV_TEMPLATE_ADJUSTMENTS: dict[str, dict[str, dict[str, float]]] = {
    "brighten": {
        "subtle": {"brightness": 10.0, "contrast": 1.03},
        "normal": {"brightness": 18.0, "contrast": 1.06},
        "strong": {"brightness": 30.0, "contrast": 1.1},
    },
    "darken": {
        "subtle": {"brightness": -10.0, "contrast": 1.02},
        "normal": {"brightness": -18.0, "contrast": 1.05},
        "strong": {"brightness": -30.0, "contrast": 1.08},
    },
    "warm": {
        "subtle": {"temperature": 8.0, "saturation": 1.03},
        "normal": {"temperature": 15.0, "saturation": 1.06},
        "strong": {"temperature": 25.0, "saturation": 1.08},
    },
    "cool": {
        "subtle": {"temperature": -8.0, "saturation": 1.0},
        "normal": {"temperature": -15.0, "saturation": 0.99},
        "strong": {"temperature": -25.0, "saturation": 0.96},
    },
    "vivid": {
        "subtle": {"saturation": 1.1, "contrast": 1.03},
        "normal": {"saturation": 1.18, "contrast": 1.06},
        "strong": {"saturation": 1.28, "contrast": 1.1},
    },
    "natural": {
        "subtle": {"saturation": 0.94, "contrast": 0.99},
        "normal": {"saturation": 0.88, "contrast": 0.98},
        "strong": {"saturation": 0.8, "contrast": 0.95},
    },
    "sharpen": {
        "subtle": {"sharpen": 0.22, "clarity": 0.18},
        "normal": {"sharpen": 0.38, "clarity": 0.32},
        "strong": {"sharpen": 0.55, "clarity": 0.48, "contrast": 1.04},
    },
    "dehaze": {
        "subtle": {"dehaze": 0.22, "clarity": 0.12, "contrast": 1.04, "saturation": 1.02},
        "normal": {"dehaze": 0.38, "clarity": 0.22, "contrast": 1.07, "saturation": 1.04},
        "strong": {"dehaze": 0.55, "clarity": 0.34, "contrast": 1.1, "saturation": 1.06},
    },
    "soft": {
        "subtle": {"contrast": 0.98, "saturation": 0.98},
        "normal": {"contrast": 0.96, "saturation": 0.96},
        "strong": {"contrast": 0.94, "saturation": 0.94},
    },
}

_OPENCV_PRESET_ADJUSTMENTS: dict[str, dict[str, float]] = {
    "vintage_film": {
        "brightness": -8.0,
        "contrast": 0.92,
        "saturation": 0.82,
        "temperature": 18.0,
        "sharpen": 0.08,
        "vignette": 0.22,
    },
    "cinematic": {
        "brightness": -6.0,
        "contrast": 1.18,
        "saturation": 0.98,
        "temperature": -6.0,
        "sharpen": 0.28,
        "vignette": 0.18,
    },
    "fresh_japanese": {
        "brightness": 12.0,
        "contrast": 0.96,
        "saturation": 0.9,
        "temperature": -4.0,
        "sharpen": 0.08,
        "vignette": 0.0,
    },
}


def build_opencv_parameters_from_plan(edit_plan: Mapping[str, Any]) -> dict[str, float]:
    plan_type = str(edit_plan.get("type") or "raw_parameters")

    if plan_type == "reference":
        return {}
    if plan_type == "raw_parameters":
        raw_parameters = edit_plan.get("raw_parameters")
        return _with_region_metadata(
            validate_edit_parameters(raw_parameters),
            _metadata_source(edit_plan, raw_parameters),
        )
    if plan_type == "preset":
        return _with_region_metadata(
            build_opencv_preset_parameters(str(edit_plan.get("preset_name") or "")),
            edit_plan,
        )
    if plan_type == "edits":
        edits = edit_plan.get("edits")
        if not isinstance(edits, list):
            return _with_region_metadata(
                validate_edit_parameters(NEUTRAL_OPENCV_PARAMETERS),
                edit_plan,
            )
        intent_strengths = []
        for edit in edits:
            if not isinstance(edit, Mapping):
                continue
            intent_strengths.append(
                (
                    str(edit.get("intent") or ""),
                    str(edit.get("strength") or "normal"),
                )
            )
        return _with_region_metadata(
            build_opencv_compound_parameters(intent_strengths),
            edit_plan,
        )

    raise ValueError(f"Unsupported edit plan type for OpenCV: {plan_type}")


def build_opencv_template_parameters(
    intent: str,
    strength: str | None = None,
) -> dict[str, float]:
    normalized_intent = normalize_edit_intent(intent)
    if normalized_intent is None:
        raise ValueError(f"Unsupported edit intent template: {intent}")

    normalized_strength = normalize_edit_strength(strength)
    parameters = NEUTRAL_OPENCV_PARAMETERS.copy()
    parameters.update(_OPENCV_TEMPLATE_ADJUSTMENTS[normalized_intent][normalized_strength])
    return _with_region_metadata(validate_edit_parameters(parameters), {})


def build_opencv_compound_parameters(
    intent_strengths: list[tuple[str, str]],
) -> dict[str, float]:
    parameters = NEUTRAL_OPENCV_PARAMETERS.copy()
    for intent, strength in intent_strengths:
        normalized_intent = normalize_edit_intent(intent)
        if normalized_intent is None:
            continue
        normalized_strength = normalize_edit_strength(strength)
        parameters.update(_OPENCV_TEMPLATE_ADJUSTMENTS[normalized_intent][normalized_strength])
    return _with_region_metadata(validate_edit_parameters(parameters), {})


def build_opencv_preset_parameters(preset_name: str) -> dict[str, float]:
    normalized_preset = normalize_preset_name(preset_name)
    if normalized_preset is None:
        raise ValueError(f"Unsupported edit preset: {preset_name}")

    parameters = NEUTRAL_OPENCV_PARAMETERS.copy()
    parameters.update(_OPENCV_PRESET_ADJUSTMENTS[normalized_preset])
    return _with_region_metadata(validate_edit_parameters(parameters), {})


def _metadata_source(
    edit_plan: Mapping[str, Any],
    raw_parameters: Any,
) -> Mapping[str, Any]:
    if isinstance(raw_parameters, Mapping):
        merged = dict(raw_parameters)
        merged.update({key: edit_plan.get(key) for key in ("region", "mask_type")})
        return merged
    return edit_plan


def _with_region_metadata(
    parameters: dict[str, float],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    region = validate_edit_region(source.get("region"))
    mask_type = validate_edit_mask_type(source.get("mask_type"))
    if mask_type == "none":
        mask_type = default_mask_type_for_region(region)
    return {
        **parameters,
        "region": region,
        "mask_type": mask_type,
    }
