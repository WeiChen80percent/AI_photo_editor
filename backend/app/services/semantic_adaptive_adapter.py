from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.services.semantic_ir import (
    RawSpanEvidence,
    SemanticIR,
    SemanticOperation,
)
from app.services.semantic_registry import (
    DEFAULT_PARAMETER_REGISTRY,
    ParameterRegistry,
)
from app.services.semantic_validator import validate_semantic_ir


@dataclass(frozen=True, slots=True)
class AdaptiveCompileDraft:
    kind: str
    operations: tuple[dict[str, Any], ...] = ()
    explicit_region: str | None = None
    explicit_mask_type: str | None = None
    contextual_all: bool = False
    negated_axes: frozenset[str] = frozenset()
    preset_name: str | None = None
    semantic_ir: SemanticIR | None = None


_KIND_CONTRACT: Mapping[str, tuple[str, str, bool, bool]] = {
    # relation, explicitness, include_companions, group_feedback
    "explicit_axis": ("initial", "explicit_axis", False, False),
    "macro": ("initial", "macro_primary", True, False),
    "observation": ("correct", "feedback", False, False),
    "context_feedback": ("correct", "feedback", False, False),
    "group_feedback": ("correct", "feedback", True, True),
    "absolute": ("absolute", "explicit_axis", False, False),
    "relative_numeric": (
        "relative_numeric",
        "explicit_axis",
        False,
        False,
    ),
    "reset": ("reset", "explicit_axis", False, False),
}

_OBSERVATION_STRENGTH_CONTRACT: Mapping[str, str] = {
    "subtle": "subtle",
    "normal": "subtle",
    "strong": "strong",
}

_GOVERNING_ACTION_EVIDENCE_SLOTS = frozenset(
    {
        "direction",
        "axis_direction",
        "action_attribute",
        "surface_action",
        "resolved_direction",
    }
)


def semantic_ir_to_adaptive_draft(
    ir: SemanticIR,
    *,
    parent_snapshot: Mapping[str, Any] | None = None,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
) -> AdaptiveCompileDraft:
    """Convert validated intent into the existing compiler operation skeleton.

    This layer intentionally does not calculate masks, stable IDs, candidates,
    bounds, anchors, or render parameters.  Those remain in the common compiler
    finalizer and adaptive controller.
    """

    validated = validate_semantic_ir(
        ir,
        registry=registry,
        engine=engine,
    )
    if validated.terminal_intent is not None:
        return AdaptiveCompileDraft(
            kind=validated.terminal_intent,
            semantic_ir=validated,
        )
    operations = tuple(
        _operation_to_adaptive(
            operation,
            ir=validated,
            registry=registry,
            parent_snapshot=parent_snapshot,
        )
        for operation in validated.operations
    )
    region_evidence = _region_evidence(validated)
    explicit_region = validated.region if region_evidence else None
    return AdaptiveCompileDraft(
        kind="adaptive",
        operations=operations,
        explicit_region=explicit_region,
        explicit_mask_type=(
            registry.regions[explicit_region].mask_type
            if explicit_region is not None
            else None
        ),
        contextual_all=bool(region_evidence and validated.region == "all"),
        semantic_ir=validated,
    )


def _operation_to_adaptive(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    parent_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    effective_controller_kind = _effective_controller_kind(
        operation.operation_kind,
        registry_controller_mode=_controller_mode_from_registry_evidence(
            operation,
            registry=registry,
        ),
    )
    relation, explicitness, include_companions, group_feedback = (
        _KIND_CONTRACT[effective_controller_kind]
    )
    observation_remedy = _is_observation_remedy(
        operation,
        registry=registry,
    )
    if observation_remedy:
        relation = "correct"
        explicitness = "feedback"
        group_feedback = False
        # A concrete same-axis remedy narrows the preceding observation. It is
        # a correction episode, not a request to replay a broad macro group.
        include_companions = False
    definition = registry.get_axis(operation.axis_id)
    source_intent = _source_intent(
        operation,
        positive_intent=definition.policy.positive_intent,
        negative_intent=definition.policy.negative_intent,
        parent_snapshot=parent_snapshot,
    )
    direction = int(operation.direction or 0)
    numeric_value = (
        float(operation.value)
        if operation.operation_kind == "absolute"
        else None
    )
    relative_delta = (
        float(operation.value)
        if operation.operation_kind == "relative_numeric"
        else None
    )
    marker = _source_marker(operation)
    return {
        "operation_id": None,
        "group_id": None,
        "source_clause": ir.raw_prompt.strip(),
        "source_intent": source_intent,
        "axis": operation.axis_id,
        "direction": direction,
        "region": None,
        "mask_type": None,
        "relation": relation,
        "strength_hint": _controller_strength(
            operation,
            ir=ir,
            registry=registry,
            observation_remedy=observation_remedy,
        ),
        "confidence": _confidence_label(ir.confidence),
        "explicitness": explicitness,
        "role": "primary",
        "numeric_value": numeric_value,
        "relative_delta": relative_delta,
        "include_companions": include_companions,
        "group_feedback": group_feedback,
        "source_marker": marker,
        "consumed_texts": _consumed_texts(operation),
        "semantic_evidence": [
            evidence.as_dict() for evidence in operation.evidence
        ],
        "semantic_operation_kind": operation.operation_kind,
        "semantic_decision_source": ir.decision_source,
    }


def _effective_controller_kind(
    operation_kind: str,
    *,
    registry_controller_mode: str | None,
) -> str:
    """Merge assembler and alias modes without broadening the operation.

    Controller modes form a one-way contract: registry metadata may narrow a
    macro to one explicit axis, but it cannot add macro companions after the
    assembler has already resolved an explicit-axis operation.
    """

    if operation_kind == "explicit_axis":
        return "explicit_axis"
    if (
        operation_kind == "macro"
        and registry_controller_mode == "explicit_axis"
    ):
        return "explicit_axis"
    return operation_kind


def _controller_mode_from_registry_evidence(
    operation: SemanticOperation,
    *,
    registry: ParameterRegistry,
) -> str | None:
    """Resolve controller semantics from typed aliases, never prompt rules.

    Plain axis nouns do not decide macro behavior: a shared direction plus an
    axis noun is already represented by ``operation_kind``. A directional
    alias carries an explicit registry contract that may correct the
    assembler's language-level operation kind.
    """

    if operation.operation_kind not in {"explicit_axis", "macro"}:
        return None
    modes: set[str] = set()
    for evidence in operation.evidence:
        if evidence.slot not in {"axis", "axis_direction", "action_attribute"}:
            continue
        binding = registry.resolve_axis_alias(
            evidence.raw_text,
            evidence.language,
        )
        if (
            binding is None
            or binding.axis_id != operation.axis_id
            or binding.role == "axis"
        ):
            continue
        modes.add(binding.controller_mode)
    if len(modes) > 1:
        raise ValueError(
            "validated operation contains conflicting alias controller modes"
        )
    return next(iter(modes)) if modes else None


def _is_observation_remedy(
    operation: SemanticOperation,
    *,
    registry: ParameterRegistry,
) -> bool:
    """Recognize a validated same-axis observation/remedy evidence episode."""

    if operation.operation_kind not in {"explicit_axis", "macro"}:
        return False
    has_observation = any(
        (
            definition := registry.shared_concepts.get(evidence.concept_id)
        )
        is not None
        and definition.slot == "observation_modifier"
        for evidence in operation.evidence
    )
    has_state_link = any(
        evidence.slot == "state_link"
        for evidence in operation.evidence
    )
    has_observed_axis_support = any(
        evidence.slot in {"axis_support", "observation_attribute"}
        and evidence.concept_id == operation.axis_id
        for evidence in operation.evidence
    )
    if has_observation and (has_state_link or has_observed_axis_support):
        return True

    if any(
        evidence.slot == "resolved_direction"
        and evidence.concept_id == "negated_removal_amount_direction"
        for evidence in operation.evidence
    ):
        # The resolver has re-proved a corrective upper-bound episode such as
        # "別去那麼多霧".  The exact derived evidence is validator-checked; the
        # adapter must not infer this relation from a negation alias alone.
        return True

    controller_support = tuple(
        evidence
        for evidence in operation.evidence
        if evidence.slot == "controller_contract_support"
    )
    if len(controller_support) == 1:
        support = controller_support[0]
        definition = registry.shared_concepts.get(support.concept_id)
        contract = (
            registry.resolve_shared_alias_contract(
                definition.slot,
                support.raw_text,
                support.language,
            )
            if definition is not None
            else None
        )
        if (
            contract is not None
            and contract.mode == "explicit_axis"
            and contract.relation == "correct"
            and contract.companions is False
            and any(
                evidence.slot == "resolved_observation"
                and evidence.start == support.start
                and evidence.end == support.end
                for evidence in operation.evidence
            )
        ):
            # Validation has already re-proved the typed shared-alias contract,
            # descriptor state, and inverse explicit remedy.  The adapter only
            # preserves that correction relation; it does not infer from text.
            return True

    concepts = {evidence.concept_id for evidence in operation.evidence}
    return {
        "clause_aspect_still",
        "request_desire_predicate",
    }.issubset(concepts)


def _controller_strength(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
    observation_remedy: bool,
) -> str:
    if operation.operation_kind in {
        "absolute",
        "relative_numeric",
        "reset",
    }:
        return _owned_controller_strength(
            operation,
            ir=ir,
            registry=registry,
        )

    # Explicit strength owned by this operation is the strongest non-numeric
    # contract.  In a remedy episode this is specifically the remedy-owned
    # strength because observation evidence is relabelled as semantic support.
    if operation.operation_kind == "observation":
        direct_strengths = _observation_intensity_strength_values(
            operation,
            registry=registry,
        )
    else:
        direct_strengths = _shared_strength_values(
            operation,
            ir=ir,
            registry=registry,
            evidence_slot="strength",
            owned_only=True,
        )
    if direct_strengths:
        return _single_strength(direct_strengths)

    resolved_strength = _resolved_controller_strength(operation)
    if resolved_strength is not None:
        return resolved_strength

    if observation_remedy:
        observation_intensity = _observation_intensity_strength_values(
            operation,
            registry=registry,
        )
        if observation_intensity:
            return _single_strength(observation_intensity)

    alias_defaults = _controller_alias_default_strengths(
        operation,
        ir=ir,
        registry=registry,
    )
    if alias_defaults:
        return _single_strength(alias_defaults)

    if operation.operation_kind == "observation" or observation_remedy:
        return _observation_controller_strength_from_evidence(
            operation,
            registry=registry,
        )

    if any(evidence.slot == "strength" for evidence in operation.evidence):
        # A trailing strength can be copied onto coordinated operations.  A
        # non-owner keeps the ordinary controller default.
        return "normal"
    return operation.strength or "normal"


def _resolved_controller_strength(
    operation: SemanticOperation,
) -> str | None:
    """Use only validator-reproved derived strength evidence.

    Derived upper-bound relations deliberately sit below an explicitly owned
    strength but above alias/category observation defaults.  This preserves
    ``far too`` as strong while keeping corrective forms such as
    ``not too much`` subtle.
    """

    if not any(
        evidence.slot == "resolved_strength"
        for evidence in operation.evidence
    ):
        return None
    strength = operation.strength
    return strength if strength in _OBSERVATION_STRENGTH_CONTRACT else None


def _owned_controller_strength(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
) -> str:
    strengths = _shared_strength_values(
        operation,
        ir=ir,
        registry=registry,
        evidence_slot="strength",
        owned_only=True,
    )
    if strengths:
        return _single_strength(strengths)

    has_strength_evidence = any(
        evidence.slot == "strength"
        for evidence in operation.evidence
    )
    if has_strength_evidence:
        # The assembler may copy a trailing modifier onto coordinated
        # operations.  When provenance assigns it to another operation, the
        # current operation keeps the controller's normal default.
        return "normal"
    return operation.strength or "normal"


def _controller_alias_default_strengths(
    operation: SemanticOperation,
    *,
    ir: SemanticIR,
    registry: ParameterRegistry,
) -> set[str]:
    """Resolve exact-source/default alias contracts from typed evidence."""

    values: set[str] = set()
    seen_evidence: set[tuple[int, int, str]] = set()
    for evidence in operation.evidence:
        evidence_key = (
            evidence.start,
            evidence.end,
            evidence.concept_id,
        )
        if evidence_key in seen_evidence:
            continue
        seen_evidence.add(evidence_key)
        definition = registry.shared_concepts.get(evidence.concept_id)
        if definition is None:
            continue
        # Strength aliases are resolved by the explicit-strength ownership
        # path above.  This stage is for observation/comparative alias defaults.
        if definition.slot == "strength":
            continue
        contract = registry.resolve_shared_alias_contract(
            definition.slot,
            evidence.raw_text,
            evidence.language,
        )
        if contract is not None and contract.default_strength is not None:
            values.add(contract.default_strength)
    return values


def _observation_intensity_strength_values(
    operation: SemanticOperation,
    *,
    registry: ParameterRegistry,
) -> set[str]:
    """Return strength modifiers that intensify an observation relation.

    A strength before the typed observation modifier (``far too bright``)
    controls correction magnitude.  A strength after it (``too strong``)
    describes the observed state and must not become a large correction.
    """

    observation_starts = tuple(
        evidence.start
        for evidence in operation.evidence
        if (
            (
                definition := registry.shared_concepts.get(
                    evidence.concept_id
                )
            )
            is not None
            and definition.slot == "observation_modifier"
        )
    )
    if not observation_starts:
        return set()
    first_observation_start = min(observation_starts)
    values: set[str] = set()
    seen_evidence: set[tuple[int, int, str]] = set()
    for evidence in operation.evidence:
        evidence_key = (
            evidence.start,
            evidence.end,
            evidence.concept_id,
        )
        if evidence_key in seen_evidence or evidence.end > first_observation_start:
            continue
        seen_evidence.add(evidence_key)
        definition = registry.shared_concepts.get(evidence.concept_id)
        if definition is None or definition.slot != "strength":
            continue
        contract = registry.resolve_shared_alias_contract(
            "strength",
            evidence.raw_text,
            evidence.language,
        )
        value = (
            contract.default_strength
            if contract is not None
            and contract.default_strength is not None
            else str(definition.value)
        )
        if value in _OBSERVATION_STRENGTH_CONTRACT:
            values.add(value)
    return values


def _observation_controller_strength_from_evidence(
    operation: SemanticOperation,
    *,
    registry: ParameterRegistry,
) -> str:
    observation_concepts = {
        evidence.concept_id
        for evidence in operation.evidence
        if (
            (
                definition := registry.shared_concepts.get(
                    evidence.concept_id
                )
            )
            is not None
            and definition.slot == "observation_modifier"
        )
    }
    if "observation_too_much" in observation_concepts:
        return "strong"
    if observation_concepts:
        # In an observation such as "too strong", `strong` describes the
        # current image state.  It is not a request for a strong corrective
        # step.  The typed observation modifier owns the controller strength.
        return "subtle"
    return _observation_controller_strength(
        operation.strength or "normal"
    )


def _shared_strength_values(
    operation: SemanticOperation,
    *,
    ir: SemanticIR | None = None,
    registry: ParameterRegistry,
    evidence_slot: str | None,
    owned_only: bool = False,
) -> set[str]:
    values: set[str] = set()
    for evidence in operation.evidence:
        if evidence_slot is not None and evidence.slot != evidence_slot:
            continue
        if (
            owned_only
            and ir is not None
            and not _strength_belongs_to_operation(
                evidence,
                operation=operation,
                ir=ir,
                registry=registry,
            )
        ):
            continue
        definition = registry.shared_concepts.get(evidence.concept_id)
        if definition is None or definition.slot != "strength":
            continue
        contract = registry.resolve_shared_alias_contract(
            "strength",
            evidence.raw_text,
            evidence.language,
        )
        value = (
            contract.default_strength
            if contract is not None
            and contract.default_strength is not None
            else str(definition.value)
        )
        if value in _OBSERVATION_STRENGTH_CONTRACT:
            values.add(value)
    return values


def _strength_belongs_to_operation(
    strength_evidence: RawSpanEvidence,
    *,
    operation: SemanticOperation,
    ir: SemanticIR,
    registry: ParameterRegistry,
) -> bool:
    """Assign a copied strength modifier from typed span provenance.

    A modifier remains distributive when coordinated operations share one
    governing action.  When each operation has a distinct governing action,
    the closest operation owns the modifier.  No raw prompt token is examined.
    """

    candidates = tuple(
        candidate
        for candidate in ir.operations
        if strength_evidence in candidate.evidence
    )
    if len(candidates) <= 1:
        return True

    governing_evidence = tuple(
        tuple(
            evidence
            for evidence in candidate.evidence
            if evidence.slot in _GOVERNING_ACTION_EVIDENCE_SLOTS
        )
        for candidate in candidates
    )
    governing_keys = tuple(
        {
            (evidence.start, evidence.end, evidence.concept_id)
            for evidence in evidence_items
        }
        for evidence_items in governing_evidence
    )
    if all(governing_keys) and set.intersection(*governing_keys):
        return True

    request_predicates = {
        (
            evidence.start,
            evidence.end,
            evidence.concept_id,
        ): evidence
        for candidate in candidates
        for evidence in candidate.evidence
        if (
            (
                definition := registry.shared_concepts.get(
                    evidence.concept_id
                )
            )
            is not None
            and definition.slot == "request_predicate"
        )
    }
    if (
        len(request_predicates) == 1
        and _is_descriptor_coordination_without_distinct_actions(
            candidates,
            strength_evidence=strength_evidence,
            request_predicate=next(iter(request_predicates.values())),
            registry=registry,
        )
    ):
        return True

    distances = {
        id(candidate): min(
            _span_distance(strength_evidence, evidence)
            for evidence in (
                governing_evidence[index]
                or tuple(
                    item
                    for item in candidate.evidence
                    if item.slot != "strength"
                )
            )
        )
        for index, candidate in enumerate(candidates)
    }
    nearest = min(distances.values())
    return distances[id(operation)] == nearest


def _is_descriptor_coordination_without_distinct_actions(
    operations: tuple[SemanticOperation, ...],
    *,
    strength_evidence: RawSpanEvidence,
    request_predicate: RawSpanEvidence,
    registry: ParameterRegistry,
) -> bool:
    """Recognize descriptors coordinated under one typed request predicate."""

    descriptor_starts: list[int] = []
    for operation in operations:
        has_descriptor = False
        for evidence in operation.evidence:
            if evidence.slot in {
                "surface_action",
                "resolved_direction",
            }:
                return False
            definition = registry.shared_concepts.get(evidence.concept_id)
            if (
                definition is not None
                and definition.slot == "direction"
            ):
                return False
            if evidence.slot not in {
                "axis",
                "axis_direction",
                "action_attribute",
            }:
                continue
            binding = registry.resolve_axis_alias(
                evidence.raw_text,
                evidence.language,
            )
            if binding is None or binding.axis_id != operation.axis_id:
                continue
            if binding.match_kind == "action":
                return False
            if (
                binding.match_kind in {"descriptor", "observation"}
                and binding.requested_direction in {-1, 1}
            ):
                has_descriptor = True
                descriptor_starts.append(evidence.start)
        if not has_descriptor:
            return False
    return bool(
        descriptor_starts
        and request_predicate.end <= strength_evidence.start
        and strength_evidence.end <= min(descriptor_starts)
    )


def _span_distance(
    left: RawSpanEvidence,
    right: RawSpanEvidence,
) -> int:
    if left.start >= right.end:
        return left.start - right.end
    if right.start >= left.end:
        return right.start - left.end
    return 0


def _single_strength(values: set[str]) -> str:
    if len(values) != 1:
        raise ValueError("validated operation contains conflicting strengths")
    return next(iter(values))


def _observation_controller_strength(semantic_strength: str) -> str:
    try:
        return _OBSERVATION_STRENGTH_CONTRACT[semantic_strength]
    except KeyError as exc:
        raise ValueError(
            f"unsupported observation strength {semantic_strength!r}"
        ) from exc


def _source_intent(
    operation: SemanticOperation,
    *,
    positive_intent: str,
    negative_intent: str,
    parent_snapshot: Mapping[str, Any] | None,
) -> str:
    if operation.operation_kind == "absolute":
        return "explicit_numeric"
    if operation.operation_kind == "relative_numeric":
        return "explicit_relative_numeric"
    if operation.operation_kind == "reset":
        return "axis_reset"
    if operation.operation_kind == "context_feedback":
        if parent_snapshot is None:
            # The semantic parser should normally resolve parent-only feedback
            # before constructing the IR.  Keeping this guard here makes the
            # adapter safe for grounded-LLM candidates as well.
            raise ValueError(
                "context_feedback requires a validated parent snapshot"
            )
        return "context_feedback"
    if operation.operation_kind == "group_feedback":
        assert operation.target_group_intent is not None
        return operation.target_group_intent
    return positive_intent if operation.direction == 1 else negative_intent


def _source_marker(operation: SemanticOperation) -> str:
    if not operation.evidence:
        return ""
    ordered = sorted(
        operation.evidence,
        key=lambda item: (item.start, item.end, item.slot),
    )
    return ordered[0].raw_text


def _consumed_texts(operation: SemanticOperation) -> list[str]:
    ordered = sorted(
        operation.evidence,
        key=lambda item: (item.start, item.end, item.slot),
    )
    unique: list[str] = []
    for evidence in ordered:
        if evidence.raw_text not in unique:
            unique.append(evidence.raw_text)
    return unique


def _region_evidence(ir: SemanticIR) -> tuple[Any, ...]:
    evidence = (
        *ir.evidence,
        *(
            item
            for operation in ir.operations
            for item in operation.evidence
        ),
    )
    return tuple(
        item
        for item in evidence
        if item.slot
        in {"region", "region_axis_binding", "region_object_binding"}
        and item.concept_id == ir.region
    )


def _confidence_label(confidence: float) -> str:
    return "high" if confidence >= 0.9 else "medium"


__all__ = [
    "AdaptiveCompileDraft",
    "semantic_ir_to_adaptive_draft",
]
