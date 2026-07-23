from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.services.adaptive_policy import (
    ADAPTIVE_AXIS_ORDER,
    AXIS_POLICIES,
    INTENT_TO_AXIS_DIRECTION,
)
from app.services.edit_intent_templates import normalize_preset_name
from app.services.edit_schema import default_mask_type_for_region
from app.services.prompt_text import normalize_prompt_text


MAX_PRIMARY_OPERATIONS = 3


@dataclass
class AdaptiveCompileError(ValueError):
    code: str
    message: str
    issues: tuple[dict[str, Any], ...] = ()
    status_code: int = 422

    def __str__(self) -> str:
        return self.message


_NEGATION_PREFIXES = (
    "還沒有",
    "我不想再",
    "不想再",
    "我不希望",
    "不希望",
    "不要再",
    "先不要",
    "我不想",
    "不想",
    "沒有",
    "不算",
    "不是",
    "並非",
    "不要",
    "別再",
    "別",
    "不必",
    "不用",
    "無需",
    "還沒",
    "尚未",
    "未",
    "不",
    "not ",
    "no ",
    "no more ",
    "don't ",
    "don't make it ",
    "i don't want ",
    "don't want ",
    "do not ",
    "do not make it ",
    "i do not want ",
    "do not want ",
)

_SATISFIED_MARKERS = ("這樣剛好", "剛好", "可以了", "這樣就好", "just right", "good now")
_GLOBAL_RESET_MARKERS = (
    "恢復原圖",
    "回到原圖",
    "回原圖",
    "重新開始",
    "全部重設",
    "reset all",
    "start over",
)
_VAGUE_OPPOSITE_MARKERS = ("太多了", "過頭了", "退一點", "收回一點", "少一點", "too much", "back off")
_AMBIGUOUS_MARKERS = (
    "我不確定",
    "不確定",
    "不知道",
    "看起來不對",
    "顏色偏了",
    "不清楚",
    "清楚過頭",
    "亮的地方爆了",
    "怪怪",
    "不太對",
    "不對勁",
    "不好看",
    "不喜歡",
    "wrong",
    "weird",
)
_STRONG_MARKERS = ("大幅", "很多", "非常", "strongly", "much ", "very ")
_SUBTLE_MARKERS = ("一點點", "一點", "稍微", "微微", "些微", "少許", "a little", "slightly", "subtle")

_REGION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("all", ("全圖", "整張", "整體", "whole image", "entire image", "global")),
    ("sky", ("天空", "sky")),
    ("person", ("人物", "人像", "臉", "face", "person", "portrait")),
    ("background", ("背景", "background")),
    ("center", ("中心", "中間", "主體", "center", "middle", "subject")),
    ("edges", ("邊緣", "邊框", "edge", "edges", "border")),
)

_AXIS_LABELS: dict[str, tuple[str, ...]] = {
    "exposure": ("曝光值", "曝光", "exposure value", "exposure"),
    "brightness": ("亮度", "brightness"),
    "contrast": ("對比度", "對比", "反差", "contrast"),
    "highlights": ("高光參數", "高光值", "高光", "highlights value", "highlights"),
    "shadows": ("陰影參數", "陰影值", "陰影", "shadows value", "shadows"),
    "saturation": ("飽和度", "鮮豔度", "飽和", "saturation"),
    "temperature": ("色溫", "color temperature", "temperature", "warmth"),
    "sharpen": ("銳化強度", "銳化", "銳利度", "sharpening", "sharpness", "sharpen"),
    "clarity": ("清晰度", "clarity"),
    "dehaze": ("去霧強度", "去霧", "除霧", "去霾", "haze removal", "dehaze"),
    "vignette": ("暗角強度", "暗角", "vignetting", "vignette"),
}

_AXIS_FEEDBACK: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # tuple: feedback requesting positive direction, feedback requesting negative direction
    "exposure": (
        (
            "曝光不足",
            "曝光太低",
            "曝光不太夠",
            "曝光有點低",
            "曝光有點不夠",
            "曝光還不夠",
            "underexposed",
            "not enough exposure",
        ),
        (
            "曝光太高",
            "曝光過頭",
            "曝光加太多",
            "曝光已經太高",
            "曝光拉太高",
            "曝光有點過",
            "曝光別這麼高",
            "exposure is too high",
            "too much exposure",
        ),
    ),
    "brightness": (
        (
            "太暗",
            "過暗",
            "有點暗",
            "暗暗的",
            "不夠亮",
            "亮度太低",
            "很暗了",
            "已經很暗",
            "不要那麼暗",
            "不要太暗",
            "too dark",
            "not bright enough",
        ),
        (
            "太亮",
            "過亮",
            "亮太多",
            "亮過頭",
            "亮度太高",
            "很亮了",
            "已經很亮",
            "不要那麼亮",
            "不要太亮",
            "別那麼亮",
            "too bright",
            "not too bright",
        ),
    ),
    "contrast": (
        (
            "對比不足",
            "對比太低",
            "對比還不夠",
            "反差不夠",
            "太平",
            "平平的",
            "too flat",
            "not enough contrast",
        ),
        (
            "對比太強",
            "對比太高",
            "反差太大",
            "對比太重",
            "對比有點衝",
            "contrast is too strong",
            "too much contrast",
            "too contrasty",
        ),
    ),
    "highlights": (
        (
            "高光太低",
            "高光不夠",
            "高光有點低",
            "高光還不夠",
            "高光參數壓太多",
            "高光壓太多",
            "高光壓過頭",
            "亮部壓太多",
            "highlights too low",
        ),
        (
            "高光太高",
            "高光過強",
            "高光太刺眼",
            "高光有點刺眼",
            "高光爆掉",
            "高光拉太多",
            "亮部太刺眼",
            "highlights are too strong",
            "highlights too high",
        ),
    ),
    "shadows": (
        (
            "陰影參數太低",
            "陰影值太低",
            "陰影參數壓太低",
            "陰影參數壓太多",
            "陰影太暗",
            "暗部太暗",
            "陰影死黑",
            "shadows too dark",
        ),
        (
            "陰影參數太高",
            "陰影值太高",
            "陰影參數拉太多",
            "陰影參數拉太高",
            "陰影參數提過頭",
            "陰影參數提太高",
            "陰影參數別那麼高",
            "陰影太亮",
            "暗部太亮",
            "shadows value is too high",
            "shadows too bright",
        ),
    ),
    "saturation": (
        (
            "不夠鮮豔",
            "不夠飽和",
            "鮮豔度不夠",
            "飽和度太低",
            "顏色太淡",
            "色彩太淡",
            "not vivid enough",
            "not saturated enough",
        ),
        (
            "太鮮豔",
            "過鮮豔",
            "太飽和",
            "過飽和",
            "飽和度太高",
            "顏色太重",
            "顏色太濃",
            "不要那麼鮮豔",
            "不要太鮮豔",
            "別那麼鮮豔",
            "不要那麼飽和",
            "不要太飽和",
            "too vivid",
            "too saturated",
            "not too saturated",
        ),
    ),
    "temperature": (
        (
            "太冷",
            "過冷",
            "太藍",
            "過藍",
            "偏冷",
            "藍藍的",
            "色溫太低",
            "too cool",
            "too blue",
        ),
        (
            "太暖",
            "過暖",
            "太黃",
            "過黃",
            "偏暖",
            "黃黃的",
            "色溫太高",
            "too warm already",
            "too warm",
            "too yellow",
        ),
    ),
    "sharpen": (
        (
            "不夠銳利",
            "不夠銳",
            "銳化不夠",
            "銳化後還是太軟",
            "銳化邊緣太軟",
            "銳利度不夠",
            "too soft",
            "not sharp enough",
        ),
        (
            "太銳利",
            "太銳",
            "銳化過頭",
            "銳化後邊緣太硬",
            "銳化邊緣太硬",
            "銳化太重",
            "銳化感太重",
            "過度銳化",
            "oversharp",
            "oversharpened",
        ),
    ),
    "clarity": (
        (
            "清晰度不足",
            "清晰度太低",
            "清晰度還不夠",
            "清晰度的局部對比不夠",
            "not enough clarity",
        ),
        (
            "清晰度太高",
            "清晰度拉太高",
            "清晰度效果太重",
            "清晰度的局部對比太強",
            "清晰度拉太多",
            "清晰度別那麼高",
            "too much clarity",
        ),
    ),
    "dehaze": (
        (
            "去霧不足",
            "去霧還不夠",
            "還有霧",
            "霧霧",
            "霧還沒散",
            "霧霾",
            "有霧",
            "hazy",
            "foggy",
        ),
        (
            "去霧太重",
            "去霧過頭",
            "除霧太多",
            "去霧效果太強",
            "去霧拉太多",
            "別去那麼多霧",
            "too much dehaze",
        ),
    ),
    "vignette": (
        (
            "暗角不夠",
            "暗角太淡",
            "暗角太少",
            "暗角還不夠深",
            "暗角還要一點",
            "not enough vignette",
        ),
        (
            "暗角太重",
            "暗角太深",
            "暗角太黑",
            "暗角邊緣太黑",
            "暗角加過頭",
            "暗角過頭",
            "too much vignette",
        ),
    ),
}

_STRONG_DESCRIPTION_PATTERNS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str, str]] = {
    "brightness": (("亮很多", "很亮", "非常亮", "very bright", "much brighter"), ("暗很多", "很暗", "非常暗", "very dark", "much darker"), "brighten", "darken"),
    "temperature": (("暖很多", "很暖", "非常暖", "very warm", "much warmer"), ("冷很多", "很冷", "非常冷", "very cool", "much cooler"), "warm", "cool"),
    "saturation": (("鮮豔很多", "很鮮豔", "非常鮮豔", "很飽和", "非常飽和", "very vivid", "very saturated", "much more vivid"), ("淡很多", "很淡", "非常淡", "very dull", "much duller", "much less saturated"), "vivid", "natural"),
}

_GROUP_FEEDBACK_PATTERNS: tuple[tuple[str, str, int, tuple[str, ...]], ...] = (
    ("dehaze", "dehaze", -1, ("去霧效果太重", "去霧效果過頭", "dehaze effect is too strong")),
    ("sharpen", "sharpen", -1, ("銳化效果太重", "銳化效果過頭", "sharpening effect is too strong")),
    ("brightness", "brighten", -1, ("提亮效果太重", "亮度效果太重", "brightening effect is too strong")),
    ("temperature", "warm", -1, ("暖色效果太重", "暖化效果太重", "warming effect is too strong")),
    ("saturation", "vivid", -1, ("鮮豔效果太重", "增艷效果太重", "vivid effect is too strong")),
)

_EXPLICIT_DIRECTION_PATTERNS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "exposure": (
        (
            "提高曝光",
            "增加曝光",
            "上調曝光",
            "曝光提高",
            "曝光增加",
            "曝光高一點",
            "曝光值高一點",
            "曝光再高一點",
            "把曝光往上調",
            "曝光再多一點",
            "再補點曝光",
            "raise exposure",
            "increase exposure",
            "more exposure",
            "exposure up a little",
        ),
        (
            "降低曝光",
            "減少曝光",
            "下調曝光",
            "曝光降低",
            "曝光減少",
            "曝光低一點",
            "曝光值低一點",
            "把曝光往下調",
            "曝光收一點",
            "lower exposure",
            "decrease exposure",
            "less exposure",
            "exposure down a little",
        ),
    ),
    "brightness": (
        (
            "提高亮度",
            "增加亮度",
            "上調亮度",
            "亮度提高",
            "亮度增加",
            "亮度高一點",
            "亮度加一點",
            "raise brightness",
            "increase brightness",
            "brightness up a little",
        ),
        (
            "降低亮度",
            "減少亮度",
            "下調亮度",
            "亮度降低",
            "亮度減少",
            "亮度低一點",
            "lower brightness",
            "decrease brightness",
            "brightness down a little",
        ),
    ),
    "contrast": (
        (
            "提高對比",
            "增加對比",
            "加強對比",
            "對比提高",
            "對比增加",
            "對比高一點",
            "對比再高一點",
            "反差大一點",
            "對比再來一點",
            "more contrast",
            "increase contrast",
            "raise contrast",
            "contrast a bit higher",
        ),
        (
            "降低對比",
            "減少對比",
            "弱化對比",
            "對比降低",
            "對比減少",
            "對比低一點",
            "反差小一點",
            "對比收一點",
            "less contrast",
            "decrease contrast",
            "lower contrast",
            "contrast a bit lower",
        ),
    ),
    "highlights": (
        (
            "提高高光",
            "拉高高光",
            "提亮高光",
            "高光提高",
            "高光高一點",
            "高光抬一點",
            "高光再抬一點",
            "raise highlights",
            "lift highlights",
            "highlights a bit higher",
        ),
        (
            "壓低高光",
            "降低高光",
            "收回高光",
            "高光降低",
            "高光低一點",
            "高光收一點",
            "recover highlights",
            "lower highlights",
            "reduce highlights",
            "highlights a bit lower",
        ),
    ),
    "shadows": (
        (
            "提亮陰影",
            "拉高陰影",
            "打開陰影",
            "提高陰影",
            "陰影參數高一點",
            "陰影參數再高一點",
            "陰影值抬一點",
            "lift the shadows a little",
            "lift shadows",
            "open shadows",
            "raise shadows",
        ),
        (
            "壓暗陰影",
            "壓低陰影",
            "加深陰影",
            "降低陰影",
            "陰影參數低一點",
            "陰影參數收一點",
            "crush shadows",
            "lower shadows",
            "deepen shadows",
        ),
    ),
    "saturation": (
        (
            "提高飽和",
            "增加飽和",
            "上調飽和",
            "飽和度提高",
            "飽和度增加",
            "增加鮮豔度",
            "increase saturation",
            "more saturation",
        ),
        (
            "降低飽和",
            "減少飽和",
            "下調飽和",
            "飽和度降低",
            "飽和度減少",
            "收點飽和",
            "decrease saturation",
            "less saturation",
            "desaturate",
        ),
    ),
    "temperature": (
        (
            "提高色溫",
            "增加色溫",
            "上調色溫",
            "色溫提高",
            "色溫增加",
            "色溫高一點",
            "色溫往暖調一點",
            "increase temperature",
            "raise temperature",
        ),
        (
            "降低色溫",
            "減少色溫",
            "下調色溫",
            "色溫降低",
            "色溫減少",
            "色溫低一點",
            "色溫往冷調一點",
            "decrease temperature",
            "lower temperature",
        ),
    ),
    "sharpen": (
        (
            "提高銳化",
            "增加銳化",
            "加強銳化",
            "銳化提高",
            "銳化補一點",
            "銳化加一點",
            "銳化後邊緣再利一點",
            "increase sharpen",
            "more sharpening",
        ),
        (
            "降低銳化",
            "減少銳化",
            "銳化少一點",
            "銳化收一點",
            "reduce sharpen",
            "less sharpening",
        ),
    ),
    "clarity": (
        (
            "提高清晰度",
            "增加清晰度",
            "清晰度提高",
            "清晰度高一點",
            "清晰度再高一點",
            "補點清晰度",
            "清晰度再來一點",
            "increase clarity",
            "more clarity",
            "more clarity please",
        ),
        (
            "降低清晰度",
            "減少清晰度",
            "清晰度降低",
            "清晰度低一點",
            "清晰度收一點",
            "清晰度效果收一點",
            "decrease clarity",
            "less clarity",
        ),
    ),
    "dehaze": (
        (
            "提高去霧",
            "增加去霧",
            "加強去霧",
            "去霧提高",
            "去霧再加一點",
            "去霧再多一點",
            "再去點霧",
            "霧再清一點",
            "more dehaze",
            "increase dehaze",
        ),
        (
            "降低去霧",
            "減少去霧",
            "去霧少一點",
            "收點去霧",
            "去霧收一點",
            "less dehaze",
            "reduce dehaze",
        ),
    ),
    "vignette": (
        (
            "增加暗角",
            "加暗角",
            "暗角多一點",
            "暗角再多一點",
            "暗角深一點",
            "再補點暗角",
            "暗角再補一點",
            "more vignette",
            "add vignette",
            "add a little vignette",
            "increase vignette",
            "vignette 再多一點",
        ),
        (
            "減少暗角",
            "降低暗角",
            "暗角少一點",
            "去除暗角",
            "去掉一點暗角",
            "暗角收一點",
            "暗角感淡一點",
            "less vignette",
            "reduce vignette",
            "remove vignette",
            "vignette 少一點",
        ),
    ),
}

_MACRO_DIRECTION_PATTERNS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str, str]] = {
    "brightness": (
        ("亮一點", "調亮", "變亮", "更亮", "再亮", "brighten", "brighter"),
        ("暗一點", "調暗", "變暗", "更暗", "再暗", "darken", "darker"),
        "brighten",
        "darken",
    ),
    "temperature": (
        ("暖一點", "調暖", "變暖", "更暖", "再暖", "warmer"),
        ("冷一點", "調冷", "變冷", "更冷", "再冷", "cooler"),
        "warm",
        "cool",
    ),
    "saturation": (
        ("鮮豔一點", "更鮮豔", "再鮮豔", "more vivid", "more colorful"),
        ("自然一點", "淡一點", "更自然", "不要那麼鮮豔", "less vivid", "more natural"),
        "vivid",
        "natural",
    ),
    "sharpen": (
        ("銳利一點", "更銳利", "清楚一點", "更清楚", "sharper", "make it sharp"),
        ("不要那麼銳", "銳化少一點", "less sharp", "less sharpening"),
        "sharpen",
        "reduce_sharpen",
    ),
    "contrast": (
        (),
        ("柔和一點", "更柔和", "softer", "soften the look"),
        "increase_contrast",
        "soft",
    ),
    "dehaze": (
        ("去霧一點", "除霧一點", "去霾", "dehaze it"),
        ("去霧少一點", "less dehaze"),
        "dehaze",
        "dehaze",
    ),
}

_UNSUPPORTED_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dehaze", ("加霧", "更霧", "變霧", "add haze", "more fog", "make it hazy")),
    ("sharpen", ("變模糊", "加模糊", "模糊一點", "blur it", "make it blurry")),
    ("vignette", ("亮角", "提亮邊緣", "反向暗角", "reverse vignette")),
)

_UNSUPPORTED_EDIT_ACTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("grain", ("增加顆粒", "加顆粒", "顆粒多一點", "add grain", "more grain")),
    ("noise", ("增加噪點", "加噪點", "add noise", "more noise")),
    ("rotate", ("旋轉", "rotate")),
    ("crop", ("裁切", "裁剪", "crop")),
    ("denoise", ("降噪", "去噪", "減少噪點", "denoise", "noise reduction")),
    ("red_eye", ("移除紅眼", "去紅眼", "remove red eye", "red-eye removal")),
    ("white_balance", ("調整白平衡", "白平衡", "white balance")),
    ("hue", ("增加色調", "調整色調", "改色調", "色相", "adjust hue", "hue")),
    ("resize", ("調整尺寸", "縮放圖片", "resize image", "resize")),
    ("flip", ("水平翻轉", "垂直翻轉", "flip image", "mirror image")),
    ("perspective", ("校正透視", "透視校正", "perspective correction")),
    ("redact", ("移除物件", "移除背景", "remove object", "remove background")),
)

_NUMBER = r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))(?![\w./+-])"
_UNCONSUMED_NUMERIC_ACTION = re.compile(
    rf"(?:又|再|並且|and)?\s*(?:提高|降低|增加|減少|上調|下調|調高|調低|increase|decrease|raise|lower|add|subtract)\s*(?:by\s*)?{_NUMBER}",
    re.IGNORECASE,
)


def compile_adaptive_request(
    *,
    prompt: str,
    deterministic_result: Mapping[str, Any],
    parent_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    original = str(prompt or "").strip()
    text = _normalize(original)
    clauses = _clauses(original)

    _validate_numeric_syntax(text)

    if re.search(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*%", text):
        _raise(
            "adaptive_unsupported_numeric_unit",
            "百分比在倍率與線性參數上語意不同，請改用參數 schema 的絕對數值。",
            reason="percentage_not_supported",
        )

    if (
        _contains(text, ("過曝", "overexposed", "overexposure"))
        and not _contains(text, ("不要過曝", "避免過曝", "別過曝", "avoid overexposure", "without overexposure"))
        and not _contains(text, ("曝光值", "曝光參數", "高光", "亮部", "天空", "sky"))
    ):
        _raise(
            "adaptive_axis_region_ambiguous",
            "單獨的「過曝」可能指曝光、亮度或高光，請明確指定參數。",
            reason="overexposure_axis_ambiguity",
            candidates=["exposure", "brightness", "highlights"],
        )

    unsupported = _detect_unsupported(text)
    if unsupported:
        axis, marker = unsupported
        _raise(
            "adaptive_unsupported_direction",
            f"{AXIS_POLICIES[axis].label}不支援「{marker}」這個反向效果。",
            axis=axis,
            source_clause=_source_clause(clauses, marker),
            reason="unsupported_direction",
        )

    unsupported_action = _detect_unsupported_edit_action(text)
    if unsupported_action:
        action, marker = unsupported_action
        _raise(
            "adaptive_unsupported_operation",
            f"目前的 OpenCV 自適應微調不支援「{marker}」；整句未套用任何調整。",
            source_clause=_source_clause(clauses, marker),
            reason="unsupported_edit_operation",
            operation=action,
        )

    global_reset = _first_unnegated(text, _GLOBAL_RESET_MARKERS)
    satisfied = _first_unnegated(text, _SATISFIED_MARKERS)

    operations: list[dict[str, Any]] = []
    operations.extend(_numeric_operations(text, clauses))
    operations.extend(_reset_operations(text, clauses))
    operations.extend(_directional_operations(text, clauses))
    operations = _resolve_tonal_feedback_shadowing(operations)
    operations = _resolve_guard_operations(operations)
    operations = _dedupe_operations(operations)
    operations = _merge_same_axis_relative_numeric(operations)

    _validate_numeric_or_reset_remainder(text, operations)

    ambiguous_marker = _first_unnegated(text, _AMBIGUOUS_MARKERS)
    if ambiguous_marker:
        _raise(
            "adaptive_clarification_required",
            "描述中仍有未指向明確參數的模糊要求；為避免只套用部分 prompt，請拆開或指定參數。",
            source_clause=_source_clause(clauses, ambiguous_marker),
            reason=(
                "unconsumed_ambiguous_clause"
                if operations
                else "low_confidence_feedback"
            ),
        )

    negated_axes = _negated_action_axes(text, clauses)
    requested_before_fallback = {
        str(operation["axis"]) for operation in operations
    }
    contradictory_axes = negated_axes & requested_before_fallback
    if contradictory_axes:
        axis = sorted(contradictory_axes, key=ADAPTIVE_AXIS_ORDER.index)[0]
        _raise(
            "adaptive_operation_conflict",
            f"同一句同時要求並禁止調整{AXIS_POLICIES[axis].label}，整句未套用。",
            axis=axis,
            reason="negated_requested_axis_conflict",
        )
    if not operations and negated_axes:
        _raise(
            "adaptive_clarification_required",
            "這句只包含不要執行的調整，因此未建立新版本；請說明實際要改哪個參數。",
            reason="negated_action_noop",
            candidates=sorted(negated_axes, key=ADAPTIVE_AXIS_ORDER.index),
        )

    # Deterministic terminal intents must be resolved before the generic
    # unconsumed-action guard and before any LLM/template fallback.  Otherwise
    # valid phrases such as "reset all" are mistaken for an unsupported reset
    # clause, or a hallucinated fallback edit can override "這樣剛好".
    if global_reset:
        if operations:
            _raise(
                "adaptive_operation_conflict",
                "回到原圖必須單獨執行，不能同時再套用其他調整。",
                source_clause=_source_clause(clauses, global_reset),
                reason="global_reset_with_operation",
            )
        return {"kind": "global_reset", "operations": []}

    if satisfied and not operations:
        return {"kind": "satisfied", "operations": []}

    _validate_unconsumed_axis_labels(
        text=text,
        operations=operations,
        negated_axes=negated_axes,
    )

    _validate_unconsumed_action_clauses(
        clauses=clauses,
        operations=operations,
    )

    if not operations:
        operations.extend(
            _fallback_template_operations(
                deterministic_result=deterministic_result,
                text=text,
                clauses=clauses,
            )
        )
        operations = _dedupe_operations(operations)
        if operations:
            _validate_unconsumed_action_clauses(
                clauses=clauses,
                operations=operations,
            )

    if not operations:
        if parent_snapshot is not None and _first_unnegated(
            text, _VAGUE_OPPOSITE_MARKERS
        ):
            active_axes = _active_parent_axes(parent_snapshot)
            if len(active_axes) != 1:
                _raise(
                    "adaptive_clarification_required",
                    "目前有多個可調參數，請指出要退回哪一項。",
                    reason="ambiguous_multi_axis_feedback",
                    candidates=active_axes,
                )
            axis = active_axes[0]
            previous = _parent_axis_state(parent_snapshot, axis)
            direction = -int((previous or {}).get("previous_direction") or 1)
            operations.append(
                _operation(
                    axis=axis,
                    direction=direction,
                    relation="correct",
                    strength="subtle",
                    source_clause=original,
                    source_intent="context_feedback",
                    explicitness="feedback",
                    confidence="medium",
                )
            )
        else:
            if _is_safe_deterministic_preset(deterministic_result):
                return {"kind": "bypass", "operations": []}
            _raise(
                "adaptive_clarification_required",
                "無法安全辨識要調整的參數或方向，請明確指定參數。",
                source_clause=original,
                reason="no_supported_operation",
            )

    if satisfied:
        _raise(
            "adaptive_operation_conflict",
            "「這樣剛好」不能和新的調整放在同一個請求。",
            reason="satisfied_with_operation",
        )

    regions = _detect_regions(text, operations)
    if len(regions) > 1:
        raise AdaptiveCompileError(
            code="adaptive_multi_region_not_supported",
            message="同一句目前只能調整一個區域，請分成兩次操作。",
            issues=tuple(
                {
                    "region": region,
                    "reason": "multiple_regions",
                }
                for region in regions
            ),
        )
    region = regions[0] if regions else _inherited_region(parent_snapshot)
    region_source = "explicit" if regions else "inherited" if region != "all" else "default"
    mask_type = default_mask_type_for_region(region)

    _validate_axis_region_ambiguity(text, operations, region)
    _validate_cross_axis_guards(text, operations)
    _validate_operation_conflicts(operations)

    suppressed_companion_axes = sorted(
        negated_axes - {str(operation["axis"]) for operation in operations},
        key=ADAPTIVE_AXIS_ORDER.index,
    )

    if len(operations) > MAX_PRIMARY_OPERATIONS:
        raise AdaptiveCompileError(
            code="adaptive_operation_limit_exceeded",
            message=f"單次最多支援 {MAX_PRIMARY_OPERATIONS} 個明確參數，請拆成兩次調整。",
            issues=tuple(
                {
                    "axis": operation["axis"],
                    "source_clause": operation["source_clause"],
                    "reason": "operation_limit",
                }
                for operation in operations
            ),
        )

    operations.sort(key=lambda item: ADAPTIVE_AXIS_ORDER.index(str(item["axis"])))
    for operation in operations:
        operation["region"] = region
        operation["mask_type"] = mask_type
        operation["group_id"] = _stable_id(
            "group",
            str(operation["source_intent"]),
            str(operation["axis"]),
            region,
            mask_type,
        )
        operation["suppressed_companion_axes"] = list(suppressed_companion_axes)
        operation["operation_id"] = _stable_id(
            "operation",
            str(operation["axis"]),
            str(operation["relation"]),
            str(operation["direction"]),
            str(operation.get("numeric_value")),
            str(operation.get("relative_delta")),
            str(operation["source_intent"]),
            region,
            mask_type,
        )

    return {
        "kind": "adaptive",
        "operations": operations,
        "region": region,
        "mask_type": mask_type,
        "region_source": region_source,
    }


def _numeric_operations(text: str, clauses: list[str]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for axis in ADAPTIVE_AXIS_ORDER:
        label = _label_pattern(axis)
        absolute_patterns = (
            re.compile(
                rf"(?:{label})(?:\s*(?:參數|數值|值|強度))?\s*"
                rf"(?:設定?(?:成|為)?|設為|設成|調(?:整)?(?:成|為|到)|調到|提高到|降低到|to|=|[:：])\s*{_NUMBER}",
                re.IGNORECASE,
            ),
            re.compile(
                rf"(?:提高|降低|increase|decrease|raise|lower)\s*(?:{label})\s*(?:到|to)\s*{_NUMBER}",
                re.IGNORECASE,
            ),
        )
        relative_patterns = (
            (1, re.compile(rf"(?:{label}).{{0,5}}?(?:提高|增加|上調|調高)\s*(?:by\s*)?{_NUMBER}", re.IGNORECASE)),
            (-1, re.compile(rf"(?:{label}).{{0,5}}?(?:降低|減少|下調|調低)\s*(?:by\s*)?{_NUMBER}", re.IGNORECASE)),
            (1, re.compile(rf"(?:increase|raise|add)\s+(?:{label})\s*(?:by\s*)?{_NUMBER}", re.IGNORECASE)),
            (-1, re.compile(rf"(?:decrease|lower|reduce|subtract)\s+(?:{label})\s*(?:by\s*)?{_NUMBER}", re.IGNORECASE)),
            (1, re.compile(rf"(?:提高|增加|上調|調高)(?:把)?\s*(?:{label})\s*{_NUMBER}", re.IGNORECASE)),
            (-1, re.compile(rf"(?:降低|減少|下調|調低)(?:把)?\s*(?:{label})\s*{_NUMBER}", re.IGNORECASE)),
        )
        found: list[dict[str, Any]] = []
        for pattern in absolute_patterns:
            for match in pattern.finditer(text):
                operation = _operation(
                        axis=axis,
                        direction=0,
                        relation="absolute",
                        strength="subtle",
                        source_clause=_source_clause(clauses, match.group(0)),
                        source_intent="explicit_numeric",
                        explicitness="explicit_axis",
                        confidence="high",
                        numeric_value=float(match.group(1)),
                    )
                operation["consumed_texts"] = [match.group(0)]
                found.append(operation)
        for sign, pattern in relative_patterns:
            for match in pattern.finditer(text):
                numeric = float(match.group(1))
                if numeric < 0:
                    _raise(
                        "adaptive_invalid_numeric",
                        f"{AXIS_POLICIES[axis].label}的方向動詞不能和負號數值混用，請改成單一明確方向。",
                        axis=axis,
                        source_clause=_source_clause(clauses, match.group(0)),
                        reason="signed_relative_direction_conflict",
                    )
                operation = _operation(
                        axis=axis,
                        direction=sign,
                        relation="relative_numeric",
                        strength="subtle",
                        source_clause=_source_clause(clauses, match.group(0)),
                        source_intent="explicit_relative_numeric",
                        explicitness="explicit_axis",
                        confidence="high",
                        relative_delta=sign * numeric,
                    )
                operation["consumed_texts"] = [match.group(0)]
                found.append(operation)
        operations.extend(found)
    return operations


def _reset_operations(text: str, clauses: list[str]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    for axis in ADAPTIVE_AXIS_ORDER:
        label = _label_pattern(axis)
        patterns = (
            re.compile(rf"(?:{label})(?:\s*(?:參數|值|強度))?\s*(?:重設|歸零|恢復預設|reset)", re.IGNORECASE),
            re.compile(rf"reset\s+(?:{label})", re.IGNORECASE),
        )
        if axis == "vignette":
            patterns = (*patterns, re.compile(r"(?:去除暗角|remove vignette)", re.IGNORECASE))
        match = next((match for pattern in patterns if (match := pattern.search(text))), None)
        if match is None or _is_negated(text, match.start()):
            continue
        operation = _operation(
                axis=axis,
                direction=0,
                relation="reset",
                strength="subtle",
                source_clause=_source_clause(clauses, match.group(0)),
                source_intent="axis_reset",
                explicitness="explicit_axis",
                confidence="high",
            )
        operation["consumed_texts"] = [match.group(0)]
        operations.append(operation)
    return operations


def _directional_operations(text: str, clauses: list[str]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    numeric_axes = {item["axis"] for item in _numeric_operations(text, clauses)}
    reset_axes = {item["axis"] for item in _reset_operations(text, clauses)}
    excluded = numeric_axes | reset_axes

    for axis, intent, direction, markers in _GROUP_FEEDBACK_PATTERNS:
        if axis in excluded:
            continue
        marker = _first_unnegated(text, markers)
        if marker:
            operations.append(
                _operation(
                    axis=axis,
                    direction=direction,
                    relation="correct",
                    strength="subtle",
                    source_clause=_source_clause(clauses, marker),
                    source_intent=intent,
                    explicitness="feedback",
                    confidence="high",
                    include_companions=True,
                    group_feedback=True,
                )
            )
            operations[-1]["source_marker"] = marker

    for axis in ADAPTIVE_AXIS_ORDER:
        if axis in excluded:
            continue
        positive, negative = _EXPLICIT_DIRECTION_PATTERNS[axis]
        for direction, markers in ((1, positive), (-1, negative)):
            mentions = _explicit_direction_mentions(
                axis=axis,
                direction=direction,
                text=text,
                clauses=clauses,
                markers=markers,
            )
            if not mentions:
                continue
            source_clauses = sorted(
                {str(item["source_clause"]) for item in mentions},
                key=_normalize,
            )
            strength = max(
                (str(item["strength"]) for item in mentions),
                key=lambda item: {"subtle": 0, "normal": 1, "strong": 2}[item],
            )
            operation = _operation(
                axis=axis,
                direction=direction,
                relation="initial",
                strength=strength,
                source_clause="、".join(source_clauses),
                source_intent=(
                    AXIS_POLICIES[axis].positive_intent
                    if direction > 0
                    else AXIS_POLICIES[axis].negative_intent
                ),
                explicitness="explicit_axis",
                confidence="high",
            )
            operation["source_markers"] = sorted(
                {str(item["marker"]) for item in mentions},
                key=_normalize,
            )
            if len(mentions) > 1:
                operation["merged_clause_count"] = len(mentions)
                operation["source_clauses"] = source_clauses
            operations.append(operation)

    explicit_axes = {str(item["axis"]) for item in operations}
    for axis, (positive, negative, positive_intent, negative_intent) in _MACRO_DIRECTION_PATTERNS.items():
        if axis in excluded or axis in explicit_axes:
            continue
        for direction, markers, intent in (
            (1, positive, positive_intent),
            (-1, negative, negative_intent),
        ):
            marker = _first_unnegated(text, markers)
            if marker:
                source_clause = _source_clause(clauses, marker)
                operations.append(
                    _operation(
                        axis=axis,
                        direction=direction,
                        relation="initial",
                        strength=_strength(source_clause, marker),
                        source_clause=source_clause,
                        source_intent=intent,
                        explicitness="macro_primary",
                        confidence="high",
                        include_companions=True,
                    )
                )
                operations[-1]["source_marker"] = marker

    present_axes = {str(item["axis"]) for item in operations}
    for axis, (positive, negative, positive_intent, negative_intent) in _STRONG_DESCRIPTION_PATTERNS.items():
        if axis in excluded or axis in present_axes:
            continue
        for direction, markers, intent in (
            (1, positive, positive_intent),
            (-1, negative, negative_intent),
        ):
            marker = _first_unnegated(text, markers)
            if marker:
                marker_start = text.find(marker)
                marker_end = marker_start + len(marker)
                if (
                    text[marker_end:marker_end + 1] == "了"
                    or text[max(0, marker_start - 3):marker_start].endswith("已經")
                ):
                    continue
                operations.append(
                    _operation(
                        axis=axis,
                        direction=direction,
                        relation="initial",
                        strength="strong",
                        source_clause=_source_clause(clauses, marker),
                        source_intent=intent,
                        explicitness="macro_primary",
                        confidence="medium",
                        include_companions=True,
                    )
                )
                operations[-1]["source_marker"] = marker
                break

    present_axes = {str(item["axis"]) for item in operations}
    for axis, (positive_feedback, negative_feedback) in _AXIS_FEEDBACK.items():
        if axis in excluded:
            continue
        for direction, markers in ((1, positive_feedback), (-1, negative_feedback)):
            marker = _first_unnegated(text, markers)
            if not marker:
                continue
            # A concrete action for the same axis wins over an observational
            # phrase when both point in the same direction.  Opposite direction
            # is retained so the conflict validator can reject it.
            source_intent = AXIS_POLICIES[axis].positive_intent if direction > 0 else AXIS_POLICIES[axis].negative_intent
            operation = _operation(
                    axis=axis,
                    direction=direction,
                    relation="correct",
                    strength="subtle",
                    source_clause=_source_clause(clauses, marker),
                    source_intent=source_intent,
                    explicitness="feedback",
                    confidence="high" if axis in present_axes else "medium",
                )
            operation["source_marker"] = marker
            operations.append(operation)
    return operations


def _fallback_template_operations(
    *,
    deterministic_result: Mapping[str, Any],
    text: str,
    clauses: list[str],
) -> list[dict[str, Any]]:
    plan = deterministic_result.get("edit_plan")
    if not isinstance(plan, Mapping) or plan.get("type") != "edits":
        return []
    edits = plan.get("edits")
    if not isinstance(edits, list):
        return []
    mentioned_axes = _mentioned_axes(text)
    operations: list[dict[str, Any]] = []
    for edit in edits:
        if not isinstance(edit, Mapping):
            continue
        intent = str(edit.get("intent") or "")
        if intent == "soft":
            axis, direction = "contrast", -1
        else:
            mapping = INTENT_TO_AXIS_DIRECTION.get(intent)
            if mapping is None:
                continue
            axis, direction = mapping
        if mentioned_axes and axis not in mentioned_axes:
            _raise(
                "adaptive_clarification_required",
                "文字明確提到的參數與 fallback 推定軸不一致，為避免錯軸已取消整句。",
                axes=sorted(mentioned_axes, key=ADAPTIVE_AXIS_ORDER.index),
                fallback_axis=axis,
                reason="fallback_axis_mismatch",
            )
        operations.append(
            _operation(
                axis=axis,
                direction=direction,
                relation="initial",
                strength=str(edit.get("strength") or _strength(text, intent)),
                source_clause=_source_clause(clauses, intent) or " ".join(clauses),
                source_intent=intent,
                explicitness="macro_primary",
                confidence="medium",
                include_companions=True,
            )
        )
    return operations


def _is_safe_deterministic_preset(
    deterministic_result: Mapping[str, Any],
) -> bool:
    if str(deterministic_result.get("resolved_intent") or "") != "apply_preset":
        return False
    plan = deterministic_result.get("edit_plan")
    if not isinstance(plan, Mapping) or plan.get("type") != "preset":
        return False
    result_name = normalize_preset_name(
        str(deterministic_result.get("preset_name") or "")
    )
    plan_name = normalize_preset_name(str(plan.get("preset_name") or ""))
    return bool(result_name and result_name == plan_name)


def _validate_operation_conflicts(operations: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        grouped.setdefault(str(operation["axis"]), []).append(operation)
    for axis, items in grouped.items():
        if len(items) <= 1:
            continue
        relations = {str(item["relation"]) for item in items}
        directions = {int(item["direction"]) for item in items if int(item["direction"]) != 0}
        absolute_values = {
            float(item["numeric_value"])
            for item in items
            if item.get("numeric_value") is not None
        }
        if len(absolute_values) > 1 or len(directions) > 1 or len(relations) > 1:
            raise AdaptiveCompileError(
                code="adaptive_operation_conflict",
                message=f"{AXIS_POLICIES[axis].label}在同一句中有互相衝突的要求。",
                issues=tuple(
                    {
                        "axis": axis,
                        "source_clause": item["source_clause"],
                        "reason": "conflicting_operations",
                    }
                    for item in items
                ),
            )


def _resolve_guard_operations(
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    guard_tokens = (
        "不要太",
        "不要那麼",
        "別太",
        "別那麼",
        "not too",
        "not so",
    )
    guards = [
        operation
        for operation in operations
        if operation.get("explicitness") == "feedback"
        and _contains(_normalize(str(operation.get("source_clause") or "")), guard_tokens)
    ]
    if not guards:
        return operations
    remaining = [operation for operation in operations if operation not in guards]
    if not remaining:
        return operations
    remaining_axes = {str(operation["axis"]) for operation in remaining}
    for guard in guards:
        guard_axis = str(guard["axis"])
        if remaining_axes != {guard_axis}:
            _raise(
                "adaptive_clarification_required",
                f"目前無法把「{guard.get('source_clause')}」當成另一參數的硬限制；請改成明確操作。",
                axis=guard_axis,
                source_clause=guard.get("source_clause"),
                reason="cross_axis_guard",
            )
    return remaining


def _resolve_tonal_feedback_shadowing(
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tonal_feedback = [
        operation
        for operation in operations
        if operation.get("explicitness") == "feedback"
        and operation.get("axis") in {"highlights", "shadows"}
    ]
    resolved: list[dict[str, Any]] = []
    for operation in operations:
        if not (
            operation.get("axis") == "brightness"
            and operation.get("explicitness") == "feedback"
        ):
            resolved.append(operation)
            continue
        brightness_clause = _normalize(
            str(operation.get("source_clause") or "")
        )
        brightness_marker = _normalize(
            str(operation.get("source_marker") or "")
        )
        overlaps_tonal_marker = any(
            brightness_clause
            == _normalize(str(tonal.get("source_clause") or ""))
            and bool(brightness_marker)
            and brightness_marker
            in _normalize(str(tonal.get("source_marker") or ""))
            for tonal in tonal_feedback
        )
        if not overlaps_tonal_marker:
            resolved.append(operation)

    # Broad fallback vocabulary (for example "太淡" or "清晰") must not
    # steal a clause that explicitly names a narrower parameter.  This keeps
    # "暗角太淡" on vignette instead of saturation and keeps a named
    # highlights/shadows request from also becoming a brightness edit.
    contextual_targets: dict[str, tuple[str, ...]] = {
        "brightness": ("highlights", "shadows", "vignette"),
        "saturation": ("vignette",),
        "sharpen": ("clarity",),
        "contrast": ("clarity",),
    }
    filtered: list[dict[str, Any]] = []
    for operation in resolved:
        axis = str(operation.get("axis") or "")
        if operation.get("explicitness") == "explicit_axis":
            filtered.append(operation)
            continue
        source_clause = _normalize(str(operation.get("source_clause") or ""))
        shadowed = False
        for target_axis in contextual_targets.get(axis, ()):
            if (
                axis == "brightness"
                and target_axis in {"highlights", "shadows"}
                and _contains(
                    source_clause,
                    (
                        "整體太亮",
                        "整體太暗",
                        "全圖太亮",
                        "全圖太暗",
                        "整張太亮",
                        "整張太暗",
                        "whole image",
                        "entire image",
                    ),
                )
            ):
                continue
            if (
                axis == "contrast"
                and target_axis == "clarity"
                and "局部對比" not in source_clause
            ):
                continue
            for target in resolved:
                if str(target.get("axis") or "") != target_axis:
                    continue
                target_clause = _normalize(
                    str(target.get("source_clause") or "")
                )
                if target_clause != source_clause:
                    continue
                if not any(
                    label in source_clause
                    for label in _AXIS_LABELS[target_axis]
                ):
                    continue
                local_contrast_clarity = (
                    axis == "contrast"
                    and target_axis == "clarity"
                    and "局部對比" in source_clause
                )
                if (
                    not local_contrast_clarity
                    and any(
                        label in source_clause
                        for label in _AXIS_LABELS[axis]
                    )
                ):
                    continue
                shadowed = True
                break
            if shadowed:
                break
        if not shadowed:
            filtered.append(operation)
    return filtered


def _merge_same_axis_relative_numeric(
    operations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for operation in operations:
        if operation.get("relation") == "relative_numeric":
            grouped.setdefault(str(operation["axis"]), []).append(operation)
        else:
            passthrough.append(operation)

    for axis, items in grouped.items():
        if len(items) == 1:
            passthrough.append(items[0])
            continue
        directions = {int(item.get("direction") or 0) for item in items}
        if len(directions) != 1:
            passthrough.extend(items)
            continue
        delta = sum(float(item.get("relative_delta") or 0.0) for item in items)
        direction = next(iter(directions))
        clauses = sorted(
            {str(item.get("source_clause") or "").strip() for item in items},
            key=_normalize,
        )
        merged = _operation(
            axis=axis,
            direction=direction,
            relation="relative_numeric",
            strength="subtle",
            source_clause="、".join(clause for clause in clauses if clause),
            source_intent="explicit_relative_numeric",
            explicitness="explicit_axis",
            confidence="high",
            relative_delta=delta,
        )
        merged["merged_clause_count"] = len(items)
        merged["source_clauses"] = clauses
        merged["consumed_texts"] = sorted(
            {
                str(consumed)
                for item in items
                for consumed in item.get("consumed_texts") or []
            },
            key=_normalize,
        )
        passthrough.append(merged)
    return passthrough


def _validate_numeric_or_reset_remainder(
    text: str,
    operations: list[dict[str, Any]],
) -> None:
    guarded_axes = {
        str(operation["axis"])
        for operation in operations
        if operation["relation"] in {"absolute", "relative_numeric", "reset"}
    }
    for axis in guarded_axes:
        marker_groups: list[tuple[str, ...]] = []
        macro = _MACRO_DIRECTION_PATTERNS.get(axis)
        if macro is not None:
            marker_groups.append((*macro[0], *macro[1]))
        explicit = _EXPLICIT_DIRECTION_PATTERNS.get(axis)
        if explicit is not None:
            marker_groups.append((*explicit[0], *explicit[1]))
        directional_remainder = text
        for operation in operations:
            if operation.get("axis") != axis:
                continue
            for consumed in operation.get("consumed_texts") or []:
                directional_remainder = directional_remainder.replace(
                    _normalize(str(consumed)), " ", 1
                )
        for markers in marker_groups:
            for marker, start in _unnegated_occurrences(
                directional_remainder, markers
            ):
                source_clause = _source_clause_at(
                    _clauses(directional_remainder),
                    directional_remainder,
                    start,
                )
                _raise(
                    "adaptive_operation_conflict",
                    f"{AXIS_POLICIES[axis].label}的數值／重設與方向要求不能混在同一句。",
                    axis=axis,
                    source_clause=source_clause,
                    reason="numeric_or_reset_with_direction",
                )
    if guarded_axes and _UNCONSUMED_NUMERIC_ACTION.search(text):
        # Labelled relative expressions are already consumed.  Remove them and
        # only reject a remaining unlabeled action such as "又提高 2".
        remainder = text
        for axis in guarded_axes:
            label = _label_pattern(axis)
            remainder = re.sub(
                rf"(?:{label}).{{0,5}}?(?:提高|降低|增加|減少|上調|下調|調高|調低)\s*(?:by\s*)?{_NUMBER}",
                " ",
                remainder,
                flags=re.IGNORECASE,
            )
            remainder = re.sub(
                rf"(?:increase|decrease|raise|lower|add|subtract)\s+(?:{label})\s*(?:by\s*)?{_NUMBER}",
                " ",
                remainder,
                flags=re.IGNORECASE,
            )
        if _UNCONSUMED_NUMERIC_ACTION.search(remainder):
            _raise(
                "adaptive_operation_conflict",
                "仍有未指明參數的數值調整，請為每個數字標示參數名稱。",
                reason="unconsumed_numeric_action",
            )


def _validate_unconsumed_action_clauses(
    *,
    clauses: list[str],
    operations: list[dict[str, Any]],
) -> None:
    consumed: set[str] = set()
    for operation in operations:
        consumed.add(_normalize(str(operation.get("source_clause") or "")))
        source_clauses = operation.get("source_clauses")
        if isinstance(source_clauses, list):
            consumed.update(_normalize(str(item)) for item in source_clauses)

    action_pattern = re.compile(
        r"(?:提高|降低|增加|減少|上調|下調|調高|調低|調整|設定|設為|設成|重設|歸零|移除|去除|校正|翻轉|縮放|裁切|裁剪|旋轉|降噪|去噪|讓|使|變成|變得|弄成|做成|increase|decrease|raise|lower|reduce|adjust|set|reset|remove|correct|flip|resize|crop|rotate|denoise|make)",
        flags=re.IGNORECASE,
    )
    for clause in clauses:
        normalized_clause = _normalize(clause)
        if not normalized_clause:
            continue
        if normalized_clause in consumed:
            if _has_unconsumed_generative_residue(
                normalized_clause,
                operations,
            ):
                _raise(
                    "adaptive_unsupported_operation",
                    f"無法安全執行未完整解析的子句「{clause}」；整句未套用任何調整。",
                    source_clause=clause,
                    reason="unconsumed_semantic_clause",
                )
            continue
        if _negated_action_axes(normalized_clause, [clause]):
            continue
        if _is_harmless_unconsumed_clause(
            normalized_clause,
            operations,
        ):
            continue
        action_matches = list(action_pattern.finditer(normalized_clause))
        if action_matches and all(
            _span_is_negated(
                normalized_clause, match.start(), match.end()
            )
            for match in action_matches
        ):
            continue
        if action_matches:
            _raise(
                "adaptive_unsupported_operation",
                f"無法安全執行未完整解析的子句「{clause}」；整句未套用任何調整。",
                source_clause=clause,
                reason="unconsumed_action_clause",
            )
        if operations:
            _raise(
                "adaptive_unsupported_operation",
                f"無法安全執行未完整解析的子句「{clause}」；整句未套用任何調整。",
                source_clause=clause,
                reason="unconsumed_semantic_clause",
            )


def _has_unconsumed_generative_residue(
    clause: str,
    operations: list[dict[str, Any]],
) -> bool:
    relevant: list[dict[str, Any]] = []
    for operation in operations:
        source_clauses = {
            _normalize(str(operation.get("source_clause") or "")),
            *(
                _normalize(str(item))
                for item in operation.get("source_clauses") or []
            ),
        }
        if clause in source_clauses:
            relevant.append(operation)

    remainder = clause
    consumed_markers: set[str] = set()
    for operation in relevant:
        consumed_markers.update(
            _normalize(str(item))
            for item in (
                operation.get("source_marker"),
                *(operation.get("source_markers") or []),
                *(operation.get("consumed_texts") or []),
            )
            if str(item or "").strip()
        )
    for marker in sorted(consumed_markers, key=len, reverse=True):
        remainder = remainder.replace(marker, " ", 1)

    if not re.search(
        r"(?:讓|使|變成|變得|弄成|做成|\bmake\b)",
        remainder,
        flags=re.IGNORECASE,
    ):
        return False

    compact = re.sub(r"[\s!?！？。,.，、；;]+", "", remainder)
    wrapper_pattern = re.compile(
        r"(?:請|麻煩|拜託|幫我|可以|能不能|能否|稍微|再|有點|一點|一些|一下|"
        r"讓|使|把|將|人|照片|畫面|圖片|影像|整體|感覺|看起來|顯得|呈現|"
        r"變成|變得|弄成|做成|更|比較|"
        r"please|make|it|the|photo|image|picture|overall|look|feel|"
        r"slightly|alittle)+",
        flags=re.IGNORECASE,
    )
    return bool(wrapper_pattern.sub("", compact))


def _is_harmless_unconsumed_clause(
    clause: str,
    operations: list[dict[str, Any]],
) -> bool:
    if any(
        clause == _normalize(marker)
        for marker in (*_VAGUE_OPPOSITE_MARKERS, "降一點")
    ):
        return True
    if _contains(
        clause,
        (
            "不要過曝",
            "避免過曝",
            "別過曝",
            "without overexposure",
            "avoid overexposure",
        ),
    ):
        return True
    requested_axes = {
        str(operation.get("axis") or "")
        for operation in operations
    }
    for axis in requested_axes:
        feedback = _AXIS_FEEDBACK.get(axis)
        strong = _STRONG_DESCRIPTION_PATTERNS.get(axis)
        markers: tuple[str, ...] = ()
        if feedback is not None:
            markers += (*feedback[0], *feedback[1])
        if strong is not None:
            markers += (*strong[0], *strong[1])
        if _contains(clause, markers):
            return True
    negated_terminal = _contains(
        clause,
        (
            *_SATISFIED_MARKERS,
            *_GLOBAL_RESET_MARKERS,
            "重設",
            "歸零",
            "reset",
        ),
    ) and any(
        clause.startswith(_normalize(prefix))
        for prefix in _NEGATION_PREFIXES
    )
    if negated_terminal:
        return True
    compact = re.sub(r"[\s!?！？。,.，、；;]+", "", clause)
    return bool(
        re.fullmatch(
            r"(?:請|麻煩|拜託|幫我|可以嗎|好嗎|謝謝|感謝|一下|吧|啦|喔|哦|就好|即可|please|thanks|thankyou)+",
            compact,
            flags=re.IGNORECASE,
        )
    )


def _validate_unconsumed_axis_labels(
    *,
    text: str,
    operations: list[dict[str, Any]],
    negated_axes: set[str],
) -> None:
    mentioned_axes = _mentioned_axes(text)
    if len(mentioned_axes) < 2 or not operations:
        return
    requested_axes = {str(operation["axis"]) for operation in operations}
    unconsumed = mentioned_axes - requested_axes - negated_axes
    if unconsumed:
        _raise(
            "adaptive_clarification_required",
            "同一句提到的部分參數沒有可安全歸因的方向；整句未套用任何調整。",
            axes=sorted(unconsumed, key=ADAPTIVE_AXIS_ORDER.index),
            reason="unconsumed_axis_labels",
        )


def _validate_axis_region_ambiguity(
    text: str,
    operations: list[dict[str, Any]],
    region: str,
) -> None:
    axes = {str(operation["axis"]) for operation in operations}
    ambiguous_highlights = (
        _contains(text, ("亮部", "高光", "highlights"))
        and "brightness" in axes
        and "highlights" not in axes
        and region == "highlights"
        and not _contains(text, ("亮部區域", "高光區域", "in highlights", "highlight region", "亮部的亮度", "高光的亮度"))
    )
    ambiguous_shadows = (
        _contains(text, ("暗部", "陰影", "shadows"))
        and "brightness" in axes
        and "shadows" not in axes
        and region == "shadows"
        and not _contains(
            text,
            (
                "暗部區域",
                "陰影區域",
                "in shadows",
                "shadow region",
                "dark shadows",
                "暗部的亮度",
                "陰影的亮度",
            ),
        )
    )
    if ambiguous_highlights or ambiguous_shadows:
        token = "亮部／高光" if ambiguous_highlights else "暗部／陰影"
        _raise(
            "adaptive_axis_region_ambiguous",
            f"「{token}亮一點」可能是調整參數或局部區域，請明確說參數或區域。",
            reason="axis_region_ambiguity",
            candidates=["parameter_axis", "masked_region"],
        )


def _validate_cross_axis_guards(text: str, operations: list[dict[str, Any]]) -> None:
    requested = {str(operation["axis"]) for operation in operations}
    for axis, (_, negative_feedback) in _AXIS_FEEDBACK.items():
        for marker in negative_feedback:
            start = text.find(marker)
            while start >= 0:
                if _is_negated(text, start) and axis not in requested:
                    # Overexposure is an established brightness guard and does
                    # not create an implicit highlights operation.
                    if marker in {"曝光太高"} or _contains(text, ("不要過曝", "避免過曝", "別過曝", "without overexposure", "avoid overexposure")):
                        break
                    _raise(
                        "adaptive_clarification_required",
                        f"目前無法同時保證「不要太{AXIS_POLICIES[axis].label}」；請把它寫成明確參數操作。",
                        axis=axis,
                        source_clause=_source_clause(_clauses(text), marker),
                        reason="cross_axis_guard",
                    )
                start = text.find(marker, start + 1)


def _detect_regions(text: str, operations: list[dict[str, Any]]) -> list[str]:
    regions: list[str] = []
    axes = {str(operation["axis"]) for operation in operations}
    for region, markers in _REGION_MARKERS:
        if _contains(text, markers):
            if (
                region == "edges"
                and (
                    (
                        "sharpen" in axes
                        and _contains(
                            text,
                            ("銳化後邊緣", "銳化邊緣"),
                        )
                    )
                    or (
                        "vignette" in axes
                        and "暗角邊緣" in text
                    )
                )
                and not _contains(
                    text,
                    ("邊緣區域", "edge region", "in the edges"),
                )
            ):
                continue
            regions.append(region)

    highlight_token = _contains(text, ("亮部", "高光", "highlight", "highlights"))
    shadow_token = _contains(text, ("暗部", "陰影", "shadow", "shadows"))
    if highlight_token and (
        "highlights" not in axes
        or _contains(text, ("亮部區域", "高光區域", "highlight region", "in highlights"))
    ):
        regions.append("highlights")
    if shadow_token and (
        "shadows" not in axes
        or _contains(text, ("暗部區域", "陰影區域", "shadow region", "in shadows"))
    ):
        regions.append("shadows")

    # Plain "人" is a region only in a compact action frame, not in phrases
    # such as "讓人感覺照片亮一點".
    if re.search(r"(?:把|將|照片中的|畫面中的|圖中的|裡的|^|[，,。；;\s])人(?=.{0,3}(?:再|更|亮|暗|暖|冷|鮮|自然|調|變))", text):
        regions.append("person")
    return list(dict.fromkeys(regions))


def _dedupe_operations(operations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for operation in operations:
        relation = str(operation["relation"])
        relation_key = (
            relation
            if relation in {"absolute", "relative_numeric", "reset"}
            else "directional"
        )
        key = (
            operation["axis"],
            relation_key,
            operation["direction"],
            operation.get("numeric_value"),
            operation.get("relative_delta"),
        )
        existing = unique.get(key)
        if existing is None:
            unique[key] = copy.deepcopy(operation)
            continue
        if _explicitness_rank(operation["explicitness"]) < _explicitness_rank(
            existing["explicitness"]
        ):
            winner, other = operation, existing
        else:
            winner, other = existing, operation
        merged = copy.deepcopy(winner)
        source_clauses = {
            str(item).strip()
            for item in (
                winner.get("source_clause"),
                other.get("source_clause"),
                *(winner.get("source_clauses") or []),
                *(other.get("source_clauses") or []),
            )
            if str(item or "").strip()
        }
        if len(source_clauses) > 1:
            merged["source_clauses"] = sorted(
                source_clauses,
                key=_normalize,
            )
            merged["merged_clause_count"] = len(source_clauses)
        source_markers = {
            str(item).strip()
            for item in (
                winner.get("source_marker"),
                other.get("source_marker"),
                *(winner.get("source_markers") or []),
                *(other.get("source_markers") or []),
            )
            if str(item or "").strip()
        }
        if source_markers:
            merged["source_markers"] = sorted(
                source_markers,
                key=_normalize,
            )
        unique[key] = merged
    return list(unique.values())


def _operation(
    *,
    axis: str,
    direction: int,
    relation: str,
    strength: str,
    source_clause: str,
    source_intent: str,
    explicitness: str,
    confidence: str,
    numeric_value: float | None = None,
    relative_delta: float | None = None,
    include_companions: bool = False,
    group_feedback: bool = False,
) -> dict[str, Any]:
    return {
        "operation_id": None,
        "group_id": None,
        "source_clause": source_clause.strip(),
        "source_intent": source_intent,
        "axis": axis,
        "direction": int(direction),
        "region": None,
        "mask_type": None,
        "relation": relation,
        "strength_hint": strength if strength in {"subtle", "normal", "strong"} else "normal",
        "confidence": confidence,
        "explicitness": explicitness,
        "role": "primary",
        "numeric_value": numeric_value,
        "relative_delta": relative_delta,
        "include_companions": bool(include_companions),
        "group_feedback": bool(group_feedback),
    }


def _detect_unsupported(text: str) -> tuple[str, str] | None:
    for axis, markers in _UNSUPPORTED_PATTERNS:
        marker = _first_unnegated(text, markers)
        if marker:
            return axis, marker
    return None


def _detect_unsupported_edit_action(text: str) -> tuple[str, str] | None:
    for action, markers in _UNSUPPORTED_EDIT_ACTIONS:
        marker = _first_unnegated(text, markers)
        if marker:
            return action, marker
    return None


def _validate_numeric_syntax(text: str) -> None:
    malformed = (
        re.search(r"(?:\d+(?:\.\d+)?|\.\d+)[eE][+-]?\d+", text),
        re.search(r"\d+(?:\.\d+){2,}", text),
        re.search(r"(?:\d+(?:\.\d+)?|\.\d+)(?:[a-z_]+)", text),
        re.search(r"\d{1,3}(?:,\d{3})+", text),
        re.search(r"\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?", text),
        re.search(r"\d+(?:\.\d+)?\s*[+-]\s*\d+(?:\.\d+)?", text),
        re.search(r"(?:=|:|：|設為|設成|調到|to)\s*[+-]{2,}\d", text),
    )
    match = next((item for item in malformed if item is not None), None)
    if match is not None:
        _raise(
            "adaptive_invalid_numeric",
            f"無法安全解析數值「{match.group(0)}」；請使用一般十進位數字。",
            source_clause=_source_clause(_clauses(text), match.group(0)),
            reason="malformed_numeric_token",
        )
    word_number = re.search(
        r"(?:零|一(?!點|些|下)|二|三|四|五|六|七|八|九|十|百|千|one\b|two\b|three\b|four\b|five\b|six\b|seven\b|eight\b|nine\b|ten\b)",
        text,
        flags=re.IGNORECASE,
    )
    for axis, labels in _AXIS_LABELS.items():
        label = "|".join(re.escape(item) for item in labels)
        match = re.search(
            rf"(?:{label}).{{0,12}}?(?:=|:|：|設為|設成|調到|to)\s*(?:nan|inf|infinity)\b",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            _raise(
                "adaptive_invalid_numeric",
                f"{AXIS_POLICIES[axis].label}必須是有限十進位數字。",
                axis=axis,
                source_clause=_source_clause(_clauses(text), match.group(0)),
                reason="non_finite_numeric_token",
            )
        assignment = re.search(
            rf"(?:{label}).{{0,8}}?(?:=|:|：|設定(?:成|為)?|設為|設成|調整(?:成|為|到)?|調(?:成|為|到)|\bto\b)\s*([^、，,；;。\s]*)",
            text,
            flags=re.IGNORECASE,
        )
        if assignment:
            token = str(assignment.group(1) or "")
            if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", token):
                _raise(
                    "adaptive_invalid_numeric",
                    f"{AXIS_POLICIES[axis].label}的指定值「{token or '(空白)'}」不是完整十進位數字。",
                    axis=axis,
                    source_clause=_source_clause(_clauses(text), assignment.group(0)),
                    reason="invalid_assignment_value",
                )
        if word_number and re.search(
            rf"(?:(?:{label}).{{0,10}}?(?:提高|降低|增加|減少|increase|decrease|raise|lower)|(?:提高|降低|增加|減少|increase|decrease|raise|lower).{{0,10}}?(?:{label})).{{0,5}}?{re.escape(word_number.group(0))}",
            text,
            flags=re.IGNORECASE,
        ):
            _raise(
                "adaptive_invalid_numeric",
                f"{AXIS_POLICIES[axis].label}的文字數值無法安全換算，請使用十進位數字。",
                axis=axis,
                source_clause=_source_clause(_clauses(text), word_number.group(0)),
                reason="word_numeric_not_supported",
            )


def _negated_action_axes(text: str, clauses: list[str]) -> set[str]:
    axes: set[str] = set()
    for axis in ADAPTIVE_AXIS_ORDER:
        explicit = _EXPLICIT_DIRECTION_PATTERNS[axis]
        marker_groups: list[tuple[str, ...]] = [explicit[0], explicit[1]]
        macro = _MACRO_DIRECTION_PATTERNS.get(axis)
        if macro is not None:
            marker_groups.extend((macro[0], macro[1]))
        for markers in marker_groups:
            for marker in markers:
                start = text.find(marker)
                while start >= 0:
                    if _is_negated(text, start):
                        axes.add(axis)
                    start = text.find(marker, start + 1)

        label = _label_pattern(axis)
        verb = (
            r"(?:提高|降低|增加|減少|上調|下調|調高|調低|raise|increase|decrease|lower|reduce|more|less)"
        )
        for clause in clauses:
            normalized_clause = _normalize(clause)
            for match in re.finditer(
                rf"(?:(?:{label}).{{0,8}}?{verb}|{verb}.{{0,8}}?(?:{label}))",
                normalized_clause,
                flags=re.IGNORECASE,
            ):
                if _span_is_negated(
                    normalized_clause, match.start(), match.end()
                ):
                    axes.add(axis)
    return axes


def _active_parent_axes(snapshot: Mapping[str, Any]) -> list[str]:
    state = snapshot.get("state") if isinstance(snapshot.get("state"), Mapping) else snapshot
    axes = state.get("axes") if isinstance(state, Mapping) else None
    active: list[str] = []
    if isinstance(axes, Mapping):
        for axis_state in axes.values():
            if not isinstance(axis_state, Mapping) or not axis_state.get("active"):
                continue
            axis = str(axis_state.get("axis") or "")
            if axis in AXIS_POLICIES and axis not in active:
                active.append(axis)
    elif isinstance(state, Mapping) and state.get("active"):
        axis = str(state.get("axis") or "")
        if axis in AXIS_POLICIES:
            active.append(axis)
    return sorted(active, key=ADAPTIVE_AXIS_ORDER.index)


def _parent_axis_state(snapshot: Mapping[str, Any], axis: str) -> Mapping[str, Any] | None:
    state = snapshot.get("state") if isinstance(snapshot.get("state"), Mapping) else snapshot
    if not isinstance(state, Mapping):
        return None
    axes = state.get("axes")
    if isinstance(axes, Mapping):
        for value in axes.values():
            if isinstance(value, Mapping) and value.get("axis") == axis:
                return value
    return state if state.get("axis") == axis else None


def _inherited_region(snapshot: Mapping[str, Any] | None) -> str:
    if not isinstance(snapshot, Mapping):
        return "all"
    state = snapshot.get("state") if isinstance(snapshot.get("state"), Mapping) else snapshot
    region = str((state or {}).get("region") or "all") if isinstance(state, Mapping) else "all"
    return region if region in {"all", "sky", "person", "background", "shadows", "highlights", "center", "edges"} else "all"


def _label_pattern(axis: str) -> str:
    return "|".join(re.escape(label) for label in sorted(_AXIS_LABELS[axis], key=len, reverse=True))


def _explicit_direction_mentions(
    *,
    axis: str,
    direction: int,
    text: str,
    clauses: list[str],
    markers: tuple[str, ...],
) -> list[dict[str, str]]:
    mentions: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for marker, start in _unnegated_occurrences(text, markers):
        end = start + len(marker)
        if _span_is_negated(text, start, end):
            continue
        source_clause = _source_clause_at(clauses, text, start)
        # Strength belongs to the clause that contains this request.  Looking
        # around the marker in the whole prompt lets a modifier from the
        # previous comma-delimited clause bleed into this operation.
        strength = _strength(source_clause, marker)
        key = (_normalize(source_clause), _normalize(marker), strength)
        if key not in seen:
            seen.add(key)
            mentions.append(
                {
                    "marker": marker,
                    "source_clause": source_clause,
                    "strength": strength,
                }
            )

    label = _label_pattern(axis)
    verb = (
        r"(?:提高|增加|上調|調高|提亮|拉高|raise|increase|more|lift|open)"
        if direction > 0
        else r"(?:降低|減少|下調|調低|壓低|壓暗|加深|收回|lower|decrease|reduce|less|crush|recover)"
    )
    for clause in clauses:
        normalized_clause = _normalize(clause)
        for match in re.finditer(
            rf"(?:(?:{label}).{{0,10}}?{verb}|{verb}.{{0,10}}?(?:{label}))",
            normalized_clause,
            flags=re.IGNORECASE,
        ):
            coordination_text = match.group(0)
            for labels in _AXIS_LABELS.values():
                for axis_label in sorted(labels, key=len, reverse=True):
                    coordination_text = coordination_text.replace(axis_label, "")
            if re.search(
                r"(?:[、，,；;。]|(?:和|以及|並且)|\b(?:and|but)\b)",
                coordination_text,
                flags=re.IGNORECASE,
            ):
                # A verb across a coordination boundary belongs to the next
                # axis label, not to the label before that boundary.  Shared
                # direction ellipsis is handled separately below.
                continue
            if _span_is_negated(
                normalized_clause, match.start(), match.end()
            ):
                continue
            marker = match.group(0)
            strength = _strength_at(
                normalized_clause, match.start(), match.end()
            )
            key = (_normalize(clause), _normalize(marker), strength)
            if key not in seen:
                seen.add(key)
                mentions.append(
                    {
                        "marker": marker,
                        "source_clause": clause,
                        "strength": strength,
                    }
                )

    for item in _shared_direction_mentions(
        axis=axis,
        direction=direction,
        text=text,
        clauses=clauses,
    ):
        key = (
            _normalize(str(item["source_clause"])),
            _normalize(str(item["marker"])),
            str(item["strength"]),
        )
        if key not in seen:
            seen.add(key)
            mentions.append(item)
    return mentions


def _shared_direction_mentions(
    *,
    axis: str,
    direction: int,
    text: str,
    clauses: list[str],
) -> list[dict[str, str]]:
    positive = r"(?:提高|增加|上調|調高|raise|increase)"
    negative = r"(?:降低|減少|下調|調低|lower|decrease|reduce)"
    verb_pattern = positive if direction > 0 else negative
    stop_verb = (
        r"(?:提高|增加|上調|調高|提亮|拉高|打開|加強|"
        r"降低|減少|下調|調低|壓低|壓暗|加深|收回|弱化|去除|"
        r"raise|increase|lift|open|add|strengthen|"
        r"lower|decrease|reduce|recover|crush|deepen|remove)"
    )
    boundary = (
        rf"(?={stop_verb}|[、，,]\s*{stop_verb}|"
        rf"(?:但是|但|可是|\bbut\b)|[；;。\n]|$)"
    )
    pattern = re.compile(
        rf"(?P<verb>{verb_pattern})\s*(?P<body>.*?){boundary}",
        flags=re.IGNORECASE,
    )
    results: list[dict[str, str]] = []
    direct_markers = _EXPLICIT_DIRECTION_PATTERNS[axis][0 if direction > 0 else 1]
    for match in pattern.finditer(text):
        if _span_is_negated(text, match.start("verb"), match.end("verb")):
            continue
        segment = match.group(0)
        mentioned = _mentioned_axes(segment)
        if len(mentioned) < 2 or axis not in mentioned:
            continue
        if any(
            marker in segment
            and not _is_negated(segment, segment.find(marker))
            for marker in direct_markers
        ):
            # The axis already has its own explicit verb-bound mention.
            continue
        label_occurrences = [
            (segment.find(label), label)
            for label in _AXIS_LABELS[axis]
            if segment.find(label) >= 0
        ]
        if not label_occurrences:
            continue
        label_offset, label = min(label_occurrences, key=lambda item: item[0])
        absolute_start = match.start() + label_offset
        source_clause = _source_clause_at(clauses, text, absolute_start)
        verb_clause = _source_clause_at(
            clauses, text, match.start("verb")
        )
        results.append(
            {
                "marker": label,
                "source_clause": source_clause,
                "strength": _strength(verb_clause, match.group("verb")),
            }
        )
    return results


def _mentioned_axes(text: str) -> set[str]:
    mentioned: set[str] = set()
    for axis, labels in _AXIS_LABELS.items():
        if any(label in text for label in labels):
            mentioned.add(axis)
    if "clarity" in mentioned and "清晰度的局部對比" in text:
        mentioned.discard("contrast")
    return mentioned


def _strength(text: str, marker: str) -> str:
    window_start = max(0, text.find(marker) - 12)
    window_end = min(len(text), text.find(marker) + len(marker) + 12)
    window = text[window_start:window_end]
    if _contains(window, _STRONG_MARKERS):
        return "strong"
    if _contains(window, _SUBTLE_MARKERS):
        return "subtle"
    return "normal"


def _strength_at(text: str, start: int, end: int) -> str:
    window = text[max(0, start - 10):min(len(text), end + 4)]
    if _contains(window, _STRONG_MARKERS):
        return "strong"
    if _contains(window, _SUBTLE_MARKERS):
        return "subtle"
    return "normal"


def _first_unnegated(text: str, markers: Iterable[str]) -> str | None:
    best: tuple[int, str] | None = None
    for marker in markers:
        start = text.find(marker)
        while start >= 0:
            if not _is_negated(text, start):
                if best is None or start < best[0]:
                    best = (start, marker)
                break
            start = text.find(marker, start + 1)
    return best[1] if best else None


def _unnegated_occurrences(
    text: str,
    markers: Iterable[str],
) -> list[tuple[str, int]]:
    occurrences: list[tuple[str, int]] = []
    for marker in markers:
        start = text.find(marker)
        while start >= 0:
            if not _is_negated(text, start):
                occurrences.append((marker, start))
            start = text.find(marker, start + 1)
    occurrences.sort(key=lambda item: (item[1], -len(item[0])))
    return occurrences


def _is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 24):start]
    if any(prefix.endswith(marker) for marker in _NEGATION_PREFIXES):
        return True
    return bool(
        re.search(
            r"(?:不要|別|不想|我不想|不希望|我不希望|無需|不用|不必)(?:再)?(?:把|將|讓|使)?[^、，,；;。\n]{0,10}$",
            prefix,
        )
    )


def _span_is_negated(text: str, start: int, end: int) -> bool:
    if _is_negated(text, start):
        return True
    segment = text[start:end]
    return bool(
        re.search(
            r"(?:不要|別|不想|不希望|無需|不用|不必|\bdo\s+not\b|\bdon't\b|\bnot\b|\bno\s+more\b)",
            segment,
            flags=re.IGNORECASE,
        )
    )


def _normalize(value: str) -> str:
    return normalize_prompt_text(value)


def _clauses(prompt: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(prompt or "")).strip()
    parts = re.split(
        r"(?:[、，,；;。\n]+|\s+(?:and|then|but)\s+|(?:而且|以及|同時|並且|並(?!非)|然後|又|但是|但|可是))",
        normalized,
        flags=re.IGNORECASE,
    )
    return [part.strip() for part in parts if part.strip()] or [normalized]


def _source_clause(clauses: list[str], marker: str) -> str:
    normalized_marker = _normalize(marker)
    for clause in clauses:
        if normalized_marker in _normalize(clause):
            return clause
    return clauses[0] if clauses else marker


def _source_clause_at(clauses: list[str], text: str, start: int) -> str:
    cursor = 0
    for clause in clauses:
        normalized_clause = _normalize(clause)
        clause_start = text.find(normalized_clause, cursor)
        if clause_start < 0:
            continue
        clause_end = clause_start + len(normalized_clause)
        if clause_start <= start < clause_end:
            return clause
        cursor = clause_end
    return clauses[0] if clauses else text


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _explicitness_rank(value: Any) -> int:
    return {
        "explicit_axis": 0,
        "feedback": 1,
        "macro_primary": 2,
    }.get(str(value), 9)


def _contains(text: str, markers: Iterable[str]) -> bool:
    return any(marker in text for marker in markers)


def _raise(code: str, message: str, **issue: Any) -> None:
    raise AdaptiveCompileError(code=code, message=message, issues=(issue,))
