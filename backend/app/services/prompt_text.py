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


def normalize_prompt_text(value: object) -> str:
    """Return the canonical text used by deterministic prompt matchers."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.translate(_PROMPT_CHARACTER_ALIASES).casefold()
    return re.sub(r"\s+", " ", normalized).strip()
