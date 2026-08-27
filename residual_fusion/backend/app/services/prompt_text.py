"""Shared, deterministic normalization for prompt matching.

The adaptive compiler, rule fallback, and LLM guard layer must make decisions
from the same text.  Unicode NFKC alone does not unify common Chinese glyph
variants such as ``豔`` / ``艷`` / ``艳``.  This module intentionally uses a
small, reviewable character map that is limited to vocabulary used by the
photo-editing contract instead of introducing a broad language-conversion
dependency.

The original prompt is never replaced in API responses or history.  The
normalized value is only used for deterministic matching.
"""

from __future__ import annotations

import re
import unicodedata


_PROMPT_CHARACTER_ALIASES = str.maketrans(
    {
        # English punctuation forms that users routinely type on phones.
        "’": "'",
        "‘": "'",
        "‛": "'",
        "`": "'",
        "´": "'",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
        # The user-reported saturation variants.
        "艳": "豔",
        "艷": "豔",
        "鲜": "鮮",
        # Axis names and common descriptive vocabulary.
        "对": "對",
        "阴": "陰",
        "饱": "飽",
        "颜": "顏",
        "温": "溫",
        "锐": "銳",
        "雾": "霧",
        "蓝": "藍",
        "黄": "黃",
        "边": "邊",
        "缘": "緣",
        "浓": "濃",
        "软": "軟",
        "细": "細",
        "节": "節",
        "肤": "膚",
        "红": "紅",
        "润": "潤",
        "脏": "髒",
        # Common actions, feedback words, and conversational wrappers.
        "调": "調",
        "减": "減",
        "参": "參",
        "数": "數",
        "强": "強",
        "过": "過",
        "后": "後",
        "还": "還",
        "经": "經",
        "点": "點",
        "够": "夠",
        "画": "畫",
        "显": "顯",
        "这": "這",
        "样": "樣",
        "没": "沒",
        "么": "麼",
        "别": "別",
        "开": "開",
        "关": "關",
        "压": "壓",
        "轻": "輕",
        "补": "補",
        "来": "來",
        "个": "個",
        "张": "張",
        "图": "圖",
        "与": "與",
        "并": "並",
        "为": "為",
        "设": "設",
        "归": "歸",
        "较": "較",
        "应": "應",
        "请": "請",
        "帮": "幫",
        "吗": "嗎",
        "无": "無",
        "须": "須",
        "变": "變",
    }
)

_CONTRACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bit['\s]+s\b", re.IGNORECASE), "it is"),
    (re.compile(r"\bthat['\s]+s\b", re.IGNORECASE), "that is"),
    (re.compile(r"\bthis['\s]+s\b", re.IGNORECASE), "this is"),
    (re.compile(r"\bthere['\s]+s\b", re.IGNORECASE), "there is"),
    (
        re.compile(
            r"\b(image|photo|picture)'s(?=\s+(?:(?:a\s+little|a\s+bit|"
            r"slightly|somewhat|very|really|far|way|still)\s+)*(?:too|not|"
            r"under[\s-]?exposed|over[\s-]?sharpened|hazy|foggy|"
            r"washed[\s-]+out|flat|sharp|warm|cool|blue|yellow|bright|dark|"
            r"looking|feeling)\b)",
            re.IGNORECASE,
        ),
        r"\1 is",
    ),
    (re.compile(r"\bi['\s]+d\b", re.IGNORECASE), "i would"),
    # The apostrophe belongs between the contraction's ``n`` and ``t``
    # (``don't``), not before ``nt``.  Accept the common apostrophe-less phone
    # spelling too, while retaining word boundaries so unrelated words cannot
    # be rewritten.
    (re.compile(r"\bdon[\s']*t\b", re.IGNORECASE), "do not"),
    (re.compile(r"\bdoesn[\s']*t\b", re.IGNORECASE), "does not"),
    (re.compile(r"\bdidn[\s']*t\b", re.IGNORECASE), "did not"),
    (re.compile(r"\bisn[\s']*t\b", re.IGNORECASE), "is not"),
    (re.compile(r"\baren[\s']*t\b", re.IGNORECASE), "are not"),
    (re.compile(r"\bwasn[\s']*t\b", re.IGNORECASE), "was not"),
    (re.compile(r"\bweren[\s']*t\b", re.IGNORECASE), "were not"),
    (re.compile(r"\bcan[\s']*t\b", re.IGNORECASE), "can not"),
    (re.compile(r"\bcouldn[\s']*t\b", re.IGNORECASE), "could not"),
    (re.compile(r"\bwouldn[\s']*t\b", re.IGNORECASE), "would not"),
    (re.compile(r"\bshouldn[\s']*t\b", re.IGNORECASE), "should not"),
    (re.compile(r"\bwon[\s']*t\b", re.IGNORECASE), "will not"),
)

_POSSESSIVE_AXIS_PATTERN = re.compile(
    r"\b(?:(?P<determiner>the|my|this|that|a|an)\s+)?"
    r"(?P<object>image|photo|picture)'s\s+"
    r"(?P<axis>exposure(?:\s+value)?|brightness|contrast|highlights?|"
    r"shadows?|saturation|(?:color|colour)\s+temperature|temperature|"
    r"warmth|sharpening|sharpness|sharpen|clarity|dehaze|haze\s+removal|"
    r"vignetting|vignette)\b",
    re.IGNORECASE,
)


def normalize_prompt_text(value: object) -> str:
    """Return the canonical text used by deterministic prompt matchers."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.translate(_PROMPT_CHARACTER_ALIASES).casefold()
    normalized = _POSSESSIVE_AXIS_PATTERN.sub(
        lambda match: (
            f"the {match.group('axis')} of "
            f"{match.group('determiner') or 'the'} {match.group('object')}"
        ),
        normalized,
    )
    for pattern, replacement in _CONTRACTION_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def contains_ascii_letters(value: object) -> bool:
    """Return whether a prompt contains at least one English alphabetic token."""

    return re.search(r"[a-z]", normalize_prompt_text(value)) is not None


def contains_cjk_ideographs(value: object) -> bool:
    """Return whether a prompt contains a CJK unified ideograph."""

    return (
        re.search(
            r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]",
            normalize_prompt_text(value),
        )
        is not None
    )


def is_mixed_english_cjk_prompt(value: object) -> bool:
    """Return whether ASCII English and CJK ideographs share one prompt."""

    return contains_ascii_letters(value) and contains_cjk_ideographs(value)


def is_allowlisted_mixed_prompt(value: object) -> bool:
    """Preserve the two established Chinese vignette alias contracts."""

    text = normalize_prompt_text(value)
    return (
        re.fullmatch(
            r"vignette\s*(?:再\s*)?(?:多|少)\s*一點[。.!?]?",
            text,
            flags=re.IGNORECASE,
        )
        is not None
    )


def is_english_prompt(value: object) -> bool:
    """Return whether the prompt should use the strict English contract.

    Mixed Chinese/English prompts remain on the existing bilingual compiler
    path for v1.  This prevents the English grammar from silently claiming a
    mixed-language sentence that the stage does not promise to support.
    """

    text = normalize_prompt_text(value)
    return bool(
        re.search(r"[a-z]", text)
        and not contains_cjk_ideographs(text)
    )


def prompt_phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Compile one literal phrase with English word boundaries when needed."""

    normalized = normalize_prompt_text(phrase)
    escaped = re.escape(normalized).replace(r"\ ", r"\s+")
    if re.search(r"[a-z]", normalized):
        return re.compile(
            rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])",
            re.IGNORECASE,
        )
    return re.compile(escaped)


def contains_prompt_phrase(text: object, phrase: str) -> bool:
    """Check a normalized phrase without matching inside English words."""

    return prompt_phrase_pattern(phrase).search(normalize_prompt_text(text)) is not None
