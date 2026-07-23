# -*- coding: utf-8 -*-
from collections.abc import Iterable

from app.services.prompt_text import normalize_prompt_text


EditIntentStrength = tuple[str, str]

_SUPPORTED_INTENTS = {
    "brighten",
    "darken",
    "warm",
    "cool",
    "vivid",
    "natural",
    "sharpen",
    "dehaze",
    "soft",
}

_SUPPORTED_PRESETS = {
    "vintage_film",
    "cinematic",
    "fresh_japanese",
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
    "clarity": "sharpen",
    "detail": "sharpen",
    "hazy": "dehaze",
    "haze": "dehaze",
    "dehazed": "dehaze",
    "foggy": "dehaze",
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
    if normalized not in _SUPPORTED_INTENTS:
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
    if normalized not in _SUPPORTED_PRESETS:
        return None
    return normalized


def build_template_parameters(intent: str, strength: str | None = None) -> dict[str, float]:
    from app.services.opencv_parameter_mapper import build_opencv_template_parameters

    return build_opencv_template_parameters(intent, strength)


def build_preset_parameters(preset_name: str) -> dict[str, float]:
    from app.services.opencv_parameter_mapper import build_opencv_preset_parameters

    return build_opencv_preset_parameters(preset_name)


def build_compound_template_parameters(
    intent_strengths: Iterable[EditIntentStrength],
) -> dict[str, float]:
    from app.services.opencv_parameter_mapper import build_opencv_compound_parameters

    return build_opencv_compound_parameters(list(intent_strengths))


def limit_intent_strengths_for_prompt(
    prompt: str,
    intent_strengths: Iterable[EditIntentStrength],
) -> list[EditIntentStrength]:
    max_count = 3 if _prompt_explicitly_lists_three_operations(prompt) else 2
    return list(intent_strengths)[:max_count]


def format_template_catalog_for_prompt() -> str:
    intent_names = ", ".join(sorted(_SUPPORTED_INTENTS))
    preset_names = ", ".join(sorted(_SUPPORTED_PRESETS))
    return (
        f"Allowed intents: {intent_names}\n"
        f"Allowed presets: {preset_names}\n"
        "Allowed strengths: subtle, normal, strong\n"
        "Use subtle for requests like 'a little', 'slightly', '一點', or '稍微'."
    )


def _all_strengths() -> set[str]:
    return {"subtle", "normal", "strong"}


def _prompt_explicitly_lists_three_operations(prompt: str) -> bool:
    text = normalize_prompt_text(prompt)
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
        ["霧", "霧霧", "灰灰", "通透", "悶", "dehaze", "hazy", "foggy"],
        ["柔焦", "柔一點", "soft", "softer"],
    ]
    matched_group_count = sum(
        1 for keywords in intent_keyword_groups if _contains(text, keywords)
    )
    return matched_group_count >= 3


def _contains(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)
