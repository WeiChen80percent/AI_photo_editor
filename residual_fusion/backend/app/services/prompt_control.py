from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from app.services.prompt_intent_encoder import (
    PROMPT_INTENT_NAMES,
    resolve_prompt_intent,
)


PROMPT_STRENGTH_NAMES = ("subtle", "normal", "strong")
PROMPT_CONSTRAINT_NAMES = (
    "preserve_natural",
    "avoid_clipping",
    "preserve_skin_tones",
    "preserve_details",
)

PROMPT_CONTROL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(PROMPT_INTENT_NAMES)},
        "strength": {"type": "string", "enum": list(PROMPT_STRENGTH_NAMES)},
        "constraints": {
            "type": "array",
            "items": {"type": "string", "enum": list(PROMPT_CONSTRAINT_NAMES)},
            "uniqueItems": True,
        },
    },
    "required": ["intent", "strength", "constraints"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PromptControl:
    intent: str
    strength: str
    constraints: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "strength": self.strength,
            "constraints": list(self.constraints),
        }


def validate_prompt_control(payload: Mapping[str, Any]) -> PromptControl:
    expected_fields = {"intent", "strength", "constraints"}
    actual_fields = set(payload)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        raise ValueError(f"Invalid prompt-control fields: missing={missing}, extra={extra}")

    intent = payload["intent"]
    strength = payload["strength"]
    raw_constraints = payload["constraints"]
    if not isinstance(intent, str) or intent not in PROMPT_INTENT_NAMES:
        raise ValueError(f"Unsupported prompt intent: {intent}")
    if not isinstance(strength, str) or strength not in PROMPT_STRENGTH_NAMES:
        raise ValueError(f"Unsupported prompt strength: {strength}")
    if not isinstance(raw_constraints, list):
        raise ValueError("Prompt constraints must be a JSON array")
    if any(not isinstance(item, str) for item in raw_constraints):
        raise ValueError("Every prompt constraint must be a string")
    if len(raw_constraints) != len(set(raw_constraints)):
        raise ValueError("Prompt constraints must not contain duplicates")
    unsupported = sorted(set(raw_constraints) - set(PROMPT_CONSTRAINT_NAMES))
    if unsupported:
        raise ValueError(f"Unsupported prompt constraints: {unsupported}")

    ordered_constraints = tuple(
        name for name in PROMPT_CONSTRAINT_NAMES if name in raw_constraints
    )
    return PromptControl(
        intent=intent,
        strength=strength,
        constraints=ordered_constraints,
    )


def parse_prompt_control_response(response_text: str) -> PromptControl:
    text = response_text.strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM output does not contain a JSON object") from None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("LLM output is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("LLM output must be a JSON object")
    return validate_prompt_control(payload)


def resolve_prompt_control_rule_based(prompt: str | None) -> PromptControl:
    text = (prompt or "").strip().lower()
    return PromptControl(
        intent=resolve_prompt_intent(text),
        strength=_resolve_strength(text),
        constraints=_resolve_constraints(text),
    )


def apply_rule_based_constraint_guard(
    prompt: str | None,
    primary: PromptControl,
) -> PromptControl:
    guarded = set(primary.constraints)
    guarded.update(resolve_prompt_control_rule_based(prompt).constraints)
    return PromptControl(
        intent=primary.intent,
        strength=primary.strength,
        constraints=tuple(name for name in PROMPT_CONSTRAINT_NAMES if name in guarded),
    )


def apply_rule_based_runtime_guard(
    prompt: str | None,
    primary: PromptControl,
) -> PromptControl:
    """Apply only high-confidence lexical corrections around the LLM prediction."""
    text = (prompt or "").strip().lower()
    guarded = apply_rule_based_constraint_guard(text, primary)
    explicit_strength = _resolve_explicit_strength(text)
    return PromptControl(
        intent=(
            "restore_natural"
            if _has_explicit_restore_intent(text)
            else guarded.intent
        ),
        strength=explicit_strength or guarded.strength,
        constraints=guarded.constraints,
    )



def build_prompt_control_prompt(user_prompt: str) -> str:
    return f"""你是 AI Photo Editor 的自然語言控制解析器。
你只能理解文字，不能看見照片，也不能輸出 OpenCV 數值參數。

請把使用者要求轉成以下三個欄位：
1. intent：
   - auto_enhance：泛用的「修好看、自然、專業」，沒有指定主要問題。
   - fix_exposure：主要問題是曝光、亮暗、對比或動態範圍。
   - fix_white_balance：主要問題是偏色、白平衡、色溫或綠洋紅色偏。
   - restore_natural：照片已被過度調色、套太重濾鏡或破壞，需要恢復自然。
2. strength：subtle、normal、strong，代表使用者要求的調整幅度。
3. constraints：只加入使用者明確要求的限制，可用值為
   preserve_natural、avoid_clipping、preserve_skin_tones、preserve_details。

規則：
- 若同時提到多個問題，選擇使用者最主要希望修正的 intent。
- 「不要過度處理」若只是限制，仍屬 auto_enhance；只有照片已經被過度處理時才選 restore_natural。
- 不可猜測照片內容，也不可加入使用者沒有說的 constraint。
- 只輸出符合 schema 的 JSON，不要 Markdown、解釋或思考過程。

使用者要求：{user_prompt.strip()}
""".strip()


def _resolve_strength(text: str) -> str:
    return _resolve_explicit_strength(text) or "normal"


def _resolve_explicit_strength(text: str) -> str | None:
    if _contains(
        text,
        (
            "一點",
            "稍微",
            "微微",
            "小幅",
            "輕微",
            "輕輕",
            "a little",
            "slightly",
            "subtle",
            "gently",
        ),
    ):
        return "subtle"
    if _contains(
        text,
        (
            "大幅",
            "明顯",
            "嚴重",
            "強烈",
            "非常",
            "dramatically",
            "significantly",
            "strongly",
            "very",
            "heavy",
        ),
    ):
        return "strong"
    return None


def _has_explicit_restore_intent(text: str) -> bool:
    return _contains(
        text,
        (
            "\u4fee\u56de",
            "\u6062\u5fa9\u81ea\u7136",
            "\u6062\u590d\u81ea\u7136",
            "\u5fa9\u539f",
            "\u590d\u539f",
            "\u9084\u539f",
            "\u8fd8\u539f",
            "\u6536\u56de",
            "\u5df2\u88ab\u904e\u5ea6\u8655\u7406",
            "\u5df2\u7d93\u904e\u5ea6\u8655\u7406",
            "\u5df2\u904e\u5ea6\u8655\u7406",
            "\u5df2\u88ab\u8fc7\u5ea6\u5904\u7406",
            "\u5df2\u7ecf\u8fc7\u5ea6\u5904\u7406",
            "\u5df2\u8fc7\u5ea6\u5904\u7406",
            "\u8abf\u8272\u592a\u91cd",
            "\u8c03\u8272\u592a\u91cd",
            "\u6ffe\u93e1\u592a\u91cd",
            "\u6ee4\u955c\u592a\u91cd",
            "\u8a87\u5f35\u6ffe\u93e1",
            "\u5938\u5f20\u6ee4\u955c",
            "restore natural",
            "restore the natural",
            "undo the edit",
            "undo this edit",
            "tone down the edit",
            "tone down this edit",
            "overprocessed",
            "over-processed",
            "heavy filter",
            "filter is too heavy",
            "filter looks too strong",
        ),
    )


def _resolve_constraints(text: str) -> tuple[str, ...]:
    matches: set[str] = set()
    if _contains(
        text,
        (
            "保持自然",
            "維持自然",
            "自然一點",
            "不要過度",
            "別太假",
            "不失真",
            "natural-looking",
            "keep it natural",
            "not overdone",
            "realistic",
        ),
    ):
        matches.add("preserve_natural")
    if _contains(
        text,
        (
            "不要過曝",
            "不要讓高光過曝",
            "避免過曝",
            "別過曝",
            "不要死白",
            "不要死黑",
            "保留高光",
            "avoid clipping",
            "avoid blown highlights",
            "protect highlights",
        ),
    ):
        matches.add("avoid_clipping")
    if _contains(
        text,
        (
            "膚色",
            "皮膚顏色",
            "人臉顏色",
            "skin tone",
            "skin tones",
            "complexion",
        ),
    ):
        matches.add("preserve_skin_tones")
    if _contains(
        text,
        (
            "保留細節",
            "保住細節",
            "紋理",
            "質感",
            "preserve detail",
            "preserve details",
            "keep texture",
        ),
    ):
        matches.add("preserve_details")
    return tuple(name for name in PROMPT_CONSTRAINT_NAMES if name in matches)


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)
