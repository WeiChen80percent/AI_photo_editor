from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.services.edit_schema import (
    EDIT_PARAMETER_SPECS,
    PUBLIC_PARAMETER_KEYS,
    default_mask_type_for_region,
    require_region_mask_pair,
)
from app.services.photo_git_schema import PhotoGitSelector


class PhotoGitResolutionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "all": ("全圖", "整張", "整體", "whole image", "entire image", "global"),
    "sky": ("天空", "sky"),
    "person": ("人物", "人像", "主體", "person", "people", "portrait", "subject"),
    "background": ("背景", "background"),
    "highlights": ("亮部", "高光區", "highlights", "highlight region"),
    "shadows": ("暗部", "陰影區", "shadows", "shadow region"),
}

_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "exposure": ("曝光", "exposure"),
    "brightness": ("亮度", "brightness"),
    "contrast": ("對比", "contrast"),
    "highlights": ("高光", "亮部數值", "highlights"),
    "shadows": ("陰影", "暗部數值", "shadows"),
    "whites": ("白位", "whites", "white point"),
    "blacks": ("黑位", "blacks", "black point"),
    "saturation": ("飽和度", "saturation"),
    "vibrance": ("自然飽和度", "vibrance"),
    "temperature": ("色溫", "temperature", "warmth"),
    "white_balance_tint": (
        "白平衡色偏",
        "色偏",
        "white balance tint",
        "tint",
    ),
    "sharpen": ("銳化", "sharpen", "sharpness"),
    "clarity": ("清晰度", "clarity"),
    "dehaze": ("去霧", "dehaze"),
    "vignette": ("暗角", "vignette"),
}


def resolve_selectors(
    *,
    instruction: str,
    selectors: Iterable[PhotoGitSelector],
) -> list[dict[str, Any]]:
    structured = [
        _normalize_structured_selector(selector)
        for selector in selectors
    ]
    if structured:
        return _deduplicate(structured)

    text = str(instruction or "").strip()
    if not text:
        raise PhotoGitResolutionError(
            "photo_git_scope_required",
            "請指定要操作的區域或參數。",
        )
    regions, parameters = _resolve_aliases(text.lower())
    if not regions and not parameters:
        raise PhotoGitResolutionError(
            "photo_git_scope_unclear",
            "無法確定要操作的區域或參數，請從正式選項中指定。",
        )

    if not regions:
        return [{"region": None, "mask_type": None, "parameters": parameters}]
    return [
        {
            "region": region,
            "mask_type": default_mask_type_for_region(region),
            "parameters": list(parameters),
        }
        for region in regions
    ]


def selector_matches(
    selector: dict[str, Any],
    contribution: dict[str, Any],
) -> bool:
    if selector.get("all_contributions") is True:
        return True
    region = selector.get("region")
    if region is not None and contribution.get("region") != region:
        return False
    mask_type = selector.get("mask_type")
    if mask_type is not None and contribution.get("mask_type") != mask_type:
        return False
    parameters = selector.get("parameters") or []
    return not parameters or contribution.get("parameter") in parameters


def _normalize_structured_selector(
    selector: PhotoGitSelector,
) -> dict[str, Any]:
    if selector.all_contributions:
        if selector.region is not None or selector.mask_type is not None or selector.parameters:
            raise PhotoGitResolutionError(
                "photo_git_scope_invalid",
                "all_contributions 不可和區域、遮罩或參數混用。",
            )
        return {
            "region": None,
            "mask_type": None,
            "parameters": [],
            "all_contributions": True,
        }
    region = str(selector.region or "").strip().lower() or None
    mask_type = str(selector.mask_type or "").strip().lower() or None
    parameters: list[str] = []
    for raw in selector.parameters:
        parameter = str(raw or "").strip()
        if parameter not in PUBLIC_PARAMETER_KEYS:
            raise PhotoGitResolutionError(
                "photo_git_parameter_unsupported",
                f"不支援的 Photo Git 參數：{parameter or '空白'}。",
            )
        if parameter not in parameters:
            parameters.append(parameter)
    if region is None and mask_type is not None:
        raise PhotoGitResolutionError(
            "photo_git_scope_invalid",
            "指定 mask_type 時必須同時指定 region。",
        )
    if region is not None:
        try:
            region, mask_type = require_region_mask_pair(
                region,
                mask_type or default_mask_type_for_region(region),
            )
        except ValueError as exc:
            raise PhotoGitResolutionError(
                "photo_git_scope_invalid",
                "指定的 Photo Git 區域與遮罩不相容。",
            ) from exc
    if region is None and not parameters:
        raise PhotoGitResolutionError(
            "photo_git_scope_required",
            "每個 selector 至少要指定區域或參數。",
        )
    return {
        "region": region,
        "mask_type": mask_type,
        "parameters": parameters,
        "all_contributions": False,
    }


def _resolve_aliases(text: str) -> tuple[list[str], list[str]]:
    candidates: list[tuple[int, int, str, str]] = []
    for kind, registry in (
        ("region", _REGION_ALIASES),
        ("parameter", _PARAMETER_ALIASES),
    ):
        for canonical, aliases in registry.items():
            for alias in aliases:
                lowered_alias = alias.lower()
                pattern = (
                    rf"(?<![a-z0-9_]){re.escape(lowered_alias)}"
                    rf"(?![a-z0-9_])"
                    if re.search(r"[a-z]", lowered_alias)
                    else re.escape(lowered_alias)
                )
                for match in re.finditer(pattern, text):
                    candidates.append(
                        (match.start(), match.end(), kind, canonical)
                    )

    # The same literal may mean either a region or a parameter (for example
    # English "highlights"). Do not silently choose both interpretations.
    meanings_by_span: dict[tuple[int, int], set[tuple[str, str]]] = {}
    for start, end, kind, canonical in candidates:
        meanings_by_span.setdefault((start, end), set()).add(
            (kind, canonical)
        )
    if any(len(meanings) > 1 for meanings in meanings_by_span.values()):
        raise PhotoGitResolutionError(
            "photo_git_scope_ambiguous",
            "操作範圍有多種解讀，請用正式選項指定區域或參數。",
        )

    # Prefer the longest alias at each overlapping text span. This prevents
    # "自然飽和度" from also being interpreted as plain "飽和度", while still
    # allowing a sentence to explicitly mention both terms in separate spans.
    selected: list[tuple[int, int, str, str]] = []
    seen_meanings: set[tuple[str, str]] = set()
    for candidate in sorted(
        candidates,
        key=lambda item: (-(item[1] - item[0]), item[0], item[2], item[3]),
    ):
        start, end, kind, canonical = candidate
        meaning = (kind, canonical)
        if meaning in seen_meanings:
            continue
        if any(
            start < chosen_end and chosen_start < end
            for chosen_start, chosen_end, _, _ in selected
        ):
            continue
        selected.append(candidate)
        seen_meanings.add(meaning)

    selected.sort(key=lambda item: item[0])
    regions = [
        canonical
        for _, _, kind, canonical in selected
        if kind == "region"
    ]
    parameters = [
        canonical
        for _, _, kind, canonical in selected
        if kind == "parameter"
    ]
    return regions, parameters


def _deduplicate(
    selectors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for selector in selectors:
        key = (
            selector.get("region"),
            selector.get("mask_type"),
            tuple(selector.get("parameters") or []),
            bool(selector.get("all_contributions")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(selector)
    return result
