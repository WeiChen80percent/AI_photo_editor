# -*- coding: utf-8 -*-
from collections.abc import Iterable

from app.services.edit_schema import validate_edit_parameters


EditIntentStrength = tuple[str, str]

NEUTRAL_PARAMETERS: dict[str, float] = {
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "temperature": 0.0,
    "sharpen": 0.0,
    "vignette": 0.0,
    "reference_tint": 0.0,
}

_TEMPLATE_ADJUSTMENTS: dict[str, dict[str, dict[str, float]]] = {
    "brighten": {
        "subtle": {"brightness": 10.0, "contrast": 1.03},
        "normal": {"brightness": 18.0, "contrast": 1.05},
        "strong": {"brightness": 28.0, "contrast": 1.08},
    },
    "darken": {
        "subtle": {"brightness": -10.0, "contrast": 1.02},
        "normal": {"brightness": -18.0, "contrast": 1.04},
        "strong": {"brightness": -28.0, "contrast": 1.06},
    },
    "warm": {
        "subtle": {"temperature": 8.0, "saturation": 1.02},
        "normal": {"temperature": 16.0, "saturation": 1.04},
        "strong": {"temperature": 26.0, "saturation": 1.06},
    },
    "cool": {
        "subtle": {"temperature": -8.0, "saturation": 1.0},
        "normal": {"temperature": -16.0, "saturation": 1.0},
        "strong": {"temperature": -26.0, "saturation": 0.98},
    },
    "vivid": {
        "subtle": {"saturation": 1.12, "contrast": 1.04},
        "normal": {"saturation": 1.22, "contrast": 1.07},
        "strong": {"saturation": 1.28, "contrast": 1.08},
    },
    "natural": {
        "subtle": {"saturation": 0.96, "contrast": 0.99},
        "normal": {"saturation": 0.94, "contrast": 0.98},
        "strong": {"saturation": 0.9, "contrast": 0.96},
    },
    "sharpen": {
        "subtle": {"sharpen": 0.22},
        "normal": {"sharpen": 0.35},
        "strong": {"sharpen": 0.5, "contrast": 1.04},
    },
    "soft": {
        "subtle": {"contrast": 0.98, "saturation": 0.98},
        "normal": {"contrast": 0.96, "saturation": 0.96},
        "strong": {"contrast": 0.94, "saturation": 0.94},
    },
}

_PRESET_ADJUSTMENTS: dict[str, dict[str, float]] = {
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

_INTENT_ALIASES: dict[str, str] = {
    "bright": "brighten",
    "brighter": "brighten",
    "brightness": "brighten",
    "lighten": "brighten",
    "dark": "darken",
    "darker": "darken",
    "warmth": "warm",
    "warmer": "warm",
    "warm_style": "warm",
    "cooler": "cool",
    "cool_style": "cool",
    "saturated": "vivid",
    "saturation": "vivid",
    "colorful": "vivid",
    "color": "vivid",
    "neutral": "natural",
    "balanced": "natural",
    "clear": "sharpen",
    "sharp": "sharpen",
    "softer": "soft",
}

_PRESET_ALIASES: dict[str, str] = {
    "retro": "vintage_film",
    "film": "vintage_film",
    "old_camera": "vintage_film",
    "vintage": "vintage_film",
    "cinema": "cinematic",
    "movie": "cinematic",
    "movie_style": "cinematic",
    "japanese": "fresh_japanese",
    "japanese_style": "fresh_japanese",
    "fresh": "fresh_japanese",
}

_STRENGTH_ALIASES: dict[str, str] = {
    "light": "subtle",
    "slight": "subtle",
    "slightly": "subtle",
    "gentle": "subtle",
    "small": "subtle",
    "low": "subtle",
    "輕微": "subtle",
    "一點": "subtle",
    "稍微": "subtle",
    "medium": "normal",
    "moderate": "normal",
    "default": "normal",
    "heavy": "strong",
    "stronger": "strong",
    "high": "strong",
    "much": "strong",
    "very": "strong",
    "大幅": "strong",
    "很": "strong",
    "非常": "strong",
    "更": "strong",
}


def normalize_edit_intent(intent: str | None) -> str | None:
    normalized = (intent or "").strip().lower()
    if not normalized:
        return None
    normalized = _INTENT_ALIASES.get(normalized, normalized)
    if normalized not in _TEMPLATE_ADJUSTMENTS:
        return None
    return normalized


def normalize_edit_strength(strength: str | None) -> str:
    normalized = (strength or "").strip().lower()
    if not normalized:
        return "normal"
    if normalized in _all_strengths():
        return normalized
    return _STRENGTH_ALIASES.get(normalized, "normal")


def normalize_preset_name(preset_name: str | None) -> str | None:
    normalized = (preset_name or "").strip().lower()
    if not normalized:
        return None
    normalized = _PRESET_ALIASES.get(normalized, normalized)
    if normalized not in _PRESET_ADJUSTMENTS:
        return None
    return normalized


def build_template_parameters(intent: str, strength: str | None = None) -> dict[str, float]:
    normalized_intent = normalize_edit_intent(intent)
    if normalized_intent is None:
        raise ValueError(f"Unsupported edit intent template: {intent}")

    normalized_strength = normalize_edit_strength(strength)
    parameters = NEUTRAL_PARAMETERS.copy()
    parameters.update(_TEMPLATE_ADJUSTMENTS[normalized_intent][normalized_strength])
    return validate_edit_parameters(parameters)


def build_preset_parameters(preset_name: str) -> dict[str, float]:
    normalized_preset = normalize_preset_name(preset_name)
    if normalized_preset is None:
        raise ValueError(f"Unsupported edit preset: {preset_name}")

    parameters = NEUTRAL_PARAMETERS.copy()
    parameters.update(_PRESET_ADJUSTMENTS[normalized_preset])
    return validate_edit_parameters(parameters)


def build_compound_template_parameters(
    intent_strengths: Iterable[EditIntentStrength],
) -> dict[str, float]:
    parameters = NEUTRAL_PARAMETERS.copy()
    for intent, strength in intent_strengths:
        normalized_intent = normalize_edit_intent(intent)
        if normalized_intent is None:
            continue
        normalized_strength = normalize_edit_strength(strength)
        parameters.update(_TEMPLATE_ADJUSTMENTS[normalized_intent][normalized_strength])
    return validate_edit_parameters(parameters)


def limit_intent_strengths_for_prompt(
    prompt: str,
    intent_strengths: Iterable[EditIntentStrength],
) -> list[EditIntentStrength]:
    max_count = 3 if _prompt_explicitly_lists_three_operations(prompt) else 2
    return list(intent_strengths)[:max_count]


def format_template_catalog_for_prompt() -> str:
    intent_names = ", ".join(sorted(_TEMPLATE_ADJUSTMENTS))
    preset_names = ", ".join(sorted(_PRESET_ADJUSTMENTS))
    return (
        f"Allowed intents: {intent_names}\n"
        f"Allowed presets: {preset_names}\n"
        "Allowed strengths: subtle, normal, strong\n"
        "Use subtle for requests like 'a little', 'slightly', '一點', or '稍微'."
    )


def _all_strengths() -> set[str]:
    return {"subtle", "normal", "strong"}


def _prompt_explicitly_lists_three_operations(prompt: str) -> bool:
    text = (prompt or "").strip().lower()
    if not text:
        return False

    intent_keyword_groups = [
        ["亮", "明亮", "調亮", "bright", "brighter", "lighten"],
        ["太亮", "過亮", "暗", "調暗", "壓暗", "dark", "darker"],
        ["暖", "暖色", "偏黃", "warm", "warmer"],
        ["冷", "冷色", "偏藍", "cool", "cooler"],
        ["鮮豔", "飽和", "色彩", "顏色", "vivid", "saturated", "colorful"],
        ["自然", "柔和", "natural", "balanced"],
        ["清楚", "清晰", "銳利", "sharp", "sharpen"],
        ["柔焦", "柔一點", "soft", "softer"],
    ]
    matched_group_count = sum(
        1 for keywords in intent_keyword_groups if _contains(text, keywords)
    )
    return matched_group_count >= 3


def _contains(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)
