from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.services.semantic_ir import RawSpanEvidence, SemanticIR, SemanticOperation
from app.services.semantic_normalizer import normalize_semantic_text
from app.services.semantic_registry import (
    DEFAULT_PARAMETER_REGISTRY,
    ParameterRegistry,
)
from app.services.semantic_scope_resolver import (
    AUTHORITATIVE_SCOPE_ERROR_CODES,
    resolve_semantic_scope,
)
from app.services.semantic_slot_extractor import (
    SemanticSlot,
    SlotExtraction,
    extract_semantic_slots,
)


MAX_SEMANTIC_OPERATIONS = 3
MIN_GROUNDED_LLM_CONFIDENCE = 0.9
_RESOLVED_SUPPORT_EVIDENCE_SLOTS = frozenset(
    {
        "axis_support",
        "context_axis_support",
        "semantic_support",
        "observation_attribute",
        "action_attribute",
        "surface_action",
        "effect_support",
        "resolved_direction",
        "resolved_observation",
        "resolved_strength",
        "prior_event_support",
        "resolved_relation_support",
        "axis_attribute_region_support",
        "controller_contract_support",
    }
)
_REGION_EVIDENCE_SLOTS = frozenset(
    {"region", "region_axis_binding", "region_object_binding"}
)
_SURFACE_ACTION_DIRECTIONS = {"remove": -1}


@dataclass(frozen=True, slots=True)
class SemanticValidationError(ValueError):
    code: str
    message: str
    issues: tuple[dict[str, Any], ...] = ()
    status_code: int = 422

    def __str__(self) -> str:
        return self.message


def validate_semantic_ir(
    ir: SemanticIR,
    *,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
    max_operations: int = MAX_SEMANTIC_OPERATIONS,
) -> SemanticIR:
    """Validate a fully grounded semantic candidate without changing it.

    The validator is deliberately independent from both deterministic parsing
    and LLM transport.  Every candidate therefore crosses the same allowlist,
    evidence, numeric, region, and operation-count boundary.
    """

    if not isinstance(ir, SemanticIR):
        _raise(
            "adaptive_clarification_required",
            "Semantic candidate does not use the supported IR schema.",
            reason="invalid_semantic_ir_type",
        )
    if not isinstance(max_operations, int) or isinstance(max_operations, bool):
        raise TypeError("max_operations must be an integer")
    if max_operations < 1:
        raise ValueError("max_operations must be positive")

    _validate_candidate_shape(ir, registry)
    if ir.unresolved_spans:
        raise SemanticValidationError(
            code="adaptive_clarification_required",
            message=(
                "The request still contains unresolved semantic content; "
                "no operation was applied."
            ),
            issues=tuple(
                {
                    "source_clause": span.raw_text,
                    "start": span.start,
                    "end": span.end,
                    "reason": span.reason,
                }
                for span in ir.unresolved_spans
            ),
        )
    if ir.terminal_intent is not None and ir.operations:
        _raise(
            "adaptive_operation_conflict",
            "Terminal intent cannot be combined with edit operations.",
            terminal_intent=ir.terminal_intent,
            operation_count=len(ir.operations),
            reason="terminal_operation_conflict",
        )
    if not ir.operations and ir.terminal_intent is None:
        _raise(
            "adaptive_clarification_required",
            "No supported edit operation or terminal intent was grounded.",
            reason="no_supported_semantic_result",
        )
    if len(ir.operations) > max_operations:
        raise SemanticValidationError(
            code="adaptive_operation_limit_exceeded",
            message=(
                f"A single request supports at most {max_operations} "
                "primary operations."
            ),
            issues=tuple(
                {
                    "axis": operation.axis_id,
                    "reason": "operation_limit",
                }
                for operation in ir.operations
            ),
        )

    if (
        ir.decision_source == "grounded_llm"
        and ir.confidence < MIN_GROUNDED_LLM_CONFIDENCE
    ):
        _raise(
            "adaptive_clarification_required",
            "Grounded LLM candidate confidence is below the safety threshold.",
            confidence=ir.confidence,
            minimum=MIN_GROUNDED_LLM_CONFIDENCE,
            reason="low_grounded_llm_confidence",
        )

    extraction = _validate_lexical_grounding(ir, registry)
    _validate_resolved_support_bindings(ir, extraction, registry)
    _validate_terminal_grounding(ir, extraction)
    _validate_region_grounding(ir, extraction, registry)
    seen_axes: set[str] = set()
    for operation in ir.operations:
        if operation.axis_id in seen_axes:
            _raise(
                "adaptive_operation_conflict",
                "The same parameter appears more than once in one request.",
                axis=operation.axis_id,
                reason="duplicate_axis_operation",
            )
        seen_axes.add(operation.axis_id)
        _validate_operation(
            operation,
            ir=ir,
            registry=registry,
            engine=engine,
            extraction=extraction,
        )

    _validate_evidence_coverage(ir, extraction)
    return ir


def _validate_candidate_shape(
    ir: SemanticIR,
    registry: ParameterRegistry,
) -> None:
    if ir.region not in registry.regions:
        _raise(
            "adaptive_clarification_required",
            "Semantic candidate uses an unknown edit region.",
            region=ir.region,
            reason="unknown_region",
        )
    for operation in ir.operations:
        if not isinstance(operation, SemanticOperation):
            _raise(
                "adaptive_clarification_required",
                "Semantic candidate contains an invalid operation object.",
                reason="invalid_semantic_operation_type",
            )
        if operation.axis_id not in registry.axes:
            _raise(
                "adaptive_clarification_required",
                "Semantic candidate uses an unknown edit parameter.",
                axis=operation.axis_id,
                reason="unknown_axis",
            )


def _validate_operation(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    engine: str,
    extraction: SlotExtraction,
) -> None:
    definition = registry.axes.get(operation.axis_id)
    if definition is None:
        _raise(
            "adaptive_clarification_required",
            "Semantic candidate uses an unknown edit parameter.",
            axis=operation.axis_id,
            reason="unknown_axis",
        )
    if operation.region != ir.region:
        _raise(
            "adaptive_multi_region_not_supported",
            "All operations in one request must use the same region.",
            axis=operation.axis_id,
            region=operation.region,
            expected_region=ir.region,
            reason="operation_region_mismatch",
        )

    capability = next(
        (
            item
            for item in definition.render_capabilities
            if item.engine == engine
        ),
        None,
    )
    if capability is None:
        _raise(
            "adaptive_unsupported_operation",
            "The selected engine cannot render this semantic parameter.",
            axis=operation.axis_id,
            engine=engine,
            reason="missing_render_capability",
        )
    if operation.region not in capability.regions:
        _raise(
            "adaptive_unsupported_operation",
            "The selected engine cannot render this parameter in that region.",
            axis=operation.axis_id,
            region=operation.region,
            engine=engine,
            reason="unsupported_axis_region_capability",
        )

    axis_evidence = _require_evidence(operation, "axis")
    axis_slot = _require_grounded_slot(
        extraction,
        axis_evidence,
        namespace="axis",
        concept_id=operation.axis_id,
    )
    if operation.operation_kind == "group_feedback":
        allowed_intents = {
            definition.policy.positive_intent,
            definition.policy.negative_intent,
        }
        if operation.target_group_intent not in allowed_intents:
            _raise(
                "adaptive_operation_conflict",
                "Group feedback does not identify a valid effect group.",
                axis=operation.axis_id,
                target_group_intent=operation.target_group_intent,
                reason="invalid_target_group_intent",
            )
    elif operation.target_group_intent is not None:
        _raise(
            "adaptive_operation_conflict",
            "Only group feedback may carry a target effect group.",
            axis=operation.axis_id,
            target_group_intent=operation.target_group_intent,
            reason="unexpected_target_group_intent",
        )

    if operation.operation_type == "relative":
        if operation.operation_kind == "relative_numeric":
            _validate_relative_numeric_grounding(
                operation,
                extraction,
                axis_slot=axis_slot,
            )
            assert operation.value is not None
            width = definition.schema.maximum - definition.schema.minimum
            if abs(operation.value) > width:
                _raise(
                    "adaptive_invalid_numeric",
                    "Relative numeric delta exceeds the full parameter range.",
                    axis=operation.axis_id,
                    value=operation.value,
                    reason="relative_delta_out_of_range",
                )
        else:
            _validate_direction_grounding(
                operation,
                ir=ir,
                registry=registry,
                extraction=extraction,
                axis_slot=axis_slot,
                definition=definition,
            )
            _validate_strength_grounding(
                operation,
                extraction,
                ir=ir,
                registry=registry,
            )
    elif operation.operation_type == "absolute":
        numeric_evidence = _require_evidence(operation, "numeric")
        numeric_slot = _require_grounded_slot(
            extraction,
            numeric_evidence,
            namespace="numeric",
        )
        assert operation.value is not None
        if float(numeric_slot.value) != operation.value:
            _raise(
                "adaptive_invalid_numeric",
                "Absolute numeric value is not grounded in its evidence.",
                axis=operation.axis_id,
                value=operation.value,
                evidence_value=numeric_slot.value,
                reason="numeric_evidence_mismatch",
            )
        relation_evidence = _find_evidence(operation, "numeric_relation")
        if relation_evidence is not None:
            relation_slot = _require_grounded_slot(
                extraction,
                relation_evidence,
                slot_name="numeric_relation",
            )
            if relation_slot.value != "absolute":
                _raise(
                    "adaptive_invalid_numeric",
                    "Absolute numeric operation uses a relative relation.",
                    axis=operation.axis_id,
                    relation=relation_slot.value,
                    reason="numeric_relation_mismatch",
                )
        elif numeric_evidence.raw_text.lstrip().startswith(("+", "-")):
            _raise(
                "adaptive_invalid_numeric",
                "A signed numeric value needs an explicit relative relation.",
                axis=operation.axis_id,
                source_clause=numeric_evidence.raw_text,
                reason="signed_absolute_numeric",
            )
        if not (
            definition.schema.minimum
            <= operation.value
            <= definition.schema.maximum
        ):
            _raise(
                "adaptive_invalid_numeric",
                "Absolute numeric value is outside the parameter schema.",
                axis=operation.axis_id,
                value=operation.value,
                minimum=definition.schema.minimum,
                maximum=definition.schema.maximum,
                reason="absolute_value_out_of_range",
            )
    elif operation.operation_type == "reset":
        reset_evidence = _require_evidence(operation, "reset")
        _require_grounded_slot(
            extraction,
            reset_evidence,
            slot_name="operation",
            concept_id="axis_reset",
        )


def _require_evidence(
    operation: SemanticOperation,
    required_slot: str,
) -> RawSpanEvidence:
    evidence = next(
        (
            item
            for item in operation.evidence
            if item.slot == required_slot
            or (
                required_slot in {"axis", "direction"}
                and item.slot == "axis_direction"
            )
            or (
                required_slot == "direction"
                and item.slot
                in {
                    "observation_attribute",
                    "action_attribute",
                    "surface_action",
                    "resolved_direction",
                    "resolved_observation",
                }
            )
        ),
        None,
    )
    if evidence is None:
        _raise(
            "adaptive_clarification_required",
            "Semantic operation is missing grounded source evidence.",
            axis=operation.axis_id,
            required_slot=required_slot,
            reason="missing_semantic_evidence",
        )
    return evidence


def _find_evidence(
    operation: SemanticOperation,
    slot_name: str,
) -> RawSpanEvidence | None:
    return next(
        (item for item in operation.evidence if item.slot == slot_name),
        None,
    )


def _operation_evidence_slots(
    operation: SemanticOperation,
    extraction: SlotExtraction,
) -> tuple[SemanticSlot, ...]:
    spans = {
        (evidence.start, evidence.end)
        for evidence in operation.evidence
        if evidence.slot not in _RESOLVED_SUPPORT_EVIDENCE_SLOTS
    }
    return tuple(
        slot
        for slot in extraction.slots
        if not slot.is_ambiguous
        and (slot.evidence.start, slot.evidence.end) in spans
    )


def _validate_relative_numeric_grounding(
    operation: SemanticOperation,
    extraction: SlotExtraction,
    *,
    axis_slot: SemanticSlot,
) -> None:
    numeric_evidence = _require_evidence(operation, "numeric")
    numeric_slot = _require_grounded_slot(
        extraction,
        numeric_evidence,
        namespace="numeric",
    )
    assert operation.value is not None
    numeric_value = float(numeric_slot.value)
    if not math.isclose(
        abs(numeric_value),
        abs(operation.value),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        _raise(
            "adaptive_invalid_numeric",
            "Relative numeric delta is not grounded in its evidence.",
            axis=operation.axis_id,
            value=operation.value,
            evidence_value=numeric_value,
            reason="numeric_evidence_mismatch",
        )

    relation_evidence = _find_evidence(operation, "numeric_relation")
    has_explicit_sign = numeric_evidence.raw_text.lstrip().startswith(
        ("+", "-")
    )
    if relation_evidence is not None:
        relation_slot = _require_grounded_slot(
            extraction,
            relation_evidence,
            slot_name="numeric_relation",
        )
        if relation_slot.value != "relative":
            _raise(
                "adaptive_invalid_numeric",
                "Relative numeric operation uses an absolute relation.",
                axis=operation.axis_id,
                relation=relation_slot.value,
                reason="numeric_relation_mismatch",
            )
    operation_slots = _operation_evidence_slots(operation, extraction)
    direction_slots = [
        slot
        for slot in operation_slots
        if slot.slot == "direction"
    ]
    fused_slots = [
        slot
        for slot in operation_slots
        if slot.namespace == "axis"
        and slot.requested_direction is not None
        and (slot.evidence.start, slot.evidence.end)
        != (axis_slot.evidence.start, axis_slot.evidence.end)
    ]
    direction_candidates = [
        int(slot.value)
        for slot in direction_slots
        if slot.concept_id not in {"comparative_more", "comparative_less"}
    ]
    if not direction_candidates:
        direction_candidates = [
            int(slot.value) for slot in direction_slots
        ]
    direction_candidates.extend(
        int(slot.requested_direction) for slot in fused_slots
    )
    if (
        not direction_candidates
        and axis_slot.match_kind in {"action", "descriptor"}
        and axis_slot.requested_direction in {-1, 1}
    ):
        direction_candidates.append(int(axis_slot.requested_direction))
    if len(set(direction_candidates)) > 1:
        _raise(
            "adaptive_operation_conflict",
            "Numeric direction evidence is contradictory.",
            axis=operation.axis_id,
            reason="conflicting_direction_evidence",
        )
    if direction_candidates:
        expected_direction = direction_candidates[0]
    elif has_explicit_sign:
        expected_direction = 1 if numeric_value > 0 else -1
    else:
        _raise(
            "adaptive_clarification_required",
            "Relative numeric operation lacks a grounded direction.",
            axis=operation.axis_id,
            reason="missing_numeric_direction",
        )
    if (
        operation.direction != expected_direction
        or (operation.value > 0) != (expected_direction > 0)
    ):
        _raise(
            "adaptive_operation_conflict",
            "Numeric delta direction disagrees with source evidence.",
            axis=operation.axis_id,
            requested_direction=operation.direction,
            grounded_direction=expected_direction,
            value=operation.value,
            reason="direction_evidence_mismatch",
        )


def _validate_strength_grounding(
    operation: SemanticOperation,
    extraction: SlotExtraction,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
) -> None:
    slots = [
        slot
        for slot in _operation_evidence_slots(operation, extraction)
        if slot.slot == "strength"
    ]
    resolved_strength_evidence = tuple(
        evidence
        for evidence in operation.evidence
        if evidence.slot == "resolved_strength"
    )
    if resolved_strength_evidence:
        if ir.decision_source != "semantic_registry":
            _raise(
                "adaptive_clarification_required",
                "Only deterministic scope may resolve comparative strength.",
                axis=operation.axis_id,
                reason="untrusted_resolved_support_binding",
            )
        resolution = resolve_semantic_scope(
            extraction,
            registry=registry,
        )
        for evidence in resolved_strength_evidence:
            binding = _resolved_strength_binding(
                operation,
                evidence,
                extraction=extraction,
                resolution=resolution,
                registry=registry,
            )
            if binding is None:
                _raise(
                    "adaptive_clarification_required",
                    "Resolved strength evidence cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    reason="unverifiable_resolved_support_binding",
                )
            slots.append(binding[1])
    values = {str(slot.value) for slot in slots}
    if len(values) > 1:
        _raise(
            "adaptive_operation_conflict",
            "Strength evidence is contradictory.",
            axis=operation.axis_id,
            strengths=sorted(values),
            reason="conflicting_strength_evidence",
        )

    if operation.operation_kind in {
        "context_feedback",
        "group_feedback",
    }:
        if operation.strength != "subtle":
            _raise(
                "adaptive_operation_conflict",
                "Feedback corrections must use the conservative step.",
                axis=operation.axis_id,
                strength=operation.strength,
                reason="unsafe_feedback_strength",
            )
        return

    expected = next(iter(values), "normal")
    if operation.strength != expected:
        _raise(
            "adaptive_operation_conflict",
            "Requested strength disagrees with grounded source evidence.",
            axis=operation.axis_id,
            requested_strength=operation.strength,
            grounded_strength=expected,
            reason="strength_evidence_mismatch",
        )


def _validate_resolved_support_bindings(
    ir: SemanticIR,
    extraction: SlotExtraction,
    registry: ParameterRegistry,
) -> None:
    """Verify deterministic support-role evidence against exact source slots.

    Support roles are emitted only by the registry-driven scope assembler
    after same-axis fusion.  They let validation retain every raw span without
    treating an observed state or redundant axis noun as a second command.
    LLM candidates may not self-assign these trusted binding roles.
    """

    bindings = tuple(
        (operation, evidence)
        for operation in ir.operations
        for evidence in operation.evidence
        if evidence.slot in _RESOLVED_SUPPORT_EVIDENCE_SLOTS
    )
    if not bindings:
        return
    if ir.decision_source != "semantic_registry":
        _raise(
            "adaptive_clarification_required",
            "Only deterministic scope resolution may emit support bindings.",
            decision_source=ir.decision_source,
            reason="untrusted_resolved_support_binding",
        )

    resolution = resolve_semantic_scope(
        extraction,
        registry=registry,
    )
    for operation in ir.operations:
        required_upper_bounds = tuple(
            slot.evidence
            for group in resolution.operation_groups
            if group.axis_id == operation.axis_id
            for slot in group.supporting_slots
            if (
                slot.evidence.slot == "resolved_observation"
                and slot.concept_id == "negated_upper_bound"
                and _contrastive_negated_comparative_command_binding(
                    operation,
                    slot.evidence,
                    extraction=extraction,
                    resolution=resolution,
                )
                is not None
            )
        )
        for required in required_upper_bounds:
            matches = tuple(
                evidence
                for evidence in operation.evidence
                if (
                    evidence.slot == required.slot
                    and evidence.concept_id == required.concept_id
                    and evidence.start == required.start
                    and evidence.end == required.end
                    and evidence.raw_text == required.raw_text
                )
            )
            if len(matches) != 1:
                _raise(
                    "adaptive_clarification_required",
                    "Contrastive upper-bound evidence is incomplete.",
                    axis=operation.axis_id,
                    source_clause=required.raw_text,
                    start=required.start,
                    end=required.end,
                    binding=required.slot,
                    concept_id=required.concept_id,
                    reason="missing_resolved_support_binding",
                )
    for operation, evidence in bindings:
        if evidence.slot == "resolved_strength":
            if (
                _resolved_strength_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
                is None
            ):
                _raise(
                    "adaptive_clarification_required",
                    "Resolved strength evidence cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    start=evidence.start,
                    end=evidence.end,
                    binding=evidence.slot,
                    concept_id=evidence.concept_id,
                    reason="unverifiable_resolved_support_binding",
                )
            continue
        if evidence.slot == "resolved_direction":
            if (
                _resolved_direction_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
                is None
            ):
                _raise(
                    "adaptive_clarification_required",
                    "Resolved ambiguous evidence cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    start=evidence.start,
                    end=evidence.end,
                    binding=evidence.slot,
                    concept_id=evidence.concept_id,
                    reason="unverifiable_resolved_support_binding",
                )
            continue
        if evidence.slot == "effect_support":
            if (
                _resolved_effect_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
                is None
            ):
                _raise(
                    "adaptive_clarification_required",
                    "Resolved effect evidence cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    start=evidence.start,
                    end=evidence.end,
                    binding=evidence.slot,
                    concept_id=evidence.concept_id,
                    reason="unverifiable_resolved_support_binding",
                )
            continue
        if evidence.slot == "resolved_observation":
            if (
                _resolved_observation_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
                is None
            ):
                _raise(
                    "adaptive_clarification_required",
                    "Resolved observation evidence cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    start=evidence.start,
                    end=evidence.end,
                    binding=evidence.slot,
                    concept_id=evidence.concept_id,
                    reason="unverifiable_resolved_support_binding",
                )
            continue
        if evidence.slot == "prior_event_support":
            if (
                _prior_event_support_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                )
                is None
            ):
                _raise(
                    "adaptive_clarification_required",
                    "Prior-event support cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    start=evidence.start,
                    end=evidence.end,
                    binding=evidence.slot,
                    concept_id=evidence.concept_id,
                    reason="unverifiable_resolved_support_binding",
                )
            continue
        if evidence.slot == "resolved_relation_support":
            if (
                _resolved_relation_support_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
                is None
            ):
                _raise(
                    "adaptive_clarification_required",
                    "Resolved relation support cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    start=evidence.start,
                    end=evidence.end,
                    binding=evidence.slot,
                    concept_id=evidence.concept_id,
                    reason="unverifiable_resolved_support_binding",
                )
            continue
        if evidence.slot == "axis_attribute_region_support":
            if (
                _axis_attribute_region_support_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
                is None
            ):
                _raise(
                    "adaptive_clarification_required",
                    "Axis-attribute region support cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    start=evidence.start,
                    end=evidence.end,
                    binding=evidence.slot,
                    concept_id=evidence.concept_id,
                    reason="unverifiable_resolved_support_binding",
                )
            continue
        if evidence.slot == "controller_contract_support":
            if (
                _controller_contract_support_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
                is None
            ):
                _raise(
                    "adaptive_clarification_required",
                    "Controller contract support cannot be re-proved.",
                    axis=operation.axis_id,
                    source_clause=evidence.raw_text,
                    start=evidence.start,
                    end=evidence.end,
                    binding=evidence.slot,
                    concept_id=evidence.concept_id,
                    reason="unverifiable_resolved_support_binding",
                )
            continue
        source_matches = tuple(
            slot
            for slot in extraction.slots
            if (
                not slot.is_ambiguous
                and slot.evidence.start == evidence.start
                and slot.evidence.end == evidence.end
                and str(slot.concept_id) == evidence.concept_id
            )
        )
        operation_groups = tuple(
            group
            for group in resolution.operation_groups
            if group.axis_id == operation.axis_id
        )
        verified = False
        if len(source_matches) == 1 and len(operation_groups) == 1:
            slot = source_matches[0]
            group = operation_groups[0]
            if evidence.slot == "observation_attribute":
                verified = (
                    slot.namespace == "axis"
                    and slot.requested_direction in {-1, 1}
                    and slot in group.attribute_axis_slots
                )
            elif evidence.slot == "action_attribute":
                verified = (
                    slot.namespace == "axis"
                    and slot.match_kind == "action"
                    and slot.requested_direction in {-1, 1}
                    and slot in group.action_attribute_slots
                )
            elif evidence.slot == "axis_support":
                verified = (
                    slot.namespace == "axis"
                    and str(slot.value) == operation.axis_id
                    and slot in group.supporting_slots
                )
            elif evidence.slot == "context_axis_support":
                verified = (
                    slot.namespace == "axis"
                    and str(slot.value) != operation.axis_id
                    and slot in group.supporting_slots
                )
            elif evidence.slot == "semantic_support":
                verified = (
                    slot.namespace != "axis"
                    and slot in group.supporting_slots
                )
            elif evidence.slot == "surface_action":
                verified = (
                    slot.slot == "surface_action"
                    and str(slot.value) in _SURFACE_ACTION_DIRECTIONS
                    and slot in group.surface_action_slots
                    and group.axis_slot.axis_role == "axis"
                    and group.direction_multiplier == -1
                    and group.surface_action_direction in {-1, 1}
                )
        if not verified:
            _raise(
                "adaptive_clarification_required",
                "Resolved support evidence does not match its source concept.",
                axis=operation.axis_id,
                source_clause=evidence.raw_text,
                start=evidence.start,
                end=evidence.end,
                binding=evidence.slot,
                concept_id=evidence.concept_id,
                reason="unverifiable_resolved_support_binding",
            )


def _validate_terminal_grounding(
    ir: SemanticIR,
    extraction: SlotExtraction,
) -> None:
    terminal_intent = ir.terminal_intent
    terminal_slots = tuple(
        slot
        for slot in extraction.slots
        if not slot.is_ambiguous and slot.slot == "terminal"
    )
    if terminal_intent is None:
        if terminal_slots:
            _raise(
                "adaptive_operation_conflict",
                "Grounded terminal text cannot be omitted from Semantic IR.",
                reason="missing_terminal_intent",
            )
        return
    if ir.decision_source != "semantic_registry":
        _raise(
            "adaptive_clarification_required",
            "Terminal intent must be resolved by the deterministic registry.",
            decision_source=ir.decision_source,
            reason="untrusted_terminal_decision_source",
        )
    if ir.region != "all":
        _raise(
            "adaptive_operation_conflict",
            "Terminal intent cannot target a local edit region.",
            terminal_intent=terminal_intent,
            region=ir.region,
            reason="terminal_region_conflict",
        )
    if len(terminal_slots) != 1:
        _raise(
            "adaptive_operation_conflict",
            "Terminal intent requires exactly one grounded terminal alias.",
            terminal_intent=terminal_intent,
            terminal_count=len(terminal_slots),
            reason="conflicting_terminal_intent",
        )
    terminal_slot = terminal_slots[0]
    if str(terminal_slot.value) != terminal_intent:
        _raise(
            "adaptive_operation_conflict",
            "Terminal intent disagrees with the grounded terminal alias.",
            terminal_intent=terminal_intent,
            grounded_terminal=str(terminal_slot.value),
            reason="terminal_intent_mismatch",
        )
    terminal_evidence = tuple(
        evidence
        for evidence in ir.evidence
        if evidence.slot == "terminal"
    )
    if (
        len(ir.evidence) != 1
        or len(terminal_evidence) != 1
        or terminal_evidence[0].concept_id != terminal_slot.concept_id
        or terminal_evidence[0].start != terminal_slot.evidence.start
        or terminal_evidence[0].end != terminal_slot.evidence.end
        or terminal_evidence[0].raw_text != terminal_slot.evidence.raw_text
        or terminal_evidence[0].language != terminal_slot.language
    ):
        _raise(
            "adaptive_clarification_required",
            "Terminal evidence cannot be re-proved from its exact source span.",
            terminal_intent=terminal_intent,
            reason="unverifiable_terminal_evidence",
        )


def _validate_region_grounding(
    ir: SemanticIR,
    extraction: SlotExtraction,
    registry: ParameterRegistry,
) -> None:
    region_evidence = [
        item
        for item in (
            *ir.evidence,
            *(
                evidence
                for operation in ir.operations
                for evidence in operation.evidence
            ),
        )
        if item.slot in _REGION_EVIDENCE_SLOTS
    ]
    mismatched_evidence = [
        evidence
        for evidence in region_evidence
        if evidence.concept_id != ir.region
    ]
    if mismatched_evidence:
        _raise(
            "adaptive_multi_region_not_supported",
            "Region evidence contains a different target region.",
            region=ir.region,
            grounded_regions=sorted(
                {item.concept_id for item in mismatched_evidence}
            ),
            reason="region_evidence_mismatch",
        )
    resolved_scope = resolve_semantic_scope(
        extraction,
        registry=registry,
    )
    authoritative_errors = tuple(
        error
        for error in resolved_scope.errors
        if error.code in AUTHORITATIVE_SCOPE_ERROR_CODES
    )
    if authoritative_errors:
        _raise(
            "adaptive_clarification_required",
            (
                "The request contains a state, hypothetical, or object "
                "binding that is not an authorized edit command."
            ),
            reasons=[error.code for error in authoritative_errors],
            reason="authoritative_scope_rejection",
        )
    extracted_regions = {
        (
            str(slot.value)
            if slot.namespace == "region"
            or slot.slot in {"region_context", "region_object"}
            else str(slot.concept_id)
        )
        for slot in resolved_scope.region_slots
        if not slot.is_ambiguous
    }
    if len(extracted_regions) > 1:
        _raise(
            "adaptive_multi_region_not_supported",
            "A single request cannot target multiple regions.",
            regions=sorted(extracted_regions),
            reason="multiple_regions",
        )
    if extracted_regions and extracted_regions != {ir.region}:
        _raise(
            "adaptive_multi_region_not_supported",
            "Semantic region does not match the grounded region evidence.",
            region=ir.region,
            grounded_regions=sorted(extracted_regions),
            reason="region_evidence_mismatch",
        )
    if ir.region == "all" and not extracted_regions:
        if region_evidence:
            _raise(
                "adaptive_multi_region_not_supported",
                "Whole-image interpretation cannot carry local-region evidence.",
                region=ir.region,
                reason="unexpected_region_evidence",
            )
        return
    matching_evidence = [
        evidence
        for evidence in region_evidence
        if evidence.concept_id == ir.region
    ]
    if not matching_evidence:
        _raise(
            "adaptive_clarification_required",
            "A local edit region must be grounded in the original request.",
            region=ir.region,
            reason="missing_region_evidence",
        )
    for evidence in matching_evidence:
        matching_context_slots = [
            slot
            for slot in extraction.slots
            if not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.slot == "region_context"
            and str(slot.value) == ir.region
        ]
        matching_object_slots = [
            slot
            for slot in extraction.slots
            if not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.slot == "region_object"
            and str(slot.value) == ir.region
            and any(
                resolved.evidence.start == evidence.start
                and resolved.evidence.end == evidence.end
                and resolved.slot == "region_object"
                for resolved in resolved_scope.region_slots
            )
        ]
        if (
            not matching_context_slots
            and not matching_object_slots
            and not _is_registered_region_alias(
                evidence.raw_text,
                ir.region,
                registry,
            )
        ):
            _raise(
                "adaptive_clarification_required",
                "Region evidence is not a registered alias for that region.",
                region=ir.region,
                source_clause=evidence.raw_text,
                reason="unverifiable_region_evidence",
            )
        matching_slots = [
            slot
            for slot in extraction.slots
            if not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.namespace == "region"
            and slot.concept_id == ir.region
        ]
        matching_slots.extend(matching_context_slots)
        matching_slots.extend(matching_object_slots)
        contextual_axis_slots = [
            slot
            for slot in extraction.slots
            if not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.namespace == "axis"
            and slot.concept_id == ir.region
            and _has_nearby_region_scope(extraction, evidence)
        ]
        resolved_axis_slots = [
            slot
            for slot in extraction.slots
            if (
                evidence.slot == "region_axis_binding"
                and ir.decision_source == "semantic_registry"
                and not slot.is_ambiguous
                and slot.evidence.start == evidence.start
                and slot.evidence.end == evidence.end
                and slot.namespace == "axis"
                and slot.concept_id == ir.region
            )
        ]
        if resolved_axis_slots:
            resolution = resolve_semantic_scope(
                extraction,
                registry=registry,
            )
            if (
                resolution.region_id != ir.region
                or not any(
                    slot.evidence.start == evidence.start
                    and slot.evidence.end == evidence.end
                    for slot in resolution.region_slots
                )
            ):
                resolved_axis_slots = []
        if (
            evidence.slot == "region_axis_binding"
            and not resolved_axis_slots
        ):
            _raise(
                "adaptive_clarification_required",
                "Inferred region binding does not match resolved scope.",
                region=ir.region,
                source_clause=evidence.raw_text,
                reason="unverifiable_region_axis_binding",
            )
        if (
            evidence.slot == "region_object_binding"
            and not matching_object_slots
        ):
            _raise(
                "adaptive_clarification_required",
                "Participant-object evidence does not match resolved scope.",
                region=ir.region,
                source_clause=evidence.raw_text,
                reason="unverifiable_region_object_binding",
            )
        if (
            evidence.slot == "region"
            and not matching_slots
            and not contextual_axis_slots
        ):
            _raise(
                "adaptive_clarification_required",
                "Region evidence does not match a registered region concept.",
                region=ir.region,
                source_clause=evidence.raw_text,
                reason="unverifiable_region_evidence",
            )


def _is_registered_region_alias(
    raw_text: str,
    region: str,
    registry: ParameterRegistry,
) -> bool:
    definition = registry.regions.get(region)
    if definition is None:
        return False
    normalized = normalize_semantic_text(raw_text).text
    if any(
        normalize_semantic_text(alias.text).text == normalized
        for alias in definition.aliases
    ):
        return True
    axis_definition = registry.axes.get(region)
    return axis_definition is not None and any(
        normalize_semantic_text(alias.text).text == normalized
        for alias in axis_definition.aliases
    )


def _has_nearby_region_scope(
    extraction: SlotExtraction,
    evidence: RawSpanEvidence,
) -> bool:
    return any(
        slot.slot == "scope"
        and slot.value == "region"
        and slot.evidence.end <= evidence.start
        and evidence.start - slot.evidence.end <= 12
        for slot in extraction.slots
        if not slot.is_ambiguous
    )


def _validate_evidence_coverage(
    ir: SemanticIR,
    extraction: SlotExtraction,
) -> None:
    evidence = (
        *ir.evidence,
        *(
            item
            for operation in ir.operations
            for item in operation.evidence
        ),
    )
    covered = {
        (item.start, item.end, item.slot)
        for item in evidence
    }
    required_labels = {
        "axis": frozenset({"axis", "axis_direction"}),
        "direction": frozenset({"direction", "axis_direction"}),
        "strength": frozenset({"strength"}),
        "region": frozenset({"region", "region_axis_binding"}),
        "numeric": frozenset({"numeric"}),
        "numeric_relation": frozenset({"numeric_relation"}),
        "operation": frozenset({"reset", "operation"}),
        "observation_modifier": frozenset(
            {"direction", "observation_modifier"}
        ),
        "effect_reference": frozenset({"effect_reference"}),
        "relation": frozenset({"relation"}),
        "surface_action": frozenset({"surface_action"}),
        "terminal": frozenset({"terminal"}),
    }
    missing: list[dict[str, Any]] = []
    for slot in extraction.slots:
        labels = required_labels.get(
            "axis" if slot.namespace == "axis" else
            "region" if slot.namespace == "region" else
            "numeric" if slot.namespace == "numeric" else
            slot.slot
        )
        if labels is None:
            continue
        is_contextual_region_axis = (
            slot.namespace == "axis"
            and slot.concept_id == ir.region
            and (
                (
                    slot.evidence.start,
                    slot.evidence.end,
                    "region_axis_binding",
                )
                in covered
                or (
                    (
                        slot.evidence.start,
                        slot.evidence.end,
                        "region",
                    )
                    in covered
                    and _has_nearby_region_scope(
                        extraction,
                        slot.evidence,
                    )
                )
            )
        )
        is_resolved_support = (
            (
                slot.evidence.start,
                slot.evidence.end,
                "axis_support",
            )
            in covered
            or (
                slot.evidence.start,
                slot.evidence.end,
                "context_axis_support",
            )
            in covered
            or (
                slot.evidence.start,
                slot.evidence.end,
                "prior_event_support",
            )
            in covered
            or (
                slot.evidence.start,
                slot.evidence.end,
                "observation_attribute",
            )
            in covered
            or (
                slot.evidence.start,
                slot.evidence.end,
                "action_attribute",
            )
            in covered
            if slot.namespace == "axis"
            else (
                (
                    slot.evidence.start,
                    slot.evidence.end,
                    "axis_attribute_region_support",
                )
                in covered
                or (
                    slot.evidence.start,
                    slot.evidence.end,
                    "semantic_support",
                )
                in covered
            )
            if slot.namespace == "region"
            else (
                (
                    slot.evidence.start,
                    slot.evidence.end,
                    "effect_support",
                )
                in covered
                if slot.namespace == "effect"
                else (
                    slot.evidence.start,
                    slot.evidence.end,
                    "semantic_support",
                )
                in covered
            )
        )
        if (
            not is_contextual_region_axis
            and not is_resolved_support
            and not any(
                (slot.evidence.start, slot.evidence.end, label) in covered
                for label in labels
            )
        ):
            missing.append(
                {
                    "source_clause": slot.evidence.raw_text,
                    "start": slot.evidence.start,
                    "end": slot.evidence.end,
                    "slot": slot.slot,
                    "concept_id": slot.concept_id,
                    "reason": "unconsumed_semantic_slot",
                }
            )
    if missing:
        raise SemanticValidationError(
            code="adaptive_clarification_required",
            message=(
                "Semantic candidate did not account for every meaningful "
                "source concept."
            ),
            issues=tuple(missing),
        )


def _validate_lexical_grounding(
    ir: SemanticIR,
    registry: ParameterRegistry,
) -> SlotExtraction:
    extraction = extract_semantic_slots(
        normalize_semantic_text(ir.raw_prompt),
        registry=registry,
    )
    resolution = (
        resolve_semantic_scope(extraction, registry=registry)
        if ir.decision_source == "semantic_registry"
        else None
    )
    trusted_ambiguous_ids = _trusted_ambiguous_resolution_ids(
        ir,
        extraction=extraction,
        resolution=resolution,
        registry=registry,
    )
    unresolved_ambiguities = tuple(
        slot
        for slot in extraction.ambiguous_slots
        if id(slot) not in trusted_ambiguous_ids
    )
    if extraction.residue_spans or unresolved_ambiguities:
        issues = [
            {
                "source_clause": span.raw_text,
                "start": span.raw_start,
                "end": span.raw_end,
                "reason": span.reason,
            }
            for span in extraction.residue_spans
        ]
        issues.extend(
            {
                "source_clause": slot.evidence.raw_text,
                "start": slot.evidence.start,
                "end": slot.evidence.end,
                "reason": "ambiguous_lexical_match",
            }
            for slot in unresolved_ambiguities
        )
        raise SemanticValidationError(
            code="adaptive_clarification_required",
            message=(
                "Semantic candidate did not account for all meaningful "
                "source text."
            ),
            issues=tuple(issues),
        )

    trusted_guard_ids = _trusted_consumed_guard_support_ids(
        ir,
        resolution=resolution,
    )
    forbidden = [
        slot
        for slot in extraction.slots
        if slot.slot in {"negation", "guard", "terminal"}
        and id(slot) not in trusted_guard_ids
        and not (
            slot.slot == "terminal"
            and ir.terminal_intent is not None
            and str(slot.value) == ir.terminal_intent
        )
    ]
    if forbidden:
        first = forbidden[0]
        code = (
            "adaptive_disjunction_not_supported"
            if first.concept_id
            in {"disjunction", "disjunction_or_still"}
            else "adaptive_exclusion_not_supported"
            if first.concept_id == "exclusion"
            else "adaptive_operation_conflict"
            if first.slot == "terminal"
            else "adaptive_clarification_required"
        )
        _raise(
            code,
            "Guarded, negated, or terminal text cannot be ignored.",
            source_clause=first.evidence.raw_text,
            concept_id=first.concept_id,
            reason="guard_requires_atomic_rejection",
        )

    contextual_region_spans = {
        (evidence.start, evidence.end)
        for evidence in (
            *ir.evidence,
            *(
                item
                for operation in ir.operations
                for item in operation.evidence
            ),
        )
        if evidence.slot in _REGION_EVIDENCE_SLOTS
        and evidence.concept_id == ir.region
        and _is_registered_region_alias(
            evidence.raw_text,
            ir.region,
            registry,
        )
        and (
            evidence.slot == "region_axis_binding"
            or _has_nearby_region_scope(extraction, evidence)
        )
    }
    resolved_support_spans = {
        (evidence.start, evidence.end)
        for operation in ir.operations
        for evidence in operation.evidence
        if evidence.slot
        in {
            "axis_support",
            "context_axis_support",
            "prior_event_support",
            "observation_attribute",
            "action_attribute",
        }
    }
    extracted_axes = {
        str(slot.value)
        for slot in extraction.slots
        if not slot.is_ambiguous and slot.namespace == "axis"
        and (slot.evidence.start, slot.evidence.end)
        not in contextual_region_spans
        and (slot.evidence.start, slot.evidence.end)
        not in resolved_support_spans
    }
    requested_axes = {operation.axis_id for operation in ir.operations}
    if extracted_axes != requested_axes:
        _raise(
            "adaptive_operation_conflict",
            "Semantic operations do not exactly match grounded axis evidence.",
            grounded_axes=sorted(extracted_axes),
            requested_axes=sorted(requested_axes),
            reason="axis_set_evidence_mismatch",
        )
    return extraction


def _trusted_ambiguous_resolution_ids(
    ir: SemanticIR,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> frozenset[int]:
    """Re-prove every deterministically selected ambiguous interpretation."""

    if (
        ir.decision_source != "semantic_registry"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
    ):
        return frozenset()
    trusted: set[int] = set()
    for operation in ir.operations:
        for evidence in operation.evidence:
            if evidence.slot not in {
                "resolved_direction",
                "effect_support",
            }:
                continue
            binding = (
                _resolved_direction_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
                if evidence.slot == "resolved_direction"
                else _resolved_effect_binding(
                    operation,
                    evidence,
                    extraction=extraction,
                    resolution=resolution,
                    registry=registry,
                )
            )
            if binding is not None:
                source, _selected, _group = binding
                if source.is_ambiguous:
                    trusted.add(id(source))
    return frozenset(trusted)


def _resolved_ambiguous_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Return one exact source/selection/group binding, otherwise ``None``."""

    if (
        evidence.slot not in {"resolved_direction", "effect_support"}
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
    ):
        return None
    operation_groups = tuple(
        group
        for group in resolution.operation_groups
        if group.axis_id == operation.axis_id
    )
    if len(operation_groups) != 1:
        return None
    group = operation_groups[0]
    if (
        operation.operation_type == "relative"
        and group.resolved_direction != operation.direction
    ):
        return None
    selected_pool = (
        group.direction_slots
        if evidence.slot == "resolved_direction"
        else group.supporting_slots
    )
    selected_matches = tuple(
        slot
        for slot in selected_pool
        if (
            slot.evidence.slot == evidence.slot
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and str(slot.concept_id) == evidence.concept_id
            and (
                (
                    slot.namespace == "shared"
                    and slot.slot == "direction"
                    and slot.value in {-1, 1}
                )
                if evidence.slot == "resolved_direction"
                else (
                    slot.namespace == "effect"
                    and slot.slot == "effect_state"
                    and slot.requested_direction in {-1, 1}
                )
            )
        )
    )
    source_matches = tuple(
        source
        for source in extraction.ambiguous_slots
        if (
            source.evidence.start == evidence.start
            and source.evidence.end == evidence.end
            and source.evidence.raw_text == evidence.raw_text
        )
    )
    if len(selected_matches) != 1 or len(source_matches) != 1:
        return None
    selected = selected_matches[0]
    source = source_matches[0]
    selected_key = selected.interpretations[0].semantic_key
    if selected_key not in {
        interpretation.semantic_key
        for interpretation in source.interpretations
    }:
        return None
    return source, selected, group


def _resolved_direction_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove one resolver-derived direction from its typed source."""

    if evidence.slot != "resolved_direction":
        return None
    ambiguous = _resolved_ambiguous_binding(
        operation,
        evidence,
        extraction=extraction,
        resolution=resolution,
    )
    if ambiguous is not None:
        return ambiguous

    effect = _resolved_effect_direction_binding(
        operation,
        evidence,
        extraction=extraction,
        resolution=resolution,
        registry=registry,
    )
    if effect is not None:
        return effect
    negated_removal = _resolved_negated_removal_amount_binding(
        operation,
        evidence,
        extraction=extraction,
        resolution=resolution,
    )
    if negated_removal is not None:
        return negated_removal

    if (
        resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or operation.direction not in {-1, 1}
    ):
        return None
    operation_groups = tuple(
        group
        for group in resolution.operation_groups
        if group.axis_id == operation.axis_id
    )
    if len(operation_groups) != 1:
        return None
    group = operation_groups[0]
    selected = tuple(
        slot
        for slot in (*group.direction_slots, *group.supporting_slots)
        if (
            slot.evidence.slot == "resolved_direction"
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and slot.concept_id == "continuation_more"
            and slot.value == 1
        )
    )
    sources = tuple(
        slot
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and slot.slot == "relation"
            and slot.concept_id == "relation_continue"
            and slot.value == "continue"
        )
    )
    if (
        group.axis_slot.axis_role == "axis"
        and group.axis_slot.match_kind == "axis"
    ):
        expected_direction = group.direction_multiplier
    elif (
        group.axis_slot.match_kind in {"action", "descriptor"}
        and group.axis_slot.requested_direction in {-1, 1}
    ):
        expected_direction = int(group.axis_slot.requested_direction)
    else:
        expected_direction = None
    if (
        len(selected) != 1
        or len(sources) != 1
        or sources[0] not in group.relation_slots
        or expected_direction not in {-1, 1}
        or not group.strength_slots
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.observation_modifier_slots
        or group.state_link_slots
        or group.guard_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or group.resolved_direction != expected_direction
        or operation.direction != expected_direction
    ):
        return None
    return sources[0], selected[0], group


def _resolved_negated_removal_amount_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove the narrow negated inverse-effect amount construction."""

    if (
        evidence.slot != "resolved_direction"
        or evidence.concept_id != "negated_removal_amount_direction"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or operation.operation_kind not in {"explicit_axis", "macro"}
        or operation.direction != -1
        or len(resolution.operation_groups) != 1
    ):
        return None
    group = resolution.operation_groups[0]
    selected = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.evidence.slot == "resolved_direction"
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and slot.concept_id == "negated_removal_amount_direction"
            and slot.value == 1
        )
    )
    sources = tuple(
        slot
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and slot.slot == "negation"
            and slot.concept_id == "negation"
            and slot.value is True
        )
    )
    direct = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "direction"
            and slot.concept_id == "direction_negative"
            and slot.value == -1
        )
    )
    comparative = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "direction"
            and slot.concept_id == "comparative_more"
            and slot.value == 1
        )
    )
    degree = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "comparison_reference"
            and slot.concept_id == "degree_comparison_reference"
            and slot.value == "degree"
        )
    )
    if (
        group.axis_id != operation.axis_id
        or group.axis_slot.axis_role != "axis"
        or group.axis_slot.match_kind != "axis"
        or group.direction_multiplier != -1
        or group.resolved_direction != operation.direction
        or len(selected) != 1
        or len(sources) != 1
        or sources[0] not in group.supporting_slots
        or len(direct) != 1
        or len(comparative) != 1
        or len(degree) != 1
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.observation_modifier_slots
        or group.state_link_slots
        or group.guard_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or not (
            sources[0].normalized_end <= direct[0].normalized_start
            and direct[0].normalized_end <= degree[0].normalized_start
            and degree[0].normalized_end <= comparative[0].normalized_start
            and comparative[0].normalized_end
            <= group.axis_slot.normalized_start
        )
    ):
        return None
    return sources[0], selected[0], group


def _resolved_relation_support_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove a continuation multiplier demoted to descriptor support."""

    if (
        evidence.slot != "resolved_relation_support"
        or evidence.concept_id != "continuation_more"
    ):
        return None
    return _resolved_direction_binding(
        operation,
        RawSpanEvidence(
            start=evidence.start,
            end=evidence.end,
            raw_text=evidence.raw_text,
            slot="resolved_direction",
            concept_id=evidence.concept_id,
            language=evidence.language,
            confidence=evidence.confidence,
        ),
        extraction=extraction,
        resolution=resolution,
        registry=registry,
    )


def _negated_comparative_state_direction(
    group: Any,
    source: SemanticSlot,
) -> int | None:
    """Re-prove the observed state behind one typed upper-bound request.

    A descriptor may be followed by one explicit inverse remedy.  That remedy
    is not another description of the state; source order and opposite
    polarity prove the state/remedy roles.  Axis nouns without fused polarity
    keep their single direct direction as the observed state.
    """

    direct_state_slots = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.evidence.slot != "resolved_direction"
            and slot.value in {-1, 1}
            and slot.concept_id
            not in {"comparative_more", "comparative_less"}
        )
    )
    direct_state_directions = {
        int(slot.value) * group.direction_multiplier
        for slot in direct_state_slots
    }
    fused_state_direction = (
        int(group.axis_slot.requested_direction)
        if (
            group.axis_slot.match_kind in {"descriptor", "observation"}
            and group.axis_slot.requested_direction in {-1, 1}
        )
        else None
    )
    if fused_state_direction is not None and direct_state_directions:
        descriptor_remedy = bool(
            direct_state_directions == {-fused_state_direction}
            and all(
                group.axis_slot.normalized_end <= slot.normalized_start
                for slot in direct_state_slots
            )
            and source.normalized_end
            <= group.axis_slot.normalized_start
        )
        return fused_state_direction if descriptor_remedy else None

    state_directions = set(direct_state_directions)
    if fused_state_direction is not None:
        state_directions.add(fused_state_direction)
    return (
        next(iter(state_directions))
        if len(state_directions) == 1
        else None
    )


def _controller_contract_support_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, Any, int] | None:
    """Re-prove a shared alias controller contract and correction polarity."""

    if (
        evidence.slot != "controller_contract_support"
        or operation.operation_kind
        not in {"macro", "observation", "explicit_axis"}
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
    ):
        return None
    sources = tuple(
        slot
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and str(slot.concept_id) == evidence.concept_id
        )
    )
    if len(sources) != 1:
        return None
    source = sources[0]
    definition = registry.shared_concepts.get(str(source.concept_id))
    if definition is None:
        return None
    contract = registry.resolve_shared_alias_contract(
        definition.slot,
        source.evidence.raw_text,
        source.language,
    )
    macro_contract = bool(
        operation.operation_kind == "macro"
        and contract is not None
        and contract.mode == "macro"
        and contract.relation == "initial"
        and contract.companions is True
    )
    correction_contract = bool(
        operation.operation_kind in {"observation", "explicit_axis"}
        and contract is not None
        and contract.mode == "explicit_axis"
        and contract.relation == "correct"
        and contract.companions is False
    )
    if not (macro_contract or correction_contract):
        return None
    groups = tuple(
        group
        for group in resolution.operation_groups
        if (
            group.axis_id == operation.axis_id
            and source in group.supporting_slots
        )
    )
    if len(groups) != 1:
        return None
    group = groups[0]
    direct_source_slots = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.evidence.slot != "resolved_direction"
            and slot.value in {-1, 1}
            and slot.concept_id
            not in {"comparative_more", "comparative_less"}
        )
    )
    if any(
        not any(
            evidence_slot.slot in {"direction", "axis_direction"}
            and evidence_slot.start == source_slot.evidence.start
            and evidence_slot.end == source_slot.evidence.end
            and evidence_slot.raw_text == source_slot.evidence.raw_text
            and evidence_slot.concept_id
            == str(source_slot.concept_id)
            for evidence_slot in operation.evidence
        )
        for source_slot in direct_source_slots
    ):
        return None
    resolved_observations = tuple(
        slot
        for slot in group.observation_modifier_slots
        if (
            slot.evidence.slot == "resolved_observation"
            and slot.concept_id == "negated_upper_bound"
            and slot.value == "too"
            and slot.evidence.start == source.evidence.start
            and slot.evidence.end == source.evidence.end
        )
    )
    state_direction = _negated_comparative_state_direction(
        group,
        source,
    )
    if (
        len(resolved_observations) != 1
        or state_direction not in {-1, 1}
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.state_link_slots
        or group.guard_slots
        or group.action_attribute_slots
        or group.surface_action_slots
    ):
        return None
    expected_direction = -int(state_direction)
    if operation.direction != expected_direction:
        return None
    return source, group, expected_direction


def _resolved_effect_direction_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove a direction derived from one registry effect binding."""

    support_evidence = next(
        (
            item
            for item in operation.evidence
            if (
                item.slot == "effect_support"
                and item.start == evidence.start
                and item.end == evidence.end
                and item.raw_text == evidence.raw_text
            )
        ),
        None,
    )
    if support_evidence is None:
        return None
    support_binding = _resolved_effect_binding(
        operation,
        support_evidence,
        extraction=extraction,
        resolution=resolution,
        registry=registry,
    )
    if support_binding is None:
        return None
    source, selected_effect, group = support_binding
    selected_directions = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.evidence.slot == "resolved_direction"
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and str(slot.concept_id) == evidence.concept_id
            and slot.value in {-1, 1}
        )
    )
    effect_interpretations = tuple(
        item
        for item in source.interpretations
        if (
            item.namespace == "effect"
            and item.slot == "effect_state"
            and item.concept_id == selected_effect.concept_id
            and item.requested_direction in {-1, 1}
        )
    )
    binding = registry.get_axis_effect_binding(
        operation.axis_id,
        str(selected_effect.concept_id),
    )
    if (
        len(selected_directions) != 1
        or len(effect_interpretations) != 1
        or binding is None
    ):
        return None
    expected = (
        int(effect_interpretations[0].requested_direction)
        * binding.direction_multiplier
    )
    selected = selected_directions[0]
    if (
        selected.value != expected
        or operation.direction != expected
        or group.resolved_direction != expected
    ):
        return None
    return source, selected, group


def _resolved_effect_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove one effect-state support edge from registry polarity."""

    if (
        evidence.slot != "effect_support"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
    ):
        return None
    operation_groups = tuple(
        group
        for group in resolution.operation_groups
        if group.axis_id == operation.axis_id
    )
    if len(operation_groups) != 1:
        return None
    group = operation_groups[0]
    selected_matches = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.namespace == "effect"
            and slot.slot == "effect_state"
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and str(slot.concept_id) == evidence.concept_id
            and slot.requested_direction in {-1, 1}
        )
    )
    source_matches = tuple(
        source
        for source in extraction.slots
        if (
            source.evidence.start == evidence.start
            and source.evidence.end == evidence.end
            and source.evidence.raw_text == evidence.raw_text
            and any(
                item.namespace == "effect"
                and item.slot == "effect_state"
                and item.concept_id == evidence.concept_id
                and item.requested_direction in {-1, 1}
                for item in source.interpretations
            )
        )
    )
    if len(selected_matches) != 1 or len(source_matches) != 1:
        return None
    selected = selected_matches[0]
    source = source_matches[0]
    effect_interpretations = tuple(
        item
        for item in source.interpretations
        if (
            item.namespace == "effect"
            and item.slot == "effect_state"
            and item.concept_id == evidence.concept_id
            and item.requested_direction in {-1, 1}
        )
    )
    binding = registry.get_axis_effect_binding(
        operation.axis_id,
        evidence.concept_id,
    )
    if len(effect_interpretations) != 1 or binding is None:
        return None
    state_direction = int(effect_interpretations[0].requested_direction)
    source_clause = next(
        (
            clause
            for clause in resolution.clauses
            if (
                clause.normalized_start <= source.normalized_start
                and source.normalized_end <= clause.normalized_end
            )
        ),
        None,
    )
    if source_clause is None:
        return None
    modifier_values = {
        str(slot.value)
        for slot in source_clause.slots
        if (
            not slot.is_ambiguous
            and slot.slot == "observation_modifier"
            and str(slot.value)
            in {"too", "too_much", "not_enough", "mild"}
        )
    }
    if "too" in modifier_values and len(modifier_values) > 1:
        modifier_values.remove("too")
    produced_effect = registry.resolve_axis_effect(
        operation.axis_id,
        evidence.concept_id,
        int(operation.direction),
    )
    if modifier_values:
        if len(modifier_values) != 1:
            return None
        desired_effect = (
            state_direction
            if "not_enough" in modifier_values
            else -state_direction
        )
        if produced_effect != desired_effect:
            return None
    else:
        derived = tuple(
            slot
            for slot in group.direction_slots
            if (
                slot.evidence.slot == "resolved_direction"
                and slot.evidence.start == evidence.start
                and slot.evidence.end == evidence.end
                and slot.evidence.raw_text == evidence.raw_text
                and slot.value in {-1, 1}
            )
        )
        expected_direction = state_direction * binding.direction_multiplier
        if len(derived) == 1:
            if (
                derived[0].value != expected_direction
                or operation.direction != expected_direction
            ):
                return None
        else:
            continuation = tuple(
                slot
                for slot in group.direction_slots
                if (
                    slot.evidence.slot == "resolved_direction"
                    and slot.concept_id == "continuation_more"
                    and slot.value == 1
                )
            )
            relation_sources = tuple(
                slot
                for slot in group.relation_slots
                if (
                    slot.slot == "relation"
                    and slot.concept_id == "relation_continue"
                    and slot.value == "continue"
                )
            )
            if (
                derived
                or len(continuation) != 1
                or len(relation_sources) != 1
                or expected_direction != 1
                or operation.direction != 1
                or not group.strength_slots
            ):
                return None
    if group.resolved_direction != operation.direction:
        return None
    return source, selected, group


def _leading_axis_observation_structure(
    operation: SemanticOperation,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove a registry-declared leading persistent-state observation."""

    if (
        resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or operation.operation_kind != "observation"
        or len(resolution.operation_groups) != 1
        or len(resolution.clauses) != 1
    ):
        return None
    group = resolution.operation_groups[0]
    first = resolution.clauses[0]
    sources = []
    for slot in extraction.slots:
        definition = registry.shared_concepts.get(
            str(slot.concept_id)
        )
        if (
            not slot.is_ambiguous
            and slot.namespace == "shared"
            and slot.slot == "conjunction"
            and definition is not None
            and definition.leading_axis_observation
            and definition.observation_strength is not None
        ):
            sources.append((slot, definition))
    if len(sources) != 1:
        return None
    source, definition = sources[0]
    selected_observations = tuple(
        slot
        for slot in group.observation_modifier_slots
        if (
            slot.evidence.slot == "resolved_observation"
            and slot.evidence.start == source.evidence.start
            and slot.evidence.end == source.evidence.end
            and slot.evidence.raw_text == source.evidence.raw_text
            and slot.concept_id == source.concept_id
            and slot.namespace == "shared"
            and slot.slot == "observation_modifier"
            and slot.value == "too_much"
        )
    )
    selected_strengths = tuple(
        slot
        for slot in group.strength_slots
        if (
            slot.evidence.slot == "resolved_strength"
            and slot.evidence.start == source.evidence.start
            and slot.evidence.end == source.evidence.end
            and slot.evidence.raw_text == source.evidence.raw_text
            and slot.concept_id == source.concept_id
            and slot.namespace == "shared"
            and slot.slot == "strength"
            and str(slot.value) == definition.observation_strength
        )
    )
    clause_slots = tuple(
        slot for slot in first.slots if not slot.is_ambiguous
    )
    expected_direction = -group.direction_multiplier
    if (
        first.boundary_before != "conjunction"
        or len(first.connector_before) != 1
        or first.connector_before[0] is not source
        or source not in group.supporting_slots
        or len(group.supporting_slots) != 1
        or group.axis_id != operation.axis_id
        or group.clause_index != first.index
        or group.axis_slot.axis_role != "axis"
        or group.axis_slot.match_kind != "axis"
        or group.axis_slot.requested_direction is not None
        or group.direction_multiplier != -1
        or len(clause_slots) != 1
        or clause_slots[0] is not group.axis_slot
        or source.normalized_end > group.axis_slot.normalized_start
        or len(selected_observations) != 1
        or len(selected_strengths) != 1
        or group.region_slot is not None
        or group.direction_slots
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.state_link_slots
        or group.guard_slots
        or group.ambiguous_slots
        or group.attribute_axis_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or group.request_force_proven
        or operation.direction != expected_direction
        or operation.strength != definition.observation_strength
    ):
        return None
    return (
        source,
        selected_observations[0],
        selected_strengths[0],
        group,
    )


def _resolved_observation_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove a negated-comparative state without trusting derived labels."""

    contrastive_command = _contrastive_negated_comparative_command_binding(
        operation,
        evidence,
        extraction=extraction,
        resolution=resolution,
    )
    if contrastive_command is not None:
        return contrastive_command
    controller_contract_operation = bool(
        operation.operation_kind in {"macro", "explicit_axis"}
        and any(
            _controller_contract_support_binding(
                operation,
                support,
                extraction=extraction,
                resolution=resolution,
                registry=registry,
            )
            is not None
            for support in operation.evidence
            if support.slot == "controller_contract_support"
        )
    )
    if (
        evidence.slot != "resolved_observation"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or (
            operation.operation_kind != "observation"
            and not controller_contract_operation
        )
    ):
        return None
    leading = _leading_axis_observation_structure(
        operation,
        extraction=extraction,
        resolution=resolution,
        registry=registry,
    )
    if (
        leading is not None
        and leading[1].evidence.start == evidence.start
        and leading[1].evidence.end == evidence.end
        and leading[1].evidence.raw_text == evidence.raw_text
        and str(leading[1].concept_id) == evidence.concept_id
    ):
        return leading[0], leading[1], leading[3]
    operation_groups = tuple(
        group
        for group in resolution.operation_groups
        if group.axis_id == operation.axis_id
    )
    if len(operation_groups) != 1:
        return None
    group = operation_groups[0]
    selected = tuple(
        slot
        for slot in group.observation_modifier_slots
        if (
            slot.evidence.slot == "resolved_observation"
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and slot.concept_id == "negated_upper_bound"
            and slot.value == "too"
        )
    )
    sources = tuple(
        slot
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and slot.slot == "negated_comparative"
            and slot.value == "less"
        )
    )
    state_direction = _negated_comparative_state_direction(
        group,
        sources[0],
    ) if len(sources) == 1 else None
    if (
        len(selected) != 1
        or len(sources) != 1
        or sources[0] not in group.supporting_slots
        or state_direction not in {-1, 1}
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.state_link_slots
        or group.guard_slots
        or group.action_attribute_slots
        or group.surface_action_slots
    ):
        return None
    expected = -int(state_direction)
    if operation.direction != expected:
        return None
    return sources[0], selected[0], group


def _contrastive_negated_comparative_command_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove one exact ``brighter, but not that bright`` command."""

    if (
        evidence.slot != "resolved_observation"
        or evidence.concept_id != "negated_upper_bound"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or operation.operation_kind != "macro"
        or operation.strength != "subtle"
        or operation.region != "all"
        or len(resolution.operation_groups) != 1
    ):
        return None
    group = resolution.operation_groups[0]
    selected = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.evidence.slot == "resolved_observation"
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and slot.concept_id == "negated_upper_bound"
            and slot.value == "too"
        )
    )
    sources = tuple(
        slot
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and slot.slot == "negated_comparative"
            and slot.concept_id == "negated_comparative_less"
            and slot.value == "less"
            and slot in group.supporting_slots
        )
    )
    observed_axes = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.namespace == "axis"
            and str(slot.value) == operation.axis_id
            and slot.match_kind == "descriptor"
            and slot.requested_direction == operation.direction
            and evidence.end <= slot.evidence.start
        )
    )
    if (
        len(selected) != 1
        or len(sources) != 1
        or len(observed_axes) != 1
        or group.axis_id != operation.axis_id
        or group.axis_slot.match_kind != "descriptor"
        or group.axis_slot.requested_direction != operation.direction
        or group.resolved_direction != operation.direction
        or group.region_slot is not None
        or len(group.strength_slots) != 1
        or str(group.strength_slots[0].value) != "subtle"
        or group.direction_slots
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.observation_modifier_slots
        or group.state_link_slots
        or group.guard_slots
        or group.ambiguous_slots
        or group.attribute_axis_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or {
            id(slot) for slot in group.supporting_slots
        }
        != {
            id(selected[0]),
            id(sources[0]),
            id(observed_axes[0]),
        }
    ):
        return None
    bound_clause = next(
        (
            clause
            for clause in resolution.clauses
            if (
                clause.normalized_start == sources[0].normalized_start
                and observed_axes[0].normalized_end == clause.normalized_end
                and sources[0] in clause.slots
                and observed_axes[0] in clause.slots
            )
        ),
        None,
    )
    if (
        bound_clause is None
        or bound_clause.boundary_before != "contrastive"
        or group.clause.boundary_after != "contrastive"
        or bound_clause.index != group.clause_index + 1
        or tuple(bound_clause.slots) != (sources[0], observed_axes[0])
        or tuple(
            slot
            for slot in group.clause.slots
            if slot.namespace == "axis"
        )
        != (group.axis_slot,)
        or any(
            slot.namespace == "region"
            or slot.slot
            in {
                "guard",
                "negation",
                "numeric",
                "numeric_relation",
                "operation",
            }
            for slot in group.clause.slots
        )
    ):
        return None
    source_support = tuple(
        item
        for item in operation.evidence
        if (
            item.slot == "semantic_support"
            and item.concept_id == str(sources[0].concept_id)
            and item.start == sources[0].evidence.start
            and item.end == sources[0].evidence.end
            and item.raw_text == sources[0].evidence.raw_text
        )
    )
    axis_support = tuple(
        item
        for item in operation.evidence
        if (
            item.slot == "axis_support"
            and item.concept_id == str(observed_axes[0].concept_id)
            and item.start == observed_axes[0].evidence.start
            and item.end == observed_axes[0].evidence.end
            and item.raw_text == observed_axes[0].evidence.raw_text
        )
    )
    if len(source_support) != 1 or len(axis_support) != 1:
        return None
    return sources[0], selected[0], group


def _composed_observation_strength_structure(
    operation: SemanticOperation,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove strength derived from a typed mild observation cue."""

    if (
        resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or operation.operation_kind != "observation"
    ):
        return None
    operation_groups = tuple(
        group
        for group in resolution.operation_groups
        if group.axis_id == operation.axis_id
    )
    if len(operation_groups) != 1:
        return None
    group = operation_groups[0]
    sources: list[tuple[SemanticSlot, object]] = []
    for source in extraction.slots:
        definition = registry.shared_concepts.get(
            str(source.concept_id)
        )
        if (
            not source.is_ambiguous
            and source.namespace == "shared"
            and source.slot == "observation_modifier"
            and definition is not None
            and definition.observation_strength is not None
            and source in group.supporting_slots
        ):
            sources.append((source, definition))
    if len(sources) != 1:
        return None
    source, definition = sources[0]
    selected = tuple(
        slot
        for slot in group.strength_slots
        if (
            slot.evidence.slot == "resolved_strength"
            and slot.evidence.start == source.evidence.start
            and slot.evidence.end == source.evidence.end
            and slot.evidence.raw_text == source.evidence.raw_text
            and slot.concept_id == source.concept_id
            and slot.namespace == "shared"
            and slot.slot == "strength"
            and str(slot.value) == definition.observation_strength
        )
    )
    specific = tuple(
        slot
        for slot in group.observation_modifier_slots
        if str(slot.value) in {"too", "too_much", "not_enough"}
    )
    if (
        len(selected) != 1
        or len(group.strength_slots) != 1
        or len(specific) != 1
        or len(group.observation_modifier_slots) != 1
        or source.normalized_end > specific[0].normalized_start
        or group.direction_slots
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.guard_slots
        or group.ambiguous_slots
        or group.attribute_axis_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or operation.strength != definition.observation_strength
    ):
        return None
    return source, selected[0], group


def _persistent_observation_strength_structure(
    operation: SemanticOperation,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove a persistent same-axis observation's derived strength."""

    if (
        resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or operation.operation_kind != "observation"
    ):
        return None
    operation_groups = tuple(
        group
        for group in resolution.operation_groups
        if group.axis_id == operation.axis_id
    )
    if len(operation_groups) != 1:
        return None
    group = operation_groups[0]
    sources: list[tuple[SemanticSlot, object]] = []
    for source in extraction.slots:
        definition = registry.shared_concepts.get(
            str(source.concept_id)
        )
        if (
            not source.is_ambiguous
            and source.namespace == "shared"
            and source.slot == "clause_aspect"
            and definition is not None
            and definition.observation_strength is not None
            and source in group.clause.slots
            and source in group.supporting_slots
        ):
            sources.append((source, definition))
    if len(sources) != 1:
        return None
    source, definition = sources[0]
    prior_axes = tuple(
        slot
        for slot in group.supporting_slots
        if (
            not slot.is_ambiguous
            and slot.namespace == "axis"
            and slot.axis_role == "axis"
            and slot.match_kind == "axis"
            and str(slot.value) == group.axis_id
        )
    )
    selected = tuple(
        slot
        for slot in group.strength_slots
        if (
            slot.evidence.slot == "resolved_strength"
            and slot.evidence.start == source.evidence.start
            and slot.evidence.end == source.evidence.end
            and slot.evidence.raw_text == source.evidence.raw_text
            and slot.concept_id == source.concept_id
            and slot.namespace == "shared"
            and slot.slot == "strength"
            and str(slot.value) == definition.observation_strength
        )
    )
    if (
        len(prior_axes) != 1
        or len(selected) != 1
        or len(group.strength_slots) != 1
        or group.axis_slot.match_kind != "observation"
        or group.axis_slot.requested_direction not in {-1, 1}
        or not (
            prior_axes[0].normalized_end <= source.normalized_start
            and source.normalized_end
            <= group.axis_slot.normalized_start
        )
        or group.direction_slots
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.observation_modifier_slots
        or group.state_link_slots
        or group.guard_slots
        or group.ambiguous_slots
        or group.attribute_axis_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or operation.strength != definition.observation_strength
    ):
        return None
    return source, selected[0], group


def _resolved_strength_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove a registry-declared preposed comparative strength."""

    if (
        evidence.slot != "resolved_strength"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
    ):
        return None
    operation_groups = tuple(
        group
        for group in resolution.operation_groups
        if group.axis_id == operation.axis_id
    )
    if len(operation_groups) != 1:
        return None
    group = operation_groups[0]
    leading = _leading_axis_observation_structure(
        operation,
        extraction=extraction,
        resolution=resolution,
        registry=registry,
    )
    if (
        leading is not None
        and leading[2].evidence.start == evidence.start
        and leading[2].evidence.end == evidence.end
        and leading[2].evidence.raw_text == evidence.raw_text
        and str(leading[2].concept_id) == evidence.concept_id
    ):
        return leading[0], leading[2], leading[3]
    composed_observation = _composed_observation_strength_structure(
        operation,
        extraction=extraction,
        resolution=resolution,
        registry=registry,
    )
    if (
        composed_observation is not None
        and composed_observation[1].evidence.start == evidence.start
        and composed_observation[1].evidence.end == evidence.end
        and composed_observation[1].evidence.raw_text
        == evidence.raw_text
        and str(composed_observation[1].concept_id)
        == evidence.concept_id
    ):
        return composed_observation
    persistent_observation = _persistent_observation_strength_structure(
        operation,
        extraction=extraction,
        resolution=resolution,
        registry=registry,
    )
    if (
        persistent_observation is not None
        and persistent_observation[1].evidence.start == evidence.start
        and persistent_observation[1].evidence.end == evidence.end
        and persistent_observation[1].evidence.raw_text
        == evidence.raw_text
        and str(persistent_observation[1].concept_id)
        == evidence.concept_id
    ):
        return persistent_observation
    selected_matches = tuple(
        slot
        for slot in group.strength_slots
        if (
            slot.evidence.slot == "resolved_strength"
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and str(slot.concept_id) == evidence.concept_id
            and slot.namespace == "shared"
            and slot.slot == "strength"
            and str(slot.value) in {"subtle", "normal", "strong"}
        )
    )
    if (
        len(selected_matches) == 1
        and selected_matches[0].concept_id
        == "negated_removal_amount_subtle"
        and selected_matches[0].value == "subtle"
    ):
        direction_evidence = next(
            (
                item
                for item in operation.evidence
                if (
                    item.slot == "resolved_direction"
                    and item.concept_id
                    == "negated_removal_amount_direction"
                    and item.start == evidence.start
                    and item.end == evidence.end
                    and item.raw_text == evidence.raw_text
                )
            ),
            None,
        )
        binding = (
            None
            if direction_evidence is None
            else _resolved_negated_removal_amount_binding(
                operation,
                direction_evidence,
                extraction=extraction,
                resolution=resolution,
            )
        )
        if (
            binding is None
            or binding[2] is not group
            or operation.strength != "subtle"
        ):
            return None
        return binding[0], selected_matches[0], group
    if (
        len(selected_matches) == 1
        and selected_matches[0].concept_id == "upper_bound_subtle"
        and selected_matches[0].value == "subtle"
    ):
        upper_bound = _upper_bound_group_binding(
            operation,
            resolution=resolution,
        )
        if upper_bound is None:
            return None
        guard, proven_group = upper_bound
        selected = selected_matches[0]
        if (
            proven_group is not group
            or guard.evidence.start != evidence.start
            or guard.evidence.end != evidence.end
            or guard.evidence.raw_text != evidence.raw_text
            or operation.strength != "subtle"
        ):
            return None
        return guard, selected, group

    if (
        operation.operation_type == "relative"
        and group.resolved_direction != operation.direction
    ):
        return None

    source_matches = tuple(
        source
        for source in extraction.slots
        if (
            not source.is_ambiguous
            and source.evidence.start == evidence.start
            and source.evidence.end == evidence.end
            and source.evidence.raw_text == evidence.raw_text
            and source.namespace == "shared"
            and source.slot == "direction"
            and source.value == 1
            and str(source.concept_id) == evidence.concept_id
        )
    )
    if len(selected_matches) != 1 or len(source_matches) != 1:
        return None
    selected = selected_matches[0]
    source = source_matches[0]
    definition = registry.shared_concepts.get(str(source.concept_id))
    direct_slots = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.slot == "direction"
            and slot.value in {-1, 1}
            and slot.concept_id
            not in {"comparative_more", "comparative_less"}
        )
    )
    if (
        definition is None
        or definition.preposed_strength != str(selected.value)
        or not direct_slots
        or source.normalized_end
        > min(slot.normalized_start for slot in direct_slots)
    ):
        return None
    return source, selected, group


def _upper_bound_group_binding(
    operation: SemanticOperation,
    *,
    resolution: Any,
) -> tuple[SemanticSlot, Any] | None:
    """Re-prove the narrow bare-negation upper-bound construction."""

    if (
        resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or operation.operation_kind != "observation"
        or operation.strength != "subtle"
        or len(resolution.operation_groups) != 1
    ):
        return None
    group = resolution.operation_groups[0]
    if group.axis_id != operation.axis_id:
        return None
    negations = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "negation"
            and slot.concept_id == "negation"
            and slot.value is True
            and slot not in group.guard_slots
        )
    )
    observation_values = {
        str(slot.value) for slot in group.observation_modifier_slots
    }
    if (
        len(negations) != 1
        or not observation_values
        or not observation_values.issubset({"too", "too_much"})
        or group.state_link_slots
        or group.direction_slots
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.guard_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or any(
            slot.namespace == "region"
            or slot.slot
            in {
                "clause_aspect",
                "clause_modal",
                "clause_subject",
                "conjunction",
                "existential",
                "region_context",
                "region_object",
                "request_marker",
                "request_predicate",
            }
            for slot in group.clause.slots
        )
    ):
        return None
    is_direct_observation = (
        group.axis_slot.match_kind == "observation"
        and group.axis_slot.requested_direction in {-1, 1}
    )
    state_direction = (
        int(group.axis_slot.requested_direction)
        if (
            group.axis_slot.match_kind
            in {"action", "descriptor", "observation"}
            and group.axis_slot.requested_direction in {-1, 1}
        )
        else group.direction_multiplier
        if group.axis_slot.axis_role == "axis"
        else None
    )
    expected_direction = (
        state_direction
        if is_direct_observation
        else -state_direction
        if state_direction in {-1, 1}
        else None
    )
    if (
        state_direction not in {-1, 1}
        or operation.direction != expected_direction
    ):
        return None
    guard = negations[0]
    matching_support = tuple(
        evidence
        for evidence in operation.evidence
        if (
            evidence.slot == "semantic_support"
            and evidence.concept_id == str(guard.concept_id)
            and evidence.start == guard.evidence.start
            and evidence.end == guard.evidence.end
            and evidence.raw_text == guard.evidence.raw_text
        )
    )
    return (guard, group) if len(matching_support) == 1 else None


def _axis_attribute_region_support_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, Any] | None:
    """Independently re-prove a bare visual-part noun as axis support."""

    if (
        evidence.slot != "axis_attribute_region_support"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.region != "all"
        or operation.operation_type != "relative"
    ):
        return None
    source_regions = tuple(
        slot
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.namespace == "region"
            and str(slot.value) == evidence.concept_id
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
        )
    )
    definition = registry.regions.get(evidence.concept_id)
    matching_groups = tuple(
        group
        for group in resolution.operation_groups
        if (
            group.axis_id == operation.axis_id
            and group.resolved_direction == operation.direction
            and any(
                slot.evidence.start == evidence.start
                and slot.evidence.end == evidence.end
                and slot.evidence.raw_text == evidence.raw_text
                and slot.namespace == "region"
                and str(slot.value) == evidence.concept_id
                for slot in group.supporting_slots
            )
        )
    )
    if (
        len(source_regions) != 1
        or definition is None
        or operation.axis_id not in definition.attribute_axis_ids
        or len(matching_groups) != 1
    ):
        return None
    source = source_regions[0]
    group = matching_groups[0]
    coordination_group = group.clause.coordination_group
    local_slots = tuple(
        slot
        for clause in resolution.clauses
        if clause.coordination_group == coordination_group
        for slot in clause.slots
    )
    if any(
        slot is not source
        and slot.slot in {"scope", "region_context", "region_support"}
        for slot in local_slots
    ):
        return None
    prior_actions = tuple(
        slot
        for slot in (*group.supporting_slots, group.axis_slot)
        if (
            slot.namespace == "axis"
            and slot.match_kind == "action"
            and str(slot.value) == operation.axis_id
            and slot.normalized_end <= source.normalized_start
        )
    )
    after_aspects = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "clause_aspect"
            and slot.value == "after"
            and any(
                prior.normalized_end <= slot.normalized_start
                for prior in prior_actions
            )
            and slot.normalized_end <= source.normalized_start
        )
    )
    continuations = tuple(
        slot
        for slot in group.relation_slots
        if (
            slot.value == "continue"
            and source.normalized_end <= slot.normalized_start
            and slot.normalized_end <= group.axis_slot.normalized_start
        )
    )
    continuation_structure = bool(
        group.axis_slot.match_kind in {"action", "descriptor"}
        and group.axis_slot.requested_direction in {-1, 1}
        and group.axis_slot.normalized_start >= source.normalized_end
        and len(prior_actions) == 1
        and len(after_aspects) == 1
        and len(continuations) == 1
    )
    completed_observation_structure = bool(
        operation.operation_kind == "observation"
        and group.axis_slot.match_kind == "action"
        and group.axis_slot.requested_direction in {-1, 1}
        and group.axis_slot.normalized_end <= source.normalized_start
        and len(prior_actions) == 1
        and prior_actions[0] is group.axis_slot
        and len(after_aspects) == 1
        and not continuations
        and any(
            source.normalized_end <= slot.normalized_start
            for slot in group.observation_modifier_slots
        )
    )
    direct_axis_heads = tuple(
        slot
        for slot in local_slots
        if (
            slot.namespace == "axis"
            and str(slot.value) == operation.axis_id
            and slot.match_kind in {"action", "axis"}
            and slot.normalized_end == source.normalized_start
        )
    )
    direct_attribute_observation_structure = bool(
        operation.operation_kind in {"explicit_axis", "observation"}
        and len(direct_axis_heads) == 1
        and (
            direct_axis_heads[0] is group.axis_slot
            or direct_axis_heads[0] in group.supporting_slots
        )
        and any(
            slot.slot == "observation_modifier"
            and source.normalized_end <= slot.normalized_start
            for slot in local_slots
        )
        and not any(
            slot.slot
            in {
                "conjunction",
                "direction",
                "guard",
                "negation",
                "numeric",
                "numeric_relation",
                "operation",
                "relation",
                "strength",
            }
            for slot in local_slots
        )
    )
    if not (
        continuation_structure
        or completed_observation_structure
        or direct_attribute_observation_structure
    ):
        return None
    matching_evidence = tuple(
        item
        for item in operation.evidence
        if (
            item.slot == "axis_attribute_region_support"
            and item.concept_id == evidence.concept_id
            and item.start == source.evidence.start
            and item.end == source.evidence.end
            and item.raw_text == source.evidence.raw_text
        )
    )
    return (source, group) if len(matching_evidence) == 1 else None


def _prior_event_support_binding(
    operation: SemanticOperation,
    evidence: RawSpanEvidence,
    *,
    extraction: SlotExtraction,
    resolution: Any,
) -> tuple[SemanticSlot, SemanticSlot, Any] | None:
    """Re-prove one completed same-axis action used only as context."""

    if (
        evidence.slot != "prior_event_support"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or len(resolution.operation_groups) != 1
    ):
        return None
    group = resolution.operation_groups[0]
    selected = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.namespace == "axis"
            and slot.match_kind == "action"
            and str(slot.value) == operation.axis_id
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and str(slot.concept_id) == evidence.concept_id
        )
    )
    sources = tuple(
        slot
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.namespace == "axis"
            and slot.match_kind == "action"
            and str(slot.value) == operation.axis_id
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.evidence.raw_text == evidence.raw_text
            and str(slot.concept_id) == evidence.concept_id
        )
    )
    after_aspects = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "clause_aspect"
            and slot.value == "after"
        )
    )
    continuations = tuple(
        slot for slot in group.relation_slots if slot.value == "continue"
    )
    if (
        group.axis_id != operation.axis_id
        or group.axis_slot.match_kind not in {"action", "descriptor"}
        or group.resolved_direction != operation.direction
        or len(selected) != 1
        or len(sources) != 1
        or len(after_aspects) != 1
        or len(continuations) != 1
        or not group.strength_slots
        or group.observation_modifier_slots
        or group.state_link_slots
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.guard_slots
        or not (
            selected[0].normalized_end
            <= after_aspects[0].normalized_start
            and after_aspects[0].normalized_end
            <= group.axis_slot.normalized_start
        )
    ):
        return None
    return sources[0], selected[0], group


def _post_event_still_guard_binding(
    operation: SemanticOperation,
    *,
    resolution: Any,
) -> tuple[SemanticSlot, Any] | None:
    """Independently prove one completed-event persistence observation."""

    if (
        resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or len(resolution.operation_groups) != 1
    ):
        return None
    group = resolution.operation_groups[0]
    if (
        group.axis_id != operation.axis_id
        or group.axis_slot.match_kind != "action"
        or group.axis_slot.requested_direction not in {-1, 1}
        or int(group.axis_slot.requested_direction) != operation.direction
    ):
        return None
    candidates = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "guard"
            and slot.concept_id == "disjunction_or_still"
            and slot.value == "or"
            and slot not in group.guard_slots
        )
    )
    if len(candidates) != 1:
        return None
    guard = candidates[0]
    adjacent = tuple(
        (left, right)
        for left, right in zip(
            resolution.clauses,
            resolution.clauses[1:],
        )
        if (
            guard in left.connector_after
            and guard in right.connector_before
            and left.boundary_after == "disjunction"
            and right.boundary_before == "disjunction"
            and right.boundary_after != "disjunction"
        )
    )
    if len(adjacent) != 1:
        return None
    left, right = adjacent[0]
    after_aspects = tuple(
        slot
        for slot in left.slots
        if (
            slot.slot == "clause_aspect"
            and slot.value == "after"
            and group.axis_slot.normalized_end <= slot.normalized_start
        )
    )
    observed_axes = tuple(
        slot
        for slot in right.slots
        if (
            not slot.is_ambiguous
            and slot.namespace == "axis"
            and str(slot.value) == operation.axis_id
            and slot.match_kind in {"descriptor", "observation"}
            and slot.requested_direction in {-1, 1}
        )
    )
    modifiers = {
        str(slot.value)
        for slot in right.slots
        if (
            not slot.is_ambiguous
            and slot.slot == "observation_modifier"
            and str(slot.value)
            in {"too", "too_much", "not_enough", "mild"}
        )
    }
    if (
        len(after_aspects) != 1
        or len(observed_axes) != 1
        or len(modifiers) > 1
        or any(
            slot.slot
            in {
                "operation",
                "numeric",
                "numeric_relation",
                "relation",
                "surface_action",
            }
            for slot in right.slots
        )
    ):
        return None
    observed = observed_axes[0]
    correction = int(observed.requested_direction)
    if observed.match_kind == "descriptor":
        modifier = next(iter(modifiers), None)
        if modifier is None:
            return None
        correction = (
            correction
            if modifier == "not_enough"
            else -correction
        )
    if correction != operation.direction:
        return None
    matching_evidence = tuple(
        evidence
        for evidence in operation.evidence
        if (
            evidence.slot == "semantic_support"
            and evidence.concept_id == "disjunction_or_still"
            and evidence.start == guard.evidence.start
            and evidence.end == guard.evidence.end
            and evidence.raw_text == guard.evidence.raw_text
        )
    )
    return (guard, group) if len(matching_evidence) == 1 else None


def _persistent_still_observation_guard_binding(
    operation: SemanticOperation,
    *,
    resolution: Any,
) -> tuple[SemanticSlot, Any] | None:
    """Independently prove one dual-role marker as observation persistence."""

    if (
        resolution is None
        or resolution.errors
        or resolution.guards
        or resolution.ambiguous_slots
        or operation.operation_type != "relative"
        or len(resolution.operation_groups) != 1
    ):
        return None
    group = resolution.operation_groups[0]
    candidates = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "guard"
            and slot.concept_id == "disjunction_or_still"
            and slot.value == "or"
            and slot not in group.guard_slots
        )
    )
    if len(candidates) != 1:
        return None
    guard = candidates[0]
    disjunctions = tuple(
        slot
        for slot in resolution.extraction.slots
        if slot.slot == "guard" and slot.value == "or"
    )
    before_axes = tuple(
        slot
        for slot in resolution.extraction.slots
        if (
            slot.namespace == "axis"
            and slot.normalized_end <= guard.normalized_start
        )
    )
    after_axes = tuple(
        slot
        for slot in resolution.extraction.slots
        if (
            slot.namespace == "axis"
            and guard.normalized_end <= slot.normalized_start
        )
    )
    if (
        len(disjunctions) != 1
        or disjunctions[0] is not guard
        or len(before_axes) > 1
        or len(after_axes) != 1
        or after_axes[0] is not group.axis_slot
        or group.axis_id != operation.axis_id
        or group.axis_slot.match_kind not in {"descriptor", "observation"}
        or group.axis_slot.requested_direction not in {-1, 1}
        or group.direction_slots
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.guard_slots
        or group.ambiguous_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or any(
            guard in clause.connector_before
            or guard in clause.connector_after
            for clause in resolution.clauses
        )
    ):
        return None
    if before_axes:
        subject = before_axes[0]
        if (
            subject.axis_role != "axis"
            or subject.match_kind != "axis"
            or str(subject.value) != group.axis_id
            or subject.requested_direction is not None
        ):
            return None
    modifiers = {
        str(slot.value) for slot in group.observation_modifier_slots
    }
    if len(modifiers) > 1:
        return None
    modifier = next(iter(modifiers), None)
    if group.axis_slot.match_kind == "observation":
        correction = int(group.axis_slot.requested_direction)
    elif modifier in {"too", "too_much", "mild", "not_enough"}:
        source = int(group.axis_slot.requested_direction)
        correction = source if modifier == "not_enough" else -source
    else:
        return None
    if correction != operation.direction:
        return None
    forbidden = {
        "direction",
        "numeric",
        "operation",
        "numeric_relation",
        "relation",
        "surface_action",
        "terminal",
        "negation",
        "conjunction",
        "guard",
    }
    if any(
        slot is not guard
        and (
            slot.is_ambiguous
            or slot.slot in forbidden
        )
        for slot in resolution.extraction.slots
    ):
        return None
    matching_evidence = tuple(
        evidence
        for evidence in operation.evidence
        if (
            evidence.slot == "semantic_support"
            and evidence.concept_id == "disjunction_or_still"
            and evidence.start == guard.evidence.start
            and evidence.end == guard.evidence.end
            and evidence.raw_text == guard.evidence.raw_text
        )
    )
    return (guard, group) if len(matching_evidence) == 1 else None


def _trusted_consumed_guard_support_ids(
    ir: SemanticIR,
    *,
    resolution: Any,
) -> frozenset[int]:
    """Return only consumed guards re-proved as deterministic support.

    Guards remain atomic blockers by default.  The scope resolver may consume
    preservation commands or the negation in a typed sufficiency relation.
    Validation independently requires the exact source slot, resolved group,
    operation, and ``semantic_support`` evidence to agree before exempting it
    from atomic guard rejection.  LLM candidates cannot assign this trusted
    role because only a fresh deterministic registry resolution is accepted.
    """

    if (
        ir.decision_source != "semantic_registry"
        or resolution is None
        or resolution.errors
        or resolution.guards
        or len(ir.operations) != 1
        or len(resolution.operation_groups) != 1
    ):
        return frozenset()
    operation = ir.operations[0]
    group = resolution.operation_groups[0]
    if group.axis_id != operation.axis_id:
        return frozenset()

    persistent_still = _persistent_still_observation_guard_binding(
        operation,
        resolution=resolution,
    )
    if persistent_still is not None:
        return frozenset({id(persistent_still[0])})

    post_event_still = _post_event_still_guard_binding(
        operation,
        resolution=resolution,
    )
    if post_event_still is not None:
        return frozenset({id(post_event_still[0])})

    negated_direction = next(
        (
            evidence
            for evidence in operation.evidence
            if (
                evidence.slot == "resolved_direction"
                and evidence.concept_id
                == "negated_removal_amount_direction"
            )
        ),
        None,
    )
    if negated_direction is not None:
        binding = _resolved_negated_removal_amount_binding(
            operation,
            negated_direction,
            extraction=resolution.extraction,
            resolution=resolution,
        )
        if binding is not None:
            guard = binding[0]
            matching_support = tuple(
                evidence
                for evidence in operation.evidence
                if (
                    evidence.slot == "semantic_support"
                    and evidence.concept_id == "negation"
                    and evidence.start == guard.evidence.start
                    and evidence.end == guard.evidence.end
                    and evidence.raw_text == guard.evidence.raw_text
                )
            )
            if len(matching_support) == 1:
                return frozenset({id(guard)})

    upper_bound = _upper_bound_group_binding(
        operation,
        resolution=resolution,
    )
    if upper_bound is not None:
        return frozenset({id(upper_bound[0])})
    contrastive_upper_bound = _contrastive_upper_bound_command_guard_binding(
        operation,
        resolution=resolution,
    )
    if contrastive_upper_bound is not None:
        return frozenset({id(contrastive_upper_bound)})

    has_sufficiency_relation = any(
        slot.concept_id == "sufficiency_enough"
        for slot in (
            *group.observation_modifier_slots,
            *group.supporting_slots,
        )
    )
    candidates = tuple(
        slot
        for slot in group.supporting_slots
        if (
            (
                slot.slot == "guard"
                and slot.concept_id == "preservation"
                and slot.value == "preserve"
            )
            or (
                has_sufficiency_relation
                and slot.slot == "negation"
                and slot.concept_id == "negation"
                and slot.value is True
            )
        )
        and slot not in group.guard_slots
    )
    if len(candidates) != 1:
        return frozenset()
    slot = candidates[0]
    matching_evidence = tuple(
        evidence
        for evidence in operation.evidence
        if (
            evidence.slot == "semantic_support"
            and evidence.concept_id == str(slot.concept_id)
            and evidence.start == slot.evidence.start
            and evidence.end == slot.evidence.end
            and evidence.raw_text == slot.evidence.raw_text
        )
    )
    return frozenset({id(slot)}) if len(matching_evidence) == 1 else frozenset()


def _contrastive_upper_bound_command_guard_binding(
    operation: SemanticOperation,
    *,
    resolution: Any,
) -> SemanticSlot | None:
    """Re-prove the consumed negation in one bounded command."""

    if (
        resolution is None
        or resolution.errors
        or resolution.guards
        or operation.operation_type != "relative"
        or len(resolution.operation_groups) != 1
    ):
        return None
    group = resolution.operation_groups[0]
    guards = tuple(
        slot
        for slot in group.supporting_slots
        if slot.slot == "negation" and slot.value is True
    )
    observed_axes = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.namespace == "axis"
            and str(slot.value) == operation.axis_id
            and slot.match_kind in {"action", "descriptor"}
            and slot.requested_direction == operation.direction
        )
    )
    modifiers = tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "observation_modifier"
            and str(slot.value) in {"too", "too_much"}
        )
    )
    if (
        len(guards) != 1
        or len(observed_axes) != 1
        or not modifiers
        or group.axis_id != operation.axis_id
        or group.resolved_direction != operation.direction
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
    ):
        return None
    guard = guards[0]
    observed_axis = observed_axes[0]
    if not all(
        guard.normalized_end <= modifier.normalized_start
        and modifier.normalized_end <= observed_axis.normalized_start
        for modifier in modifiers
    ):
        return None
    bound_clause = next(
        (
            clause
            for clause in resolution.clauses
            if (
                clause.normalized_start <= guard.normalized_start
                and observed_axis.normalized_end <= clause.normalized_end
            )
        ),
        None,
    )
    if (
        bound_clause is None
        or bound_clause.boundary_before != "contrastive"
        or abs(bound_clause.index - group.clause_index) != 1
    ):
        return None
    evidence = tuple(
        item
        for item in operation.evidence
        if (
            item.slot == "semantic_support"
            and item.concept_id == str(guard.concept_id)
            and item.start == guard.evidence.start
            and item.end == guard.evidence.end
            and item.raw_text == guard.evidence.raw_text
        )
    )
    return guard if len(evidence) == 1 else None


def _require_grounded_slot(
    extraction: SlotExtraction,
    evidence: RawSpanEvidence,
    *,
    namespace: str | None = None,
    slot_name: str | None = None,
    concept_id: str | None = None,
) -> SemanticSlot:
    matches = [
        slot
        for slot in extraction.slots
        if slot.evidence.start == evidence.start
        and slot.evidence.end == evidence.end
        and not slot.is_ambiguous
        and (namespace is None or slot.namespace == namespace)
        and (slot_name is None or slot.slot == slot_name)
        and (concept_id is None or slot.concept_id == concept_id)
    ]
    if len(matches) != 1:
        _raise(
            "adaptive_clarification_required",
            "Semantic evidence does not match a registered source concept.",
            source_clause=evidence.raw_text,
            start=evidence.start,
            end=evidence.end,
            required_namespace=namespace,
            required_slot=slot_name,
            required_concept=concept_id,
            reason="unverifiable_semantic_evidence",
        )
    matched = matches[0]
    if evidence.concept_id != matched.concept_id:
        _raise(
            "adaptive_clarification_required",
            "Semantic evidence names a concept different from its source span.",
            source_clause=evidence.raw_text,
            evidence_concept=evidence.concept_id,
            grounded_concept=matched.concept_id,
            reason="semantic_evidence_concept_mismatch",
        )
    return matched


def _validate_direction_grounding(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    extraction: SlotExtraction,
    axis_slot: SemanticSlot,
    definition: Any,
) -> None:
    direction_evidence = _require_evidence(operation, "direction")
    resolved_direction_slots: list[SemanticSlot] = []
    if direction_evidence.slot in {
        "resolved_direction",
        "resolved_observation",
    }:
        resolution = resolve_semantic_scope(
            extraction,
            registry=registry,
        )
        binding = (
            _resolved_direction_binding(
                operation,
                direction_evidence,
                extraction=extraction,
                resolution=resolution,
                registry=registry,
            )
            if direction_evidence.slot == "resolved_direction"
            else _resolved_observation_binding(
                operation,
                direction_evidence,
                extraction=extraction,
                resolution=resolution,
                registry=registry,
            )
        )
        if binding is None:
            _raise(
                "adaptive_clarification_required",
                "Resolved direction evidence cannot be re-proved.",
                axis=operation.axis_id,
                source_clause=direction_evidence.raw_text,
                reason="unverifiable_direction_evidence",
            )
        resolved_direction_slots.append(binding[1])
    evidence_slots = [
        slot
        for evidence in operation.evidence
        if (
            evidence.slot not in _RESOLVED_SUPPORT_EVIDENCE_SLOTS
            or (
                evidence.slot == "observation_attribute"
                and operation.operation_kind == "observation"
            )
            or (
                evidence.slot == "action_attribute"
                and operation.operation_kind == "explicit_axis"
            )
            or (
                evidence.slot == "surface_action"
                and operation.operation_kind == "explicit_axis"
            )
        )
        for slot in extraction.slots
        if slot.evidence.start == evidence.start
        and slot.evidence.end == evidence.end
        and not slot.is_ambiguous
    ]
    evidence_slots.extend(resolved_direction_slots)
    direction_slots = [
        slot for slot in evidence_slots if slot.slot == "direction"
    ]
    observation_slots = [
        slot
        for slot in evidence_slots
        if slot.slot in {"observation_modifier", "state_link"}
    ]
    observation_attribute_slots = [
        slot
        for evidence in operation.evidence
        if evidence.slot == "observation_attribute"
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.namespace == "axis"
            and slot.requested_direction in {-1, 1}
        )
    ]
    action_attribute_slots = [
        slot
        for evidence in operation.evidence
        if evidence.slot == "action_attribute"
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.namespace == "axis"
            and slot.match_kind == "action"
            and slot.requested_direction in {-1, 1}
        )
    ]
    surface_action_slots = [
        slot
        for evidence in operation.evidence
        if evidence.slot == "surface_action"
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.slot == "surface_action"
            and str(slot.value) in _SURFACE_ACTION_DIRECTIONS
        )
    ]
    return_negative_slots = [
        slot
        for evidence in operation.evidence
        if evidence.slot == "direction"
        for slot in extraction.slots
        if (
            not slot.is_ambiguous
            and slot.evidence.start == evidence.start
            and slot.evidence.end == evidence.end
            and slot.slot == "generic_action"
            and slot.value == "return_negative"
        )
    ]
    effect_reference = any(
        slot.slot == "effect_reference" for slot in evidence_slots
    )
    fused_direction_slots = [
        slot
        for slot in evidence_slots
        if slot.namespace == "axis"
        and slot.requested_direction is not None
    ]

    # The explicit evidence object must itself resolve to either a registered
    # direction concept, a fused axis+direction concept, or an observation
    # modifier that changes the requested direction.
    grounded_direction_slots = (
        resolved_direction_slots
        if direction_evidence.slot in {
            "resolved_direction",
            "resolved_observation",
        }
        else [
            slot
            for slot in extraction.slots
            if not slot.is_ambiguous
            and slot.evidence.start == direction_evidence.start
            and slot.evidence.end == direction_evidence.end
            and (
                slot.slot in {"direction", "observation_modifier"}
                or slot.slot == "surface_action"
                or (
                    slot.slot == "generic_action"
                    and slot.value == "return_negative"
                )
                or (
                    slot.namespace == "axis"
                    and slot.requested_direction is not None
                )
            )
        ]
    )
    if not grounded_direction_slots:
        _raise(
            "adaptive_clarification_required",
            "Direction evidence is not a registered direction concept.",
            axis=operation.axis_id,
            source_clause=direction_evidence.raw_text,
            reason="unverifiable_direction_evidence",
        )
    if direction_evidence.concept_id not in {
        str(slot.concept_id) for slot in grounded_direction_slots
    }:
        _raise(
            "adaptive_clarification_required",
            "Direction evidence names a concept different from its source span.",
            axis=operation.axis_id,
            source_clause=direction_evidence.raw_text,
            evidence_concept=direction_evidence.concept_id,
            grounded_concepts=sorted(
                {str(slot.concept_id) for slot in grounded_direction_slots}
            ),
            reason="semantic_evidence_concept_mismatch",
        )

    base_direction = axis_slot.requested_direction
    axis_direction_multiplier = (
        int(axis_slot.direction_multiplier)
        if axis_slot.axis_role == "axis"
        and axis_slot.direction_multiplier in {-1, 1}
        else 1
    )
    direct_directions = [
        int(slot.value)
        for slot in direction_slots
        if slot.concept_id
        not in {"comparative_more", "comparative_less"}
    ]
    comparative_directions = [
        int(slot.value)
        for slot in direction_slots
        if slot.concept_id in {"comparative_more", "comparative_less"}
    ]
    fused_directions = [
        int(slot.requested_direction)
        for slot in fused_direction_slots
        if (
            slot.evidence.start,
            slot.evidence.end,
        )
        != (
            axis_slot.evidence.start,
            axis_slot.evidence.end,
        )
    ]
    surface_directions = [
        _SURFACE_ACTION_DIRECTIONS[str(slot.value)]
        * axis_direction_multiplier
        for slot in surface_action_slots
    ]
    if len(set(direct_directions)) > 1 or len(set(comparative_directions)) > 1:
        _raise(
            "adaptive_operation_conflict",
            "Direction evidence contains contradictory modifiers.",
            axis=operation.axis_id,
            reason="conflicting_direction_evidence",
        )

    expected: int | None = None
    if operation.operation_kind == "explicit_axis":
        if axis_slot.axis_role != "axis":
            contract_support = next(
                (
                    evidence
                    for evidence in operation.evidence
                    if evidence.slot == "controller_contract_support"
                ),
                None,
            )
            contract_binding = (
                _controller_contract_support_binding(
                    operation,
                    contract_support,
                    extraction=extraction,
                    resolution=resolve_semantic_scope(
                        extraction,
                        registry=registry,
                    ),
                    registry=registry,
                )
                if contract_support is not None
                else None
            )
            controller_expected = (
                _validate_registry_explicit_controller_mode(
                    operation,
                    ir=ir,
                    registry=registry,
                    extraction=extraction,
                    axis_slot=axis_slot,
                )
                if contract_binding is None
                else None
            )
            if contract_binding is not None:
                expected = contract_binding[2]
            elif controller_expected is not None:
                expected = controller_expected
            elif axis_slot.match_kind == "action":
                expected = _validate_registry_action_with_direct_direction(
                    operation,
                    ir=ir,
                    registry=registry,
                    extraction=extraction,
                    axis_slot=axis_slot,
                )
            elif axis_slot.match_kind == "descriptor":
                expected = _validate_registry_descriptor_remedy(
                    operation,
                    ir=ir,
                    registry=registry,
                    extraction=extraction,
                    axis_slot=axis_slot,
                )
            else:
                _raise(
                    "adaptive_operation_conflict",
                    "Explicit-axis operation is not grounded by an axis noun.",
                    axis=operation.axis_id,
                    match_kind=axis_slot.match_kind,
                    reason="operation_kind_evidence_mismatch",
                )
        else:
            action_directions = [
                int(slot.requested_direction)
                for slot in action_attribute_slots
            ]
            canonical_direct_directions = [
                direction * axis_direction_multiplier
                for direction in direct_directions
            ]
            canonical_comparative_directions = [
                direction * axis_direction_multiplier
                for direction in comparative_directions
            ]
            return_directions = []
            if return_negative_slots:
                resolution = resolve_semantic_scope(
                    extraction,
                    registry=registry,
                )
                matching_groups = tuple(
                    group
                    for group in resolution.operation_groups
                    if group.axis_id == operation.axis_id
                )
                if (
                    len(return_negative_slots) == 1
                    and len(matching_groups) == 1
                    and matching_groups[0].resolved_direction == -1
                    and any(
                        slot.slot == "return_relation"
                        for slot in matching_groups[0].supporting_slots
                    )
                ):
                    return_directions.append(-1)
            explicit_sources = [
                *canonical_direct_directions,
                *canonical_comparative_directions,
                *action_directions,
                *surface_directions,
                *return_directions,
            ]
            if len(set(explicit_sources)) > 1:
                _raise(
                    "adaptive_operation_conflict",
                    "Bound action and direction evidence disagree.",
                    axis=operation.axis_id,
                    reason="conflicting_action_attribute_direction",
                )
            candidates = (
                canonical_direct_directions
                or canonical_comparative_directions
                or action_directions
                or surface_directions
                or return_directions
                or fused_directions
            )
            if len(set(candidates)) > 1:
                _raise(
                    "adaptive_operation_conflict",
                    "Direction evidence contains contradictory modifiers.",
                    axis=operation.axis_id,
                    reason="conflicting_direction_evidence",
                )
            expected = candidates[0] if candidates else None
    elif operation.operation_kind == "macro":
        contract_support = next(
            (
                evidence
                for evidence in operation.evidence
                if evidence.slot == "controller_contract_support"
            ),
            None,
        )
        contract_binding = (
            _controller_contract_support_binding(
                operation,
                contract_support,
                extraction=extraction,
                resolution=resolve_semantic_scope(
                    extraction,
                    registry=registry,
                ),
                registry=registry,
            )
            if contract_support is not None
            else None
        )
        controller_binding = _resolved_group_axis_controller_mode(
            operation,
            ir=ir,
            registry=registry,
            extraction=extraction,
            axis_slot=axis_slot,
        )
        if contract_binding is not None:
            expected = contract_binding[2]
        elif (
            axis_slot.axis_role == "axis"
            or axis_slot.match_kind not in {"action", "descriptor"}
            or base_direction is None
            or controller_binding is None
        ):
            _raise(
                "adaptive_operation_conflict",
                "Macro operation is not grounded by an action or descriptor.",
                axis=operation.axis_id,
                match_kind=axis_slot.match_kind,
                reason="operation_kind_evidence_mismatch",
            )
        else:
            expected = base_direction
            if comparative_directions:
                expected *= comparative_directions[0]
            if (
                axis_slot.match_kind == "action"
                and direct_directions
                and not _has_macro_direct_direction_contract(
                    direction_slots,
                    registry=registry,
                )
            ):
                _raise(
                    "adaptive_operation_conflict",
                    "Macro action uses an uncontracted direct direction.",
                    axis=operation.axis_id,
                    reason="operation_kind_evidence_mismatch",
                )
            if direct_directions and direct_directions[0] != expected:
                _raise(
                    "adaptive_operation_conflict",
                    "Action and direction evidence disagree.",
                    axis=operation.axis_id,
                    reason="conflicting_direction_evidence",
                )
    elif operation.operation_kind == "observation":
        attribute_directions: set[int] = set()
        if observation_attribute_slots:
            observation_resolution = resolve_semantic_scope(
                extraction,
                registry=registry,
            )
            for attribute_slot in observation_attribute_slots:
                matching_groups = tuple(
                    group
                    for group in observation_resolution.operation_groups
                    if (
                        group.axis_id == operation.axis_id
                        and attribute_slot in group.attribute_axis_slots
                        and group.observation_attribute_direction in {-1, 1}
                    )
                )
                if len(matching_groups) == 1:
                    attribute_directions.add(
                        int(
                            matching_groups[
                                0
                            ].observation_attribute_direction
                        )
                    )
        modifier_values = {
            str(slot.value) for slot in observation_slots
        }
        if len(attribute_directions) > 1:
            _raise(
                "adaptive_operation_conflict",
                "Observation attributes disagree on correction polarity.",
                axis=operation.axis_id,
                reason="conflicting_observation_attribute",
            )
        if attribute_directions:
            attribute_direction = next(iter(attribute_directions))
            expected = (
                attribute_direction
                if "not_enough" in modifier_values
                else -attribute_direction
            )
            if direct_directions and any(
                direction != expected for direction in direct_directions
            ):
                _raise(
                    "adaptive_operation_conflict",
                    "Remedy direction disagrees with the observed state.",
                    axis=operation.axis_id,
                    reason="observation_remedy_direction_mismatch",
                )
        elif axis_slot.match_kind == "observation":
            expected = base_direction
        elif (
            direct_directions
            and observation_slots
            and any(
                slot.slot == "state_link"
                for slot in observation_slots
            )
        ):
            expected = (
                direct_directions[0]
                if "not_enough" in modifier_values
                else -direct_directions[0]
            )
        elif (
            axis_slot.match_kind in {"action", "descriptor"}
            and base_direction is not None
        ):
            expected = (
                base_direction
                if "not_enough" in modifier_values
                else -base_direction
            )
        elif axis_slot.axis_role == "axis":
            if "not_enough" in modifier_values:
                expected = axis_direction_multiplier
            elif modifier_values & {
                "too",
                "too_much",
                "mild",
            }:
                source = (direct_directions or comparative_directions)
                expected = (
                    -(source[0] * axis_direction_multiplier)
                    if source
                    else -axis_direction_multiplier
                )
        if expected is None:
            _raise(
                "adaptive_clarification_required",
                "Observation does not ground a unique correction direction.",
                axis=operation.axis_id,
                reason="ambiguous_observation_direction",
            )
    elif operation.operation_kind == "group_feedback":
        if not effect_reference:
            _raise(
                "adaptive_clarification_required",
                "Group feedback lacks an explicit effect reference.",
                axis=operation.axis_id,
                reason="missing_effect_group_evidence",
            )
        reproved = _reprove_group_feedback_contract(
            operation,
            ir=ir,
            registry=registry,
            extraction=extraction,
            axis_slot=axis_slot,
            definition=definition,
        )
        if reproved is None:
            _raise(
                "adaptive_clarification_required",
                "Group feedback is not grounded by one overdone macro effect.",
                axis=operation.axis_id,
                reason="unverifiable_group_feedback_contract",
            )
        expected, target_group_intent = reproved
        if operation.target_group_intent != target_group_intent:
            _raise(
                "adaptive_operation_conflict",
                "Group feedback target disagrees with the axis policy.",
                axis=operation.axis_id,
                target_group_intent=operation.target_group_intent,
                grounded_target_group_intent=target_group_intent,
                reason="group_feedback_target_mismatch",
            )
    else:
        _raise(
            "adaptive_clarification_required",
            "Unsupported relative semantic operation kind.",
            axis=operation.axis_id,
            operation_kind=operation.operation_kind,
            reason="unsupported_relative_operation_kind",
        )

    if expected != operation.direction:
        _raise(
            "adaptive_operation_conflict",
            "Requested direction disagrees with grounded source evidence.",
            axis=operation.axis_id,
            requested_direction=operation.direction,
            grounded_direction=expected,
            reason="direction_evidence_mismatch",
        )


def _has_macro_direct_direction_contract(
    direction_slots: list[SemanticSlot],
    *,
    registry: ParameterRegistry,
) -> bool:
    direct_slots = tuple(
        slot
        for slot in direction_slots
        if slot.concept_id not in {"comparative_more", "comparative_less"}
    )
    if len(direct_slots) != 1:
        return False
    slot = direct_slots[0]
    definition = registry.shared_concepts.get(str(slot.concept_id))
    if definition is None:
        return False
    contract = registry.resolve_shared_alias_contract(
        definition.slot,
        slot.evidence.raw_text,
        slot.language,
    )
    return bool(contract is not None and contract.mode == "macro")


def _resolved_group_axis_controller_mode(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    extraction: SlotExtraction,
    axis_slot: SemanticSlot,
) -> tuple[str, Any] | None:
    """Independently re-prove assembler controller mode from typed aliases."""

    primary = registry.resolve_axis_alias(
        axis_slot.evidence.raw_text,
        axis_slot.language,
    )
    if primary is None or primary.axis_id != operation.axis_id:
        return None
    resolution = resolve_semantic_scope(
        extraction,
        registry=registry,
    )
    if resolution.errors:
        return None
    groups = tuple(
        group
        for group in resolution.operation_groups
        if (
            group.axis_id == operation.axis_id
            and group.axis_slot.evidence.start == axis_slot.evidence.start
            and group.axis_slot.evidence.end == axis_slot.evidence.end
            and group.axis_slot.concept_id == axis_slot.concept_id
        )
    )
    if len(groups) != 1:
        return None
    group = groups[0]
    support_modes = {
        binding.controller_mode
        for slot in group.supporting_slots
        if (
            slot.namespace == "axis"
            and str(slot.value) == operation.axis_id
            and slot.axis_role == "axis"
        )
        if (
            binding := registry.resolve_axis_alias(
                slot.evidence.raw_text,
                slot.language,
            )
        )
        is not None
        and binding.axis_id == operation.axis_id
    }
    if len(support_modes) > 1:
        return None
    return (
        next(iter(support_modes))
        if support_modes
        else primary.controller_mode,
        group,
    )


def _reprove_group_feedback_contract(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    extraction: SlotExtraction,
    axis_slot: SemanticSlot,
    definition: Any,
) -> tuple[int, str] | None:
    """Rebuild group-feedback eligibility from scope and registry metadata."""

    resolved = _resolved_group_axis_controller_mode(
        operation,
        ir=ir,
        registry=registry,
        extraction=extraction,
        axis_slot=axis_slot,
    )
    if resolved is None:
        return None
    group = resolved[1]
    if not any(
        alias.controller_mode == "macro"
        for alias in definition.aliases
    ):
        return None

    effect_references = tuple(
        slot
        for slot in group.supporting_slots
        if slot.slot == "effect_reference"
    )
    observation_values = {
        str(slot.value) for slot in group.observation_modifier_slots
    }
    if (
        len(effect_references) != 1
        or not observation_values
        or not observation_values.issubset({"too", "too_much"})
    ):
        return None
    effect_evidence_spans = {
        (evidence.start, evidence.end)
        for evidence in operation.evidence
        if (
            evidence.slot == "effect_reference"
            and evidence.concept_id == "effect_reference"
        )
    }
    effect_reference = effect_references[0]
    if (
        effect_reference.evidence.start,
        effect_reference.evidence.end,
    ) not in effect_evidence_spans:
        return None

    direction = group.resolved_direction
    if direction not in {-1, 1}:
        return None
    target_group_intent = (
        definition.policy.positive_intent
        if direction == -1
        else definition.policy.negative_intent
    )
    return direction, target_group_intent


def _validate_registry_explicit_controller_mode(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    extraction: SlotExtraction,
    axis_slot: SemanticSlot,
) -> int | None:
    """Re-prove an explicit action/descriptor registry contract."""

    resolved = _resolved_group_axis_controller_mode(
        operation,
        ir=ir,
        registry=registry,
        extraction=extraction,
        axis_slot=axis_slot,
    )
    if resolved is None or resolved[0] != "explicit_axis":
        return None
    group = resolved[1]
    if (
        axis_slot.match_kind not in {"action", "descriptor"}
        or axis_slot.requested_direction not in {-1, 1}
    ):
        return None
    direct = {
        int(slot.value)
        for slot in group.direction_slots
        if (
            slot.value in {-1, 1}
            and slot.concept_id
            not in {"comparative_more", "comparative_less"}
        )
    }
    comparative = {
        int(slot.value)
        for slot in group.direction_slots
        if (
            slot.value in {-1, 1}
            and slot.concept_id
            in {"comparative_more", "comparative_less"}
        )
    }
    if len(direct) > 1 or len(comparative) > 1:
        return None
    expected = int(axis_slot.requested_direction)
    if comparative:
        expected *= next(iter(comparative))
    if direct:
        direct_direction = next(iter(direct))
        if axis_slot.match_kind == "descriptor" and direct_direction != expected:
            return None
        expected = direct_direction
    if (
        group.resolved_direction is not None
        and group.resolved_direction != expected
    ):
        return None
    return expected


def _validate_registry_action_with_direct_direction(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    extraction: SlotExtraction,
    axis_slot: SemanticSlot,
) -> int:
    """Re-prove the only action alias that may become an explicit operation.

    A registered action such as ``sharpen`` already carries a polarity.  A
    direct modifier such as ``reduce`` may invert that polarity, but this
    composition is trusted only when the deterministic scope resolver can
    reconstruct the exact source group.  Grounded-LLM candidates cannot
    manufacture this operation-kind promotion.
    """

    if (
        ir.decision_source != "semantic_registry"
        or axis_slot.match_kind != "action"
        or axis_slot.requested_direction not in {-1, 1}
    ):
        _raise(
            "adaptive_operation_conflict",
            "Explicit-axis operation is not grounded by a trusted axis form.",
            axis=operation.axis_id,
            match_kind=axis_slot.match_kind,
            decision_source=ir.decision_source,
            reason="operation_kind_evidence_mismatch",
        )

    resolution = resolve_semantic_scope(
        extraction,
        registry=registry,
    )
    if resolution.errors:
        _raise(
            "adaptive_operation_conflict",
            "Action-direction composition does not have a safe scope.",
            axis=operation.axis_id,
            reasons=sorted({error.code for error in resolution.errors}),
            reason="operation_kind_evidence_mismatch",
        )
    matching_groups = tuple(
        group
        for group in resolution.operation_groups
        if (
            group.axis_id == operation.axis_id
            and group.axis_slot.evidence.start == axis_slot.evidence.start
            and group.axis_slot.evidence.end == axis_slot.evidence.end
            and group.axis_slot.concept_id == axis_slot.concept_id
        )
    )
    if len(matching_groups) != 1:
        _raise(
            "adaptive_operation_conflict",
            "Action-direction composition is not uniquely scoped.",
            axis=operation.axis_id,
            group_count=len(matching_groups),
            reason="operation_kind_evidence_mismatch",
        )
    group = matching_groups[0]
    direct_slots = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.concept_id
            not in {"comparative_more", "comparative_less"}
            and slot.value in {-1, 1}
        )
    )
    comparative_slots = tuple(
        slot
        for slot in group.direction_slots
        if slot.concept_id in {"comparative_more", "comparative_less"}
    )
    if len(direct_slots) != 1 or comparative_slots:
        _raise(
            "adaptive_operation_conflict",
            "Action-direction composition needs one direct modifier.",
            axis=operation.axis_id,
            direct_count=len(direct_slots),
            comparative_count=len(comparative_slots),
            reason="operation_kind_evidence_mismatch",
        )

    direct_slot = direct_slots[0]
    direction_evidence = tuple(
        evidence
        for evidence in operation.evidence
        if evidence.slot in {"direction", "axis_direction"}
    )
    if not any(
        evidence.start == direct_slot.evidence.start
        and evidence.end == direct_slot.evidence.end
        and evidence.concept_id == str(direct_slot.concept_id)
        for evidence in direction_evidence
    ):
        _raise(
            "adaptive_clarification_required",
            "Action-direction modifier is not present in operation evidence.",
            axis=operation.axis_id,
            reason="unverifiable_direction_evidence",
        )

    expected = int(direct_slot.value)
    if group.resolved_direction != expected:
        _raise(
            "adaptive_operation_conflict",
            "Action-direction composition disagrees with resolved scope.",
            axis=operation.axis_id,
            resolved_direction=group.resolved_direction,
            expected_direction=expected,
            reason="operation_kind_evidence_mismatch",
        )
    return expected


def _validate_registry_descriptor_remedy(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    extraction: SlotExtraction,
    axis_slot: SemanticSlot,
) -> int:
    """Re-prove a later explicit remedy for one observed descriptor.

    ``contrast is too strong; reduce it`` is not an axis-noun command, but it
    is safe when the deterministic resolver binds one observed descriptor to
    one corrective direction in a later clause.  Same-clause combinations and
    LLM-promoted candidates remain outside this trust path.
    """

    if (
        ir.decision_source != "semantic_registry"
        or axis_slot.match_kind != "descriptor"
        or axis_slot.requested_direction not in {-1, 1}
    ):
        _raise(
            "adaptive_operation_conflict",
            "Descriptor remedy is not grounded by trusted source semantics.",
            axis=operation.axis_id,
            match_kind=axis_slot.match_kind,
            decision_source=ir.decision_source,
            reason="operation_kind_evidence_mismatch",
        )

    resolution = resolve_semantic_scope(
        extraction,
        registry=registry,
    )
    if resolution.errors:
        _raise(
            "adaptive_operation_conflict",
            "Descriptor remedy does not have a safe scope.",
            axis=operation.axis_id,
            reasons=sorted({error.code for error in resolution.errors}),
            reason="operation_kind_evidence_mismatch",
        )
    matching_groups = tuple(
        group
        for group in resolution.operation_groups
        if (
            group.axis_id == operation.axis_id
            and group.axis_slot.evidence.start == axis_slot.evidence.start
            and group.axis_slot.evidence.end == axis_slot.evidence.end
            and group.axis_slot.concept_id == axis_slot.concept_id
        )
    )
    if len(matching_groups) != 1:
        _raise(
            "adaptive_operation_conflict",
            "Descriptor remedy is not uniquely scoped.",
            axis=operation.axis_id,
            group_count=len(matching_groups),
            reason="operation_kind_evidence_mismatch",
        )
    group = matching_groups[0]
    observation_slots = tuple(
        slot
        for slot in group.observation_modifier_slots
        if slot.value in {"too", "too_much", "not_enough", "mild"}
    )
    direct_slots = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.concept_id
            not in {"comparative_more", "comparative_less"}
            and slot.value in {-1, 1}
        )
    )
    comparative_slots = tuple(
        slot
        for slot in group.direction_slots
        if slot.concept_id in {"comparative_more", "comparative_less"}
    )
    clause_for_slot = {
        id(slot): clause.index
        for clause in resolution.clauses
        for slot in clause.slots
    }
    if (
        len(observation_slots) != 1
        or len(direct_slots) != 1
        or comparative_slots
        or clause_for_slot.get(id(observation_slots[0]))
        != group.clause_index
        or clause_for_slot.get(id(direct_slots[0]), group.clause_index)
        <= group.clause_index
    ):
        _raise(
            "adaptive_operation_conflict",
            "Descriptor remedy needs one later explicit correction.",
            axis=operation.axis_id,
            observation_count=len(observation_slots),
            direct_count=len(direct_slots),
            comparative_count=len(comparative_slots),
            reason="operation_kind_evidence_mismatch",
        )

    observation = observation_slots[0]
    direct = direct_slots[0]
    base_direction = int(axis_slot.requested_direction)
    corrective_direction = (
        base_direction
        if observation.value == "not_enough"
        else -base_direction
    )
    expected = int(direct.value)
    if expected != corrective_direction or group.resolved_direction != expected:
        _raise(
            "adaptive_operation_conflict",
            "Later remedy does not correct the observed descriptor.",
            axis=operation.axis_id,
            observed_direction=base_direction,
            remedy_direction=expected,
            resolved_direction=group.resolved_direction,
            reason="operation_kind_evidence_mismatch",
        )

    direction_evidence = tuple(
        evidence
        for evidence in operation.evidence
        if evidence.slot in {"direction", "axis_direction"}
    )
    if not any(
        evidence.start == direct.evidence.start
        and evidence.end == direct.evidence.end
        and evidence.concept_id == str(direct.concept_id)
        for evidence in direction_evidence
    ):
        _raise(
            "adaptive_clarification_required",
            "Descriptor remedy is not present in operation evidence.",
            axis=operation.axis_id,
            reason="unverifiable_direction_evidence",
        )
    return expected


def _raise(code: str, message: str, **issue: Any) -> None:
    raise SemanticValidationError(
        code=code,
        message=message,
        issues=(dict(issue),),
    )


__all__ = [
    "MAX_SEMANTIC_OPERATIONS",
    "MIN_GROUNDED_LLM_CONFIDENCE",
    "SemanticValidationError",
    "validate_semantic_ir",
]
