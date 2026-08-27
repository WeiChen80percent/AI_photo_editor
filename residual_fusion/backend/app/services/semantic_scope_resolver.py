"""Generic clause and modifier scope resolution for semantic edit slots.

The resolver is intentionally language- and axis-neutral.  It consumes the
grounded output of :mod:`semantic_slot_extractor`, partitions it by structural
connectors, and associates modifiers with axis slots.  It does not create a
``SemanticIR`` and it never reads controller state.

All domain decisions come from slot metadata or an injected
``ParameterRegistry``.  In particular, an axis that can also name a region is
recognized by the registry intersection rather than by a hard-coded axis list.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Literal

from app.services.semantic_registry import (
    DEFAULT_PARAMETER_REGISTRY,
    ParameterRegistry,
)
from app.services.semantic_slot_extractor import (
    SemanticSlot,
    SlotInterpretation,
    SlotExtraction,
)


BoundaryKind = Literal[
    "conjunction",
    "contrastive",
    "disjunction",
    "comma",
    "topic",
    "sentence",
]

_PUNCTUATION_BOUNDARIES: dict[str, BoundaryKind] = {
    ",": "comma",
    "，": "comma",
    "、": "comma",
    ":": "topic",
    "：": "topic",
    ";": "sentence",
    "；": "sentence",
    ".": "sentence",
    "。": "sentence",
    "!": "sentence",
    "！": "sentence",
    "?": "sentence",
    "？": "sentence",
}
_CLUSTER_BREAKS = frozenset({"contrastive", "disjunction", "sentence"})
_BOUNDARY_PRIORITY = {
    "conjunction": 10,
    "comma": 20,
    "topic": 30,
    "contrastive": 40,
    "disjunction": 50,
    "sentence": 60,
}
_MODIFIER_SLOTS = (
    "direction",
    "strength",
    "numeric",
    "operation",
    "numeric_relation",
    "relation",
    "observation_modifier",
    "state_link",
)
_GUARD_SLOTS = frozenset({"guard", "negation"})
_GROUP_FIELD_BY_SLOT = {
    "direction": "direction_slots",
    "strength": "strength_slots",
    "numeric": "numeric_slots",
    "operation": "operation_slots",
    "numeric_relation": "numeric_relation_slots",
    "relation": "relation_slots",
    "observation_modifier": "observation_modifier_slots",
    "state_link": "state_link_slots",
}
_COMPARATIVE_CONCEPTS = frozenset(
    {"comparative_more", "comparative_less"}
)
_OBSERVATION_VALUES = frozenset(
    {"too", "too_much", "not_enough", "mild"}
)
_SURFACE_ACTION_DIRECTIONS = {"remove": -1}
_CLAUSE_FORCE_SLOTS = frozenset(
    {
        "clause_aspect",
        "clause_modal",
        "clause_subject",
        "existential",
        "request_marker",
        "request_predicate",
    }
)
AUTHORITATIVE_SCOPE_ERROR_CODES = frozenset(
    {
        "clause_hypothetical_without_request",
        "clause_state_without_request",
        "existential_state_without_request",
        "progressive_state_without_request",
        "possessive_state_without_request",
        "subject_state_without_request",
        "unresolved_region_object",
    }
)


@dataclass(frozen=True, slots=True)
class ScopeGuard:
    """An authoritative safety concept that downstream code must handle."""

    kind: str
    concept_id: str
    value: str | int | float | bool
    slot: SemanticSlot
    clause_index: int | None
    authoritative: bool = True

    def __post_init__(self) -> None:
        for field_name in ("kind", "concept_id"):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value)
        if self.clause_index is not None and self.clause_index < 0:
            raise ValueError("clause_index must be non-negative")
        if not self.authoritative:
            raise ValueError("scope guards are always authoritative")


@dataclass(frozen=True, slots=True)
class ScopeError:
    """A structured ambiguity or conflict found before operation assembly."""

    code: str
    message: str
    clause_index: int | None = None
    normalized_start: int | None = None
    normalized_end: int | None = None
    slots: tuple[SemanticSlot, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("code", "message"):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, value)
        if self.clause_index is not None and self.clause_index < 0:
            raise ValueError("clause_index must be non-negative")
        if (self.normalized_start is None) != (self.normalized_end is None):
            raise ValueError("scope error span needs both start and end")
        if self.normalized_start is not None:
            if self.normalized_start < 0:
                raise ValueError("normalized_start must be non-negative")
            if self.normalized_end <= self.normalized_start:
                raise ValueError("scope error span must be non-empty")
        object.__setattr__(self, "slots", tuple(self.slots))


@dataclass(frozen=True, slots=True)
class ClauseScope:
    """One connector-delimited prompt clause with exact source provenance."""

    index: int
    coordination_group: int
    normalized_start: int
    normalized_end: int
    raw_start: int
    raw_end: int
    raw_text: str
    slots: tuple[SemanticSlot, ...]
    connector_before: tuple[SemanticSlot, ...] = ()
    connector_after: tuple[SemanticSlot, ...] = ()
    boundary_before: BoundaryKind | None = None
    boundary_after: BoundaryKind | None = None

    def __post_init__(self) -> None:
        if self.index < 0 or self.coordination_group < 0:
            raise ValueError("clause indices must be non-negative")
        if self.normalized_start < 0 or self.normalized_end <= self.normalized_start:
            raise ValueError("normalized clause span must be non-empty")
        if self.raw_start < 0 or self.raw_end <= self.raw_start:
            raise ValueError("raw clause span must be non-empty")
        if len(self.raw_text) != self.raw_end - self.raw_start:
            raise ValueError("raw_text must match the raw clause span")
        object.__setattr__(self, "slots", tuple(self.slots))
        object.__setattr__(
            self,
            "connector_before",
            tuple(self.connector_before),
        )
        object.__setattr__(
            self,
            "connector_after",
            tuple(self.connector_after),
        )

    @property
    def connector_slots(self) -> tuple[SemanticSlot, ...]:
        """All structural connector slots adjacent to this clause."""

        return (*self.connector_before, *self.connector_after)


@dataclass(frozen=True, slots=True)
class OperationSlotGroup:
    """All grounded slots currently associated with one axis occurrence."""

    axis_slot: SemanticSlot
    clause_index: int
    clause: ClauseScope
    normalized_start: int
    normalized_end: int
    region_slot: SemanticSlot | None = None
    direction_slots: tuple[SemanticSlot, ...] = ()
    strength_slots: tuple[SemanticSlot, ...] = ()
    numeric_slots: tuple[SemanticSlot, ...] = ()
    operation_slots: tuple[SemanticSlot, ...] = ()
    numeric_relation_slots: tuple[SemanticSlot, ...] = ()
    relation_slots: tuple[SemanticSlot, ...] = ()
    observation_modifier_slots: tuple[SemanticSlot, ...] = ()
    state_link_slots: tuple[SemanticSlot, ...] = ()
    guard_slots: tuple[SemanticSlot, ...] = ()
    ambiguous_slots: tuple[SemanticSlot, ...] = ()
    attribute_axis_slots: tuple[SemanticSlot, ...] = ()
    observation_attribute_direction_override: int | None = None
    action_attribute_slots: tuple[SemanticSlot, ...] = ()
    surface_action_slots: tuple[SemanticSlot, ...] = ()
    supporting_slots: tuple[SemanticSlot, ...] = ()
    request_force_proven: bool = False

    def __post_init__(self) -> None:
        if self.axis_slot.namespace != "axis":
            raise ValueError("axis_slot must have one unambiguous axis meaning")
        if self.clause_index != self.clause.index:
            raise ValueError("clause_index must match clause.index")
        if self.normalized_start < 0 or self.normalized_end <= self.normalized_start:
            raise ValueError("operation group span must be non-empty")
        tuple_fields = (
            "direction_slots",
            "strength_slots",
            "numeric_slots",
            "operation_slots",
            "numeric_relation_slots",
            "relation_slots",
            "observation_modifier_slots",
            "state_link_slots",
            "guard_slots",
            "ambiguous_slots",
            "attribute_axis_slots",
            "action_attribute_slots",
            "surface_action_slots",
            "supporting_slots",
        )
        for field_name in tuple_fields:
            object.__setattr__(
                self,
                field_name,
                tuple(getattr(self, field_name)),
            )
        if self.observation_attribute_direction_override not in {
            None,
            -1,
            1,
        }:
            raise ValueError(
                "observation attribute direction override must be -1, 1, "
                "or None"
            )
        if not isinstance(self.request_force_proven, bool):
            raise TypeError("request_force_proven must be a boolean")

    @property
    def axis_id(self) -> str:
        return str(self.axis_slot.value)

    @property
    def fused_direction(self) -> int | None:
        return self.axis_slot.requested_direction

    @property
    def explicit_directions(self) -> tuple[int, ...]:
        return tuple(
            int(slot.value)
            for slot in self.direction_slots
            if slot.value in {-1, 1}
        )

    @property
    def direction_multiplier(self) -> int:
        multiplier = self.axis_slot.direction_multiplier
        return (
            int(multiplier)
            if self.axis_slot.axis_role == "axis"
            and multiplier in {-1, 1}
            else 1
        )

    @property
    def canonical_explicit_directions(self) -> tuple[int, ...]:
        return tuple(
            direction * self.direction_multiplier
            for direction in self.explicit_directions
        )

    @property
    def resolved_direction(self) -> int | None:
        comparative = {
            int(slot.value)
            for slot in self.direction_slots
            if (
                slot.concept_id in _COMPARATIVE_CONCEPTS
                and slot.value in {-1, 1}
            )
        }
        direct = {
            int(slot.value) * self.direction_multiplier
            for slot in self.direction_slots
            if (
                slot.concept_id not in _COMPARATIVE_CONCEPTS
                and slot.value in {-1, 1}
            )
        }
        observation_values = {
            str(slot.value) for slot in self.observation_modifier_slots
        }
        has_bound_observed_state = any(
            slot.slot == "direction"
            and slot.value in {-1, 1}
            and (
                self.clause.normalized_start
                <= slot.normalized_start
                and slot.normalized_end <= self.clause.normalized_end
            )
            for slot in self.supporting_slots
        )
        has_cross_clause_remedy = any(
            slot.value in {-1, 1}
            and not (
                self.clause.normalized_start
                <= slot.normalized_start
                and slot.normalized_end <= self.clause.normalized_end
            )
            for slot in self.direction_slots
        )
        if (
            self.axis_slot.axis_role == "axis"
            and len(observation_values) == 1
            and len(direct) == 1
            and not comparative
            and has_bound_observed_state
            and has_cross_clause_remedy
        ):
            return next(iter(direct))
        if (
            self.axis_slot.match_kind == "action"
            and self.fused_direction in {-1, 1}
            and not observation_values
            and len(direct) <= 1
            and len(comparative) <= 1
            and (direct or comparative)
        ):
            direction = int(self.fused_direction)
            if comparative:
                direction *= next(iter(comparative))
            if direct:
                direct_direction = next(iter(direct))
                if direct_direction != direction:
                    direction = direct_direction
            return direction

        if (
            self.axis_slot.match_kind in {"action", "descriptor"}
            and self.fused_direction in {-1, 1}
            and len(observation_values) == 1
        ):
            expected = (
                int(self.fused_direction)
                if "not_enough" in observation_values
                else -int(self.fused_direction)
            )
            explicit = set(self.canonical_explicit_directions)
            if not explicit or explicit == {expected}:
                return expected

        attribute_direction = self.observation_attribute_direction
        if (
            attribute_direction in {-1, 1}
            and len(observation_values) == 1
        ):
            return (
                int(attribute_direction)
                if "not_enough" in observation_values
                else -int(attribute_direction)
            )

        if (
            self.axis_slot.axis_role == "axis"
            and self.state_link_slots
            and len(observation_values) == 1
            and not comparative
            and len(direct) == 1
        ):
            source_direction = next(iter(direct))
            return (
                source_direction
                if "not_enough" in observation_values
                else -source_direction
            )

        directions = set(self.canonical_explicit_directions)
        if self.fused_direction is not None:
            directions.add(self.fused_direction)
        if self.action_attribute_direction is not None:
            directions.add(self.action_attribute_direction)
        if self.surface_action_direction is not None:
            directions.add(self.surface_action_direction)
        return_negative_actions = tuple(
            slot
            for slot in self.supporting_slots
            if (
                slot.slot == "generic_action"
                and slot.value == "return_negative"
            )
        )
        return_relations = tuple(
            slot
            for slot in self.supporting_slots
            if slot.slot == "return_relation"
        )
        if (
            self.axis_slot.axis_role == "axis"
            and len(return_negative_actions) == 1
            and len(return_relations) == 1
        ):
            directions.add(-1)
        if len(directions) == 1:
            return next(iter(directions))
        if (
            self.axis_slot.match_kind in {"action", "descriptor"}
            and self.fused_direction in {-1, 1}
            and len(comparative) == 1
        ):
            expected = int(self.fused_direction) * next(iter(comparative))
            if not direct or direct == {expected}:
                return expected
        return None

    @property
    def reset_slots(self) -> tuple[SemanticSlot, ...]:
        return tuple(
            slot for slot in self.operation_slots if slot.value == "reset"
        )

    @property
    def observation_slots(self) -> tuple[SemanticSlot, ...]:
        return self.observation_modifier_slots

    @property
    def state_slots(self) -> tuple[SemanticSlot, ...]:
        return self.state_link_slots

    @property
    def observation_attribute_direction(self) -> int | None:
        if self.observation_attribute_direction_override in {-1, 1}:
            return self.observation_attribute_direction_override
        directions = {
            int(slot.requested_direction)
            for slot in self.attribute_axis_slots
            if slot.requested_direction in {-1, 1}
        }
        return next(iter(directions)) if len(directions) == 1 else None

    @property
    def action_attribute_direction(self) -> int | None:
        """Canonical direction supplied by a bound cross-axis action."""

        directions = {
            int(slot.requested_direction)
            for slot in self.action_attribute_slots
            if slot.requested_direction in {-1, 1}
        }
        return next(iter(directions)) if len(directions) == 1 else None

    @property
    def surface_action_direction(self) -> int | None:
        """Map one typed surface action through inverse-noun metadata."""

        surface_directions = {
            _SURFACE_ACTION_DIRECTIONS[str(slot.value)]
            for slot in self.surface_action_slots
            if str(slot.value) in _SURFACE_ACTION_DIRECTIONS
        }
        if (
            self.axis_slot.axis_role != "axis"
            or self.direction_multiplier != -1
            or len(self.surface_action_slots) != 1
            or len(surface_directions) != 1
        ):
            return None
        return next(iter(surface_directions)) * self.direction_multiplier


@dataclass(frozen=True, slots=True)
class SemanticScopeResolution:
    """Immutable scope output ready for operation assembly and validation."""

    extraction: SlotExtraction
    clauses: tuple[ClauseScope, ...]
    operation_groups: tuple[OperationSlotGroup, ...]
    region_slot: SemanticSlot | None
    region_slots: tuple[SemanticSlot, ...]
    guards: tuple[ScopeGuard, ...]
    errors: tuple[ScopeError, ...]
    ambiguous_slots: tuple[SemanticSlot, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "clauses",
            "operation_groups",
            "region_slots",
            "guards",
            "errors",
            "ambiguous_slots",
        ):
            object.__setattr__(
                self,
                field_name,
                tuple(getattr(self, field_name)),
            )
        if self.region_slot is not None and self.region_slot not in self.region_slots:
            raise ValueError("region_slot must be one of region_slots")

    @property
    def groups(self) -> tuple[OperationSlotGroup, ...]:
        return self.operation_groups

    @property
    def region_id(self) -> str | None:
        if self.region_slot is None:
            return None
        if self.region_slot.namespace == "region":
            return str(self.region_slot.value)
        if self.region_slot.slot in {"region_context", "region_object"}:
            return str(self.region_slot.value)
        return str(self.region_slot.concept_id)

    @property
    def has_authoritative_guards(self) -> bool:
        return bool(self.guards)

    @property
    def is_unambiguous(self) -> bool:
        return not self.errors and not self.ambiguous_slots


@dataclass(frozen=True, slots=True)
class _Boundary:
    normalized_start: int
    normalized_end: int
    kind: BoundaryKind
    slot: SemanticSlot | None = None


@dataclass(slots=True)
class _ClauseDraft:
    normalized_start: int
    normalized_end: int
    before: list[_Boundary]
    after: list[_Boundary]


class SemanticScopeResolver:
    """Resolve clause, region, and modifier scope without assembling edits."""

    def __init__(
        self,
        registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    ) -> None:
        if not isinstance(registry, ParameterRegistry):
            raise TypeError("registry must be a ParameterRegistry")
        self._registry = registry

    @property
    def registry(self) -> ParameterRegistry:
        return self._registry

    def resolve(self, extraction: SlotExtraction) -> SemanticScopeResolution:
        if not isinstance(extraction, SlotExtraction):
            raise TypeError("extraction must be a SlotExtraction")

        clauses = _build_clauses(extraction)
        clauses, consumed_direction_ambiguity_ids = (
            _resolve_structural_direction_ambiguities(
                clauses,
                registry=self._registry,
            )
        )
        clause_by_slot = _map_slots_to_clauses(clauses)
        all_content_slots = tuple(
            slot for clause in clauses for slot in clause.slots
        )
        errors: list[ScopeError] = []

        ambiguous_slots: list[SemanticSlot] = []

        guard_slots = tuple(
            slot for slot in extraction.slots if slot.slot in _GUARD_SLOTS
        )

        axis_slots = [
            slot for slot in all_content_slots if slot.namespace == "axis"
        ]
        explicit_region_slots = [
            slot for slot in all_content_slots if slot.namespace == "region"
        ]
        typed_region_objects = tuple(
            slot
            for slot in all_content_slots
            if slot.slot == "region_object"
        )
        resolved_region_objects, region_object_errors = (
            _resolve_typed_region_objects(
                clauses,
                axis_slots=axis_slots,
                region_object_slots=typed_region_objects,
            )
        )
        explicit_region_slots.extend(resolved_region_objects)
        errors.extend(region_object_errors)
        axis_attribute_regions = _resolve_axis_attribute_region_roles(
            clauses,
            axis_slots=axis_slots,
            explicit_region_slots=explicit_region_slots,
            registry=self._registry,
        )
        axis_attribute_region_ids = {
            id(slot) for slot in axis_attribute_regions
        }
        explicit_region_slots = [
            slot
            for slot in explicit_region_slots
            if id(slot) not in axis_attribute_region_ids
        ]
        quantity_all_slots = _resolve_explicit_all_quantifiers(
            clauses,
            explicit_region_slots=explicit_region_slots,
            axis_slots=axis_slots,
        )
        quantity_all_ids = {id(slot) for slot in quantity_all_slots}
        explicit_region_slots = [
            slot
            for slot in explicit_region_slots
            if id(slot) not in quantity_all_ids
        ]
        contextual_all_slots = [
            slot
            for slot in all_content_slots
            if slot.slot == "region_context" and slot.value == "all"
        ]
        effective_contextual_all = _resolve_contextual_all_scope(
            clauses,
            contextual_all_slots=contextual_all_slots,
            explicit_region_slots=explicit_region_slots,
            axis_slots=axis_slots,
        )
        inferred_regions, conceptual_ambiguities, scope_errors = (
            _resolve_axis_region_scope(
                clauses,
                axis_slots,
                explicit_region_slots,
                self._registry,
            )
        )
        errors.extend(scope_errors)
        errors.extend(
            _multi_or_nested_region_candidate_errors(
                clauses,
                axis_slots=axis_slots,
                explicit_region_slots=explicit_region_slots,
                registry=self._registry,
            )
        )
        ambiguous_slots.extend(conceptual_ambiguities)

        inferred_ids = {id(slot) for slot in inferred_regions}
        effective_axis_slots = [
            slot for slot in axis_slots if id(slot) not in inferred_ids
        ]
        region_slots = tuple(
            (
                *explicit_region_slots,
                *effective_contextual_all,
                *inferred_regions,
            )
        )
        distinct_regions = {
            _region_id(slot) for slot in region_slots
        }
        region_slot: SemanticSlot | None
        if len(distinct_regions) > 1:
            region_slot = None
            errors.append(
                ScopeError(
                    code="multiple_regions",
                    message=(
                        "A prompt may target only one distinct region "
                        "atomically."
                    ),
                    normalized_start=min(
                        slot.normalized_start for slot in region_slots
                    ),
                    normalized_end=max(
                        slot.normalized_end for slot in region_slots
                    ),
                    slots=region_slots,
                )
            )
        elif region_slots:
            region_slot = region_slots[0]
        else:
            region_slot = None

        groups, group_errors = _build_operation_groups(
            clauses=clauses,
            axis_slots=effective_axis_slots,
            region_slot=region_slot,
            conceptual_ambiguities=tuple(conceptual_ambiguities),
        )
        errors.extend(group_errors)
        groups = _bind_axis_attribute_region_support(
            groups,
            axis_attribute_regions=axis_attribute_regions,
            registry=self._registry,
        )
        groups = _localize_same_axis_modifiers(groups)
        groups = _bind_clause_local_modifiers(groups, clauses)
        groups = _bind_leading_axis_observations(
            groups,
            clauses,
            registry=self._registry,
        )
        groups = _bind_local_anaphora(groups, clauses)
        groups = _bind_local_surface_actions(groups, clauses)
        groups = _bind_local_generic_actions(groups, clauses)
        groups = _bind_spatial_relations(
            groups,
            clauses,
            explicit_region_slots=tuple(explicit_region_slots),
        )
        groups = _bind_cross_clause_modifiers(groups, clauses)
        groups = _bind_anaphoric_observation_remedies(groups, clauses)
        groups = _normalize_composed_observation_modifiers(
            groups,
            registry=self._registry,
        )
        groups = _normalize_observed_magnitude_strength(groups)
        groups = _normalize_adjacent_strength_intensifiers(groups)
        groups = _normalize_generic_strength_modifiers(groups)
        groups = _normalize_relative_strength_markers(groups)
        groups = _bind_axis_continuation_strength(groups)
        groups, attribute_errors = _bind_observation_attributes(
            groups,
            registry=self._registry,
        )
        errors.extend(attribute_errors)
        groups = _bind_cross_clause_observation_context(groups, clauses)
        groups, action_attribute_errors = _bind_cross_axis_actions(
            groups,
            registry=self._registry,
        )
        errors.extend(action_attribute_errors)
        groups = _bind_cross_axis_descriptor_support(groups)
        groups = _bind_local_effect_references(groups, clauses)
        groups = _bind_mechanism_scope(groups, clauses)
        groups, effect_binding_errors = _bind_axis_effect_controls(
            groups,
            registry=self._registry,
        )
        errors.extend(effect_binding_errors)
        effect_state_slots = (
            *extraction.ambiguous_slots,
            *(
                slot
                for slot in extraction.slots
                if (
                    not slot.is_ambiguous
                    and slot.namespace == "effect"
                    and slot.slot == "effect_state"
                )
            ),
        )
        (
            groups,
            local_effect_ambiguity_ids,
        ) = _bind_local_axis_effect_states(
            groups,
            clauses,
            effect_state_slots,
            registry=self._registry,
        )
        (
            groups,
            consumed_effect_ambiguity_ids,
            effect_state_errors,
        ) = _bind_ambiguous_effect_states(
            groups,
            clauses,
            effect_state_slots,
            registry=self._registry,
        )
        consumed_effect_ambiguity_ids.update(
            local_effect_ambiguity_ids
        )
        errors.extend(effect_state_errors)
        (
            groups,
            negated_removal_guard_ids,
        ) = _bind_negated_removal_amounts(
            groups,
            clauses,
            guard_slots,
        )
        groups, upper_bound_guard_ids = _bind_upper_bound_negations(
            groups,
            clauses,
            guard_slots,
        )
        (
            groups,
            sufficiency_guard_ids,
            sufficiency_errors,
        ) = _bind_discontinuous_sufficiency_negations(
            groups,
            clauses,
            guard_slots,
        )
        errors.extend(sufficiency_errors)
        groups, consumed_guard_ids = _bind_corrective_negations(
            groups,
            clauses,
            tuple(
                slot
                for slot in guard_slots
                if (
                    id(slot) not in sufficiency_guard_ids
                    and id(slot) not in upper_bound_guard_ids
                )
            ),
        )
        consumed_guard_ids.update(sufficiency_guard_ids)
        consumed_guard_ids.update(upper_bound_guard_ids)
        consumed_guard_ids.update(negated_removal_guard_ids)
        groups = _bind_standalone_negated_comparatives(
            groups,
            clauses,
        )
        groups = _bind_negated_comparative_remedies(groups, clauses)
        groups = _normalize_reinforcing_comparatives(
            groups,
            registry=self._registry,
        )
        groups = _bind_completed_event_context(
            groups,
            clauses,
        )
        (
            groups,
            post_event_still_guard_ids,
        ) = _bind_post_event_still_observations(
            groups,
            clauses,
            guard_slots,
        )
        consumed_guard_ids.update(post_event_still_guard_ids)
        groups, fusion_errors = _fuse_compatible_same_axis_groups(groups)
        errors.extend(fusion_errors)
        (
            groups,
            persistent_still_guard_ids,
        ) = _bind_persistent_still_observation_guards(
            groups,
            clauses,
            guard_slots,
            extraction=extraction,
        )
        consumed_guard_ids.update(persistent_still_guard_ids)
        groups, preservation_guard_ids = _bind_preservation_commands(
            groups,
            clauses,
            guard_slots,
            registry=self._registry,
        )
        consumed_guard_ids.update(preservation_guard_ids)
        groups, typed_context_errors = _bind_typed_context_support(
            groups,
            clauses,
            grounded_region_slots=region_slots,
            quantity_all_slots=quantity_all_slots,
        )
        errors.extend(typed_context_errors)
        groups = _bind_persistent_observation_strength(
            groups,
            registry=self._registry,
        )
        errors.extend(
            _axis_region_observation_errors(
                groups,
                region_slots=region_slots,
            )
        )
        errors.extend(_typed_function_word_errors(clauses))
        errors.extend(
            _unsupported_numeric_unit_errors(
                clauses,
                groups=groups,
                registry=self._registry,
            )
        )
        groups, clause_force_errors = _bind_clause_force_support(
            clauses,
            groups,
        )
        errors.extend(clause_force_errors)
        errors.extend(_declarative_state_errors(groups))
        errors.extend(_contradictory_descriptor_errors(clauses))
        errors.extend(_borrowed_modifier_clause_errors(groups))
        errors.extend(
            _missing_connector_between_command_heads_errors(groups)
        )
        errors.extend(
            _single_region_attachment_errors(
                groups,
                clauses=clauses,
                region_slots=region_slots,
            )
        )
        errors.extend(
            _local_cross_axis_observation_command_errors(
                groups,
                clauses=clauses,
                region_slots=region_slots,
            )
        )
        errors.extend(
            _orphan_leading_axis_observation_errors(
                extraction,
                clauses=clauses,
                registry=self._registry,
            )
        )
        errors.extend(_connector_completeness_errors(clauses, groups))
        errors.extend(_operation_group_errors(groups))
        errors.extend(_duplicate_axis_errors(groups))
        consumed_ambiguity_ids = {
            *consumed_direction_ambiguity_ids,
            *consumed_effect_ambiguity_ids,
        }
        unresolved_lexical_ambiguities = tuple(
            slot
            for slot in extraction.ambiguous_slots
            if id(slot) not in consumed_ambiguity_ids
        )
        ambiguous_slots.extend(unresolved_lexical_ambiguities)
        for slot in unresolved_lexical_ambiguities:
            errors.append(
                _slot_error(
                    "ambiguous_lexical_slot",
                    "A lexical span has more than one registry meaning.",
                    slot,
                    _clause_for_slot_span(slot, clauses),
                )
            )
        guards = tuple(
            ScopeGuard(
                kind=str(slot.slot),
                concept_id=str(slot.concept_id),
                value=slot.value,
                slot=slot,
                clause_index=(
                    clause_by_slot[id(slot)].index
                    if id(slot) in clause_by_slot
                    else None
                ),
            )
            for slot in guard_slots
            if id(slot) not in consumed_guard_ids
        )

        return SemanticScopeResolution(
            extraction=extraction,
            clauses=clauses,
            operation_groups=groups,
            region_slot=region_slot,
            region_slots=region_slots,
            guards=guards,
            errors=_deduplicate_errors(errors),
            ambiguous_slots=_ordered_unique_slots(ambiguous_slots),
        )


def resolve_semantic_scope(
    extraction: SlotExtraction,
    *,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
) -> SemanticScopeResolution:
    """Convenience entry point for one-off scope resolution."""

    return SemanticScopeResolver(registry).resolve(extraction)


def _build_clauses(extraction: SlotExtraction) -> tuple[ClauseScope, ...]:
    normalized = extraction.normalized
    boundaries = _collect_boundaries(extraction)
    drafts: list[_ClauseDraft] = []
    cursor = 0
    pending: list[_Boundary] = []

    for boundary in boundaries:
        start, end = _trim_span(
            normalized.text,
            cursor,
            boundary.normalized_start,
        )
        if start < end:
            draft = _ClauseDraft(start, end, list(pending), [boundary])
            drafts.append(draft)
            pending.clear()
        elif drafts:
            drafts[-1].after.append(boundary)
        pending.append(boundary)
        cursor = max(cursor, boundary.normalized_end)

    start, end = _trim_span(normalized.text, cursor, len(normalized.text))
    if start < end:
        drafts.append(_ClauseDraft(start, end, list(pending), []))

    if not drafts:
        return ()

    clauses: list[ClauseScope] = []
    coordination_group = 0
    for index, draft in enumerate(drafts):
        boundary_before = _strongest_boundary(draft.before)
        boundary_after = _strongest_boundary(draft.after)
        if index and boundary_before in _CLUSTER_BREAKS:
            coordination_group += 1
        raw_span = normalized.restore_span(
            draft.normalized_start,
            draft.normalized_end,
        )
        content_slots = tuple(
            slot
            for slot in extraction.slots
            if (
                draft.normalized_start <= slot.normalized_start
                and slot.normalized_end <= draft.normalized_end
                and slot.slot != "conjunction"
                and not (
                    slot.slot == "guard"
                    and slot.value == "or"
                )
            )
        )
        clauses.append(
            ClauseScope(
                index=index,
                coordination_group=coordination_group,
                normalized_start=draft.normalized_start,
                normalized_end=draft.normalized_end,
                raw_start=raw_span.start,
                raw_end=raw_span.end,
                raw_text=normalized.raw_text[raw_span.start : raw_span.end],
                slots=content_slots,
                connector_before=tuple(
                    item.slot for item in draft.before if item.slot is not None
                ),
                connector_after=tuple(
                    item.slot for item in draft.after if item.slot is not None
                ),
                boundary_before=boundary_before,
                boundary_after=boundary_after,
            )
        )
    return tuple(clauses)


def _resolve_structural_direction_ambiguities(
    clauses: tuple[ClauseScope, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[tuple[ClauseScope, ...], set[int]]:
    """Resolve a polysemous action only when syntax names one safe target.

    Some surface words can be both a generic direction and an observable
    effect state.  The extractor correctly preserves that ambiguity.  This
    pass selects the direction interpretation only when a local axis noun (or
    one immediately preceding axis referenced by typed anaphora) is unique and
    that axis does not itself declare the competing effect dimension.
    """

    replacements: dict[int, SemanticSlot] = {}
    consumed: set[int] = set()
    for clause in clauses:
        for slot in clause.slots:
            if not slot.is_ambiguous:
                continue
            direction_interpretations = tuple(
                item
                for item in slot.interpretations
                if (
                    item.namespace == "shared"
                    and item.slot == "direction"
                    and item.value in {-1, 1}
                )
            )
            effect_interpretations = tuple(
                item
                for item in slot.interpretations
                if (
                    item.namespace == "effect"
                    and item.slot == "effect_state"
                    and item.requested_direction in {-1, 1}
                )
            )
            if (
                len(direction_interpretations) != 1
                or not effect_interpretations
                or len(slot.interpretations)
                != len(direction_interpretations)
                + len(effect_interpretations)
            ):
                continue
            if any(
                item.slot in {"observation_modifier", "state_link"}
                for item in clause.slots
                if item is not slot
            ):
                # In an observed state such as ``too open``, the effect
                # interpretation remains authoritative until a compatible
                # corrective control is proved below.
                continue

            local_axes = tuple(
                item
                for item in clause.slots
                if (
                    item.namespace == "axis"
                    and _only_local_support_between(slot, item, clause)
                )
            )
            target: SemanticSlot | None = (
                local_axes[0] if len(local_axes) == 1 else None
            )
            if target is None:
                anaphora = tuple(
                    item
                    for item in clause.slots
                    if item.slot == "anaphora"
                )
                if (
                    len(anaphora) == 1
                    and clause.index > 0
                    and clause.boundary_before
                    not in {"contrastive", "disjunction"}
                ):
                    previous = clauses[clause.index - 1]
                    previous_axes = tuple(
                        item
                        for item in previous.slots
                        if item.namespace == "axis"
                    )
                    if len(previous_axes) == 1:
                        target = previous_axes[0]
            if target is None:
                continue

            competing_effect_ids = {
                item.concept_id for item in effect_interpretations
            }
            if any(
                registry.get_axis_effect_binding(
                    str(target.value),
                    effect_id,
                )
                is not None
                for effect_id in competing_effect_ids
            ):
                continue

            selected = direction_interpretations[0]
            replacements[id(slot)] = _materialize_interpretation(
                slot,
                selected,
                evidence_slot="resolved_direction",
            )
            consumed.add(id(slot))

    if not replacements:
        return clauses, consumed
    return (
        tuple(
            replace(
                clause,
                slots=tuple(
                    replacements.get(id(slot), slot)
                    for slot in clause.slots
                ),
            )
            for clause in clauses
        ),
        consumed,
    )


def _materialize_interpretation(
    slot: SemanticSlot,
    interpretation: SlotInterpretation,
    *,
    evidence_slot: str,
) -> SemanticSlot:
    """Materialize one registry interpretation with auditable provenance."""

    return SemanticSlot(
        normalized_start=slot.normalized_start,
        normalized_end=slot.normalized_end,
        normalized_text=slot.normalized_text,
        evidence=replace(
            slot.evidence,
            slot=evidence_slot,
            concept_id=interpretation.concept_id,
            language=interpretation.language,
        ),
        interpretations=(interpretation,),
    )


def _derived_shared_slot(
    source: SemanticSlot,
    *,
    slot: str,
    concept_id: str,
    value: str | int | float | bool,
    evidence_slot: str,
) -> SemanticSlot:
    """Derive a typed structural meaning while preserving source provenance."""

    interpretation = source.interpretation
    language = (
        interpretation.language
        if interpretation is not None
        else source.evidence.language
    )
    priority = interpretation.priority if interpretation is not None else 0
    return SemanticSlot(
        normalized_start=source.normalized_start,
        normalized_end=source.normalized_end,
        normalized_text=source.normalized_text,
        evidence=replace(
            source.evidence,
            slot=evidence_slot,
            concept_id=concept_id,
            language=language,
        ),
        interpretations=(
            SlotInterpretation(
                namespace="shared",
                slot=slot,
                concept_id=concept_id,
                value=value,
                language=language,
                priority=priority,
            ),
        ),
    )


def _clause_for_slot_span(
    slot: SemanticSlot,
    clauses: tuple[ClauseScope, ...],
) -> ClauseScope | None:
    return next(
        (
            clause
            for clause in clauses
            if (
                clause.normalized_start <= slot.normalized_start
                and slot.normalized_end <= clause.normalized_end
            )
        ),
        None,
    )


def _map_slots_to_clauses(
    clauses: tuple[ClauseScope, ...],
) -> dict[int, ClauseScope]:
    mapped = {
        id(slot): clause
        for clause in clauses
        for slot in clause.slots
    }
    # A connector semantically introduces the following clause.  A trailing
    # connector with no following content falls back to the preceding clause.
    for clause in clauses:
        for slot in clause.connector_before:
            mapped.setdefault(id(slot), clause)
    for clause in clauses:
        for slot in clause.connector_after:
            mapped.setdefault(id(slot), clause)
    return mapped


def _collect_boundaries(extraction: SlotExtraction) -> tuple[_Boundary, ...]:
    semantic: list[_Boundary] = []
    for slot in extraction.slots:
        if slot.slot == "conjunction":
            kind: BoundaryKind = (
                "contrastive"
                if slot.value == "but"
                else "conjunction"
            )
            semantic.append(
                _Boundary(
                    slot.normalized_start,
                    slot.normalized_end,
                    kind,
                    slot,
                )
            )
        elif (
            slot.slot == "guard"
            and slot.value == "or"
            and not _guard_is_typed_persistent_observation(
                extraction,
                slot,
            )
        ):
            semantic.append(
                _Boundary(
                    slot.normalized_start,
                    slot.normalized_end,
                    "disjunction",
                    slot,
                )
            )

    semantic_ranges = tuple(
        (item.normalized_start, item.normalized_end) for item in semantic
    )
    punctuation = [
        _Boundary(token.start, token.end, _PUNCTUATION_BOUNDARIES[token.text])
        for token in extraction.normalized.tokens
        if (
            token.kind == "punctuation"
            and token.text in _PUNCTUATION_BOUNDARIES
            and not any(
                start <= token.start and token.end <= end
                for start, end in semantic_ranges
            )
            and not any(
                slot.normalized_start <= token.start
                and token.end <= slot.normalized_end
                for slot in extraction.slots
            )
        )
    ]
    # Whitespace normalization intentionally collapses every run to one space,
    # but a user-entered line break still carries the same coordination intent
    # as a comma-separated list. Recover that structure from provenance instead
    # of teaching every compound prompt an order-specific phrase.
    line_breaks = [
        _Boundary(index, index + 1, "comma")
        for index, char in enumerate(extraction.normalized.text)
        if (
            char == " "
            and any(
                raw_char in "\r\n"
                for raw_char in extraction.normalized.raw_text[
                    extraction.normalized.normalized_to_raw[index].start :
                    extraction.normalized.normalized_to_raw[index].end
                ]
            )
            and not any(
                start <= index and index + 1 <= end
                for start, end in semantic_ranges
            )
            and not any(
                slot.normalized_start <= index
                and index + 1 <= slot.normalized_end
                for slot in extraction.slots
            )
        )
    ]
    ordered = sorted(
        (*semantic, *punctuation, *line_breaks),
        key=lambda item: (
            item.normalized_start,
            item.normalized_end,
            0 if item.slot is not None else 1,
        ),
    )
    disjoint: list[_Boundary] = []
    for boundary in ordered:
        if disjoint and boundary.normalized_start < disjoint[-1].normalized_end:
            continue
        disjoint.append(boundary)
    return tuple(disjoint)


def _guard_is_typed_persistent_observation(
    extraction: SlotExtraction,
    guard: SemanticSlot,
) -> bool:
    """Distinguish a single persisted observation from an alternative.

    The dual-role Chinese marker represented by ``disjunction_or_still`` is
    temporal only when the complete typed prompt contains one observation
    tail and no competing branch.  A preceding region/context is allowed, as
    is one redundant noun naming the same axis.  Commands, two observations,
    different axes, or another disjunction remain authoritative guards.
    """

    if (
        guard.slot != "guard"
        or guard.concept_id != "disjunction_or_still"
        or guard.value != "or"
    ):
        return False
    disjunctions = tuple(
        slot
        for slot in extraction.slots
        if slot.slot == "guard" and slot.value == "or"
    )
    if len(disjunctions) != 1 or disjunctions[0] is not guard:
        return False

    before = tuple(
        slot
        for slot in extraction.slots
        if slot is not guard and slot.normalized_end <= guard.normalized_start
    )
    after = tuple(
        slot
        for slot in extraction.slots
        if slot is not guard and guard.normalized_end <= slot.normalized_start
    )
    before_axes = tuple(
        slot for slot in before if slot.namespace == "axis"
    )
    after_axes = tuple(
        slot for slot in after if slot.namespace == "axis"
    )
    if len(after_axes) != 1:
        return False
    observation = after_axes[0]
    if (
        observation.match_kind not in {"descriptor", "observation"}
        or observation.requested_direction not in {-1, 1}
    ):
        return False
    if not (
        observation.match_kind == "observation"
        or any(
            slot.slot == "observation_modifier"
            and str(slot.value) in _OBSERVATION_VALUES
            for slot in after
        )
    ):
        return False

    if len(before_axes) > 1:
        return False
    if before_axes:
        subject = before_axes[0]
        if (
            subject.axis_role != "axis"
            or subject.match_kind != "axis"
            or str(subject.value) != str(observation.value)
            or subject.requested_direction is not None
        ):
            return False

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
    return not any(
        slot.is_ambiguous or slot.slot in forbidden
        for slot in (*before, *after)
    )


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _strongest_boundary(
    boundaries: Iterable[_Boundary],
) -> BoundaryKind | None:
    materialized = tuple(boundaries)
    if not materialized:
        return None
    return max(materialized, key=lambda item: _BOUNDARY_PRIORITY[item.kind]).kind


def _resolve_typed_region_objects(
    clauses: tuple[ClauseScope, ...],
    *,
    axis_slots: list[SemanticSlot],
    region_object_slots: tuple[SemanticSlot, ...],
) -> tuple[tuple[SemanticSlot, ...], tuple[ScopeError, ...]]:
    """Resolve a typed participant only in a proven direct-object position."""

    if not region_object_slots:
        return (), ()

    clause_by_slot = _map_slots_to_clauses(clauses)
    resolved: list[SemanticSlot] = []
    errors: list[ScopeError] = []
    for region_object in region_object_slots:
        clause = clause_by_slot.get(id(region_object))
        candidates = tuple(
            axis
            for axis in axis_slots
            if (
                clause is not None
                and clause_by_slot.get(id(axis)) is clause
                and axis.match_kind == "action"
                and axis.object_binding
                in {"self_or_region", "cross_axis_target"}
                and axis.normalized_end <= region_object.normalized_start
                and _only_binding_material_between(
                    axis,
                    region_object,
                    clause,
                )
                and all(
                    slot is region_object
                    or slot.normalized_end
                    <= region_object.normalized_end
                    or slot.slot
                    in {"strength", "noise", "function_word"}
                    for slot in clause.slots
                )
            )
        )
        if candidates:
            nearest_distance = min(
                _span_distance(candidate, region_object)
                for candidate in candidates
            )
            nearest = tuple(
                candidate
                for candidate in candidates
                if _span_distance(candidate, region_object)
                == nearest_distance
            )
        else:
            nearest = ()
        if len(nearest) == 1:
            resolved.append(region_object)
            continue
        related = _ordered_unique_slots((region_object, *nearest))
        errors.append(
            ScopeError(
                code="unresolved_region_object",
                message=(
                    "A typed participant object needs one adjacent edit "
                    "action that declares region-object binding."
                ),
                clause_index=None if clause is None else clause.index,
                normalized_start=min(
                    slot.normalized_start for slot in related
                ),
                normalized_end=max(
                    slot.normalized_end for slot in related
                ),
                slots=related,
            )
        )
    return _ordered_unique_slots(resolved), _deduplicate_errors(errors)


def _resolve_axis_attribute_region_roles(
    clauses: tuple[ClauseScope, ...],
    *,
    axis_slots: list[SemanticSlot],
    explicit_region_slots: list[SemanticSlot],
    registry: ParameterRegistry,
) -> tuple[SemanticSlot, ...]:
    """Demote a region noun only when typed event structure proves an attribute.

    Some visual-part nouns can name either a mask or the thing whose state an
    axis changes.  The registry declares which axes own that secondary role.
    A bare noun is treated as attribute support only inside the narrow
    ``completed action + after + noun + continuation + same-axis state``
    structure.  Locative, region-container, and contextual-region evidence
    keeps the noun as a real image region.
    """

    if not explicit_region_slots:
        return ()
    clause_by_slot = _map_slots_to_clauses(clauses)
    resolved: list[SemanticSlot] = []
    for region_slot in explicit_region_slots:
        region_id = _region_id(region_slot)
        definition = registry.regions.get(region_id)
        if definition is None or not definition.attribute_axis_ids:
            continue
        clause = clause_by_slot.get(id(region_slot))
        if clause is None:
            continue
        coordination_group = clause.coordination_group
        local_slots = tuple(
            slot
            for candidate in clauses
            if candidate.coordination_group == coordination_group
            for slot in candidate.slots
        )
        if any(
            slot is not region_slot
            and slot.slot
            in {"scope", "region_context", "region_support"}
            for slot in local_slots
        ):
            continue
        prior_actions = tuple(
            axis
            for axis in axis_slots
            if (
                str(axis.value) in definition.attribute_axis_ids
                and axis.match_kind == "action"
                and axis.normalized_end <= region_slot.normalized_start
                and clause_by_slot.get(id(axis)) is not None
                and clause_by_slot[id(axis)].coordination_group
                == coordination_group
                and any(
                    aspect.slot == "clause_aspect"
                    and aspect.value == "after"
                    and axis.normalized_end <= aspect.normalized_start
                    and aspect.normalized_end
                    <= region_slot.normalized_start
                    for aspect in local_slots
                )
            )
        )
        later_states = tuple(
            axis
            for axis in axis_slots
            if (
                str(axis.value) in definition.attribute_axis_ids
                and axis.match_kind in {"action", "descriptor"}
                and axis.requested_direction in {-1, 1}
                and axis.normalized_start >= region_slot.normalized_end
                and clause_by_slot.get(id(axis)) is not None
                and clause_by_slot[id(axis)].coordination_group
                == coordination_group
                and any(
                    relation.slot == "relation"
                    and relation.value == "continue"
                    and region_slot.normalized_end
                    <= relation.normalized_start
                    and relation.normalized_end <= axis.normalized_start
                    for relation in local_slots
                )
            )
        )
        proven_pairs = tuple(
            (prior, later)
            for prior in prior_actions
            for later in later_states
            if str(prior.value) == str(later.value)
        )
        later_observations = tuple(
            slot
            for slot in local_slots
            if (
                slot.slot == "observation_modifier"
                and region_slot.normalized_end <= slot.normalized_start
            )
        )
        direct_axis_heads = tuple(
            axis
            for axis in axis_slots
            if (
                str(axis.value) in definition.attribute_axis_ids
                and axis.match_kind in {"action", "axis"}
                and axis.normalized_end == region_slot.normalized_start
                and clause_by_slot.get(id(axis)) is clause
            )
        )
        direct_attribute_observation = bool(
            len(direct_axis_heads) == 1
            and later_observations
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
        completed_observation = bool(
            len(prior_actions) == 1
            and not later_states
            and later_observations
        )
        if (
            len(proven_pairs) == 1
            or completed_observation
            or direct_attribute_observation
        ):
            resolved.append(region_slot)
    return _ordered_unique_slots(resolved)


def _bind_axis_attribute_region_support(
    groups: tuple[OperationSlotGroup, ...],
    *,
    axis_attribute_regions: tuple[SemanticSlot, ...],
    registry: ParameterRegistry,
) -> tuple[OperationSlotGroup, ...]:
    """Attach each proven attribute noun to its following same-axis state."""

    if not axis_attribute_regions:
        return groups
    result = list(groups)
    for region_slot in axis_attribute_regions:
        definition = registry.regions.get(_region_id(region_slot))
        if definition is None:
            continue
        candidates = tuple(
            index
            for index, group in enumerate(result)
            if (
                group.axis_id in definition.attribute_axis_ids
                and (
                    group.axis_slot.normalized_start
                    >= region_slot.normalized_end
                    or group.axis_slot.normalized_end
                    <= region_slot.normalized_start
                )
            )
        )
        if not candidates:
            continue
        following_candidates = tuple(
            index
            for index in candidates
            if result[index].axis_slot.normalized_start
            >= region_slot.normalized_end
        )
        eligible_candidates = following_candidates or candidates
        distances = {
            index: _span_distance(
                result[index].axis_slot,
                region_slot,
            )
            for index in eligible_candidates
        }
        nearest_distance = min(distances.values())
        nearest = tuple(
            index
            for index in eligible_candidates
            if distances[index] == nearest_distance
        )
        if len(nearest) != 1:
            continue
        index = nearest[0]
        target = result[index]
        result[index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                region_slot.normalized_start,
            ),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, region_slot)
            ),
        )
    return tuple(result)


def _resolve_explicit_all_quantifiers(
    clauses: tuple[ClauseScope, ...],
    *,
    explicit_region_slots: list[SemanticSlot],
    axis_slots: list[SemanticSlot],
) -> tuple[SemanticSlot, ...]:
    """Treat local ``all`` as quantity only when syntax proves a local target.

    A standalone/global ``all`` remains explicit whole-image evidence so a
    parent-local edit cannot leak into it.  When the same clause also contains
    one non-all region and ``all`` directly scopes an axis or that region,
    ``all`` is a distributive quantity marker instead of a competing target.
    """

    all_slots = tuple(
        slot
        for slot in explicit_region_slots
        if _region_id(slot) == "all"
    )
    local_regions = tuple(
        slot
        for slot in explicit_region_slots
        if _region_id(slot) != "all"
    )
    if not all_slots or not local_regions:
        return ()
    clause_by_slot = {
        id(slot): clause for clause in clauses for slot in clause.slots
    }
    result: list[SemanticSlot] = []
    for slot in all_slots:
        clause = clause_by_slot.get(id(slot))
        if clause is None:
            continue
        local_targets = tuple(
            target
            for target in (*axis_slots, *local_regions)
            if (
                clause_by_slot.get(id(target)) is clause
                and slot.normalized_end <= target.normalized_start
                and _only_local_support_between(slot, target, clause)
            )
        )
        if len(local_targets) == 1:
            result.append(slot)
    return _ordered_unique_slots(result)


def _resolve_contextual_all_scope(
    clauses: tuple[ClauseScope, ...],
    *,
    contextual_all_slots: list[SemanticSlot],
    explicit_region_slots: list[SemanticSlot],
    axis_slots: list[SemanticSlot],
) -> tuple[SemanticSlot, ...]:
    """Promote image/photo nouns to global scope only with local syntax.

    ``image`` is often harmless container text in a local request
    (``darken the sky in the image``), but it is an actual all-image target in
    ``darken the image``.  The registry marks the lexical concept once; this
    resolver decides its role from clause ownership instead of enumerating
    sentence templates.

    With an explicit local region elsewhere, a contextual-all noun is promoted
    when its own clause proves that noun is the edit target.  Coordination
    groups alone are insufficient: a conjunction may join a local observation
    to a second, explicit whole-image observation.  Exact clause ownership and
    typed locative scope keep ordinary local wrappers local.
    """

    if not contextual_all_slots:
        return ()
    clause_by_slot = {
        id(slot): clause for clause in clauses for slot in clause.slots
    }
    local_region_clauses = {
        clause_by_slot[id(slot)].coordination_group
        for slot in explicit_region_slots
        if id(slot) in clause_by_slot and str(slot.value) != "all"
    }
    promoted: list[SemanticSlot] = []
    for context_slot in contextual_all_slots:
        clause = clause_by_slot.get(id(context_slot))
        if clause is None:
            continue
        same_clause_local_region = any(
            clause_by_slot.get(id(region_slot)) is clause
            for region_slot in explicit_region_slots
            if str(region_slot.value) != "all"
        )
        if same_clause_local_region:
            continue
        if not local_region_clauses:
            promoted.append(context_slot)
            continue
        has_local_axis = any(
            clause_by_slot.get(id(axis_slot)) is clause
            for axis_slot in axis_slots
        )
        if (
            has_local_axis
            and (
                clause.coordination_group not in local_region_clauses
                or _contextual_all_is_explicit_clause_target(
                    context_slot,
                    clause=clause,
                    axis_slots=axis_slots,
                )
            )
        ):
            promoted.append(context_slot)
    return _ordered_unique_slots(promoted)


def _contextual_all_is_explicit_clause_target(
    context_slot: SemanticSlot,
    *,
    clause: ClauseScope,
    axis_slots: list[SemanticSlot],
) -> bool:
    """Prove that one contextual-all noun is a target, not a wrapper.

    The proof is language- and axis-neutral.  It uses only exact slot spans,
    clause ownership, lexical match roles, and typed locative scope.  A noun
    governed by a region-scope marker (for example a container phrase) cannot
    become an independent whole-image target.
    """

    if not (
        clause.normalized_start
        <= context_slot.normalized_start
        < context_slot.normalized_end
        <= clause.normalized_end
    ):
        return False
    local_axes = tuple(
        slot
        for slot in axis_slots
        if (
            clause.normalized_start
            <= slot.normalized_start
            < slot.normalized_end
            <= clause.normalized_end
        )
    )
    if not local_axes:
        return False
    if any(
        slot.slot == "scope"
        and slot.value == "region"
        and slot.normalized_end <= context_slot.normalized_start
        for slot in clause.slots
    ):
        return False

    following_axes = tuple(
        slot
        for slot in local_axes
        if context_slot.normalized_end <= slot.normalized_start
    )
    if following_axes:
        prior_targets = tuple(
            slot
            for slot in clause.slots
            if (
                slot is not context_slot
                and slot.normalized_end <= context_slot.normalized_start
                and (
                    slot.namespace in {"axis", "region"}
                    or slot.slot
                    in {"region_context", "region_object", "region_support"}
                )
            )
        )
        return not prior_targets

    preceding_actions = tuple(
        slot
        for slot in local_axes
        if (
            slot.normalized_end <= context_slot.normalized_start
            and slot.match_kind == "action"
        )
    )
    return len(preceding_actions) == 1 and len(local_axes) == 1


def _resolve_axis_region_scope(
    clauses: tuple[ClauseScope, ...],
    axis_slots: list[SemanticSlot],
    explicit_region_slots: list[SemanticSlot],
    registry: ParameterRegistry,
) -> tuple[
    tuple[SemanticSlot, ...],
    tuple[SemanticSlot, ...],
    tuple[ScopeError, ...],
]:
    clause_by_slot = {
        id(slot): clause for clause in clauses for slot in clause.slots
    }
    region_like_axes = [
        slot
        for slot in axis_slots
        if (
            str(slot.concept_id) in registry.regions
            and slot.match_kind == "axis"
        )
    ]
    scope_slots = [
        slot
        for clause in clauses
        for slot in clause.slots
        if slot.slot == "scope" and slot.value == "region"
    ]
    contextual_region_slots = [
        slot
        for clause in clauses
        for slot in clause.slots
        if slot.slot == "region_context"
    ]
    inferred: list[SemanticSlot] = []
    errors: list[ScopeError] = []

    region_like_targets = (
        *explicit_region_slots,
        *contextual_region_slots,
        *region_like_axes,
    )
    for scope_slot in scope_slots:
        clause = clause_by_slot.get(id(scope_slot))
        if clause is None:
            continue
        adjacent_contextual = [
            candidate
            for candidate in contextual_region_slots
            if (
                clause_by_slot.get(id(candidate)) is clause
                and _span_distance(scope_slot, candidate) == 0
            )
        ]
        same_clause = [
            candidate
            for candidate in region_like_targets
            if clause_by_slot.get(id(candidate)) is clause
        ]
        following_same_clause = [
            candidate
            for candidate in same_clause
            if candidate.normalized_start >= scope_slot.normalized_end
        ]
        preceding_same_clause = [
            candidate
            for candidate in same_clause
            if candidate.normalized_end <= scope_slot.normalized_start
        ]
        # A locative marker governs its following complement before a
        # preceding axis/region homonym.  This resolves suffix forms such as
        # ``... shadows in background`` without naming either concept, while
        # preserving ambiguity when more than one following region remains.
        candidates = (
            adjacent_contextual
            or following_same_clause
            or preceding_same_clause
            or [
            candidate
            for candidate in region_like_targets
            if (
                clause_by_slot.get(id(candidate)) is not None
                and clause_by_slot[id(candidate)].coordination_group
                == clause.coordination_group
            )
            ]
        )
        if not candidates:
            errors.append(
                _slot_error(
                    "dangling_region_scope",
                    "A region scope marker has no grounded region target.",
                    scope_slot,
                    clause,
                )
            )
            continue
        selected = _nearest_slots(scope_slot, candidates)
        selected_regions = {_region_id(slot) for slot in selected}
        if len(selected_regions) > 1:
            errors.append(
                ScopeError(
                    code="ambiguous_region_scope",
                    message=(
                        "A region scope marker is equally close to more than "
                        "one distinct region candidate."
                    ),
                    clause_index=clause.index,
                    normalized_start=min(
                        [scope_slot.normalized_start]
                        + [slot.normalized_start for slot in selected]
                    ),
                    normalized_end=max(
                        [scope_slot.normalized_end]
                        + [slot.normalized_end for slot in selected]
                    ),
                    slots=(scope_slot, *selected),
                )
            )
            continue
        target = selected[0]
        if target in region_like_axes and _has_other_axis(
            target,
            axis_slots,
            clause_by_slot,
        ):
            inferred.append(target)

    inferred = list(_ordered_unique_slots(inferred))
    inferred_ids = {id(slot) for slot in inferred}
    explicit_ids = {_region_id(slot) for slot in explicit_region_slots}
    conceptual_ambiguities: list[SemanticSlot] = []
    for candidate in region_like_axes:
        if id(candidate) in inferred_ids:
            continue
        if explicit_ids:
            continue
        if _has_other_axis(candidate, axis_slots, clause_by_slot):
            if _is_coordinated_axis_reading(
                candidate,
                axis_slots,
                clauses,
                clause_by_slot,
            ):
                continue
            inferred.append(candidate)
            inferred_ids.add(id(candidate))
            continue

    return (
        _ordered_unique_slots(inferred),
        _ordered_unique_slots(conceptual_ambiguities),
        _deduplicate_errors(errors),
    )


def _has_other_axis(
    candidate: SemanticSlot,
    axis_slots: Iterable[SemanticSlot],
    clause_by_slot: dict[int, ClauseScope],
) -> bool:
    clause = clause_by_slot.get(id(candidate))
    if clause is None:
        return False
    return any(
        other is not candidate
        and other.concept_id != candidate.concept_id
        and clause_by_slot.get(id(other)) is not None
        and clause_by_slot[id(other)].coordination_group
        == clause.coordination_group
        for other in axis_slots
    )


def _is_coordinated_axis_reading(
    candidate: SemanticSlot,
    axis_slots: Iterable[SemanticSlot],
    clauses: tuple[ClauseScope, ...],
    clause_by_slot: dict[int, ClauseScope],
) -> bool:
    """Prefer an axis reading when syntax grounds a coordinated axis list.

    ``highlights`` and ``shadows`` are both public axes and valid region
    targets.  A full-sentence allowlist cannot safely decide their meaning.
    Instead, the resolver uses structural evidence:

    * an explicit conjunction makes the items an axis list;
    * an operation signal before the candidate scopes forward over the list.

    A bare region-like noun followed by a different edited axis, such as
    ``shadows could use more brightness``, remains a region reading.
    """

    clause = clause_by_slot.get(id(candidate))
    if clause is None:
        return False
    peers = tuple(
        other
        for other in axis_slots
        if (
            other is not candidate
            and other.concept_id != candidate.concept_id
            and clause_by_slot.get(id(other)) is not None
            and clause_by_slot[id(other)].coordination_group
            == clause.coordination_group
        )
    )
    if not peers:
        return False

    group_clauses = tuple(
        item
        for item in clauses
        if item.coordination_group == clause.coordination_group
    )
    has_local_observation = any(
        slot.slot in {"observation_modifier", "state_link"}
        for slot in clause.slots
    )
    has_local_attribute = any(
        other.match_kind in {"descriptor", "observation"}
        and clause_by_slot.get(id(other)) is clause
        for other in peers
    )
    if has_local_observation and has_local_attribute:
        local_attribute_ids = {
            str(other.concept_id)
            for other in peers
            if (
                other.match_kind in {"descriptor", "observation"}
                and clause_by_slot.get(id(other)) is clause
            )
        }
        has_later_explicit_remedy = any(
            str(other.concept_id) in local_attribute_ids
            and other.match_kind == "axis"
            and clause_by_slot.get(id(other)) is not None
            and clause_by_slot[id(other)].index > clause.index
            and any(
                slot.slot
                in {
                    "direction",
                    "operation",
                    "numeric",
                    "numeric_relation",
                    "relation",
                }
                for slot in clause_by_slot[id(other)].slots
            )
            for other in peers
        )
        if has_later_explicit_remedy:
            return False
        return True
    if any(
        other.match_kind == "action"
        and other.requested_direction in {-1, 1}
        and clause_by_slot.get(id(other)) is clause
        and other.normalized_end <= candidate.normalized_start
        and _only_binding_material_between(other, candidate, clause)
        for other in peers
    ):
        return True
    if any(
        connector.value == "and"
        for item in group_clauses
        for connector in item.connector_slots
    ):
        return True

    operation_slots = frozenset(
        {
            "direction",
            "operation",
            "numeric",
            "numeric_relation",
            "relation",
        }
    )
    return any(
        slot.normalized_end <= candidate.normalized_start
        and slot.slot in operation_slots
        for item in group_clauses
        for slot in item.slots
    )


def _build_operation_groups(
    *,
    clauses: tuple[ClauseScope, ...],
    axis_slots: list[SemanticSlot],
    region_slot: SemanticSlot | None,
    conceptual_ambiguities: tuple[SemanticSlot, ...],
) -> tuple[tuple[OperationSlotGroup, ...], tuple[ScopeError, ...]]:
    clause_by_slot = {
        id(slot): clause for clause in clauses for slot in clause.slots
    }
    ambiguous_ids = {id(slot) for slot in conceptual_ambiguities}
    groups: list[OperationSlotGroup] = []
    errors: list[ScopeError] = []

    for axis_slot in axis_slots:
        clause = clause_by_slot[id(axis_slot)]
        assigned: dict[str, tuple[SemanticSlot, ...]] = {}
        for modifier_slot in _MODIFIER_SLOTS:
            selected, _selection_error = _select_modifier_slots(
                axis_slot,
                clause,
                clauses,
                modifier_slot,
            )
            assigned[modifier_slot] = selected

        guard_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot in _GUARD_SLOTS
        )
        group_ambiguities = (
            (axis_slot,) if id(axis_slot) in ambiguous_ids else ()
        )
        span_slots = [
            axis_slot,
            *(
                slot
                for selected in assigned.values()
                for slot in selected
            ),
            *guard_slots,
            *group_ambiguities,
        ]
        if (
            region_slot is not None
            and clause.normalized_start
            <= region_slot.normalized_start
            < clause.normalized_end
        ):
            span_slots.append(region_slot)

        group = OperationSlotGroup(
            axis_slot=axis_slot,
            clause_index=clause.index,
            clause=clause,
            normalized_start=min(
                slot.normalized_start for slot in span_slots
            ),
            normalized_end=max(slot.normalized_end for slot in span_slots),
            region_slot=region_slot,
            direction_slots=assigned["direction"],
            strength_slots=assigned["strength"],
            numeric_slots=assigned["numeric"],
            operation_slots=assigned["operation"],
            numeric_relation_slots=assigned["numeric_relation"],
            relation_slots=assigned["relation"],
            observation_modifier_slots=assigned[
                "observation_modifier"
            ],
            state_link_slots=assigned["state_link"],
            guard_slots=guard_slots,
            ambiguous_slots=group_ambiguities,
            attribute_axis_slots=(),
            action_attribute_slots=(),
            surface_action_slots=(),
            supporting_slots=(),
        )
        groups.append(group)

    return tuple(groups), _deduplicate_errors(errors)


def _select_modifier_slots(
    axis_slot: SemanticSlot,
    clause: ClauseScope,
    clauses: tuple[ClauseScope, ...],
    modifier_slot: str,
) -> tuple[tuple[SemanticSlot, ...], ScopeError | None]:
    local = tuple(
        slot for slot in clause.slots if slot.slot == modifier_slot
    )
    if (
        modifier_slot == "direction"
        and not local
        and axis_slot.match_kind == "action"
        and axis_slot.requested_direction in {-1, 1}
    ):
        # A typed action such as ``lower highlights`` already carries its
        # direction.  A direction in a neighbouring coordinated clause may
        # share with a bare axis noun, but must never overwrite that action.
        return (), None
    if modifier_slot in {"observation_modifier", "state_link"}:
        if len(local) <= 1:
            if len(local) == 1:
                same_axis_occurrences = tuple(
                    slot
                    for slot in clause.slots
                    if (
                        not slot.is_ambiguous
                        and slot.namespace == "axis"
                        and str(slot.value) == str(axis_slot.value)
                    )
                )
                if len(same_axis_occurrences) > 1:
                    nearest_axes = _nearest_slots(
                        local[0],
                        same_axis_occurrences,
                    )
                    if axis_slot not in nearest_axes:
                        return (), None
                    if len(nearest_axes) > 1:
                        return (
                            local,
                            ScopeError(
                                code="ambiguous_modifier_scope",
                                message=(
                                    f"The {modifier_slot} modifier is equally "
                                    "close to repeated mentions of one axis."
                                ),
                                clause_index=clause.index,
                                normalized_start=min(
                                    local[0].normalized_start,
                                    *(
                                        slot.normalized_start
                                        for slot in nearest_axes
                                    ),
                                ),
                                normalized_end=max(
                                    local[0].normalized_end,
                                    *(
                                        slot.normalized_end
                                        for slot in nearest_axes
                                    ),
                                ),
                                slots=(*nearest_axes, local[0]),
                            ),
                        )
            return local, None
        selected = _nearest_slots(axis_slot, local)
        semantic_values = {_semantic_value(slot) for slot in selected}
        if len(semantic_values) <= 1:
            return selected, None
        return (
            selected,
            ScopeError(
                code="ambiguous_modifier_scope",
                message=(
                    f"More than one {modifier_slot} modifier is equally "
                    "close to an axis."
                ),
                clause_index=clause.index,
                normalized_start=min(
                    [axis_slot.normalized_start]
                    + [slot.normalized_start for slot in selected]
                ),
                normalized_end=max(
                    [axis_slot.normalized_end]
                    + [slot.normalized_end for slot in selected]
                ),
                slots=(axis_slot, *selected),
            ),
        )
    if len(local) == 1:
        return local, None
    if len(local) > 1:
        if len({_semantic_value(slot) for slot in local}) == 1:
            return local, None
        selected = _nearest_slots(axis_slot, local)
    else:
        coordinated = tuple(
            slot
            for candidate_clause in clauses
            if candidate_clause.coordination_group == clause.coordination_group
            for slot in candidate_clause.slots
            if slot.slot == modifier_slot
        )
        if len(coordinated) <= 1:
            return coordinated, None
        selected = _nearest_slots(axis_slot, coordinated)

    semantic_values = {
        _semantic_value(slot) for slot in selected
    }
    if len(semantic_values) <= 1:
        return selected, None
    return (
        selected,
        ScopeError(
            code="ambiguous_modifier_scope",
            message=(
                f"More than one {modifier_slot} modifier is equally close "
                "to an axis."
            ),
            clause_index=clause.index,
            normalized_start=min(
                [axis_slot.normalized_start]
                + [slot.normalized_start for slot in selected]
            ),
            normalized_end=max(
                [axis_slot.normalized_end]
                + [slot.normalized_end for slot in selected]
            ),
            slots=(axis_slot, *selected),
        ),
    )


def _localize_same_axis_modifiers(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Undo cross-clause leakage from a descriptor into an explicit command.

    Shared modifiers may legitimately flow across coordinated *different*
    axes.  Repeated mentions of one axis are different: a modifier physically
    located beside the later explicit noun must not be attached to an earlier
    descriptive restatement merely because both occurrences share an id.
    """

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        updates: dict[str, tuple[SemanticSlot, ...]] = {}
        for slot_name, field_name in _GROUP_FIELD_BY_SLOT.items():
            del slot_name
            current = tuple(getattr(group, field_name))
            retained = tuple(
                slot
                for slot in current
                if (
                    _slot_is_in_clause(slot, group.clause)
                    or not any(
                        other is not group
                        and _slot_is_in_clause(slot, other.clause)
                        and (
                            (
                                other.axis_id == group.axis_id
                                and (
                                    group.axis_slot.match_kind
                                    in {"descriptor", "observation"}
                                    or other.axis_slot.match_kind
                                    in {"descriptor", "observation"}
                                )
                            )
                            or (
                                group.axis_slot.match_kind
                                in {"descriptor", "observation"}
                                and other.axis_slot.match_kind == "axis"
                                and group.clause_index
                                != other.clause_index
                                and group.clause.boundary_after
                                not in {"conjunction", "contrastive", "disjunction"}
                                and other.clause.boundary_before
                                not in {"conjunction", "contrastive", "disjunction"}
                            )
                        )
                        for other in groups
                    )
                )
            )
            if retained != current:
                updates[field_name] = retained
        materialized.append(replace(group, **updates) if updates else group)
    return tuple(materialized)


def _bind_clause_local_modifiers(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Bind every local modifier when a clause has one unique operation."""

    result = list(groups)
    bound_ids = {
        id(slot)
        for group in result
        for slot in _all_group_slots(group)
    }
    for clause in clauses:
        indexes = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        ]
        if len(indexes) != 1:
            continue
        index = indexes[0]
        group = result[index]
        updates: dict[str, tuple[SemanticSlot, ...]] = {}
        added: list[SemanticSlot] = []
        for slot_name, field_name in _GROUP_FIELD_BY_SLOT.items():
            local = tuple(
                slot
                for slot in clause.slots
                if slot.slot == slot_name and id(slot) not in bound_ids
            )
            if not local:
                continue
            updates[field_name] = _ordered_unique_slots(
                (*getattr(group, field_name), *local)
            )
            added.extend(local)
        if not updates:
            continue
        result[index] = replace(
            group,
            normalized_start=min(
                [group.normalized_start]
                + [slot.normalized_start for slot in added]
            ),
            normalized_end=max(
                [group.normalized_end]
                + [slot.normalized_end for slot in added]
            ),
            **updates,
        )
        bound_ids.update(id(slot) for slot in added)
    return tuple(result)


def _bind_leading_axis_observations(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[OperationSlotGroup, ...]:
    """Bind a registry-declared leading persistent-state observation.

    A conjunction remains a structural boundary by default.  A registry entry
    may opt into this narrower role only when it leads one standalone inverse
    axis noun (for example, the typed equivalent of ``there is still haze``).
    The inverse noun supplies the observable-state polarity; the registry
    supplies the conservative correction strength.
    """

    if len(groups) != 1 or len(clauses) != 1:
        return groups
    first = clauses[0]
    if first.boundary_before != "conjunction":
        return groups
    candidates: list[tuple[SemanticSlot, object]] = []
    for slot in first.connector_before:
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
            candidates.append((slot, definition))
    if (
        len(candidates) != 1
        or len(first.connector_before) != 1
    ):
        return groups

    marker, definition = candidates[0]
    target = groups[0]
    clause_slots = tuple(
        slot for slot in first.slots if not slot.is_ambiguous
    )
    if (
        target.clause_index != first.index
        or target.axis_slot.axis_role != "axis"
        or target.axis_slot.match_kind != "axis"
        or target.axis_slot.requested_direction is not None
        or target.direction_multiplier != -1
        or len(clause_slots) != 1
        or clause_slots[0] is not target.axis_slot
        or marker.normalized_end > target.axis_slot.normalized_start
        or target.region_slot is not None
        or target.direction_slots
        or target.strength_slots
        or target.numeric_slots
        or target.operation_slots
        or target.numeric_relation_slots
        or target.relation_slots
        or target.observation_modifier_slots
        or target.state_link_slots
        or target.guard_slots
        or target.ambiguous_slots
        or target.attribute_axis_slots
        or target.action_attribute_slots
        or target.surface_action_slots
        or target.supporting_slots
        or target.request_force_proven
    ):
        return groups

    resolved_observation = _derived_shared_slot(
        marker,
        slot="observation_modifier",
        concept_id=str(marker.concept_id),
        value="too_much",
        evidence_slot="resolved_observation",
    )
    resolved_strength = _derived_shared_slot(
        marker,
        slot="strength",
        concept_id=str(marker.concept_id),
        value=str(definition.observation_strength),
        evidence_slot="resolved_strength",
    )
    return (
        replace(
            target,
            normalized_start=min(
                target.normalized_start,
                marker.normalized_start,
            ),
            observation_modifier_slots=(resolved_observation,),
            strength_slots=(resolved_strength,),
            supporting_slots=(marker,),
        ),
    )


def _bind_local_anaphora(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Bind a local pronoun only when its single operation is structural."""

    result = list(groups)
    for clause in clauses:
        indexes = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        ]
        if len(indexes) != 1:
            continue
        index = indexes[0]
        target = result[index]
        anaphora = tuple(
            slot for slot in clause.slots if slot.slot == "anaphora"
        )
        if not anaphora:
            continue
        reference_actions = tuple(
            slot
            for slot in clause.slots
            if slot.slot in {"generic_action", "surface_action"}
        )
        prefix_wrapper = bool(
            all(
                slot.normalized_end <= target.axis_slot.normalized_start
                for slot in (*reference_actions, *anaphora)
            )
        )
        postfix_operation_object = bool(
            (
                target.axis_slot.match_kind == "action"
                or (
                    _has_local_operation_signal(target)
                    and not _is_observation_group(target)
                )
                or (
                    target.axis_slot.match_kind == "descriptor"
                    and target.axis_slot.requested_direction in {-1, 1}
                    and not target.state_link_slots
                    and (
                        any(
                            slot.slot
                            in {"request_marker", "request_predicate"}
                            for slot in clause.slots
                        )
                        or (
                            any(
                                slot.slot == "clause_modal"
                                and str(slot.value) in {"can", "could"}
                                for slot in clause.slots
                            )
                            and any(
                                slot.slot == "clause_subject"
                                and str(slot.value) == "second_person"
                                for slot in clause.slots
                            )
                        )
                    )
                )
            )
            and not reference_actions
            and all(
                target.axis_slot.normalized_end <= slot.normalized_start
                for slot in anaphora
            )
        )
        if not (prefix_wrapper or postfix_operation_object):
            continue
        has_local_sufficiency = any(
            slot.concept_id == "sufficiency_enough"
            and slot.normalized_start >= target.axis_slot.normalized_end
            for slot in clause.slots
        )
        if any(
            slot.slot
            in {
                "guard",
                "mechanism",
                "negated_comparative",
                "terminal",
                "effect_reference",
            }
            or (
                slot.slot == "negation"
                and not has_local_sufficiency
            )
            for slot in clause.slots
        ):
            continue
        support = (*reference_actions, *anaphora)
        result[index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                *(slot.normalized_start for slot in support),
            ),
            normalized_end=max(
                target.normalized_end,
                *(slot.normalized_end for slot in support),
            ),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, *support)
            ),
        )
    return tuple(result)


def _bind_local_surface_actions(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Bind a typed surface removal only to one inverse surface noun.

    ``clear haze`` is compositional: ``clear`` removes the named surface
    quality, while the axis alias metadata declares that more haze maps to
    less dehaze.  A normal control noun such as brightness has no inverse
    multiplier, so the same verb remains unbound and fails closed.
    """

    result = list(groups)
    for clause in clauses:
        indexes = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        ]
        if len(indexes) != 1:
            continue
        surface_actions = tuple(
            slot
            for slot in clause.slots
            if slot.slot == "surface_action" and slot.value == "remove"
        )
        if len(surface_actions) != 1:
            continue
        if any(
            slot.slot
            in {
                "anaphora",
                "guard",
                "negation",
                "mechanism",
                "negated_comparative",
                "terminal",
                "effect_reference",
            }
            for slot in clause.slots
        ):
            continue

        index = indexes[0]
        target = result[index]
        if (
            target.axis_slot.axis_role != "axis"
            or target.direction_multiplier != -1
            or target.surface_action_slots
            or target.direction_slots
            or target.numeric_slots
            or target.operation_slots
            or target.numeric_relation_slots
            or target.relation_slots
            or target.observation_modifier_slots
            or target.state_link_slots
            or target.action_attribute_slots
        ):
            continue
        action = surface_actions[0]
        if not _only_local_support_between(
            action,
            target.axis_slot,
            clause,
        ):
            continue
        result[index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                action.normalized_start,
            ),
            normalized_end=max(
                target.normalized_end,
                action.normalized_end,
            ),
            surface_action_slots=(action,),
        )
    return tuple(result)


def _bind_local_generic_actions(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Treat one adjacent generic edit verb as support for a typed command."""

    result = list(groups)
    for clause in clauses:
        indexes = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        ]
        if len(indexes) != 1:
            continue
        generic_actions = tuple(
            slot for slot in clause.slots if slot.slot == "generic_action"
        )
        if not generic_actions or any(
            slot.slot
            in {
                "anaphora",
                "guard",
                "negation",
                "mechanism",
                "negated_comparative",
                "terminal",
                "effect_reference",
            }
            for slot in clause.slots
        ):
            continue
        index = indexes[0]
        target = result[index]
        has_grounded_numeric_operation = bool(
            len(target.numeric_slots) == 1
            and len(target.numeric_relation_slots) <= 1
            and not target.observation_modifier_slots
            and not target.state_link_slots
        )
        return_negative_actions = tuple(
            slot
            for slot in generic_actions
            if slot.value == "return_negative"
        )
        has_directional_return_action = bool(
            len(return_negative_actions) == 1
            and len(return_negative_actions) == len(generic_actions)
            and any(
                slot.slot == "return_relation"
                for slot in clause.slots
            )
            and target.axis_slot.axis_role == "axis"
        )
        if (
            _command_group_direction(target) not in {-1, 1}
            and not has_grounded_numeric_operation
            and not has_directional_return_action
        ):
            continue
        if not all(
            _only_local_support_between(
                action,
                target.axis_slot,
                clause,
                additional_allowed_slots=(
                    frozenset({"return_relation"})
                    if has_directional_return_action
                    else frozenset()
                ),
            )
            for action in generic_actions
        ):
            continue
        result[index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                *(slot.normalized_start for slot in generic_actions),
            ),
            normalized_end=max(
                target.normalized_end,
                *(slot.normalized_end for slot in generic_actions),
            ),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, *generic_actions)
            ),
        )
    return tuple(result)


def _bind_spatial_relations(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    *,
    explicit_region_slots: tuple[SemanticSlot, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Ground a typed spatial relation to one adjacent explicit region."""

    result = list(groups)
    clause_by_slot = _map_slots_to_clauses(clauses)
    for clause in clauses:
        relations = tuple(
            slot for slot in clause.slots if slot.slot == "spatial_relation"
        )
        for relation in relations:
            candidates = tuple(
                region
                for region in explicit_region_slots
                if (
                    clause_by_slot.get(id(region)) is clause
                    and _only_local_support_between(
                        relation,
                        region,
                        clause,
                    )
                )
            )
            if len(candidates) != 1:
                continue
            region = candidates[0]
            region_id = _region_id(region)
            indexes = [
                index
                for index, group in enumerate(result)
                if (
                    group.region_slot is not None
                    and _region_id(group.region_slot) == region_id
                )
            ]
            if not indexes:
                continue
            for index in indexes:
                target = result[index]
                result[index] = replace(
                    target,
                    normalized_start=min(
                        target.normalized_start,
                        relation.normalized_start,
                    ),
                    normalized_end=max(
                        target.normalized_end,
                        relation.normalized_end,
                    ),
                    supporting_slots=_ordered_unique_slots(
                        (*target.supporting_slots, relation)
                    ),
                )
    return tuple(result)


def _only_local_support_between(
    left: SemanticSlot,
    right: SemanticSlot,
    clause: ClauseScope,
    *,
    additional_allowed_slots: frozenset[str] = frozenset(),
) -> bool:
    """Allow only non-semantic wrappers between two local support spans."""

    if left.normalized_end <= right.normalized_start:
        start, end = left.normalized_end, right.normalized_start
    elif right.normalized_end <= left.normalized_start:
        start, end = right.normalized_end, left.normalized_start
    else:
        return True
    allowed_slots = frozenset(
        {
            "noise",
            "function_word",
            "scope",
            "region",
            "region_context",
            "strength",
        }
    ).union(additional_allowed_slots)
    return all(
        slot is left
        or slot is right
        or not (
            start <= slot.normalized_start
            and slot.normalized_end <= end
        )
        or slot.slot in allowed_slots
        for slot in clause.slots
    )


def _bind_cross_clause_modifiers(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Resolve a modifier-only continuation to one immediately prior group.

    This is the deterministic coreference rule for forms such as
    ``underexposed; bring it up``.  It never guesses between two candidate
    operations and never crosses a contrastive or disjunctive boundary.
    """

    result = list(groups)
    for clause in clauses:
        if any(group.clause_index == clause.index for group in result):
            continue
        if clause.boundary_before in {"contrastive", "disjunction"}:
            continue
        modifiers = tuple(
            slot
            for slot in clause.slots
            if slot.slot in _GROUP_FIELD_BY_SLOT
        )
        if not modifiers or not any(
            slot.slot
            in {
                "direction",
                "strength",
                "numeric",
                "operation",
                "numeric_relation",
                "relation",
            }
            for slot in modifiers
        ):
            continue
        if any(
            slot.namespace in {"axis", "region"}
            or slot.slot
            in {
                "guard",
                "negation",
                "mechanism",
                "negated_comparative",
                "terminal",
                "effect_reference",
            }
            for slot in clause.slots
        ):
            continue
        previous = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index - 1
        ]
        if len(previous) != 1:
            continue
        index = previous[0]
        group = result[index]
        observation_remedy = _bind_axis_observation_remedy(
            group,
            clause=clause,
            modifiers=modifiers,
        )
        if observation_remedy is not None:
            result[index] = observation_remedy
            continue
        updates: dict[str, tuple[SemanticSlot, ...]] = {}
        for slot_name, field_name in _GROUP_FIELD_BY_SLOT.items():
            scoped = tuple(
                slot for slot in modifiers if slot.slot == slot_name
            )
            if scoped:
                updates[field_name] = _ordered_unique_slots(
                    (*getattr(group, field_name), *scoped)
                )
        result[index] = replace(
            group,
            normalized_start=min(
                [group.normalized_start]
                + [slot.normalized_start for slot in modifiers]
            ),
            normalized_end=max(
                [group.normalized_end]
                + [slot.normalized_end for slot in modifiers]
            ),
            **updates,
        )
    return tuple(result)


def _bind_axis_observation_remedy(
    group: OperationSlotGroup,
    *,
    clause: ClauseScope,
    modifiers: tuple[SemanticSlot, ...],
) -> OperationSlotGroup | None:
    """Bind one observed axis state to one adjacent corrective direction.

    An axis noun can describe a state before the actual command, for example
    ``saturation is too high, lower it a little``.  The observed ``high`` is
    evidence for why the correction points down; it is not a second command.
    This rule is axis-neutral and relies only on typed observation, direction,
    boundary, and modifier slots.
    """

    if (
        group.axis_slot.axis_role != "axis"
        or group.clause_index + 1 != clause.index
        or clause.boundary_before not in {"comma", "conjunction"}
        or group.clause.boundary_after not in {"comma", "conjunction"}
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.guard_slots
        or group.ambiguous_slots
        or group.attribute_axis_slots
        or group.action_attribute_slots
        or group.surface_action_slots
        or group.supporting_slots
        or any(
            _slot_is_in_clause(slot, group.clause)
            for slot in group.strength_slots
        )
        or not group.observation_modifier_slots
        or any(
            not _slot_is_in_clause(slot, group.clause)
            for slot in group.observation_modifier_slots
        )
    ):
        return None

    observation_values = {
        str(slot.value) for slot in group.observation_modifier_slots
    }
    if (
        len(observation_values) != 1
        or not observation_values.issubset(_OBSERVATION_VALUES)
    ):
        return None

    all_directions = _ordered_unique_slots(
        (
            *group.direction_slots,
            *(
                slot
                for slot in modifiers
                if slot.slot == "direction"
            ),
        )
    )
    local_directions = tuple(
        slot
        for slot in all_directions
        if (
            slot.slot == "direction"
            and slot.value in {-1, 1}
            and slot.concept_id not in _COMPARATIVE_CONCEPTS
            and _slot_is_in_clause(slot, group.clause)
        )
    )
    if len(local_directions) != 1:
        return None

    cross_directions = tuple(
        slot
        for slot in all_directions
        if (
            slot.slot == "direction"
            and slot.value in {-1, 1}
            and slot.concept_id not in _COMPARATIVE_CONCEPTS
            and _slot_is_in_clause(slot, clause)
        )
    )
    if (
        len(cross_directions) != 1
        or len(all_directions) != 2
        or any(
            slot.slot
            in {
                "numeric",
                "operation",
                "numeric_relation",
                "relation",
                "observation_modifier",
                "state_link",
            }
            for slot in modifiers
        )
    ):
        return None

    expected = _observation_correction_direction(group)
    requested = (
        int(cross_directions[0].value) * group.direction_multiplier
    )
    if expected not in {-1, 1} or requested != expected:
        return None

    updates: dict[str, tuple[SemanticSlot, ...]] = {}
    for slot_name, field_name in _GROUP_FIELD_BY_SLOT.items():
        scoped = tuple(
            slot for slot in modifiers if slot.slot == slot_name
        )
        if scoped:
            updates[field_name] = (
                _ordered_unique_slots(cross_directions)
                if slot_name == "direction"
                else _ordered_unique_slots(
                    (*getattr(group, field_name), *scoped)
                )
            )
    updates["direction_slots"] = _ordered_unique_slots(cross_directions)
    return replace(
        group,
        normalized_start=min(
            [group.normalized_start]
            + [slot.normalized_start for slot in modifiers]
        ),
        normalized_end=max(
            [group.normalized_end]
            + [slot.normalized_end for slot in modifiers]
        ),
        supporting_slots=_ordered_unique_slots(
            (*group.supporting_slots, *local_directions)
        ),
        **updates,
    )


def _bind_anaphoric_observation_remedies(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Bind typed anaphora/actions only to one prior observed operation."""

    result = list(groups)
    for clause in clauses:
        reference_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot in {
                "generic_action",
                "surface_action",
                "anaphora",
            }
        )
        if not reference_slots:
            continue
        if clause.boundary_before in {"contrastive", "disjunction"}:
            continue
        if any(
            group.clause_index == clause.index for group in result
        ):
            continue
        if any(
            slot.namespace == "axis"
            or slot.slot
            in {
                "guard",
                "negation",
                "mechanism",
                "negated_comparative",
                "terminal",
                "effect_reference",
            }
            for slot in clause.slots
        ):
            continue
        previous = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index - 1
        ]
        if len(previous) != 1:
            continue
        index = previous[0]
        target = result[index]
        if not _is_observation_group(target):
            continue
        result[index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                *(slot.normalized_start for slot in reference_slots),
            ),
            normalized_end=max(
                target.normalized_end,
                *(slot.normalized_end for slot in reference_slots),
            ),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, *reference_slots)
            ),
        )
    return tuple(result)


def _bind_cross_clause_observation_context(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Attach an axis-free observation to one explicit corrective command."""

    result = list(groups)
    for clause in clauses:
        if any(group.clause_index == clause.index for group in result):
            continue
        if clause.boundary_after in {"contrastive", "disjunction"}:
            continue
        observation_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot
            in {
                "observation_modifier",
                "state_link",
                "generic_action",
                "surface_action",
            }
        )
        modifiers = tuple(
            slot
            for slot in observation_slots
            if slot.slot == "observation_modifier"
        )
        direction_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot == "direction" and slot.value in {-1, 1}
        )
        strength_slots = tuple(
            slot for slot in clause.slots if slot.slot == "strength"
        )
        directional_values = {int(slot.value) for slot in direction_slots}
        directional_description = bool(
            not modifiers
            and len(directional_values) == 1
            and strength_slots
        )
        if not modifiers and not directional_description:
            continue
        if any(
            slot.namespace == "axis"
            or slot.slot
            in {
                "operation",
                "numeric",
                "numeric_relation",
                "relation",
                "guard",
                "negation",
                "mechanism",
                "negated_comparative",
                "terminal",
                "effect_reference",
            }
            or (
                slot.slot == "direction"
                and not directional_description
            )
            for slot in clause.slots
        ):
            continue
        following = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index + 1
        ]
        if len(following) != 1:
            continue
        index = following[0]
        target = result[index]
        correction = (
            next(iter(directional_values))
            if directional_description
            else _axis_free_observation_direction(
                modifiers,
                target,
            )
        )
        if correction is None or correction != _command_group_direction(target):
            continue
        context_slots = _ordered_unique_slots(
            (
                *observation_slots,
                *(direction_slots if directional_description else ()),
            )
        )
        result[index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                *(slot.normalized_start for slot in context_slots),
            ),
            normalized_end=max(
                target.normalized_end,
                *(slot.normalized_end for slot in context_slots),
            ),
            strength_slots=_ordered_unique_slots(
                (
                    *target.strength_slots,
                    *(strength_slots if directional_description else ()),
                )
            ),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, *context_slots)
            ),
        )
    return tuple(result)


def _axis_free_observation_direction(
    modifiers: tuple[SemanticSlot, ...],
    target: OperationSlotGroup,
) -> int | None:
    values = {str(slot.value) for slot in modifiers}
    if "too" in values and len(values) > 1:
        values.remove("too")
    if not values or not values.issubset(_OBSERVATION_VALUES):
        return None
    if len(values) != 1:
        return None
    value = next(iter(values))
    return (
        target.direction_multiplier
        if value == "not_enough"
        else -target.direction_multiplier
    )


def _normalize_composed_observation_modifiers(
    groups: tuple[OperationSlotGroup, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[OperationSlotGroup, ...]:
    """Compose one generic degree cue with one specific observed quality."""

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        values = {
            str(slot.value) for slot in group.observation_modifier_slots
        }
        # A mild degree cue can qualify a following excess observation
        # (for example, ``有點過了`` / ``a little too much``). Keep ``too`` as
        # the observed polarity and turn only the preceding ``mild`` cue into
        # correction strength. Reversed or repeated cues stay ambiguous.
        if values == {"mild", "too"} and not group.strength_slots:
            mild_slots = tuple(
                slot
                for slot in group.observation_modifier_slots
                if str(slot.value) == "mild"
            )
            too_slots = tuple(
                slot
                for slot in group.observation_modifier_slots
                if str(slot.value) == "too"
            )
            if (
                len(mild_slots) == 1
                and len(too_slots) == 1
                and mild_slots[0].normalized_end
                <= too_slots[0].normalized_start
            ):
                definition = registry.shared_concepts.get(
                    str(mild_slots[0].concept_id)
                )
                if (
                    definition is not None
                    and definition.slot == "observation_modifier"
                    and definition.observation_strength is not None
                ):
                    materialized.append(
                        replace(
                            group,
                            observation_modifier_slots=too_slots,
                            strength_slots=(
                                _derived_shared_slot(
                                    mild_slots[0],
                                    slot="strength",
                                    concept_id=str(
                                        mild_slots[0].concept_id
                                    ),
                                    value=str(
                                        definition.observation_strength
                                    ),
                                    evidence_slot="resolved_strength",
                                ),
                            ),
                            supporting_slots=_ordered_unique_slots(
                                (*group.supporting_slots, *mild_slots)
                            ),
                        )
                    )
                    continue
        generic_values = values.intersection({"too", "mild"})
        if not generic_values or len(values) <= 1:
            materialized.append(group)
            continue
        generic = tuple(
            slot
            for slot in group.observation_modifier_slots
            if str(slot.value) in generic_values
        )
        specific = tuple(
            slot
            for slot in group.observation_modifier_slots
            if str(slot.value) not in generic_values
        )
        if (
            len(generic_values) != 1
            or len({str(slot.value) for slot in specific}) != 1
        ):
            materialized.append(group)
            continue
        derived_strength: tuple[SemanticSlot, ...] = ()
        if len(generic) == 1 and not group.strength_slots:
            definition = registry.shared_concepts.get(
                str(generic[0].concept_id)
            )
            if (
                definition is not None
                and definition.slot == "observation_modifier"
                and definition.observation_strength is not None
            ):
                derived_strength = (
                    _derived_shared_slot(
                        generic[0],
                        slot="strength",
                        concept_id=str(generic[0].concept_id),
                        value=str(definition.observation_strength),
                        evidence_slot="resolved_strength",
                    ),
                )
        materialized.append(
            replace(
                group,
                observation_modifier_slots=specific,
                strength_slots=_ordered_unique_slots(
                    (*group.strength_slots, *derived_strength)
                ),
                supporting_slots=_ordered_unique_slots(
                    (*group.supporting_slots, *generic)
                ),
            )
        )
    return tuple(materialized)


def _normalize_observed_magnitude_strength(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Separate observed magnitude from the requested correction strength."""

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        values = {str(slot.value) for slot in group.strength_slots}
        observation_values = {
            str(slot.value)
            for slot in group.observation_modifier_slots
        }
        if (
            values != {"subtle", "strong"}
            or not group.state_link_slots
            or not observation_values.intersection({"too", "too_much"})
            or group.numeric_slots
            or group.operation_slots
        ):
            materialized.append(group)
            continue
        observed_magnitude = tuple(
            slot
            for slot in group.strength_slots
            if str(slot.value) == "strong"
        )
        correction_strength = tuple(
            slot
            for slot in group.strength_slots
            if str(slot.value) == "subtle"
        )
        materialized.append(
            replace(
                group,
                strength_slots=correction_strength,
                supporting_slots=_ordered_unique_slots(
                    (*group.supporting_slots, *observed_magnitude)
                ),
            )
        )
    return tuple(materialized)


def _normalize_adjacent_strength_intensifiers(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Treat an adjacent degree adverb as support for a specific strength.

    Registry strength words remain independent by default.  The only safe
    composition here is one strong degree adverb immediately modifying one
    subtle cue (for example ``really slightly``).  No observation, numeric,
    reset, guard, or relation semantics may compete for that phrase.
    """

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        values = {str(slot.value) for slot in group.strength_slots}
        strong = tuple(
            slot
            for slot in group.strength_slots
            if str(slot.value) == "strong"
        )
        subtle = tuple(
            slot
            for slot in group.strength_slots
            if str(slot.value) == "subtle"
        )
        if (
            values != {"strong", "subtle"}
            or len(strong) != 1
            or len(subtle) != 1
            or strong[0].normalized_end > subtle[0].normalized_start
            or group.observation_modifier_slots
            or group.state_link_slots
            or group.numeric_slots
            or group.operation_slots
            or group.numeric_relation_slots
            or group.relation_slots
            or group.guard_slots
            or any(
                slot is not strong[0]
                and slot is not subtle[0]
                and strong[0].normalized_end <= slot.normalized_start
                and slot.normalized_end <= subtle[0].normalized_start
                for slot in group.clause.slots
            )
        ):
            materialized.append(group)
            continue
        materialized.append(
            replace(
                group,
                strength_slots=subtle,
                supporting_slots=_ordered_unique_slots(
                    (*group.supporting_slots, *strong)
                ),
            )
        )
    return tuple(materialized)


def _normalize_generic_strength_modifiers(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Let a specific subtle/strong cue override a co-occurring normal cue."""

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        values = {str(slot.value) for slot in group.strength_slots}
        if "normal" not in values or len(values) <= 1:
            materialized.append(group)
            continue
        normal = tuple(
            slot
            for slot in group.strength_slots
            if str(slot.value) == "normal"
        )
        specific = tuple(
            slot
            for slot in group.strength_slots
            if str(slot.value) != "normal"
        )
        if {str(slot.value) for slot in specific} != {"subtle"}:
            materialized.append(group)
            continue
        materialized.append(
            replace(
                group,
                strength_slots=specific,
                supporting_slots=_ordered_unique_slots(
                    (*group.supporting_slots, *normal)
                ),
            )
        )
    return tuple(materialized)


def _normalize_relative_strength_markers(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Treat a relative marker before a lexical strength as non-numeric."""

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        relative = tuple(
            slot
            for slot in group.numeric_relation_slots
            if slot.value == "relative"
        )
        if (
            group.numeric_slots
            or not group.strength_slots
            or not relative
            or len(relative) != len(group.numeric_relation_slots)
        ):
            materialized.append(group)
            continue
        materialized.append(
            replace(
                group,
                numeric_relation_slots=(),
                supporting_slots=_ordered_unique_slots(
                    (*group.supporting_slots, *relative)
                ),
            )
        )
    return tuple(materialized)


def _bind_axis_continuation_strength(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Ground ``axis + continue + strength`` as a positive relative request.

    The relation alone is parent-dependent, while an explicit axis plus a
    lexical strength is self-contained: it asks for some more of that public
    parameter.  The rule is axis-neutral and remains unavailable when any
    competing direction, numeric, reset, observation, or guard is present.
    """

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        continuation = tuple(
            slot
            for slot in group.relation_slots
            if (
                slot.value == "continue"
                and _slot_is_in_clause(slot, group.clause)
            )
        )
        if (
            group.axis_slot.axis_role != "axis"
            or group.axis_slot.match_kind != "axis"
            or len(continuation) != 1
            or len(continuation) != len(group.relation_slots)
            or not group.strength_slots
            or group.direction_slots
            or group.numeric_slots
            or group.operation_slots
            or group.numeric_relation_slots
            or group.observation_modifier_slots
            or group.state_link_slots
            or group.guard_slots
            or group.action_attribute_slots
            or group.surface_action_slots
        ):
            materialized.append(group)
            continue
        direction = _derived_shared_slot(
            continuation[0],
            slot="direction",
            concept_id="continuation_more",
            value=1,
            evidence_slot="resolved_direction",
        )
        materialized.append(
            replace(
                group,
                direction_slots=(direction,),
            )
        )
    return tuple(materialized)


def _bind_observation_attributes(
    groups: tuple[OperationSlotGroup, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[tuple[OperationSlotGroup, ...], tuple[ScopeError, ...]]:
    """Bind a qualitative descriptor to the explicit axis it describes.

    In ``highlights are too dark``, ``highlights`` is the requested edit axis
    while ``dark`` describes its current state.  Treating ``dark`` as a second
    brightness operation would be a bag-of-words error.  Binding is allowed
    only inside one clause, with an explicit axis noun, an observation marker,
    and one uniquely nearest directional descriptor.
    """

    replacements: dict[int, OperationSlotGroup] = {}
    removed: set[int] = set()
    for descriptor_index, descriptor in enumerate(groups):
        if (
            descriptor.axis_slot.match_kind
            not in {"descriptor", "observation"}
            or descriptor.axis_slot.requested_direction not in {-1, 1}
        ):
            continue
        if any(
            _slot_is_in_clause(slot, descriptor.clause)
            for slot in (
                *descriptor.direction_slots,
                *descriptor.operation_slots,
                *descriptor.numeric_slots,
                *descriptor.numeric_relation_slots,
            )
        ):
            continue
        candidates = tuple(
            (index, group)
            for index, group in enumerate(groups)
            if (
                index != descriptor_index
                and index not in removed
                and group.axis_id != descriptor.axis_id
                and group.clause_index == descriptor.clause_index
                and (
                    group.axis_slot.match_kind == "axis"
                    or (
                        group.axis_slot.match_kind == "action"
                        and _has_local_effect_attribute_bridge(
                            group,
                            descriptor,
                        )
                    )
                )
                and _is_observation_group(group)
            )
        )
        if not candidates:
            continue
        distances = {
            index: _span_distance(
                group.axis_slot,
                descriptor.axis_slot,
            )
            for index, group in candidates
        }
        minimum = min(distances.values())
        nearest = tuple(
            (index, group)
            for index, group in candidates
            if distances[index] == minimum
        )
        if len(nearest) != 1:
            continue
        target_index, target = nearest[0]
        current = replacements.get(target_index, target)
        attribute_direction_override: int | None = None
        canonical_effects = tuple(
            binding
            for binding in registry.get_axis(
                descriptor.axis_id
            ).effect_bindings
            if binding.canonical
        )
        if len(canonical_effects) == 1:
            canonical_effect = canonical_effects[0]
            target_effect = registry.get_axis_effect_binding(
                target.axis_id,
                canonical_effect.effect_id,
            )
            if target_effect is not None:
                observed_effect_direction = registry.resolve_axis_effect(
                    descriptor.axis_id,
                    canonical_effect.effect_id,
                    int(descriptor.axis_slot.requested_direction),
                )
                target_state_direction = (
                    observed_effect_direction
                    * target_effect.direction_multiplier
                )
                attribute_direction_override = target_state_direction
        attributes = _ordered_unique_slots(
            (
                *current.attribute_axis_slots,
                descriptor.axis_slot,
            )
        )
        if len(
            {
                slot.requested_direction
                for slot in attributes
                if slot.requested_direction in {-1, 1}
            }
        ) > 1:
            continue
        replacements[target_index] = replace(
            current,
            normalized_start=min(
                current.normalized_start,
                descriptor.normalized_start,
            ),
            normalized_end=max(
                current.normalized_end,
                descriptor.normalized_end,
            ),
            attribute_axis_slots=attributes,
            supporting_slots=_ordered_unique_slots(
                (
                    *current.supporting_slots,
                    *(
                        _local_effect_attribute_slots(
                            current,
                            descriptor,
                        )
                        if current.axis_slot.match_kind == "action"
                        else ()
                    ),
                )
            ),
            observation_attribute_direction_override=(
                attribute_direction_override
                if attribute_direction_override is not None
                else current.observation_attribute_direction_override
            ),
        )
        removed.add(descriptor_index)

    materialized = tuple(
        replacements.get(index, group)
        for index, group in enumerate(groups)
        if index not in removed
    )
    return materialized, ()


def _local_effect_attribute_slots(
    target: OperationSlotGroup,
    descriptor: OperationSlotGroup,
) -> tuple[SemanticSlot, ...]:
    return tuple(
        slot
        for slot in target.clause.slots
        if (
            slot.slot == "effect_reference"
            and target.axis_slot.normalized_end
            <= slot.normalized_start
            and slot.normalized_end
            <= descriptor.axis_slot.normalized_start
        )
    )


def _has_local_effect_attribute_bridge(
    target: OperationSlotGroup,
    descriptor: OperationSlotGroup,
) -> bool:
    """Require one typed effect noun between an action and its state word."""

    effect_slots = _local_effect_attribute_slots(target, descriptor)
    if len(effect_slots) != 1:
        return False
    allowed_between = frozenset(
        {
            "effect_reference",
            "observation_modifier",
            "strength",
            "state_link",
            "noise",
            "function_word",
        }
    )
    return all(
        slot is target.axis_slot
        or slot is descriptor.axis_slot
        or not (
            target.axis_slot.normalized_end <= slot.normalized_start
            and slot.normalized_end <= descriptor.axis_slot.normalized_start
        )
        or slot.slot in allowed_between
        for slot in target.clause.slots
    )


def _bind_cross_axis_actions(
    groups: tuple[OperationSlotGroup, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[tuple[OperationSlotGroup, ...], tuple[ScopeError, ...]]:
    """Bind one local action to an adjacent explicitly named control.

    ``Soften contrast`` requests one contrast operation; the generic soften
    action is an attribute of the following explicit noun, not a second
    sharpen operation.  The rule is structural and axis-neutral: one action,
    one uniquely nearest explicit target, and no intervening semantic material.
    """

    replacements: dict[int, OperationSlotGroup] = {}
    removed: set[int] = set()
    errors: list[ScopeError] = []
    for action_index, action in enumerate(groups):
        local_direction_slots = tuple(
            slot
            for slot in action.direction_slots
            if _slot_is_in_clause(slot, action.clause)
        )
        if (
            action.axis_slot.match_kind != "action"
            or action.axis_slot.requested_direction not in {-1, 1}
            or local_direction_slots
            or action.numeric_slots
            or action.operation_slots
            or action.numeric_relation_slots
            or any(
                slot.value != "continue"
                for slot in action.relation_slots
            )
            or action.observation_modifier_slots
            or action.state_link_slots
        ):
            continue

        candidates = tuple(
            (index, group)
            for index, group in enumerate(groups)
            if (
                index != action_index
                and index not in removed
                and group.axis_id != action.axis_id
                and group.clause_index == action.clause_index
                and group.axis_slot.match_kind == "axis"
                and action.axis_slot.normalized_end
                <= group.axis_slot.normalized_start
                and _only_binding_material_between(
                    action.axis_slot,
                    group.axis_slot,
                    action.clause,
                )
            )
        )
        if not candidates:
            continue
        distances = {
            index: _span_distance(action.axis_slot, group.axis_slot)
            for index, group in candidates
        }
        minimum = min(distances.values())
        nearest = tuple(
            (index, group)
            for index, group in candidates
            if distances[index] == minimum
        )
        if len(nearest) != 1:
            continue

        target_index, target = nearest[0]
        object_binding = action.axis_slot.object_binding
        if object_binding == "self_only":
            continue
        has_typed_determiner = any(
            slot.slot == "function_word"
            and slot.value == "determiner"
            and action.axis_slot.normalized_end <= slot.normalized_start
            and slot.normalized_end <= target.axis_slot.normalized_start
            for slot in action.clause.slots
        )
        if (
            object_binding == "self_or_region"
            and target.axis_id in registry.regions
            and not has_typed_determiner
        ):
            errors.append(
                ScopeError(
                    code="adaptive_axis_region_ambiguous",
                    message=(
                        "The action target can denote either a control axis "
                        "or an edit region."
                    ),
                    clause_index=target.clause_index,
                    normalized_start=action.normalized_start,
                    normalized_end=target.normalized_end,
                    slots=(action.axis_slot, target.axis_slot),
                )
            )
            continue
        if (
            object_binding != "cross_axis_target"
            and not (
                object_binding == "self_or_region"
                and target.axis_id in registry.regions
                and has_typed_determiner
            )
        ):
            continue
        current = replacements.get(target_index, target)
        attributes = _ordered_unique_slots(
            (*current.action_attribute_slots, action.axis_slot)
        )
        directions = {
            int(slot.requested_direction)
            for slot in attributes
            if slot.requested_direction in {-1, 1}
        }
        if len(directions) != 1:
            continue
        replacements[target_index] = replace(
            current,
            normalized_start=min(
                current.normalized_start,
                action.normalized_start,
            ),
            normalized_end=max(
                current.normalized_end,
                action.normalized_end,
            ),
            strength_slots=_ordered_unique_slots(
                (*current.strength_slots, *action.strength_slots)
            ),
            relation_slots=_ordered_unique_slots(
                (*current.relation_slots, *action.relation_slots)
            ),
            action_attribute_slots=attributes,
            supporting_slots=_ordered_unique_slots(
                (*current.supporting_slots, *action.supporting_slots)
            ),
        )
        removed.add(action_index)

    materialized = tuple(
        replacements.get(index, group)
        for index, group in enumerate(groups)
        if index not in removed
    )
    return materialized, _deduplicate_errors(errors)


def _bind_cross_axis_descriptor_support(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Collapse same-direction descriptive context into an explicit target."""

    replacements: dict[int, OperationSlotGroup] = {}
    removed: set[int] = set()
    for source_index, source in enumerate(groups):
        is_observation = _is_observation_group(source)
        if source.axis_slot.match_kind not in {
            "observation",
            "descriptor",
        }:
            continue
        if source.guard_slots or source.ambiguous_slots:
            continue
        if is_observation:
            if source.axis_slot.match_kind != "observation":
                continue
            source_direction = _observation_correction_direction(source)
        elif (
            source.axis_slot.match_kind == "descriptor"
            and source.axis_slot.requested_direction in {-1, 1}
            and not _has_local_operation_signal(source)
            and not source.observation_modifier_slots
            and not source.state_link_slots
            and source.region_slot is not None
            and _slot_is_in_clause(source.region_slot, source.clause)
            and source.region_slot.normalized_end
            <= source.axis_slot.normalized_start
        ):
            source_direction = int(source.axis_slot.requested_direction)
        else:
            continue
        if source_direction not in {-1, 1}:
            continue
        candidates = tuple(
            (index, group)
            for index, group in enumerate(groups)
            if (
                index != source_index
                and index not in removed
                and group.axis_id != source.axis_id
                and group.axis_slot.match_kind == "axis"
                and not group.numeric_slots
                and not group.numeric_relation_slots
                and not group.operation_slots
                and _groups_can_restate(source, group)
                and (
                    not is_observation
                    or source.clause_index < group.clause_index
                )
                and _command_group_direction(group)
                == source_direction
            )
        )
        if not candidates:
            continue
        distances = {
            index: _span_distance(source.axis_slot, group.axis_slot)
            for index, group in candidates
        }
        minimum = min(distances.values())
        nearest = tuple(
            (index, group)
            for index, group in candidates
            if distances[index] == minimum
        )
        if len(nearest) != 1:
            continue
        target_index, target = nearest[0]
        current = replacements.get(target_index, target)
        replacements[target_index] = replace(
            current,
            normalized_start=min(
                current.normalized_start,
                source.normalized_start,
            ),
            normalized_end=max(
                current.normalized_end,
                source.normalized_end,
            ),
            supporting_slots=_ordered_unique_slots(
                (
                    *current.supporting_slots,
                    *_all_group_slots(source),
                )
            ),
        )
        removed.add(source_index)
    return tuple(
        replacements.get(index, group)
        for index, group in enumerate(groups)
        if index not in removed
    )


def _only_binding_material_between(
    action_slot: SemanticSlot,
    target_slot: SemanticSlot,
    clause: ClauseScope,
) -> bool:
    """Return whether two slots form one local action-target phrase."""

    allowed_slots = frozenset(
        {
            "noise",
            "function_word",
            "strength",
            "region",
            "scope",
            "region_context",
        }
    )
    return all(
        slot is action_slot
        or slot is target_slot
        or not (
            action_slot.normalized_end <= slot.normalized_start
            and slot.normalized_end <= target_slot.normalized_start
        )
        or slot.slot in allowed_slots
        for slot in clause.slots
    )


def _bind_local_effect_references(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Bind ``effect`` to one explicit axis in the same local clause."""

    result = list(groups)
    for clause in clauses:
        effect_slots = tuple(
            slot for slot in clause.slots if slot.slot == "effect_reference"
        )
        if not effect_slots:
            continue
        indexes = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        ]
        if len(indexes) != 1:
            continue
        index = indexes[0]
        target = result[index]
        if _is_observation_group(target):
            observation_starts = tuple(
                slot.normalized_start
                for slot in target.observation_modifier_slots
            )
            if (
                len(effect_slots) != 1
                or not observation_starts
                or target.axis_slot.normalized_end
                > effect_slots[0].normalized_start
                or effect_slots[0].normalized_end
                > min(observation_starts)
            ):
                continue
        elif _command_group_direction(target) is None:
            continue
        result[index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                *(slot.normalized_start for slot in effect_slots),
            ),
            normalized_end=max(
                target.normalized_end,
                *(slot.normalized_end for slot in effect_slots),
            ),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, *effect_slots)
            ),
        )
    return tuple(result)


def _bind_mechanism_scope(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Make an explicit control after a typed mechanism marker primary.

    A mechanism slot is registry metadata, not a raw-text check.  In
    ``darken edges with more vignette`` it grounds the relationship between
    the desired visual effect and the named control.  The marker never creates
    an operation by itself and unresolved/multi-target forms stay fail-closed.
    """

    result = list(groups)
    removed: set[int] = set()
    for clause in clauses:
        mechanism_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot == "mechanism" and slot.value == "with"
        )
        if len(mechanism_slots) != 1:
            continue
        mechanism = mechanism_slots[0]
        target_indexes = [
            index
            for index, group in enumerate(result)
            if (
                index not in removed
                and group.clause_index == clause.index
                and mechanism.normalized_end
                <= group.axis_slot.normalized_start
            )
        ]
        target_axis_ids = {
            result[index].axis_id for index in target_indexes
        }
        if len(target_axis_ids) != 1:
            continue
        target_axis_id = next(iter(target_axis_ids))
        primary_index = _select_primary_command_index(
            result,
            target_indexes,
        )
        if primary_index is None:
            continue

        source_indexes = [
            index
            for index, group in enumerate(result)
            if (
                index not in removed
                and group.axis_id != target_axis_id
                and (
                    (
                        group.clause_index == clause.index
                        and group.axis_slot.normalized_end
                        <= mechanism.normalized_start
                    )
                    or group.clause_index == clause.index - 1
                )
            )
        ]
        if len(source_indexes) != 1:
            continue
        source_index = source_indexes[0]
        source = result[source_index]
        if source.guard_slots or source.ambiguous_slots:
            continue

        target = result[primary_index]
        support = _ordered_unique_slots(
            (
                *target.supporting_slots,
                mechanism,
                *_all_group_slots(source),
            )
        )
        result[primary_index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                mechanism.normalized_start,
                source.normalized_start,
            ),
            normalized_end=max(
                target.normalized_end,
                mechanism.normalized_end,
                source.normalized_end,
            ),
            supporting_slots=support,
        )
        removed.add(source_index)
    return tuple(
        group for index, group in enumerate(result) if index not in removed
    )


def _bind_axis_effect_controls(
    groups: tuple[OperationSlotGroup, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[tuple[OperationSlotGroup, ...], tuple[ScopeError, ...]]:
    """Collapse a desired effect into one compatible named control.

    The source must be a descriptor of an axis whose effect binding is marked
    canonical in registry data.  The target must be a distinct control that
    declares the same effect.  Matching polarity turns the source into
    provenance support; opposite polarity is an atomic conflict.  Explicit
    axis-noun commands are intentionally never collapsed.
    """

    result = list(groups)
    removed: set[int] = set()
    errors: list[ScopeError] = []
    for source_index, source in enumerate(result):
        if (
            source_index in removed
            or source.axis_slot.match_kind != "descriptor"
            or source.axis_slot.axis_role == "axis"
            or source.guard_slots
            or source.ambiguous_slots
            or source.observation_modifier_slots
            or source.state_link_slots
            or any(
                slot.slot == "negated_comparative"
                for slot in source.clause.slots
            )
            or source.numeric_slots
            or source.numeric_relation_slots
            or source.operation_slots
        ):
            continue
        source_direction = _command_group_direction(source)
        canonical_bindings = tuple(
            binding
            for binding in registry.get_axis(source.axis_id).effect_bindings
            if binding.canonical
        )
        if source_direction not in {-1, 1} or len(canonical_bindings) != 1:
            continue
        canonical = canonical_bindings[0]

        target_indexes = [
            index
            for index, target in enumerate(result)
            if (
                index != source_index
                and index not in removed
                and target.normalized_start > source.normalized_start
                and _groups_can_restate(source, target)
                and _is_local_command_group(target)
                and _command_group_direction(target) in {-1, 1}
                and (
                    binding := registry.get_axis_effect_binding(
                        target.axis_id,
                        canonical.effect_id,
                    )
                )
                is not None
                and not binding.canonical
            )
        ]
        if len(target_indexes) != 1:
            continue
        target_index = target_indexes[0]
        target = result[target_index]
        target_direction = _command_group_direction(target)
        assert target_direction in {-1, 1}
        source_effect = registry.resolve_axis_effect(
            source.axis_id,
            canonical.effect_id,
            int(source_direction),
        )
        target_effect = registry.resolve_axis_effect(
            target.axis_id,
            canonical.effect_id,
            int(target_direction),
        )
        if source_effect != target_effect:
            errors.append(
                ScopeError(
                    code="effect_polarity_conflict",
                    message=(
                        "The named control would move the described visual "
                        "effect in the opposite direction."
                    ),
                    clause_index=target.clause_index,
                    normalized_start=min(
                        source.normalized_start,
                        target.normalized_start,
                    ),
                    normalized_end=max(
                        source.normalized_end,
                        target.normalized_end,
                    ),
                    slots=(
                        source.axis_slot,
                        target.axis_slot,
                    ),
                )
            )
            continue

        result[target_index] = replace(
            target,
            normalized_start=min(
                source.normalized_start,
                target.normalized_start,
            ),
            normalized_end=max(
                source.normalized_end,
                target.normalized_end,
            ),
            supporting_slots=_ordered_unique_slots(
                (
                    *target.supporting_slots,
                    *_all_group_slots(source),
                )
            ),
        )
        removed.add(source_index)

    return (
        tuple(
            group
            for index, group in enumerate(result)
            if index not in removed
        ),
        _deduplicate_errors(errors),
    )


def _bind_local_axis_effect_states(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    effect_state_slots: tuple[SemanticSlot, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[tuple[OperationSlotGroup, ...], set[int]]:
    """Bind a registry effect state to one local explicit axis noun.

    The same surface adjective may be both a public-axis descriptor and an
    effect state.  It becomes an effect only when one local axis declares that
    effect binding.  Observation markers produce a corrective observation;
    an explicit strength/continuation produces a directional command.
    """

    result = list(groups)
    consumed: set[int] = set()
    for source in effect_state_slots:
        effect_interpretations = tuple(
            item
            for item in source.interpretations
            if (
                item.namespace == "effect"
                and item.slot == "effect_state"
                and item.requested_direction in {-1, 1}
            )
        )
        if len(effect_interpretations) != 1:
            continue
        effect = effect_interpretations[0]
        clause = _clause_for_slot_span(source, clauses)
        if clause is None:
            continue
        anaphora = tuple(
            item for item in clause.slots if item.slot == "anaphora"
        )
        candidates = tuple(
            (index, group, anaphoric)
            for index, group in enumerate(result)
            for anaphoric in (
                (
                    group.clause_index == clause.index - 1
                    and len(anaphora) == 1
                    and anaphora[0] in group.supporting_slots
                    and _only_local_support_between(
                        source,
                        anaphora[0],
                        clause,
                    )
                ),
            )
            if (
                group.axis_slot.axis_role == "axis"
                and group.axis_slot.match_kind == "axis"
                and registry.get_axis_effect_binding(
                    group.axis_id,
                    effect.concept_id,
                )
                is not None
                and (
                    anaphoric
                    or (
                        group.clause_index == clause.index
                        and _only_local_support_between(
                            group.axis_slot,
                            source,
                            clause,
                            additional_allowed_slots=frozenset(
                                {
                                    "observation_modifier",
                                    "state_link",
                                    "clause_aspect",
                                    "relation",
                                }
                            ),
                        )
                    )
                )
            )
        )
        if len(candidates) != 1:
            continue
        index, target, anaphoric_command = candidates[0]
        continuation_directions = tuple(
            slot
            for slot in target.direction_slots
            if (
                slot.evidence.slot == "resolved_direction"
                and slot.concept_id == "continuation_more"
                and slot.value == 1
            )
        )
        if (
            (
                target.direction_slots
                and len(continuation_directions)
                != len(target.direction_slots)
            )
            or target.numeric_slots
            or target.operation_slots
            or target.numeric_relation_slots
            or target.guard_slots
            or target.action_attribute_slots
            or target.surface_action_slots
        ):
            continue
        binding = registry.get_axis_effect_binding(
            target.axis_id,
            effect.concept_id,
        )
        assert binding is not None
        axis_state_direction = (
            int(effect.requested_direction)
            * binding.direction_multiplier
        )
        selected_effect = (
            _materialize_interpretation(
                source,
                effect,
                evidence_slot="effect_support",
            )
            if source.is_ambiguous
            else source
        )
        if anaphoric_command:
            observed_direction = _observation_correction_direction(target)
            if (
                observed_direction in {-1, 1}
                and observed_direction != axis_state_direction
            ):
                continue
            direction = _derived_shared_slot(
                source,
                slot="direction",
                concept_id=f"{effect.concept_id}_axis_direction",
                value=axis_state_direction,
                evidence_slot="resolved_direction",
            )
            result[index] = replace(
                target,
                normalized_start=min(
                    target.normalized_start,
                    source.normalized_start,
                ),
                normalized_end=max(
                    target.normalized_end,
                    source.normalized_end,
                ),
                direction_slots=_ordered_unique_slots(
                    (*target.direction_slots, direction)
                ),
                supporting_slots=_ordered_unique_slots(
                    (*target.supporting_slots, selected_effect)
                ),
            )
            if source.is_ambiguous:
                consumed.add(id(source))
            continue
        has_observation = bool(target.observation_modifier_slots)
        continuation_relations = tuple(
            slot
            for slot in target.relation_slots
            if (
                slot.slot == "relation"
                and slot.concept_id == "relation_continue"
                and slot.value == "continue"
            )
        )
        if (
            target.relation_slots
            and len(continuation_relations)
            != len(target.relation_slots)
        ):
            continue
        has_command_modifier = bool(
            target.strength_slots or continuation_relations
        )
        if not has_observation and not has_command_modifier:
            continue
        if has_observation and target.relation_slots:
            # A mixed observed-state/command fragment needs an explicit verb;
            # do not infer which modifier governs the effect word.
            continue
        if has_observation:
            result[index] = replace(
                target,
                normalized_start=min(
                    target.normalized_start,
                    source.normalized_start,
                ),
                normalized_end=max(
                    target.normalized_end,
                    source.normalized_end,
                ),
                observation_attribute_direction_override=axis_state_direction,
                supporting_slots=_ordered_unique_slots(
                    (*target.supporting_slots, selected_effect)
                ),
            )
        else:
            # ``continue/more`` repeats the requested *effect*, not the
            # parameter's positive direction.  Once one explicit axis and one
            # registry effect binding prove the local construction, derive
            # the axis-space direction from that binding.  A real explicit
            # direction is still excluded above and therefore cannot be
            # silently overwritten.
            direction_slots = (
                _derived_shared_slot(
                    source,
                    slot="direction",
                    concept_id=(
                        f"{effect.concept_id}_axis_direction"
                    ),
                    value=axis_state_direction,
                    evidence_slot="resolved_direction",
                ),
            )
            result[index] = replace(
                target,
                normalized_start=min(
                    target.normalized_start,
                    source.normalized_start,
                ),
                normalized_end=max(
                    target.normalized_end,
                    source.normalized_end,
                ),
                direction_slots=direction_slots,
                supporting_slots=_ordered_unique_slots(
                    (*target.supporting_slots, selected_effect)
                ),
            )
        if source.is_ambiguous:
            consumed.add(id(source))
    return tuple(result), consumed


def _bind_ambiguous_effect_states(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    effect_state_slots: tuple[SemanticSlot, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[
    tuple[OperationSlotGroup, ...],
    set[int],
    tuple[ScopeError, ...],
]:
    """Bind an observed effect state to one polarity-compatible remedy.

    This is the fail-closed counterpart to structural direction resolution.
    An ambiguous state is consumed only when a typed observation modifier and
    one adjacent explicit command prove both the effect dimension and the
    corrective polarity from registry metadata.
    """

    result = list(groups)
    consumed: set[int] = set()
    errors: list[ScopeError] = []
    for slot in effect_state_slots:
        effect_interpretations = tuple(
            item
            for item in slot.interpretations
            if (
                item.namespace == "effect"
                and item.slot == "effect_state"
                and item.requested_direction in {-1, 1}
            )
        )
        if len(effect_interpretations) != 1:
            continue
        clause = _clause_for_slot_span(slot, clauses)
        if clause is None:
            continue
        modifiers = tuple(
            item
            for item in clause.slots
            if (
                not item.is_ambiguous
                and item.slot == "observation_modifier"
                and str(item.value) in _OBSERVATION_VALUES
            )
        )
        modifier_values = {str(item.value) for item in modifiers}
        if "too" in modifier_values and len(modifier_values) > 1:
            modifier_values.remove("too")
        if len(modifier_values) != 1:
            continue
        modifier_value = next(iter(modifier_values))
        effect = effect_interpretations[0]
        desired_effect_direction = int(effect.requested_direction)
        if modifier_value != "not_enough":
            desired_effect_direction *= -1

        candidate_indexes = [
            index
            for index, target in enumerate(result)
            if (
                target.clause_index in {clause.index, clause.index + 1}
                and (
                    target.clause_index != clause.index
                    or target.axis_slot.normalized_start
                    > slot.normalized_start
                )
                and _clauses_allow_effect_binding(clause, target.clause)
                and _is_local_command_group(target)
                and _command_group_direction(target) in {-1, 1}
                and registry.get_axis_effect_binding(
                    target.axis_id,
                    effect.concept_id,
                )
                is not None
            )
        ]
        if len(candidate_indexes) != 1:
            continue
        target_index = candidate_indexes[0]
        target = result[target_index]
        target_direction = _command_group_direction(target)
        assert target_direction in {-1, 1}
        produced_effect_direction = registry.resolve_axis_effect(
            target.axis_id,
            effect.concept_id,
            int(target_direction),
        )
        if produced_effect_direction != desired_effect_direction:
            errors.append(
                ScopeError(
                    code="effect_polarity_conflict",
                    message=(
                        "The requested control contradicts the observed "
                        "effect-state correction."
                    ),
                    clause_index=target.clause_index,
                    normalized_start=min(
                        slot.normalized_start,
                        target.normalized_start,
                    ),
                    normalized_end=max(
                        slot.normalized_end,
                        target.normalized_end,
                    ),
                    slots=(slot, target.axis_slot, *modifiers),
                )
            )
            continue

        selected_effect = (
            _materialize_interpretation(
                slot,
                effect,
                evidence_slot="effect_support",
            )
            if slot.is_ambiguous
            else slot
        )
        state_slots = tuple(
            item
            for item in clause.slots
            if not item.is_ambiguous and item.slot == "state_link"
        )
        strength_slots = tuple(
            item
            for item in clause.slots
            if not item.is_ambiguous and item.slot == "strength"
        )
        source_context_ids = {
            id(item) for item in (*modifiers, *state_slots)
        }
        result[target_index] = replace(
            target,
            normalized_start=min(
                slot.normalized_start,
                target.normalized_start,
                *(item.normalized_start for item in modifiers),
            ),
            normalized_end=max(
                slot.normalized_end,
                target.normalized_end,
                *(item.normalized_end for item in modifiers),
            ),
            observation_modifier_slots=tuple(
                item
                for item in target.observation_modifier_slots
                if id(item) not in source_context_ids
            ),
            state_link_slots=tuple(
                item
                for item in target.state_link_slots
                if id(item) not in source_context_ids
            ),
            strength_slots=_ordered_unique_slots(
                (*target.strength_slots, *strength_slots)
            ),
            supporting_slots=_ordered_unique_slots(
                (
                    *target.supporting_slots,
                    selected_effect,
                    *modifiers,
                    *state_slots,
                )
            ),
        )
        if slot.is_ambiguous:
            consumed.add(id(slot))

    return tuple(result), consumed, _deduplicate_errors(errors)


def _clauses_allow_effect_binding(
    observation: ClauseScope,
    command: ClauseScope,
) -> bool:
    if command.index < observation.index or command.index - observation.index > 1:
        return False
    if observation.index == command.index:
        return True
    return (
        observation.boundary_after not in {"contrastive", "disjunction"}
        and command.boundary_before not in {"contrastive", "disjunction"}
    )


def _bind_negated_removal_amounts(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    guard_slots: tuple[SemanticSlot, ...],
) -> tuple[tuple[OperationSlotGroup, ...], set[int]]:
    """Compose one typed ``do not remove that much <inverse effect>`` frame.

    The accepted structure is intentionally narrow and parameter-neutral:
    one negation, one removal direction, one degree reference, one positive
    amount comparative, and one axis noun whose registry multiplier declares
    that the noun names the inverse of the control.  The negation therefore
    requests a subtle step in the opposite canonical direction.  Missing or
    contradictory pieces leave the negation authoritative.
    """

    result = list(groups)
    consumed: set[int] = set()
    clause_by_slot = _map_slots_to_clauses(clauses)
    for guard in guard_slots:
        if (
            guard.slot != "negation"
            or guard.concept_id != "negation"
            or guard.value is not True
        ):
            continue
        clause = clause_by_slot.get(id(guard))
        if clause is None:
            continue
        local_indexes = tuple(
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        )
        local_guards = tuple(
            slot
            for slot in guard_slots
            if clause_by_slot.get(id(slot)) is clause
        )
        if len(local_indexes) != 1 or len(local_guards) != 1:
            continue
        target_index = local_indexes[0]
        target = result[target_index]
        direct = tuple(
            slot
            for slot in target.direction_slots
            if (
                slot.concept_id == "direction_negative"
                and slot.value == -1
            )
        )
        comparative = tuple(
            slot
            for slot in target.direction_slots
            if (
                slot.concept_id == "comparative_more"
                and slot.value == 1
            )
        )
        degree_references = tuple(
            slot
            for slot in clause.slots
            if (
                slot.slot == "comparison_reference"
                and slot.concept_id == "degree_comparison_reference"
                and slot.value == "degree"
            )
        )
        if (
            target.axis_slot.axis_role != "axis"
            or target.axis_slot.match_kind != "axis"
            or target.direction_multiplier != -1
            or len(target.direction_slots) != 2
            or len(direct) != 1
            or len(comparative) != 1
            or len(degree_references) != 1
            or target.strength_slots
            or target.numeric_slots
            or target.operation_slots
            or target.numeric_relation_slots
            or target.relation_slots
            or target.observation_modifier_slots
            or target.state_link_slots
            or target.action_attribute_slots
            or target.surface_action_slots
        ):
            continue
        degree = degree_references[0]
        if not (
            guard.normalized_end <= direct[0].normalized_start
            and direct[0].normalized_end <= degree.normalized_start
            and degree.normalized_end <= comparative[0].normalized_start
            and comparative[0].normalized_end
            <= target.axis_slot.normalized_start
        ):
            continue

        resolved_direction = _derived_shared_slot(
            guard,
            slot="direction",
            concept_id="negated_removal_amount_direction",
            value=1,
            evidence_slot="resolved_direction",
        )
        resolved_strength = _derived_shared_slot(
            guard,
            slot="strength",
            concept_id="negated_removal_amount_subtle",
            value="subtle",
            evidence_slot="resolved_strength",
        )
        result[target_index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                guard.normalized_start,
            ),
            normalized_end=max(
                target.normalized_end,
                guard.normalized_end,
            ),
            direction_slots=(resolved_direction,),
            strength_slots=(resolved_strength,),
            guard_slots=tuple(
                slot for slot in target.guard_slots if slot is not guard
            ),
            supporting_slots=_ordered_unique_slots(
                (
                    *target.supporting_slots,
                    guard,
                    degree,
                    *direct,
                    *comparative,
                )
            ),
        )
        consumed.add(id(guard))
    return tuple(result), consumed


def _bind_upper_bound_negations(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    guard_slots: tuple[SemanticSlot, ...],
) -> tuple[tuple[OperationSlotGroup, ...], set[int]]:
    """Compose a bare ``negation + too-state`` preference.

    This is narrower than general negation handling.  It accepts only one
    axis-bearing observation fragment with no state subject, explicit command,
    region, numeric value, or coordination.  A normal negated command therefore
    remains an authoritative guard, while ``not too bright`` becomes a subtle
    upper-bound correction.
    """

    result = list(groups)
    consumed: set[int] = set()
    clause_by_slot = _map_slots_to_clauses(clauses)
    for guard in guard_slots:
        if guard.slot != "negation":
            continue
        clause = clause_by_slot.get(id(guard))
        if clause is None:
            continue
        local_indexes = tuple(
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        )
        local_guards = tuple(
            slot
            for slot in guard_slots
            if clause_by_slot.get(id(slot)) is clause
        )
        if len(local_indexes) != 1 or len(local_guards) != 1:
            continue
        target_index = local_indexes[0]
        target = result[target_index]
        observation_values = {
            str(slot.value)
            for slot in target.observation_modifier_slots
        }
        if not observation_values or not observation_values.issubset(
            {"too", "too_much"}
        ):
            continue
        if (
            target.state_link_slots
            or target.direction_slots
            or target.numeric_slots
            or target.operation_slots
            or target.numeric_relation_slots
            or target.relation_slots
            or target.action_attribute_slots
            or target.surface_action_slots
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
                for slot in clause.slots
            )
            or any(
                str(slot.value) != "subtle"
                for slot in target.strength_slots
            )
        ):
            continue
        implicit_strength = (
            ()
            if target.strength_slots
            else (
                _derived_shared_slot(
                    guard,
                    slot="strength",
                    concept_id="upper_bound_subtle",
                    value="subtle",
                    evidence_slot="resolved_strength",
                ),
            )
        )
        result[target_index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                guard.normalized_start,
            ),
            normalized_end=max(
                target.normalized_end,
                guard.normalized_end,
            ),
            strength_slots=_ordered_unique_slots(
                (*target.strength_slots, *implicit_strength)
            ),
            guard_slots=tuple(
                slot for slot in target.guard_slots if slot is not guard
            ),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, guard)
            ),
        )
        for index, group in enumerate(result):
            if index == target_index or guard not in group.guard_slots:
                continue
            result[index] = replace(
                group,
                guard_slots=tuple(
                    slot for slot in group.guard_slots if slot is not guard
                ),
            )
        consumed.add(id(guard))
    return tuple(result), consumed


def _bind_standalone_negated_comparatives(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Compose one axis-local ``not so <state>`` upper-bound preference."""

    result = list(groups)
    for clause in clauses:
        markers = tuple(
            slot
            for slot in clause.slots
            if slot.slot == "negated_comparative"
        )
        if len(markers) != 1:
            continue
        coordination_indexes = tuple(
            index
            for index, group in enumerate(result)
            if (
                group.clause.coordination_group
                == clause.coordination_group
            )
        )
        local_indexes = tuple(
            index
            for index in coordination_indexes
            if result[index].clause_index == clause.index
        )
        if (
            len(coordination_indexes) != 1
            or len(local_indexes) != 1
        ):
            continue
        index = local_indexes[0]
        target = result[index]
        marker = markers[0]
        explicit_state_slots = tuple(
            slot
            for slot in target.direction_slots
            if (
                slot.value in {-1, 1}
                and slot.concept_id not in _COMPARATIVE_CONCEPTS
            )
        )
        explicit_state_directions = {
            int(slot.value) * target.direction_multiplier
            for slot in explicit_state_slots
        }
        fused_state_direction = (
            int(target.axis_slot.requested_direction)
            if (
                target.axis_slot.match_kind == "descriptor"
                and target.axis_slot.requested_direction in {-1, 1}
            )
            else None
        )
        descriptor_remedy = bool(
            fused_state_direction is not None
            and explicit_state_directions
            and explicit_state_directions == {-fused_state_direction}
            and all(
                target.axis_slot.normalized_end
                <= slot.normalized_start
                for slot in explicit_state_slots
            )
            and marker.normalized_end
            <= target.axis_slot.normalized_start
        )
        if fused_state_direction is not None and explicit_state_directions:
            # For a directional descriptor, an extra direct direction is safe
            # only as a later, typed corrective remedy.  Reinforcing or
            # preposed directions remain unbound/conflicting and fail closed.
            if not descriptor_remedy:
                continue
            state_directions = {fused_state_direction}
        else:
            state_directions = set(explicit_state_directions)
            if fused_state_direction is not None:
                state_directions.add(fused_state_direction)
        if (
            len(state_directions) != 1
            or target.axis_slot.match_kind not in {"axis", "descriptor"}
            or target.numeric_slots
            or target.operation_slots
            or target.numeric_relation_slots
            or target.observation_modifier_slots
            or target.state_link_slots
            or target.guard_slots
            or target.action_attribute_slots
            or target.surface_action_slots
            or any(
                slot.slot
                in {
                    "clause_aspect",
                    "clause_modal",
                    "clause_subject",
                    "existential",
                    "request_predicate",
                }
                for slot in clause.slots
            )
        ):
            continue
        observation = _derived_shared_slot(
            marker,
            slot="observation_modifier",
            concept_id="negated_upper_bound",
            value="too",
            evidence_slot="resolved_observation",
        )
        result[index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                marker.normalized_start,
            ),
            normalized_end=max(
                target.normalized_end,
                marker.normalized_end,
            ),
            observation_modifier_slots=(observation,),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, marker)
            ),
        )
    return tuple(result)


def _bind_discontinuous_sufficiency_negations(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    guard_slots: tuple[SemanticSlot, ...],
) -> tuple[
    tuple[OperationSlotGroup, ...],
    set[int],
    tuple[ScopeError, ...],
]:
    """Compose ``negation + observed state + enough`` from typed slots.

    ``enough`` is intentionally not a synonym for ``not enough`` by itself.
    The corrective meaning is enabled only when one local negation, one
    registry-grounded observed state, and the sufficiency marker occur in that
    order with no competing semantic material between them.  The observed
    state can be an axis descriptor or an effect state already proven against
    a compatible command by the registry-driven effect binder.
    """

    result = list(groups)
    consumed_guards: set[int] = set()
    errors: list[ScopeError] = []
    clause_by_slot = {
        id(slot): clause for clause in clauses for slot in clause.slots
    }
    sufficiency_slots = tuple(
        slot
        for clause in clauses
        for slot in clause.slots
        if slot.concept_id == "sufficiency_enough"
    )

    def relation_is_clean(
        clause: ClauseScope,
        guard: SemanticSlot,
        pivot: SemanticSlot,
        marker: SemanticSlot,
    ) -> bool:
        if not (
            guard.normalized_end <= pivot.normalized_start
            and pivot.normalized_end <= marker.normalized_start
        ):
            return False
        return not any(
            not (
                other is guard
                or other is marker
                or other.slot == "state_link"
                or (
                    other.normalized_start == pivot.normalized_start
                    and other.normalized_end == pivot.normalized_end
                    and other.evidence.start == pivot.evidence.start
                    and other.evidence.end == pivot.evidence.end
                )
            )
            and guard.normalized_end
            <= other.normalized_start
            and other.normalized_end
            <= marker.normalized_start
            for other in clause.slots
        )

    for marker in sufficiency_slots:
        clause = clause_by_slot.get(id(marker))
        if clause is None:
            continue
        negations = tuple(
            guard
            for guard in guard_slots
            if (
                guard.slot == "negation"
                and clause_by_slot.get(id(guard)) is clause
                and id(guard) not in consumed_guards
            )
        )
        candidates: list[tuple[int, SemanticSlot]] = []
        if len(negations) == 1:
            guard = negations[0]
            for index, group in enumerate(result):
                if (
                    group.clause_index == clause.index
                    and any(
                        slot is marker
                        for slot in group.observation_modifier_slots
                    )
                ):
                    observed_state_pivots = _ordered_unique_slots(
                        (
                            group.axis_slot,
                            *group.attribute_axis_slots,
                            *group.direction_slots,
                        )
                    )
                    clean_pivots = tuple(
                        pivot
                        for pivot in observed_state_pivots
                        if relation_is_clean(
                            clause,
                            guard,
                            pivot,
                            marker,
                        )
                    )
                    if len(clean_pivots) == 1:
                        candidates.append((index, clean_pivots[0]))
                    continue
                if not any(
                    slot is marker for slot in group.supporting_slots
                ):
                    continue
                effect_pivots = tuple(
                    slot
                    for slot in group.supporting_slots
                    if (
                        slot.namespace == "effect"
                        and slot.slot == "effect_state"
                        and relation_is_clean(
                            clause,
                            guard,
                            slot,
                            marker,
                        )
                    )
                )
                if len(effect_pivots) == 1:
                    candidates.append((index, effect_pivots[0]))

        if len(negations) != 1 or len(candidates) != 1:
            related = tuple(
                (
                    *negations,
                    *(pivot for _, pivot in candidates),
                )
            )
            errors.append(
                ScopeError(
                    code="unresolved_sufficiency_relation",
                    message=(
                        "A sufficiency marker needs exactly one local "
                        "negation and one registry-grounded observed state."
                    ),
                    clause_index=clause.index,
                    normalized_start=min(
                        [
                            marker.normalized_start,
                            *(slot.normalized_start for slot in related),
                        ]
                    ),
                    normalized_end=max(
                        [
                            marker.normalized_end,
                            *(slot.normalized_end for slot in related),
                        ]
                    ),
                    slots=_ordered_unique_slots((marker, *related)),
                )
            )
            continue

        guard = negations[0]
        target_index, _ = candidates[0]
        target = result[target_index]
        result[target_index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                guard.normalized_start,
            ),
            normalized_end=max(
                target.normalized_end,
                marker.normalized_end,
            ),
            guard_slots=tuple(
                slot for slot in target.guard_slots if slot is not guard
            ),
            supporting_slots=_ordered_unique_slots(
                (*target.supporting_slots, guard)
            ),
        )
        for index, group in enumerate(result):
            if index == target_index or guard not in group.guard_slots:
                continue
            result[index] = replace(
                group,
                guard_slots=tuple(
                    slot for slot in group.guard_slots if slot is not guard
                ),
            )
        consumed_guards.add(id(guard))

    return tuple(result), consumed_guards, _deduplicate_errors(errors)


def _bind_corrective_negations(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    guard_slots: tuple[SemanticSlot, ...],
) -> tuple[tuple[OperationSlotGroup, ...], set[int]]:
    """Consume only observation-like negation with an explicit later remedy."""

    result = list(groups)
    removed: set[int] = set()
    consumed: set[int] = set()
    clause_by_slot = {
        id(slot): clause for clause in clauses for slot in clause.slots
    }
    for guard in guard_slots:
        if guard.slot != "negation":
            continue
        clause = clause_by_slot.get(id(guard))
        if clause is None:
            continue
        if any(
            slot.slot
            in {
                "direction",
                "operation",
                "numeric",
                "numeric_relation",
                "relation",
            }
            for slot in clause.slots
        ):
            # A locally negated command remains authoritative.
            continue

        observation_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot in {"observation_modifier", "state_link"}
        )
        observation_modifiers = tuple(
            slot
            for slot in observation_slots
            if slot.slot == "observation_modifier"
        )
        source_indexes = [
            index
            for index, group in enumerate(result)
            if (
                index not in removed
                and group.clause_index == clause.index
                and group.axis_slot.match_kind
                in {"descriptor", "observation"}
            )
        ]
        if not observation_modifiers and len(source_indexes) != 1:
            continue

        target_index: int | None = None
        source_index = (
            source_indexes[0] if len(source_indexes) == 1 else None
        )
        if source_index is not None:
            source = result[source_index]
            if any(
                not _slot_is_in_clause(slot, clause)
                for slot in (
                    *source.direction_slots,
                    *source.operation_slots,
                    *source.numeric_slots,
                    *source.numeric_relation_slots,
                    *source.relation_slots,
                )
            ):
                target_index = source_index

        if target_index is None:
            following = [
                index
                for index, group in enumerate(result)
                if (
                    index not in removed
                    and group.clause_index == clause.index + 1
                    and (
                        source_index is None
                        or group.axis_id == result[source_index].axis_id
                    )
                )
            ]
            target_index = _select_primary_command_index(result, following)
        if target_index is None:
            continue

        target = result[target_index]
        command_direction = _command_group_direction(target)
        if command_direction is None:
            continue
        if source_index is not None:
            source = result[source_index]
            if observation_modifiers:
                expected = _observation_correction_direction(source)
            else:
                expected = (
                    -int(source.axis_slot.requested_direction)
                    if source.axis_slot.requested_direction in {-1, 1}
                    else None
                )
        else:
            expected = _axis_free_observation_direction(
                observation_modifiers,
                target,
            )
        if expected is None or expected != command_direction:
            continue

        support: list[SemanticSlot] = [
            *target.supporting_slots,
            guard,
            *observation_slots,
        ]
        if source_index is not None and source_index != target_index:
            support.extend(_all_group_slots(result[source_index]))
            removed.add(source_index)
        result[target_index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                guard.normalized_start,
                *(
                    [result[source_index].normalized_start]
                    if source_index is not None
                    else []
                ),
            ),
            normalized_end=max(
                target.normalized_end,
                guard.normalized_end,
                *(
                    [result[source_index].normalized_end]
                    if source_index is not None
                    else []
                ),
            ),
            guard_slots=tuple(
                slot for slot in target.guard_slots if slot is not guard
            ),
            supporting_slots=_ordered_unique_slots(support),
        )
        for index, group in enumerate(result):
            if index in removed or guard not in group.guard_slots:
                continue
            result[index] = replace(
                group,
                guard_slots=tuple(
                    slot for slot in group.guard_slots if slot is not guard
                ),
            )
        consumed.add(id(guard))
    return (
        tuple(
            group
            for index, group in enumerate(result)
            if index not in removed
        ),
        consumed,
    )


def _bind_negated_comparative_remedies(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Bind a typed ``not so`` observation to one later explicit remedy."""

    result = list(groups)
    removed: set[int] = set()
    for clause in clauses:
        markers = tuple(
            slot
            for slot in clause.slots
            if slot.slot == "negated_comparative"
        )
        if len(markers) != 1:
            continue
        marker = markers[0]
        source_indexes = [
            index
            for index, group in enumerate(result)
            if (
                index not in removed
                and group.clause_index == clause.index
                and group.axis_slot.match_kind
                in {"descriptor", "observation"}
                and not any(
                    _slot_is_in_clause(slot, clause)
                    for slot in (
                        *group.direction_slots,
                        *group.operation_slots,
                        *group.numeric_slots,
                        *group.numeric_relation_slots,
                        *group.relation_slots,
                    )
                )
            )
        ]
        observation_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot in {"observation_modifier", "state_link"}
        )
        observation_modifiers = tuple(
            slot
            for slot in observation_slots
            if slot.slot == "observation_modifier"
        )
        if len(source_indexes) > 1:
            continue
        source_index = source_indexes[0] if source_indexes else None
        if source_index is None and not observation_modifiers:
            continue
        source = result[source_index] if source_index is not None else None

        if source is not None and any(
            not _slot_is_in_clause(slot, clause)
            for slot in (
                *source.direction_slots,
                *source.operation_slots,
                *source.numeric_slots,
                *source.numeric_relation_slots,
                *source.relation_slots,
            )
        ):
            command_direction = _command_group_direction(source)
            expected = (
                _observation_correction_direction(source)
                if observation_modifiers
                else -int(source.axis_slot.requested_direction)
                if source.axis_slot.requested_direction in {-1, 1}
                else None
            )
            if expected is None or expected != command_direction:
                continue
            result[source_index] = replace(
                source,
                normalized_start=min(
                    source.normalized_start,
                    marker.normalized_start,
                ),
                normalized_end=max(
                    source.normalized_end,
                    marker.normalized_end,
                ),
                supporting_slots=_ordered_unique_slots(
                    (
                        *source.supporting_slots,
                        marker,
                        *observation_slots,
                    )
                ),
            )
            continue

        following = [
            index
            for index, group in enumerate(result)
            if (
                index not in removed
                and group.clause_index == clause.index + 1
            )
        ]
        following_axis_ids = {
            result[index].axis_id for index in following
        }
        if len(following_axis_ids) != 1:
            continue
        target_index = _select_primary_command_index(result, following)
        if target_index is None:
            continue
        target = result[target_index]
        command_direction = _command_group_direction(target)
        if command_direction is None:
            continue
        if source is not None and source.axis_id == target.axis_id:
            expected = (
                -int(source.axis_slot.requested_direction)
                if source.axis_slot.requested_direction in {-1, 1}
                else None
            )
            if expected is None or expected != command_direction:
                continue
        elif source is None:
            expected = _axis_free_observation_direction(
                observation_modifiers,
                target,
            )
            if expected is None or expected != command_direction:
                continue

        result[target_index] = replace(
            target,
            normalized_start=min(
                target.normalized_start,
                *(
                    [source.normalized_start]
                    if source is not None
                    else [marker.normalized_start]
                ),
                marker.normalized_start,
            ),
            normalized_end=max(
                target.normalized_end,
                *(
                    [source.normalized_end]
                    if source is not None
                    else [marker.normalized_end]
                ),
                marker.normalized_end,
            ),
            supporting_slots=_ordered_unique_slots(
                (
                    *target.supporting_slots,
                    marker,
                    *observation_slots,
                    *(
                        _all_group_slots(source)
                        if source is not None
                        else ()
                    ),
                )
            ),
        )
        if source_index is not None:
            removed.add(source_index)
    return tuple(
        group for index, group in enumerate(result) if index not in removed
    )


def _select_primary_command_index(
    groups: list[OperationSlotGroup],
    indexes: Iterable[int],
) -> int | None:
    candidates = [
        index
        for index in indexes
        if _command_group_direction(groups[index]) is not None
    ]
    if len(candidates) == 1:
        return candidates[0]
    fused = [
        index
        for index in candidates
        if groups[index].axis_slot.match_kind in {"action", "descriptor"}
    ]
    return fused[0] if len(fused) == 1 else None


def _bind_preservation_commands(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    guard_slots: tuple[SemanticSlot, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[tuple[OperationSlotGroup, ...], set[int]]:
    """Consume preservation only when one grounded edit asks for change.

    ``keep`` and ``保留`` are safety guards by default.  A single-clause,
    single-operation request can consume that guard only when grounded
    metadata proves the phrase asks for a relative change: either a typed
    comparative slot or a directional axis alias marked ``implies_change``.
    """

    if len(clauses) != 1 or len(groups) != 1 or len(guard_slots) != 1:
        return groups, set()
    guard = guard_slots[0]
    if (
        guard.slot != "guard"
        or guard.concept_id != "preservation"
        or guard.value != "preserve"
    ):
        return groups, set()
    group = groups[0]
    clause = clauses[0]
    if (
        group.clause_index != clause.index
        or _command_group_direction(group) not in {-1, 1}
    ):
        return groups, set()

    comparative_change = any(
        slot.slot == "direction"
        and slot.concept_id in _COMPARATIVE_CONCEPTS
        for slot in (*group.direction_slots, *group.supporting_slots)
    )
    alias_binding = registry.resolve_axis_alias(
        group.axis_slot.normalized_text,
        group.axis_slot.language,
    )
    alias_change = bool(
        alias_binding is not None
        and alias_binding.axis_id == group.axis_id
        and alias_binding.implies_change
    )
    if not (comparative_change or alias_change):
        return groups, set()

    updated = replace(
        group,
        normalized_start=min(
            group.normalized_start,
            guard.normalized_start,
        ),
        normalized_end=max(
            group.normalized_end,
            guard.normalized_end,
        ),
        guard_slots=tuple(
            slot for slot in group.guard_slots if slot is not guard
        ),
        supporting_slots=_ordered_unique_slots(
            (*group.supporting_slots, guard)
        ),
    )
    return (updated,), {id(guard)}


def _bind_typed_context_support(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    *,
    grounded_region_slots: tuple[SemanticSlot, ...],
    quantity_all_slots: tuple[SemanticSlot, ...],
) -> tuple[tuple[OperationSlotGroup, ...], tuple[ScopeError, ...]]:
    """Bind formerly-loaded filler only to one provable semantic target.

    These concepts are deliberately typed in the registry instead of being
    discarded as noise.  A region reference needs one explicit region and one
    operation antecedent; an attribute or return marker needs one operation;
    group markers need a complete, same-coordination operation set.  Anything
    else stays visible as a structured scope error.
    """

    typed_slots = tuple(
        slot
        for clause in clauses
        for slot in clause.slots
        if slot.slot
        in {
            "region_support",
            "region_constraint",
            "region_anaphora",
            "semantic_attribute",
            "scope_quantifier",
            "compound_marker",
            "return_relation",
        }
    )
    if not typed_slots and not quantity_all_slots:
        return groups, ()

    result = list(groups)
    errors: list[ScopeError] = []
    clause_by_slot = {
        id(slot): clause for clause in clauses for slot in clause.slots
    }
    clause_by_index = {clause.index: clause for clause in clauses}

    def operation_candidates(slot: SemanticSlot) -> list[int]:
        clause = clause_by_slot.get(id(slot))
        if clause is None:
            return []
        local = [
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        ]
        if local:
            return local
        coordinated = [
            index
            for index, group in enumerate(result)
            if (
                clause_by_index.get(group.clause_index) is not None
                and clause_by_index[
                    group.clause_index
                ].coordination_group
                == clause.coordination_group
            )
        ]
        return coordinated or list(range(len(result)))

    def region_candidates(slot: SemanticSlot) -> tuple[SemanticSlot, ...]:
        clause = clause_by_slot.get(id(slot))
        coordinated = tuple(
            region
            for region in grounded_region_slots
            if (
                clause is not None
                and clause_by_slot.get(id(region)) is not None
                and clause_by_slot[
                    id(region)
                ].coordination_group
                == clause.coordination_group
            )
        )
        return coordinated or grounded_region_slots

    def add_support(index: int, slot: SemanticSlot) -> None:
        group = result[index]
        result[index] = replace(
            group,
            normalized_start=min(
                group.normalized_start,
                slot.normalized_start,
            ),
            normalized_end=max(
                group.normalized_end,
                slot.normalized_end,
            ),
            supporting_slots=_ordered_unique_slots(
                (*group.supporting_slots, slot)
            ),
        )

    def add_error(
        slot: SemanticSlot,
        *,
        code: str,
        message: str,
        related: Iterable[SemanticSlot] = (),
    ) -> None:
        clause = clause_by_slot.get(id(slot))
        materialized = _ordered_unique_slots((slot, *tuple(related)))
        errors.append(
            ScopeError(
                code=code,
                message=message,
                clause_index=clause.index if clause is not None else None,
                normalized_start=min(
                    item.normalized_start for item in materialized
                ),
                normalized_end=max(
                    item.normalized_end for item in materialized
                ),
                slots=materialized,
            )
        )

    for slot in typed_slots:
        candidates = operation_candidates(slot)
        if slot.slot in {
            "region_support",
            "region_constraint",
            "region_anaphora",
        }:
            regions = region_candidates(slot)
            region_ids = {_region_id(region) for region in regions}
            if len(regions) == 0 or len(region_ids) != 1 or len(candidates) != 1:
                add_error(
                    slot,
                    code="unresolved_typed_region_reference",
                    message=(
                        "A typed region reference needs one explicit region "
                        "and one operation antecedent."
                    ),
                    related=(
                        *regions,
                        *(result[index].axis_slot for index in candidates),
                    ),
                )
                continue
            if (
                slot.slot == "region_constraint"
                and str(slot.value) not in region_ids
            ):
                add_error(
                    slot,
                    code="region_constraint_mismatch",
                    message=(
                        "A content constraint does not match the explicit "
                        "supported region."
                    ),
                    related=regions,
                )
                continue
            add_support(candidates[0], slot)
            continue

        if slot.slot in {"semantic_attribute", "return_relation"}:
            if len(candidates) != 1:
                add_error(
                    slot,
                    code=(
                        "ambiguous_semantic_attribute"
                        if slot.slot == "semantic_attribute"
                        else "unresolved_return_relation"
                    ),
                    message=(
                        "A semantic attribute or return relation needs one "
                        "explicit operation antecedent."
                    ),
                    related=tuple(
                        result[index].axis_slot for index in candidates
                    ),
                )
                continue
            add_support(candidates[0], slot)
            continue

        if slot.slot == "scope_quantifier":
            if not 1 <= len(candidates) <= 3:
                add_error(
                    slot,
                    code="unresolved_scope_quantifier",
                    message=(
                        "A distributive scope marker needs one to three "
                        "explicit operations in one coordination group."
                    ),
                    related=tuple(
                        result[index].axis_slot for index in candidates
                    ),
                )
                continue
            for index in candidates:
                add_support(index, slot)
            continue

        if slot.slot == "compound_marker":
            clause = clause_by_slot.get(id(slot))
            if clause is not None:
                candidates = [
                    index
                    for index, group in enumerate(result)
                    if (
                        clause_by_index.get(group.clause_index) is not None
                        and clause_by_index[
                            group.clause_index
                        ].coordination_group
                        == clause.coordination_group
                    )
                ]
            if not 2 <= len(candidates) <= 3:
                add_error(
                    slot,
                    code="incomplete_compound_marker",
                    message=(
                        "A compound marker needs two or three explicit "
                        "operations in one coordination group."
                    ),
                    related=tuple(
                        result[index].axis_slot for index in candidates
                    ),
                )
                continue
            for index in candidates:
                add_support(index, slot)

    for slot in quantity_all_slots:
        candidates = operation_candidates(slot)
        if not 1 <= len(candidates) <= 3:
            add_error(
                slot,
                code="unresolved_scope_quantifier",
                message=(
                    "A local all-quantity marker needs one to three explicit "
                    "operations in one coordination group."
                ),
                related=tuple(
                    result[index].axis_slot for index in candidates
                ),
            )
            continue
        for index in candidates:
            add_support(index, slot)

    return tuple(result), _deduplicate_errors(errors)


def _bind_persistent_observation_strength(
    groups: tuple[OperationSlotGroup, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[OperationSlotGroup, ...]:
    """Apply registry-declared strength to one persisted axis observation.

    The aspect marker is accepted only between a redundant axis noun and one
    same-axis observation alias.  This keeps ordinary declarative ``still``
    phrases fail-closed while allowing parameter additions to inherit the
    structure through registry metadata instead of sentence templates.
    """

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        candidates: list[tuple[SemanticSlot, object]] = []
        for slot in group.clause.slots:
            definition = registry.shared_concepts.get(
                str(slot.concept_id)
            )
            if (
                not slot.is_ambiguous
                and slot.namespace == "shared"
                and slot.slot == "clause_aspect"
                and definition is not None
                and definition.observation_strength is not None
            ):
                candidates.append((slot, definition))
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
        if (
            len(candidates) != 1
            or len(prior_axes) != 1
            or group.axis_slot.match_kind != "observation"
            or group.axis_slot.requested_direction not in {-1, 1}
            or group.direction_slots
            or group.strength_slots
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
        ):
            materialized.append(group)
            continue
        marker, definition = candidates[0]
        prior = prior_axes[0]
        if not (
            prior.normalized_end <= marker.normalized_start
            and marker.normalized_end
            <= group.axis_slot.normalized_start
        ):
            materialized.append(group)
            continue
        resolved_strength = _derived_shared_slot(
            marker,
            slot="strength",
            concept_id=str(marker.concept_id),
            value=str(definition.observation_strength),
            evidence_slot="resolved_strength",
        )
        materialized.append(
            replace(
                group,
                normalized_start=min(
                    group.normalized_start,
                    marker.normalized_start,
                ),
                normalized_end=max(
                    group.normalized_end,
                    marker.normalized_end,
                ),
                strength_slots=(resolved_strength,),
                supporting_slots=_ordered_unique_slots(
                    (*group.supporting_slots, marker)
                ),
            )
        )
    return tuple(materialized)


def _normalize_reinforcing_comparatives(
    groups: tuple[OperationSlotGroup, ...],
    *,
    registry: ParameterRegistry,
) -> tuple[OperationSlotGroup, ...]:
    """Treat trailing ``more`` as support for an explicit direct action.

    With an explicit axis noun, ``lower exposure some more`` inherits the
    polarity of ``lower``.  The comparative span remains grounded support
    evidence, but is not allowed to reverse or conflict with that direct verb.
    """

    materialized: list[OperationSlotGroup] = []
    for group in groups:
        if group.axis_slot.axis_role != "axis":
            materialized.append(group)
            continue
        direct = tuple(
            slot
            for slot in group.direction_slots
            if (
                slot.concept_id not in _COMPARATIVE_CONCEPTS
                and slot.value in {-1, 1}
            )
        )
        comparative = tuple(
            slot
            for slot in group.direction_slots
            if slot.concept_id in _COMPARATIVE_CONCEPTS
        )
        if (
            len({int(slot.value) for slot in direct}) != 1
            or not comparative
            or any(slot.value != 1 for slot in comparative)
        ):
            materialized.append(group)
            continue
        comparative_ids = {id(slot) for slot in comparative}
        preposed_strengths = {
            definition.preposed_strength
            for slot in comparative
            if (
                slot.normalized_end
                <= min(item.normalized_start for item in direct)
                and (
                    definition := registry.shared_concepts.get(
                        str(slot.concept_id)
                    )
                )
                is not None
                and definition.preposed_strength is not None
            )
        }
        derived_strength_slots: tuple[SemanticSlot, ...] = ()
        if len(preposed_strengths) == 1:
            strength = next(iter(preposed_strengths))
            assert strength is not None
            source = min(
                (
                    slot
                    for slot in comparative
                    if (
                        registry.shared_concepts.get(
                            str(slot.concept_id)
                        )
                        is not None
                        and registry.shared_concepts[
                            str(slot.concept_id)
                        ].preposed_strength
                        == strength
                    )
                ),
                key=lambda slot: slot.normalized_start,
            )
            source_interpretation = source.interpretations[0]
            derived_strength_slots = (
                SemanticSlot(
                    normalized_start=source.normalized_start,
                    normalized_end=source.normalized_end,
                    normalized_text=source.normalized_text,
                    evidence=replace(
                        source.evidence,
                        slot="resolved_strength",
                        concept_id=str(source.concept_id),
                    ),
                    interpretations=(
                        SlotInterpretation(
                            namespace="shared",
                            slot="strength",
                            concept_id=str(source.concept_id),
                            value=strength,
                            language=source.language,
                            priority=source_interpretation.priority,
                        ),
                    ),
                ),
            )
        materialized.append(
            replace(
                group,
                direction_slots=tuple(
                    slot
                    for slot in group.direction_slots
                    if id(slot) not in comparative_ids
                ),
                strength_slots=_ordered_unique_slots(
                    (*group.strength_slots, *derived_strength_slots)
                ),
                supporting_slots=_ordered_unique_slots(
                    (*group.supporting_slots, *comparative)
                ),
            )
        )
    return tuple(materialized)


def _axis_region_observation_errors(
    groups: tuple[OperationSlotGroup, ...],
    *,
    region_slots: tuple[SemanticSlot, ...],
) -> tuple[ScopeError, ...]:
    """Reject an unscoped region/axis overlap in an observation."""

    errors: list[ScopeError] = []
    for group in groups:
        if not _is_observation_group(group):
            continue
        for region_slot in region_slots:
            if (
                region_slot.namespace != "region"
                or _region_id(region_slot) != group.axis_id
                or not _slot_is_in_clause(region_slot, group.clause)
                or any(
                    slot.slot == "scope" and slot.value == "region"
                    for slot in group.clause.slots
                )
            ):
                continue
            errors.append(
                ScopeError(
                    code="ambiguous_axis_region_scope",
                    message=(
                        "An observed axis is also the unscoped local region; "
                        "the requested target is not unique."
                    ),
                    clause_index=group.clause_index,
                    normalized_start=min(
                        region_slot.normalized_start,
                        group.normalized_start,
                    ),
                    normalized_end=max(
                        region_slot.normalized_end,
                        group.normalized_end,
                    ),
                    slots=(region_slot, group.axis_slot),
                )
            )
    return _deduplicate_errors(errors)


def _multi_or_nested_region_candidate_errors(
    clauses: tuple[ClauseScope, ...],
    *,
    axis_slots: list[SemanticSlot],
    explicit_region_slots: list[SemanticSlot],
    registry: ParameterRegistry,
) -> tuple[ScopeError, ...]:
    """Reject two region candidates governed by one locative scope marker.

    A public axis may also be a legal region noun.  Once a locative marker
    follows an already named edit axis, later region-like nouns belong to the
    locative complement unless a new explicit operation establishes a separate
    role.  This covers coordinated and nested masks without naming any axis.
    """

    clause_by_slot = _map_slots_to_clauses(clauses)
    candidates = tuple(
        (
            *explicit_region_slots,
            *(
                slot
                for slot in axis_slots
                if str(slot.concept_id) in registry.regions
            ),
        )
    )
    errors: list[ScopeError] = []
    for clause in clauses:
        scope_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot == "scope" and slot.value == "region"
        )
        locative_targets = tuple(
            slot
            for slot in clause.slots
            if (
                slot.namespace == "region"
                or slot.slot == "region_context"
            )
        )
        clause_axes = tuple(
            slot for slot in clause.slots if slot.namespace == "axis"
        )
        has_observation_evidence = any(
            slot.slot in {"observation_modifier", "state_link"}
            for slot in clause.slots
        )
        immediate_following_clause = next(
            (
                candidate
                for candidate in clauses
                if candidate.index == clause.index + 1
            ),
            None,
        )
        preposed_locative_scope = bool(
            immediate_following_clause is not None
            and clause.boundary_after in {"comma", "topic"}
            and immediate_following_clause.coordination_group
            == clause.coordination_group
            and any(
                slot.namespace == "axis"
                for slot in immediate_following_clause.slots
            )
        )
        if (
            scope_slots
            and locative_targets
            and not clause_axes
            and not has_observation_evidence
            and not preposed_locative_scope
        ):
            errors.append(
                ScopeError(
                    code="orphan_locative_region_clause",
                    message=(
                        "A locative region clause has no edit operation to "
                        "scope."
                    ),
                    clause_index=clause.index,
                    normalized_start=min(
                        slot.normalized_start
                        for slot in (*scope_slots, *locative_targets)
                    ),
                    normalized_end=max(
                        slot.normalized_end
                        for slot in (*scope_slots, *locative_targets)
                    ),
                    slots=(*scope_slots, *locative_targets),
                )
            )
        for scope_slot in scope_slots:
            has_preceding_edit_axis = any(
                slot.namespace == "axis"
                and str(slot.concept_id) not in registry.regions
                and slot.normalized_end <= scope_slot.normalized_start
                for slot in clause.slots
            )
            if not has_preceding_edit_axis:
                continue
            scoped_candidates = tuple(
                slot
                for slot in candidates
                if (
                    slot.normalized_start >= scope_slot.normalized_end
                    and clause_by_slot.get(id(slot)) is not None
                    and clause_by_slot[id(slot)].coordination_group
                    == clause.coordination_group
                )
            )
            region_ids = {
                _region_id(slot) for slot in scoped_candidates
            }
            if len(region_ids) <= 1:
                continue
            errors.append(
                ScopeError(
                    code="multiple_or_nested_region_scope",
                    message=(
                        "One locative scope contains multiple or nested "
                        "region candidates."
                    ),
                    clause_index=clause.index,
                    normalized_start=scope_slot.normalized_start,
                    normalized_end=max(
                        slot.normalized_end
                        for slot in scoped_candidates
                    ),
                    slots=(scope_slot, *scoped_candidates),
                )
            )
    return _deduplicate_errors(errors)


def _typed_function_word_errors(
    clauses: tuple[ClauseScope, ...],
) -> tuple[ScopeError, ...]:
    """Validate typed determiners/possessives without lexical allowlists."""

    errors: list[ScopeError] = []
    blocking_slots = frozenset(
        {
            "direction",
            "operation",
            "numeric",
            "numeric_relation",
            "relation",
            "generic_action",
            "surface_action",
            "guard",
            "negation",
        }
    )
    possessive_blockers = frozenset(
        {"observation_modifier", "state_link"}
    )
    for clause in clauses:
        function_words = tuple(
            slot for slot in clause.slots if slot.slot == "function_word"
        )
        for function_word in function_words:
            if str(function_word.value) == "demonstrative":
                observation_axes = tuple(
                    slot
                    for slot in clause.slots
                    if (
                        slot.namespace == "axis"
                        and slot.normalized_start
                        >= function_word.normalized_end
                    )
                )
                has_observation_frame = bool(
                    len(observation_axes) == 1
                    and any(
                        slot.slot == "state_link"
                        and slot.normalized_start
                        >= function_word.normalized_end
                        for slot in clause.slots
                    )
                    and any(
                        slot.slot == "observation_modifier"
                        for slot in clause.slots
                    )
                    and not any(
                        slot.slot
                        in {
                            "clause_aspect",
                            "clause_modal",
                            "direction",
                            "operation",
                            "numeric",
                            "numeric_relation",
                            "relation",
                            "generic_action",
                            "surface_action",
                            "guard",
                            "negation",
                        }
                        for slot in clause.slots
                    )
                )
                if has_observation_frame:
                    continue
            nominal_candidates = tuple(
                slot
                for slot in clause.slots
                if (
                    slot.normalized_start >= function_word.normalized_end
                    and (
                        slot.namespace == "region"
                        or slot.slot == "region_context"
                        or slot.namespace == "axis"
                    )
                )
            )
            if not nominal_candidates:
                has_preceding_axis = any(
                    slot.namespace == "axis"
                    and slot.normalized_end
                    <= function_word.normalized_start
                    for slot in clause.slots
                )
                directional_action_candidates = tuple(
                    slot
                    for slot in clause.slots
                    if (
                        has_preceding_axis
                        and slot.slot == "direction"
                        and slot.normalized_start
                        >= function_word.normalized_end
                        and all(
                            between.slot in {"strength", "noise"}
                            for between in clause.slots
                            if (
                                between is not function_word
                                and between is not slot
                                and function_word.normalized_end
                                <= between.normalized_start
                                and between.normalized_end
                                <= slot.normalized_start
                            )
                        )
                    )
                )
                nominal_candidates = directional_action_candidates
            if not nominal_candidates:
                errors.append(
                    _slot_error(
                        "dangling_function_word",
                        "A typed function word has no nominal complement.",
                        function_word,
                        clause,
                    )
                )
                continue
            nominal = min(
                nominal_candidates,
                key=lambda slot: (
                    slot.normalized_start,
                    slot.normalized_end,
                ),
            )
            if (
                str(function_word.value) == "possessive"
                and nominal.namespace == "axis"
                and not any(
                    slot.slot == "observation_modifier"
                    for slot in clause.slots
                )
                and not any(
                    slot.normalized_end
                    <= function_word.normalized_start
                    and (
                        (
                            slot.namespace == "axis"
                            and slot.match_kind == "action"
                        )
                        or slot.slot
                        in {
                            "direction",
                            "operation",
                            "relation",
                            "generic_action",
                            "surface_action",
                            "request_marker",
                            "request_predicate",
                        }
                    )
                    for slot in clause.slots
                )
            ):
                errors.append(
                    ScopeError(
                        code="possessive_state_without_request",
                        message=(
                            "A possessive-led state phrase lacks an explicit "
                            "edit request or corrective observation."
                        ),
                        clause_index=clause.index,
                        normalized_start=function_word.normalized_start,
                        normalized_end=nominal.normalized_end,
                        slots=(function_word, nominal),
                    )
                )
            forbidden = set(blocking_slots)
            forbidden.add("observation_modifier")
            if str(function_word.value) == "possessive":
                forbidden.update(possessive_blockers)
            intervening = tuple(
                slot
                for slot in clause.slots
                if (
                    slot is not function_word
                    and slot is not nominal
                    and function_word.normalized_end
                    <= slot.normalized_start
                    and slot.normalized_end
                    <= nominal.normalized_start
                    and slot.slot in forbidden
                )
            )
            if intervening:
                errors.append(
                    ScopeError(
                        code="invalid_function_word_complement",
                        message=(
                            "A typed function word is separated from its "
                            "nominal complement by an edit operation."
                        ),
                        clause_index=clause.index,
                        normalized_start=function_word.normalized_start,
                        normalized_end=nominal.normalized_end,
                        slots=(function_word, *intervening, nominal),
                    )
                )
    return _deduplicate_errors(errors)


def _unsupported_numeric_unit_errors(
    clauses: tuple[ClauseScope, ...],
    *,
    groups: tuple[OperationSlotGroup, ...],
    registry: ParameterRegistry,
) -> tuple[ScopeError, ...]:
    """Keep numeric units typed until an axis declares a conversion."""

    del registry
    errors: list[ScopeError] = []
    group_clause_indexes = {group.clause_index for group in groups}
    for clause in clauses:
        unit_slots = tuple(
            slot for slot in clause.slots if slot.slot == "numeric_unit"
        )
        if not unit_slots:
            continue
        errors.append(
            ScopeError(
                code="unsupported_numeric_unit",
                message=(
                    "A numeric unit was supplied but no axis conversion "
                    "contract is registered."
                ),
                clause_index=clause.index,
                normalized_start=min(
                    slot.normalized_start for slot in unit_slots
                ),
                normalized_end=max(
                    slot.normalized_end for slot in unit_slots
                ),
                slots=unit_slots,
            )
        )
        if clause.index not in group_clause_indexes:
            continue
    return _deduplicate_errors(errors)


def _bind_clause_force_support(
    clauses: tuple[ClauseScope, ...],
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[tuple[OperationSlotGroup, ...], tuple[ScopeError, ...]]:
    """Classify request versus state force from typed, compositional evidence."""

    result = list(groups)
    errors: list[ScopeError] = []
    for clause in clauses:
        force_slots = tuple(
            slot
            for slot in clause.slots
            if slot.slot in _CLAUSE_FORCE_SLOTS
        )
        progressive_slots = tuple(
            slot
            for slot in clause.slots
            if (
                slot.slot == "direction"
                and slot.surface_form_kind == "progressive"
            )
        )
        if not force_slots and not progressive_slots:
            continue

        indexes = tuple(
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        )
        if not indexes:
            indexes = tuple(
                index
                for index, group in enumerate(result)
                if any(
                    _slot_is_in_clause(slot, clause)
                    for slot in _all_group_slots(group)
                )
            )
        if not indexes:
            indexes = tuple(
                index
                for index, group in enumerate(result)
                if (
                    group.clause.coordination_group
                    == clause.coordination_group
                )
            )
        local_groups = tuple(result[index] for index in indexes)
        modal_slots = tuple(
            slot for slot in force_slots if slot.slot == "clause_modal"
        )
        aspect_slots = tuple(
            slot for slot in force_slots if slot.slot == "clause_aspect"
        )
        subject_slots = tuple(
            slot for slot in force_slots if slot.slot == "clause_subject"
        )
        existential_slots = tuple(
            slot for slot in force_slots if slot.slot == "existential"
        )
        request_markers = tuple(
            slot for slot in force_slots if slot.slot == "request_marker"
        )
        request_predicates = tuple(
            slot for slot in force_slots if slot.slot == "request_predicate"
        )

        axis_slots = tuple(group.axis_slot for group in local_groups)
        anaphora_subjects = tuple(
            slot
            for slot in clause.slots
            if (
                slot.slot == "anaphora"
                and axis_slots
                and slot.normalized_end
                <= min(axis.normalized_start for axis in axis_slots)
                and not any(
                    predicate.normalized_end <= slot.normalized_start
                    for predicate in request_predicates
                )
            )
        )
        subject_like = _ordered_unique_slots(
            (*subject_slots, *anaphora_subjects)
        )
        observations = tuple(
            group
            for group in local_groups
            if (
                group.observation_modifier_slots
                or group.axis_slot.match_kind == "observation"
                or any(
                    slot.slot == "observation_modifier"
                    and _slot_is_in_clause(slot, clause)
                    for slot in group.supporting_slots
                )
            )
        )

        base_command_slots: list[SemanticSlot] = []
        preposed_progressive_slots: list[SemanticSlot] = []
        for group in local_groups:
            if group.axis_slot.match_kind == "action":
                base_command_slots.append(group.axis_slot)
            base_command_slots.extend(
                slot
                for slot in group.direction_slots
                if (
                    slot.concept_id not in _COMPARATIVE_CONCEPTS
                    and slot.surface_form_kind == "base"
                    and slot.normalized_end
                    <= group.axis_slot.normalized_start
                )
            )
            preposed_progressive_slots.extend(
                slot
                for slot in group.direction_slots
                if (
                    slot.surface_form_kind == "progressive"
                    and slot.normalized_end
                    <= group.axis_slot.normalized_start
                )
            )
            base_command_slots.extend(group.operation_slots)
            base_command_slots.extend(group.numeric_slots)
            base_command_slots.extend(group.numeric_relation_slots)
            base_command_slots.extend(group.relation_slots)
            base_command_slots.extend(group.surface_action_slots)
            base_command_slots.extend(group.action_attribute_slots)
            base_command_slots.extend(
                slot
                for slot in group.supporting_slots
                if slot.slot == "generic_action"
            )

        has_base_command = bool(base_command_slots)
        has_preposed_progressive = bool(preposed_progressive_slots)
        has_postposed_progressive = bool(
            len(preposed_progressive_slots) != len(progressive_slots)
        )
        has_requestable_descriptor = any(
            group.axis_slot.match_kind == "descriptor"
            and group.axis_slot.requested_direction in {-1, 1}
            for group in local_groups
        )
        subject_values = {str(slot.value) for slot in subject_slots}
        modal_values = {str(slot.value) for slot in modal_slots}
        predicate_values = {
            str(slot.value) for slot in request_predicates
        }
        modal_before_second_person = any(
            modal.normalized_end <= subject.normalized_start
            and str(subject.value) == "second_person"
            for modal in modal_slots
            for subject in subject_slots
        )
        modal_before_first_person = any(
            modal.normalized_end <= subject.normalized_start
            and str(subject.value) == "first_person"
            for modal in modal_slots
            for subject in subject_slots
        )
        subject_precedes_modal = any(
            subject.normalized_end <= modal.normalized_start
            for subject in subject_like
            for modal in modal_slots
        )

        desire_request = bool(
            "desire" in predicate_values
            and (
                not modal_slots
                or not subject_like
                or (
                    subject_values == {"first_person"}
                    and not anaphora_subjects
                    and modal_values == {"would"}
                )
            )
        )
        predicate_request = bool(
            desire_request
            or (
                "imperative" in predicate_values
                and (
                    not subject_like
                    or "second_person" in subject_values
                    or modal_before_first_person
                )
            )
        )
        plain_request = bool(
            not modal_slots
            and not subject_like
            and not has_postposed_progressive
            and (has_base_command or has_preposed_progressive)
        )
        modal_request = bool(
            modal_slots
            and not subject_precedes_modal
            and not progressive_slots
            and (
                (
                    modal_before_second_person
                    and (
                        has_base_command
                        or has_requestable_descriptor
                        or observations
                    )
                )
                or (
                    modal_before_first_person
                    and "imperative" in predicate_values
                    and has_base_command
                )
                or (
                    not subject_like
                    and not request_markers
                    and modal_values.issubset({"can", "could"})
                    and has_base_command
                )
            )
        )
        marked_request = bool(
            request_markers
            and not modal_slots
            and (
                has_base_command
                or has_preposed_progressive
                or has_requestable_descriptor
            )
        )
        explicit_request = bool(
            predicate_request
            or plain_request
            or modal_request
            or marked_request
        )

        clause_errors: list[ScopeError] = []

        def add_force_error(
            code: str,
            message: str,
            related: Iterable[SemanticSlot],
        ) -> None:
            materialized = _ordered_unique_slots(tuple(related))
            if not materialized:
                return
            clause_errors.append(
                ScopeError(
                    code=code,
                    message=message,
                    clause_index=clause.index,
                    normalized_start=min(
                        slot.normalized_start for slot in materialized
                    ),
                    normalized_end=max(
                        slot.normalized_end for slot in materialized
                    ),
                    slots=materialized,
                )
            )

        if modal_slots and not explicit_request:
            add_force_error(
                "clause_hypothetical_without_request",
                (
                    "A modal or hypothetical clause lacks a structurally "
                    "complete edit request."
                ),
                (
                    *modal_slots,
                    *subject_like,
                    *request_markers,
                    *progressive_slots,
                    *axis_slots,
                ),
            )
        if (
            progressive_slots
            and not explicit_request
            and (
                len(preposed_progressive_slots)
                != len(progressive_slots)
                or subject_like
                or aspect_slots
                or existential_slots
                or any(group.state_link_slots for group in local_groups)
            )
        ):
            add_force_error(
                "progressive_state_without_request",
                (
                    "A progressive surface form is a state report unless "
                    "typed request structure authorizes an edit."
                ),
                (
                    *progressive_slots,
                    *subject_like,
                    *aspect_slots,
                    *existential_slots,
                    *axis_slots,
                ),
            )
        if existential_slots and not observations:
            add_force_error(
                "existential_state_without_request",
                (
                    "An existential comparative or state report is not an "
                    "edit request."
                ),
                (*existential_slots, *axis_slots),
            )
        if (
            any(str(slot.value) == "already" for slot in aspect_slots)
            and not observations
            and not explicit_request
        ):
            add_force_error(
                "clause_state_without_request",
                (
                    "An already-marked state report lacks a corrective "
                    "observation or explicit request."
                ),
                (*aspect_slots, *axis_slots, *progressive_slots),
            )
        if (
            subject_like
            and not observations
            and not explicit_request
            and not modal_slots
        ):
            add_force_error(
                "subject_state_without_request",
                (
                    "A subject-led state clause lacks a typed request "
                    "predicate or command frame."
                ),
                (*subject_like, *axis_slots, *progressive_slots),
            )
        if (
            modal_slots
            and not local_groups
            and not clause_errors
        ):
            add_force_error(
                "clause_hypothetical_without_request",
                "A bare modal does not identify an edit operation.",
                (*modal_slots, *subject_like, *request_markers),
            )

        errors.extend(clause_errors)
        if clause_errors or not force_slots:
            continue
        for index in indexes:
            group = result[index]
            result[index] = replace(
                group,
                normalized_start=min(
                    group.normalized_start,
                    *(slot.normalized_start for slot in force_slots),
                ),
                normalized_end=max(
                    group.normalized_end,
                    *(slot.normalized_end for slot in force_slots),
                ),
                supporting_slots=_ordered_unique_slots(
                    (*group.supporting_slots, *force_slots)
                ),
                request_force_proven=explicit_request,
            )

    return tuple(result), _deduplicate_errors(errors)


def _declarative_state_errors(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[ScopeError, ...]:
    """A bare comparative state is evidence, not authorization to edit."""

    errors: list[ScopeError] = []
    for group in groups:
        if (
            not group.state_link_slots
            or group.request_force_proven
            or group.observation_modifier_slots
            or group.operation_slots
            or group.numeric_slots
            or group.numeric_relation_slots
            or group.relation_slots
            or group.axis_slot.match_kind not in {"axis", "descriptor"}
        ):
            continue
        has_directional_state = bool(
            group.direction_slots
            or group.fused_direction in {-1, 1}
        )
        if not has_directional_state:
            continue
        if (
            group.direction_slots
            and all(
                slot.normalized_end
                <= min(
                    state_link.normalized_start
                    for state_link in group.state_link_slots
                )
                for slot in group.direction_slots
            )
        ):
            continue
        errors.append(
            ScopeError(
                code="declarative_state_without_command",
                message=(
                    "A declarative comparative state lacks a corrective "
                    "operator or explicit edit action."
                ),
                clause_index=group.clause_index,
                normalized_start=group.normalized_start,
                normalized_end=group.normalized_end,
                slots=(
                    group.axis_slot,
                    *group.state_link_slots,
                    *group.direction_slots,
                ),
            )
        )
    return _deduplicate_errors(errors)


def _contradictory_descriptor_errors(
    clauses: tuple[ClauseScope, ...],
) -> tuple[ScopeError, ...]:
    """Reject opposite observed attributes inside one coordination group."""

    by_group_axis: dict[
        tuple[int, str],
        list[SemanticSlot],
    ] = {}
    for clause in clauses:
        if not any(
            slot.slot == "observation_modifier"
            for slot in clause.slots
        ):
            continue
        for slot in clause.slots:
            if (
                slot.namespace == "axis"
                and slot.match_kind in {"descriptor", "observation"}
                and slot.requested_direction in {-1, 1}
            ):
                by_group_axis.setdefault(
                    (clause.coordination_group, str(slot.concept_id)),
                    [],
                ).append(slot)
    errors: list[ScopeError] = []
    for slots in by_group_axis.values():
        directions = {
            int(slot.requested_direction)
            for slot in slots
            if slot.requested_direction in {-1, 1}
        }
        if len(directions) <= 1:
            continue
        errors.append(
            ScopeError(
                code="contradictory_observed_attribute",
                message=(
                    "One observed attribute has opposite directions in the "
                    "same coordinated request."
                ),
                normalized_start=min(
                    slot.normalized_start for slot in slots
                ),
                normalized_end=max(
                    slot.normalized_end for slot in slots
                ),
                slots=tuple(slots),
            )
        )
    return _deduplicate_errors(errors)


def _borrowed_modifier_clause_errors(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[ScopeError, ...]:
    """Allow shared directions only for a structurally bare axis clause."""

    errors: list[ScopeError] = []
    for group in groups:
        local_direction = tuple(
            slot
            for slot in group.direction_slots
            if _slot_is_in_clause(slot, group.clause)
        )
        borrowed_direction = tuple(
            slot
            for slot in group.direction_slots
            if not _slot_is_in_clause(slot, group.clause)
        )
        if (
            local_direction
            or not borrowed_direction
            or group.axis_slot.axis_role != "axis"
            or group.axis_slot.match_kind != "axis"
            or _is_observation_group(group)
        ):
            continue
        unexpected_local = tuple(
            slot
            for slot in group.clause.slots
            if (
                slot is not group.axis_slot
                and slot.normalized_end
                <= group.axis_slot.normalized_start
                and slot.slot
                in {
                    "clause_modal",
                    "clause_subject",
                    "function_word",
                    "generic_action",
                    "noise",
                    "request_marker",
                    "request_predicate",
                }
            )
        )
        if not unexpected_local:
            continue
        errors.append(
            ScopeError(
                code="borrowed_direction_in_incomplete_clause",
                message=(
                    "A coordinated direction cannot repair a non-bare, "
                    "directionless operation clause."
                ),
                clause_index=group.clause_index,
                normalized_start=group.clause.normalized_start,
                normalized_end=group.clause.normalized_end,
                slots=(
                    group.axis_slot,
                    *borrowed_direction,
                    *unexpected_local,
                ),
            )
        )
    return _deduplicate_errors(errors)


def _missing_connector_between_command_heads_errors(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[ScopeError, ...]:
    """Reject adjacent command heads that lack a typed connector.

    Clause partitioning is data-driven by connector slots.  Therefore two
    operation groups inside one clause may share a leading modifier, but a
    later action-form axis is a second command head and needs an explicit
    connector.  This prevents a trailing local command from silently
    capturing an earlier global operation.
    """

    by_clause: dict[int, list[OperationSlotGroup]] = {}
    for group in groups:
        by_clause.setdefault(group.clause_index, []).append(group)

    errors: list[ScopeError] = []
    for clause_groups in by_clause.values():
        ordered = sorted(
            clause_groups,
            key=lambda group: (
                group.axis_slot.normalized_start,
                group.axis_slot.normalized_end,
            ),
        )
        for index, group in enumerate(ordered[1:], start=1):
            if (
                group.axis_slot.match_kind != "action"
                or group.axis_slot.requested_direction not in {-1, 1}
            ):
                continue
            preceding = tuple(ordered[:index])
            errors.append(
                ScopeError(
                    code="missing_connector_between_command_heads",
                    message=(
                        "Adjacent command heads need an explicit typed "
                        "connector before they can share scope."
                    ),
                    clause_index=group.clause_index,
                    normalized_start=min(
                        item.axis_slot.normalized_start
                        for item in (*preceding, group)
                    ),
                    normalized_end=group.axis_slot.normalized_end,
                    slots=tuple(
                        item.axis_slot for item in (*preceding, group)
                    ),
                )
            )
    return _deduplicate_errors(errors)


def _single_region_attachment_errors(
    groups: tuple[OperationSlotGroup, ...],
    *,
    clauses: tuple[ClauseScope, ...],
    region_slots: tuple[SemanticSlot, ...],
) -> tuple[ScopeError, ...]:
    """Prove that one local region actually scopes every operation."""

    distinct_regions = {_region_id(slot) for slot in region_slots}
    if len(distinct_regions) != 1 or distinct_regions == {"all"}:
        return ()
    region_slot = region_slots[0]
    clause_by_slot = _map_slots_to_clauses(clauses)
    region_clause = clause_by_slot.get(id(region_slot))
    if region_clause is None or not groups:
        return ()

    group_axis_starts = [group.axis_slot.normalized_start for group in groups]
    group_axis_ends = [group.axis_slot.normalized_end for group in groups]
    group_coordination = {group.clause.coordination_group for group in groups}
    same_coordination = group_coordination == {
        region_clause.coordination_group
    }
    has_later_existential = any(
        clause.coordination_group == region_clause.coordination_group
        and clause.index >= region_clause.index
        and any(slot.slot == "existential" for slot in clause.slots)
        for clause in clauses
    )
    shared_nominal_prefix = bool(
        same_coordination
        and region_slot.normalized_end <= min(group_axis_starts)
        and not has_later_existential
    )
    scope_slots = tuple(
        slot
        for slot in region_clause.slots
        if slot.slot == "scope" and slot.value == "region"
    )
    shared_explicit_scope = bool(
        same_coordination
        and scope_slots
        and (
            max(slot.normalized_end for slot in scope_slots)
            <= min(group_axis_starts)
            or region_slot.normalized_start >= max(group_axis_ends)
        )
    )

    errors: list[ScopeError] = []
    for group in groups:
        locally_grounded = _slot_is_in_clause(
            region_slot,
            group.clause,
        )
        anaphoric = any(
            slot.slot == "anaphora"
            for slot in group.supporting_slots
        )
        earlier_grounded_support = any(
            clause_by_slot.get(id(slot)) is region_clause
            and region_clause.index < group.clause_index
            for slot in group.supporting_slots
        )
        if (
            locally_grounded
            or anaphoric
            or earlier_grounded_support
            or shared_nominal_prefix
            or shared_explicit_scope
        ):
            continue
        errors.append(
            ScopeError(
                code="unattached_local_region",
                message=(
                    "A local region is attached to only part of the request."
                ),
                clause_index=group.clause_index,
                normalized_start=min(
                    region_slot.normalized_start,
                    group.normalized_start,
                ),
                normalized_end=max(
                    region_slot.normalized_end,
                    group.normalized_end,
                ),
                slots=(region_slot, group.axis_slot),
            )
        )
    return _deduplicate_errors(errors)


def _local_cross_axis_observation_command_errors(
    groups: tuple[OperationSlotGroup, ...],
    *,
    clauses: tuple[ClauseScope, ...],
    region_slots: tuple[SemanticSlot, ...],
) -> tuple[ScopeError, ...]:
    """Reject a local observed axis plus unrelated explicit command."""

    del clauses
    distinct_regions = {_region_id(slot) for slot in region_slots}
    if len(distinct_regions) != 1 or distinct_regions == {"all"}:
        return ()
    observations = tuple(
        group for group in groups if _is_observation_group(group)
    )
    commands = tuple(
        group
        for group in groups
        if (
            not _is_observation_group(group)
            and (
                _command_group_direction(group) in {-1, 1}
                or group.numeric_slots
                or group.operation_slots
            )
        )
    )
    errors: list[ScopeError] = []
    for observation in observations:
        for command in commands:
            if (
                observation.axis_id == command.axis_id
                or any(
                    slot.slot == "anaphora"
                    for slot in command.supporting_slots
                )
            ):
                continue
            errors.append(
                ScopeError(
                    code="local_cross_axis_observation_command",
                    message=(
                        "A local observation and a different explicit "
                        "command need an explicit shared-scope contract."
                    ),
                    normalized_start=min(
                        observation.normalized_start,
                        command.normalized_start,
                    ),
                    normalized_end=max(
                        observation.normalized_end,
                        command.normalized_end,
                    ),
                    slots=(
                        observation.axis_slot,
                        command.axis_slot,
                    ),
                )
            )
    return _deduplicate_errors(errors)


def _orphan_leading_axis_observation_errors(
    extraction: SlotExtraction,
    *,
    clauses: tuple[ClauseScope, ...],
    registry: ParameterRegistry,
) -> tuple[ScopeError, ...]:
    """Reject an opted-in leading observation marker with no state clause."""

    if clauses:
        return ()
    errors: list[ScopeError] = []
    for slot in extraction.slots:
        definition = registry.shared_concepts.get(
            str(slot.concept_id)
        )
        if (
            slot.is_ambiguous
            or slot.namespace != "shared"
            or slot.slot != "conjunction"
            or definition is None
            or not definition.leading_axis_observation
        ):
            continue
        errors.append(
            ScopeError(
                code="dangling_leading_connector",
                message=(
                    "A leading observation marker needs one observable "
                    "axis state."
                ),
                normalized_start=slot.normalized_start,
                normalized_end=slot.normalized_end,
                slots=(slot,),
            )
        )
    return tuple(errors)


def _connector_completeness_errors(
    clauses: tuple[ClauseScope, ...],
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[ScopeError, ...]:
    """Reject leading, trailing, or one-sided semantic connectors."""

    if not clauses:
        return ()
    errors: list[ScopeError] = []
    connector_boundaries = {"conjunction", "contrastive", "disjunction"}
    bound_slot_ids = {
        id(slot)
        for group in groups
        for slot in _all_group_slots(group)
    }
    first = clauses[0]
    leading = tuple(
        slot
        for slot in first.connector_before
        if first.boundary_before in connector_boundaries
        and id(slot) not in bound_slot_ids
    )
    if leading:
        errors.append(
            ScopeError(
                code="dangling_leading_connector",
                message="A leading connector has no preceding semantic clause.",
                clause_index=first.index,
                normalized_start=min(slot.normalized_start for slot in leading),
                normalized_end=max(slot.normalized_end for slot in leading),
                slots=leading,
            )
        )
    last = clauses[-1]
    trailing = tuple(
        slot
        for slot in last.connector_after
        if last.boundary_after in connector_boundaries
    )
    if trailing:
        errors.append(
            ScopeError(
                code="dangling_trailing_connector",
                message="A trailing connector has no following semantic clause.",
                clause_index=last.index,
                normalized_start=min(slot.normalized_start for slot in trailing),
                normalized_end=max(slot.normalized_end for slot in trailing),
                slots=trailing,
            )
        )

    grouped_clauses = {
        clause.index
        for clause in clauses
        if (
            any(group.clause_index == clause.index for group in groups)
            or any(id(slot) in bound_slot_ids for slot in clause.slots)
        )
    }
    for left, right in zip(clauses, clauses[1:]):
        boundary = right.boundary_before or left.boundary_after
        if boundary not in connector_boundaries:
            continue
        if left.index in grouped_clauses and right.index in grouped_clauses:
            continue
        connectors = _ordered_unique_slots(
            (*left.connector_after, *right.connector_before)
        )
        if not connectors:
            continue
        errors.append(
            ScopeError(
                code="incomplete_coordinated_clause",
                message=(
                    "Both sides of a semantic connector need a grounded "
                    "operation."
                ),
                clause_index=right.index,
                normalized_start=min(
                    left.normalized_start,
                    *(slot.normalized_start for slot in connectors),
                ),
                normalized_end=max(
                    right.normalized_end,
                    *(slot.normalized_end for slot in connectors),
                ),
                slots=connectors,
            )
        )
    return _deduplicate_errors(errors)


def _bind_completed_event_context(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
) -> tuple[OperationSlotGroup, ...]:
    """Demote one completed action before a same-axis follow-up command.

    The temporal aspect is a typed event boundary: one action before ``after``
    supplies context, while one later same-axis descriptor/action carrying a
    continuation marker supplies the actual edit.  Multiple candidates,
    different axes/regions, observations, or conflicting polarity remain
    untouched and are rejected by the duplicate-operation gate.
    """

    result = list(groups)
    removed: set[int] = set()
    replacements: dict[int, OperationSlotGroup] = {}
    for clause in clauses:
        after_aspects = tuple(
            slot
            for slot in clause.slots
            if slot.slot == "clause_aspect" and slot.value == "after"
        )
        indexes = tuple(
            index
            for index, group in enumerate(result)
            if group.clause_index == clause.index
        )
        if len(after_aspects) != 1 or len(indexes) != 2:
            continue
        after = after_aspects[0]
        ordered = tuple(
            sorted(indexes, key=lambda index: result[index].axis_slot.normalized_start)
        )
        prior_index, command_index = ordered
        prior = result[prior_index]
        command = result[command_index]
        continuation = tuple(
            slot
            for slot in command.relation_slots
            if slot.value == "continue"
        )
        if (
            prior.axis_slot.match_kind != "action"
            or command.axis_slot.match_kind not in {"action", "descriptor"}
            or prior.axis_id != command.axis_id
            or len(continuation) != 1
            or not command.strength_slots
            or prior.resolved_direction != command.resolved_direction
            or prior.resolved_direction not in {-1, 1}
            or prior.region_slot is not command.region_slot
            or prior.observation_modifier_slots
            or command.observation_modifier_slots
            or prior.state_link_slots
            or command.state_link_slots
            or prior.numeric_slots
            or command.numeric_slots
            or prior.operation_slots
            or command.operation_slots
            or prior.numeric_relation_slots
            or command.numeric_relation_slots
            or prior.guard_slots
            or command.guard_slots
            or not (
                prior.axis_slot.normalized_end <= after.normalized_start
                and after.normalized_end
                <= command.axis_slot.normalized_start
            )
        ):
            continue
        replacements[command_index] = replace(
            command,
            normalized_start=min(
                prior.normalized_start,
                command.normalized_start,
                after.normalized_start,
            ),
            normalized_end=max(
                prior.normalized_end,
                command.normalized_end,
                after.normalized_end,
            ),
            supporting_slots=_ordered_unique_slots(
                (
                    *command.supporting_slots,
                    prior.axis_slot,
                    after,
                )
            ),
        )
        removed.add(prior_index)
    return tuple(
        replacements.get(index, group)
        for index, group in enumerate(result)
        if index not in removed
    )


def _bind_post_event_still_observations(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    guard_slots: tuple[SemanticSlot, ...],
) -> tuple[tuple[OperationSlotGroup, ...], set[int]]:
    """Disambiguate a dual-role ``still/or`` marker from typed structure.

    A marker may mean temporal persistence only between one completed
    axis-local action and one immediately following observation of the same
    axis.  Any alternative coordinate, missing completion aspect, different
    axis, or polarity mismatch leaves it as an authoritative disjunction.
    """

    result = list(groups)
    consumed: set[int] = set()
    for guard in guard_slots:
        if (
            guard.slot != "guard"
            or guard.concept_id != "disjunction_or_still"
            or guard.value != "or"
        ):
            continue
        adjacent = tuple(
            (left, right)
            for left, right in zip(clauses, clauses[1:])
            if (
                guard in left.connector_after
                and guard in right.connector_before
                and left.boundary_after == "disjunction"
                and right.boundary_before == "disjunction"
                and right.boundary_after != "disjunction"
            )
        )
        if len(adjacent) != 1:
            continue
        left, right = adjacent[0]
        local_guards = tuple(
            slot
            for slot in guard_slots
            if (
                left.normalized_start
                <= slot.normalized_start
                and slot.normalized_end <= right.normalized_end
            )
        )
        left_indexes = tuple(
            index
            for index, group in enumerate(result)
            if group.clause_index == left.index
        )
        right_indexes = tuple(
            index
            for index, group in enumerate(result)
            if group.clause_index == right.index
        )
        if (
            len(local_guards) != 1
            or len(left_indexes) != 1
            or len(right_indexes) != 1
        ):
            continue
        left_index = left_indexes[0]
        command = result[left_index]
        observation = result[right_indexes[0]]
        after_aspects = tuple(
            slot
            for slot in left.slots
            if (
                slot.slot == "clause_aspect"
                and slot.value == "after"
                and command.axis_slot.normalized_end
                <= slot.normalized_start
            )
        )
        command_direction = _command_group_direction(command)
        observation_direction = _observation_correction_direction(
            observation
        )
        if (
            len(after_aspects) != 1
            or command.axis_slot.match_kind != "action"
            or not _is_observation_group(observation)
            or command.axis_id != observation.axis_id
            or command_direction not in {-1, 1}
            or observation_direction != command_direction
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
            continue
        result[left_index] = replace(
            command,
            normalized_start=min(
                command.normalized_start,
                guard.normalized_start,
            ),
            normalized_end=max(
                command.normalized_end,
                guard.normalized_end,
            ),
            supporting_slots=_ordered_unique_slots(
                (*command.supporting_slots, guard)
            ),
        )
        consumed.add(id(guard))
    return tuple(result), consumed


def _bind_persistent_still_observation_guards(
    groups: tuple[OperationSlotGroup, ...],
    clauses: tuple[ClauseScope, ...],
    guard_slots: tuple[SemanticSlot, ...],
    *,
    extraction: SlotExtraction,
) -> tuple[tuple[OperationSlotGroup, ...], set[int]]:
    """Bind one dual-role marker to its structurally proven observation."""

    result = list(groups)
    consumed: set[int] = set()
    for guard in guard_slots:
        if not _guard_is_typed_persistent_observation(extraction, guard):
            continue
        candidates = tuple(
            index
            for index, group in enumerate(result)
            if (
                group.clause.normalized_start
                <= guard.normalized_start
                and guard.normalized_end
                <= group.clause.normalized_end
                and guard.normalized_end
                <= group.axis_slot.normalized_start
            )
        )
        if len(candidates) != 1:
            continue
        index = candidates[0]
        observation = result[index]
        if (
            not _is_observation_group(observation)
            or _observation_correction_direction(observation) not in {-1, 1}
            or observation.direction_slots
            or observation.numeric_slots
            or observation.operation_slots
            or observation.numeric_relation_slots
            or observation.relation_slots
            or observation.guard_slots
            or observation.ambiguous_slots
            or observation.action_attribute_slots
            or observation.surface_action_slots
        ):
            continue
        result[index] = replace(
            observation,
            normalized_start=min(
                observation.normalized_start,
                guard.normalized_start,
            ),
            normalized_end=max(
                observation.normalized_end,
                guard.normalized_end,
            ),
            supporting_slots=_ordered_unique_slots(
                (*observation.supporting_slots, guard)
            ),
        )
        consumed.add(id(guard))
    return tuple(result), consumed


def _fuse_compatible_same_axis_groups(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[tuple[OperationSlotGroup, ...], tuple[ScopeError, ...]]:
    """Fuse redundant mentions without hiding real duplicate commands.

    Natural prompts often name an axis and then describe or act on it
    (``colors more vivid``), or state a problem and give the matching remedy
    (``too cool; warm it up``).  Both forms describe one requested operation.
    This routine is axis-neutral: it relies only on match roles, local scope,
    and correction polarity.

    Two command-bearing occurrences are deliberately left separate so the
    existing duplicate/conflict gate rejects them atomically.
    """

    indexed: dict[str, list[tuple[int, OperationSlotGroup]]] = {}
    for index, group in enumerate(groups):
        indexed.setdefault(group.axis_id, []).append((index, group))

    replacements: dict[int, OperationSlotGroup] = {}
    removed: set[int] = set()
    errors: list[ScopeError] = []
    for duplicates in indexed.values():
        if len(duplicates) < 2:
            continue
        fusion, fusion_error = _fuse_axis_duplicates(
            tuple(group for _, group in duplicates)
        )
        if fusion_error is not None:
            errors.append(fusion_error)
            continue
        if fusion is None:
            continue
        first_index = min(index for index, _ in duplicates)
        replacements[first_index] = fusion
        removed.update(
            index for index, _ in duplicates if index != first_index
        )

    materialized = tuple(
        replacements.get(index, group)
        for index, group in enumerate(groups)
        if index not in removed
    )
    return materialized, _deduplicate_errors(errors)


def _fuse_axis_duplicates(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[OperationSlotGroup | None, ScopeError | None]:
    contiguous_descriptor_conflict = (
        _contiguous_descriptor_direction_conflict(groups)
    )
    if contiguous_descriptor_conflict is not None:
        return None, contiguous_descriptor_conflict

    reduplicated_observation = _fuse_reduplicated_descriptor_groups(groups)
    if reduplicated_observation is not None:
        return reduplicated_observation, None

    noun_support_ids = {
        id(group)
        for group in groups
        if (
            group.axis_slot.match_kind == "axis"
            and any(
                other is not group
                and other.clause_index == group.clause_index
                and other.axis_slot.match_kind != "axis"
                for other in groups
            )
        )
    }
    observation_groups = tuple(
        group
        for group in groups
        if id(group) not in noun_support_ids
        and _is_observation_group(group)
    )
    explicit_commands = tuple(
        group
        for group in groups
        if (
            id(group) not in noun_support_ids
            and group not in observation_groups
            and group.axis_slot.match_kind == "axis"
            and _is_local_command_group(group)
        )
    )
    restatement_ids: set[int] = set()
    for group in groups:
        if (
            id(group) in noun_support_ids
            or group in observation_groups
            or group.axis_slot.match_kind != "descriptor"
            or _has_local_operation_signal(group)
        ):
            continue
        compatible = tuple(
            command
            for command in explicit_commands
            if (
                _groups_can_restate(group, command)
                and _restatement_direction(group)
                == _command_group_direction(command)
            )
        )
        if len(compatible) == 1:
            restatement_ids.add(id(group))
    command_groups = tuple(
        group
        for group in groups
        if id(group) not in noun_support_ids
        and id(group) not in restatement_ids
        if _is_local_command_group(group)
        and group not in observation_groups
    )
    support_groups = tuple(
        group
        for group in groups
        if (
            id(group) in noun_support_ids
            or id(group) in restatement_ids
            or (
                group not in observation_groups
                and group not in command_groups
            )
        )
    )

    primary: OperationSlotGroup | None = None
    evidence_only: tuple[OperationSlotGroup, ...] = ()
    ignored_support_slot_ids: set[int] = set()

    if len(command_groups) == 1 and observation_groups:
        observation_directions = {
            _observation_correction_direction(group)
            for group in observation_groups
        }
        command_direction = _command_group_direction(command_groups[0])
        contrastive_upper_bound = (
            len(observation_groups) == 1
            and _is_contrastive_upper_bound_command_pair(
                command_groups[0],
                observation_groups[0],
            )
        )
        has_consumed_bound_marker = any(
            slot.slot in {"negation", "negated_comparative"}
            for group in observation_groups
            for slot in group.supporting_slots
        )
        if contrastive_upper_bound:
            primary = command_groups[0]
            evidence_only = (*observation_groups, *support_groups)
            ignored_support_slot_ids.update(
                id(slot)
                for slot in observation_groups[0].strength_slots
                if slot.evidence.slot == "resolved_strength"
            )
        elif (
            has_consumed_bound_marker
            or
            None in observation_directions
            or len(observation_directions) != 1
            or command_direction is None
            or next(iter(observation_directions)) != command_direction
        ):
            return None, None
        else:
            primary = command_groups[0]
            evidence_only = (*observation_groups, *support_groups)
    elif len(command_groups) == 1 and not observation_groups:
        if not all(
            _same_clause(group, command_groups[0])
            or id(group) in restatement_ids
            for group in support_groups
        ):
            return None, None
        primary = command_groups[0]
        evidence_only = support_groups
    elif not command_groups and observation_groups:
        observation_directions = {
            _observation_correction_direction(group)
            for group in observation_groups
        }
        if None in observation_directions or len(observation_directions) != 1:
            return None, None
        primary = observation_groups[0]
        if not all(
            _same_clause(group, primary)
            for group in support_groups
        ):
            return None, None
        evidence_only = (*observation_groups[1:], *support_groups)
    else:
        return None, None

    if not evidence_only:
        return None, None
    if any(group.ambiguous_slots for group in evidence_only):
        return None, None
    if (
        any(group.guard_slots for group in evidence_only)
        and not _groups_share_one_preservation_guard(
            (primary, *evidence_only)
        )
    ):
        return None, None
    attribute_axis_slots = _ordered_unique_slots(
        slot
        for group in (primary, *evidence_only)
        for slot in group.attribute_axis_slots
    )
    action_attribute_slots = _ordered_unique_slots(
        slot
        for group in (primary, *evidence_only)
        for slot in group.action_attribute_slots
    )
    attribute_slot_ids = {id(slot) for slot in attribute_axis_slots}
    attribute_slot_ids.update(id(slot) for slot in action_attribute_slots)
    primary_slot_ids = {
        id(slot) for slot in _all_group_slots(primary)
    }
    supporting_slots = _ordered_unique_slots(
        (
            *primary.supporting_slots,
            *(
                slot
                for group in evidence_only
                for slot in _all_group_slots(group)
                if (
                    id(slot) not in primary_slot_ids
                    and id(slot) not in attribute_slot_ids
                    and id(slot) not in ignored_support_slot_ids
                )
            ),
        )
    )
    all_groups = (primary, *evidence_only)
    return (
        OperationSlotGroup(
            axis_slot=primary.axis_slot,
            clause_index=primary.clause_index,
            clause=primary.clause,
            normalized_start=min(
                group.normalized_start for group in all_groups
            ),
            normalized_end=max(group.normalized_end for group in all_groups),
            region_slot=primary.region_slot,
            direction_slots=primary.direction_slots,
            strength_slots=primary.strength_slots,
            numeric_slots=primary.numeric_slots,
            operation_slots=primary.operation_slots,
            numeric_relation_slots=primary.numeric_relation_slots,
            relation_slots=primary.relation_slots,
            observation_modifier_slots=primary.observation_modifier_slots,
            state_link_slots=primary.state_link_slots,
            guard_slots=primary.guard_slots,
            ambiguous_slots=primary.ambiguous_slots,
            attribute_axis_slots=attribute_axis_slots,
            action_attribute_slots=action_attribute_slots,
            surface_action_slots=primary.surface_action_slots,
            supporting_slots=supporting_slots,
        ),
        None,
    )


def _is_contrastive_upper_bound_command_pair(
    command: OperationSlotGroup,
    observation: OperationSlotGroup,
) -> bool:
    """Prove ``increase, but not too high`` as one bounded command."""

    command_direction = _command_group_direction(command)
    correction_direction = _observation_correction_direction(observation)
    observed_direction = observation.axis_slot.requested_direction
    observation_values = {
        str(slot.value) for slot in observation.observation_modifier_slots
    }
    consumed_negations = tuple(
        slot
        for slot in observation.supporting_slots
        if slot.slot == "negation" and slot.value is True
    )
    consumed_comparatives = tuple(
        slot
        for slot in observation.supporting_slots
        if (
            slot.slot == "negated_comparative"
            and slot.value == "less"
            and slot.concept_id == "negated_comparative_less"
        )
    )
    marker_is_exact = bool(
        (
            len(consumed_negations) == 1
            and not consumed_comparatives
        )
        or (
            not consumed_negations
            and len(consumed_comparatives) == 1
            and command.axis_slot.match_kind == "descriptor"
            and len(command.strength_slots) == 1
            and str(command.strength_slots[0].value) == "subtle"
            and observation.axis_slot.match_kind == "descriptor"
            and not observation.strength_slots
            and len(observation.observation_modifier_slots) == 1
            and observation.observation_modifier_slots[0].value == "too"
            and observation.observation_modifier_slots[0].concept_id
            == "negated_upper_bound"
            and observation.observation_modifier_slots[0].evidence.slot
            == "resolved_observation"
            and observation.observation_modifier_slots[0].evidence.start
            == consumed_comparatives[0].evidence.start
            and observation.observation_modifier_slots[0].evidence.end
            == consumed_comparatives[0].evidence.end
            and observation.observation_modifier_slots[0].evidence.raw_text
            == consumed_comparatives[0].evidence.raw_text
        )
    )
    return bool(
        command.axis_id == observation.axis_id
        and command_direction in {-1, 1}
        and correction_direction == -command_direction
        and observed_direction == command_direction
        and abs(command.clause_index - observation.clause_index) == 1
        and (
            command.clause.boundary_after == "contrastive"
            or observation.clause.boundary_before == "contrastive"
        )
        and observation_values
        and observation_values.issubset({"too", "too_much"})
        and marker_is_exact
        and not command.numeric_slots
        and not command.operation_slots
        and not command.numeric_relation_slots
        and command.region_slot is observation.region_slot
        and not observation.guard_slots
        and not observation.direction_slots
        and not observation.numeric_slots
        and not observation.operation_slots
        and not observation.numeric_relation_slots
        and not observation.relation_slots
        and not observation.state_link_slots
        and not observation.ambiguous_slots
        and not observation.attribute_axis_slots
        and not observation.action_attribute_slots
        and not observation.surface_action_slots
        and all(
            str(slot.value) == "subtle"
            for slot in observation.strength_slots
        )
    )


def _contiguous_descriptor_direction_conflict(
    groups: tuple[OperationSlotGroup, ...],
) -> ScopeError | None:
    """Reject a no-boundary descriptor run with opposite polarities."""

    if len(groups) < 2:
        return None
    ordered = tuple(
        sorted(
            groups,
            key=lambda group: (
                group.axis_slot.normalized_start,
                group.axis_slot.normalized_end,
            ),
        )
    )
    if any(
        group.clause_index != ordered[0].clause_index
        or group.clause is not ordered[0].clause
        or group.axis_slot.match_kind != "descriptor"
        or group.axis_slot.requested_direction not in {-1, 1}
        for group in ordered
    ):
        return None
    if any(
        left.axis_slot.normalized_end
        != right.axis_slot.normalized_start
        or left.axis_slot.evidence.end
        != right.axis_slot.evidence.start
        for left, right in zip(ordered, ordered[1:])
    ):
        return None
    directions = {
        int(group.axis_slot.requested_direction)
        for group in ordered
    }
    if len(directions) <= 1:
        return None
    slots = tuple(group.axis_slot for group in ordered)
    return ScopeError(
        code="conflicting_axis_direction",
        message=(
            "A contiguous descriptor run has opposite meanings for the same "
            "axis."
        ),
        clause_index=ordered[0].clause_index,
        normalized_start=min(slot.normalized_start for slot in slots),
        normalized_end=max(slot.normalized_end for slot in slots),
        slots=slots,
    )


def _fuse_reduplicated_descriptor_groups(
    groups: tuple[OperationSlotGroup, ...],
) -> OperationSlotGroup | None:
    """Collapse one contiguous repeated descriptor into an observation.

    Languages may express a mild observed state by immediately repeating an
    adjective (for example, a no-space CJK reduplication).  This rule is
    lexical and axis-neutral: every occurrence must be the exact same
    registered descriptor, polarity, clause, and region, with touching source
    spans and no independent command/modifier material.  Repetition across
    whitespace, connectors, axes, or directions therefore remains subject to
    the normal duplicate/conflict gates.
    """

    if len(groups) < 2:
        return None
    ordered = tuple(
        sorted(
            groups,
            key=lambda group: (
                group.axis_slot.normalized_start,
                group.axis_slot.normalized_end,
            ),
        )
    )
    first = ordered[0]
    first_axis = first.axis_slot
    interpretation = first_axis.interpretation
    if (
        interpretation is None
        or first_axis.namespace != "axis"
        or first_axis.match_kind != "descriptor"
        or first_axis.requested_direction not in {-1, 1}
        or first_axis.surface_form_kind != "base"
    ):
        return None

    def has_independent_material(group: OperationSlotGroup) -> bool:
        return bool(
            group.direction_slots
            or group.strength_slots
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
            or group.supporting_slots
        )

    if any(has_independent_material(group) for group in ordered):
        return None
    if any(
        (
            group.clause_index != first.clause_index
            or group.clause is not first.clause
            or group.axis_slot.namespace != "axis"
            or group.axis_slot.concept_id != first_axis.concept_id
            or group.axis_slot.normalized_text != first_axis.normalized_text
            or group.axis_slot.match_kind != "descriptor"
            or group.axis_slot.requested_direction
            != first_axis.requested_direction
            or group.axis_slot.surface_form_kind != "base"
            or _region_id(group.region_slot)
            != _region_id(first.region_slot)
            if group.region_slot is not None
            and first.region_slot is not None
            else group.region_slot is not first.region_slot
        )
        for group in ordered
    ):
        return None
    if any(
        left.axis_slot.normalized_end
        != right.axis_slot.normalized_start
        or left.axis_slot.evidence.end
        != right.axis_slot.evidence.start
        for left, right in zip(ordered, ordered[1:])
    ):
        return None

    return replace(
        first,
        normalized_start=min(group.normalized_start for group in ordered),
        normalized_end=max(group.normalized_end for group in ordered),
        observation_attribute_direction_override=int(
            first_axis.requested_direction
        ),
        supporting_slots=_ordered_unique_slots(
            (
                *first.supporting_slots,
                *(
                    group.axis_slot
                    for group in ordered[1:]
                ),
            )
        ),
    )


def _groups_share_one_preservation_guard(
    groups: tuple[OperationSlotGroup, ...],
) -> bool:
    guards = tuple(
        slot
        for group in groups
        for slot in group.guard_slots
    )
    return bool(
        guards
        and len({id(slot) for slot in guards}) == 1
        and all(
            slot.slot == "guard"
            and slot.concept_id == "preservation"
            and slot.value == "preserve"
            for slot in guards
        )
        and all(group.guard_slots for group in groups)
    )


def _is_observation_group(group: OperationSlotGroup) -> bool:
    return bool(
        group.axis_slot.match_kind == "observation"
        or group.observation_modifier_slots
        or group.state_link_slots
    )


def _is_local_command_group(group: OperationSlotGroup) -> bool:
    if (
        group.axis_slot.match_kind in {"action", "descriptor"}
        or group.action_attribute_slots
        or group.surface_action_slots
    ):
        return True
    local_slots = (
        *group.direction_slots,
        *group.numeric_slots,
        *group.operation_slots,
        *group.numeric_relation_slots,
        *group.relation_slots,
    )
    return any(_slot_is_in_clause(slot, group.clause) for slot in local_slots)


def _has_local_operation_signal(group: OperationSlotGroup) -> bool:
    return bool(
        group.action_attribute_slots
        or group.surface_action_slots
        or any(
            _slot_is_in_clause(slot, group.clause)
            for slot in (
                *group.direction_slots,
                *group.numeric_slots,
                *group.operation_slots,
                *group.numeric_relation_slots,
                *group.relation_slots,
            )
        )
    )


def _restatement_direction(group: OperationSlotGroup) -> int | None:
    direction = group.axis_slot.requested_direction
    if direction not in {-1, 1}:
        return None
    if any(
        slot.slot in {"negation", "negated_comparative"}
        for slot in group.supporting_slots
    ):
        return -int(direction)
    return int(direction)


def _groups_can_restate(
    restatement: OperationSlotGroup,
    command: OperationSlotGroup,
) -> bool:
    if abs(restatement.clause_index - command.clause_index) > 1:
        return False
    earlier, later = (
        (restatement.clause, command.clause)
        if restatement.clause_index <= command.clause_index
        else (command.clause, restatement.clause)
    )
    return (
        earlier.boundary_after not in {"contrastive", "disjunction"}
        and later.boundary_before not in {"contrastive", "disjunction"}
    )


def _slot_is_in_clause(slot: SemanticSlot, clause: ClauseScope) -> bool:
    return (
        clause.normalized_start <= slot.normalized_start
        and slot.normalized_end <= clause.normalized_end
    )


def _same_clause(
    left: OperationSlotGroup,
    right: OperationSlotGroup,
) -> bool:
    return left.clause_index == right.clause_index


def _command_group_direction(group: OperationSlotGroup) -> int | None:
    direct_directions = {
        int(slot.value) * group.direction_multiplier
        for slot in group.direction_slots
        if (
            slot.value in {-1, 1}
            and slot.concept_id
            not in {"comparative_more", "comparative_less"}
        )
    }
    comparative_directions = {
        int(slot.value)
        for slot in group.direction_slots
        if (
            slot.value in {-1, 1}
            and slot.concept_id
            in {"comparative_more", "comparative_less"}
        )
    }
    base_directions = {
        direction
        for direction in (
            group.fused_direction,
            group.action_attribute_direction,
            group.surface_action_direction,
        )
        if direction in {-1, 1}
    }
    if len(base_directions) > 1 or len(comparative_directions) > 1:
        return None
    if (
        group.axis_slot.match_kind == "action"
        and group.fused_direction in {-1, 1}
        and not group.observation_modifier_slots
        and not group.state_link_slots
    ):
        if len(direct_directions) > 1:
            return None
        direction = int(group.fused_direction)
        if comparative_directions:
            direction *= next(iter(comparative_directions))
        if direct_directions:
            direct_direction = next(iter(direct_directions))
            if direct_direction != direction:
                direction = direct_direction
        return direction
    if base_directions:
        direction = next(iter(base_directions))
        if comparative_directions:
            direction *= next(iter(comparative_directions))
        direct_directions.add(direction)
    else:
        direct_directions.update(
            direction * group.direction_multiplier
            for direction in comparative_directions
        )
    return (
        next(iter(direct_directions))
        if len(direct_directions) == 1
        else None
    )


def _observation_correction_direction(
    group: OperationSlotGroup,
) -> int | None:
    axis = group.axis_slot
    modifiers = {
        str(slot.value) for slot in group.observation_modifier_slots
    }
    if len(modifiers) > 1:
        return None
    modifier = next(iter(modifiers), None)
    attribute_direction = group.observation_attribute_direction

    if axis.match_kind == "observation" and axis.requested_direction in {-1, 1}:
        return int(axis.requested_direction)
    if (
        axis.match_kind in {"action", "descriptor"}
        and axis.requested_direction in {-1, 1}
    ):
        base = int(axis.requested_direction)
        return base if modifier == "not_enough" else -base
    if axis.axis_role == "axis":
        if attribute_direction is not None:
            return (
                attribute_direction
                if modifier == "not_enough"
                else -attribute_direction
            )
        local_directions = {
            int(slot.value) * group.direction_multiplier
            for slot in group.direction_slots
            if (
                slot.value in {-1, 1}
                and _slot_is_in_clause(slot, group.clause)
            )
        }
        source_direction = (
            next(iter(local_directions))
            if len(local_directions) == 1
            else None
        )
        if modifier == "not_enough":
            return group.direction_multiplier
        if modifier in {"too", "too_much", "mild"}:
            return -(source_direction or group.direction_multiplier)
    return None


def _all_group_slots(
    group: OperationSlotGroup,
) -> tuple[SemanticSlot, ...]:
    return _ordered_unique_slots(
        (
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
    )


def _operation_group_errors(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[ScopeError, ...]:
    errors: list[ScopeError] = []
    for group in groups:
        mismatched_remedy = _mismatched_axis_observation_remedy(
            group
        )
        if mismatched_remedy:
            errors.append(
                ScopeError(
                    code="conflicting_direction_scope",
                    message=(
                        "The corrective direction disagrees with the "
                        "observed axis state."
                    ),
                    clause_index=group.clause_index,
                    normalized_start=group.normalized_start,
                    normalized_end=group.normalized_end,
                    slots=(
                        group.axis_slot,
                        *mismatched_remedy,
                    ),
                )
            )
            errors.extend(_modifier_value_errors(group))
            continue
        direction_values = set(group.canonical_explicit_directions)
        if group.fused_direction is not None:
            direction_values.add(group.fused_direction)
        if group.action_attribute_direction is not None:
            direction_values.add(group.action_attribute_direction)
        if group.surface_action_direction is not None:
            direction_values.add(group.surface_action_direction)
        if len(direction_values) > 1:
            action_composition = bool(
                group.axis_slot.match_kind == "action"
                and group.fused_direction in {-1, 1}
                and any(
                    slot.concept_id not in _COMPARATIVE_CONCEPTS
                    for slot in group.direction_slots
                )
                and _command_group_direction(group) in {-1, 1}
            )
            comparative_macro = bool(
                group.axis_slot.match_kind in {"action", "descriptor"}
                and group.direction_slots
                and all(
                    slot.concept_id in _COMPARATIVE_CONCEPTS
                    for slot in group.direction_slots
                )
                and _command_group_direction(group) in {-1, 1}
            )
            observation_remedy = bool(
                _is_observation_group(group)
                and _observation_correction_direction(group) in {-1, 1}
                and set(group.canonical_explicit_directions)
                == {_observation_correction_direction(group)}
            )
            if action_composition or comparative_macro or observation_remedy:
                errors.extend(_modifier_value_errors(group))
                continue
            errors.append(
                ScopeError(
                    code="conflicting_direction_scope",
                    message=(
                        "An axis has conflicting fused and scoped directions."
                    ),
                    clause_index=group.clause_index,
                    normalized_start=group.normalized_start,
                    normalized_end=group.normalized_end,
                    slots=(
                        group.axis_slot,
                        *group.direction_slots,
                        *group.action_attribute_slots,
                    ),
                )
            )
        errors.extend(_modifier_value_errors(group))
    return _deduplicate_errors(errors)


def _mismatched_axis_observation_remedy(
    group: OperationSlotGroup,
) -> tuple[SemanticSlot, ...]:
    """Return evidence when an observed state and its remedy disagree."""

    if (
        group.axis_slot.axis_role != "axis"
        or group.clause.boundary_after not in {"comma", "conjunction"}
        or not group.observation_modifier_slots
        or any(
            not _slot_is_in_clause(slot, group.clause)
            for slot in group.observation_modifier_slots
        )
        or group.numeric_slots
        or group.operation_slots
        or group.numeric_relation_slots
        or group.relation_slots
        or group.guard_slots
        or group.ambiguous_slots
    ):
        return ()

    local_directions = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.value in {-1, 1}
            and slot.concept_id not in _COMPARATIVE_CONCEPTS
            and _slot_is_in_clause(slot, group.clause)
        )
    )
    remedy_directions = tuple(
        slot
        for slot in group.direction_slots
        if (
            slot.value in {-1, 1}
            and slot.concept_id not in _COMPARATIVE_CONCEPTS
            and not _slot_is_in_clause(slot, group.clause)
        )
    )
    if len(local_directions) != 1 or len(remedy_directions) != 1:
        return ()

    expected = _observation_correction_direction(group)
    requested = (
        int(remedy_directions[0].value) * group.direction_multiplier
    )
    if expected not in {-1, 1} or requested == expected:
        return ()
    return (*local_directions, *remedy_directions)


def _nearest_slots(
    anchor: SemanticSlot,
    candidates: Iterable[SemanticSlot],
) -> tuple[SemanticSlot, ...]:
    materialized = tuple(candidates)
    if not materialized:
        return ()
    distances = {
        id(candidate): _span_distance(anchor, candidate)
        for candidate in materialized
    }
    minimum = min(distances.values())
    return tuple(
        candidate
        for candidate in materialized
        if distances[id(candidate)] == minimum
    )


def _span_distance(left: SemanticSlot, right: SemanticSlot) -> int:
    if left.normalized_end <= right.normalized_start:
        return right.normalized_start - left.normalized_end
    if right.normalized_end <= left.normalized_start:
        return left.normalized_start - right.normalized_end
    return 0


def _modifier_value_errors(
    group: OperationSlotGroup,
) -> tuple[ScopeError, ...]:
    checks = (
        ("strength", group.strength_slots),
        ("numeric", group.numeric_slots),
        ("operation", group.operation_slots),
        ("numeric_relation", group.numeric_relation_slots),
        ("relation", group.relation_slots),
        ("observation_modifier", group.observation_modifier_slots),
    )
    errors: list[ScopeError] = []
    for name, slots in checks:
        values = {_semantic_value(slot) for slot in slots}
        if len(values) <= 1:
            continue
        errors.append(
            ScopeError(
                code=f"conflicting_{name}_scope",
                message=f"An axis has conflicting {name} modifiers.",
                clause_index=group.clause_index,
                normalized_start=group.normalized_start,
                normalized_end=group.normalized_end,
                slots=(group.axis_slot, *slots),
            )
        )
    return tuple(errors)


def _duplicate_axis_errors(
    groups: tuple[OperationSlotGroup, ...],
) -> tuple[ScopeError, ...]:
    grouped: dict[str, list[OperationSlotGroup]] = {}
    for group in groups:
        grouped.setdefault(group.axis_id, []).append(group)

    errors: list[ScopeError] = []
    for duplicates in grouped.values():
        if len(duplicates) < 2:
            continue
        directions = {
            group.resolved_direction
            for group in duplicates
            if group.resolved_direction is not None
        }
        code = (
            "conflicting_axis_direction"
            if len(directions) > 1
            else "duplicate_axis_operation"
        )
        errors.append(
            ScopeError(
                code=code,
                message=(
                    "The same axis appears more than once with conflicting "
                    "directions."
                    if code == "conflicting_axis_direction"
                    else "The same axis appears more than once."
                ),
                normalized_start=min(
                    group.normalized_start for group in duplicates
                ),
                normalized_end=max(
                    group.normalized_end for group in duplicates
                ),
                slots=tuple(group.axis_slot for group in duplicates),
            )
        )
    return tuple(errors)


def _slot_error(
    code: str,
    message: str,
    slot: SemanticSlot,
    clause: ClauseScope | None,
) -> ScopeError:
    return ScopeError(
        code=code,
        message=message,
        clause_index=None if clause is None else clause.index,
        normalized_start=slot.normalized_start,
        normalized_end=slot.normalized_end,
        slots=(slot,),
    )


def _region_id(slot: SemanticSlot) -> str:
    if slot.namespace == "region":
        return str(slot.value)
    if slot.slot in {"region_context", "region_object"}:
        return str(slot.value)
    return str(slot.concept_id)


def _semantic_value(slot: SemanticSlot) -> object:
    value = slot.value
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    return value


def _ordered_unique_slots(
    slots: Iterable[SemanticSlot],
) -> tuple[SemanticSlot, ...]:
    unique: dict[int, SemanticSlot] = {}
    for slot in slots:
        unique.setdefault(id(slot), slot)
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.normalized_start,
                item.normalized_end,
            ),
        )
    )


def _deduplicate_errors(
    errors: Iterable[ScopeError],
) -> tuple[ScopeError, ...]:
    unique: dict[tuple[object, ...], ScopeError] = {}
    for error in errors:
        key = (
            error.code,
            error.clause_index,
            error.normalized_start,
            error.normalized_end,
            tuple(id(slot) for slot in error.slots),
        )
        unique.setdefault(key, error)
    return tuple(unique.values())


__all__ = [
    "ClauseScope",
    "OperationSlotGroup",
    "ScopeError",
    "ScopeGuard",
    "SemanticScopeResolution",
    "SemanticScopeResolver",
    "resolve_semantic_scope",
]
