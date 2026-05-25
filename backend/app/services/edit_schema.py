from collections.abc import Mapping
from typing import Any
import math


EDIT_PARAMETER_RANGES: dict[str, tuple[float, float]] = {
    "brightness": (-80.0, 80.0),
    "contrast": (0.5, 1.8),
    "saturation": (0.0, 2.0),
    "temperature": (-50.0, 50.0),
    "sharpen": (0.0, 1.0),
    "vignette": (0.0, 0.8),
    "reference_tint": (0.0, 0.5),
}


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
