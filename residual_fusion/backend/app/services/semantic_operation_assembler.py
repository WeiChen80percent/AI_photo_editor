"""Registry-driven assembly from semantic scope into validated Semantic IR.

The assembler is deliberately deterministic and axis-neutral.  It consumes
only the relationships established by ``SemanticScopeResolution`` and metadata
carried by semantic slots.  It does not parse prompt strings, consult session
state, or contain branches for individual edit parameters.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.services.semantic_ir import RawSpanEvidence, SemanticIR, SemanticOperation
from app.services.semantic_registry import (
    DEFAULT_PARAMETER_REGISTRY,
    ParameterRegistry,
)
from app.services.semantic_scope_resolver import (
    OperationSlotGroup,
    ScopeError,
    SemanticScopeResolution,
)
from app.services.semantic_slot_extractor import SemanticSlot
from app.services.semantic_validator import (
    MAX_SEMANTIC_OPERATIONS,
    SemanticValidationError,
    validate_semantic_ir,
)


SEMANTIC_ASSEMBLER_VERSION = "semantic_operation_assembler_v1"

_COMPARATIVE_CONCEPTS = frozenset(
    {"comparative_more", "comparative_less"}
)
_STRUCTURAL_SLOTS = frozenset(
    {
        "conjunction",
        "noise",
        "scope",
        "region_context",
        "function_word",
    }
)
_UNSAFE_CONTEXT_SLOTS = frozenset({"effect_reference", "terminal"})
_OBSERVATION_VALUES = frozenset(
    {"too", "too_much", "not_enough", "mild"}
)
_STRENGTHS = frozenset({"subtle", "normal", "strong"})


@dataclass(frozen=True, slots=True)
class SemanticAssemblyError(ValueError):
    """Stable, structured fail-closed result from operation assembly."""

    code: str
    message: str
    issues: tuple[dict[str, Any], ...] = ()
    status_code: int = 422

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "issues": [dict(issue) for issue in self.issues],
            "status_code": self.status_code,
        }


SemanticOperationAssemblyError = SemanticAssemblyError
SemanticAssemblyResult = SemanticIR | SemanticAssemblyError


@dataclass(frozen=True, slots=True)
class _AssemblyFailure(Exception):
    error: SemanticAssemblyError


@dataclass(frozen=True, slots=True)
class _DirectionParts:
    direct: int | None
    comparative: int | None


class SemanticOperationAssembler:
    """Assemble and validate one immutable scope resolution."""

    def __init__(
        self,
        registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
        *,
        engine: str = "opencv",
        max_operations: int = MAX_SEMANTIC_OPERATIONS,
    ) -> None:
        if not isinstance(registry, ParameterRegistry):
            raise TypeError("registry must be a ParameterRegistry")
        if not isinstance(max_operations, int) or isinstance(
            max_operations,
            bool,
        ):
            raise TypeError("max_operations must be an integer")
        if max_operations < 1:
            raise ValueError("max_operations must be positive")
        engine_name = str(engine).strip()
        if not engine_name:
            raise ValueError("engine must not be empty")
        self._registry = registry
        self._engine = engine_name
        self._max_operations = max_operations

    @property
    def registry(self) -> ParameterRegistry:
        return self._registry

    def assemble(
        self,
        resolution: SemanticScopeResolution,
    ) -> SemanticAssemblyResult:
        if not isinstance(resolution, SemanticScopeResolution):
            return _error(
                "assembler_invalid_scope",
                "Assembler input must be a SemanticScopeResolution.",
                reason="invalid_scope_type",
                actual_type=type(resolution).__name__,
            )
        try:
            return self._assemble(resolution)
        except _AssemblyFailure as exc:
            return exc.error
        except (TypeError, ValueError, OverflowError) as exc:
            return _error(
                "assembler_invalid_contract",
                "Scope data could not be represented by the semantic IR.",
                reason="invalid_ir_contract",
                detail=str(exc),
            )

    def _assemble(
        self,
        resolution: SemanticScopeResolution,
    ) -> SemanticAssemblyResult:
        extraction = resolution.extraction
        raw_prompt = extraction.normalized.raw_text

        if extraction.residue_spans:
            _raise_error(
                "assembler_unresolved_text",
                "Meaningful source text remains unresolved.",
                issues=tuple(
                    {
                        "source_clause": span.raw_text,
                        "start": span.raw_start,
                        "end": span.raw_end,
                        "reason": span.reason,
                    }
                    for span in extraction.residue_spans
                ),
            )
        if resolution.ambiguous_slots:
            slots = _unique_slots(resolution.ambiguous_slots)
            _raise_error(
                "assembler_ambiguous_scope",
                "Ambiguous semantic slots cannot be assembled atomically.",
                issues=tuple(_slot_issue(slot, "ambiguous_slot") for slot in slots),
            )
        if resolution.guards:
            _raise_error(
                "assembler_guard_rejected",
                "Negation, exclusion, and disjunction require clarification.",
                issues=tuple(
                    {
                        "guard": guard.concept_id,
                        "kind": guard.kind,
                        "source_clause": guard.slot.evidence.raw_text,
                        "start": guard.slot.evidence.start,
                        "end": guard.slot.evidence.end,
                        "reason": "authoritative_guard",
                    }
                    for guard in resolution.guards
                ),
            )

        terminal_slots = tuple(
            slot
            for slot in extraction.slots
            if not slot.is_ambiguous and slot.slot == "terminal"
        )
        if terminal_slots:
            return self._assemble_terminal(
                resolution,
                terminal_slots=terminal_slots,
            )

        bound_context_ids = {
            id(slot)
            for group in resolution.operation_groups
            for slot in group.supporting_slots
        }
        unsafe_context = tuple(
            slot
            for slot in extraction.slots
            if (
                slot.slot in _UNSAFE_CONTEXT_SLOTS
                and id(slot) not in bound_context_ids
            )
        )
        if unsafe_context:
            _raise_error(
                "assembler_context_feedback_unsupported",
                (
                    "Context, terminal, and group feedback are not safe "
                    "without controller state."
                ),
                issues=tuple(
                    _slot_issue(slot, "context_required")
                    for slot in unsafe_context
                ),
            )

        blocking_scope_errors = tuple(
            error
            for error in resolution.errors
            if not _is_unique_comparative_false_positive(error, resolution)
        )
        if blocking_scope_errors:
            _raise_error(
                "assembler_scope_rejected",
                "Scope resolution contains an atomic ambiguity or conflict.",
                issues=tuple(
                    _scope_error_issue(error) for error in blocking_scope_errors
                ),
            )

        groups = resolution.operation_groups
        if not groups:
            _raise_error(
                "assembler_no_operation",
                "No grounded edit operation is available for assembly.",
                reason="no_operation_group",
            )
        if len(groups) > self._max_operations:
            _raise_error(
                "assembler_operation_limit_exceeded",
                (
                    f"A request supports at most {self._max_operations} "
                    "operations."
                ),
                operation_count=len(groups),
                maximum=self._max_operations,
                reason="operation_limit",
            )

        axis_ids = [group.axis_id for group in groups]
        if len(axis_ids) != len(set(axis_ids)):
            _raise_error(
                "assembler_duplicate_axis",
                "The same axis cannot be assembled more than once.",
                axes=axis_ids,
                reason="duplicate_axis",
            )
        unknown_axes = sorted(
            axis_id
            for axis_id in axis_ids
            if axis_id not in self._registry.axes
        )
        if unknown_axes:
            _raise_error(
                "assembler_unknown_axis",
                "Scope resolution contains axes outside the registry.",
                axes=unknown_axes,
                reason="unknown_axis",
            )

        region = resolution.region_id or "all"
        if region not in self._registry.regions:
            _raise_error(
                "assembler_unknown_region",
                "Scope resolution contains a region outside the registry.",
                region=region,
                reason="unknown_region",
            )
        distinct_regions = {
            _region_id(slot) for slot in resolution.region_slots
        }
        if len(distinct_regions) > 1:
            _raise_error(
                "assembler_multiple_regions",
                "A request cannot edit multiple regions atomically.",
                regions=sorted(distinct_regions),
                reason="multiple_regions",
            )

        unbound = _unbound_semantic_slots(resolution)
        if unbound:
            _raise_error(
                "assembler_unbound_semantic_slot",
                "A meaningful semantic slot was not bound to an operation.",
                issues=tuple(
                    _slot_issue(slot, "unbound_semantic_slot")
                    for slot in unbound
                ),
            )

        operations = tuple(
            self._assemble_group(group, region=region)
            for group in groups
        )
        region_evidence = tuple(
            _slot_evidence(
                slot,
                slot_name=(
                    "region_axis_binding"
                    if slot.namespace == "axis"
                    else "region_object_binding"
                    if slot.slot == "region_object"
                    else "region"
                ),
                concept_id=_region_id(slot),
            )
            for slot in resolution.region_slots
        )
        languages = _evidence_languages(
            (
                *region_evidence,
                *(
                    evidence
                    for operation in operations
                    for evidence in operation.evidence
                ),
            )
        )
        ir = SemanticIR(
            raw_prompt=raw_prompt,
            normalized_prompt=extraction.normalized.text,
            operations=operations,
            region=region,
            language_sources=languages,
            decision_source="semantic_registry",
            evidence=region_evidence,
            parser_version=SEMANTIC_ASSEMBLER_VERSION,
            confidence=1.0,
        )
        try:
            return validate_semantic_ir(
                ir,
                registry=self._registry,
                engine=self._engine,
                max_operations=self._max_operations,
            )
        except SemanticValidationError as exc:
            return SemanticAssemblyError(
                code="assembler_validation_failed",
                message=(
                    "Assembled semantics failed the shared validation "
                    "boundary."
                ),
                issues=tuple(
                    {
                        "validator_code": exc.code,
                        **dict(issue),
                    }
                    for issue in exc.issues
                )
                or (
                    {
                        "validator_code": exc.code,
                        "reason": "semantic_validation_failed",
                    },
                ),
                status_code=exc.status_code,
            )

    def _assemble_terminal(
        self,
        resolution: SemanticScopeResolution,
        *,
        terminal_slots: tuple[SemanticSlot, ...],
    ) -> SemanticAssemblyResult:
        extraction = resolution.extraction
        raw_prompt = extraction.normalized.raw_text
        other_semantics = tuple(
            slot
            for slot in extraction.slots
            if (
                not slot.is_ambiguous
                and slot.slot not in {"terminal", "noise", "function_word"}
            )
        )
        if resolution.operation_groups or other_semantics:
            _raise_error(
                "assembler_operation_conflict",
                "Terminal intent cannot be combined with an edit operation.",
                issues=tuple(
                    _slot_issue(slot, "terminal_operation_conflict")
                    for slot in (*terminal_slots, *other_semantics)
                ),
            )
        terminal_values = {str(slot.value) for slot in terminal_slots}
        if len(terminal_slots) != 1 or len(terminal_values) != 1:
            _raise_error(
                "assembler_operation_conflict",
                "A request must contain exactly one terminal intent.",
                issues=tuple(
                    _slot_issue(slot, "conflicting_terminal_intent")
                    for slot in terminal_slots
                ),
            )
        if resolution.errors:
            _raise_error(
                "assembler_scope_rejected",
                "Terminal intent contains unresolved scope structure.",
                issues=tuple(
                    _scope_error_issue(error) for error in resolution.errors
                ),
            )

        terminal = terminal_slots[0]
        terminal_intent = next(iter(terminal_values))
        terminal_evidence = (
            _slot_evidence(
                terminal,
                slot_name="terminal",
                concept_id=str(terminal.concept_id),
            ),
        )
        ir = SemanticIR(
            raw_prompt=raw_prompt,
            normalized_prompt=extraction.normalized.text,
            operations=(),
            terminal_intent=terminal_intent,
            region="all",
            language_sources=_evidence_languages(terminal_evidence),
            decision_source="semantic_registry",
            evidence=terminal_evidence,
            parser_version=SEMANTIC_ASSEMBLER_VERSION,
            confidence=1.0,
        )
        try:
            return validate_semantic_ir(
                ir,
                registry=self._registry,
                engine=self._engine,
                max_operations=self._max_operations,
            )
        except SemanticValidationError as exc:
            return SemanticAssemblyError(
                code="assembler_validation_failed",
                message=(
                    "Assembled terminal semantics failed the shared "
                    "validation boundary."
                ),
                issues=tuple(
                    {
                        "validator_code": exc.code,
                        **dict(issue),
                    }
                    for issue in exc.issues
                )
                or (
                    {
                        "validator_code": exc.code,
                        "reason": "semantic_validation_failed",
                    },
                ),
                status_code=exc.status_code,
            )

    def _assemble_group(
        self,
        group: OperationSlotGroup,
        *,
        region: str,
    ) -> SemanticOperation:
        if group.ambiguous_slots:
            _raise_error(
                "assembler_ambiguous_scope",
                "An operation group contains an ambiguous axis.",
                issues=tuple(
                    _slot_issue(slot, "ambiguous_operation_group")
                    for slot in group.ambiguous_slots
                ),
            )
        if group.guard_slots:
            _raise_error(
                "assembler_guard_rejected",
                "An operation group contains an authoritative guard.",
                issues=tuple(
                    _slot_issue(slot, "authoritative_guard")
                    for slot in group.guard_slots
                ),
            )

        reset_slots = tuple(
            slot for slot in group.operation_slots if slot.value == "reset"
        )
        unsupported_operations = tuple(
            slot for slot in group.operation_slots if slot.value != "reset"
        )
        if unsupported_operations:
            _raise_error(
                "assembler_unsupported_operation",
                "Scope contains an unsupported operation marker.",
                issues=tuple(
                    _slot_issue(slot, "unsupported_operation_marker")
                    for slot in unsupported_operations
                ),
            )

        has_numeric = bool(
            group.numeric_slots or group.numeric_relation_slots
        )
        if reset_slots and has_numeric:
            _group_conflict(
                group,
                "Reset and numeric operations cannot be combined.",
                "reset_numeric_conflict",
            )
        if reset_slots:
            return self._assemble_reset(group, region=region)
        if has_numeric:
            return self._assemble_numeric(group, region=region)
        return self._assemble_relative(group, region=region)

    def _assemble_reset(
        self,
        group: OperationSlotGroup,
        *,
        region: str,
    ) -> SemanticOperation:
        if len(group.reset_slots) != 1:
            _group_conflict(
                group,
                "Reset requires exactly one reset marker.",
                "ambiguous_reset",
            )
        if (
            group.direction_slots
            or group.strength_slots
            or group.numeric_slots
            or group.numeric_relation_slots
            or group.observation_modifier_slots
            or group.state_link_slots
            or group.relation_slots
            or group.action_attribute_slots
            or group.surface_action_slots
        ):
            _group_conflict(
                group,
                "Reset cannot carry direction, strength, numeric, or state.",
                "reset_modifier_conflict",
            )
        evidence = _operation_evidence(
            group.axis_slot.evidence,
            *_supporting_evidence(group, registry=self._registry),
            _slot_evidence(
                group.reset_slots[0],
                slot_name="reset",
                concept_id="axis_reset",
            ),
        )
        return SemanticOperation(
            axis_id=group.axis_id,
            operation_type="reset",
            operation_kind="reset",
            direction=None,
            strength=None,
            region=region,
            evidence=evidence,
            language_sources=_evidence_languages(evidence),
        )

    def _assemble_numeric(
        self,
        group: OperationSlotGroup,
        *,
        region: str,
    ) -> SemanticOperation:
        if len(group.numeric_slots) != 1:
            _group_conflict(
                group,
                "Numeric operations require exactly one number.",
                "ambiguous_numeric_value",
            )
        if len(group.numeric_relation_slots) > 1:
            _group_conflict(
                group,
                "Numeric operations require one absolute/relative relation.",
                "ambiguous_numeric_relation",
            )
        if group.observation_modifier_slots or group.state_link_slots:
            _group_conflict(
                group,
                "Numeric operations cannot be inferred from observations.",
                "numeric_observation_conflict",
            )
        if group.relation_slots:
            _group_conflict(
                group,
                "Numeric operations cannot also carry continuation markers.",
                "numeric_context_conflict",
            )

        numeric_slot = group.numeric_slots[0]
        numeric = _finite_number(numeric_slot.value, group)
        relation_slot = (
            group.numeric_relation_slots[0]
            if group.numeric_relation_slots
            else None
        )
        return_slots = tuple(
            slot
            for slot in group.supporting_slots
            if slot.slot == "return_relation"
        )
        if relation_slot is not None:
            relation = str(relation_slot.value)
        elif len(return_slots) == 1:
            relation = "absolute"
        elif (
            group.direction_slots
            or (
                group.axis_slot.match_kind in {"action", "descriptor"}
                and group.axis_slot.requested_direction in {-1, 1}
            )
        ):
            relation = "relative"
        else:
            _group_conflict(
                group,
                (
                    "An unsigned numeric value needs an explicit relation, "
                    "a return marker, or grounded change direction."
                ),
                "missing_numeric_relation",
            )
        axis_evidence = group.axis_slot.evidence
        numeric_evidence = _slot_evidence(
            numeric_slot,
            slot_name="numeric",
            concept_id="numeric_literal",
        )
        relation_evidence = (
            (
                _slot_evidence(
                    relation_slot,
                    slot_name="numeric_relation",
                    concept_id=str(relation_slot.concept_id),
                ),
            )
            if relation_slot is not None
            else ()
        )

        if relation == "absolute":
            if (
                group.direction_slots
                or group.strength_slots
                or group.action_attribute_slots
                or group.surface_action_slots
            ):
                _group_conflict(
                    group,
                    (
                        "Absolute numeric edits require an axis noun and "
                        "cannot carry relative modifiers."
                    ),
                    "absolute_modifier_conflict",
                )
            schema = self._registry.axes[group.axis_id].schema
            if not schema.minimum <= numeric <= schema.maximum:
                _raise_error(
                    "assembler_numeric_out_of_range",
                    "Absolute numeric value is outside the axis schema.",
                    axis=group.axis_id,
                    value=numeric,
                    minimum=schema.minimum,
                    maximum=schema.maximum,
                    reason="absolute_value_out_of_range",
                )
            evidence = _operation_evidence(
                axis_evidence,
                *_supporting_evidence(group, registry=self._registry),
                numeric_evidence,
                *relation_evidence,
            )
            return SemanticOperation(
                axis_id=group.axis_id,
                operation_type="absolute",
                operation_kind="absolute",
                direction=None,
                strength=None,
                value=numeric,
                region=region,
                evidence=evidence,
                language_sources=_evidence_languages(evidence),
            )

        if relation != "relative":
            _group_conflict(
                group,
                "Numeric relation is outside the supported schema.",
                "unknown_numeric_relation",
            )
        if group.strength_slots:
            _group_conflict(
                group,
                "Relative numeric edits cannot also carry strength.",
                "numeric_strength_conflict",
            )
        if numeric == 0:
            _raise_error(
                "assembler_invalid_numeric",
                "Relative numeric delta must be non-zero.",
                axis=group.axis_id,
                value=numeric,
                reason="zero_relative_delta",
            )

        command_kind, semantic_direction, direction_evidence = (
            _command_direction(
                group,
                registry=self._registry,
                allow_missing=True,
            )
        )
        del command_kind
        numeric_direction = 1 if numeric > 0 else -1
        if (
            semantic_direction is not None
            and numeric < 0
            and semantic_direction != numeric_direction
        ):
            _group_conflict(
                group,
                "Signed numeric delta conflicts with semantic direction.",
                "signed_numeric_direction_conflict",
            )
        direction = semantic_direction or numeric_direction
        value = abs(numeric) * direction
        evidence = _operation_evidence(
            axis_evidence,
            *_supporting_evidence(group, registry=self._registry),
            numeric_evidence,
            *relation_evidence,
            *direction_evidence,
        )
        return SemanticOperation(
            axis_id=group.axis_id,
            operation_kind="relative_numeric",
            direction=direction,
            strength=None,
            value=value,
            region=region,
            evidence=evidence,
            language_sources=_evidence_languages(evidence),
        )

    def _assemble_relative(
        self,
        group: OperationSlotGroup,
        *,
        region: str,
    ) -> SemanticOperation:
        if any(
            slot.slot == "return_relation"
            for slot in group.supporting_slots
        ) and not _has_directional_return_support(group):
            _group_conflict(
                group,
                "A return relation needs an explicit numeric target.",
                "return_relation_without_numeric_target",
            )
        strength, strength_evidence = _strength(group)
        has_cross_clause_remedy = _has_cross_clause_direction(group)
        is_observation = bool(
            group.axis_slot.match_kind == "observation"
            or group.observation_attribute_direction_override in {-1, 1}
            or (
                not has_cross_clause_remedy
                and (
                    group.observation_modifier_slots
                    or (
                        group.state_link_slots
                        and not (
                        group.direction_slots
                        or group.action_attribute_slots
                        or group.surface_action_slots
                        or group.axis_slot.match_kind
                        in {"action", "descriptor"}
                        )
                    )
                )
            )
        )
        if is_observation:
            direction, direction_evidence = _observation_direction(group)
            target_group_intent = _group_feedback_target_intent(
                group,
                direction=direction,
                registry=self._registry,
            )
            if target_group_intent is not None:
                strength = "subtle"
            controller_contract = _group_shared_controller_contract(
                group,
                registry=self._registry,
            )
            kind = (
                "group_feedback"
                if target_group_intent is not None
                else "macro"
                if (
                    controller_contract is not None
                    and controller_contract.mode == "macro"
                    and controller_contract.relation == "initial"
                )
                else "observation"
            )
        else:
            target_group_intent = None
            kind, direction, direction_evidence = _command_direction(
                group,
                registry=self._registry,
            )

        evidence = _operation_evidence(
            group.axis_slot.evidence,
            *_supporting_evidence(group, registry=self._registry),
            *direction_evidence,
            *strength_evidence,
            *(
                slot.evidence
                for slot in (
                    *group.relation_slots,
                    *group.observation_modifier_slots,
                    *group.state_link_slots,
                )
            ),
        )
        return SemanticOperation(
            axis_id=group.axis_id,
            operation_kind=kind,
            direction=direction,
            strength=strength,
            region=region,
            target_group_intent=target_group_intent,
            evidence=evidence,
            language_sources=_evidence_languages(evidence),
        )


def assemble_semantic_ir(
    resolution: SemanticScopeResolution,
    *,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
    max_operations: int = MAX_SEMANTIC_OPERATIONS,
) -> SemanticAssemblyResult:
    return SemanticOperationAssembler(
        registry,
        engine=engine,
        max_operations=max_operations,
    ).assemble(resolution)


def assemble_semantic_operations(
    resolution: SemanticScopeResolution,
    *,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
    max_operations: int = MAX_SEMANTIC_OPERATIONS,
) -> SemanticAssemblyResult:
    """Compatibility name emphasizing the assembler's operation boundary."""

    return assemble_semantic_ir(
        resolution,
        registry=registry,
        engine=engine,
        max_operations=max_operations,
    )


def _command_direction(
    group: OperationSlotGroup,
    *,
    registry: ParameterRegistry,
    allow_missing: bool = False,
) -> tuple[str, int | None, tuple[RawSpanEvidence, ...]]:
    parts = _direction_parts(group)
    axis = group.axis_slot

    if axis.axis_role == "axis":
        action_direction = group.action_attribute_direction
        surface_action_direction = group.surface_action_direction
        if (
            parts.direct is not None
            and parts.comparative is not None
            and parts.direct != parts.comparative
        ):
            _group_conflict(
                group,
                "Direct and comparative directions disagree.",
                "conflicting_direction",
            )
        surface_direction = (
            parts.direct
            if parts.direct is not None
            else parts.comparative
        )
        scoped_direction = (
            surface_direction * group.direction_multiplier
            if surface_direction is not None
            else None
        )
        bound_directions = {
            direction
            for direction in (
                action_direction,
                surface_action_direction,
                _return_negative_direction(group),
            )
            if direction in {-1, 1}
        }
        if len(bound_directions) > 1:
            _group_conflict(
                group,
                "Bound actions disagree on the requested direction.",
                "conflicting_action_attribute_direction",
            )
        bound_direction = (
            next(iter(bound_directions)) if bound_directions else None
        )
        direction = (
            scoped_direction
            if scoped_direction is not None
            else bound_direction
        )
        if (
            bound_direction is not None
            and direction is not None
            and bound_direction != direction
        ):
            _group_conflict(
                group,
                "Bound action and scoped directions disagree.",
                "conflicting_action_attribute_direction",
            )
        if direction is None:
            if allow_missing:
                return "explicit_axis", None, ()
            _group_conflict(
                group,
                "An explicit axis needs a shared or local direction.",
                "missing_direction",
            )
        return (
            "explicit_axis",
            direction,
            (
                *tuple(slot.evidence for slot in group.direction_slots),
                *tuple(
                    _slot_evidence(
                        slot,
                        slot_name="action_attribute",
                        concept_id=str(slot.concept_id),
                    )
                    for slot in group.action_attribute_slots
                ),
                *tuple(
                    _slot_evidence(
                        slot,
                        slot_name="surface_action",
                        concept_id=str(slot.concept_id),
                    )
                    for slot in group.surface_action_slots
                ),
                *tuple(
                    _slot_evidence(
                        slot,
                        slot_name="direction",
                        concept_id=str(slot.concept_id),
                    )
                    for slot in _return_negative_action_slots(group)
                ),
            ),
        )

    if (
        axis.match_kind not in {"action", "descriptor"}
        or axis.requested_direction not in {-1, 1}
    ):
        if allow_missing:
            return "macro", None, ()
        _group_conflict(
            group,
            "Fused axis semantics do not provide a safe direction.",
            "unsupported_fused_axis",
        )
    direction = int(axis.requested_direction)
    controller_mode = _group_axis_controller_mode(
        group,
        registry=registry,
    )
    if axis.match_kind == "action":
        if parts.comparative is not None:
            direction *= parts.comparative
        operation_kind = controller_mode
        if parts.direct is not None:
            if parts.direct != direction:
                direction = parts.direct
                operation_kind = "explicit_axis"
            elif (
                _direct_direction_controller_mode(
                    group,
                    registry=registry,
                )
                != "macro"
            ):
                operation_kind = "explicit_axis"
    elif (
        parts.direct is not None
        and group.observation_modifier_slots
        and _has_cross_clause_direct_direction(group)
    ):
        direction = parts.direct
        operation_kind = "explicit_axis"
    else:
        if parts.comparative is not None:
            direction *= parts.comparative
        if parts.direct is not None and parts.direct != direction:
            _group_conflict(
                group,
                "Fused and direct directions disagree.",
                "conflicting_direction",
            )
        operation_kind = controller_mode
    direction_evidence = tuple(
        slot.evidence for slot in group.direction_slots
    )
    if not direction_evidence:
        direction_evidence = (
            _slot_evidence(
                axis,
                slot_name="axis_direction",
                concept_id=axis.concept_id or group.axis_id,
            ),
        )
    return operation_kind, direction, direction_evidence


def _group_axis_controller_mode(
    group: OperationSlotGroup,
    *,
    registry: ParameterRegistry,
) -> str:
    """Resolve controller mode from the exact typed axis aliases in scope."""

    primary = registry.resolve_axis_alias(
        group.axis_slot.evidence.raw_text,
        group.axis_slot.language,
    )
    if primary is None or primary.axis_id != group.axis_id:
        _group_conflict(
            group,
            "Axis controller mode cannot be re-resolved from its evidence.",
            "unresolved_axis_controller_mode",
        )
    support_bindings = tuple(
        binding
        for slot in group.supporting_slots
        if (
            slot.namespace == "axis"
            and str(slot.value) == group.axis_id
            and slot.axis_role == "axis"
        )
        if (
            binding := registry.resolve_axis_alias(
                slot.evidence.raw_text,
                slot.language,
            )
        )
        is not None
        and binding.axis_id == group.axis_id
    )
    support_modes = {
        binding.controller_mode for binding in support_bindings
    }
    if len(support_modes) > 1:
        _group_conflict(
            group,
            "Axis support aliases declare conflicting controller modes.",
            "conflicting_axis_controller_mode",
        )
    if support_modes:
        return next(iter(support_modes))
    return primary.controller_mode


def _group_feedback_target_intent(
    group: OperationSlotGroup,
    *,
    direction: int,
    registry: ParameterRegistry,
) -> str | None:
    """Resolve one typed overdone effect to its registry-owned macro group."""

    if direction not in {-1, 1}:
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

    definition = registry.get_axis(group.axis_id)
    if not any(
        alias.controller_mode == "macro"
        for alias in definition.aliases
    ):
        return None
    if group.resolved_direction != direction:
        return None
    return (
        definition.policy.positive_intent
        if direction == -1
        else definition.policy.negative_intent
    )


def _direct_direction_controller_mode(
    group: OperationSlotGroup,
    *,
    registry: ParameterRegistry,
) -> str | None:
    modes = {
        contract.mode
        for slot in group.direction_slots
        if (
            slot.concept_id not in _COMPARATIVE_CONCEPTS
            and slot.value in {-1, 1}
        )
        if (
            definition := registry.shared_concepts.get(
                str(slot.concept_id)
            )
        )
        is not None
        if (
            contract := registry.resolve_shared_alias_contract(
                definition.slot,
                slot.evidence.raw_text,
                slot.language,
            )
        )
        is not None
        and contract.mode is not None
    }
    if len(modes) > 1:
        _group_conflict(
            group,
            "Direct directions declare conflicting controller modes.",
            "conflicting_direction_controller_mode",
        )
    return next(iter(modes)) if modes else None


def _group_shared_controller_contract(
    group: OperationSlotGroup,
    *,
    registry: ParameterRegistry,
) -> Any | None:
    """Resolve one shared alias contract already bound into this group."""

    contracts = []
    for slot in group.supporting_slots:
        definition = registry.shared_concepts.get(str(slot.concept_id))
        if definition is None:
            continue
        contract = registry.resolve_shared_alias_contract(
            definition.slot,
            slot.evidence.raw_text,
            slot.language,
        )
        if contract is not None and (
            contract.mode is not None or contract.relation is not None
        ):
            contracts.append(contract)
    unique = set(contracts)
    if len(unique) > 1:
        _group_conflict(
            group,
            "Shared aliases declare conflicting controller contracts.",
            "conflicting_shared_controller_contract",
        )
    return next(iter(unique)) if unique else None


def _has_cross_clause_direction(group: OperationSlotGroup) -> bool:
    return any(
        not (
            group.clause.normalized_start
            <= slot.normalized_start
            and slot.normalized_end <= group.clause.normalized_end
        )
        for slot in group.direction_slots
    )


def _has_directional_return_support(group: OperationSlotGroup) -> bool:
    """Return whether conversational return language has a grounded direction.

    A bare return relation remains unsafe because it could mean reset,
    absolute restoration, or relative undo.  It becomes a relative command
    only when the same operation already carries an explicit direction word
    or a directional action/descriptor axis.
    """

    if any(
        slot.value in {-1, 1}
        for slot in group.direction_slots
    ):
        return True
    if (
        group.axis_slot.match_kind in {"action", "descriptor"}
        and group.axis_slot.requested_direction in {-1, 1}
    ):
        return True
    if _return_negative_direction(group) in {-1, 1}:
        return True
    support_kinds = {
        str(slot.slot) for slot in group.supporting_slots
    }
    return bool(
        group.axis_slot.match_kind == "observation"
        and group.axis_slot.requested_direction in {-1, 1}
        and {
            "generic_action",
            "anaphora",
            "return_relation",
        }.issubset(support_kinds)
    )


def _return_negative_action_slots(
    group: OperationSlotGroup,
) -> tuple[SemanticSlot, ...]:
    return tuple(
        slot
        for slot in group.supporting_slots
        if (
            slot.slot == "generic_action"
            and slot.value == "return_negative"
        )
    )


def _return_negative_direction(
    group: OperationSlotGroup,
) -> int | None:
    actions = _return_negative_action_slots(group)
    returns = tuple(
        slot
        for slot in group.supporting_slots
        if slot.slot == "return_relation"
    )
    if (
        group.axis_slot.axis_role == "axis"
        and len(actions) == 1
        and len(returns) == 1
    ):
        return -1
    return None


def _has_cross_clause_direct_direction(group: OperationSlotGroup) -> bool:
    return any(
        slot.concept_id not in _COMPARATIVE_CONCEPTS
        and slot.value in {-1, 1}
        and not (
            group.clause.normalized_start
            <= slot.normalized_start
            and slot.normalized_end <= group.clause.normalized_end
        )
        for slot in group.direction_slots
    )


def _observation_direction(
    group: OperationSlotGroup,
) -> tuple[int, tuple[RawSpanEvidence, ...]]:
    axis = group.axis_slot
    modifiers = {
        str(slot.value) for slot in group.observation_modifier_slots
    }
    if not modifiers.issubset(_OBSERVATION_VALUES) or len(modifiers) > 1:
        _group_conflict(
            group,
            "Observation modifiers do not identify one correction.",
            "ambiguous_observation",
        )
    modifier = next(iter(modifiers), None)
    parts = _direction_parts(group)
    if (
        parts.direct is not None
        and parts.comparative is not None
        and parts.direct != parts.comparative
    ):
        _group_conflict(
            group,
            "Observation direction modifiers disagree.",
            "conflicting_observation_direction",
        )
    source_direction = parts.direct or parts.comparative
    if source_direction is not None and axis.axis_role == "axis":
        source_direction *= group.direction_multiplier
    attribute_direction = group.observation_attribute_direction

    if (
        source_direction is not None
        and group.state_link_slots
        and modifier in {"too", "too_much", "not_enough", "mild"}
    ):
        direction = (
            source_direction
            if modifier == "not_enough"
            else -source_direction
        )
        anchor = group.observation_modifier_slots[0]
    elif (
        axis.match_kind == "observation"
        and axis.requested_direction in {-1, 1}
    ):
        direction = int(axis.requested_direction)
        anchor = axis
    elif (
        axis.match_kind in {"action", "descriptor"}
        and axis.requested_direction in {-1, 1}
    ):
        base = int(axis.requested_direction)
        direction = base if modifier == "not_enough" else -base
        anchor = (
            group.observation_modifier_slots[0]
            if group.observation_modifier_slots
            else axis
        )
    elif axis.axis_role == "axis":
        if attribute_direction is not None:
            direction = (
                attribute_direction
                if modifier == "not_enough"
                else -attribute_direction
            )
        elif modifier == "not_enough":
            direction = group.direction_multiplier
        elif modifier in {"too", "too_much", "mild"}:
            direction = -(
                source_direction or group.direction_multiplier
            )
        else:
            _group_conflict(
                group,
                "An axis observation lacks too/not-enough semantics.",
                "missing_observation_modifier",
            )
        anchor = group.observation_modifier_slots[0]
    else:
        _group_conflict(
            group,
            "Observation axis does not carry a correction polarity.",
            "unsupported_observation_axis",
        )

    direction_evidence = [
        _slot_evidence(
            anchor,
            slot_name=(
                "resolved_observation"
                if anchor.evidence.slot == "resolved_observation"
                else "axis_direction"
                if anchor is axis
                else "direction"
            ),
            concept_id=anchor.concept_id or group.axis_id,
        )
    ]
    direction_evidence.extend(
        slot.evidence for slot in group.direction_slots
    )
    return direction, tuple(direction_evidence)


def _direction_parts(group: OperationSlotGroup) -> _DirectionParts:
    direct = {
        int(slot.value)
        for slot in group.direction_slots
        if slot.concept_id not in _COMPARATIVE_CONCEPTS
        and slot.value in {-1, 1}
    }
    comparative = {
        int(slot.value)
        for slot in group.direction_slots
        if slot.concept_id in _COMPARATIVE_CONCEPTS
        and slot.value in {-1, 1}
    }
    if len(direct) > 1 or len(comparative) > 1:
        _group_conflict(
            group,
            "Direction slots contain contradictory values.",
            "conflicting_direction",
        )
    return _DirectionParts(
        direct=next(iter(direct), None),
        comparative=next(iter(comparative), None),
    )


def _strength(
    group: OperationSlotGroup,
) -> tuple[str, tuple[RawSpanEvidence, ...]]:
    values = {str(slot.value) for slot in group.strength_slots}
    if len(values) > 1:
        _group_conflict(
            group,
            "Strength slots contain contradictory values.",
            "conflicting_strength",
        )
    strength = next(iter(values), "normal")
    if strength not in _STRENGTHS:
        _group_conflict(
            group,
            "Strength is outside the supported semantic schema.",
            "invalid_strength",
        )
    return strength, tuple(slot.evidence for slot in group.strength_slots)


def _is_unique_comparative_false_positive(
    error: ScopeError,
    resolution: SemanticScopeResolution,
) -> bool:
    """Narrowly repair the resolver's pre-multiplication direction check."""

    if error.code != "conflicting_direction_scope":
        return False
    axis_slots = tuple(
        slot for slot in error.slots if slot.namespace == "axis"
    )
    if len(axis_slots) != 1:
        return False
    group = next(
        (
            candidate
            for candidate in resolution.operation_groups
            if candidate.axis_slot is axis_slots[0]
        ),
        None,
    )
    if group is None or group.fused_direction not in {-1, 1}:
        return False
    if not group.direction_slots or not all(
        slot.concept_id in _COMPARATIVE_CONCEPTS
        and slot.value in {-1, 1}
        for slot in group.direction_slots
    ):
        return False
    comparative_values = {
        int(slot.value) for slot in group.direction_slots
    }
    if len(comparative_values) != 1:
        return False
    product = int(group.fused_direction) * next(iter(comparative_values))
    return product in {-1, 1}


def _unbound_semantic_slots(
    resolution: SemanticScopeResolution,
) -> tuple[SemanticSlot, ...]:
    selected_ambiguities = tuple(
        slot
        for group in resolution.operation_groups
        for slot in _group_slots(group)
        if slot.evidence.slot in {"resolved_direction", "effect_support"}
    )
    resolved_ambiguous_ids = {
        id(source)
        for source in resolution.extraction.ambiguous_slots
        if any(
            selected.evidence.start == source.evidence.start
            and selected.evidence.end == source.evidence.end
            and selected.interpretations[0].semantic_key
            in {
                interpretation.semantic_key
                for interpretation in source.interpretations
            }
            for selected in selected_ambiguities
        )
    }
    bound_ids = {
        id(slot)
        for group in resolution.operation_groups
        for slot in _group_slots(group)
    }
    bound_ids.update(id(slot) for slot in resolution.region_slots)
    bound_ids.update(resolved_ambiguous_ids)
    return tuple(
        slot
        for slot in resolution.extraction.slots
        if (
            slot.slot not in _STRUCTURAL_SLOTS
            and slot.slot not in _UNSAFE_CONTEXT_SLOTS
            and slot.slot not in {"guard", "negation"}
            and id(slot) not in bound_ids
        )
    )


def _group_slots(group: OperationSlotGroup) -> tuple[SemanticSlot, ...]:
    return (
        group.axis_slot,
        *group.direction_slots,
        *group.strength_slots,
        *group.numeric_slots,
        *group.operation_slots,
        *group.numeric_relation_slots,
        *group.relation_slots,
        *group.observation_modifier_slots,
        *group.state_link_slots,
        *group.guard_slots,
        *group.ambiguous_slots,
        *group.attribute_axis_slots,
        *group.action_attribute_slots,
        *group.surface_action_slots,
        *group.supporting_slots,
    )


def _finite_number(value: object, group: OperationSlotGroup) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _group_conflict(
            group,
            "Numeric slot is not numeric.",
            "invalid_numeric",
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        _group_conflict(
            group,
            "Numeric slot must be finite.",
            "invalid_numeric",
        )
    return numeric


def _slot_evidence(
    slot: SemanticSlot,
    *,
    slot_name: str,
    concept_id: str,
) -> RawSpanEvidence:
    source = slot.evidence
    if source.slot == slot_name and source.concept_id == concept_id:
        return source
    return RawSpanEvidence(
        start=source.start,
        end=source.end,
        raw_text=source.raw_text,
        slot=slot_name,
        concept_id=str(concept_id),
        language=source.language,
        confidence=source.confidence,
    )


def _supporting_evidence(
    group: OperationSlotGroup,
    *,
    registry: ParameterRegistry,
) -> tuple[RawSpanEvidence, ...]:
    """Encode resolved supporting bindings without changing their raw spans.

    The shared validator must distinguish the canonical operation anchor from
    redundant noun/observation context.  These labels are an immutable binding
    manifest, not a second interpretation of the prompt.
    """

    attributes = tuple(
        _slot_evidence(
            slot,
            slot_name="observation_attribute",
            concept_id=str(slot.concept_id),
        )
        for slot in group.attribute_axis_slots
    )
    action_attributes = tuple(
        _slot_evidence(
            slot,
            slot_name="action_attribute",
            concept_id=str(slot.concept_id),
        )
        for slot in group.action_attribute_slots
    )
    prior_event_ids = {
        id(slot)
        for slot in group.supporting_slots
        if (
            slot.namespace == "axis"
            and slot.match_kind == "action"
            and str(slot.value) == group.axis_id
            and any(
                aspect.slot == "clause_aspect"
                and aspect.value == "after"
                and slot.normalized_end <= aspect.normalized_start
                and aspect.normalized_end
                <= group.axis_slot.normalized_start
                for aspect in group.supporting_slots
            )
        )
    }
    support = tuple(
        _slot_evidence(
            slot,
            slot_name=(
                "resolved_relation_support"
                if (
                    slot.evidence.slot == "resolved_direction"
                    and slot.concept_id == "continuation_more"
                )
                else slot.evidence.slot
                if slot.evidence.slot
                in {
                    "resolved_direction",
                    "resolved_strength",
                    "resolved_observation",
                    "effect_support",
                    "effect_reference",
                }
                else "prior_event_support"
                if id(slot) in prior_event_ids
                else "axis_support"
                if (
                    slot.namespace == "axis"
                    and str(slot.value) == group.axis_id
                )
                else "context_axis_support"
                if slot.namespace == "axis"
                else "axis_attribute_region_support"
                if (
                    slot.namespace == "region"
                    and str(slot.value) in registry.regions
                    and group.axis_id
                    in registry.regions[
                        str(slot.value)
                    ].attribute_axis_ids
                )
                else "controller_contract_support"
                if _slot_has_controller_relation_contract(
                    slot,
                    group=group,
                    registry=registry,
                )
                else "effect_support"
                if slot.namespace == "effect"
                else "semantic_support"
            ),
            concept_id=str(slot.concept_id),
        )
        for slot in group.supporting_slots
    )
    return (*attributes, *action_attributes, *support)


def _slot_has_controller_relation_contract(
    slot: SemanticSlot,
    *,
    group: OperationSlotGroup,
    registry: ParameterRegistry,
) -> bool:
    definition = registry.shared_concepts.get(str(slot.concept_id))
    if definition is None:
        return False
    contract = registry.resolve_shared_alias_contract(
        definition.slot,
        slot.evidence.raw_text,
        slot.language,
    )
    return bool(
        contract is not None
        and contract.mode is not None
        and contract.relation is not None
        and any(
            observation.evidence.slot == "resolved_observation"
            and observation.evidence.start == slot.evidence.start
            and observation.evidence.end == slot.evidence.end
            for observation in group.observation_modifier_slots
        )
    )


def _operation_evidence(
    *items: RawSpanEvidence,
) -> tuple[RawSpanEvidence, ...]:
    unique: dict[tuple[object, ...], RawSpanEvidence] = {}
    for item in items:
        key = (
            item.start,
            item.end,
            item.slot,
            item.concept_id,
        )
        unique.setdefault(key, item)
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.start,
                item.end,
                0 if item.slot == "axis" else 1,
                item.slot,
            ),
        )
    )


def _evidence_languages(
    evidence: Iterable[RawSpanEvidence],
) -> tuple[str, ...]:
    languages = tuple(dict.fromkeys(item.language for item in evidence))
    return languages or ("und",)


def _region_id(slot: SemanticSlot) -> str:
    if slot.namespace == "region":
        return str(slot.value)
    if slot.slot in {"region_context", "region_object"}:
        return str(slot.value)
    return str(slot.concept_id)


def _unique_slots(slots: Iterable[SemanticSlot]) -> tuple[SemanticSlot, ...]:
    unique: dict[int, SemanticSlot] = {}
    for slot in slots:
        unique.setdefault(id(slot), slot)
    return tuple(unique.values())


def _slot_issue(slot: SemanticSlot, reason: str) -> dict[str, Any]:
    return {
        "source_clause": slot.evidence.raw_text,
        "start": slot.evidence.start,
        "end": slot.evidence.end,
        "slot": slot.slot,
        "concept_id": slot.concept_id,
        "reason": reason,
    }


def _scope_error_issue(error: ScopeError) -> dict[str, Any]:
    return {
        "scope_code": error.code,
        "clause_index": error.clause_index,
        "start": error.normalized_start,
        "end": error.normalized_end,
        "source_clauses": [
            slot.evidence.raw_text for slot in error.slots
        ],
        "reason": "scope_error",
    }


def _group_conflict(
    group: OperationSlotGroup,
    message: str,
    reason: str,
) -> None:
    _raise_error(
        "assembler_operation_conflict",
        message,
        axis=group.axis_id,
        clause_index=group.clause_index,
        reason=reason,
    )


def _error(
    code: str,
    message: str,
    *,
    status_code: int = 422,
    issues: tuple[dict[str, Any], ...] | None = None,
    **issue: Any,
) -> SemanticAssemblyError:
    if issues is None:
        issues = (dict(issue),) if issue else ()
    return SemanticAssemblyError(
        code=code,
        message=message,
        issues=issues,
        status_code=status_code,
    )


def _raise_error(
    code: str,
    message: str,
    *,
    issues: tuple[dict[str, Any], ...] | None = None,
    **issue: Any,
) -> None:
    raise _AssemblyFailure(
        _error(code, message, issues=issues, **issue)
    )


__all__ = [
    "SEMANTIC_ASSEMBLER_VERSION",
    "SemanticAssemblyError",
    "SemanticAssemblyResult",
    "SemanticOperationAssembler",
    "SemanticOperationAssemblyError",
    "assemble_semantic_ir",
    "assemble_semantic_operations",
]
