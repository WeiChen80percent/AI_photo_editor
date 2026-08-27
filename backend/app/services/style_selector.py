from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.command_number_normalizer import find_percentage
from app.services.edit_engines import build_engine_parameters
from app.services.edit_plan import build_style_edit_plan
from app.services.style_registry import (
    STYLE_RENDERER_VERSION,
    StyleDefinition,
    get_style_registry,
    normalize_style_text,
)


@dataclass
class StyleSelectionError(ValueError):
    code: str
    message: str
    candidates: tuple[dict[str, Any], ...] = ()
    status_code: int = 422

    def __str__(self) -> str:
        return self.message


_DECIMAL_PATTERN = re.compile(
    r"(?:strength|強度)\s*[:：=]?\s*(0(?:\.[0-9]+)?|1(?:\.0+)?)\b",
    re.IGNORECASE,
)
_ALLOWED_REMAINDER = re.compile(
    r"^(?:"
    r"請|幫我|給我|把|以|套用|使用|換成|改成|調成|設定為|選擇|"
    r"please|apply|use|select|the|a|an|at|with|to|"
    r"style|look|風格|效果|"
    r"strength|強度|的|到|"
    r"輕微|淡一點|柔和一點|正常|完整|強烈|"
    r"subtle|light|medium|normal|full|strong|"
    r"百分之|百分|趴|percent|percentage|"
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred|and|minus|"
    r"[零〇○一二兩两三四五六七八九十百千點点0-9.%：:=\s]"
    r")*$",
    re.IGNORECASE,
)


def try_resolve_style_prompt(prompt: str) -> dict[str, Any] | None:
    normalized_prompt = normalize_style_text(prompt)
    registry = get_style_registry()
    candidates = registry.exact_alias_candidates(normalized_prompt)
    if not candidates:
        candidates = registry.prompt_alias_candidates(normalized_prompt)
    if not candidates:
        return None
    if len(candidates) > 1:
        raise StyleSelectionError(
            code="style_selection_ambiguous",
            message="這段描述同時符合多個風格，請指定其中一個名稱。",
            candidates=tuple(
                _candidate_payload(style) for style in candidates[:5]
            ),
        )
    style = candidates[0]
    matched_surface = _longest_matched_surface(style, normalized_prompt)
    remainder = normalized_prompt.replace(matched_surface, " ", 1).strip()
    if remainder and _ALLOWED_REMAINDER.fullmatch(remainder) is None:
        raise StyleSelectionError(
            code="style_compound_not_supported",
            message=(
                "第一版請分兩步操作：先套用風格，再用下一句調整參數。"
            ),
            candidates=(_candidate_payload(style),),
        )
    strength = _parse_strength(prompt, style)
    plan = build_style_edit_plan(
        prompt=prompt,
        style_id=style.style_id,
        style_version=style.version,
        strength=strength,
        recipe_hash=style.recipe_hash,
        asset_hash=style.asset_hash,
        renderer_version=STYLE_RENDERER_VERSION,
    )
    return {
        "prompt": prompt,
        "resolved_intent": "apply_style",
        "preset_name": style.legacy_preset_name,
        "edit_plan": plan,
        "parameters": build_engine_parameters("opencv", plan),
        "explanation": (
            f"Style Catalog 選擇「{style.display_name_zh}」"
            f"（{style.style_id}@{style.version}，strength={strength:g}）。"
        ),
        "parser_source": "style_registry",
        "fallback_reason": None,
        "style": style.public_metadata(),
    }


def _parse_strength(prompt: str, style: StyleDefinition) -> float:
    percentage = find_percentage(prompt)
    if percentage is not None:
        return style.validate_strength(percentage.value)
    decimal_match = _DECIMAL_PATTERN.search(prompt)
    if decimal_match is not None:
        return style.validate_strength(float(decimal_match.group(1)))
    normalized = normalize_style_text(prompt)
    if any(
        token in normalized
        for token in ("輕微", "淡一點", "柔和一點", "subtle", "light")
    ):
        return style.validate_strength(
            max(style.strength_minimum, min(0.45, style.strength_maximum))
        )
    if any(token in normalized for token in ("強烈", "完整", "strong", "full")):
        return style.validate_strength(style.strength_maximum)
    return style.validate_strength(None)


def _longest_matched_surface(
    style: StyleDefinition,
    normalized_prompt: str,
) -> str:
    surfaces = (
        style.style_id,
        style.display_name_zh,
        style.display_name_en,
        *style.aliases,
    )
    matches = [
        normalized
        for surface in surfaces
        if (normalized := normalize_style_text(surface)) in normalized_prompt
    ]
    if not matches:
        return normalize_style_text(style.style_id)
    return max(matches, key=len)


def _candidate_payload(style: StyleDefinition) -> dict[str, Any]:
    return {
        "style_id": style.style_id,
        "version": style.version,
        "display_name": {
            "zh": style.display_name_zh,
            "en": style.display_name_en,
        },
        "family": style.family,
    }


__all__ = ["StyleSelectionError", "try_resolve_style_prompt"]
