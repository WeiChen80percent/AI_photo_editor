import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


EDIT_PARAMETER_SPECS: dict[str, dict[str, Any]] = {
    "exposure": {
        "label": "曝光",
        "label_en": "Exposure",
        "group": "light",
        "minimum": -2.0,
        "maximum": 2.0,
        "step": 0.05,
        "neutral": 0.0,
        "unit": "EV",
        "default_visible": True,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "brightness": {
        "label": "亮度",
        "label_en": "Brightness",
        "group": "light",
        "minimum": -80.0,
        "maximum": 80.0,
        "step": 0.25,
        "neutral": 0.0,
        "unit": "",
        "default_visible": True,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "contrast": {
        "label": "對比",
        "label_en": "Contrast",
        "group": "light",
        "minimum": 0.5,
        "maximum": 1.8,
        "step": 0.01,
        "neutral": 1.0,
        "unit": "x",
        "default_visible": True,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "highlights": {
        "label": "亮部",
        "label_en": "Highlights",
        "group": "light",
        "minimum": -100.0,
        "maximum": 100.0,
        "step": 1.0,
        "neutral": 0.0,
        "unit": "",
        "default_visible": True,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "shadows": {
        "label": "暗部",
        "label_en": "Shadows",
        "group": "light",
        "minimum": -100.0,
        "maximum": 100.0,
        "step": 1.0,
        "neutral": 0.0,
        "unit": "",
        "default_visible": True,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "whites": {
        "label": "白位",
        "label_en": "Whites",
        "group": "light",
        "minimum": -100.0,
        "maximum": 100.0,
        "step": 1.0,
        "neutral": 0.0,
        "unit": "",
        "default_visible": False,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "blacks": {
        "label": "黑位",
        "label_en": "Blacks",
        "group": "light",
        "minimum": -100.0,
        "maximum": 100.0,
        "step": 1.0,
        "neutral": 0.0,
        "unit": "",
        "default_visible": False,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "saturation": {
        "label": "飽和度",
        "label_en": "Saturation",
        "group": "color",
        "minimum": 0.0,
        "maximum": 2.0,
        "step": 0.01,
        "neutral": 1.0,
        "unit": "x",
        "default_visible": True,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "vibrance": {
        "label": "自然飽和度",
        "label_en": "Vibrance",
        "group": "color",
        "minimum": -1.0,
        "maximum": 1.0,
        "step": 0.01,
        "neutral": 0.0,
        "unit": "",
        "default_visible": True,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "temperature": {
        "label": "色溫",
        "label_en": "Temperature",
        "group": "color",
        "minimum": -50.0,
        "maximum": 50.0,
        "step": 1.0,
        "neutral": 0.0,
        "unit": "",
        "default_visible": True,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "white_balance_tint": {
        "label": "白平衡色偏",
        "label_en": "White Balance Tint",
        "group": "color",
        "minimum": -50.0,
        "maximum": 50.0,
        "step": 1.0,
        "neutral": 0.0,
        "unit": "",
        "default_visible": False,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "sharpen": {
        "label": "銳化",
        "label_en": "Sharpen",
        "group": "detail",
        "minimum": 0.0,
        "maximum": 1.0,
        "step": 0.01,
        "neutral": 0.0,
        "unit": "",
        "default_visible": False,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "clarity": {
        "label": "清晰度",
        "label_en": "Clarity",
        "group": "detail",
        "minimum": 0.0,
        "maximum": 1.0,
        "step": 0.01,
        "neutral": 0.0,
        "unit": "",
        "default_visible": False,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "dehaze": {
        "label": "去霧",
        "label_en": "Dehaze",
        "group": "detail",
        "minimum": 0.0,
        "maximum": 1.0,
        "step": 0.01,
        "neutral": 0.0,
        "unit": "",
        "default_visible": False,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "vignette": {
        "label": "暗角",
        "label_en": "Vignette",
        "group": "effects",
        "minimum": 0.0,
        "maximum": 0.8,
        "step": 0.01,
        "neutral": 0.0,
        "unit": "",
        "default_visible": False,
        "public": True,
        "manual_adjustable": True,
        "semantic_enabled": True,
    },
    "reference_tint": {
        "label": "參考圖色調混合",
        "label_en": "Reference Tint Blend",
        "group": "internal",
        "minimum": 0.0,
        "maximum": 0.5,
        "step": 0.01,
        "neutral": 0.0,
        "unit": "",
        "default_visible": False,
        "public": False,
        "manual_adjustable": False,
        "semantic_enabled": False,
    },
}

EDIT_PARAMETER_RANGES: dict[str, tuple[float, float]] = {
    key: (float(spec["minimum"]), float(spec["maximum"]))
    for key, spec in EDIT_PARAMETER_SPECS.items()
}

PUBLIC_PARAMETER_KEYS = tuple(
    key
    for key, spec in EDIT_PARAMETER_SPECS.items()
    if bool(spec["public"])
)
MANUAL_PARAMETER_KEYS = tuple(
    key
    for key, spec in EDIT_PARAMETER_SPECS.items()
    if bool(spec["manual_adjustable"])
)
SEMANTIC_PARAMETER_KEYS = tuple(
    key
    for key, spec in EDIT_PARAMETER_SPECS.items()
    if bool(spec["semantic_enabled"])
)


class ManualParameterValidationError(ValueError):
    def __init__(self, field: str | None, reason: str):
        self.field = field
        self.reason = reason
        prefix = f"Manual parameter '{field}'" if field else "Manual parameters"
        super().__init__(f"{prefix}: {reason}")

EDIT_REGION_MASK_TYPES: Mapping[str, str] = MappingProxyType({
    "all": "none",
    "sky": "semantic_sky",
    "person": "semantic_person",
    "background": "semantic_background",
    "shadows": "luminance_shadows",
    "highlights": "luminance_highlights",
    "center": "center_ellipse",
    "edges": "edge_vignette",
})
EDIT_REGIONS = frozenset(EDIT_REGION_MASK_TYPES)
EDIT_MASK_TYPES = frozenset(EDIT_REGION_MASK_TYPES.values())


def validate_edit_parameters(parameters: Mapping[str, Any] | None) -> dict[str, float]:
    """Keep only supported OpenCV edit parameters and clamp them to safe ranges."""
    if not parameters:
        return {}

    validated: dict[str, float] = {}
    for key, value in parameters.items():
        if key not in EDIT_PARAMETER_RANGES:
            continue

        numeric_value = _coerce_float(value)
        if numeric_value is None:
            continue

        low, high = EDIT_PARAMETER_RANGES[key]
        validated[key] = round(min(max(numeric_value, low), high), 4)

    return validated


def validate_manual_parameter_overrides(
    parameters: Mapping[str, Any] | None,
) -> dict[str, float]:
    """Strict validation for user-visible manual controls.

    Unlike LLM validation, this path never drops or clamps a value silently.
    """
    if parameters is None:
        return {}
    if not isinstance(parameters, Mapping):
        raise ManualParameterValidationError(None, "must be a JSON object")

    validated: dict[str, float] = {}
    for key, value in parameters.items():
        if key not in MANUAL_PARAMETER_KEYS:
            raise ManualParameterValidationError(str(key), "is not user-adjustable")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ManualParameterValidationError(key, "must be a finite JSON number")

        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ManualParameterValidationError(key, "must be finite")

        spec = EDIT_PARAMETER_SPECS[key]
        minimum = float(spec["minimum"])
        maximum = float(spec["maximum"])
        step = float(spec["step"])
        if numeric_value < minimum or numeric_value > maximum:
            raise ManualParameterValidationError(
                key,
                f"must be between {minimum:g} and {maximum:g}",
            )

        step_position = (numeric_value - minimum) / step
        if not math.isclose(step_position, round(step_position), abs_tol=1e-7):
            raise ManualParameterValidationError(
                key,
                f"must align to step {step:g}",
            )
        validated[key] = _round_for_step(numeric_value, step)

    return validated


def manual_parameter_schema() -> dict[str, Any]:
    parameters = []
    for order, key in enumerate(MANUAL_PARAMETER_KEYS):
        spec = EDIT_PARAMETER_SPECS[key]
        parameters.append(
            {
                "key": key,
                "label": spec["label"],
                "label_en": spec["label_en"],
                "labels": {
                    "zh": spec["label"],
                    "en": spec["label_en"],
                },
                "group": spec["group"],
                "minimum": spec["minimum"],
                "maximum": spec["maximum"],
                "step": spec["step"],
                "neutral": spec["neutral"],
                "unit": spec["unit"],
                "supports_negative": float(spec["minimum"]) < 0.0,
                "can_reset": True,
                "default_visible": bool(spec["default_visible"]),
                "order": order,
            }
        )
    return {
        "schema_version": "manual_opencv_v1",
        "engine": "opencv",
        "parameters": parameters,
    }


def validate_edit_region(region: Any) -> str:
    normalized = str(region or "all").strip().lower()
    if normalized in EDIT_REGIONS:
        return normalized
    return "all"


def require_edit_region(region: Any) -> str:
    """Return a valid region or fail instead of silently widening to all."""

    normalized = str(region or "all").strip().lower()
    if normalized not in EDIT_REGIONS:
        raise ValueError(f"Unsupported edit region: {region!r}")
    return normalized


def validate_edit_mask_type(mask_type: Any) -> str:
    normalized = str(mask_type or "none").strip().lower()
    if normalized in EDIT_MASK_TYPES:
        return normalized
    return "none"


def require_edit_mask_type(mask_type: Any) -> str:
    """Return a valid mask type or fail instead of silently disabling it."""

    normalized = str(mask_type or "none").strip().lower()
    if normalized not in EDIT_MASK_TYPES:
        raise ValueError(f"Unsupported edit mask type: {mask_type!r}")
    return normalized


def default_mask_type_for_region(region: Any) -> str:
    return EDIT_REGION_MASK_TYPES[validate_edit_region(region)]


def require_region_mask_pair(
    region: Any,
    mask_type: Any,
) -> tuple[str, str]:
    """Validate the exact public region/mask contract atomically."""

    resolved_region = require_edit_region(region)
    resolved_mask_type = require_edit_mask_type(mask_type)
    if resolved_mask_type == "none":
        resolved_mask_type = EDIT_REGION_MASK_TYPES[resolved_region]
    expected = EDIT_REGION_MASK_TYPES[resolved_region]
    if resolved_mask_type != expected:
        raise ValueError(
            "Mask type does not match edit region: "
            f"region={resolved_region!r}, mask_type={resolved_mask_type!r}, "
            f"expected={expected!r}"
        )
    return resolved_region, resolved_mask_type


def format_edit_schema_for_prompt() -> str:
    return "\n".join(
        f"- {key}: {low} to {high}"
        for key, (low, high) in EDIT_PARAMETER_RANGES.items()
    )


def _coerce_float(value: Any) -> float | None:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(numeric_value):
        return None

    return numeric_value


def _round_for_step(value: float, step: float) -> float:
    text = f"{step:.12f}".rstrip("0")
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    return round(value, decimals)
