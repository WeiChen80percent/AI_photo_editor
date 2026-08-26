from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.prompt_intent_encoder import PROMPT_INTENT_NAMES


EDIT_PARAMETER_NAMES = (
    "brightness",
    "contrast",
    "gamma",
    "saturation",
    "temperature",
    "tint",
    "sharpen",
    "vignette",
)

LEGACY_NEUTRAL_PARAMETERS: dict[str, float] = {
    "brightness": 0.0,
    "contrast": 1.0,
    "gamma": 1.0,
    "saturation": 1.0,
    "temperature": 0.0,
    "tint": 0.0,
    "sharpen": 0.0,
    "vignette": 0.0,
}

INTENT_PARAMETER_PROFILES: dict[str, tuple[str, ...]] = {
    "auto_enhance": EDIT_PARAMETER_NAMES,
    "fix_exposure": (
        "brightness",
        "contrast",
        "gamma",
        "saturation",
    ),
    "fix_white_balance": (
        "brightness",
        "contrast",
        "saturation",
        "temperature",
        "tint",
    ),
    "restore_natural": (
        "brightness",
        "contrast",
        "gamma",
        "saturation",
        "temperature",
        "tint",
        "vignette",
    ),
}


def apply_intent_parameter_profile(
    parameters: Mapping[str, Any],
    intent: str,
) -> dict[str, float]:
    if intent not in INTENT_PARAMETER_PROFILES:
        raise ValueError(f"Unsupported prompt intent: {intent}")
    allowed = set(INTENT_PARAMETER_PROFILES[intent])
    return {
        name: (
            float(parameters.get(name, LEGACY_NEUTRAL_PARAMETERS[name]))
            if name in allowed
            else LEGACY_NEUTRAL_PARAMETERS[name]
        )
        for name in EDIT_PARAMETER_NAMES
    }


if set(INTENT_PARAMETER_PROFILES) != set(PROMPT_INTENT_NAMES):
    raise RuntimeError("Every prompt intent must define one parameter profile")