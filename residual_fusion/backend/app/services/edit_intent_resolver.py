# -*- coding: utf-8 -*-
from collections.abc import Callable, Mapping
from typing import Any
import json
import os
import urllib.request

from app.services.edit_intent_templates import (
    format_template_catalog_for_prompt,
    limit_intent_strengths_for_prompt,
    normalize_edit_intent,
    normalize_preset_name,
    normalize_edit_strength,
)
from app.services.edit_engines import build_engine_parameters
from app.services.edit_plan import (
    build_compound_edit_plan,
    build_preset_edit_plan,
    build_raw_parameter_edit_plan,
    build_single_edit_plan,
)
from app.services.edit_schema import (
    default_mask_type_for_region,
    validate_edit_mask_type,
    validate_edit_parameters,
    validate_edit_region,
)
from app.services.prompt_parser import parse_edit_prompt
from app.services.prompt_text import normalize_prompt_text


LLMClient = Callable[[str], str]


def resolve_edit_intent(
    prompt: str | None,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    prompt_text = (prompt or "").strip()
    client = llm_client if llm_client is not None else _get_default_llm_client()

    if client is not None and prompt_text:
        try:
            return _parse_llm_response(prompt_text, client(prompt_text))
        except Exception as exc:
            return _fallback_result(prompt_text, str(exc))

    fallback_reason = "LLM disabled or prompt missing; using rule-based fallback."
    return _fallback_result(prompt_text, fallback_reason)


def _parse_llm_response(prompt: str, response_text: str) -> dict[str, Any]:
    data = _load_json_object(response_text)

    preset_result = _parse_preset_response(prompt, data)
    if preset_result is not None:
        return preset_result

    compound_result = _parse_compound_template_response(prompt, data)
    if compound_result is not None:
        return compound_result

    template_result = _parse_template_response(prompt, data)
    if template_result is not None:
        return template_result

    raw_parameters = data.get("parameters")
    if not isinstance(raw_parameters, Mapping):
        raise ValueError("LLM parameters must be a JSON object.")

    parameters = validate_edit_parameters(raw_parameters)
    if not parameters:
        raise ValueError("LLM did not provide any supported OpenCV parameters.")
    region, mask_type = _resolve_region_mask(prompt, data)
    edit_plan = build_raw_parameter_edit_plan(
        prompt=prompt,
        parameters=parameters,
        region=region,
        mask_type=mask_type,
    )

    resolved_intent = str(data.get("resolved_intent") or "llm_parameters")
    explanation = str(
        data.get("explanation")
        or "LLM converted the prompt into direct OpenCV parameters."
    )

    return {
        "prompt": prompt,
        "resolved_intent": resolved_intent,
        "preset_name": None,
        "edit_plan": edit_plan,
        "parameters": build_engine_parameters("opencv", edit_plan),
        "explanation": explanation,
        "parser_source": "llm",
        "fallback_reason": None,
    }


def _parse_preset_response(prompt: str, data: dict[str, Any]) -> dict[str, Any] | None:
    raw_preset_name = data.get("preset") or data.get("preset_name")
    if raw_preset_name is None:
        return None

    preset_name = normalize_preset_name(str(raw_preset_name))
    if preset_name is None:
        raise ValueError(f"LLM selected unsupported preset: {raw_preset_name}")
    region, mask_type = _resolve_region_mask(prompt, data)
    edit_plan = build_preset_edit_plan(
        prompt=prompt,
        preset_name=preset_name,
        region=region,
        mask_type=mask_type,
    )

    return {
        "prompt": prompt,
        "resolved_intent": "apply_preset",
        "preset_name": preset_name,
        "edit_plan": edit_plan,
        "parameters": build_engine_parameters("opencv", edit_plan),
        "explanation": (
            str(data.get("explanation"))
            if data.get("explanation")
            else f"LLM selected the {preset_name} preset fallback."
        ),
        "parser_source": "llm",
        "fallback_reason": None,
    }


def _parse_compound_template_response(
    prompt: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    raw_edits = data.get("edits")
    if raw_edits is None:
        return None
    if not isinstance(raw_edits, list) or not raw_edits:
        raise ValueError("LLM edits must be a non-empty JSON array.")

    intent_strengths: list[tuple[str, str]] = []
    for raw_edit in raw_edits:
        if not isinstance(raw_edit, Mapping):
            raise ValueError("Each LLM edit must be a JSON object.")
        raw_intent = raw_edit.get("intent") or raw_edit.get("edit_intent")
        resolved_intent = normalize_edit_intent(str(raw_intent or ""))
        if resolved_intent is None:
            raise ValueError(f"LLM selected unsupported edit intent: {raw_intent}")
        strength = normalize_edit_strength(
            str(raw_edit.get("strength") or raw_edit.get("intensity") or "normal")
        )
        intent_strengths.append((resolved_intent, strength))

    limited_intent_strengths = limit_intent_strengths_for_prompt(
        prompt,
        intent_strengths,
    )
    limited_intent_strengths = _apply_prompt_specific_compound_guards(
        prompt,
        limited_intent_strengths,
    )
    region, mask_type = _resolve_region_mask(prompt, data)
    edit_plan = build_compound_edit_plan(
        prompt=prompt,
        intent_strengths=limited_intent_strengths,
        region=region,
        mask_type=mask_type,
    )
    edits = edit_plan["edits"]
    resolved_intent = (
        limited_intent_strengths[0][0]
        if len(limited_intent_strengths) == 1
        else "compound"
    )
    explanation = str(
        data.get("explanation")
        or f"LLM selected {len(edits)} safe edit intent templates."
    )

    return {
        "prompt": prompt,
        "resolved_intent": resolved_intent,
        "preset_name": None,
        "edits": edits,
        "edit_plan": edit_plan,
        "parameters": build_engine_parameters("opencv", edit_plan),
        "explanation": explanation,
        "parser_source": "llm",
        "fallback_reason": None,
    }


def _apply_prompt_specific_compound_guards(
    prompt: str,
    intent_strengths: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    text = normalize_prompt_text(prompt)
    if not intent_strengths:
        return intent_strengths

    if _contains(
        text,
        ["一點", "稍微", "微微", "有點", "小幅", "little", "slightly", "light"],
    ):
        intent_strengths = [(intent, "subtle") for intent, _ in intent_strengths]

    has_natural = any(intent == "natural" for intent, _ in intent_strengths)
    if has_natural and _contains(
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
        intent_strengths = [
            (intent, strength)
            for intent, strength in intent_strengths
            if intent != "vivid"
        ]

    has_vivid = any(intent == "vivid" for intent, _ in intent_strengths)
    if has_vivid and _contains(
        text,
        ["膚色不要太紅", "不要太紅", "別太紅", "不要太紅潤", "別太紅潤"],
    ):
        return [
            (intent, strength)
            for intent, strength in intent_strengths
            if intent != "natural"
        ]

    return intent_strengths


def _parse_template_response(prompt: str, data: dict[str, Any]) -> dict[str, Any] | None:
    raw_intent = data.get("intent") or data.get("edit_intent")
    if raw_intent is None:
        return None

    resolved_intent = normalize_edit_intent(str(raw_intent))
    if resolved_intent is None:
        raise ValueError(f"LLM selected unsupported edit intent: {raw_intent}")

    strength = normalize_edit_strength(
        str(data.get("strength") or data.get("intensity") or "normal")
    )
    style = str(data.get("style") or "natural")
    region, mask_type = _resolve_region_mask(prompt, data)
    edit_plan = build_single_edit_plan(
        prompt=prompt,
        intent=resolved_intent,
        strength=strength,
        region=region,
        mask_type=mask_type,
    )
    explanation = str(
        data.get("explanation")
        or f"LLM selected the {resolved_intent}/{strength} intent template."
    )

    return {
        "prompt": prompt,
        "resolved_intent": resolved_intent,
        "preset_name": None,
        "strength": strength,
        "style": style,
        "edit_plan": edit_plan,
        "parameters": build_engine_parameters("opencv", edit_plan),
        "explanation": explanation,
        "parser_source": "llm",
        "fallback_reason": None,
    }


def _load_json_object(response_text: str) -> dict[str, Any]:
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        start = response_text.find("{")
        end = response_text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("LLM 輸出格式無法解析")
        try:
            data = json.loads(response_text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("LLM 輸出格式無法解析") from exc

    if not isinstance(data, dict):
        raise ValueError("LLM 輸出格式無法解析")

    return data


def _fallback_result(prompt: str, fallback_reason: str) -> dict[str, Any]:
    result = parse_edit_prompt(prompt)
    if "edit_plan" not in result:
        result["edit_plan"] = build_raw_parameter_edit_plan(
            prompt=prompt,
            parameters=result.get("parameters"),
        )
    result["parameters"] = build_engine_parameters("opencv", result["edit_plan"])
    result["parser_source"] = "rule_based_fallback"
    result["fallback_reason"] = fallback_reason
    return result


def _resolve_region_mask(prompt: str, data: Mapping[str, Any]) -> tuple[str, str]:
    region = validate_edit_region(data.get("region"))
    if region == "all":
        region = _infer_region_from_prompt(prompt)

    mask_type = validate_edit_mask_type(data.get("mask_type"))
    if mask_type == "none":
        mask_type = default_mask_type_for_region(region)
    return region, mask_type


def _infer_region_from_prompt(prompt: str) -> str:
    text = normalize_prompt_text(prompt)
    if _contains(text, ["shadow", "shadows", "暗部", "陰影"]):
        return "shadows"
    if _contains(
        text,
        ["highlight", "highlights", "高光", "亮部"],
    ):
        return "highlights"
    if _contains(text, ["sky", "天空"]):
        return "sky"
    if _contains(text, ["overexposure", "overexposed", "過曝"]) and not _contains(
        text,
        ["不要過曝", "避免過曝", "別過曝"],
    ):
        return "highlights"
    if _contains(
        text,
        [
            "center",
            "middle",
            "subject",
            "主體",
            "中間",
            "中心",
        ],
    ):
        return "center"
    if _contains(text, ["face", "person", "portrait", "臉", "人", "人物", "人像"]):
        return "person"
    if _contains(text, ["background", "背景"]):
        return "background"
    if _contains(text, ["edge", "edges", "border", "邊緣"]):
        return "edges"
    return "all"


def _get_default_llm_client() -> LLMClient | None:
    enabled = os.getenv("AI_PHOTO_USE_LLM", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None
    return _call_ollama_prompt_parser


def _call_ollama_prompt_parser(prompt: str) -> str:
    url = os.getenv("AI_PHOTO_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
    model = os.getenv("AI_PHOTO_OLLAMA_MODEL", "llama3.2:3b")
    timeout = float(os.getenv("AI_PHOTO_OLLAMA_TIMEOUT", "8"))
    num_predict = int(os.getenv("AI_PHOTO_OLLAMA_NUM_PREDICT", "128"))
    payload = {
        "model": model,
        "prompt": _build_ollama_prompt(prompt),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_payload = json.loads(response.read().decode("utf-8"))

    response_text = response_payload.get("response")
    if not isinstance(response_text, str):
        raise ValueError("Ollama response did not include a response string.")
    return response_text


def _build_ollama_prompt(user_prompt: str) -> str:
    return f"""
You convert natural-language photo editing requests into a safe edit intent template.
Return only one JSON object. Do not include markdown or extra text.
Return either "preset" for abstract visual styles, or "edits" for concrete adjustments.
Use at most 2 edits by default.
Use 3 edits only when the user explicitly lists three operations.
Treat "a little" or "一點" continuation requests as subtle strength.
Treat "not too vivid/saturated" constraints as natural, not as a vivid edit.
Treat skin redness constraints as a strength guard, not as a separate natural edit.
For local edits, use region sky, person, background, highlights, or shadows.
Do not replace sky/person/background with geometric center or edge regions.

Intent template catalog:
{format_template_catalog_for_prompt()}

Concrete adjustment JSON format:
{{
  "edits": [
    {{"intent": "brighten", "strength": "subtle"}}
  ]
}}

Abstract style JSON format:
{{
  "preset": "vintage_film"
}}

Examples:
- "亮一點" -> {{"edits":[{{"intent":"brighten","strength":"subtle"}}]}}
- "太亮了，暗一點" -> {{"edits":[{{"intent":"darken","strength":"subtle"}}]}}
- "讓照片暖一點" -> {{"edits":[{{"intent":"warm","strength":"subtle"}}]}}
- "色彩更鮮豔" -> {{"edits":[{{"intent":"vivid","strength":"strong"}}]}}
- "自然一點，不要太鮮豔" -> {{"edits":[{{"intent":"natural","strength":"subtle"}}]}}
- "再亮一點" -> {{"edits":[{{"intent":"brighten","strength":"subtle"}}]}}
- "亮一點但不要過曝" -> {{"edits":[{{"intent":"brighten","strength":"subtle"}}]}}
- "剛剛太鮮豔，收斂一點" -> {{"edits":[{{"intent":"natural","strength":"subtle"}}]}}
- "用剛剛那張再自然一點" -> {{"edits":[{{"intent":"natural","strength":"subtle"}}]}}
- "色彩鮮豔一點但膚色不要太紅" -> {{"edits":[{{"intent":"vivid","strength":"subtle"}}]}}
- "照片太悶了" -> {{"edits":[{{"intent":"brighten","strength":"normal"}},{{"intent":"vivid","strength":"normal"}}]}}
- "看起來清爽一點" -> {{"edits":[{{"intent":"brighten","strength":"subtle"}},{{"intent":"cool","strength":"subtle"}}]}}
- "霧霧的" -> {{"edits":[{{"intent":"dehaze","strength":"subtle"}}]}}
- "臉太暗" -> {{"region":"person","edits":[{{"intent":"brighten","strength":"subtle"}}]}}
- "天空太亮" -> {{"region":"sky","edits":[{{"intent":"darken","strength":"subtle"}}]}}
- "背景有點搶" -> {{"region":"background","edits":[{{"intent":"natural","strength":"subtle"}}]}}
- "亮一點、冷一點、清晰一點" -> {{"edits":[{{"intent":"brighten","strength":"subtle"}},{{"intent":"cool","strength":"subtle"}},{{"intent":"sharpen","strength":"subtle"}}]}}
- "暗黃底片感" -> {{"preset":"vintage_film"}}
- "電影感" -> {{"preset":"cinematic"}}
- "清新日系風格" -> {{"preset":"fresh_japanese"}}

User request: {user_prompt}
""".strip()


def _contains(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)
