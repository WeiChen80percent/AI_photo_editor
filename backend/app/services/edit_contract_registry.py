"""Registry of measurable capabilities for verifiable edit contracts.

Prompt wording lives in small, typed aliases while image behavior lives in a
metric definition and its evaluator.  The parser therefore never branches on
metric ids, and adding a metric is an injected registry operation rather than
a rewrite of semantic core code.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from app.services.edit_contract_schema import (
    ContractConstraint,
    MetricEvaluationContext,
    MetricMeasurement,
)
from app.services.edit_schema import EDIT_MASK_TYPES, EDIT_REGIONS
from app.services.semantic_normalizer import normalize_semantic_text


METRIC_REGISTRY_VERSION = "edit_contract_metric_registry_v1"
MetricEvaluator = Callable[[MetricEvaluationContext], MetricMeasurement]

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_LANGUAGES = frozenset({"en", "zh"})
_OPERATORS = frozenset({"<=", "no_worse_than_baseline"})
_REFERENCE_MODES = frozenset(
    {"absolute_outcome", "selected_target_baseline"}
)
_ENFORCEMENT_STRATEGIES = frozenset(
    {"scale_search", "protected_mask_then_search"}
)
_AUTOMATIC_POLICY_TRIGGERS = frozenset({"local_edit"})
_CONTRACT_REGIONS = frozenset(set(EDIT_REGIONS) | {"outside_edit_scope"})


class MetricRegistryValidationError(ValueError):
    pass


def _non_empty(value: object, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise MetricRegistryValidationError(f"{field_name} must not be empty")
    return normalized


def _identifier(value: object, field_name: str) -> str:
    normalized = _non_empty(value, field_name).lower()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MetricRegistryValidationError(
            f"{field_name} must be a stable identifier"
        )
    return normalized


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricRegistryValidationError(f"{field_name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise MetricRegistryValidationError(f"{field_name} must be finite")
    return numeric


def normalize_contract_alias(value: object) -> str:
    return normalize_semantic_text(str(value)).text


def _localized_metadata(
    value: Mapping[str, str],
    field_name: str,
) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise MetricRegistryValidationError(f"{field_name} must be a mapping")
    normalized = {
        _non_empty(language, f"{field_name} language").lower(): _non_empty(
            text,
            f"{field_name}.{language}",
        )
        for language, text in value.items()
    }
    missing = _LANGUAGES.difference(normalized)
    if missing:
        raise MetricRegistryValidationError(
            f"{field_name} is missing languages {sorted(missing)}"
        )
    return MappingProxyType(normalized)


@dataclass(frozen=True, slots=True)
class ContractAlias:
    text: str
    language: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _non_empty(self.text, "alias text"))
        language = _non_empty(self.language, "alias language").lower()
        if language not in _LANGUAGES:
            raise MetricRegistryValidationError(
                f"alias language must be one of {sorted(_LANGUAGES)}"
            )
        object.__setattr__(self, "language", language)
        if not self.normalized_text:
            raise MetricRegistryValidationError("alias normalizes to empty text")

    @property
    def normalized_text(self) -> str:
        return normalize_contract_alias(self.text)


@dataclass(frozen=True, slots=True)
class OperatorAlias:
    text: str
    language: str
    operator: str

    def __post_init__(self) -> None:
        alias = ContractAlias(self.text, self.language)
        object.__setattr__(self, "text", alias.text)
        object.__setattr__(self, "language", alias.language)
        operator = _non_empty(self.operator, "operator")
        if operator not in _OPERATORS:
            raise MetricRegistryValidationError(
                f"unsupported operator {operator!r}"
            )
        object.__setattr__(self, "operator", operator)

    @property
    def normalized_text(self) -> str:
        return normalize_contract_alias(self.text)


@dataclass(frozen=True, slots=True)
class MetricInputUnit:
    unit_id: str
    aliases: tuple[ContractAlias, ...]
    to_canonical_factor: float = 1.0
    implicit: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit_id", _identifier(self.unit_id, "unit_id"))
        aliases = tuple(self.aliases)
        if not aliases and not self.implicit:
            raise MetricRegistryValidationError(
                "non-implicit input units require at least one alias"
            )
        object.__setattr__(self, "aliases", aliases)
        factor = _finite(self.to_canonical_factor, "to_canonical_factor")
        if factor <= 0:
            raise MetricRegistryValidationError(
                "to_canonical_factor must be positive"
            )
        object.__setattr__(self, "to_canonical_factor", factor)
        if not isinstance(self.implicit, bool):
            raise MetricRegistryValidationError("implicit must be boolean")


@dataclass(frozen=True, slots=True)
class MetricUnitDefinition:
    unit_id: str
    minimum: float
    maximum: float
    display_precision: int
    labels: Mapping[str, str]
    input_units: tuple[MetricInputUnit, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit_id", _identifier(self.unit_id, "unit_id"))
        minimum = _finite(self.minimum, "unit minimum")
        maximum = _finite(self.maximum, "unit maximum")
        if minimum >= maximum:
            raise MetricRegistryValidationError(
                "unit minimum must be smaller than maximum"
            )
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        if (
            isinstance(self.display_precision, bool)
            or not isinstance(self.display_precision, int)
            or not 0 <= self.display_precision <= 8
        ):
            raise MetricRegistryValidationError(
                "display_precision must be an integer between 0 and 8"
            )
        object.__setattr__(self, "labels", _localized_metadata(self.labels, "unit labels"))
        input_units = tuple(self.input_units)
        if not input_units:
            raise MetricRegistryValidationError("unit needs at least one input unit")
        identifiers = [item.unit_id for item in input_units]
        if len(identifiers) != len(set(identifiers)):
            raise MetricRegistryValidationError("duplicate input unit id")
        implicit_count = sum(item.implicit for item in input_units)
        if implicit_count > 1:
            raise MetricRegistryValidationError(
                "unit may have at most one implicit input representation"
            )
        object.__setattr__(self, "input_units", input_units)

    def normalize_threshold(self, value: float, source_unit: str) -> float:
        input_unit = next(
            (item for item in self.input_units if item.unit_id == source_unit),
            None,
        )
        if input_unit is None:
            raise MetricRegistryValidationError(
                f"unknown input unit {source_unit!r} for {self.unit_id!r}"
            )
        normalized = _finite(value, "threshold") * input_unit.to_canonical_factor
        if not self.minimum <= normalized <= self.maximum:
            raise MetricRegistryValidationError(
                f"threshold {normalized} is outside {self.unit_id!r} range"
            )
        return normalized


@dataclass(frozen=True, slots=True)
class MetricProtectionProfile:
    profile_id: str
    version: str
    operator: str
    threshold: float
    unit: str
    reference_mode: str
    aliases: tuple[ContractAlias, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "profile_id",
            _identifier(self.profile_id, "profile_id"),
        )
        object.__setattr__(self, "version", _identifier(self.version, "profile version"))
        operator = _non_empty(self.operator, "profile operator")
        if operator not in _OPERATORS:
            raise MetricRegistryValidationError("unsupported profile operator")
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "threshold", _finite(self.threshold, "profile threshold"))
        object.__setattr__(self, "unit", _identifier(self.unit, "profile unit"))
        reference_mode = _non_empty(
            self.reference_mode,
            "profile reference_mode",
        ).lower()
        if reference_mode not in _REFERENCE_MODES:
            raise MetricRegistryValidationError(
                "unsupported profile reference mode"
            )
        object.__setattr__(self, "reference_mode", reference_mode)
        aliases = tuple(self.aliases)
        if not aliases:
            raise MetricRegistryValidationError("profile needs relation aliases")
        object.__setattr__(self, "aliases", aliases)


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    metric_version: str
    aliases: tuple[ContractAlias, ...]
    supported_subject_regions: frozenset[str]
    required_mask_types: frozenset[str]
    capability_requirements: frozenset[str]
    supported_operators: frozenset[str]
    unit: str
    profiles: tuple[MetricProtectionProfile, ...]
    requires_baseline: bool
    supports_magnitude_search: bool
    enforcement_strategy: str
    labels: Mapping[str, str]
    descriptions: Mapping[str, str]
    evaluator: MetricEvaluator
    automatic_policy_trigger: str | None = None
    automatic_profile_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _identifier(self.metric_id, "metric_id"))
        object.__setattr__(
            self,
            "metric_version",
            _identifier(self.metric_version, "metric_version"),
        )
        aliases = tuple(self.aliases)
        if not aliases:
            raise MetricRegistryValidationError("metric needs at least one alias")
        object.__setattr__(self, "aliases", aliases)
        regions = frozenset(
            _identifier(region, "supported subject region")
            for region in self.supported_subject_regions
        )
        if not regions or not regions.issubset(_CONTRACT_REGIONS):
            raise MetricRegistryValidationError(
                "metric has missing or unsupported subject regions"
            )
        object.__setattr__(self, "supported_subject_regions", regions)
        mask_types = frozenset(
            _identifier(mask, "required mask type")
            for mask in self.required_mask_types
        )
        if not mask_types or not mask_types.issubset(EDIT_MASK_TYPES):
            raise MetricRegistryValidationError(
                "metric has missing or unsupported mask capability"
            )
        object.__setattr__(self, "required_mask_types", mask_types)
        requirements = frozenset(
            _identifier(item, "capability requirement")
            for item in self.capability_requirements
        )
        if not requirements:
            raise MetricRegistryValidationError(
                "metric needs at least one capability requirement"
            )
        object.__setattr__(self, "capability_requirements", requirements)
        operators = frozenset(self.supported_operators)
        if not operators or not operators.issubset(_OPERATORS):
            raise MetricRegistryValidationError(
                "metric has unsupported operators"
            )
        object.__setattr__(self, "supported_operators", operators)
        object.__setattr__(self, "unit", _identifier(self.unit, "metric unit"))
        profiles = tuple(self.profiles)
        profile_ids = [profile.profile_id for profile in profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise MetricRegistryValidationError("duplicate metric profile id")
        object.__setattr__(self, "profiles", profiles)
        if not isinstance(self.requires_baseline, bool):
            raise MetricRegistryValidationError("requires_baseline must be boolean")
        if not isinstance(self.supports_magnitude_search, bool):
            raise MetricRegistryValidationError(
                "supports_magnitude_search must be boolean"
            )
        strategy = _identifier(self.enforcement_strategy, "enforcement_strategy")
        if strategy not in _ENFORCEMENT_STRATEGIES:
            raise MetricRegistryValidationError(
                "unsupported enforcement strategy"
            )
        object.__setattr__(self, "enforcement_strategy", strategy)
        if (
            strategy == "protected_mask_then_search"
            and "subject_mask" not in requirements
        ):
            raise MetricRegistryValidationError(
                "protected-mask enforcement requires subject_mask capability"
            )
        object.__setattr__(self, "labels", _localized_metadata(self.labels, "metric labels"))
        object.__setattr__(
            self,
            "descriptions",
            _localized_metadata(self.descriptions, "metric descriptions"),
        )
        if not callable(self.evaluator):
            raise MetricRegistryValidationError("metric evaluator must be callable")
        trigger = self.automatic_policy_trigger
        if trigger is not None:
            trigger = _identifier(trigger, "automatic_policy_trigger")
            if trigger not in _AUTOMATIC_POLICY_TRIGGERS:
                raise MetricRegistryValidationError(
                    "unsupported automatic policy trigger"
                )
            object.__setattr__(self, "automatic_policy_trigger", trigger)
            if self.automatic_profile_id is None:
                raise MetricRegistryValidationError(
                    "automatic policies require automatic_profile_id"
                )
        if self.automatic_profile_id is not None:
            profile_id = _identifier(
                self.automatic_profile_id,
                "automatic_profile_id",
            )
            if profile_id not in profile_ids:
                raise MetricRegistryValidationError(
                    "automatic_profile_id must reference a metric profile"
                )
            object.__setattr__(self, "automatic_profile_id", profile_id)

    def get_profile(self, profile_id: str) -> MetricProtectionProfile:
        normalized = _identifier(profile_id, "profile_id")
        for profile in self.profiles:
            if profile.profile_id == normalized:
                return profile
        raise KeyError(
            f"unknown protection profile {profile_id!r} for {self.metric_id!r}"
        )


@dataclass(frozen=True, slots=True)
class AliasMatch:
    start: int
    end: int
    raw_text: str
    normalized_text: str
    language: str
    kind: str
    concept_id: str
    value: str


@dataclass(frozen=True, slots=True, init=False)
class MetricCapabilityRegistry:
    registry_version: str
    _metrics: Mapping[str, MetricDefinition]
    _units: Mapping[str, MetricUnitDefinition]
    _operator_aliases: tuple[OperatorAlias, ...]
    _protection_aliases: tuple[ContractAlias, ...]

    def __init__(
        self,
        metrics: Iterable[MetricDefinition],
        *,
        units: Iterable[MetricUnitDefinition],
        operator_aliases: Iterable[OperatorAlias],
        protection_aliases: Iterable[ContractAlias] = (),
        registry_version: str = METRIC_REGISTRY_VERSION,
    ) -> None:
        version = _identifier(registry_version, "metric registry version")
        unit_map: dict[str, MetricUnitDefinition] = {}
        input_aliases: dict[str, tuple[str, str]] = {}
        for definition in units:
            if definition.unit_id in unit_map:
                raise MetricRegistryValidationError(
                    f"duplicate unit id {definition.unit_id!r}"
                )
            unit_map[definition.unit_id] = definition
            for input_unit in definition.input_units:
                for alias in input_unit.aliases:
                    key = alias.normalized_text
                    previous = input_aliases.get(key)
                    current = (definition.unit_id, input_unit.unit_id)
                    if previous is not None and previous != current:
                        raise MetricRegistryValidationError(
                            f"unit alias collision for {alias.text!r}"
                        )
                    input_aliases[key] = current
        if not unit_map:
            raise MetricRegistryValidationError("registry needs at least one unit")

        metric_map: dict[str, MetricDefinition] = {}
        metric_aliases: dict[str, str] = {}
        for definition in metrics:
            if definition.metric_id in metric_map:
                raise MetricRegistryValidationError(
                    f"duplicate metric id {definition.metric_id!r}"
                )
            if definition.unit not in unit_map:
                raise MetricRegistryValidationError(
                    f"metric {definition.metric_id!r} uses unknown unit"
                )
            unit = unit_map[definition.unit]
            for profile in definition.profiles:
                if profile.unit != definition.unit:
                    raise MetricRegistryValidationError(
                        f"profile {profile.profile_id!r} unit does not match metric"
                    )
                if profile.operator not in definition.supported_operators:
                    raise MetricRegistryValidationError(
                        f"profile {profile.profile_id!r} operator is unsupported"
                    )
                if not unit.minimum <= profile.threshold <= unit.maximum:
                    raise MetricRegistryValidationError(
                        f"profile {profile.profile_id!r} threshold is out of range"
                    )
            for alias in definition.aliases:
                key = alias.normalized_text
                previous = metric_aliases.get(key)
                if previous is not None and previous != definition.metric_id:
                    raise MetricRegistryValidationError(
                        f"metric alias collision for {alias.text!r}"
                    )
                metric_aliases[key] = definition.metric_id
            metric_map[definition.metric_id] = definition
        if not metric_map:
            raise MetricRegistryValidationError("registry needs at least one metric")

        operators = tuple(operator_aliases)
        if not operators:
            raise MetricRegistryValidationError("registry needs operator aliases")
        operator_map: dict[str, str] = {}
        for alias in operators:
            previous = operator_map.get(alias.normalized_text)
            if previous is not None and previous != alias.operator:
                raise MetricRegistryValidationError(
                    f"operator alias collision for {alias.text!r}"
                )
            operator_map[alias.normalized_text] = alias.operator

        protections = tuple(protection_aliases)
        if not protections:
            protections = _default_protection_aliases()
        protection_keys: set[str] = set()
        for alias in protections:
            if alias.normalized_text in protection_keys:
                raise MetricRegistryValidationError(
                    f"duplicate protection alias {alias.text!r}"
                )
            protection_keys.add(alias.normalized_text)

        object.__setattr__(self, "registry_version", version)
        object.__setattr__(self, "_metrics", MappingProxyType(metric_map))
        object.__setattr__(self, "_units", MappingProxyType(unit_map))
        object.__setattr__(self, "_operator_aliases", operators)
        object.__setattr__(self, "_protection_aliases", protections)

    @property
    def metrics(self) -> Mapping[str, MetricDefinition]:
        return self._metrics

    @property
    def units(self) -> Mapping[str, MetricUnitDefinition]:
        return self._units

    @property
    def operator_aliases(self) -> tuple[OperatorAlias, ...]:
        return self._operator_aliases

    @property
    def protection_aliases(self) -> tuple[ContractAlias, ...]:
        return self._protection_aliases

    def get(self, metric_id: str) -> MetricDefinition:
        try:
            return self._metrics[metric_id]
        except KeyError as exc:
            raise KeyError(f"unknown contract metric {metric_id!r}") from exc

    def resolve_profile(
        self,
        metric_id: str,
        profile_id: str,
    ) -> MetricProtectionProfile:
        return self.get(metric_id).get_profile(profile_id)

    def validate_constraint(
        self,
        constraint: ContractConstraint,
    ) -> ContractConstraint:
        definition = self.get(constraint.metric_id)
        if constraint.metric_version != definition.metric_version:
            raise MetricRegistryValidationError("constraint metric version mismatch")
        if constraint.subject_region not in definition.supported_subject_regions:
            raise MetricRegistryValidationError("unsupported constraint subject region")
        if constraint.mask_type not in definition.required_mask_types:
            raise MetricRegistryValidationError("unsupported constraint mask type")
        if constraint.operator not in definition.supported_operators:
            raise MetricRegistryValidationError("unsupported constraint operator")
        if constraint.unit != definition.unit:
            raise MetricRegistryValidationError("constraint unit mismatch")
        unit = self._units[definition.unit]
        if not unit.minimum <= constraint.threshold <= unit.maximum:
            raise MetricRegistryValidationError("constraint threshold is out of range")
        if set(constraint.capability_requirements) != set(
            definition.capability_requirements
        ):
            raise MetricRegistryValidationError(
                "constraint capability requirements do not match metric definition"
            )
        if constraint.threshold_source == "policy_default":
            profile = definition.get_profile(str(constraint.profile_id))
            if (
                constraint.operator != profile.operator
                or constraint.threshold != profile.threshold
                or constraint.reference_mode != profile.reference_mode
            ):
                raise MetricRegistryValidationError(
                    "constraint does not match its versioned policy profile"
                )
        return constraint

    def match_aliases(self, raw_prompt: str) -> tuple[AliasMatch, ...]:
        """Return longest, disjoint registry aliases with exact raw offsets."""

        normalized = normalize_semantic_text(raw_prompt)
        candidates: list[tuple[int, int, str, str, str, str]] = []
        for definition in self._metrics.values():
            for alias in definition.aliases:
                candidates.extend(
                    _normalized_alias_occurrences(
                        normalized.text,
                        alias.normalized_text,
                        "metric",
                        definition.metric_id,
                        definition.metric_id,
                        alias.language,
                    )
                )
        # Profile aliases such as "keep" and "不要" are intentionally
        # reusable across metrics.  Emit one generic signal here; callers bind
        # it to a concrete metric through ``match_profile_aliases``.
        profile_signals: dict[str, ContractAlias] = {}
        for definition in self._metrics.values():
            for profile in definition.profiles:
                for alias in profile.aliases:
                    profile_signals.setdefault(alias.normalized_text, alias)
        for normalized_alias, alias in profile_signals.items():
            candidates.extend(
                _normalized_alias_occurrences(
                    normalized.text,
                    normalized_alias,
                    "profile_signal",
                    normalized_alias,
                    normalized_alias,
                    alias.language,
                )
            )
        for alias in self._operator_aliases:
            candidates.extend(
                _normalized_alias_occurrences(
                    normalized.text,
                    alias.normalized_text,
                    "operator",
                    alias.operator,
                    alias.operator,
                    alias.language,
                )
            )
        for alias in self._protection_aliases:
            candidates.extend(
                _normalized_alias_occurrences(
                    normalized.text,
                    alias.normalized_text,
                    "protection_signal",
                    "hard_protection",
                    "hard_protection",
                    alias.language,
                )
            )
        for unit in self._units.values():
            for input_unit in unit.input_units:
                for alias in input_unit.aliases:
                    candidates.extend(
                        _normalized_alias_occurrences(
                            normalized.text,
                            alias.normalized_text,
                            "unit",
                            input_unit.unit_id,
                            unit.unit_id,
                            alias.language,
                        )
                    )

        selected: list[tuple[int, int, str, str, str, str]] = []
        for candidate in sorted(
            candidates,
            key=lambda item: (item[0], -(item[1] - item[0]), item[2], item[3]),
        ):
            start, end = candidate[:2]
            if any(start < existing[1] and end > existing[0] for existing in selected):
                continue
            selected.append(candidate)
        matches: list[AliasMatch] = []
        for start, end, kind, concept_id, value, language in sorted(selected):
            raw_span = normalized.restore_span(start, end)
            matches.append(
                AliasMatch(
                    start=raw_span.start,
                    end=raw_span.end,
                    raw_text=raw_prompt[raw_span.start : raw_span.end],
                    normalized_text=normalized.text[start:end],
                    language=language,
                    kind=kind,
                    concept_id=concept_id,
                    value=value,
                )
            )
        return tuple(matches)

    def match_profile_aliases(
        self,
        metric_id: str,
        raw_prompt: str,
    ) -> tuple[AliasMatch, ...]:
        definition = self.get(metric_id)
        normalized = normalize_semantic_text(raw_prompt)
        candidates: list[tuple[int, int, str, str, str, str]] = []
        for profile in definition.profiles:
            for alias in profile.aliases:
                candidates.extend(
                    _normalized_alias_occurrences(
                        normalized.text,
                        alias.normalized_text,
                        "profile",
                        profile.profile_id,
                        definition.metric_id,
                        alias.language,
                    )
                )
        matches: list[AliasMatch] = []
        selected: list[tuple[int, int, str, str, str, str]] = []
        for candidate in sorted(
            candidates,
            key=lambda item: (item[0], -(item[1] - item[0]), item[3]),
        ):
            if any(
                candidate[0] < existing[1] and candidate[1] > existing[0]
                for existing in selected
            ):
                continue
            selected.append(candidate)
        for start, end, kind, concept_id, value, language in sorted(selected):
            raw_span = normalized.restore_span(start, end)
            matches.append(
                AliasMatch(
                    start=raw_span.start,
                    end=raw_span.end,
                    raw_text=raw_prompt[raw_span.start : raw_span.end],
                    normalized_text=normalized.text[start:end],
                    language=language,
                    kind=kind,
                    concept_id=concept_id,
                    value=value,
                )
            )
        return tuple(matches)

    def resolve_operator_alias(self, raw_text: str) -> str | None:
        normalized = normalize_contract_alias(raw_text)
        values = {
            alias.operator
            for alias in self._operator_aliases
            if alias.normalized_text == normalized
        }
        return next(iter(values)) if len(values) == 1 else None

    def resolve_input_unit_alias(
        self,
        raw_text: str,
    ) -> tuple[str, str] | None:
        normalized = normalize_contract_alias(raw_text)
        values = {
            (unit.unit_id, input_unit.unit_id)
            for unit in self._units.values()
            for input_unit in unit.input_units
            for alias in input_unit.aliases
            if alias.normalized_text == normalized
        }
        return next(iter(values)) if len(values) == 1 else None

    def normalize_explicit_threshold(
        self,
        metric_id: str,
        value: float,
        input_unit_id: str,
    ) -> float:
        definition = self.get(metric_id)
        return self._units[definition.unit].normalize_threshold(
            value,
            input_unit_id,
        )

    def as_schema_payload(self) -> dict[str, Any]:
        return {
            "metric_registry_version": self.registry_version,
            "operators": sorted(_OPERATORS),
            "protection_signals": sorted(
                {alias.normalized_text for alias in self._protection_aliases}
            ),
            "units": [
                {
                    "unit_id": unit.unit_id,
                    "minimum": unit.minimum,
                    "maximum": unit.maximum,
                    "display_precision": unit.display_precision,
                    "labels": dict(unit.labels),
                    "input_units": [
                        {
                            "unit_id": item.unit_id,
                            "to_canonical_factor": item.to_canonical_factor,
                            "implicit": item.implicit,
                        }
                        for item in unit.input_units
                    ],
                }
                for unit in self._units.values()
            ],
            "metrics": [
                {
                    "metric_id": metric.metric_id,
                    "metric_version": metric.metric_version,
                    "supported_subject_regions": sorted(
                        metric.supported_subject_regions
                    ),
                    "required_mask_types": sorted(metric.required_mask_types),
                    "capability_requirements": sorted(
                        metric.capability_requirements
                    ),
                    "supported_operators": sorted(metric.supported_operators),
                    "unit": metric.unit,
                    "requires_baseline": metric.requires_baseline,
                    "supports_magnitude_search": metric.supports_magnitude_search,
                    "enforcement_strategy": metric.enforcement_strategy,
                    "automatic_policy_trigger": metric.automatic_policy_trigger,
                    "automatic_profile_id": metric.automatic_profile_id,
                    "labels": dict(metric.labels),
                    "descriptions": dict(metric.descriptions),
                    "profiles": [
                        {
                            "profile_id": profile.profile_id,
                            "version": profile.version,
                            "operator": profile.operator,
                            "threshold": profile.threshold,
                            "unit": profile.unit,
                            "reference_mode": profile.reference_mode,
                        }
                        for profile in metric.profiles
                    ],
                }
                for metric in self._metrics.values()
            ],
        }


def _normalized_alias_occurrences(
    text: str,
    alias: str,
    kind: str,
    concept_id: str,
    value: str,
    language: str,
) -> list[tuple[int, int, str, str, str, str]]:
    results: list[tuple[int, int, str, str, str, str]] = []
    cursor = 0
    while cursor < len(text):
        start = text.find(alias, cursor)
        if start < 0:
            break
        end = start + len(alias)
        left_ok = start == 0 or not _word_character(text[start - 1]) or not _word_character(alias[0])
        right_ok = end == len(text) or not _word_character(text[end]) or not _word_character(alias[-1])
        if left_ok and right_ok:
            results.append((start, end, kind, concept_id, value, language))
        cursor = start + 1
    return results


def _word_character(value: str) -> bool:
    # ASCII words need lexical boundaries; CJK aliases are naturally adjacent
    # and must remain matchable without spaces.
    return value == "_" or (value.isascii() and value.isalnum())


def build_metric_registry(
    metrics: Iterable[MetricDefinition],
    *,
    units: Iterable[MetricUnitDefinition],
    operator_aliases: Iterable[OperatorAlias] = (),
    protection_aliases: Iterable[ContractAlias] = (),
    registry_version: str = METRIC_REGISTRY_VERSION,
) -> MetricCapabilityRegistry:
    resolved_operator_aliases = tuple(operator_aliases)
    return MetricCapabilityRegistry(
        metrics,
        units=units,
        operator_aliases=(
            resolved_operator_aliases
            if resolved_operator_aliases
            else _default_operator_aliases()
        ),
        protection_aliases=tuple(protection_aliases),
        registry_version=registry_version,
    )


def build_default_metric_registry(
    evaluators: Mapping[str, MetricEvaluator] | None = None,
) -> MetricCapabilityRegistry:
    resolved = dict(evaluators or _load_default_evaluators())
    required = {
        "highlight_clip_ratio",
        "shadow_clip_ratio",
        "protected_region_color_delta",
        "outside_edit_scope_change_ratio",
    }
    missing = required.difference(resolved)
    if missing:
        raise MetricRegistryValidationError(
            f"missing evaluator for {sorted(missing)[0]!r}"
        )
    unexpected = set(resolved).difference(required)
    if unexpected:
        raise MetricRegistryValidationError(
            f"unexpected default evaluator {sorted(unexpected)[0]!r}"
        )
    return MetricCapabilityRegistry(
        _default_metric_definitions(resolved),
        units=_default_units(),
        operator_aliases=_default_operator_aliases(),
        protection_aliases=_default_protection_aliases(),
    )


@lru_cache(maxsize=1)
def get_default_metric_registry() -> MetricCapabilityRegistry:
    return build_default_metric_registry()


def _load_default_evaluators() -> Mapping[str, MetricEvaluator]:
    try:
        from app.services.edit_contract_metrics import (
            evaluate_highlight_clip_ratio,
            evaluate_outside_edit_scope_change_ratio,
            evaluate_protected_region_color_delta,
            evaluate_shadow_clip_ratio,
        )
    except (ImportError, AttributeError) as exc:
        raise MetricRegistryValidationError(
            "default contract metric evaluators are unavailable"
        ) from exc
    return {
        "highlight_clip_ratio": evaluate_highlight_clip_ratio,
        "shadow_clip_ratio": evaluate_shadow_clip_ratio,
        "protected_region_color_delta": evaluate_protected_region_color_delta,
        "outside_edit_scope_change_ratio": evaluate_outside_edit_scope_change_ratio,
    }


def _aliases(language: str, *texts: str) -> tuple[ContractAlias, ...]:
    return tuple(ContractAlias(text, language) for text in texts)


def _default_units() -> tuple[MetricUnitDefinition, ...]:
    return (
        MetricUnitDefinition(
            unit_id="ratio",
            minimum=0.0,
            maximum=1.0,
            display_precision=4,
            labels={"zh": "比例", "en": "Ratio"},
            input_units=(
                MetricInputUnit(
                    unit_id="percent",
                    aliases=(ContractAlias("%", "en"), ContractAlias("％", "zh")),
                    to_canonical_factor=0.01,
                ),
                MetricInputUnit(
                    unit_id="ratio",
                    aliases=(),
                    implicit=True,
                ),
            ),
        ),
        MetricUnitDefinition(
            unit_id="delta_e",
            minimum=0.0,
            maximum=100.0,
            display_precision=2,
            labels={"zh": "色差 ΔE", "en": "Color difference ΔE"},
            input_units=(
                MetricInputUnit(
                    unit_id="delta_e",
                    aliases=(
                        ContractAlias("delta e", "en"),
                        ContractAlias("ΔE", "en"),
                        ContractAlias("色差", "zh"),
                    ),
                    implicit=True,
                ),
            ),
        ),
    )


def _default_operator_aliases() -> tuple[OperatorAlias, ...]:
    return (
        OperatorAlias("不超過", "zh", "<="),
        OperatorAlias("不要超過", "zh", "<="),
        OperatorAlias("不得超過", "zh", "<="),
        OperatorAlias("低於", "zh", "<="),
        OperatorAlias("最多", "zh", "<="),
        OperatorAlias("below", "en", "<="),
        OperatorAlias("under", "en", "<="),
        OperatorAlias("at most", "en", "<="),
        OperatorAlias("no more than", "en", "<="),
        OperatorAlias("不要惡化", "zh", "no_worse_than_baseline"),
        OperatorAlias("不得惡化", "zh", "no_worse_than_baseline"),
        OperatorAlias("不能變差", "zh", "no_worse_than_baseline"),
        OperatorAlias("no worse", "en", "no_worse_than_baseline"),
        OperatorAlias("do not worsen", "en", "no_worse_than_baseline"),
        OperatorAlias("don't worsen", "en", "no_worse_than_baseline"),
    )


def _default_protection_aliases() -> tuple[ContractAlias, ...]:
    return (
        *_aliases("zh", "不要", "不能", "不可", "不得", "避免", "保護"),
        *_aliases(
            "en",
            "keep",
            "keeping",
            "avoid",
            "without",
            "cannot",
            "can't",
            "must not",
            "protect",
            "preserve",
        ),
    )


def _default_metric_definitions(
    evaluators: Mapping[str, MetricEvaluator],
) -> tuple[MetricDefinition, ...]:
    return (
        MetricDefinition(
            metric_id="highlight_clip_ratio",
            metric_version="bt709_encoded_luma_v1",
            aliases=(
                *_aliases("zh", "高光過曝", "亮部過曝", "過曝"),
                *_aliases(
                    "en",
                    "clipped highlights",
                    "highlight clipping",
                    "overexposed highlights",
                    "overexposure",
                    "overexpose",
                    "overexposed",
                ),
            ),
            supported_subject_regions=frozenset({"all"}),
            required_mask_types=frozenset({"none"}),
            capability_requirements=frozenset(
                {"baseline_image", "candidate_image"}
            ),
            supported_operators=frozenset({"<=", "no_worse_than_baseline"}),
            unit="ratio",
            profiles=(
                MetricProtectionProfile(
                    profile_id="highlight_protection_v1",
                    version="highlight_protection_v1",
                    operator="no_worse_than_baseline",
                    threshold=0.01,
                    unit="ratio",
                    reference_mode="selected_target_baseline",
                    aliases=(
                        *_aliases("zh", "不要", "避免", "別讓"),
                        *_aliases(
                            "en", "avoid", "don't", "do not", "keep", "keeping"
                        ),
                    ),
                ),
            ),
            requires_baseline=True,
            supports_magnitude_search=True,
            enforcement_strategy="scale_search",
            labels={"zh": "高光過曝比例", "en": "Clipped highlights"},
            descriptions={
                "zh": "高於版本化亮度門檻的像素比例。",
                "en": "Ratio of pixels above the versioned highlight limit.",
            },
            evaluator=evaluators["highlight_clip_ratio"],
        ),
        MetricDefinition(
            metric_id="shadow_clip_ratio",
            metric_version="bt709_encoded_luma_v1",
            aliases=(
                *_aliases("zh", "暗部死黑", "陰影死黑", "死黑"),
                *_aliases(
                    "en",
                    "crushed shadows",
                    "shadow clipping",
                    "clipped shadows",
                    "black clipping",
                ),
            ),
            supported_subject_regions=frozenset({"all"}),
            required_mask_types=frozenset({"none"}),
            capability_requirements=frozenset(
                {"baseline_image", "candidate_image"}
            ),
            supported_operators=frozenset({"<=", "no_worse_than_baseline"}),
            unit="ratio",
            profiles=(
                MetricProtectionProfile(
                    profile_id="shadow_protection_v1",
                    version="shadow_protection_v1",
                    operator="no_worse_than_baseline",
                    threshold=0.01,
                    unit="ratio",
                    reference_mode="selected_target_baseline",
                    aliases=(
                        *_aliases("zh", "不要", "避免", "別讓"),
                        *_aliases(
                            "en", "avoid", "don't", "do not", "keep", "keeping"
                        ),
                    ),
                ),
            ),
            requires_baseline=True,
            supports_magnitude_search=True,
            enforcement_strategy="scale_search",
            labels={"zh": "暗部死黑比例", "en": "Clipped shadows"},
            descriptions={
                "zh": "低於版本化亮度門檻的像素比例。",
                "en": "Ratio of pixels below the versioned shadow limit.",
            },
            evaluator=evaluators["shadow_clip_ratio"],
        ),
        MetricDefinition(
            metric_id="protected_region_color_delta",
            metric_version="cie76_core_p95_v1",
            aliases=(
                *_aliases("zh", "顏色", "色差", "色彩"),
                *_aliases("en", "colors", "colours", "color change", "colour change"),
            ),
            supported_subject_regions=frozenset({"person"}),
            required_mask_types=frozenset({"semantic_person"}),
            capability_requirements=frozenset(
                {"baseline_image", "candidate_image", "subject_mask"}
            ),
            supported_operators=frozenset({"<="}),
            unit="delta_e",
            profiles=(
                MetricProtectionProfile(
                    profile_id="strict_color_preservation_v1",
                    version="strict_color_preservation_v1",
                    operator="<=",
                    threshold=4.0,
                    unit="delta_e",
                    reference_mode="selected_target_baseline",
                    aliases=(
                        *_aliases(
                            "zh",
                            "不要變",
                            "不能變",
                            "不可改變",
                            "維持不變",
                            "保持",
                            "保護",
                        ),
                        *_aliases(
                            "en",
                            "unchanged",
                            "without changing",
                            "cannot change",
                            "must not change",
                            "preserve",
                            "keep",
                            "keeping",
                        ),
                    ),
                ),
            ),
            requires_baseline=True,
            supports_magnitude_search=True,
            enforcement_strategy="protected_mask_then_search",
            labels={"zh": "保護區域色差", "en": "Protected-region color difference"},
            descriptions={
                "zh": "在可信人物核心遮罩內比較修圖前後色差。",
                "en": "Color difference inside the reliable person-mask core.",
            },
            evaluator=evaluators["protected_region_color_delta"],
        ),
        MetricDefinition(
            metric_id="outside_edit_scope_change_ratio",
            metric_version="cie76_outside_guard_v1",
            aliases=(
                *_aliases("zh", "外溢", "遮罩外變化", "範圍外變化"),
                *_aliases("en", "spill", "spillover", "outside-scope change"),
            ),
            supported_subject_regions=frozenset({"outside_edit_scope"}),
            required_mask_types=frozenset(set(EDIT_MASK_TYPES) - {"none"}),
            capability_requirements=frozenset(
                {"baseline_image", "candidate_image", "edit_mask"}
            ),
            supported_operators=frozenset({"<="}),
            unit="ratio",
            profiles=(
                MetricProtectionProfile(
                    profile_id="strict_local_scope_v1",
                    version="strict_local_scope_v1",
                    operator="<=",
                    threshold=0.005,
                    unit="ratio",
                    reference_mode="selected_target_baseline",
                    aliases=(
                        *_aliases("zh", "不要", "避免", "只改"),
                        *_aliases(
                            "en", "avoid", "do not", "only change", "keep", "keeping"
                        ),
                    ),
                ),
            ),
            requires_baseline=True,
            supports_magnitude_search=True,
            enforcement_strategy="scale_search",
            automatic_policy_trigger="local_edit",
            automatic_profile_id="strict_local_scope_v1",
            labels={"zh": "局部修圖外溢比例", "en": "Outside-scope change"},
            descriptions={
                "zh": "在 feather guard band 外可感知變化的像素比例。",
                "en": "Perceptibly changed pixels outside the feather guard band.",
            },
            evaluator=evaluators["outside_edit_scope_change_ratio"],
        ),
    )


__all__ = [
    "METRIC_REGISTRY_VERSION",
    "AliasMatch",
    "ContractAlias",
    "MetricCapabilityRegistry",
    "MetricDefinition",
    "MetricEvaluator",
    "MetricInputUnit",
    "MetricProtectionProfile",
    "MetricRegistryValidationError",
    "MetricUnitDefinition",
    "OperatorAlias",
    "build_default_metric_registry",
    "build_metric_registry",
    "get_default_metric_registry",
    "normalize_contract_alias",
]
