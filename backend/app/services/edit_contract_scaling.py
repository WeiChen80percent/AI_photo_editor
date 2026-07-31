from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.adaptive_controller_v2 import ADAPTIVE_SCHEMA_VERSION_V2
from app.services.adaptive_policy import (
    ADAPTIVE_AXIS_ORDER,
    AXIS_POLICIES,
    coordinate,
    from_coordinate,
    quantize,
)
from app.services.edit_engines import build_engine_parameters


@dataclass(frozen=True)
class ScaledAdaptiveV2Result:
    """A detached prompt/adaptive pair for one deterministic render attempt."""

    prompt_result: dict[str, Any]
    adaptive: dict[str, Any]


class EditContractScalingError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def scale_adaptive_v2_result(
    *,
    prompt_result: Mapping[str, Any],
    adaptive: Mapping[str, Any],
    scale: float,
) -> ScaledAdaptiveV2Result:
    """Scale only the current adaptive-v2 request without mutating inputs.

    Primary operations interpolate from their parent value. New macro
    companions interpolate from the axis neutral, because ledger compilation
    adds those effects in coordinate space. Continued or corrected effect-group
    companions instead interpolate from ``before_value`` so an existing group
    is preserved or corrected rather than reintroduced. The returned prompt
    plan, adaptive snapshot and Photo Git ledger all describe the same
    quantized render vector.
    """

    normalized_scale = _validate_scale(scale)
    source_prompt, source_adaptive = _validate_source(
        prompt_result=prompt_result,
        adaptive=adaptive,
    )

    # Exact parity matters for the overwhelmingly common pass-at-full-strength
    # path. Deep copies still guarantee callers cannot mutate the source result.
    if normalized_scale == 1.0:
        return ScaledAdaptiveV2Result(
            prompt_result=copy.deepcopy(source_prompt),
            adaptive=copy.deepcopy(source_adaptive),
        )

    scaled_prompt = copy.deepcopy(source_prompt)
    scaled_adaptive = copy.deepcopy(source_adaptive)
    operations = _scale_operations(
        source_adaptive.get("operations"),
        normalized_scale,
    )
    operation_by_group = _operations_by_group(operations)

    state = _mapping_copy(scaled_adaptive.get("state"), "adaptive state")
    axes = _scale_axes(
        state.get("axes"),
        operations=operations,
    )
    ledger = _scale_current_request_ledger(
        scaled_adaptive.get("contribution_ledger"),
        operation_by_group=operation_by_group,
        scale=normalized_scale,
    )
    render_parameters = _compile_render_parameters(
        source_adaptive.get("render_parameters"),
        axes=axes,
        ledger=ledger,
    )
    _annotate_merged_values(ledger, render_parameters)

    state["axes"] = axes
    state["render_parameters"] = copy.deepcopy(render_parameters)
    _sync_single_operation_state(state, operations, axes)

    scaled_adaptive["operations"] = copy.deepcopy(operations)
    scaled_adaptive["contribution_ledger"] = copy.deepcopy(ledger)
    scaled_adaptive["render_parameters"] = copy.deepcopy(render_parameters)
    scaled_adaptive["state"] = state
    scaled_adaptive["applied_axes"] = [
        str(operation["axis"])
        for operation in operations
        if bool(operation.get("applied"))
    ]
    scaled_adaptive["applied"] = any(
        bool(operation.get("applied"))
        and operation.get("relation")
        not in {"absolute", "relative_numeric", "reset"}
        for operation in operations
    )
    _sync_single_operation_adaptive(scaled_adaptive, operations)

    plan = _mapping_copy(scaled_prompt.get("edit_plan"), "edit plan")
    plan["normalized_operations"] = copy.deepcopy(operations)
    plan["adaptation"] = copy.deepcopy(scaled_adaptive)
    scaled_prompt["edit_plan"] = plan
    scaled_prompt["parameters"] = build_engine_parameters("opencv", plan)
    scaled_prompt["explanation"] = _adaptive_explanation(operations)

    return ScaledAdaptiveV2Result(
        prompt_result=scaled_prompt,
        adaptive=scaled_adaptive,
    )


def _validate_scale(value: float) -> float:
    if isinstance(value, bool):
        raise EditContractScalingError(
            "contract_scale_invalid",
            "Adaptive scale must be a finite number between 0 and 1.",
        )
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise EditContractScalingError(
            "contract_scale_invalid",
            "Adaptive scale must be a finite number between 0 and 1.",
        ) from exc
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise EditContractScalingError(
            "contract_scale_out_of_range",
            "Adaptive scale must be between 0 and 1 inclusive.",
        )
    return normalized


def _validate_source(
    *,
    prompt_result: Mapping[str, Any],
    adaptive: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(prompt_result, Mapping) or not isinstance(adaptive, Mapping):
        raise EditContractScalingError(
            "contract_adaptive_invalid",
            "Scaling requires a prompt result and adaptive-v2 metadata.",
        )
    prompt = dict(prompt_result)
    metadata = dict(adaptive)
    if metadata.get("schema_version") != ADAPTIVE_SCHEMA_VERSION_V2:
        raise EditContractScalingError(
            "contract_adaptive_unsupported",
            "Only adaptive-v2 prompt results can be scaled.",
        )
    state = metadata.get("state")
    if (
        not isinstance(state, Mapping)
        or state.get("schema_version") != ADAPTIVE_SCHEMA_VERSION_V2
        or not isinstance(state.get("axes"), Mapping)
        or not isinstance(metadata.get("operations"), list)
        or not metadata.get("operations")
        or not isinstance(metadata.get("contribution_ledger"), list)
        or not isinstance(metadata.get("render_parameters"), Mapping)
    ):
        raise EditContractScalingError(
            "contract_adaptive_invalid",
            "Adaptive-v2 metadata is incomplete and cannot be scaled safely.",
        )
    plan = prompt.get("edit_plan")
    if not isinstance(plan, Mapping) or not isinstance(
        plan.get("adaptation"), Mapping
    ):
        raise EditContractScalingError(
            "contract_edit_plan_invalid",
            "The prompt result is missing its adaptive EditPlan.",
        )
    if dict(plan["adaptation"]) != metadata:
        raise EditContractScalingError(
            "contract_adaptive_mismatch",
            "Prompt and adaptive metadata do not describe the same request.",
        )
    return prompt, metadata


def _scale_operations(value: Any, scale: float) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise EditContractScalingError(
            "contract_operations_invalid",
            "Adaptive operations are required for deterministic scaling.",
        )
    scaled: list[dict[str, Any]] = []
    seen_axes: set[str] = set()
    for raw in value:
        operation = _mapping_copy(raw, "adaptive operation")
        axis = str(operation.get("axis") or "")
        if axis not in AXIS_POLICIES or axis in seen_axes:
            raise EditContractScalingError(
                "contract_operation_axis_invalid",
                "Each adaptive operation must target one unique public axis.",
            )
        seen_axes.add(axis)
        current = _finite(operation.get("current_value"), "operation current")
        requested = _finite(operation.get("next_value"), "operation next")
        candidate = _scaled_value(axis, current, requested, scale)
        policy = AXIS_POLICIES[axis]
        applied = not math.isclose(
            candidate,
            current,
            abs_tol=policy.quantum / 10.0,
        )
        operation["next_value"] = candidate
        operation["delta_from_parent"] = _clean(candidate - current)
        operation["applied"] = applied
        operation["step_after"] = _clean(
            abs(coordinate(policy, candidate) - coordinate(policy, current))
        )
        scaled.append(operation)
    return scaled


def _operations_by_group(
    operations: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for operation in operations:
        group_id = str(operation.get("group_id") or "").strip()
        if not group_id or group_id in result:
            raise EditContractScalingError(
                "contract_operation_group_invalid",
                "Each adaptive operation must have a unique contribution group.",
            )
        result[group_id] = operation
    return result


def _scale_axes(
    value: Any,
    *,
    operations: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise EditContractScalingError(
            "contract_axes_invalid",
            "Adaptive axis state is required for deterministic scaling.",
        )
    axes = {
        str(key): _mapping_copy(raw, "adaptive axis state")
        for key, raw in value.items()
    }
    for operation in operations:
        axis = str(operation["axis"])
        matches = [
            axis_state
            for axis_state in axes.values()
            if str(axis_state.get("axis") or "") == axis
        ]
        if len(matches) != 1:
            raise EditContractScalingError(
                "contract_axis_state_invalid",
                f"Adaptive axis state is missing or ambiguous for {axis}.",
            )
        axis_state = matches[0]
        axis_state["next_value"] = operation["next_value"]
        axis_state["current_candidate"] = operation["next_value"]
        axis_state["step_after"] = operation["step_after"]
    return axes


def _scale_current_request_ledger(
    value: Any,
    *,
    operation_by_group: Mapping[str, Mapping[str, Any]],
    scale: float,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise EditContractScalingError(
            "contract_ledger_invalid",
            "Adaptive contribution ledger is required for scaling.",
        )
    ledger: list[dict[str, Any]] = []
    matched_primary: set[tuple[str, str]] = set()
    for raw in value:
        entry = _mapping_copy(raw, "adaptive ledger entry")
        group_id = str(entry.get("group_id") or "")
        operation = operation_by_group.get(group_id)
        if operation is None:
            ledger.append(entry)
            continue
        axis = str(entry.get("axis") or "")
        role = str(entry.get("role") or "")
        if axis not in AXIS_POLICIES:
            raise EditContractScalingError(
                "contract_ledger_axis_invalid",
                "Current-request ledger entries must use public axes.",
            )
        suppressed = bool(entry.get("suppressed"))
        if role == "primary":
            if axis != str(operation.get("axis") or ""):
                raise EditContractScalingError(
                    "contract_ledger_primary_invalid",
                    "A primary ledger entry does not match its operation.",
                )
            candidate = float(operation["next_value"])
            current = float(operation["current_value"])
            entry["before_value"] = current
            entry["base_value"] = candidate
            entry["proposed_value"] = candidate
            entry["applied"] = bool(operation.get("applied")) and not suppressed
            matched_primary.add((group_id, axis))
        elif role in {"companion", "legacy_companion"}:
            requested = _finite(entry.get("proposed_value"), "ledger proposed")
            updates_existing_group = (
                bool(operation.get("group_feedback"))
                or str(operation.get("relation") or "") != "initial"
            )
            baseline = (
                _finite(entry.get("before_value"), "ledger before")
                if updates_existing_group
                else AXIS_POLICIES[axis].neutral
            )
            candidate = _scaled_value(axis, baseline, requested, scale)
            entry["proposed_value"] = candidate
            if not updates_existing_group:
                entry["base_value"] = candidate
            entry["applied"] = (
                not suppressed
                and not math.isclose(
                    candidate,
                    baseline,
                    abs_tol=AXIS_POLICIES[axis].quantum / 10.0,
                )
            )
        else:
            raise EditContractScalingError(
                "contract_ledger_role_invalid",
                "Current-request ledger entries have an unsupported role.",
            )
        ledger.append(entry)

    expected = {
        (group_id, str(operation["axis"]))
        for group_id, operation in operation_by_group.items()
    }
    if matched_primary != expected:
        raise EditContractScalingError(
            "contract_ledger_primary_missing",
            "Every adaptive operation must have one current-request ledger entry.",
        )
    return ledger


def _compile_render_parameters(
    value: Any,
    *,
    axes: Mapping[str, Mapping[str, Any]],
    ledger: list[Mapping[str, Any]],
) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise EditContractScalingError(
            "contract_render_parameters_invalid",
            "Adaptive render parameters are required for scaling.",
        )
    parameters: dict[str, float] = {}
    for axis in ADAPTIVE_AXIS_ORDER:
        parameters[axis] = quantize(
            AXIS_POLICIES[axis],
            _finite(value.get(axis), f"render parameter {axis}"),
        )

    state_axes: set[str] = set()
    for axis_state in axes.values():
        axis = str(axis_state.get("axis") or "")
        if axis not in AXIS_POLICIES or axis in state_axes:
            raise EditContractScalingError(
                "contract_axis_state_invalid",
                "Adaptive axis states must use unique public axes.",
            )
        state_axes.add(axis)
        parameters[axis] = quantize(
            AXIS_POLICIES[axis],
            _finite(axis_state.get("next_value"), f"axis state {axis}"),
        )

    companion_axes = {
        str(entry.get("axis") or "")
        for entry in ledger
        if entry.get("role") in {"companion", "legacy_companion"}
    }
    for axis in companion_axes:
        if axis not in AXIS_POLICIES:
            raise EditContractScalingError(
                "contract_ledger_axis_invalid",
                "Adaptive companion ledger uses an unsupported axis.",
            )
        if axis in state_axes:
            continue
        policy = AXIS_POLICIES[axis]
        delta = 0.0
        for entry in ledger:
            if (
                str(entry.get("axis") or "") != axis
                or entry.get("role")
                not in {"companion", "legacy_companion"}
                or bool(entry.get("suppressed"))
            ):
                continue
            target = _finite(entry.get("proposed_value"), "ledger proposed")
            delta += coordinate(policy, target) - coordinate(
                policy,
                policy.neutral,
            )
        parameters[axis] = quantize(
            policy,
            from_coordinate(
                policy,
                coordinate(policy, policy.neutral) + delta,
            ),
        )
    return parameters


def _annotate_merged_values(
    ledger: list[dict[str, Any]],
    render_parameters: Mapping[str, float],
) -> None:
    for entry in ledger:
        axis = str(entry.get("axis") or "")
        if axis in render_parameters:
            entry["merged_value"] = float(render_parameters[axis])


def _sync_single_operation_state(
    state: dict[str, Any],
    operations: list[dict[str, Any]],
    axes: Mapping[str, Mapping[str, Any]],
) -> None:
    if len(operations) != 1:
        return
    operation = operations[0]
    axis = str(operation["axis"])
    axis_state = next(
        value
        for value in axes.values()
        if str(value.get("axis") or "") == axis
    )
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


def _sync_single_operation_adaptive(
    adaptive: dict[str, Any],
    operations: list[dict[str, Any]],
) -> None:
    if len(operations) != 1:
        return
    operation = operations[0]
    for key in (
        "axis",
        "direction",
        "relation",
        "confidence",
        "reason",
        "current_value",
        "next_value",
        "delta_from_parent",
        "lower_bound",
        "upper_bound",
        "bounds_before",
        "step_before",
        "step_after",
        "refinement_round",
        "reversal_count",
        "converged",
    ):
        adaptive[key] = copy.deepcopy(operation.get(key))


def _scaled_value(axis: str, start: float, requested: float, scale: float) -> float:
    policy = AXIS_POLICIES[axis]
    # ``coordinate`` deliberately floors log axes at ``minimum_active``.
    # Preserve exact endpoints so a scale-zero diagnostic never changes a
    # valid parent value such as saturation == 0.
    if scale == 0.0:
        return quantize(policy, start)
    if scale == 1.0:
        return quantize(policy, requested)
    start_coordinate = coordinate(policy, start)
    requested_coordinate = coordinate(policy, requested)
    return quantize(
        policy,
        from_coordinate(
            policy,
            start_coordinate
            + (requested_coordinate - start_coordinate) * scale,
        ),
    )


def _adaptive_explanation(operations: list[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for operation in operations:
        axis = str(operation.get("axis") or "")
        policy = AXIS_POLICIES.get(axis)
        label = policy.label if policy is not None else axis
        current = _finite(operation.get("current_value"), "operation current")
        candidate = _finite(operation.get("next_value"), "operation next")
        parts.append(
            f"{label} {current:g} → {candidate:g} "
            f"({_clean(candidate - current):+g})"
        )
    prefix = "多軸自適應" if len(parts) > 1 else "自適應微調"
    return f"{prefix} · {len(parts)} 項：" + "；".join(parts) + "。"


def _mapping_copy(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EditContractScalingError(
            "contract_adaptive_invalid",
            f"Invalid {label}.",
        )
    return copy.deepcopy(dict(value))


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise EditContractScalingError(
            "contract_adaptive_invalid",
            f"Invalid numeric {label}.",
        )
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise EditContractScalingError(
            "contract_adaptive_invalid",
            f"Invalid numeric {label}.",
        ) from exc
    if not math.isfinite(numeric):
        raise EditContractScalingError(
            "contract_adaptive_invalid",
            f"Invalid numeric {label}.",
        )
    return numeric


def _clean(value: float) -> float:
    return round(float(value), 6)
