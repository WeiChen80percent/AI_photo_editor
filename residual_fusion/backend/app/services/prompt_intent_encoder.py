from __future__ import annotations


PROMPT_INTENT_NAMES = (
    "auto_enhance",
    "fix_exposure",
    "fix_white_balance",
    "restore_natural",
)

_PROMPT_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fix_white_balance": (
        "白平衡",
        "偏色",
        "色偏",
        "偏藍",
        "偏黃",
        "偏綠",
        "偏洋紅",
        "色溫",
        "temperature",
        "tint",
        "white balance",
        "color cast",
    ),
    "fix_exposure": (
        "曝光",
        "黑位",
        "白位",
        "亮度",
        "明暗",
        "對比",
        "暗部",
        "亮部",
        "exposure",
        "brightness",
        "contrast",
        "shadow",
        "highlight",
    ),
    "restore_natural": (
        "亂調",
        "過度處理",
        "調色太重",
        "修回",
        "恢復",
        "還原",
        "復原",
        "restore",
        "overprocessed",
        "over-processed",
    ),
    "auto_enhance": (
        "好看",
        "美化",
        "修圖",
        "優化",
        "自然一點",
        "更自然",
        "專業",
        "專家",
        "expert c",
        "enhance",
        "improve",
        "look better",
        "make it better",
        "professional",
        "auto enhance",
    ),
}


def detect_prompt_intent(prompt: str | None) -> str | None:
    """Return a supervised intent only when the text has an explicit signal."""

    text = (prompt or "").strip().lower()
    if not text:
        return None
    for intent in ("fix_white_balance", "fix_exposure", "restore_natural"):
        if _contains(text, _PROMPT_INTENT_KEYWORDS[intent]):
            return intent
    if _contains(text, _PROMPT_INTENT_KEYWORDS["auto_enhance"]):
        return "auto_enhance"
    return None


def resolve_prompt_intent(prompt: str | None) -> str:
    """Reduce natural-language guidance to a stable downstream intent contract."""
    return detect_prompt_intent(prompt) or "auto_enhance"


def encode_prompt_intent(prompt: str | None) -> dict[str, float]:
    resolved = resolve_prompt_intent(prompt)
    return {
        name: 1.0 if name == resolved else 0.0
        for name in PROMPT_INTENT_NAMES
    }


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)