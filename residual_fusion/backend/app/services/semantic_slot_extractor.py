"""Registry-driven lexical slot extraction for normalized edit prompts.

This module deliberately stops before scope resolution or operation assembly.
It finds grounded lexical concepts, preserves genuine ambiguity, and reports
the remaining text.  Domain vocabulary comes exclusively from an injected
``ParameterRegistry``; adding an axis must not require another parsing branch.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Literal

from app.services.semantic_ir import RawSpanEvidence, UnresolvedSpan
from app.services.semantic_normalizer import (
    NormalizedText,
)
from app.services.semantic_registry import (
    DEFAULT_PARAMETER_REGISTRY,
    ParameterRegistry,
    has_semantic_word_boundaries,
)


_NAMESPACE_AXIS = "axis"
_NAMESPACE_REGION = "region"
_NAMESPACE_SHARED = "shared"
_NAMESPACE_EFFECT = "effect"
_NAMESPACE_NUMERIC = "numeric"
_AMBIGUOUS = "ambiguous"
_NUMERIC_SLOT = "numeric"
_NUMERIC_CONCEPT = "numeric_literal"

_MATCH_KIND_PRIORITY = {
    "action": 40,
    "observation": 30,
    "descriptor": 20,
    "axis": 10,
}
_DEFAULT_ALIAS_PRIORITY = 10
_NUMBER_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_NON_IGNORABLE_GUARD_SEPARATORS = frozenset({"/", "\\", "|"})

SlotValue = str | int | float | bool


@dataclass(frozen=True, slots=True)
class SlotInterpretation:
    """One possible registry meaning for a grounded lexical span."""

    namespace: Literal["axis", "region", "shared", "effect", "numeric"]
    slot: str
    concept_id: str
    value: SlotValue
    language: str
    priority: int
    axis_role: str | None = None
    match_kind: str | None = None
    requested_direction: int | None = None
    direction_multiplier: int = 1
    object_binding: str = "self_only"
    surface_form_kind: Literal["base", "progressive"] = "base"

    def __post_init__(self) -> None:
        if self.namespace not in {
            _NAMESPACE_AXIS,
            _NAMESPACE_REGION,
            _NAMESPACE_SHARED,
            _NAMESPACE_EFFECT,
            _NAMESPACE_NUMERIC,
        }:
            raise ValueError(f"unsupported slot namespace {self.namespace!r}")
        for field_name in ("slot", "concept_id", "language"):
            value = str(getattr(self, field_name)).strip()
            if not value:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(
                self,
                field_name,
                value.lower() if field_name == "language" else value,
            )
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")
        if self.requested_direction not in {None, -1, 1}:
            raise ValueError("requested_direction must be None, -1, or 1")
        if (
            isinstance(self.direction_multiplier, bool)
            or not isinstance(self.direction_multiplier, int)
            or self.direction_multiplier not in {-1, 1}
        ):
            raise ValueError(
                "direction_multiplier must be the integer -1 or 1"
            )
        if (
            self.namespace != _NAMESPACE_AXIS
            and self.direction_multiplier != 1
        ):
            raise ValueError(
                "only axis interpretations may invert the surface direction"
            )
        if (
            self.axis_role != "axis"
            and self.direction_multiplier != 1
        ):
            raise ValueError(
                "only axis-noun interpretations may invert the surface direction"
            )
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("numeric slot values must be finite")
        if self.object_binding not in {
            "self_only",
            "self_or_region",
            "cross_axis_target",
        }:
            raise ValueError("unsupported object_binding")
        if (
            self.object_binding != "self_only"
            and (
                self.namespace != _NAMESPACE_AXIS
                or self.match_kind != "action"
            )
        ):
            raise ValueError(
                "non-default object_binding requires an axis action"
            )
        if self.surface_form_kind not in {"base", "progressive"}:
            raise ValueError("surface_form_kind must be base or progressive")

    @property
    def semantic_key(self) -> tuple[object, ...]:
        """Stable identity used to collapse duplicate aliases."""

        return (
            self.namespace,
            self.slot,
            self.concept_id,
            self.value,
            self.axis_role,
            self.match_kind,
            self.requested_direction,
            self.direction_multiplier,
            self.object_binding,
            self.surface_form_kind,
        )


@dataclass(frozen=True, slots=True)
class SemanticSlot:
    """An immutable normalized span with one or more grounded meanings."""

    normalized_start: int
    normalized_end: int
    normalized_text: str
    evidence: RawSpanEvidence
    interpretations: tuple[SlotInterpretation, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.normalized_start, bool)
            or not isinstance(self.normalized_start, int)
            or isinstance(self.normalized_end, bool)
            or not isinstance(self.normalized_end, int)
        ):
            raise TypeError("normalized offsets must be integers")
        if self.normalized_start < 0 or self.normalized_end <= self.normalized_start:
            raise ValueError(
                "normalized slot span must satisfy 0 <= start < end"
            )
        if len(self.normalized_text) != (
            self.normalized_end - self.normalized_start
        ):
            raise ValueError(
                "normalized_text length must equal normalized span length"
            )
        interpretations = tuple(self.interpretations)
        if not interpretations:
            raise ValueError("a semantic slot needs at least one interpretation")
        keys = [item.semantic_key for item in interpretations]
        if len(keys) != len(set(keys)):
            raise ValueError("semantic slot interpretations must be unique")
        object.__setattr__(self, "interpretations", interpretations)

    @property
    def is_ambiguous(self) -> bool:
        return len(self.interpretations) > 1

    @property
    def interpretation(self) -> SlotInterpretation | None:
        return None if self.is_ambiguous else self.interpretations[0]

    @property
    def kind(self) -> str:
        item = self.interpretation
        return _AMBIGUOUS if item is None else item.namespace

    @property
    def namespace(self) -> str | None:
        item = self.interpretation
        return None if item is None else item.namespace

    @property
    def slot(self) -> str | None:
        item = self.interpretation
        return None if item is None else item.slot

    @property
    def concept_id(self) -> str | None:
        item = self.interpretation
        return None if item is None else item.concept_id

    @property
    def value(self) -> SlotValue | None:
        item = self.interpretation
        return None if item is None else item.value

    @property
    def language(self) -> str:
        item = self.interpretation
        return self.evidence.language if item is None else item.language

    @property
    def axis_role(self) -> str | None:
        item = self.interpretation
        return None if item is None else item.axis_role

    @property
    def match_kind(self) -> str | None:
        item = self.interpretation
        return None if item is None else item.match_kind

    @property
    def requested_direction(self) -> int | None:
        item = self.interpretation
        return None if item is None else item.requested_direction

    @property
    def direction_multiplier(self) -> int | None:
        item = self.interpretation
        return None if item is None else item.direction_multiplier

    @property
    def object_binding(self) -> str | None:
        item = self.interpretation
        return None if item is None else item.object_binding

    @property
    def surface_form_kind(self) -> str | None:
        item = self.interpretation
        return None if item is None else item.surface_form_kind


@dataclass(frozen=True, slots=True)
class ExtractionTextSpan:
    """An unmatched normalized span restored to its exact raw source slice."""

    normalized_start: int
    normalized_end: int
    normalized_text: str
    raw_start: int
    raw_end: int
    raw_text: str
    kind: Literal["noise", "residue"]
    reason: str

    def __post_init__(self) -> None:
        if self.normalized_start < 0 or self.normalized_end <= self.normalized_start:
            raise ValueError(
                "normalized text span must satisfy 0 <= start < end"
            )
        if len(self.normalized_text) != (
            self.normalized_end - self.normalized_start
        ):
            raise ValueError(
                "normalized_text length must equal normalized span length"
            )
        if self.raw_start < 0 or self.raw_end <= self.raw_start:
            raise ValueError("raw text span must satisfy 0 <= start < end")
        if len(self.raw_text) != self.raw_end - self.raw_start:
            raise ValueError("raw_text length must equal raw span length")
        if self.kind not in {"noise", "residue"}:
            raise ValueError("kind must be noise or residue")
        if not str(self.reason).strip():
            raise ValueError("reason must not be empty")


@dataclass(frozen=True, slots=True)
class SlotExtraction:
    """Immutable lexical extraction result before semantic assembly."""

    normalized: NormalizedText
    slots: tuple[SemanticSlot, ...]
    noise_spans: tuple[ExtractionTextSpan, ...]
    residue_spans: tuple[ExtractionTextSpan, ...]
    registry_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "slots", tuple(self.slots))
        object.__setattr__(self, "noise_spans", tuple(self.noise_spans))
        object.__setattr__(self, "residue_spans", tuple(self.residue_spans))
        if not str(self.registry_version).strip():
            raise ValueError("registry_version must not be empty")

        previous_end = 0
        for slot in self.slots:
            if slot.normalized_end > len(self.normalized.text):
                raise ValueError("semantic slot lies outside normalized text")
            if slot.normalized_start < previous_end:
                raise ValueError("semantic slots must be ordered and disjoint")
            if (
                self.normalized.text[
                    slot.normalized_start : slot.normalized_end
                ]
                != slot.normalized_text
            ):
                raise ValueError(
                    "semantic slot normalized_text must match normalized input"
                )
            if not slot.evidence.matches(self.normalized.raw_text):
                raise ValueError(
                    "semantic slot evidence must match the raw input slice"
                )
            previous_end = slot.normalized_end

    @property
    def ambiguous_slots(self) -> tuple[SemanticSlot, ...]:
        return tuple(slot for slot in self.slots if slot.is_ambiguous)

    @property
    def is_fully_lexed(self) -> bool:
        return not self.residue_spans and not self.ambiguous_slots

    @property
    def noise(self) -> tuple[ExtractionTextSpan, ...]:
        return self.noise_spans

    @property
    def residue(self) -> tuple[ExtractionTextSpan, ...]:
        return self.residue_spans

    @property
    def unresolved_spans(self) -> tuple[UnresolvedSpan, ...]:
        unresolved = [
            UnresolvedSpan(
                start=span.raw_start,
                end=span.raw_end,
                raw_text=span.raw_text,
                reason=span.reason,
            )
            for span in self.residue_spans
        ]
        unresolved.extend(
            UnresolvedSpan(
                start=slot.evidence.start,
                end=slot.evidence.end,
                raw_text=slot.evidence.raw_text,
                reason="ambiguous_lexical_match",
                language=slot.evidence.language,
            )
            for slot in self.ambiguous_slots
        )
        return tuple(sorted(unresolved, key=lambda item: (item.start, item.end)))


@dataclass(frozen=True, slots=True)
class _AliasPattern:
    text: str
    interpretation: SlotInterpretation


class SemanticSlotExtractor:
    """Compile a registry once, then scan normalized prompts left-to-right."""

    def __init__(
        self,
        registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    ) -> None:
        if not isinstance(registry, ParameterRegistry):
            raise TypeError("registry must be a ParameterRegistry")
        self._registry = registry
        self._patterns = _build_pattern_index(registry)

    @property
    def registry(self) -> ParameterRegistry:
        return self._registry

    def extract(self, normalized: NormalizedText) -> SlotExtraction:
        if not isinstance(normalized, NormalizedText):
            raise TypeError("normalized must be a NormalizedText")

        text = normalized.text
        slots: list[SemanticSlot] = []
        covered = [False] * len(text)
        index = 0
        while index < len(text):
            alias_matches = _alias_matches_at(
                text,
                index,
                self._patterns.get(text[index], ()),
            )
            numeric_match = _numeric_match_at(text, index)
            candidates = list(alias_matches)
            if numeric_match is not None:
                candidates.append(numeric_match)
            if not candidates:
                index += 1
                continue

            longest = max(len(item.text) for item in candidates)
            candidates = [
                item for item in candidates if len(item.text) == longest
            ]
            interpretations = _resolve_equal_span_candidates(candidates)
            end = index + longest
            slots.append(
                _make_semantic_slot(
                    normalized,
                    index,
                    end,
                    interpretations,
                )
            )
            for covered_index in range(index, end):
                covered[covered_index] = True
            index = end

        noise_spans, residue_spans = _classify_unmatched(
            normalized,
            covered,
        )
        return SlotExtraction(
            normalized=normalized,
            slots=tuple(slots),
            noise_spans=noise_spans,
            residue_spans=residue_spans,
            registry_version=self._registry.registry_version,
        )


def extract_semantic_slots(
    normalized: NormalizedText,
    *,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
) -> SlotExtraction:
    """Extract slots through the precompiled production registry when possible.

    The production registry is immutable for the lifetime of a process, so its
    lexical index is compiled once at import time.  Explicitly injected
    registries remain isolated and are compiled by their own extractor.
    """

    extractor = (
        _DEFAULT_SLOT_EXTRACTOR
        if registry is DEFAULT_PARAMETER_REGISTRY
        else SemanticSlotExtractor(registry)
    )
    return extractor.extract(normalized)


def _build_pattern_index(
    registry: ParameterRegistry,
) -> dict[str, tuple[_AliasPattern, ...]]:
    patterns: list[_AliasPattern] = []

    for definition in registry.axes.values():
        for alias in definition.aliases:
            for (
                normalized_text,
                surface_form_kind,
            ) in alias.normalized_surface_forms:
                patterns.append(
                    _AliasPattern(
                    normalized_text,
                    SlotInterpretation(
                        namespace=_NAMESPACE_AXIS,
                        slot=_NAMESPACE_AXIS,
                        concept_id=definition.axis_id,
                        value=definition.axis_id,
                        language=alias.language,
                        priority=_MATCH_KIND_PRIORITY.get(
                            alias.match_kind,
                            _DEFAULT_ALIAS_PRIORITY,
                        ),
                        axis_role=alias.role,
                        match_kind=alias.match_kind,
                        requested_direction=alias.requested_direction,
                        direction_multiplier=alias.direction_multiplier,
                        object_binding=alias.object_binding,
                        surface_form_kind=surface_form_kind,
                    ),
                    )
                )

    for definition in registry.regions.values():
        for alias in definition.aliases:
            patterns.append(
                _AliasPattern(
                    alias.normalized_text,
                    SlotInterpretation(
                        namespace=_NAMESPACE_REGION,
                        slot=_NAMESPACE_REGION,
                        concept_id=definition.region_id,
                        value=definition.region_id,
                        language=alias.language,
                        priority=_DEFAULT_ALIAS_PRIORITY,
                    ),
                )
            )

    for definition in registry.shared_concepts.values():
        for alias in definition.aliases:
            for (
                normalized_text,
                surface_form_kind,
            ) in alias.normalized_surface_forms:
                patterns.append(
                    _AliasPattern(
                    normalized_text,
                    SlotInterpretation(
                        namespace=_NAMESPACE_SHARED,
                        slot=definition.slot,
                        concept_id=definition.concept_id,
                        value=definition.value,
                        language=alias.language,
                        priority=_DEFAULT_ALIAS_PRIORITY,
                        surface_form_kind=surface_form_kind,
                    ),
                    )
                )

    for definition in registry.effect_dimensions.values():
        for alias in definition.aliases:
            patterns.append(
                _AliasPattern(
                    alias.normalized_text,
                    SlotInterpretation(
                        namespace=_NAMESPACE_EFFECT,
                        slot="effect_state",
                        concept_id=definition.effect_id,
                        value=definition.effect_id,
                        language=alias.language,
                        priority=_DEFAULT_ALIAS_PRIORITY,
                        requested_direction=alias.state_direction,
                    ),
                )
            )

    by_first: dict[str, list[_AliasPattern]] = {}
    for pattern in patterns:
        if not pattern.text:
            continue
        by_first.setdefault(pattern.text[0], []).append(pattern)
    return {
        first: tuple(
            sorted(
                items,
                key=lambda item: (
                    -len(item.text),
                    -item.interpretation.priority,
                    item.interpretation.namespace,
                    item.interpretation.slot,
                    item.interpretation.concept_id,
                    item.interpretation.language,
                ),
            )
        )
        for first, items in by_first.items()
    }


def _alias_matches_at(
    text: str,
    start: int,
    patterns: Iterable[_AliasPattern],
) -> tuple[_AliasPattern, ...]:
    matches: list[_AliasPattern] = []
    for pattern in patterns:
        end = start + len(pattern.text)
        if end > len(text) or text[start:end] != pattern.text:
            continue
        if not has_semantic_word_boundaries(text, start, end):
            continue
        matches.append(pattern)
    return tuple(matches)


def _numeric_match_at(text: str, start: int) -> _AliasPattern | None:
    match = _NUMBER_PATTERN.match(text, start)
    if match is None:
        return None
    end = match.end()
    if not has_semantic_word_boundaries(text, start, end):
        return None
    lexical = match.group(0)
    try:
        value = float(lexical)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return _AliasPattern(
        lexical,
        SlotInterpretation(
            namespace=_NAMESPACE_NUMERIC,
            slot=_NUMERIC_SLOT,
            concept_id=_NUMERIC_CONCEPT,
            value=value,
            language="und",
            priority=_DEFAULT_ALIAS_PRIORITY,
        ),
    )


def _resolve_equal_span_candidates(
    candidates: Iterable[_AliasPattern],
) -> tuple[SlotInterpretation, ...]:
    materialized = tuple(candidates)
    namespaces = {
        item.interpretation.namespace for item in materialized
    }

    # A same-length cross-namespace collision is genuine lexical ambiguity.
    # Namespace ranking must never silently turn it into a semantic decision.
    if len(namespaces) > 1:
        selected = [item.interpretation for item in materialized]
    else:
        highest_priority = max(
            item.interpretation.priority for item in materialized
        )
        selected = [
            item.interpretation
            for item in materialized
            if item.interpretation.priority == highest_priority
        ]

    unique: dict[tuple[object, ...], SlotInterpretation] = {}
    for interpretation in selected:
        previous = unique.get(interpretation.semantic_key)
        if previous is None or interpretation.language < previous.language:
            unique[interpretation.semantic_key] = interpretation
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.namespace,
                item.slot,
                item.concept_id,
                item.language,
            ),
        )
    )


def _make_semantic_slot(
    normalized: NormalizedText,
    start: int,
    end: int,
    interpretations: tuple[SlotInterpretation, ...],
) -> SemanticSlot:
    raw_span = normalized.restore_span(start, end)
    ambiguous = len(interpretations) > 1
    language = (
        _combined_language(interpretations)
        if ambiguous
        else interpretations[0].language
    )
    evidence = RawSpanEvidence(
        start=raw_span.start,
        end=raw_span.end,
        raw_text=normalized.raw_text[raw_span.start : raw_span.end],
        slot=_AMBIGUOUS if ambiguous else interpretations[0].slot,
        concept_id=(
            "ambiguous_lexical_match"
            if ambiguous
            else interpretations[0].concept_id
        ),
        language=language,
    )
    return SemanticSlot(
        normalized_start=start,
        normalized_end=end,
        normalized_text=normalized.text[start:end],
        evidence=evidence,
        interpretations=interpretations,
    )


def _combined_language(
    interpretations: Iterable[SlotInterpretation],
) -> str:
    languages = {item.language for item in interpretations}
    return next(iter(languages)) if len(languages) == 1 else "mul"


def _classify_unmatched(
    normalized: NormalizedText,
    covered: list[bool],
) -> tuple[tuple[ExtractionTextSpan, ...], tuple[ExtractionTextSpan, ...]]:
    noise: list[ExtractionTextSpan] = []
    residue: list[ExtractionTextSpan] = []
    index = 0
    while index < len(normalized.text):
        if covered[index]:
            index += 1
            continue
        kind: Literal["noise", "residue"] = (
            "noise"
            if _is_ignorable_noise(normalized.text[index])
            else "residue"
        )
        end = index + 1
        while (
            end < len(normalized.text)
            and not covered[end]
            and (
                "noise"
                if _is_ignorable_noise(normalized.text[end])
                else "residue"
            )
            == kind
        ):
            end += 1
        raw_span = normalized.restore_span(index, end)
        span = ExtractionTextSpan(
            normalized_start=index,
            normalized_end=end,
            normalized_text=normalized.text[index:end],
            raw_start=raw_span.start,
            raw_end=raw_span.end,
            raw_text=normalized.raw_text[raw_span.start : raw_span.end],
            kind=kind,
            reason=(
                "ignorable_separator"
                if kind == "noise"
                else "unrecognized_semantic_text"
            ),
        )
        (noise if kind == "noise" else residue).append(span)
        index = end
    return tuple(noise), tuple(residue)


def _is_ignorable_noise(char: str) -> bool:
    if char in _NON_IGNORABLE_GUARD_SEPARATORS:
        return False
    return char.isspace() or unicodedata.category(char).startswith("P")


_DEFAULT_SLOT_EXTRACTOR = SemanticSlotExtractor(DEFAULT_PARAMETER_REGISTRY)


__all__ = [
    "ExtractionTextSpan",
    "SemanticSlot",
    "SemanticSlotExtractor",
    "SlotExtraction",
    "SlotInterpretation",
    "extract_semantic_slots",
]
