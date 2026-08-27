from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass
from typing import Any, Mapping

from app.services.adaptive_policy import (
    ADAPTIVE_AXIS_ORDER,
    AXIS_POLICIES,
    AXIS_POLICY_VERSION,
    AxisPolicy,
    active_quantize,
    advance,
    coordinate,
    distance,
    from_coordinate,
    midpoint,
    policy_registry_payload,
    quantize,
    seed_distance,
)
from app.services.adaptive_prompt_compiler import (
    AdaptiveCompileError,
    compile_adaptive_request,
    semantic_attempt_is_authoritative_rejection,
    semantic_attempt_is_released,
)
from app.services.edit_engines import build_engine_parameters
from app.services.edit_plan import (
    build_compound_edit_plan,
    build_raw_parameter_edit_plan,
)
from app.services.edit_schema import (
    MANUAL_PARAMETER_KEYS,
    default_mask_type_for_region,
    require_region_mask_pair,
    validate_edit_mask_type,
    validate_edit_parameters,
    validate_edit_region,
)
from app.services.opencv_parameter_mapper import get_opencv_template_adjustments
from app.services.prompt_parser import parse_edit_prompt
from app.services.semantic_parser import (
    SemanticParseAttempt,
    parse_semantic_prompt,
)


ADAPTIVE_SCHEMA_VERSION_V2 = "adaptive_prompt_v2"
ADAPTIVE_POLICY_VERSION_V2 = "multi_axis_bounded_controller_v2"
LEGACY_SCHEMA_VERSION = "adaptive_prompt_v1"
LEGACY_POLICY_VERSION = "bounded_bisection_v1"
MAX_REFINEMENT_ROUNDS = 12


@dataclass(frozen=True)
class AdaptiveV2Resolution:
    prompt_result: dict[str, Any]
    adaptive: dict[str, Any] | None
    render_base_image_path: str
    explanation: str | None = None


@dataclass(frozen=True)
class AdaptiveSemanticPreflight:
    semantic_attempt: SemanticParseAttempt | None
    prompt_result: dict[str, Any] | None
    bypass_intent_resolver: bool


@dataclass
class AdaptiveV2Error(ValueError):
    code: str
    message: str
    status_code: int
    issues: tuple[dict[str, Any], ...] = ()

    def __str__(self) -> str:
        return self.message


def resolve_adaptive_v2(
    *,
    prompt_result: Mapping[str, Any],
    prompt: str,
    parent_record: Mapping[str, Any] | None,
    default_base_image_path: str,
    engine_name: str,
    semantic_attempt: SemanticParseAttempt | None = None,
) -> AdaptiveV2Resolution:
    result = copy.deepcopy(dict(prompt_result))
    default_base = str(default_base_image_path or "")
    if str(engine_name or "").strip().lower() != "opencv":
        return AdaptiveV2Resolution(result, None, default_base)

    parent_snapshot = read_parent_snapshot(parent_record)
    attempt = semantic_attempt
    released_semantic = semantic_attempt_is_released(attempt)
    authoritative_rejection = semantic_attempt_is_authoritative_rejection(
        attempt
    )
    deterministic_result = (
        {}
        if released_semantic or authoritative_rejection
        else parse_edit_prompt(prompt)
    )
    try:
        compiled = compile_adaptive_request(
            prompt=prompt,
            deterministic_result=deterministic_result,
            parent_snapshot=parent_snapshot,
            semantic_attempt=attempt,
        )
    except AdaptiveCompileError as exc:
        raise AdaptiveV2Error(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            issues=exc.issues,
        ) from exc

    semantic_ir = compiled.get("semantic_ir")
    if isinstance(semantic_ir, Mapping):
        result["parser_source"] = "semantic_registry"
        result["fallback_reason"] = None
        result["semantic_ir"] = copy.deepcopy(dict(semantic_ir))
        result["semantic_parser_version"] = compiled.get(
            "semantic_parser_version"
        )
        result["semantic_decision_source"] = compiled.get(
            "semantic_decision_source"
        )

    kind = str(compiled.get("kind") or "bypass")
    if kind == "satisfied":
        raise AdaptiveV2Error(
            code="adaptive_feedback_satisfied",
            message="已理解目前效果剛好；未新增零變化版本。",
            status_code=409,
        )
    if kind == "global_reset":
        return _global_reset(
            result=_safe_rule_result(result, deterministic_result),
            prompt=prompt,
            parent_record=parent_record,
            default_base=default_base,
        )
    if kind == "bypass":
        return AdaptiveV2Resolution(
            _safe_rule_result(result, deterministic_result),
            None,
            default_base,
        )

    operations = copy.deepcopy(list(compiled.get("operations") or []))
    try:
        region, mask_type = require_region_mask_pair(
            compiled.get("region"),
            compiled.get("mask_type"),
        )
    except ValueError as exc:
        raise AdaptiveV2Error(
            code="adaptive_region_contract_invalid",
            message=(
                "Compiled edit region/mask metadata is invalid; no edit was "
                "rendered."
            ),
            status_code=422,
            issues=(
                {
                    "reason": "invalid_region_mask_contract",
                    "detail": str(exc),
                },
            ),
        ) from exc
    region_source = str(compiled.get("region_source") or "default")

    vector = _prepare_parent_vector(
        parent_snapshot=parent_snapshot,
        parent_record=parent_record,
        default_base=default_base,
        region=region,
        mask_type=mask_type,
        operations=operations,
    )
    anchor_path = str(vector["anchor_image_path"])
    anchor_edit_id = _optional_text(vector.get("anchor_edit_id"))
    parameters = _canonical_parameters(vector.get("render_parameters"))
    axes = copy.deepcopy(dict(vector.get("axes") or {}))
    ledger = copy.deepcopy(list(vector.get("contribution_ledger") or []))
    parent_axes = copy.deepcopy(axes)
    requested_axes = [str(operation["axis"]) for operation in operations]
    transitioned: list[dict[str, Any]] = []

    for operation in operations:
        axis = str(operation["axis"])
        policy = AXIS_POLICIES[axis]
        existing_key, existing_state = _find_axis_state(
            axes,
            axis=axis,
            region=region,
            mask_type=mask_type,
        )
        current = _finite(parameters.get(axis))
        if current is None:
            current = policy.neutral
        transition = _transition_axis(
            policy=policy,
            operation=operation,
            current=current,
            existing_state=existing_state,
            anchor_image_path=anchor_path,
            anchor_edit_id=anchor_edit_id,
            region=region,
            mask_type=mask_type,
        )
        if existing_key is not None and existing_key != transition["scope_key"]:
            axes.pop(existing_key, None)
        axes[str(transition["scope_key"])] = transition
        parameters[axis] = float(transition["next_value"])
        transitioned.append(
            _operation_metadata(operation, transition)
        )

    ledger = _update_contribution_ledger(
        ledger=ledger,
        operations=operations,
        transitions=transitioned,
        parameters=parameters,
        axes=axes,
    )
    parameters = _compile_render_parameters(
        base_parameters=parameters,
        axes=axes,
        ledger=ledger,
        requested_axes=set(requested_axes),
    )
    for operation_meta in transitioned:
        axis = str(operation_meta["axis"])
        operation_meta["next_value"] = parameters[axis]
        operation_meta["delta_from_parent"] = _clean(
            parameters[axis] - float(operation_meta["current_value"])
        )

    ledger = _annotate_ledger_merge(ledger, parameters, axes)
    state = _vector_state(
        anchor_image_path=anchor_path,
        anchor_edit_id=anchor_edit_id,
        region=region,
        mask_type=mask_type,
        axes=axes,
        render_parameters=parameters,
        operations=transitioned,
    )
    unchanged_axes = [
        axis
        for axis in _axes_in_state(parent_axes)
        if axis not in set(requested_axes)
    ]
    adaptive = _adaptive_metadata(
        state=state,
        operations=transitioned,
        ledger=ledger,
        requested_axes=requested_axes,
        unchanged_axes=unchanged_axes,
        region_source=region_source,
        migration=vector.get("migration"),
    )
    if isinstance(semantic_ir, Mapping):
        adaptive["semantic_ir"] = copy.deepcopy(dict(semantic_ir))
        adaptive["semantic_parser_version"] = compiled.get(
            "semantic_parser_version"
        )
        adaptive["semantic_decision_source"] = compiled.get(
            "semantic_decision_source"
        )
    prepared = _prepare_prompt_result(
        result=result,
        deterministic_result=deterministic_result,
        prompt=prompt,
        operations=operations,
        parameters=parameters,
        region=region,
        mask_type=mask_type,
        adaptive=adaptive,
    )
    return AdaptiveV2Resolution(
        prompt_result=prepared,
        adaptive=adaptive,
        render_base_image_path=anchor_path,
        explanation=_adaptive_explanation(adaptive),
    )


def preflight_adaptive_semantic_prompt(
    *,
    prompt: str,
    engine_name: str,
) -> AdaptiveSemanticPreflight:
    """Classify one prompt before any legacy intent resolver can run."""

    if str(engine_name or "").strip().lower() != "opencv":
        return AdaptiveSemanticPreflight(
            semantic_attempt=None,
            prompt_result=None,
            bypass_intent_resolver=False,
        )
    attempt = parse_semantic_prompt(str(prompt or "").strip())
    bypass = (
        semantic_attempt_is_released(attempt)
        or semantic_attempt_is_authoritative_rejection(attempt)
    )
    if not bypass:
        return AdaptiveSemanticPreflight(
            semantic_attempt=attempt,
            prompt_result=None,
            bypass_intent_resolver=False,
        )
    semantic_ir = attempt.accepted_ir
    prompt_result: dict[str, Any] = {
        "prompt": str(prompt or "").strip(),
        "parser_source": "semantic_registry",
        "fallback_reason": None,
    }
    if semantic_ir is not None:
        prompt_result["semantic_ir"] = semantic_ir.as_dict()
        prompt_result["semantic_parser_version"] = semantic_ir.parser_version
        prompt_result["semantic_decision_source"] = semantic_ir.decision_source
    return AdaptiveSemanticPreflight(
        semantic_attempt=attempt,
        prompt_result=prompt_result,
        bypass_intent_resolver=True,
    )


def build_released_semantic_prompt_seed(
    *,
    prompt: str,
    parent_record: Mapping[str, Any] | None,
    engine_name: str,
) -> dict[str, Any] | None:
    """Backward-compatible seed helper for callers not passing the attempt."""

    del parent_record
    preflight = preflight_adaptive_semantic_prompt(
        prompt=prompt,
        engine_name=engine_name,
    )
    return preflight.prompt_result if preflight.bypass_intent_resolver else None


def read_parent_snapshot(
    parent_record: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(parent_record, Mapping):
        return None
    adaptive = parent_record.get("adaptive")
    if not isinstance(adaptive, Mapping):
        plan = parent_record.get("edit_plan")
        adaptive = plan.get("adaptation") if isinstance(plan, Mapping) else None
    if not isinstance(adaptive, Mapping):
        return None

    schema = str(adaptive.get("schema_version") or "")
    if schema == ADAPTIVE_SCHEMA_VERSION_V2:
        return _normalize_v2_snapshot(adaptive)
    if schema == LEGACY_SCHEMA_VERSION:
        return _migrate_v1_snapshot(adaptive)
    return None


def _normalize_v2_snapshot(adaptive: Mapping[str, Any]) -> dict[str, Any] | None:
    state_value = adaptive.get("state")
    if not isinstance(state_value, Mapping):
        return None
    state = copy.deepcopy(dict(state_value))
    if (
        state.get("schema_version") != ADAPTIVE_SCHEMA_VERSION_V2
        or state.get("policy_version") != ADAPTIVE_POLICY_VERSION_V2
        or not state.get("anchor_image_path")
        or not isinstance(state.get("active"), bool)
        or not isinstance(state.get("converged"), bool)
    ):
        return None
    raw_region = str(state.get("region") or "").strip().lower()
    raw_mask_type = str(state.get("mask_type") or "").strip().lower()
    region = validate_edit_region(raw_region)
    mask_type = validate_edit_mask_type(raw_mask_type)
    if raw_region != region or raw_mask_type != mask_type:
        return None
    axes_value = state.get("axes")
    axes: dict[str, dict[str, Any]] = {}
    seen_axes: set[str] = set()
    if not isinstance(axes_value, Mapping):
        return None
    if isinstance(axes_value, Mapping):
        for raw_scope_key, axis_value in axes_value.items():
            if not isinstance(axis_value, Mapping):
                return None
            axis_state = copy.deepcopy(dict(axis_value))
            axis = str(axis_state.get("axis") or "")
            policy = AXIS_POLICIES.get(axis)
            if (
                policy is None
                or axis_state.get("policy_version") != policy.policy_version
                or str(axis_state.get("region") or "").strip().lower()
                != region
                or str(axis_state.get("mask_type") or "").strip().lower()
                != mask_type
                or not isinstance(axis_state.get("active"), bool)
                or not isinstance(axis_state.get("converged"), bool)
                or str(axis_state.get("anchor_image_path") or "")
                != str(state["anchor_image_path"])
            ):
                return None
            next_value = _finite(axis_state.get("next_value"))
            if (
                next_value is None
                or next_value < policy.minimum
                or next_value > policy.maximum
            ):
                return None
            if axis in seen_axes:
                # Multiple states for one public axis make lookup/render order
                # ambiguous.  Ignore the whole snapshot and start safely from
                # the visible parent instead of selecting one by dict order.
                return None
            scope_key = _axis_scope_key(axis, region, mask_type)
            if (
                str(raw_scope_key) != scope_key
                or str(axis_state.get("scope_key") or "") != scope_key
            ):
                return None
            # Validate the persisted axis payload before legacy scalar mirrors
            # are applied.  Otherwise a valid top-level mirror can silently
            # overwrite corrupt nested bounds or candidates and revive an
            # unsafe snapshot.
            if not _valid_snapshot_axis_state(axis_state, policy):
                return None
            seen_axes.add(axis)
            axis_state["scope_key"] = scope_key
            axis_state["next_value"] = quantize(policy, next_value)
            axis_state["current_candidate"] = axis_state["next_value"]
            axes[scope_key] = axis_state

    # v1 tests and the first Flutter implementation edited/read scalar mirrors.
    # For a one-axis v2 state, honour those mirrors when they are present.
    if len(axes) == 1:
        key, axis_state = next(iter(axes.items()))
        for name in (
            "active",
            "current_value",
            "next_value",
            "current_candidate",
            "lower_bound",
            "upper_bound",
            "previous_direction",
            "base_step",
            "step_before",
            "step_after",
            "refinement_round",
            "reversal_count",
            "converged",
        ):
            if name not in state:
                continue
            raw = state[name]
            if name in {"active", "converged"}:
                if not isinstance(raw, bool):
                    return None
                axis_state[name] = raw
            elif name in {"refinement_round", "reversal_count"}:
                try:
                    axis_state[name] = max(0, int(raw))
                except (TypeError, ValueError):
                    return None
            elif name == "previous_direction":
                try:
                    direction = int(raw)
                except (TypeError, ValueError):
                    return None
                if direction not in {-1, 0, 1}:
                    return None
                axis_state[name] = direction
            else:
                if raw is None and name in {"lower_bound", "upper_bound"}:
                    axis_state[name] = None
                    continue
                number = _finite(raw)
                if number is None:
                    return None
                axis_state[name] = number
        axes[key] = axis_state

    for axis_state in axes.values():
        policy = AXIS_POLICIES[str(axis_state["axis"])]
        if not _valid_snapshot_axis_state(axis_state, policy):
            return None

    parameters = _strict_snapshot_parameters(state.get("render_parameters"))
    if parameters is None:
        return None
    for axis_state in axes.values():
        axis = str(axis_state["axis"])
        policy = AXIS_POLICIES[axis]
        if not math.isclose(
            parameters[axis],
            float(axis_state["next_value"]),
            abs_tol=policy.quantum / 10,
        ):
            return None

    ledger = _normalize_contribution_ledger(
        adaptive.get("contribution_ledger")
    )
    if ledger is None:
        return None

    state["axes"] = axes
    state["render_parameters"] = parameters
    snapshot = copy.deepcopy(dict(adaptive))
    snapshot["state"] = state
    snapshot["render_parameters"] = copy.deepcopy(state["render_parameters"])
    snapshot["contribution_ledger"] = ledger
    return snapshot


def _valid_snapshot_axis_state(
    axis_state: Mapping[str, Any],
    policy: AxisPolicy,
) -> bool:
    if (
        not isinstance(axis_state.get("active"), bool)
        or not isinstance(axis_state.get("converged"), bool)
    ):
        return False
    for name in ("current_value", "next_value", "current_candidate"):
        value = _finite(axis_state.get(name))
        if value is None or value < policy.minimum or value > policy.maximum:
            return False
    lower = _finite(axis_state.get("lower_bound"))
    upper = _finite(axis_state.get("upper_bound"))
    if axis_state.get("lower_bound") is not None and lower is None:
        return False
    if axis_state.get("upper_bound") is not None and upper is None:
        return False
    if lower is not None and not (policy.minimum <= lower <= policy.maximum):
        return False
    if upper is not None and not (policy.minimum <= upper <= policy.maximum):
        return False
    if lower is not None and upper is not None and lower > upper:
        return False
    next_value = float(axis_state["next_value"])
    if lower is not None and next_value < lower - policy.quantum / 10:
        return False
    if upper is not None and next_value > upper + policy.quantum / 10:
        return False
    for name in ("base_step", "step_before", "step_after"):
        value = _finite(axis_state.get(name))
        if value is None or value < 0:
            return False
    if not math.isclose(
        float(axis_state["next_value"]),
        float(axis_state["current_candidate"]),
        abs_tol=policy.quantum / 10,
    ):
        return False
    for name in ("refinement_round", "reversal_count"):
        value = axis_state.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        if value < 0:
            return False
    direction_value = axis_state.get("previous_direction")
    if isinstance(direction_value, bool) or not isinstance(direction_value, int):
        return False
    return direction_value in {-1, 0, 1}


def _normalize_contribution_ledger(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    normalized: dict[str, dict[str, Any]] = {}
    allowed_roles = {"primary", "companion", "legacy_companion"}
    for raw in value:
        if not isinstance(raw, Mapping):
            return None
        axis = str(raw.get("axis") or "")
        role = str(raw.get("role") or "")
        group_id = str(raw.get("group_id") or "").strip()
        source_intent = str(raw.get("source_intent") or "").strip()
        policy = AXIS_POLICIES.get(axis)
        target = _finite(raw.get("proposed_value"))
        if policy is None:
            # Unknown future/foreign axes cannot affect rendering and are
            # safely discarded for compatibility.
            continue
        if (
            role not in allowed_roles
            or not group_id
            or not source_intent
            or target is None
            or target < policy.minimum
            or target > policy.maximum
            or not math.isclose(
                target,
                quantize(policy, target),
                abs_tol=policy.quantum / 10,
            )
            or (
                "suppressed" in raw
                and not isinstance(raw.get("suppressed"), bool)
            )
        ):
            return None
        entry = copy.deepcopy(dict(raw))
        entry["axis"] = axis
        entry["role"] = role
        entry["group_id"] = group_id
        entry["source_intent"] = source_intent
        entry["proposed_value"] = quantize(policy, target)
        contribution_id = str(entry.get("contribution_id") or "").strip()
        if not contribution_id:
            contribution_id = _stable_id(
                "contribution", group_id, source_intent, role, axis
            )
        if contribution_id in normalized:
            return None
        entry["contribution_id"] = contribution_id
        entry["suppressed"] = bool(entry.get("suppressed"))
        normalized[contribution_id] = entry
    result = list(normalized.values())
    result.sort(
        key=lambda item: (
            ADAPTIVE_AXIS_ORDER.index(str(item["axis"])),
            str(item["contribution_id"]),
        )
    )
    return result


def _migrate_v1_snapshot(adaptive: Mapping[str, Any]) -> dict[str, Any] | None:
    state_value = adaptive.get("state")
    state = state_value if isinstance(state_value, Mapping) else adaptive
    axis = str(state.get("axis") or "")
    policy = AXIS_POLICIES.get(axis)
    if (
        policy is None
        or state.get("schema_version") != LEGACY_SCHEMA_VERSION
        or state.get("policy_version") != LEGACY_POLICY_VERSION
        or not state.get("anchor_image_path")
    ):
        return None
    region = validate_edit_region(state.get("region"))
    mask_type = validate_edit_mask_type(state.get("mask_type"))
    if mask_type == "none":
        mask_type = default_mask_type_for_region(region)
    render_parameters = _canonical_parameters(
        state.get("render_parameters") or adaptive.get("render_parameters")
    )
    current = _finite(state.get("current_value"))
    next_value = _finite(state.get("next_value"))
    if next_value is None:
        next_value = _finite(state.get("current_candidate"))
    if next_value is None:
        return None
    if current is None:
        current = policy.neutral
    axis_state = {
        "schema_version": ADAPTIVE_SCHEMA_VERSION_V2,
        "policy_version": policy.policy_version,
        "legacy_policy_version": LEGACY_POLICY_VERSION,
        "axis": axis,
        "region": region,
        "mask_type": mask_type,
        "scope_key": _axis_scope_key(axis, region, mask_type),
        "episode_id": str(state.get("episode_id") or _episode_id(str(state["anchor_image_path"]), axis, region, mask_type)),
        "anchor_edit_id": _optional_text(state.get("anchor_edit_id")),
        "anchor_image_path": str(state["anchor_image_path"]),
        "active": bool(state.get("active", True)),
        "converged": bool(state.get("converged", False)),
        "current_value": _clean(current),
        "next_value": _clean(next_value),
        "current_candidate": _clean(next_value),
        "lower_bound": _clean_optional(_finite(state.get("lower_bound"))),
        "upper_bound": _clean_optional(_finite(state.get("upper_bound"))),
        "previous_direction": int(state.get("previous_direction") or 0),
        "base_step": _clean(_finite(state.get("base_step")) or max(policy.quantum, distance(policy, policy.neutral, next_value))),
        "step_before": _clean(_finite(state.get("step_before")) or policy.quantum),
        "step_after": _clean(_finite(state.get("step_after")) or policy.quantum),
        "refinement_round": int(state.get("refinement_round") or 0),
        "reversal_count": int(state.get("reversal_count") or 0),
        "relation": str(adaptive.get("relation") or "legacy"),
        "confidence": str(adaptive.get("confidence") or "high"),
        "reason": str(adaptive.get("reason") or "v1_migration"),
    }
    axes = {axis_state["scope_key"]: axis_state}
    ledger: list[dict[str, Any]] = []
    for other_axis, value in render_parameters.items():
        other_policy = AXIS_POLICIES[other_axis]
        if other_axis == axis or math.isclose(value, other_policy.neutral, abs_tol=other_policy.quantum / 10):
            continue
        ledger.append(
            _ledger_entry(
                group_id="legacy_v1",
                source_intent=str(adaptive.get("axis") or axis),
                role="legacy_companion",
                axis=other_axis,
                base_value=value,
                before_value=other_policy.neutral,
                proposed_value=value,
                precedence=6,
            )
        )
    vector_state = _vector_state(
        anchor_image_path=str(state["anchor_image_path"]),
        anchor_edit_id=_optional_text(state.get("anchor_edit_id")),
        region=region,
        mask_type=mask_type,
        axes=axes,
        render_parameters=render_parameters,
        operations=[],
    )
    migrated = {
        "schema_version": ADAPTIVE_SCHEMA_VERSION_V2,
        "policy_version": ADAPTIVE_POLICY_VERSION_V2,
        "state": vector_state,
        "render_parameters": render_parameters,
        "contribution_ledger": ledger,
        "operations": [],
        "migration": {
            "source_schema_version": LEGACY_SCHEMA_VERSION,
            "source_policy_version": LEGACY_POLICY_VERSION,
            "mode": "in_memory_single_axis",
        },
    }
    return migrated


def _prepare_parent_vector(
    *,
    parent_snapshot: Mapping[str, Any] | None,
    parent_record: Mapping[str, Any] | None,
    default_base: str,
    region: str,
    mask_type: str,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    state = (
        parent_snapshot.get("state")
        if isinstance(parent_snapshot, Mapping)
        and isinstance(parent_snapshot.get("state"), Mapping)
        else None
    )
    strong_reanchor = any(
        str(operation.get("strength_hint")) == "strong"
        and not (
            AXIS_POLICIES[str(operation.get("axis"))].one_sided
            and int(operation.get("direction") or 0) < 0
        )
        for operation in operations
        if str(operation.get("axis")) in AXIS_POLICIES
    )
    compatible = (
        isinstance(state, Mapping)
        and validate_edit_region(state.get("region")) == region
        and validate_edit_mask_type(state.get("mask_type")) == mask_type
        and bool(state.get("anchor_image_path"))
        and not strong_reanchor
    )
    if compatible:
        axes = copy.deepcopy(dict(state.get("axes") or {}))
        active_axes = [
            value
            for value in axes.values()
            if isinstance(value, Mapping) and value.get("active")
        ]
        # Preserve the established v1 behaviour after a single inactive numeric
        # snapshot: a qualitative request starts from the visible parent result.
        directional = all(
            operation.get("relation") not in {"absolute", "relative_numeric", "reset"}
            for operation in operations
        )
        preserve_one_sided_reduction = any(
            AXIS_POLICIES[str(operation.get("axis"))].one_sided
            and int(operation.get("direction") or 0) < 0
            and float(
                _canonical_parameters(state.get("render_parameters")).get(
                    str(operation.get("axis")), 0.0
                )
            )
            > AXIS_POLICIES[str(operation.get("axis"))].neutral
            for operation in operations
            if str(operation.get("axis")) in AXIS_POLICIES
        )
        if (
            directional
            and not active_axes
            and len(axes) <= 1
            and not preserve_one_sided_reduction
        ):
            compatible = False
    if compatible:
        return {
            "anchor_image_path": str(state["anchor_image_path"]),
            "anchor_edit_id": _optional_text(state.get("anchor_edit_id")),
            "render_parameters": _canonical_parameters(state.get("render_parameters")),
            "axes": copy.deepcopy(dict(state.get("axes") or {})),
            "contribution_ledger": copy.deepcopy(list(parent_snapshot.get("contribution_ledger") or [])),
            "migration": copy.deepcopy(parent_snapshot.get("migration")),
        }

    return {
        "anchor_image_path": default_base,
        "anchor_edit_id": _record_text(parent_record, "edit_id"),
        "render_parameters": _canonical_parameters(None),
        "axes": {},
        "contribution_ledger": [],
        "migration": None,
    }


def _transition_axis(
    *,
    policy: AxisPolicy,
    operation: Mapping[str, Any],
    current: float,
    existing_state: Mapping[str, Any] | None,
    anchor_image_path: str,
    anchor_edit_id: str | None,
    region: str,
    mask_type: str,
) -> dict[str, Any]:
    relation = str(operation.get("relation") or "initial")
    direction = int(operation.get("direction") or 0)
    current = quantize(policy, current)
    bounds_before = {"lower": None, "upper": None}

    if relation == "absolute":
        value = _validated_numeric(policy, operation.get("numeric_value"))
        return _inactive_transition(
            policy=policy,
            current=current,
            value=value,
            relation="absolute",
            reason="absolute_value_reset",
            operation=operation,
            anchor_image_path=anchor_image_path,
            anchor_edit_id=anchor_edit_id,
            region=region,
            mask_type=mask_type,
        )
    if relation == "relative_numeric":
        delta = _finite(operation.get("relative_delta"))
        if delta is None:
            _error("adaptive_invalid_numeric", "相對數值不是有限數字。", axis=policy.axis)
        value = _validated_numeric(policy, current + float(delta))
        return _inactive_transition(
            policy=policy,
            current=current,
            value=value,
            relation="relative_numeric",
            reason="relative_numeric_reset",
            operation=operation,
            anchor_image_path=anchor_image_path,
            anchor_edit_id=anchor_edit_id,
            region=region,
            mask_type=mask_type,
        )
    if relation == "reset":
        if math.isclose(current, policy.neutral, abs_tol=policy.quantum / 10):
            _converged(
                f"{policy.label}目前已是中性值；未新增零變化版本。",
                axis=policy.axis,
                reason="already_neutral",
            )
        return _inactive_transition(
            policy=policy,
            current=current,
            value=policy.neutral,
            relation="reset",
            reason="axis_reset",
            operation=operation,
            anchor_image_path=anchor_image_path,
            anchor_edit_id=anchor_edit_id,
            region=region,
            mask_type=mask_type,
            converged=True,
        )

    compatible = _compatible_axis_state(
        existing_state,
        policy=policy,
        region=region,
        mask_type=mask_type,
    )
    if compatible is None:
        return _start_axis_transition(
            policy=policy,
            operation=operation,
            current=current,
            anchor_image_path=anchor_image_path,
            anchor_edit_id=anchor_edit_id,
            region=region,
            mask_type=mask_type,
        )

    current_state_value = _finite(compatible.get("next_value"))
    if current_state_value is not None:
        current = quantize(policy, current_state_value)
    lower = _bounded_optional(policy, compatible.get("lower_bound"))
    upper = _bounded_optional(policy, compatible.get("upper_bound"))
    had_upper = upper is not None
    bounds_before = {"lower": lower, "upper": upper}
    if direction > 0:
        lower = current if lower is None else max(lower, current)
    else:
        if policy.one_sided and current <= policy.neutral + policy.quantum / 10:
            _converged(
                f"{policy.label}已在最小值，無法再減少；未新增版本。",
                axis=policy.axis,
                reason="one_sided_boundary",
            )
        upper = current if upper is None else min(upper, current)
        if policy.one_sided and lower is None:
            lower = policy.neutral
    if lower is not None and upper is not None and lower > upper:
        _error(
            "adaptive_state_invalid",
            f"{policy.label}的自適應區間已不一致，請從目前版本重新開始。",
            axis=policy.axis,
            reason="inverted_bounds",
        )

    base_step = _finite(compatible.get("base_step"))
    if base_step is None or base_step <= 0:
        base_step = max(policy.quantum, _finite(compatible.get("step_after")) or 0.0)
    strength = str(operation.get("strength_hint") or "normal")
    if (
        policy.one_sided
        and direction < 0
        and not had_upper
        and not operation.get("group_feedback")
    ):
        decrement = max(policy.quantum, policy.seed_target(-1, strength))
        raw_candidate = max(
            policy.neutral if lower is None else lower,
            current - decrement,
        )
        candidate = _directional_quantize(
            policy=policy,
            current=current,
            candidate=raw_candidate,
            direction=direction,
        )
        base_step = decrement
        reason = "one_sided_negative_seed"
    elif lower is not None and upper is not None:
        candidate = midpoint(policy, lower, upper)
        reason = "bracket_midpoint"
    else:
        candidate = advance(policy, current, direction, base_step)
        reason = "unbounded_template_step"
    candidate = _directional_quantize(
        policy=policy,
        current=current,
        candidate=candidate,
        direction=direction,
    )
    if (
        policy.minimum_active is not None
        and math.isclose(candidate, policy.minimum, abs_tol=policy.quantum / 10)
    ):
        lower = policy.minimum
    refinement_round = int(compatible.get("refinement_round") or 0)
    if lower is not None and upper is not None:
        refinement_round += 1
    if refinement_round > MAX_REFINEMENT_ROUNDS:
        _converged(
            f"{policy.label}已收斂到最小步長 {policy.quantum:g}；未新增零變化版本。",
            axis=policy.axis,
            reason="minimum_step",
        )
    previous_direction = int(compatible.get("previous_direction") or direction)
    reversal_count = int(compatible.get("reversal_count") or 0)
    if previous_direction != direction:
        reversal_count += 1
    step = _transition_distance(policy, current, candidate)
    return _axis_state(
        policy=policy,
        episode_id=str(compatible.get("episode_id") or _episode_id(anchor_image_path, policy.axis, region, mask_type)),
        anchor_edit_id=anchor_edit_id,
        anchor_image_path=anchor_image_path,
        region=region,
        mask_type=mask_type,
        current=current,
        candidate=candidate,
        lower=lower,
        upper=upper,
        previous_direction=direction,
        base_step=base_step,
        step_before=_finite(compatible.get("step_after")) or base_step,
        step_after=step,
        refinement_round=refinement_round,
        reversal_count=reversal_count,
        active=True,
        converged=False,
        relation="correct" if previous_direction != direction or relation == "correct" else "continue",
        confidence=str(operation.get("confidence") or "medium"),
        reason=reason,
        bounds_before=bounds_before,
    )


def _start_axis_transition(
    *,
    policy: AxisPolicy,
    operation: Mapping[str, Any],
    current: float,
    anchor_image_path: str,
    anchor_edit_id: str | None,
    region: str,
    mask_type: str,
) -> dict[str, Any]:
    direction = int(operation.get("direction") or 0)
    strength = str(operation.get("strength_hint") or "normal")
    if policy.one_sided and direction < 0:
        if current <= policy.neutral + policy.quantum / 10:
            _converged(
                f"{policy.label}已在最小值，無法再減少；未新增版本。",
                axis=policy.axis,
                reason="one_sided_boundary",
            )
        decrement = max(policy.quantum, policy.seed_target(-1, strength))
        lower, upper = policy.neutral, current
        candidate = _directional_quantize(
            policy=policy,
            current=current,
            candidate=max(policy.neutral, current - decrement),
            direction=direction,
        )
        base_step = decrement
        reason = "one_sided_negative_seed"
    else:
        step = seed_distance(policy, direction, strength)
        if math.isclose(current, policy.neutral, abs_tol=policy.quantum / 10):
            candidate = policy.seed_target(direction, strength)
        else:
            # This is how an explicit axis takes ownership of an earlier macro
            # companion while preserving the fixed vector anchor.
            base_for_coordinate = current
            if policy.minimum_active is not None and base_for_coordinate <= 0:
                candidate = (
                    policy.minimum_active if direction > 0 else policy.minimum
                )
            else:
                candidate = advance(policy, base_for_coordinate, direction, step)
        candidate = _directional_quantize(
            policy=policy,
            current=current,
            candidate=candidate,
            direction=direction,
        )
        lower = current if direction > 0 else None
        upper = current if direction < 0 else None
        if policy.one_sided:
            lower = policy.neutral if direction > 0 else lower
        base_step = step
        reason = "initial_template" if math.isclose(current, policy.neutral, abs_tol=policy.quantum / 10) else "companion_takeover"
    relation = "initial" if anchor_edit_id is None else "new_episode"
    step_after = _transition_distance(policy, current, candidate)
    return _axis_state(
        policy=policy,
        episode_id=_episode_id(anchor_image_path, policy.axis, region, mask_type),
        anchor_edit_id=anchor_edit_id,
        anchor_image_path=anchor_image_path,
        region=region,
        mask_type=mask_type,
        current=current,
        candidate=candidate,
        lower=lower,
        upper=upper,
        previous_direction=direction,
        base_step=max(base_step, policy.quantum),
        step_before=max(base_step, policy.quantum),
        step_after=step_after,
        refinement_round=0,
        reversal_count=0,
        active=True,
        converged=False,
        relation=relation,
        confidence=str(operation.get("confidence") or "high"),
        reason=(
            "explicit_strength_reset"
            if str(operation.get("strength_hint")) == "strong" and anchor_edit_id is not None
            else reason
        ),
        bounds_before={"lower": None, "upper": None},
    )


def _directional_quantize(
    *,
    policy: AxisPolicy,
    current: float,
    candidate: float,
    direction: int,
) -> float:
    tolerance = policy.quantum / 10
    if direction not in {-1, 1}:
        _error(
            "adaptive_state_invalid",
            f"{policy.label}缺少明確調整方向。",
            axis=policy.axis,
            reason="invalid_direction",
        )
    if direction > 0 and current >= policy.maximum - tolerance:
        _converged(
            f"{policy.label}已在最大值，無法再增加；未新增版本。",
            axis=policy.axis,
            reason="parameter_boundary",
        )
    if direction < 0 and current <= policy.minimum + tolerance:
        _converged(
            f"{policy.label}已在最小值，無法再減少；未新增版本。",
            axis=policy.axis,
            reason="parameter_boundary",
        )

    if (
        direction < 0
        and policy.minimum_active is not None
        and current <= policy.minimum_active + tolerance
    ):
        quantized = policy.minimum
    else:
        quantized = active_quantize(policy, candidate)

    if direction > 0 and quantized <= current + tolerance:
        quantized = quantize(policy, current + policy.quantum)
        if (
            policy.minimum_active is not None
            and quantized < policy.minimum_active
        ):
            quantized = quantize(policy, policy.minimum_active)
    elif direction < 0 and quantized >= current - tolerance:
        quantized = quantize(policy, current - policy.quantum)

    delta = quantized - current
    if direction * delta <= tolerance:
        if (
            (direction > 0 and current >= policy.maximum - tolerance)
            or (direction < 0 and current <= policy.minimum + tolerance)
        ):
            _converged(
                f"{policy.label}已到可用邊界；未新增零變化版本。",
                axis=policy.axis,
                reason="parameter_boundary",
            )
        _error(
            "adaptive_state_invalid",
            f"{policy.label}的候選值未沿要求方向前進，請從目前版本重新開始。",
            axis=policy.axis,
            reason="directional_progress_invariant",
            current_value=current,
            candidate=quantized,
            direction=direction,
        )
    return quantized


def _transition_distance(
    policy: AxisPolicy,
    current: float,
    candidate: float,
) -> float:
    transformed = distance(policy, current, candidate)
    if transformed <= 1e-12 and not math.isclose(
        current, candidate, abs_tol=policy.quantum / 10
    ):
        return policy.quantum
    return transformed


def _inactive_transition(
    *,
    policy: AxisPolicy,
    current: float,
    value: float,
    relation: str,
    reason: str,
    operation: Mapping[str, Any],
    anchor_image_path: str,
    anchor_edit_id: str | None,
    region: str,
    mask_type: str,
    converged: bool = False,
) -> dict[str, Any]:
    if math.isclose(value, current, abs_tol=policy.quantum / 10):
        _converged(
            f"{policy.label}已是要求的 {value:g}；未新增零變化版本。",
            axis=policy.axis,
            reason="numeric_noop" if relation != "reset" else "already_neutral",
        )
    step = distance(policy, current, value)
    return _axis_state(
        policy=policy,
        episode_id=_episode_id(anchor_image_path, policy.axis, region, mask_type),
        anchor_edit_id=anchor_edit_id,
        anchor_image_path=anchor_image_path,
        region=region,
        mask_type=mask_type,
        current=current,
        candidate=value,
        lower=None,
        upper=None,
        previous_direction=0 if relation == "reset" else 1 if value > current else -1,
        base_step=max(policy.quantum, distance(policy, policy.neutral, value)),
        step_before=step,
        step_after=step,
        refinement_round=0,
        reversal_count=0,
        active=False,
        converged=converged,
        relation=relation,
        confidence=str(operation.get("confidence") or "high"),
        reason=reason,
        bounds_before={"lower": None, "upper": None},
    )


def _axis_state(
    *,
    policy: AxisPolicy,
    episode_id: str,
    anchor_edit_id: str | None,
    anchor_image_path: str,
    region: str,
    mask_type: str,
    current: float,
    candidate: float,
    lower: float | None,
    upper: float | None,
    previous_direction: int,
    base_step: float,
    step_before: float,
    step_after: float,
    refinement_round: int,
    reversal_count: int,
    active: bool,
    converged: bool,
    relation: str,
    confidence: str,
    reason: str,
    bounds_before: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": ADAPTIVE_SCHEMA_VERSION_V2,
        "policy_version": policy.policy_version,
        "policy_id": f"{policy.axis}:{policy.policy_version}",
        "axis": policy.axis,
        "region": region,
        "mask_type": mask_type,
        "scope_key": _axis_scope_key(policy.axis, region, mask_type),
        "episode_id": episode_id,
        "anchor_edit_id": anchor_edit_id,
        "anchor_image_path": anchor_image_path,
        "active": bool(active),
        "converged": bool(converged),
        "current_value": _clean(current),
        "next_value": _clean(candidate),
        "current_candidate": _clean(candidate),
        "lower_bound": _clean_optional(lower),
        "upper_bound": _clean_optional(upper),
        "previous_direction": int(previous_direction),
        "base_step": _clean(base_step),
        "step_before": _clean(step_before),
        "step_after": _clean(step_after),
        "refinement_round": int(refinement_round),
        "reversal_count": int(reversal_count),
        "relation": relation,
        "confidence": confidence,
        "reason": reason,
        "bounds_before": {
            "lower": _clean_optional(_finite(bounds_before.get("lower"))),
            "upper": _clean_optional(_finite(bounds_before.get("upper"))),
        },
    }


def _operation_metadata(
    operation: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    current = float(state["current_value"])
    candidate = float(state["next_value"])
    return {
        "operation_id": operation.get("operation_id"),
        "group_id": operation.get("group_id"),
        "source_clause": operation.get("source_clause"),
        "source_marker": operation.get("source_marker"),
        "source_clauses": copy.deepcopy(operation.get("source_clauses")),
        "merged_clause_count": operation.get("merged_clause_count"),
        "source_intent": operation.get("source_intent"),
        "role": "primary",
        "explicitness": operation.get("explicitness"),
        "axis": state.get("axis"),
        "direction": state.get("previous_direction"),
        "region": state.get("region"),
        "mask_type": state.get("mask_type"),
        "relation": state.get("relation"),
        "strength_hint": operation.get("strength_hint"),
        "include_companions": bool(operation.get("include_companions")),
        "group_feedback": bool(operation.get("group_feedback")),
        "semantic_operation_kind": operation.get(
            "semantic_operation_kind"
        ),
        "semantic_decision_source": operation.get(
            "semantic_decision_source"
        ),
        "semantic_evidence": copy.deepcopy(
            operation.get("semantic_evidence") or []
        ),
        "suppressed_companion_axes": copy.deepcopy(
            operation.get("suppressed_companion_axes") or []
        ),
        "confidence": state.get("confidence"),
        "reason": state.get("reason"),
        "current_value": current,
        "next_value": candidate,
        "delta_from_parent": _clean(candidate - current),
        "lower_bound": state.get("lower_bound"),
        "upper_bound": state.get("upper_bound"),
        "bounds_before": copy.deepcopy(state.get("bounds_before") or {}),
        "bounds_after": {
            "lower": state.get("lower_bound"),
            "upper": state.get("upper_bound"),
        },
        "step_before": state.get("step_before"),
        "step_after": state.get("step_after"),
        "refinement_round": state.get("refinement_round"),
        "reversal_count": state.get("reversal_count"),
        "applied": not math.isclose(candidate, current, abs_tol=AXIS_POLICIES[str(state["axis"])].quantum / 10),
        "converged": bool(state.get("converged")),
    }


def _update_contribution_ledger(
    *,
    ledger: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    parameters: Mapping[str, float],
    axes: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("contribution_id")): copy.deepcopy(item)
        for item in ledger
        if isinstance(item, Mapping) and item.get("contribution_id")
    }
    primary_requested = {str(operation["axis"]) for operation in operations}
    transition_by_axis = {str(item["axis"]): item for item in transitions}

    for operation in operations:
        axis = str(operation["axis"])
        transition = transition_by_axis[axis]
        policy = AXIS_POLICIES[axis]
        new_intent = str(operation.get("source_intent") or "")
        new_direction = (
            1
            if new_intent == policy.positive_intent
            else -1
            if new_intent == policy.negative_intent
            else 0
        )
        if new_direction:
            superseded_groups = {
                str(item.get("group_id"))
                for item in by_id.values()
                if item.get("role") == "primary"
                and item.get("axis") == axis
                and (
                    (
                        item.get("source_intent") == policy.positive_intent
                        and new_direction < 0
                    )
                    or (
                        item.get("source_intent") == policy.negative_intent
                        and new_direction > 0
                    )
                )
            }
            retire_companions = bool(operation.get("include_companions"))
            for contribution_id, existing in list(by_id.items()):
                if str(existing.get("group_id")) not in superseded_groups:
                    continue
                if (
                    existing.get("role") != "primary"
                    and not retire_companions
                ):
                    continue
                retired = copy.deepcopy(existing)
                retired["suppressed"] = True
                retired["suppression_reason"] = "superseded_by_reverse_primary"
                by_id[contribution_id] = retired
        primary_entry = _ledger_entry(
            group_id=str(operation["group_id"]),
            source_intent=str(operation["source_intent"]),
            role="primary",
            axis=axis,
            base_value=float(transition["next_value"]),
            before_value=float(transition["current_value"]),
            proposed_value=float(transition["next_value"]),
            precedence=_precedence(operation),
        )
        by_id[str(primary_entry["contribution_id"])] = primary_entry

        if not operation.get("include_companions"):
            continue
        intent = str(operation.get("source_intent") or "")
        if operation.get("group_feedback"):
            current_primary = float(transition["current_value"])
            next_primary = float(transition["next_value"])
            primary_policy = AXIS_POLICIES[axis]
            old_effect = coordinate(primary_policy, current_primary) - coordinate(
                primary_policy, primary_policy.neutral
            )
            new_effect = coordinate(primary_policy, next_primary) - coordinate(
                primary_policy, primary_policy.neutral
            )
            scale = 0.0 if math.isclose(old_effect, 0.0, abs_tol=1e-12) else new_effect / old_effect
            matched_existing = False
            for contribution_id, existing in list(by_id.items()):
                if (
                    existing.get("group_id") != operation["group_id"]
                    or existing.get("role") != "companion"
                ):
                    continue
                companion_axis = str(existing.get("axis") or "")
                if companion_axis not in AXIS_POLICIES:
                    continue
                companion_policy = AXIS_POLICIES[companion_axis]
                old_target = _finite(existing.get("proposed_value"))
                if old_target is None:
                    continue
                old_delta = coordinate(companion_policy, old_target) - coordinate(
                    companion_policy, companion_policy.neutral
                )
                new_target = quantize(
                    companion_policy,
                    from_coordinate(
                        companion_policy,
                        coordinate(companion_policy, companion_policy.neutral)
                        + old_delta * scale,
                    ),
                )
                updated = copy.deepcopy(existing)
                updated["before_value"] = _clean(old_target)
                updated["proposed_value"] = _clean(new_target)
                updated["suppressed"] = companion_axis in primary_requested or _has_axis_state(axes, companion_axis)
                updated["suppression_reason"] = "explicit_or_primary_axis" if updated["suppressed"] else None
                by_id[contribution_id] = updated
                matched_existing = True
            # Feedback corrects an existing effect group.  It must never
            # synthesize companions that were not part of that group.
            continue
        try:
            adjustments = get_opencv_template_adjustments(
                intent,
                str(operation.get("strength_hint") or "normal"),
            )
        except ValueError:
            adjustments = {}
        suppressed_companion_axes = {
            str(item)
            for item in operation.get("suppressed_companion_axes") or []
            if str(item) in AXIS_POLICIES
        }
        for companion_axis, target in adjustments.items():
            if companion_axis not in AXIS_POLICIES or companion_axis == axis:
                continue
            companion_entry = _ledger_entry(
                group_id=str(operation["group_id"]),
                source_intent=intent,
                role="companion",
                axis=companion_axis,
                base_value=float(target),
                before_value=float(parameters.get(companion_axis, AXIS_POLICIES[companion_axis].neutral)),
                proposed_value=float(target),
                precedence=5,
            )
            companion_entry["suppressed"] = (
                companion_axis in primary_requested
                or _has_axis_state(axes, companion_axis)
                or companion_axis in suppressed_companion_axes
            )
            companion_entry["suppression_reason"] = (
                "negated_companion_guard"
                if companion_axis in suppressed_companion_axes
                else "explicit_or_primary_axis"
                if companion_entry["suppressed"]
                else None
            )
            by_id[str(companion_entry["contribution_id"])] = companion_entry

    result = _resolve_current_compound_companion_collisions(
        list(by_id.values()),
        current_group_ids={
            str(operation.get("group_id") or "")
            for operation in operations
        },
        primary_requested=primary_requested,
    )
    _validate_companion_collisions(result, primary_requested, axes)
    result.sort(key=lambda item: (ADAPTIVE_AXIS_ORDER.index(str(item["axis"])), str(item["contribution_id"])))
    return result


def _resolve_current_compound_companion_collisions(
    ledger: list[dict[str, Any]],
    *,
    current_group_ids: set[str],
    primary_requested: set[str],
) -> list[dict[str, Any]]:
    """Let an explicitly corrected old primary yield to a new compound macro.

    A standalone axis-only correction intentionally preserves companions from
    its previous macro group.  When the same request also introduces a new
    macro whose companion points the other way, preserving both would create
    an artificial collision.  In that narrow case only, the old companion is
    retired because its own primary axis is explicitly being corrected now.
    """

    resolved = copy.deepcopy(ledger)
    group_primary_axis = {
        str(item.get("group_id") or ""): str(item.get("axis") or "")
        for item in resolved
        if item.get("role") == "primary"
    }
    current_companions = [
        item
        for item in resolved
        if item.get("role") in {"companion", "legacy_companion"}
        and not item.get("suppressed")
        and str(item.get("group_id") or "") in current_group_ids
    ]
    for item in resolved:
        if (
            item.get("role") not in {"companion", "legacy_companion"}
            or item.get("suppressed")
        ):
            continue
        group_id = str(item.get("group_id") or "")
        if group_id in current_group_ids:
            continue
        if group_primary_axis.get(group_id) not in primary_requested:
            continue
        axis = str(item.get("axis") or "")
        if axis not in AXIS_POLICIES:
            continue
        policy = AXIS_POLICIES[axis]
        target = _finite(item.get("proposed_value"))
        if target is None:
            continue
        delta = coordinate(policy, target) - coordinate(
            policy, policy.neutral
        )
        if math.isclose(delta, 0.0, abs_tol=1e-12):
            continue
        has_opposite_current = False
        for current in current_companions:
            if str(current.get("axis") or "") != axis:
                continue
            current_target = _finite(current.get("proposed_value"))
            if current_target is None:
                continue
            current_delta = coordinate(
                policy, current_target
            ) - coordinate(policy, policy.neutral)
            if delta * current_delta < 0:
                has_opposite_current = True
                break
        if has_opposite_current:
            item["suppressed"] = True
            item[
                "suppression_reason"
            ] = "superseded_by_current_compound_companion"
    return resolved


def _compile_render_parameters(
    *,
    base_parameters: Mapping[str, float],
    axes: Mapping[str, Mapping[str, Any]],
    ledger: list[dict[str, Any]],
    requested_axes: set[str],
) -> dict[str, float]:
    parameters = _canonical_parameters(base_parameters)
    for axis in ADAPTIVE_AXIS_ORDER:
        state = _axis_state_for(axes, axis)
        if state is not None:
            value = _finite(state.get("next_value"))
            if value is not None:
                parameters[axis] = quantize(AXIS_POLICIES[axis], value)

    companion_axes = {
        str(item.get("axis"))
        for item in ledger
        if item.get("role") in {"companion", "legacy_companion"}
    }
    for axis in companion_axes:
        if axis not in AXIS_POLICIES or _has_axis_state(axes, axis):
            continue
        policy = AXIS_POLICIES[axis]
        deltas: list[float] = []
        for item in ledger:
            if (
                item.get("axis") != axis
                or item.get("role") not in {"companion", "legacy_companion"}
                or item.get("suppressed")
            ):
                continue
            target = _finite(item.get("proposed_value"))
            if target is None:
                continue
            deltas.append(coordinate(policy, target) - coordinate(policy, policy.neutral))
        parameters[axis] = quantize(
            policy,
            from_coordinate(
                policy,
                coordinate(policy, policy.neutral) + sum(deltas),
            ),
        )
    return _canonical_parameters(parameters)


def _validate_companion_collisions(
    ledger: list[dict[str, Any]],
    requested_axes: set[str],
    axes: Mapping[str, Mapping[str, Any]],
) -> None:
    for axis in ADAPTIVE_AXIS_ORDER:
        if axis in requested_axes or _has_axis_state(axes, axis):
            continue
        policy = AXIS_POLICIES[axis]
        signs = set()
        entries = []
        for item in ledger:
            if (
                item.get("axis") != axis
                or item.get("role") not in {"companion", "legacy_companion"}
                or item.get("suppressed")
            ):
                continue
            target = _finite(item.get("proposed_value"))
            if target is None:
                continue
            delta = coordinate(policy, target) - coordinate(policy, policy.neutral)
            if not math.isclose(delta, 0.0, abs_tol=1e-12):
                signs.add(1 if delta > 0 else -1)
                entries.append(item)
        if len(signs) > 1:
            raise AdaptiveV2Error(
                code="adaptive_contribution_conflict",
                message=f"不同效果對{policy.label}提出相反的 companion 貢獻，請明確指定{policy.label}。",
                status_code=422,
                issues=tuple(
                    {
                        "axis": axis,
                        "group_id": item.get("group_id"),
                        "source_intent": item.get("source_intent"),
                        "reason": "opposite_companion_contributions",
                    }
                    for item in entries
                ),
            )


def _annotate_ledger_merge(
    ledger: list[dict[str, Any]],
    parameters: Mapping[str, float],
    axes: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    annotated = copy.deepcopy(ledger)
    for item in annotated:
        axis = str(item.get("axis") or "")
        if axis not in AXIS_POLICIES:
            continue
        item["merged_value"] = float(parameters[axis])
        if item.get("role") in {"companion", "legacy_companion"} and _has_axis_state(axes, axis):
            item["suppressed"] = True
            item["suppression_reason"] = "explicit_or_primary_axis"
        item["applied"] = not bool(item.get("suppressed"))
    return annotated


def _ledger_entry(
    *,
    group_id: str,
    source_intent: str,
    role: str,
    axis: str,
    base_value: float,
    before_value: float,
    proposed_value: float,
    precedence: int,
) -> dict[str, Any]:
    contribution_id = _stable_id("contribution", group_id, source_intent, role, axis)
    return {
        "contribution_id": contribution_id,
        "group_id": group_id,
        "source_intent": source_intent,
        "role": role,
        "axis": axis,
        "precedence": precedence,
        "base_value": _clean(base_value),
        "before_value": _clean(before_value),
        "proposed_value": _clean(proposed_value),
        "merged_value": None,
        "suppressed": False,
        "suppression_reason": None,
        "applied": True,
    }


def _vector_state(
    *,
    anchor_image_path: str,
    anchor_edit_id: str | None,
    region: str,
    mask_type: str,
    axes: Mapping[str, Mapping[str, Any]],
    render_parameters: Mapping[str, Any],
    operations: list[Mapping[str, Any]],
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema_version": ADAPTIVE_SCHEMA_VERSION_V2,
        "policy_version": ADAPTIVE_POLICY_VERSION_V2,
        "active": any(bool(value.get("active")) for value in axes.values()),
        "scope_episode_id": _scope_episode_id(anchor_image_path, region, mask_type),
        "episode_id": _scope_episode_id(anchor_image_path, region, mask_type),
        "region": region,
        "mask_type": mask_type,
        "anchor_edit_id": anchor_edit_id,
        "anchor_image_path": anchor_image_path,
        "axes": copy.deepcopy(dict(axes)),
        "render_parameters": _canonical_parameters(render_parameters),
        "converged": bool(axes) and all(bool(value.get("converged")) for value in axes.values()),
    }
    if len(operations) == 1:
        operation = operations[0]
        axis_state = _axis_state_for(axes, str(operation.get("axis") or ""))
        if axis_state is not None:
            for key in (
                "axis",
                "current_value",
                "next_value",
                "current_candidate",
                "lower_bound",
                "upper_bound",
                "previous_direction",
                "base_step",
                "step_before",
                "step_after",
                "refinement_round",
                "reversal_count",
            ):
                state[key] = copy.deepcopy(axis_state.get(key))
            state["active"] = bool(axis_state.get("active"))
            state["converged"] = bool(axis_state.get("converged"))
    return state


def _adaptive_metadata(
    *,
    state: Mapping[str, Any],
    operations: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    requested_axes: list[str],
    unchanged_axes: list[str],
    region_source: str,
    migration: Any,
) -> dict[str, Any]:
    adaptive: dict[str, Any] = {
        "schema_version": ADAPTIVE_SCHEMA_VERSION_V2,
        "policy_version": ADAPTIVE_POLICY_VERSION_V2,
        "axis_policy_version": AXIS_POLICY_VERSION,
        "decision_source": "adaptive_v2_deterministic",
        "policy_registry": policy_registry_payload(),
        "applied": any(bool(operation.get("applied")) and operation.get("relation") not in {"absolute", "relative_numeric", "reset"} for operation in operations),
        "requested_axes": list(requested_axes),
        "applied_axes": [str(operation["axis"]) for operation in operations if operation.get("applied")],
        "unchanged_axes": list(unchanged_axes),
        "operation_count": len(operations),
        "region": state.get("region"),
        "mask_type": state.get("mask_type"),
        "region_source": region_source,
        "anchor_edit_id": state.get("anchor_edit_id"),
        "anchor_image_path": state.get("anchor_image_path"),
        "scope_episode_id": state.get("scope_episode_id"),
        "render_parameters": copy.deepcopy(dict(state.get("render_parameters") or {})),
        "operations": copy.deepcopy(operations),
        "contribution_ledger": copy.deepcopy(ledger),
        "state": copy.deepcopy(dict(state)),
        "migration": copy.deepcopy(migration),
    }
    if len(operations) == 1:
        operation = operations[0]
        adaptive.update(
            {
                "axis": operation.get("axis"),
                "direction": operation.get("direction"),
                "relation": operation.get("relation"),
                "confidence": operation.get("confidence"),
                "reason": operation.get("reason"),
                "episode_id": _axis_state_for(state.get("axes") or {}, str(operation.get("axis"))) .get("episode_id") if _axis_state_for(state.get("axes") or {}, str(operation.get("axis"))) else state.get("scope_episode_id"),
                "current_value": operation.get("current_value"),
                "next_value": operation.get("next_value"),
                "delta_from_parent": operation.get("delta_from_parent"),
                "lower_bound": operation.get("lower_bound"),
                "upper_bound": operation.get("upper_bound"),
                "bounds_before": copy.deepcopy(operation.get("bounds_before") or {}),
                "step_before": operation.get("step_before"),
                "step_after": operation.get("step_after"),
                "refinement_round": operation.get("refinement_round"),
                "reversal_count": operation.get("reversal_count"),
                "converged": operation.get("converged"),
            }
        )
    else:
        adaptive.update(
            {
                "axis": None,
                "direction": None,
                "relation": "compound",
                "confidence": "high" if all(item.get("confidence") == "high" for item in operations) else "medium",
                "reason": "multi_axis_compound",
                "episode_id": state.get("scope_episode_id"),
                "current_value": None,
                "next_value": None,
                "delta_from_parent": None,
                "lower_bound": None,
                "upper_bound": None,
                "bounds_before": {},
                "step_before": None,
                "step_after": None,
                "refinement_round": None,
                "reversal_count": None,
                "converged": all(bool(item.get("converged")) for item in operations),
            }
        )
    return adaptive


def _prepare_prompt_result(
    *,
    result: dict[str, Any],
    deterministic_result: Mapping[str, Any],
    prompt: str,
    operations: list[dict[str, Any]],
    parameters: Mapping[str, Any],
    region: str,
    mask_type: str,
    adaptive: Mapping[str, Any],
) -> dict[str, Any]:
    semantic_edits = [
        (
            str(operation.get("source_intent") or AXIS_POLICIES[str(operation["axis"])].positive_intent),
            str(operation.get("strength_hint") or "normal"),
        )
        for operation in operations
        if operation.get("relation") not in {"absolute", "relative_numeric", "reset"}
    ]
    if semantic_edits:
        plan = build_compound_edit_plan(
            prompt=prompt,
            intent_strengths=semantic_edits,
            region=region,
            mask_type=mask_type,
        )
    else:
        plan = build_raw_parameter_edit_plan(
            prompt=prompt,
            parameters=parameters,
            region=region,
            mask_type=mask_type,
        )
    original_plan = result.get("edit_plan")
    if isinstance(original_plan, Mapping):
        plan["llm_edit_plan"] = copy.deepcopy(dict(original_plan))
    rule_plan = deterministic_result.get("edit_plan")
    if isinstance(rule_plan, Mapping):
        plan["deterministic_edit_plan"] = copy.deepcopy(dict(rule_plan))
    plan["normalized_operations"] = copy.deepcopy(operations)
    plan["adaptation"] = copy.deepcopy(dict(adaptive))
    semantic_ir = result.get("semantic_ir")
    if isinstance(semantic_ir, Mapping):
        plan["semantic_ir"] = copy.deepcopy(dict(semantic_ir))
        plan["semantic_parser_version"] = result.get(
            "semantic_parser_version"
        )
        plan["semantic_decision_source"] = result.get(
            "semantic_decision_source"
        )
    result["prompt"] = prompt
    result["edit_plan"] = plan
    result["parameters"] = build_engine_parameters("opencv", plan)
    result["resolved_intent"] = (
        str(operations[0].get("source_intent"))
        if len(operations) == 1
        else "compound"
    )
    result["preset_name"] = None
    result["parser_source"] = str(result.get("parser_source") or "adaptive_v2_deterministic")
    result["fallback_reason"] = result.get("fallback_reason")
    result["explanation"] = _adaptive_explanation(adaptive)
    return result


def _safe_rule_result(
    provided: dict[str, Any],
    deterministic: Mapping[str, Any],
) -> dict[str, Any]:
    safe = copy.deepcopy(provided)
    for key in (
        "prompt",
        "resolved_intent",
        "preset_name",
        "edit_plan",
        "parameters",
        "explanation",
    ):
        if key in deterministic:
            safe[key] = copy.deepcopy(deterministic[key])
    safe.setdefault("parser_source", provided.get("parser_source") or "rule_fallback")
    safe.setdefault("fallback_reason", provided.get("fallback_reason"))
    return safe


def _global_reset(
    *,
    result: dict[str, Any],
    prompt: str,
    parent_record: Mapping[str, Any] | None,
    default_base: str,
) -> AdaptiveV2Resolution:
    if not isinstance(parent_record, Mapping):
        _converged("目前已是原圖起點；未新增零變化版本。", reason="already_original")
    original = _record_text(parent_record, "original_image_path") or default_base
    parent_result = _record_text(parent_record, "result_image_path")
    parent_adaptive = parent_record.get("adaptive")
    if parent_result == original or (
        isinstance(parent_adaptive, Mapping)
        and parent_adaptive.get("reason") == "global_reset"
    ):
        _converged("目前版本已是原圖；未新增零變化版本。", reason="already_original")
    parameters = _canonical_parameters(None)
    state = _vector_state(
        anchor_image_path=original,
        anchor_edit_id=None,
        region="all",
        mask_type="none",
        axes={},
        render_parameters=parameters,
        operations=[],
    )
    adaptive = {
        "schema_version": ADAPTIVE_SCHEMA_VERSION_V2,
        "policy_version": ADAPTIVE_POLICY_VERSION_V2,
        "axis_policy_version": AXIS_POLICY_VERSION,
        "policy_registry": policy_registry_payload(),
        "applied": False,
        "requested_axes": [],
        "applied_axes": [],
        "unchanged_axes": [],
        "operation_count": 0,
        "region": "all",
        "mask_type": "none",
        "region_source": "reset",
        "anchor_edit_id": None,
        "anchor_image_path": original,
        "scope_episode_id": state["scope_episode_id"],
        "render_parameters": parameters,
        "operations": [],
        "contribution_ledger": [],
        "state": state,
        "migration": None,
        "axis": None,
        "direction": 0,
        "relation": "reset",
        "confidence": "high",
        "reason": "global_reset",
        "episode_id": state["scope_episode_id"],
        "current_value": None,
        "next_value": None,
        "delta_from_parent": None,
        "lower_bound": None,
        "upper_bound": None,
        "bounds_before": {},
        "step_before": None,
        "step_after": None,
        "refinement_round": 0,
        "reversal_count": 0,
        "converged": True,
    }
    plan = build_raw_parameter_edit_plan(
        prompt=prompt,
        parameters=parameters,
        region="all",
        mask_type="none",
    )
    plan["adaptation"] = copy.deepcopy(adaptive)
    result["prompt"] = prompt
    result["edit_plan"] = plan
    result["parameters"] = build_engine_parameters("opencv", plan)
    result["resolved_intent"] = "reset_to_original"
    result["preset_name"] = None
    result["explanation"] = "已回到原圖並清除全部自適應參數。"
    return AdaptiveV2Resolution(
        prompt_result=result,
        adaptive=adaptive,
        render_base_image_path=original,
        explanation="已回到原圖並清除全部自適應參數。",
    )


def _adaptive_explanation(adaptive: Mapping[str, Any]) -> str:
    operations = adaptive.get("operations")
    if not isinstance(operations, list) or not operations:
        return "已依要求更新自適應參數。"
    parts = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        axis = str(operation.get("axis") or "")
        label = AXIS_POLICIES[axis].label if axis in AXIS_POLICIES else axis
        current = _finite(operation.get("current_value")) or 0.0
        candidate = _finite(operation.get("next_value")) or 0.0
        delta = candidate - current
        parts.append(f"{label} {current:g} → {candidate:g} ({delta:+g})")
    prefix = "多軸自適應" if len(parts) > 1 else "自適應微調"
    return f"{prefix} · {len(parts)} 項：" + "；".join(parts) + "。"


def _canonical_parameters(value: Any) -> dict[str, float]:
    parameters = {
        axis: float(AXIS_POLICIES[axis].neutral)
        for axis in MANUAL_PARAMETER_KEYS
    }
    if isinstance(value, Mapping):
        validated = validate_edit_parameters(value)
        for axis in MANUAL_PARAMETER_KEYS:
            if axis in validated:
                parameters[axis] = quantize(AXIS_POLICIES[axis], validated[axis])
    return parameters


def _strict_snapshot_parameters(value: Any) -> dict[str, float] | None:
    """Validate persisted v2 parameters without dropping or clamping data."""
    if not isinstance(value, Mapping):
        return None
    if set(value.keys()) != set(MANUAL_PARAMETER_KEYS):
        return None

    parameters: dict[str, float] = {}
    for axis in MANUAL_PARAMETER_KEYS:
        raw = value.get(axis)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        number = _finite(raw)
        policy = AXIS_POLICIES[axis]
        if (
            number is None
            or number < policy.minimum
            or number > policy.maximum
        ):
            return None
        canonical = quantize(policy, number)
        if not math.isclose(
            number,
            canonical,
            abs_tol=policy.quantum / 10,
        ):
            return None
        parameters[axis] = canonical
    return parameters


def _compatible_axis_state(
    state: Mapping[str, Any] | None,
    *,
    policy: AxisPolicy,
    region: str,
    mask_type: str,
) -> dict[str, Any] | None:
    if not isinstance(state, Mapping):
        return None
    if (
        state.get("axis") != policy.axis
        or state.get("policy_version") != policy.policy_version
        or validate_edit_region(state.get("region")) != region
        or validate_edit_mask_type(state.get("mask_type")) != mask_type
        or not state.get("active")
        or _finite(state.get("next_value")) is None
    ):
        return None
    return copy.deepcopy(dict(state))


def _find_axis_state(
    axes: Mapping[str, Mapping[str, Any]],
    *,
    axis: str,
    region: str,
    mask_type: str,
) -> tuple[str | None, dict[str, Any] | None]:
    for key, value in axes.items():
        if (
            isinstance(value, Mapping)
            and value.get("axis") == axis
            and validate_edit_region(value.get("region")) == region
            and validate_edit_mask_type(value.get("mask_type")) == mask_type
        ):
            return str(key), copy.deepcopy(dict(value))
    return None, None


def _axis_state_for(
    axes: Mapping[str, Mapping[str, Any]],
    axis: str,
) -> Mapping[str, Any] | None:
    for value in axes.values():
        if isinstance(value, Mapping) and value.get("axis") == axis:
            return value
    return None


def _has_axis_state(axes: Mapping[str, Mapping[str, Any]], axis: str) -> bool:
    return _axis_state_for(axes, axis) is not None


def _axes_in_state(axes: Mapping[str, Mapping[str, Any]]) -> list[str]:
    found = {
        str(value.get("axis"))
        for value in axes.values()
        if isinstance(value, Mapping) and value.get("axis") in AXIS_POLICIES
    }
    return sorted(found, key=ADAPTIVE_AXIS_ORDER.index)


def _validated_numeric(policy: AxisPolicy, value: Any) -> float:
    number = _finite(value)
    if number is None:
        _error("adaptive_invalid_numeric", f"{policy.label}必須是有限數字。", axis=policy.axis)
    assert number is not None
    if number < policy.minimum or number > policy.maximum:
        _error(
            "adaptive_numeric_out_of_range",
            f"{policy.label}必須介於 {policy.minimum:g} 和 {policy.maximum:g}。",
            axis=policy.axis,
            minimum=policy.minimum,
            maximum=policy.maximum,
            value=number,
        )
    return quantize(policy, number)


def _bounded_optional(policy: AxisPolicy, value: Any) -> float | None:
    number = _finite(value)
    return None if number is None else quantize(policy, number)


def _precedence(operation: Mapping[str, Any]) -> int:
    relation = str(operation.get("relation") or "")
    if relation == "absolute":
        return 1
    if relation == "relative_numeric":
        return 2
    if operation.get("explicitness") == "explicit_axis":
        return 3
    if operation.get("explicitness") == "macro_primary":
        return 4
    return 4


def _axis_scope_key(axis: str, region: str, mask_type: str) -> str:
    return f"{axis}:{region}:{mask_type}"


def _scope_episode_id(anchor: str, region: str, mask_type: str) -> str:
    return _stable_id("scope", anchor, region, mask_type)


def _episode_id(anchor: str, axis: str, region: str, mask_type: str) -> str:
    return _stable_id("episode", anchor, axis, region, mask_type)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _finite(value: Any) -> float | None:
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


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _record_text(record: Mapping[str, Any] | None, key: str) -> str | None:
    return _optional_text(record.get(key)) if isinstance(record, Mapping) else None


def _error(code: str, message: str, **issue: Any) -> None:
    raise AdaptiveV2Error(
        code=code,
        message=message,
        status_code=422,
        issues=(issue,),
    )


def _converged(message: str, **issue: Any) -> None:
    raise AdaptiveV2Error(
        code="adaptive_step_converged",
        message=message,
        status_code=409,
        issues=(issue,),
    )
