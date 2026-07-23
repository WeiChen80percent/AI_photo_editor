from __future__ import annotations

import copy
import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.services.edit_engines import build_engine_parameters
from app.services.edit_plan import build_raw_parameter_edit_plan, build_single_edit_plan
from app.services.edit_schema import (
    EDIT_PARAMETER_RANGES,
    default_mask_type_for_region,
    validate_edit_mask_type,
    validate_edit_parameters,
    validate_edit_region,
)
from app.services.opencv_parameter_mapper import NEUTRAL_OPENCV_PARAMETERS
from app.services.prompt_parser import parse_edit_prompt
from app.services.adaptive_controller_v2 import (
    ADAPTIVE_POLICY_VERSION_V2,
    ADAPTIVE_SCHEMA_VERSION_V2,
    AdaptiveV2Error,
    resolve_adaptive_v2,
)


ADAPTIVE_SCHEMA_VERSION = ADAPTIVE_SCHEMA_VERSION_V2
ADAPTIVE_POLICY_VERSION = ADAPTIVE_POLICY_VERSION_V2
MAX_REFINEMENT_ROUNDS = 12


@dataclass(frozen=True)
class AxisPolicy:
    axis: str
    positive_intent: str
    negative_intent: str
    neutral: float
    minimum: float
    maximum: float
    quantum: float
    transform: str = "linear"


@dataclass(frozen=True)
class AdaptiveAdjustment:
    prompt_result: dict[str, Any]
    adaptive: dict[str, Any] | None
    render_base_image_path: str
    explanation: str | None = None

    @property
    def applied(self) -> bool:
        return bool(self.adaptive and self.adaptive.get("applied"))


class AdaptiveAdjustmentError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        issues: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    ):
        self.code = code
        self.status_code = status_code
        self.issues = copy.deepcopy(list(issues or []))
        super().__init__(message)


class AdaptiveClarificationRequired(AdaptiveAdjustmentError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "adaptive_clarification_required",
        issues: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    ):
        super().__init__(code, message, status_code=422, issues=issues)


class AdaptiveStepConverged(AdaptiveAdjustmentError):
    def __init__(
        self,
        message: str,
        *,
        issues: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    ):
        super().__init__(
            "adaptive_step_converged",
            message,
            status_code=409,
            issues=issues,
        )


class AdaptiveFeedbackSatisfied(AdaptiveAdjustmentError):
    def __init__(
        self,
        message: str,
        *,
        issues: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    ):
        super().__init__(
            "adaptive_feedback_satisfied",
            message,
            status_code=409,
            issues=issues,
        )


_AXIS_POLICIES = {
    "brightness": AxisPolicy(
        axis="brightness",
        positive_intent="brighten",
        negative_intent="darken",
        neutral=0.0,
        minimum=EDIT_PARAMETER_RANGES["brightness"][0],
        maximum=EDIT_PARAMETER_RANGES["brightness"][1],
        quantum=0.25,
    ),
    "temperature": AxisPolicy(
        axis="temperature",
        positive_intent="warm",
        negative_intent="cool",
        neutral=0.0,
        minimum=EDIT_PARAMETER_RANGES["temperature"][0],
        maximum=EDIT_PARAMETER_RANGES["temperature"][1],
        quantum=1.0,
    ),
    "saturation": AxisPolicy(
        axis="saturation",
        positive_intent="vivid",
        negative_intent="natural",
        neutral=1.0,
        minimum=max(0.01, EDIT_PARAMETER_RANGES["saturation"][0]),
        maximum=EDIT_PARAMETER_RANGES["saturation"][1],
        quantum=0.01,
        transform="log",
    ),
}

_INTENT_TO_AXIS_DIRECTION = {
    policy.positive_intent: (policy.axis, 1)
    for policy in _AXIS_POLICIES.values()
} | {
    policy.negative_intent: (policy.axis, -1)
    for policy in _AXIS_POLICIES.values()
}

_EXPLICIT_REGION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("all", ("全圖", "整張", "整體", "whole image", "entire image", "global")),
    ("shadows", ("shadow", "shadows", "暗部", "陰影")),
    (
        "highlights",
        (
            "highlight",
            "highlights",
            "高光",
            "亮部",
            "過曝",
            "overexposure",
            "overexposed",
        ),
    ),
    ("sky", ("sky", "天空")),
    ("person", ("face", "person", "portrait", "臉", "人物", "人像")),
    ("background", ("background", "背景")),
    ("center", ("center", "middle", "subject", "主體", "中間", "中心")),
    ("edges", ("edge", "edges", "border", "邊緣")),
)

_CORRECTION_MARKERS = (
    "太",
    "過頭",
    "過亮",
    "還是",
    "不要那麼",
    "別那麼",
    "剛剛",
    "退一點",
    "收回",
    "too ",
    "less ",
)
_CONTINUATION_MARKERS = ("再", "更", "還可以", "一點", "稍微", "again", "more")
_VAGUE_OPPOSITE_MARKERS = (
    "太多了",
    "過頭了",
    "退一點",
    "收回一點",
    "少一點",
    "不要那麼多",
    "too much",
    "back off",
)
_SATISFIED_MARKERS = ("剛好", "可以了", "這樣就好", "just right", "good now")
_AMBIGUOUS_FEEDBACK_MARKERS = (
    "怪怪",
    "不太對",
    "不對勁",
    "不好看",
    "不喜歡",
    "wrong",
    "weird",
)
_GLOBAL_RESET_MARKERS = (
    "恢復原圖",
    "回到原圖",
    "回原圖",
    "重新開始",
    "全部重設",
    "reset all",
    "start over",
)
_AXIS_RESET_MARKERS = ("歸零", "重設", "恢復預設", "reset")
_STRONG_ACTION_MARKERS = (
    "大幅調",
    "大幅提高",
    "大幅增加",
    "大幅降低",
    "大幅減少",
    "大幅變",
    "strongly ",
)
_SUBTLE_MARKERS = (
    "一點點",
    "一點",
    "稍微",
    "些微",
    "少許",
    "a little",
    "slightly",
    "subtle",
)
_NEGATION_PREFIXES = (
    "還沒有",
    "不要再",
    "別再",
    "沒有",
    "不是",
    "不算",
    "不想",
    "並非",
    "不要",
    "先不要",
    "別",
    "不必",
    "不用",
    "無需",
    "還沒",
    "尚未",
    "未",
    "不",
    "not ",
    "don't ",
    "do not ",
)

_OVEREXPOSURE_GUARDS = (
    "不要過曝",
    "避免過曝",
    "別過曝",
    "avoid overexposure",
    "prevent overexposure",
    "without overexposure",
    "don't overexpose",
    "do not overexpose",
)

_AXIS_FEEDBACK_MARKERS: dict[
    str,
    tuple[tuple[int, tuple[str, ...]], ...],
] = {
    "brightness": (
        (
            -1,
            (
                "太亮",
                "過亮",
                "亮太多",
                "亮過頭",
                "亮度太高",
                "不要那麼亮",
                "別那麼亮",
                "不要太亮",
                "別太亮",
                "不太暗",
                "too bright",
                "not too bright",
            ),
        ),
        (
            1,
            (
                "太暗",
                "過暗",
                "有點暗",
                "不夠亮",
                "亮度太低",
                "不太亮",
                "不要那麼暗",
                "別那麼暗",
                "too dark",
                "not bright enough",
            ),
        ),
    ),
    "temperature": (
        (
            -1,
            (
                "太暖",
                "過暖",
                "太黃",
                "過黃",
                "色溫太高",
                "不要那麼暖",
                "不要那麼黃",
                "不太冷",
                "不太藍",
                "too warm",
                "too yellow",
            ),
        ),
        (
            1,
            (
                "太冷",
                "過冷",
                "太藍",
                "過藍",
                "色溫太低",
                "不太暖",
                "不太黃",
                "不要那麼冷",
                "不要那麼藍",
                "too cool",
                "too blue",
            ),
        ),
    ),
    "saturation": (
        (
            -1,
            (
                "太鮮豔",
                "過鮮豔",
                "太飽和",
                "過飽和",
                "飽和度太高",
                "飽和太高",
                "顏色太重",
                "不要那麼鮮豔",
                "不要那麼飽和",
                "too vivid",
                "too saturated",
            ),
        ),
        (
            1,
            (
                "不夠鮮豔",
                "不夠飽和",
                "不太鮮豔",
                "不太飽和",
                "飽和度太低",
                "飽和太低",
                "太淡",
                "顏色太淡",
                "不要那麼淡",
                "not vivid enough",
                "not saturated enough",
            ),
        ),
    ),
}

_AXIS_REQUEST_DIRECTION_MARKERS: dict[
    str,
    tuple[tuple[str, ...], tuple[str, ...]],
] = {
    "brightness": (
        (
            "亮一點",
            "調亮",
            "變亮",
            "更亮",
            "再亮",
            "增加亮度",
            "提高亮度",
            "亮度提高",
            "亮度增加",
            "亮度大幅提高",
            "亮度大幅增加",
            "brighten",
            "brighter",
        ),
        (
            "暗一點",
            "調暗",
            "變暗",
            "更暗",
            "再暗",
            "降低亮度",
            "減少亮度",
            "亮度降低",
            "亮度減少",
            "亮度大幅降低",
            "亮度大幅減少",
            "darken",
            "darker",
        ),
    ),
    "temperature": (
        (
            "暖一點",
            "調暖",
            "變暖",
            "更暖",
            "再暖",
            "提高色溫",
            "增加色溫",
            "色溫提高",
            "色溫增加",
            "色溫大幅提高",
            "色溫大幅增加",
            "warmer",
        ),
        (
            "冷一點",
            "調冷",
            "變冷",
            "更冷",
            "再冷",
            "降低色溫",
            "減少色溫",
            "色溫降低",
            "色溫減少",
            "色溫大幅降低",
            "色溫大幅減少",
            "cooler",
        ),
    ),
    "saturation": (
        (
            "鮮豔一點",
            "提高飽和",
            "增加飽和",
            "飽和度提高",
            "飽和度增加",
            "飽和度大幅提高",
            "飽和度大幅增加",
            "更鮮豔",
            "more vivid",
        ),
        (
            "自然一點",
            "降低飽和",
            "減少飽和",
            "飽和度降低",
            "飽和度減少",
            "飽和度大幅降低",
            "飽和度大幅減少",
            "淡一點",
            "less saturated",
        ),
    ),
}

_AXIS_STRONG_DESCRIPTION_DIRECTION_MARKERS = {
    "brightness": (
        ("亮很多", "很亮", "非常亮", "very bright", "much brighter"),
        ("暗很多", "很暗", "非常暗", "very dark", "much darker"),
    ),
    "temperature": (
        ("暖很多", "很暖", "非常暖", "very warm", "much warmer"),
        ("冷很多", "很冷", "非常冷", "very cool", "much cooler"),
    ),
    "saturation": (
        (
            "鮮豔很多",
            "很鮮豔",
            "非常鮮豔",
            "很飽和",
            "非常飽和",
            "very vivid",
            "very saturated",
            "much more vivid",
            "much more saturated",
        ),
        (
            "淡很多",
            "很淡",
            "非常淡",
            "very dull",
            "much duller",
            "much less saturated",
        ),
    ),
}

_POSITIVE_GUARD_MARKERS = {
    "brightness": (
        "不要太亮",
        "不要那麼亮",
        "別太亮",
        "別那麼亮",
        "不要過曝",
        "避免過曝",
        "not too bright",
    ),
    "temperature": (
        "不要太暖",
        "不要那麼暖",
        "不要太黃",
        "不要那麼黃",
        "別太暖",
        "別那麼暖",
        "別太黃",
        "not too warm",
        "not too yellow",
    ),
    "saturation": (
        "不要太鮮豔",
        "不要那麼鮮豔",
        "不要太飽和",
        "不要那麼飽和",
        "別太鮮豔",
        "別那麼鮮豔",
        "別太飽和",
        "not too vivid",
        "not too saturated",
    ),
}
_NEGATIVE_GUARD_MARKERS = {
    "brightness": ("不要太暗", "不要那麼暗", "別太暗", "別那麼暗", "not too dark"),
    "temperature": ("不要太冷", "不要那麼冷", "不要太藍", "不要那麼藍", "別太冷", "別那麼冷", "別太藍", "not too cool", "not too blue"),
    "saturation": ("不要太淡", "不要那麼淡", "別太淡", "別那麼淡", "not too dull"),
}

_NUMERIC_VALUE_PATTERN = r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))"
_NUMERIC_PATTERNS = {
    "brightness": re.compile(
        rf"(?:亮度|brightness)\s*"
        rf"(?:設(?:定)?(?:成|為)?|調(?:整)?(?:成|為|到)?|調(?:高|低)(?:成|為|到)|提高到|降低到|to|=|[:：])?\s*"
        rf"{_NUMERIC_VALUE_PATTERN}",
        re.IGNORECASE,
    ),
    "temperature": re.compile(
        rf"(?:色溫|temperature)\s*"
        rf"(?:設(?:定)?(?:成|為)?|調(?:整)?(?:成|為|到)?|調(?:高|低)(?:成|為|到)|提高到|降低到|to|=|[:：])?\s*"
        rf"{_NUMERIC_VALUE_PATTERN}",
        re.IGNORECASE,
    ),
    "saturation": re.compile(
        rf"(?:飽和度|saturation)\s*"
        rf"(?:設(?:定)?(?:成|為)?|調(?:整)?(?:成|為|到)?|調(?:高|低)(?:成|為|到)|提高到|降低到|to|=|[:：])?\s*"
        rf"{_NUMERIC_VALUE_PATTERN}",
        re.IGNORECASE,
    ),
}

_RELATIVE_NUMERIC_PATTERNS = {
    axis: (
        re.compile(
            rf"(?:{label})\s*(?:提高|增加|上調|調高)(?!到)\s*{_NUMERIC_VALUE_PATTERN}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?:{label})\s*(?:降低|減少|下調|調低)(?!到)\s*{_NUMERIC_VALUE_PATTERN}",
            re.IGNORECASE,
        ),
    )
    for axis, label in {
        "brightness": "亮度|brightness",
        "temperature": "色溫|temperature",
        "saturation": "飽和度|saturation",
    }.items()
}
_UNCONSUMED_NUMERIC_ACTION_PATTERN = re.compile(
    rf"(?:設(?:定)?(?:成|為)?|調(?:整)?(?:成|為|到)|提高|降低|增加|減少|"
    rf"上調|下調|調高|調低|increase|decrease|raise|lower|add|subtract)"
    rf"\s*(?:by\s*)?{_NUMERIC_VALUE_PATTERN}",
    re.IGNORECASE,
)


def resolve_adaptive_adjustment(
    prompt_result: Mapping[str, Any],
    prompt: str,
    parent_record: Mapping[str, Any] | None,
    default_base_image_path: str,
    engine_name: str = "opencv",
) -> AdaptiveAdjustment:
    """Resolve one immutable branch-local adaptive v2 request.

    The public signature intentionally remains identical to v1 so `/edit`,
    history and existing callers can migrate without an API break.
    """

    try:
        resolution = resolve_adaptive_v2(
            prompt_result=prompt_result,
            prompt=prompt,
            parent_record=parent_record,
            default_base_image_path=default_base_image_path,
            engine_name=engine_name,
        )
    except AdaptiveV2Error as exc:
        if exc.code == "adaptive_feedback_satisfied":
            raise AdaptiveFeedbackSatisfied(
                exc.message,
                issues=exc.issues,
            ) from exc
        if exc.status_code == 409:
            raise AdaptiveStepConverged(
                exc.message,
                issues=exc.issues,
            ) from exc
        if exc.status_code == 422:
            raise AdaptiveClarificationRequired(
                exc.message,
                code=exc.code,
                issues=exc.issues,
            ) from exc
        raise AdaptiveAdjustmentError(
            exc.code,
            exc.message,
            status_code=exc.status_code,
            issues=exc.issues,
        ) from exc
    return AdaptiveAdjustment(
        prompt_result=resolution.prompt_result,
        adaptive=resolution.adaptive,
        render_base_image_path=resolution.render_base_image_path,
        explanation=resolution.explanation,
    )


def _resolve_adaptive_adjustment_v1(
    prompt_result: Mapping[str, Any],
    prompt: str,
    parent_record: Mapping[str, Any] | None,
    default_base_image_path: str,
    engine_name: str = "opencv",
) -> AdaptiveAdjustment:
    """Prepare a deterministic, branch-local adaptive prompt edit.

    No state is mutated here. The returned snapshot becomes durable only after the
    caller successfully renders and writes the edit history record.
    """

    result = copy.deepcopy(dict(prompt_result))
    text = (prompt or "").strip().lower()
    default_base = str(default_base_image_path or "")

    if str(engine_name or "").strip().lower() != "opencv":
        return AdaptiveAdjustment(result, None, default_base)

    parent_snapshot = _read_parent_snapshot(parent_record)
    parent_state = (
        parent_snapshot
        if parent_snapshot is not None and parent_snapshot.get("active") is not False
        else None
    )
    rule_result = parse_edit_prompt(prompt)
    if _contains_unnegated(text, _GLOBAL_RESET_MARKERS):
        if _result_has_action(rule_result) or _has_directional_request(text):
            raise AdaptiveClarificationRequired(
                "回到原圖與其他操作同時出現，請分開輸入以免忽略其中一項。"
            )
        return _build_global_reset(result, text, parent_record, default_base)
    explicit_regions = _detect_explicit_regions(text)
    if len(explicit_regions) > 1:
        raise AdaptiveClarificationRequired(
            "第一版一次只支援一個修圖區域，請分開指定全圖、人物、天空或其他區域。"
        )
    explicit_region = explicit_regions[0] if explicit_regions else None
    reset_axes = _detect_axis_resets(text)
    if len(reset_axes) > 1:
        raise AdaptiveClarificationRequired(
            "第一版一次只支援重設一個軸，請分開重設亮度、色溫或飽和度。"
        )

    relative_numeric_matches = _detect_relative_numerics(text)
    if len(relative_numeric_matches) > 1:
        raise AdaptiveClarificationRequired(
            "第一版一次只支援一個相對數值軸，請分開調整亮度、色溫或飽和度。"
        )
    numeric_matches = _detect_explicit_numerics(text)
    if numeric_matches and relative_numeric_matches:
        raise AdaptiveClarificationRequired(
            "同一句同時包含絕對值與相對數值，請分開調整。"
        )
    if len(numeric_matches) > 1:
        raise AdaptiveClarificationRequired(
            "第一版一次只支援一個明確數值軸，請分開調整亮度、色溫或飽和度。"
        )
    if numeric_matches or relative_numeric_matches:
        axis, value = (
            numeric_matches[0]
            if numeric_matches
            else relative_numeric_matches[0]
        )
        numeric_rule_semantic = _semantic_from_result(rule_result)
        numeric_remainder = _without_numeric_expressions(text)
        if (
            _plan_type(rule_result) == "preset"
            or _edit_count(rule_result) > 1
            or bool(reset_axes)
            or _has_directional_request(numeric_remainder)
            or _UNCONSUMED_NUMERIC_ACTION_PATTERN.search(numeric_remainder) is not None
            or numeric_rule_semantic is not None
            and numeric_rule_semantic.get("axis") != axis
        ):
            raise AdaptiveClarificationRequired(
                "明確數值與其他操作同時出現，請分開輸入以免忽略其中一項。"
            )
        return _build_absolute_adjustment(
            result=result,
            prompt=prompt,
            axis=axis,
            value=value,
            parent_record=parent_record,
            parent_state=parent_snapshot,
            default_base=default_base,
            explicit_region=explicit_region,
            relative_delta=None if numeric_matches else value,
        )

    if reset_axes and (
        _has_directional_request(text)
        or _plan_type(rule_result) == "preset"
        or _edit_count(rule_result) > 1
    ):
        raise AdaptiveClarificationRequired(
            "參數重設與其他操作同時出現，請分開輸入以免忽略其中一項。"
        )
    if (
        _contains_unnegated(text, _SATISFIED_MARKERS)
        and not reset_axes
        and not _result_has_action(rule_result)
    ):
        raise AdaptiveFeedbackSatisfied(
            "已保留目前版本；未新增沒有像素變化的歷史版本。"
        )
    direction_conflicts = _detect_direction_conflicts(text)
    if direction_conflicts:
        raise AdaptiveClarificationRequired(
            f"同一句對{_axis_label(direction_conflicts[0])}包含相反方向，請分開描述。"
        )
    if not reset_axes and _plan_type(rule_result) == "preset":
        return AdaptiveAdjustment(
            _merge_rule_result(result, rule_result),
            None,
            default_base,
        )
    if not reset_axes and _edit_count(rule_result) > 1:
        return AdaptiveAdjustment(
            _merge_rule_result(result, rule_result),
            None,
            default_base,
        )

    feedback_semantics = _semantics_from_feedback(text, parent_state)
    if len(feedback_semantics) > 1:
        raise AdaptiveClarificationRequired(
            "同一句包含多個可能的修正軸，請分開調整亮度、色溫或飽和度。"
        )

    provided_semantic = _semantic_from_result(result)
    rule_semantic = _semantic_from_result(rule_result)
    semantic = rule_semantic or provided_semantic
    semantic_plan_source = rule_result if rule_semantic is not None else result
    if reset_axes:
        semantic = {
            "axis": reset_axes[0],
            "direction": 0,
            "strength": "subtle",
            "inferred_axis": True,
        }
        semantic_plan_source = rule_result
    elif feedback_semantics:
        if _edit_count(rule_result) > 1:
            raise AdaptiveClarificationRequired(
                "同一句同時包含修正回饋與其他操作，請分開輸入以免套用錯誤軸。"
            )
        feedback_semantic = feedback_semantics[0]
        parsed_axes = (
            {str(rule_semantic["axis"])} if rule_semantic is not None else set()
        )
        parsed_axes.update(_directional_request_axes(text))
        if parsed_axes and parsed_axes != {str(feedback_semantic["axis"])}:
            raise AdaptiveClarificationRequired(
                "同一句同時包含不同調整軸，請分開調整以保留正確的收斂狀態。"
            )
        semantic = feedback_semantic
        semantic_plan_source = rule_result if rule_semantic is not None else result
    if semantic is None:
        if parent_record is not None and _contains(
            text, _AMBIGUOUS_FEEDBACK_MARKERS + _VAGUE_OPPOSITE_MARKERS
        ):
            raise AdaptiveClarificationRequired(
                "無法安全判斷要微調亮度、色溫或飽和度，請補充要調整的項目。"
            )
        return AdaptiveAdjustment(result, None, default_base)

    axis = str(semantic["axis"])
    direction = int(semantic["direction"])
    semantic["strength"] = _deterministic_strength(
        text,
        inferred=bool(semantic.get("inferred_axis")),
        axis=axis,
        direction=direction,
    )
    result = _normalize_result_for_semantic(
        result,
        prompt=prompt,
        axis=axis,
        direction=direction,
        strength=str(semantic["strength"]),
        plan_source=semantic_plan_source,
    )
    policy = _AXIS_POLICIES[axis]
    region_source = "explicit" if explicit_region is not None else "default"
    region = explicit_region or "all"
    mask_type = default_mask_type_for_region(region)

    region_parent_state = parent_snapshot
    if (
        explicit_region is None
        and region_parent_state is not None
        and region_parent_state.get("axis") == axis
    ):
        region = validate_edit_region(region_parent_state.get("region"))
        mask_type = validate_edit_mask_type(region_parent_state.get("mask_type"))
        region_source = "inherited"
    mask_type = default_mask_type_for_region(region)

    if reset_axes:
        return _build_axis_reset(
            result=result,
            prompt=prompt,
            policy=policy,
            region=region,
            mask_type=mask_type,
            region_source=region_source,
            parent_record=parent_record,
            parent_state=parent_snapshot,
            default_base=default_base,
        )

    compatible_parent = _compatible_parent_state(
        parent_state,
        axis=axis,
        region=region,
        mask_type=mask_type,
    )
    force_new_episode = semantic.get("strength") == "strong"
    if compatible_parent is None or force_new_episode:
        return _start_episode(
            result=result,
            prompt=prompt,
            policy=policy,
            direction=direction,
            region=region,
            mask_type=mask_type,
            region_source=region_source,
            parent_record=parent_record,
            default_base=default_base,
            reason="explicit_strength_reset" if force_new_episode else "initial_template",
        )

    return _continue_episode(
        result=result,
        prompt=prompt,
        policy=policy,
        direction=direction,
        region=region,
        mask_type=mask_type,
        region_source=region_source,
        parent_state=compatible_parent,
        parent_record=parent_record,
        inferred_axis=bool(semantic.get("inferred_axis")),
        text=text,
    )


def _semantic_from_result(result: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = result.get("edit_plan")
    if not isinstance(plan, Mapping) or plan.get("type") != "edits":
        return None
    edits = plan.get("edits")
    if not isinstance(edits, list) or len(edits) != 1:
        return None
    edit = edits[0]
    if not isinstance(edit, Mapping):
        return None
    mapping = _INTENT_TO_AXIS_DIRECTION.get(str(edit.get("intent") or ""))
    if mapping is None:
        return None
    axis, direction = mapping
    return {
        "axis": axis,
        "direction": direction,
        "strength": str(edit.get("strength") or "normal"),
        "inferred_axis": False,
    }


def _semantics_from_feedback(
    text: str,
    parent_state: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    for axis, (positive_markers, _) in _AXIS_REQUEST_DIRECTION_MARKERS.items():
        if _contains_unnegated(text, positive_markers) and _contains(
            text,
            _POSITIVE_GUARD_MARKERS[axis],
        ):
            return [
                {
                    "axis": axis,
                    "direction": 1,
                    "strength": "subtle",
                    "inferred_axis": True,
                }
            ]

    for axis, (_, negative_markers) in _AXIS_REQUEST_DIRECTION_MARKERS.items():
        if _contains_unnegated(text, negative_markers) and _contains(
            text,
            _NEGATIVE_GUARD_MARKERS[axis],
        ):
            return [
                {
                    "axis": axis,
                    "direction": -1,
                    "strength": "subtle",
                    "inferred_axis": True,
                }
            ]

    semantics: list[dict[str, Any]] = []
    for axis, direction_markers in _AXIS_FEEDBACK_MARKERS.items():
        for direction, markers in direction_markers:
            if _contains_unnegated(text, markers):
                semantics.append(
                    {
                        "axis": axis,
                        "direction": direction,
                        "strength": "subtle",
                        "inferred_axis": True,
                    }
                )
    if semantics:
        return semantics

    request_semantics: list[dict[str, Any]] = []
    for axis, (positive_markers, negative_markers) in (
        _AXIS_REQUEST_DIRECTION_MARKERS.items()
    ):
        if _contains_unnegated(text, positive_markers):
            request_semantics.append(
                {
                    "axis": axis,
                    "direction": 1,
                    "strength": "subtle",
                    "inferred_axis": False,
                }
            )
        if _contains_unnegated(text, negative_markers):
            request_semantics.append(
                {
                    "axis": axis,
                    "direction": -1,
                    "strength": "subtle",
                    "inferred_axis": False,
                }
            )
    if request_semantics:
        return request_semantics

    strong_description_semantics: list[dict[str, Any]] = []
    for axis, (positive_markers, negative_markers) in (
        _AXIS_STRONG_DESCRIPTION_DIRECTION_MARKERS.items()
    ):
        if _contains_unnegated(text, positive_markers):
            strong_description_semantics.append(
                {
                    "axis": axis,
                    "direction": 1,
                    "strength": "strong",
                    "inferred_axis": False,
                }
            )
        if _contains_unnegated(text, negative_markers):
            strong_description_semantics.append(
                {
                    "axis": axis,
                    "direction": -1,
                    "strength": "strong",
                    "inferred_axis": False,
                }
            )
    if strong_description_semantics:
        return strong_description_semantics

    if parent_state is not None and _contains_unnegated(
        text,
        _VAGUE_OPPOSITE_MARKERS,
    ):
        axis = str(parent_state.get("axis") or "")
        if axis in _AXIS_POLICIES:
            return [
                {
                    "axis": axis,
                    "direction": -int(parent_state.get("previous_direction") or 1),
                    "strength": "subtle",
                    "inferred_axis": True,
                }
            ]
    return []


def _edit_count(result: Mapping[str, Any]) -> int:
    plan = result.get("edit_plan")
    if not isinstance(plan, Mapping):
        return 0
    edits = plan.get("edits")
    return len(edits) if isinstance(edits, list) else 0


def _detect_direction_conflicts(text: str) -> list[str]:
    return [
        axis
        for axis, (positive_markers, negative_markers) in (
            _AXIS_REQUEST_DIRECTION_MARKERS.items()
        )
        if _contains_unnegated(text, positive_markers)
        and _contains_unnegated(text, negative_markers)
    ]


def _has_directional_request(text: str) -> bool:
    return any(
        _contains_unnegated(text, positive_markers)
        or _contains_unnegated(text, negative_markers)
        for positive_markers, negative_markers in (
            _AXIS_REQUEST_DIRECTION_MARKERS.values()
        )
    )


def _directional_request_axes(text: str) -> set[str]:
    return {
        axis
        for axis, (positive_markers, negative_markers) in (
            _AXIS_REQUEST_DIRECTION_MARKERS.items()
        )
        if _contains_unnegated(text, positive_markers)
        or _contains_unnegated(text, negative_markers)
    }


def _plan_type(result: Mapping[str, Any]) -> str:
    plan = result.get("edit_plan")
    return str(plan.get("type") or "") if isinstance(plan, Mapping) else ""


def _result_has_action(result: Mapping[str, Any]) -> bool:
    plan = result.get("edit_plan")
    if not isinstance(plan, Mapping):
        return False
    plan_type = str(plan.get("type") or "")
    if plan_type in {"preset", "reference"} or _edit_count(result) > 0:
        return True
    raw_parameters = plan.get("raw_parameters")
    return isinstance(raw_parameters, Mapping) and bool(raw_parameters)


def _merge_rule_result(
    result: dict[str, Any],
    rule_result: Mapping[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(result)
    for key in (
        "prompt",
        "resolved_intent",
        "preset_name",
        "edit_plan",
        "parameters",
        "explanation",
    ):
        if key in rule_result:
            merged[key] = copy.deepcopy(rule_result[key])
    return merged


def _deterministic_strength(
    text: str,
    *,
    inferred: bool,
    axis: str,
    direction: int,
) -> str:
    if inferred:
        return "subtle"
    if _contains_unnegated(text, _STRONG_ACTION_MARKERS):
        return "strong"
    direction_markers = _AXIS_STRONG_DESCRIPTION_DIRECTION_MARKERS.get(axis)
    if direction_markers is not None and _contains_unnegated(
        text,
        direction_markers[0 if direction > 0 else 1],
    ):
        return "strong"
    if _contains(text, _SUBTLE_MARKERS):
        return "subtle"
    return "normal"


def _normalize_result_for_semantic(
    result: dict[str, Any],
    *,
    prompt: str,
    axis: str,
    direction: int,
    strength: str,
    plan_source: Mapping[str, Any],
) -> dict[str, Any]:
    if direction == 0:
        return result
    policy = _AXIS_POLICIES[axis]
    intent = policy.positive_intent if direction > 0 else policy.negative_intent
    original_plan = result.get("edit_plan")
    original_plan_mapping = original_plan if isinstance(original_plan, Mapping) else {}
    source_plan = plan_source.get("edit_plan")
    plan_mapping = source_plan if isinstance(source_plan, Mapping) else {}
    original_edits = copy.deepcopy(original_plan_mapping.get("edits") or [])
    plan = build_single_edit_plan(
        prompt=prompt,
        intent=intent,
        strength=strength,
        region=validate_edit_region(plan_mapping.get("region")),
        mask_type=validate_edit_mask_type(plan_mapping.get("mask_type")),
    )
    if original_edits != plan["edits"]:
        plan["parser_semantic_edits"] = original_edits
    result["edit_plan"] = plan
    result["parameters"] = build_engine_parameters("opencv", plan)
    result["resolved_intent"] = intent
    result["preset_name"] = None
    return result


def _start_episode(
    *,
    result: dict[str, Any],
    prompt: str,
    policy: AxisPolicy,
    direction: int,
    region: str,
    mask_type: str,
    region_source: str,
    parent_record: Mapping[str, Any] | None,
    default_base: str,
    reason: str,
) -> AdaptiveAdjustment:
    source_parameters = result.get("parameters")
    if not isinstance(source_parameters, Mapping):
        source_parameters = build_engine_parameters("opencv", result.get("edit_plan") or {})
    render_parameters = _canonical_parameters(source_parameters)
    candidate = float(render_parameters.get(policy.axis, policy.neutral))
    if math.isclose(candidate, policy.neutral, abs_tol=policy.quantum / 10):
        return AdaptiveAdjustment(result, None, default_base)
    candidate = _quantize(policy, candidate)
    render_parameters[policy.axis] = candidate
    lower = policy.neutral if direction > 0 else None
    upper = policy.neutral if direction < 0 else None
    step = _distance(policy, policy.neutral, candidate)
    anchor_edit_id = _record_text(parent_record, "edit_id")
    state = _state_snapshot(
        policy=policy,
        episode_id=_episode_id(default_base, policy.axis, region, mask_type),
        anchor_edit_id=anchor_edit_id,
        anchor_image_path=default_base,
        region=region,
        mask_type=mask_type,
        current_value=policy.neutral,
        next_value=candidate,
        lower_bound=lower,
        upper_bound=upper,
        previous_direction=direction,
        base_step=step,
        step_before=step,
        step_after=step,
        refinement_round=0,
        reversal_count=0,
        render_parameters=render_parameters,
        active=True,
        converged=False,
    )
    relation = "initial" if parent_record is None else "new_episode"
    adaptive = _adaptive_metadata(
        state=state,
        applied=True,
        reason=reason,
        relation=relation,
        confidence="high",
        region_source=region_source,
        direction=direction,
        bounds_before={"lower": None, "upper": None},
    )
    explanation = _adaptive_explanation(adaptive)
    prepared = _prepare_result(result, prompt, render_parameters, region, mask_type, adaptive)
    return AdaptiveAdjustment(prepared, adaptive, default_base, explanation)


def _continue_episode(
    *,
    result: dict[str, Any],
    prompt: str,
    policy: AxisPolicy,
    direction: int,
    region: str,
    mask_type: str,
    region_source: str,
    parent_state: Mapping[str, Any],
    parent_record: Mapping[str, Any] | None,
    inferred_axis: bool,
    text: str,
) -> AdaptiveAdjustment:
    current = _finite_number(parent_state.get("next_value"))
    if current is None:
        current = _finite_number(parent_state.get("current_candidate"))
    if current is None:
        current = policy.neutral
    current = _quantize(policy, current)
    lower = _bounded_optional(policy, parent_state.get("lower_bound"))
    upper = _bounded_optional(policy, parent_state.get("upper_bound"))
    bounds_before = {"lower": lower, "upper": upper}
    if direction > 0:
        lower = current if lower is None else max(lower, current)
    else:
        upper = current if upper is None else min(upper, current)
    if lower is not None and upper is not None and lower > upper:
        raise AdaptiveClarificationRequired(
            "目前版本的自適應區間已不一致，請指定新方向或從原圖重新開始。"
        )

    base_step = _finite_number(parent_state.get("base_step"))
    if base_step is None or base_step <= 0:
        base_step = max(policy.quantum, _finite_number(parent_state.get("step_after")) or 0)
    if lower is not None and upper is not None:
        candidate = _midpoint(policy, lower, upper)
        reason = "bracket_midpoint"
    else:
        candidate = _advance(policy, current, direction, base_step)
        reason = "unbounded_template_step"
    candidate = _quantize(policy, candidate)
    step = _distance(policy, current, candidate)
    refinement_round = int(parent_state.get("refinement_round") or 0)
    if lower is not None and upper is not None:
        refinement_round += 1
    if (
        math.isclose(candidate, current, abs_tol=policy.quantum / 10)
        or refinement_round > MAX_REFINEMENT_ROUNDS
    ):
        raise AdaptiveStepConverged(
            f"{policy.axis} 已收斂到最小步長 {policy.quantum:g}；未新增零變化版本。"
        )

    previous_direction = int(parent_state.get("previous_direction") or direction)
    stored_render_parameters = parent_state.get("render_parameters")
    if isinstance(stored_render_parameters, Mapping):
        render_parameters = _canonical_parameters(stored_render_parameters)
    else:
        render_parameters = _canonical_parameters(
            (parent_record or {}).get("engine_parameters")
            or (parent_record or {}).get("parameters")
        )
    render_parameters[policy.axis] = candidate
    reversal_count = int(parent_state.get("reversal_count") or 0)
    if previous_direction != direction:
        reversal_count += 1
    state = _state_snapshot(
        policy=policy,
        episode_id=str(parent_state.get("episode_id") or ""),
        anchor_edit_id=_optional_text(parent_state.get("anchor_edit_id")),
        anchor_image_path=str(parent_state.get("anchor_image_path") or ""),
        region=region,
        mask_type=mask_type,
        current_value=current,
        next_value=candidate,
        lower_bound=lower,
        upper_bound=upper,
        previous_direction=direction,
        base_step=base_step,
        step_before=_finite_number(parent_state.get("step_after")) or base_step,
        step_after=step,
        refinement_round=refinement_round,
        reversal_count=reversal_count,
        render_parameters=render_parameters,
        active=True,
        converged=False,
    )
    relation = "correct" if previous_direction != direction else "continue"
    confidence = "high" if inferred_axis or _contains(text, _CORRECTION_MARKERS) else "medium"
    adaptive = _adaptive_metadata(
        state=state,
        applied=True,
        reason=reason,
        relation=relation,
        confidence=confidence,
        region_source=region_source,
        direction=direction,
        bounds_before=bounds_before,
    )
    explanation = _adaptive_explanation(adaptive)
    prepared = _prepare_result(result, prompt, render_parameters, region, mask_type, adaptive)
    return AdaptiveAdjustment(
        prepared,
        adaptive,
        str(parent_state.get("anchor_image_path") or ""),
        explanation,
    )


def _build_absolute_adjustment(
    *,
    result: dict[str, Any],
    prompt: str,
    axis: str,
    value: float,
    parent_record: Mapping[str, Any] | None,
    parent_state: Mapping[str, Any] | None,
    default_base: str,
    explicit_region: str | None,
    relative_delta: float | None,
) -> AdaptiveAdjustment:
    policy = _AXIS_POLICIES[axis]
    parent_region = (
        validate_edit_region(parent_state.get("region"))
        if parent_state is not None
        else None
    )
    region = (
        explicit_region
        or (parent_region if parent_state is not None and parent_state.get("axis") == axis else None)
        or "all"
    )
    mask_type = default_mask_type_for_region(region)
    compatible_state = _compatible_parent_state(
        parent_state,
        axis=axis,
        region=region,
        mask_type=mask_type,
    )
    if compatible_state is not None:
        render_base = str(compatible_state.get("anchor_image_path") or default_base)
        parameters = _canonical_parameters(compatible_state.get("render_parameters"))
        current = _finite_number(compatible_state.get("next_value"))
        if current is None:
            current = policy.neutral
        anchor_edit_id = _optional_text(compatible_state.get("anchor_edit_id"))
    else:
        render_base = default_base
        parameters = _canonical_parameters(None)
        current = policy.neutral
        anchor_edit_id = _record_text(parent_record, "edit_id")
    if relative_delta is not None:
        value = current + relative_delta
    if axis == "saturation" and value <= EDIT_PARAMETER_RANGES[axis][0]:
        value = EDIT_PARAMETER_RANGES[axis][0]
    else:
        value = _quantize(policy, value)
    if math.isclose(value, current, abs_tol=policy.quantum / 10):
        raise AdaptiveStepConverged(
            f"{_axis_label(axis)}已是要求的 {_plain(value)}；未新增零變化版本。"
        )
    parameters[axis] = value
    state = _state_snapshot(
        policy=policy,
        episode_id=_episode_id(render_base, axis, region, mask_type),
        anchor_edit_id=anchor_edit_id,
        anchor_image_path=render_base,
        region=region,
        mask_type=mask_type,
        current_value=current,
        next_value=value,
        lower_bound=None,
        upper_bound=None,
        previous_direction=1 if value >= current else -1,
        base_step=max(policy.quantum, _distance(policy, policy.neutral, value)),
        step_before=_distance(policy, current, value),
        step_after=_distance(policy, current, value),
        refinement_round=0,
        reversal_count=0,
        render_parameters=parameters,
        active=False,
        converged=False,
    )
    adaptive = _adaptive_metadata(
        state=state,
        applied=False,
        reason=(
            "relative_numeric_reset"
            if relative_delta is not None
            else "absolute_value_reset"
        ),
        relation="relative_numeric" if relative_delta is not None else "absolute",
        confidence="high",
        region_source=(
            "explicit"
            if explicit_region is not None
            else "inherited"
            if compatible_state is not None
            else "default"
        ),
        direction=int(state["previous_direction"]),
        bounds_before={"lower": None, "upper": None},
    )
    explanation = (
        f"已依相對數值將{_axis_label(axis)}調整 {_signed(relative_delta)}，"
        f"候選值為 {_signed(value)}，並重設先前區間。"
        if relative_delta is not None
        else f"已依明確數值將{_axis_label(axis)}設為 {_signed(value)}，並重設先前區間。"
    )
    prepared = _prepare_result(result, prompt, parameters, region, mask_type, adaptive)
    return AdaptiveAdjustment(prepared, adaptive, render_base, explanation)


def _build_axis_reset(
    *,
    result: dict[str, Any],
    prompt: str,
    policy: AxisPolicy,
    region: str,
    mask_type: str,
    region_source: str,
    parent_record: Mapping[str, Any] | None,
    parent_state: Mapping[str, Any] | None,
    default_base: str,
) -> AdaptiveAdjustment:
    compatible_state = _compatible_parent_state(
        parent_state,
        axis=policy.axis,
        region=region,
        mask_type=mask_type,
    )
    if compatible_state is None:
        raise AdaptiveStepConverged(
            f"{region} 的{_axis_label(policy.axis)}目前沒有可重設的自適應調整；未新增零變化版本。"
        )
    render_base = str(compatible_state.get("anchor_image_path") or default_base)
    parameters = _canonical_parameters(compatible_state.get("render_parameters"))
    current = _finite_number(compatible_state.get("next_value"))
    if current is None:
        current = policy.neutral
    parameters[policy.axis] = policy.neutral
    state = _state_snapshot(
        policy=policy,
        episode_id=_episode_id(render_base, policy.axis, region, mask_type),
        anchor_edit_id=_optional_text(compatible_state.get("anchor_edit_id")),
        anchor_image_path=render_base,
        region=region,
        mask_type=mask_type,
        current_value=current,
        next_value=policy.neutral,
        lower_bound=None,
        upper_bound=None,
        previous_direction=0,
        base_step=policy.quantum,
        step_before=_distance(policy, current, policy.neutral),
        step_after=_distance(policy, current, policy.neutral),
        refinement_round=0,
        reversal_count=0,
        render_parameters=parameters,
        active=False,
        converged=True,
    )
    adaptive = _adaptive_metadata(
        state=state,
        applied=False,
        reason="axis_reset",
        relation="reset",
        confidence="high",
        region_source=region_source,
        direction=0,
        bounds_before={"lower": None, "upper": None},
    )
    explanation = f"已將{_axis_label(policy.axis)}重設為中性值並清除先前區間。"
    prepared = _prepare_result(result, prompt, parameters, region, mask_type, adaptive)
    return AdaptiveAdjustment(prepared, adaptive, render_base, explanation)


def _build_global_reset(
    result: dict[str, Any],
    text: str,
    parent_record: Mapping[str, Any] | None,
    default_base: str,
) -> AdaptiveAdjustment:
    del text
    if parent_record is None:
        raise AdaptiveStepConverged(
            "目前已是原圖起點；未新增零變化版本。"
        )
    render_base = _record_text(parent_record, "original_image_path") or default_base
    parent_result = _record_text(parent_record, "result_image_path")
    parent_adaptive = parent_record.get("adaptive")
    if (
        parent_result == render_base
        or isinstance(parent_adaptive, Mapping)
        and parent_adaptive.get("reason") == "global_reset"
    ):
        raise AdaptiveStepConverged(
            "目前版本已是原圖；未新增零變化版本。"
        )
    parameters = _canonical_parameters(None)
    state = {
        "active": False,
        "schema_version": ADAPTIVE_SCHEMA_VERSION,
        "policy_version": ADAPTIVE_POLICY_VERSION,
        "episode_id": _episode_id(render_base, "reset", "all", "none"),
        "axis": None,
        "region": "all",
        "mask_type": "none",
        "scope_key": f"reset:all:none:{render_base}",
        "anchor_edit_id": None,
        "converged": True,
        "anchor_image_path": render_base,
        "current_value": None,
        "next_value": None,
        "current_candidate": None,
        "lower_bound": None,
        "upper_bound": None,
        "previous_direction": 0,
        "base_step": None,
        "step_before": None,
        "step_after": None,
        "refinement_round": 0,
        "reversal_count": 0,
        "render_parameters": parameters,
    }
    adaptive = {
        "schema_version": ADAPTIVE_SCHEMA_VERSION,
        "policy_version": ADAPTIVE_POLICY_VERSION,
        "applied": False,
        "reason": "global_reset",
        "relation": "reset",
        "confidence": "high",
        "axis": None,
        "direction": 0,
        "region": "all",
        "mask_type": "none",
        "region_source": "reset",
        "episode_id": state["episode_id"],
        "anchor_edit_id": None,
        "anchor_image_path": render_base,
        "current_value": None,
        "next_value": None,
        "delta_from_parent": None,
        "lower_bound": None,
        "upper_bound": None,
        "bounds_before": {"lower": None, "upper": None},
        "step_before": None,
        "step_after": None,
        "refinement_round": 0,
        "reversal_count": 0,
        "converged": True,
        "state": state,
        "render_parameters": parameters,
    }
    prepared = _prepare_result(result, result.get("prompt") or "", parameters, "all", "none", adaptive)
    return AdaptiveAdjustment(
        prepared,
        adaptive,
        render_base,
        "已回到原圖並清除先前的自適應區間。",
    )


def _prepare_result(
    result: dict[str, Any],
    prompt: str,
    parameters: Mapping[str, Any],
    region: str,
    mask_type: str,
    adaptive: Mapping[str, Any],
) -> dict[str, Any]:
    original_plan = result.get("edit_plan")
    relation = str(adaptive.get("relation") or "")
    if (
        isinstance(original_plan, Mapping)
        and original_plan.get("type") == "edits"
        and relation not in {"absolute", "relative_numeric", "reset"}
    ):
        plan = copy.deepcopy(dict(original_plan))
        plan["region"] = region
        plan["mask_type"] = mask_type
    else:
        plan = build_raw_parameter_edit_plan(
            prompt=prompt,
            parameters=parameters,
            region=region,
            mask_type=mask_type,
        )
        if isinstance(original_plan, Mapping):
            plan["semantic_edits"] = copy.deepcopy(original_plan.get("edits") or [])
    plan["adaptation"] = copy.deepcopy(dict(adaptive))
    result["edit_plan"] = plan
    result["parameters"] = build_engine_parameters("opencv", plan)
    axis = str(adaptive.get("axis") or "")
    direction = int(adaptive.get("direction") or 0)
    policy = _AXIS_POLICIES.get(axis)
    if policy is not None and direction != 0:
        result["resolved_intent"] = (
            policy.positive_intent if direction > 0 else policy.negative_intent
        )
    elif adaptive.get("reason") == "global_reset":
        result["resolved_intent"] = "reset_to_original"
    elif adaptive.get("relation") == "reset":
        result["resolved_intent"] = f"reset_{axis}"
    result["preset_name"] = None
    return result


def _state_snapshot(
    *,
    policy: AxisPolicy,
    episode_id: str,
    anchor_edit_id: str | None,
    anchor_image_path: str,
    region: str,
    mask_type: str,
    current_value: float,
    next_value: float,
    lower_bound: float | None,
    upper_bound: float | None,
    previous_direction: int,
    base_step: float,
    step_before: float,
    step_after: float,
    refinement_round: int,
    reversal_count: int,
    render_parameters: Mapping[str, Any],
    active: bool,
    converged: bool,
) -> dict[str, Any]:
    return {
        "active": active,
        "schema_version": ADAPTIVE_SCHEMA_VERSION,
        "policy_version": ADAPTIVE_POLICY_VERSION,
        "episode_id": episode_id,
        "axis": policy.axis,
        "region": region,
        "mask_type": mask_type,
        "scope_key": f"{policy.axis}:{region}:{mask_type}:{episode_id}",
        "anchor_edit_id": anchor_edit_id,
        "anchor_image_path": anchor_image_path,
        "current_value": _clean(current_value),
        "next_value": _clean(next_value),
        "current_candidate": _clean(next_value),
        "lower_bound": _clean_optional(lower_bound),
        "upper_bound": _clean_optional(upper_bound),
        "previous_direction": previous_direction,
        "base_step": _clean(base_step),
        "step_before": _clean(step_before),
        "step_after": _clean(step_after),
        "refinement_round": refinement_round,
        "reversal_count": reversal_count,
        "render_parameters": dict(render_parameters),
        "converged": converged,
    }


def _adaptive_metadata(
    *,
    state: Mapping[str, Any],
    applied: bool,
    reason: str,
    relation: str,
    confidence: str,
    region_source: str,
    direction: int,
    bounds_before: Mapping[str, Any],
) -> dict[str, Any]:
    current = _finite_number(state.get("current_value"))
    next_value = _finite_number(state.get("next_value"))
    delta = None if current is None or next_value is None else _clean(next_value - current)
    return {
        "schema_version": ADAPTIVE_SCHEMA_VERSION,
        "policy_version": ADAPTIVE_POLICY_VERSION,
        "applied": applied,
        "reason": reason,
        "relation": relation,
        "confidence": confidence,
        "axis": state.get("axis"),
        "direction": direction,
        "region": state.get("region"),
        "mask_type": state.get("mask_type"),
        "region_source": region_source,
        "episode_id": state.get("episode_id"),
        "anchor_edit_id": state.get("anchor_edit_id"),
        "anchor_image_path": state.get("anchor_image_path"),
        "current_value": current,
        "next_value": next_value,
        "delta_from_parent": delta,
        "lower_bound": state.get("lower_bound"),
        "upper_bound": state.get("upper_bound"),
        "bounds_before": dict(bounds_before),
        "step_before": state.get("step_before"),
        "step_after": state.get("step_after"),
        "refinement_round": state.get("refinement_round"),
        "reversal_count": state.get("reversal_count"),
        "converged": bool(state.get("converged")),
        "render_parameters": dict(state.get("render_parameters") or {}),
        "state": dict(state),
    }


def _adaptive_explanation(adaptive: Mapping[str, Any]) -> str:
    axis = str(adaptive.get("axis") or "")
    delta = _finite_number(adaptive.get("delta_from_parent")) or 0.0
    candidate = _finite_number(adaptive.get("next_value")) or 0.0
    lower = adaptive.get("lower_bound")
    upper = adaptive.get("upper_bound")
    if lower is not None and upper is not None:
        bracket = f"，目前區間 {_plain(lower)}～{_plain(upper)}"
    else:
        bracket = "，目前仍在單向探索"
    return (
        f"理解為延續{_axis_label(axis)}微調：本次 {_signed(delta)}，"
        f"相對錨點候選總值 {_signed(candidate)}{bracket}。"
    )


def _read_parent_snapshot(
    parent_record: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(parent_record, Mapping):
        return None
    adaptive = parent_record.get("adaptive")
    if not isinstance(adaptive, Mapping):
        plan = parent_record.get("edit_plan")
        if isinstance(plan, Mapping):
            adaptive = plan.get("adaptation")
    if not isinstance(adaptive, Mapping):
        return None
    state = adaptive.get("state")
    if not isinstance(state, Mapping):
        state = adaptive
    axis = str(state.get("axis") or "")
    if (
        axis not in _AXIS_POLICIES
        or state.get("schema_version") != ADAPTIVE_SCHEMA_VERSION
        or state.get("policy_version") != ADAPTIVE_POLICY_VERSION
        or not state.get("anchor_image_path")
    ):
        return None
    return copy.deepcopy(dict(state))


def _compatible_parent_state(
    state: Mapping[str, Any] | None,
    *,
    axis: str,
    region: str,
    mask_type: str,
) -> dict[str, Any] | None:
    if not isinstance(state, Mapping):
        return None
    if (
        state.get("axis") != axis
        or validate_edit_region(state.get("region")) != region
        or validate_edit_mask_type(state.get("mask_type")) != mask_type
        or state.get("policy_version") != ADAPTIVE_POLICY_VERSION
    ):
        return None
    current = _finite_number(state.get("next_value"))
    if current is None:
        return None
    return copy.deepcopy(dict(state))


def _canonical_parameters(value: Any) -> dict[str, Any]:
    parameters: dict[str, Any] = dict(NEUTRAL_OPENCV_PARAMETERS)
    if isinstance(value, Mapping):
        parameters.update(validate_edit_parameters(value))
    return parameters


def _detect_explicit_regions(text: str) -> list[str]:
    regions: list[str] = []
    for region, keywords in _EXPLICIT_REGION_KEYWORDS:
        if (
            region == "highlights"
            and _contains(text, _OVEREXPOSURE_GUARDS)
            and not _contains(text, ("highlight", "highlights", "高光", "亮部"))
        ):
            continue
        if _contains(text, keywords):
            regions.append(region)
    if re.search(
        r"(?:^|把|將|照片中的|畫面中的|圖中的|裡的|[\s，,。；;])"
        r"人(?=.{0,3}(?:再|更|亮|暗|暖|冷|鮮|自然|調|變))",
        text,
    ):
        regions.append("person")
    return list(dict.fromkeys(regions))


def _detect_explicit_numerics(text: str) -> list[tuple[str, float]]:
    matches: list[tuple[str, float]] = []
    for axis, pattern in _NUMERIC_PATTERNS.items():
        match = pattern.search(text)
        if match:
            matches.append((axis, float(match.group(1))))
    return matches


def _without_numeric_expressions(text: str) -> str:
    """Remove handled numeric clauses before looking for an extra action."""

    remainder = text
    for increase_pattern, decrease_pattern in _RELATIVE_NUMERIC_PATTERNS.values():
        remainder = increase_pattern.sub(" ", remainder)
        remainder = decrease_pattern.sub(" ", remainder)
    for pattern in _NUMERIC_PATTERNS.values():
        remainder = pattern.sub(" ", remainder)
    return remainder


def _detect_relative_numerics(text: str) -> list[tuple[str, float]]:
    matches: list[tuple[str, float]] = []
    for axis, (increase_pattern, decrease_pattern) in (
        _RELATIVE_NUMERIC_PATTERNS.items()
    ):
        increase = increase_pattern.search(text)
        decrease = decrease_pattern.search(text)
        if increase is not None:
            matches.append((axis, abs(float(increase.group(1)))))
        if decrease is not None:
            matches.append((axis, -abs(float(decrease.group(1)))))
    return matches


def _detect_axis_resets(text: str) -> list[str]:
    labels_by_axis = {
        "brightness": ("亮度", "brightness"),
        "temperature": ("色溫", "temperature"),
        "saturation": ("飽和", "saturation"),
    }
    if not _contains_unnegated(text, _AXIS_RESET_MARKERS):
        return []
    return [
        axis
        for axis, labels in labels_by_axis.items()
        if _contains(text, labels)
    ]


def _episode_id(anchor: str, axis: str, region: str, mask_type: str) -> str:
    digest = hashlib.sha256(
        f"{anchor}|{axis}|{region}|{mask_type}".encode("utf-8")
    ).hexdigest()[:16]
    return f"episode_{digest}"


def _midpoint(policy: AxisPolicy, lower: float, upper: float) -> float:
    return _from_coordinate(policy, (_coordinate(policy, lower) + _coordinate(policy, upper)) / 2)


def _advance(policy: AxisPolicy, current: float, direction: int, step: float) -> float:
    coordinate = _coordinate(policy, current) + direction * step
    return _from_coordinate(policy, coordinate)


def _distance(policy: AxisPolicy, left: float, right: float) -> float:
    return abs(_coordinate(policy, right) - _coordinate(policy, left))


def _coordinate(policy: AxisPolicy, value: float) -> float:
    if policy.transform == "log":
        return math.log(max(value, policy.minimum))
    return value


def _from_coordinate(policy: AxisPolicy, value: float) -> float:
    if policy.transform == "log":
        return math.exp(value)
    return value


def _quantize(policy: AxisPolicy, value: float) -> float:
    bounded = min(max(float(value), policy.minimum), policy.maximum)
    steps = round((bounded - policy.minimum) / policy.quantum)
    return _clean(policy.minimum + steps * policy.quantum)


def _bounded_optional(policy: AxisPolicy, value: Any) -> float | None:
    number = _finite_number(value)
    return None if number is None else _quantize(policy, number)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clean(value: float) -> float:
    return round(float(value), 4)


def _clean_optional(value: float | None) -> float | None:
    return None if value is None else _clean(value)


def _record_text(record: Mapping[str, Any] | None, key: str) -> str | None:
    if not isinstance(record, Mapping):
        return None
    return _optional_text(record.get(key))


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _axis_label(axis: str) -> str:
    return {"brightness": "亮度", "temperature": "色溫", "saturation": "飽和度"}.get(axis, axis)


def _plain(value: Any) -> str:
    number = float(value)
    return f"{number:g}"


def _signed(value: float) -> str:
    return f"{value:+g}"


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _contains_unnegated(text: str, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        start = text.find(marker)
        while start >= 0:
            prefix = text[max(0, start - 12) : start]
            if not any(prefix.endswith(negation) for negation in _NEGATION_PREFIXES):
                return True
            start = text.find(marker, start + 1)
    return False
