# -*- coding: utf-8 -*-
from typing import Any

from app.services.edit_intent_templates import (
    limit_intent_strengths_for_prompt,
    normalize_preset_name,
    normalize_edit_strength,
)
from app.services.edit_engines import build_engine_parameters
from app.services.edit_plan import (
    build_compound_edit_plan,
    build_preset_edit_plan,
    build_raw_parameter_edit_plan,
)


def parse_edit_prompt(prompt: str | None) -> dict[str, Any]:
    """Map deterministic fallback prompts to the same templates used by LLM intent parsing."""
    user_prompt = (prompt or "").strip()
    normalized = user_prompt.lower()

    if not normalized:
        edit_plan = build_raw_parameter_edit_plan(prompt=user_prompt, parameters={})
        return _build_result(
            prompt=user_prompt,
            resolved_intent="default",
            edit_plan=edit_plan,
            parameters=build_engine_parameters("opencv", edit_plan),
            reason="No prompt was provided; using OpenCV defaults.",
        )

    strength = _detect_strength(normalized)
    preset_name = _detect_preset_name(normalized)
    if preset_name is not None:
        edit_plan = build_preset_edit_plan(prompt=user_prompt, preset_name=preset_name)
        return _build_result(
            prompt=user_prompt,
            resolved_intent="apply_preset",
            edit_plan=edit_plan,
            parameters=build_engine_parameters("opencv", edit_plan),
            reason=f"Parsed prompt as preset {preset_name}.",
            preset_name=preset_name,
        )

    intent_strengths = _detect_intent_strengths(normalized, strength)

    if not intent_strengths:
        edit_plan = build_raw_parameter_edit_plan(prompt=user_prompt, parameters={})
        return _build_result(
            prompt=user_prompt,
            resolved_intent="default",
            edit_plan=edit_plan,
            parameters=build_engine_parameters("opencv", edit_plan),
            reason="No supported edit intent was detected; using OpenCV defaults.",
        )

    intent_strengths = limit_intent_strengths_for_prompt(user_prompt, intent_strengths)
    edit_plan = build_compound_edit_plan(
        prompt=user_prompt,
        intent_strengths=intent_strengths,
    )
    intents = [intent for intent, _ in intent_strengths]
    resolved_intent = intents[0] if len(intents) == 1 else "compound"
    return _build_result(
        prompt=user_prompt,
        resolved_intent=resolved_intent,
        edit_plan=edit_plan,
        parameters=build_engine_parameters("opencv", edit_plan),
        reason=f"Parsed prompt as {resolved_intent} with {strength} strength.",
    )


def _detect_intent_strengths(text: str, strength: str) -> list[tuple[str, str]]:
    if _contains(text, ["悶", "沉悶", "灰灰", "flat"]):
        return [("brighten", strength), ("vivid", strength)]

    if _contains(text, ["清爽", "通透", "fresh", "airy"]):
        return [("brighten", strength), ("cool", strength)]

    if _contains(text, ["自然", "柔和", "natural", "balanced"]) or _contains(
        text,
        [
            "收斂",
            "太鮮豔",
            "過鮮豔",
            "太飽和",
            "過飽和",
            "不要太鮮豔",
            "別太鮮豔",
            "不要太飽和",
            "別太飽和",
        ],
    ):
        return [("natural", strength)]

    intent_strengths: list[tuple[str, str]] = []

    overexposure_guard = _contains(text, ["不要過曝", "避免過曝", "別過曝"])
    if _contains(text, ["太亮", "過亮", "暗一點", "調暗", "壓暗", "dark", "darker"]) or (
        "過曝" in text and not overexposure_guard
    ):
        intent_strengths.append(("darken", strength))
    elif _contains(text, ["亮", "明亮", "調亮", "bright", "brighter", "lighten"]):
        intent_strengths.append(("brighten", strength))

    if _contains(text, ["暖", "暖色", "偏黃", "warm", "warmer"]):
        intent_strengths.append(("warm", strength))
    elif _contains(text, ["冷", "冷色", "偏藍", "cool", "cooler"]):
        intent_strengths.append(("cool", strength))

    if _contains(text, ["鮮豔", "飽和", "色彩", "顏色", "vivid", "saturated", "colorful"]):
        intent_strengths.append(("vivid", strength))

    if _contains(text, ["清楚", "清晰", "銳利", "sharp", "sharpen"]):
        intent_strengths.append(("sharpen", strength))

    if _contains(text, ["柔焦", "柔一點", "soft", "softer"]):
        intent_strengths.append(("soft", strength))

    return intent_strengths


def _detect_preset_name(text: str) -> str | None:
    if _contains(
        text,
        ["暗黃", "底片", "復古", "老相機", "懷舊", "retro", "film", "vintage"],
    ):
        return normalize_preset_name("vintage_film")

    if _contains(text, ["電影感", "電影", "劇照", "cinematic", "cinema", "movie"]):
        return normalize_preset_name("cinematic")

    if _contains(text, ["清新日系", "日系", "淡雅", "清淡", "japanese"]):
        return normalize_preset_name("fresh_japanese")

    return None


def _detect_strength(text: str) -> str:
    for keyword in ["一點", "稍微", "微微", "有點", "小幅", "little", "slightly", "light"]:
        if keyword in text:
            return normalize_edit_strength("subtle")

    for keyword in ["很", "非常", "大幅", "加強", "更", "much", "very", "heavy", "strong"]:
        if keyword in text:
            return normalize_edit_strength("strong")

    return normalize_edit_strength("normal")


def _contains(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _build_result(
    *,
    prompt: str,
    resolved_intent: str,
    edit_plan: dict[str, Any],
    parameters: dict[str, float],
    reason: str,
    preset_name: str | None = None,
) -> dict[str, Any]:
    parameter_text = _format_parameters(parameters)
    explanation = f"{reason} Parameters: {parameter_text}"
    return {
        "prompt": prompt,
        "resolved_intent": resolved_intent,
        "preset_name": preset_name,
        "edit_plan": edit_plan,
        "parameters": parameters,
        "explanation": explanation,
    }


def _format_parameters(parameters: dict[str, float]) -> str:
    if not parameters:
        return "none"

    return ", ".join(f"{key}={value}" for key, value in parameters.items())
