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
    region, mask_type = _detect_region_mask(normalized)
    if preset_name is not None:
        edit_plan = build_preset_edit_plan(
            prompt=user_prompt,
            preset_name=preset_name,
            region=region,
            mask_type=mask_type,
        )
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
        edit_plan = build_raw_parameter_edit_plan(
            prompt=user_prompt,
            parameters={},
            region=region,
            mask_type=mask_type,
        )
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
        region=region,
        mask_type=mask_type,
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
    if _contains(text, ["不要那麼銳", "不要太銳", "太銳利", "太清晰", "oversharp"]):
        return [("soft", "subtle")]

    if _contains(text, ["霧霧", "霧霾", "有霧", "hazy", "haze", "foggy"]):
        return [("dehaze", strength)]

    if _contains(text, ["悶", "沉悶", "灰灰", "髒髒", "dirty", "flat", "dull"]):
        return [("dehaze", strength), ("vivid", "subtle")]

    if _contains(text, ["臉", "人", "人物", "人像", "主體"]) and _contains(
        text,
        ["太暗", "有點暗", "暗", "不夠亮"],
    ):
        return [("brighten", strength)]

    if _contains(text, ["天空", "sky"]) and _contains(
        text,
        ["太亮", "過亮", "刺眼", "過曝", "壓"],
    ):
        return [("darken", strength)]

    if _contains(text, ["背景", "background"]) and _contains(
        text,
        ["搶", "太搶", "淡", "弱一點", "不要那麼明顯", "distracting"],
    ):
        return [("natural", strength)]

    if _contains(text, ["too blue", "less blue", "偏藍", "太藍", "藍藍"]):
        return [("warm", "subtle")]

    if _contains(text, ["色彩太淡", "顏色太淡", "不夠鮮豔", "不夠飽和", "washed out"]):
        return [("vivid", strength)]

    if _contains(text, ["膚色不要太紅", "不要太紅", "別太紅", "太紅潤"]):
        return [("natural", "subtle")]

    if _contains(
        text,
        [
            "not too saturated",
            "too saturated",
            "less saturated",
            "less vivid",
            "too colorful",
            "不要太飽和",
            "不要太鮮豔",
            "太鮮豔",
            "顏色太重",
        ],
    ):
        return [("natural", "subtle")]

    if _contains(
        text,
        ["too yellow", "less yellow", "not so yellow", "不要那麼黃", "太黃", "偏黃"],
    ):
        return [("cool", "subtle")]

    if _contains(text, ["shadow", "shadows", "暗部", "陰影"]) and _contains(
        text,
        ["brighten", "brighter", "lighten", "lift", "亮", "拉"],
    ):
        return [("brighten", "subtle")]

    if _contains(text, ["highlight", "highlights", "高光", "亮部"]) and _contains(
        text,
        ["protect", "overexposure", "overexposed", "too bright", "壓", "過曝"],
    ):
        return [("darken", "subtle")]

    if _contains(text, ["center", "middle", "subject", "主體", "中間", "中心"]) and _contains(
        text,
        ["bright", "brighter", "brighten", "clear", "亮", "清楚"],
    ):
        return [("brighten", "subtle")]

    if _contains(text, ["edge", "edges", "border", "background", "邊緣", "背景"]) and _contains(
        text,
        ["dark", "darken", "less", "darker", "暗", "淡"],
    ):
        return [("darken", "subtle")]
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
    elif _contains(
        text,
        [
            "太暗",
            "有點暗",
            "曝光不足",
            "不夠亮",
            "亮",
            "明亮",
            "調亮",
            "bright",
            "brighter",
            "lighten",
            "underexposed",
        ],
    ):
        intent_strengths.append(("brighten", strength))

    if _contains(text, ["暖", "暖色", "偏黃", "warm", "warmer"]):
        intent_strengths.append(("warm", strength))
    elif _contains(text, ["冷", "冷色", "偏藍", "cool", "cooler"]):
        intent_strengths.append(("cool", strength))

    if _contains(text, ["鮮豔", "飽和", "色彩", "顏色", "vivid", "saturated", "colorful"]):
        intent_strengths.append(("vivid", strength))

    if _contains(text, ["清楚", "清晰", "銳利", "細節", "有點糊", "糊", "sharp", "sharpen"]):
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


def _detect_region_mask(text: str) -> tuple[str, str | None]:
    if _contains(text, ["shadow", "shadows", "暗部", "陰影"]):
        return "shadows", "luminance_shadows"
    if _contains(
        text,
        [
            "highlight",
            "highlights",
            "高光",
            "亮部",
        ],
    ):
        return "highlights", "luminance_highlights"
    if _contains(text, ["sky", "天空"]):
        return "sky", "semantic_sky"
    if _contains(text, ["overexposure", "overexposed", "過曝"]) and not _contains(
        text,
        ["不要過曝", "避免過曝", "別過曝"],
    ):
        return "highlights", "luminance_highlights"
    if _contains(text, ["face", "person", "portrait", "臉", "人", "人物", "人像"]):
        return "person", "semantic_person"
    if _contains(text, ["background", "背景"]):
        return "background", "semantic_background"
    if _contains(
        text,
        ["center", "middle", "subject", "主體", "中間", "中心"],
    ):
        return "center", "center_ellipse"
    if _contains(text, ["edge", "edges", "border", "邊緣"]):
        return "edges", "edge_vignette"
    return "all", None


def _detect_strength(text: str) -> str:
    for keyword in ["again", "previous", "last one", "上一張", "剛剛", "再"]:
        if keyword in text:
            return normalize_edit_strength("subtle")

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
