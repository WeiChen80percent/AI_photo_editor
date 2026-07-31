"""Immutable schemas for verifiable edit contracts.

The contract layer deliberately reuses :class:`SemanticIR` and
``RawSpanEvidence`` instead of introducing a second operation language.  This
module contains no prompt parsing, image processing, or route behavior; it is
the stable boundary shared by semantic adaptation, metric evaluators, search,
history, and API serialization.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from app.services.semantic_ir import (
    RawSpanEvidence,
    SemanticIR,
    SemanticOperation,
    UnresolvedSpan,
)


CONTRACT_SCHEMA_VERSION = "edit_contract_v1"
MAX_CONTRACT_OPERATIONS = 3
MAX_CONTRACT_CONSTRAINTS = 3

ContractOperator = Literal["<=", "no_worse_than_baseline"]
ThresholdSource = Literal["user_explicit", "policy_default"]
ReferenceMode = Literal["absolute_outcome", "selected_target_baseline"]
ConstraintSource = Literal["user", "system_policy"]
ContractDecisionSource = Literal["deterministic", "grounded_llm", "hybrid"]
ContractStatus = Literal[
    "passed",
    "adjusted",
    "clarification_required",
    "unsupported",
    "unsatisfied",
    "no_change",
]
ContractDisposition = Literal[
    "not_contract",
    "accepted",
    "clarification_required",
    "unsupported",
    "rejected",
]

_OPERATORS = frozenset({"<=", "no_worse_than_baseline"})
_THRESHOLD_SOURCES = frozenset({"user_explicit", "policy_default"})
_REFERENCE_MODES = frozenset(
    {"absolute_outcome", "selected_target_baseline"}
)
_CONSTRAINT_SOURCES = frozenset({"user", "system_policy"})
_DECISION_SOURCES = frozenset({"deterministic", "grounded_llm", "hybrid"})
_STATUSES = frozenset(
    {
        "passed",
        "adjusted",
        "clarification_required",
        "unsupported",
        "unsatisfied",
        "no_change",
    }
)
_DISPOSITIONS = frozenset(
    {
        "not_contract",
        "accepted",
        "clarification_required",
        "unsupported",
        "rejected",
    }
)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _non_empty(value: object, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _identifier(value: object, field_name: str) -> str:
    normalized = _non_empty(value, field_name).lower()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a stable identifier")
    return normalized


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be finite")
    return numeric


def _optional_finite(value: object | None, field_name: str) -> float | None:
    return None if value is None else _finite(value, field_name)


def _freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be a mapping")
    return MappingProxyType(dict(value))


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _json_value(value.as_dict())
    raise TypeError(f"value of type {type(value).__name__} is not JSON-safe")


@dataclass(frozen=True, slots=True)
class ContractConstraint:
    """One hard, measurable condition attached to an edit request."""

    constraint_id: str
    metric_id: str
    metric_version: str
    subject_region: str
    mask_type: str
    capability_requirements: tuple[str, ...]
    operator: ContractOperator
    threshold: float
    unit: str
    threshold_source: ThresholdSource
    reference_mode: ReferenceMode
    source: ConstraintSource = "user"
    profile_id: str | None = None
    hard: bool = True
    source_evidence: RawSpanEvidence | None = None
    evidence: tuple[RawSpanEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "constraint_id",
            _identifier(self.constraint_id, "constraint_id"),
        )
        object.__setattr__(
            self,
            "metric_id",
            _identifier(self.metric_id, "metric_id"),
        )
        object.__setattr__(
            self,
            "metric_version",
            _identifier(self.metric_version, "metric_version"),
        )
        object.__setattr__(
            self,
            "subject_region",
            _identifier(self.subject_region, "subject_region"),
        )
        object.__setattr__(
            self,
            "mask_type",
            _identifier(self.mask_type, "mask_type"),
        )
        requirements = tuple(
            _identifier(item, "capability requirement")
            for item in self.capability_requirements
        )
        if len(requirements) != len(set(requirements)):
            raise ValueError("capability requirements must be unique")
        object.__setattr__(self, "capability_requirements", requirements)

        operator = _non_empty(self.operator, "operator")
        if operator not in _OPERATORS:
            raise ValueError(f"unsupported contract operator {operator!r}")
        object.__setattr__(self, "operator", operator)
        threshold = _finite(self.threshold, "threshold")
        object.__setattr__(self, "threshold", threshold)
        object.__setattr__(self, "unit", _identifier(self.unit, "unit"))

        threshold_source = _non_empty(
            self.threshold_source,
            "threshold_source",
        ).lower()
        if threshold_source not in _THRESHOLD_SOURCES:
            raise ValueError("unsupported threshold_source")
        object.__setattr__(self, "threshold_source", threshold_source)

        reference_mode = _non_empty(
            self.reference_mode,
            "reference_mode",
        ).lower()
        if reference_mode not in _REFERENCE_MODES:
            raise ValueError("unsupported reference_mode")
        object.__setattr__(self, "reference_mode", reference_mode)

        source = _non_empty(self.source, "source").lower()
        if source not in _CONSTRAINT_SOURCES:
            raise ValueError("unsupported constraint source")
        object.__setattr__(self, "source", source)
        if self.profile_id is not None:
            object.__setattr__(
                self,
                "profile_id",
                _identifier(self.profile_id, "profile_id"),
            )
        if self.hard is not True:
            raise ValueError("v1 edit contract constraints must be hard")

        evidence = tuple(self.evidence)
        if not all(isinstance(item, RawSpanEvidence) for item in evidence):
            raise TypeError("constraint evidence must contain RawSpanEvidence")
        object.__setattr__(self, "evidence", evidence)
        if self.source_evidence is not None and not isinstance(
            self.source_evidence,
            RawSpanEvidence,
        ):
            raise TypeError("source_evidence must be RawSpanEvidence")
        if source == "user" and self.source_evidence is None:
            raise ValueError("user constraints require exact source evidence")
        if threshold_source == "user_explicit" and self.profile_id is not None:
            raise ValueError("explicit thresholds cannot also select a profile")
        if threshold_source == "policy_default" and self.profile_id is None:
            raise ValueError("policy defaults require a versioned profile_id")

    @property
    def source_text(self) -> str | None:
        return None if self.source_evidence is None else self.source_evidence.raw_text

    @property
    def source_start(self) -> int | None:
        return None if self.source_evidence is None else self.source_evidence.start

    @property
    def source_end(self) -> int | None:
        return None if self.source_evidence is None else self.source_evidence.end

    @property
    def language(self) -> str:
        return (
            "und"
            if self.source_evidence is None
            else self.source_evidence.language
        )

    @property
    def confidence(self) -> float:
        return (
            1.0
            if self.source_evidence is None
            else self.source_evidence.confidence
        )

    def matches(self, raw_prompt: str) -> bool:
        if self.source_evidence is not None and not self.source_evidence.matches(
            raw_prompt
        ):
            return False
        return all(item.matches(raw_prompt) for item in self.evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "metric_id": self.metric_id,
            "metric_version": self.metric_version,
            "subject_region": self.subject_region,
            "mask_type": self.mask_type,
            "capability_requirements": list(self.capability_requirements),
            "operator": self.operator,
            "threshold": self.threshold,
            "unit": self.unit,
            "threshold_source": self.threshold_source,
            "reference_mode": self.reference_mode,
            "source": self.source,
            "profile_id": self.profile_id,
            "hard": self.hard,
            "source_text": self.source_text,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "language": self.language,
            "confidence": self.confidence,
            "source_evidence": (
                None
                if self.source_evidence is None
                else self.source_evidence.as_dict()
            ),
            "evidence": [item.as_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class EditContractIR:
    """Fully grounded operation semantics plus measurable constraints."""

    raw_prompt: str
    semantic_ir: SemanticIR
    constraints: tuple[ContractConstraint, ...]
    unresolved_spans: tuple[UnresolvedSpan, ...] = ()
    language_sources: tuple[str, ...] = ()
    decision_source: ContractDecisionSource = "deterministic"
    schema_version: str = CONTRACT_SCHEMA_VERSION
    semantic_registry_version: str = "semantic_registry_v1"
    metric_registry_version: str = "edit_contract_metric_registry_v1"
    parser_version: str = "edit_contract_semantic_v1"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        raw_prompt = str(self.raw_prompt)
        if not raw_prompt.strip():
            raise ValueError("raw_prompt must not be empty")
        object.__setattr__(self, "raw_prompt", raw_prompt)
        if not isinstance(self.semantic_ir, SemanticIR):
            raise TypeError("semantic_ir must be SemanticIR")
        if self.semantic_ir.raw_prompt != raw_prompt:
            raise ValueError("semantic_ir must use the same raw prompt")
        if not 1 <= len(self.semantic_ir.operations) <= MAX_CONTRACT_OPERATIONS:
            raise ValueError("contract requires one to three edit operations")
        if self.semantic_ir.terminal_intent is not None:
            raise ValueError("terminal intents cannot be edit contracts")

        constraints = tuple(self.constraints)
        if not 1 <= len(constraints) <= MAX_CONTRACT_CONSTRAINTS:
            raise ValueError("contract requires one to three hard constraints")
        if not all(isinstance(item, ContractConstraint) for item in constraints):
            raise TypeError("constraints must contain ContractConstraint")
        identifiers = [item.constraint_id for item in constraints]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("constraint identifiers must be unique")
        if not all(item.matches(raw_prompt) for item in constraints):
            raise ValueError("constraint evidence must match the raw prompt")
        object.__setattr__(self, "constraints", constraints)

        unresolved = tuple(self.unresolved_spans)
        if not all(isinstance(item, UnresolvedSpan) for item in unresolved):
            raise TypeError("unresolved_spans must contain UnresolvedSpan")
        if any(not item.matches(raw_prompt) for item in unresolved):
            raise ValueError("unresolved spans must match the raw prompt")
        object.__setattr__(self, "unresolved_spans", unresolved)

        languages = tuple(
            dict.fromkeys(
                _non_empty(item, "language source").lower()
                for item in (
                    self.language_sources
                    or tuple(self.semantic_ir.language_sources)
                    + tuple(item.language for item in constraints)
                )
            )
        )
        object.__setattr__(self, "language_sources", languages or ("und",))
        source = _non_empty(self.decision_source, "decision_source").lower()
        if source not in _DECISION_SOURCES:
            raise ValueError("unsupported contract decision source")
        object.__setattr__(self, "decision_source", source)
        for field_name in (
            "schema_version",
            "semantic_registry_version",
            "metric_registry_version",
            "parser_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _identifier(getattr(self, field_name), field_name),
            )
        confidence = _finite(self.confidence, "confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "confidence", confidence)

    @property
    def operations(self) -> tuple[SemanticOperation, ...]:
        return self.semantic_ir.operations

    @property
    def is_fully_resolved(self) -> bool:
        return self.semantic_ir.is_fully_resolved and not self.unresolved_spans

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "semantic_registry_version": self.semantic_registry_version,
            "metric_registry_version": self.metric_registry_version,
            "parser_version": self.parser_version,
            "raw_prompt": self.raw_prompt,
            "semantic_ir": self.semantic_ir.as_dict(),
            "operations": [item.as_dict() for item in self.operations],
            "constraints": [item.as_dict() for item in self.constraints],
            "unresolved_spans": [
                item.as_dict() for item in self.unresolved_spans
            ],
            "language_sources": list(self.language_sources),
            "decision_source": self.decision_source,
            "confidence": self.confidence,
            "is_fully_resolved": self.is_fully_resolved,
        }


@dataclass(frozen=True, slots=True)
class MetricEvaluationContext:
    metric_id: str
    metric_version: str
    baseline_image: Any
    candidate_image: Any
    subject_region: str = "all"
    subject_mask: Any | None = None
    edit_mask: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _identifier(self.metric_id, "metric_id"))
        object.__setattr__(
            self,
            "metric_version",
            _identifier(self.metric_version, "metric_version"),
        )
        object.__setattr__(
            self,
            "subject_region",
            _identifier(self.subject_region, "subject_region"),
        )
        if self.baseline_image is None or self.candidate_image is None:
            raise ValueError("metric evaluation requires baseline and candidate images")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class MetricMeasurement:
    metric_id: str
    metric_version: str
    value: float
    unit: str
    sample_count: int
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_id", _identifier(self.metric_id, "metric_id"))
        object.__setattr__(
            self,
            "metric_version",
            _identifier(self.metric_version, "metric_version"),
        )
        object.__setattr__(self, "value", _finite(self.value, "value"))
        object.__setattr__(self, "unit", _identifier(self.unit, "unit"))
        if (
            isinstance(self.sample_count, bool)
            or not isinstance(self.sample_count, int)
            or self.sample_count < 0
        ):
            raise ValueError("sample_count must be a non-negative integer")
        object.__setattr__(self, "details", _freeze_mapping(self.details))

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "metric_version": self.metric_version,
            "value": self.value,
            "unit": self.unit,
            "sample_count": self.sample_count,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class MetricCheck:
    constraint_id: str
    metric_id: str
    metric_version: str
    operator: ContractOperator
    unit: str
    policy_threshold: float
    effective_threshold: float
    candidate_value: float
    passed: bool
    baseline_value: float | None = None
    threshold_source: ThresholdSource = "policy_default"
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("constraint_id", "metric_id", "metric_version", "unit"):
            object.__setattr__(
                self,
                field_name,
                _identifier(getattr(self, field_name), field_name),
            )
        if self.operator not in _OPERATORS:
            raise ValueError("unsupported metric check operator")
        for field_name in (
            "policy_threshold",
            "effective_threshold",
            "candidate_value",
        ):
            object.__setattr__(
                self,
                field_name,
                _finite(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "baseline_value",
            _optional_finite(self.baseline_value, "baseline_value"),
        )
        if self.threshold_source not in _THRESHOLD_SOURCES:
            raise ValueError("unsupported metric check threshold_source")
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be boolean")
        object.__setattr__(self, "details", _freeze_mapping(self.details))

    def as_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "metric_id": self.metric_id,
            "metric_version": self.metric_version,
            "operator": self.operator,
            "unit": self.unit,
            "policy_threshold": self.policy_threshold,
            "effective_threshold": self.effective_threshold,
            "baseline_value": self.baseline_value,
            "candidate_value": self.candidate_value,
            "threshold_source": self.threshold_source,
            "passed": self.passed,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class ContractSearchAttempt:
    scale: float
    checks: tuple[MetricCheck, ...]
    passed: bool
    render_ms: float = 0.0
    verification_ms: float = 0.0
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        scale = _finite(self.scale, "scale")
        if not 0.0 <= scale <= 1.0:
            raise ValueError("search scale must be between 0 and 1")
        object.__setattr__(self, "scale", scale)
        checks = tuple(self.checks)
        if not all(isinstance(item, MetricCheck) for item in checks):
            raise TypeError("checks must contain MetricCheck")
        object.__setattr__(self, "checks", checks)
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be boolean")
        if self.failure_reason is None and self.passed != (
            bool(checks) and all(item.passed for item in checks)
        ):
            raise ValueError("attempt passed state must match all metric checks")
        for field_name in ("render_ms", "verification_ms"):
            value = _finite(getattr(self, field_name), field_name)
            if value < 0:
                raise ValueError(f"{field_name} cannot be negative")
            object.__setattr__(self, field_name, value)
        if self.failure_reason is not None:
            object.__setattr__(
                self,
                "failure_reason",
                _non_empty(self.failure_reason, "failure_reason"),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "scale": self.scale,
            "checks": [item.as_dict() for item in self.checks],
            "passed": self.passed,
            "render_ms": self.render_ms,
            "verification_ms": self.verification_ms,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True, slots=True)
class EditContractReport:
    contract_ir: EditContractIR
    status: ContractStatus
    contract_hash: str
    target_edit_id: str
    selected_target_baseline_path: str
    render_anchor_path: str
    mask_source_path: str
    requested_edit_plan: Mapping[str, Any]
    requested_parameter_vector: Mapping[str, float]
    requested_scale: float
    search_policy_version: str
    applied_scale: float | None = None
    actual_parameter_vector: Mapping[str, float] = field(default_factory=dict)
    checks: tuple[MetricCheck, ...] = ()
    attempts: tuple[ContractSearchAttempt, ...] = ()
    timings: Mapping[str, float] = field(default_factory=dict)
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.contract_ir, EditContractIR):
            raise TypeError("contract_ir must be EditContractIR")
        status = _non_empty(self.status, "status").lower()
        if status not in _STATUSES:
            raise ValueError("unsupported contract status")
        object.__setattr__(self, "status", status)
        digest = _non_empty(self.contract_hash, "contract_hash").lower()
        if not _HASH_PATTERN.fullmatch(digest):
            raise ValueError("contract_hash must be a SHA-256 hex digest")
        object.__setattr__(self, "contract_hash", digest)
        for field_name in (
            "target_edit_id",
            "selected_target_baseline_path",
            "render_anchor_path",
            "mask_source_path",
            "search_policy_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _non_empty(getattr(self, field_name), field_name),
            )
        requested_scale = _finite(self.requested_scale, "requested_scale")
        if requested_scale != 1.0:
            raise ValueError("v1 requested_scale must be 1.0")
        object.__setattr__(self, "requested_scale", requested_scale)
        applied = _optional_finite(self.applied_scale, "applied_scale")
        if applied is not None and not 0.0 <= applied <= 1.0:
            raise ValueError("applied_scale must be between 0 and 1")
        object.__setattr__(self, "applied_scale", applied)
        object.__setattr__(
            self,
            "requested_edit_plan",
            _freeze_mapping(self.requested_edit_plan),
        )
        object.__setattr__(
            self,
            "requested_parameter_vector",
            _freeze_numeric_mapping(
                self.requested_parameter_vector,
                "requested_parameter_vector",
            ),
        )
        object.__setattr__(
            self,
            "actual_parameter_vector",
            _freeze_numeric_mapping(
                self.actual_parameter_vector,
                "actual_parameter_vector",
            ),
        )
        checks = tuple(self.checks)
        attempts = tuple(self.attempts)
        if not all(isinstance(item, MetricCheck) for item in checks):
            raise TypeError("checks must contain MetricCheck")
        if not all(isinstance(item, ContractSearchAttempt) for item in attempts):
            raise TypeError("attempts must contain ContractSearchAttempt")
        object.__setattr__(self, "checks", checks)
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(
            self,
            "timings",
            _freeze_numeric_mapping(self.timings, "timings", non_negative=True),
        )
        if status in {"passed", "adjusted"}:
            if applied is None or applied <= 0:
                raise ValueError("successful reports require a positive applied scale")
            if not checks or not all(item.passed for item in checks):
                raise ValueError("successful reports require all final checks to pass")
            if self.failure_reason is not None:
                raise ValueError("successful reports cannot carry a failure reason")
        elif self.failure_reason is not None:
            object.__setattr__(
                self,
                "failure_reason",
                _non_empty(self.failure_reason, "failure_reason"),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "contract_hash": self.contract_hash,
            "contract_ir": self.contract_ir.as_dict(),
            "target_edit_id": self.target_edit_id,
            "selected_target_baseline_path": self.selected_target_baseline_path,
            "render_anchor_path": self.render_anchor_path,
            "mask_source_path": self.mask_source_path,
            "requested_edit_plan": dict(self.requested_edit_plan),
            "requested_parameter_vector": dict(self.requested_parameter_vector),
            "requested_scale": self.requested_scale,
            "search_policy_version": self.search_policy_version,
            "applied_scale": self.applied_scale,
            "actual_parameter_vector": dict(self.actual_parameter_vector),
            "checks": [item.as_dict() for item in self.checks],
            "attempts": [item.as_dict() for item in self.attempts],
            "timings": dict(self.timings),
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True, slots=True)
class EditContractError(ValueError):
    code: str
    message: str
    disposition: ContractDisposition
    issues: tuple[dict[str, Any], ...] = ()
    status_code: int = 422
    retryable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _identifier(self.code, "error code"))
        object.__setattr__(self, "message", _non_empty(self.message, "message"))
        disposition = _non_empty(self.disposition, "disposition").lower()
        if disposition not in _DISPOSITIONS or disposition in {
            "accepted",
            "not_contract",
        }:
            raise ValueError("errors require a failure disposition")
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(
            self,
            "issues",
            tuple(dict(issue) for issue in self.issues),
        )
        if (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 400 <= self.status_code <= 599
        ):
            raise ValueError("status_code must be an HTTP error status")
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be boolean")

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "disposition": self.disposition,
            "issues": [dict(issue) for issue in self.issues],
            "status_code": self.status_code,
            "retryable": self.retryable,
        }


def _freeze_numeric_mapping(
    value: Mapping[str, Any],
    field_name: str,
    *,
    non_negative: bool = False,
) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    normalized: dict[str, float] = {}
    for key, item in value.items():
        normalized_key = _non_empty(key, f"{field_name} key")
        numeric = _finite(item, f"{field_name}.{normalized_key}")
        if non_negative and numeric < 0:
            raise ValueError(f"{field_name}.{normalized_key} cannot be negative")
        normalized[normalized_key] = numeric
    return MappingProxyType(normalized)


def compute_contract_hash(
    contract_ir: EditContractIR,
    *,
    target_identity: str,
    baseline_identity: str,
    render_anchor_identity: str,
    requested_edit_plan: Mapping[str, Any],
    requested_parameters: Mapping[str, float],
    search_policy_version: str,
) -> str:
    """Hash only immutable request inputs, never a future edit id or timings."""

    if not isinstance(contract_ir, EditContractIR):
        raise TypeError("contract_ir must be EditContractIR")
    payload = {
        "contract_ir": _canonical_contract_for_hash(contract_ir),
        "target_identity": _non_empty(target_identity, "target_identity"),
        "baseline_identity": _non_empty(baseline_identity, "baseline_identity"),
        "render_anchor_identity": _non_empty(
            render_anchor_identity,
            "render_anchor_identity",
        ),
        "requested_edit_plan": _canonical_contract_value(
            requested_edit_plan,
        ),
        "requested_parameters": _json_value(requested_parameters),
        "search_policy_version": _non_empty(
            search_policy_version,
            "search_policy_version",
        ),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _canonical_contract_for_hash(contract_ir: EditContractIR) -> dict[str, Any]:
    """Project stable contract semantics for idempotency hashing.

    Confidence is useful provenance, but it is not part of the executable
    contract and may legitimately drift between equivalent grounded retries.
    Constraint identifiers are assigned from provider output order, so the
    semantic constraint set is sorted after those positional identifiers are
    removed. Exact source spans/text, metric versions, policies, and all other
    executable fields remain in the hash.
    """

    projected = _canonical_contract_value(contract_ir.as_dict())
    if not isinstance(projected, dict):  # pragma: no cover - defensive
        raise TypeError("canonical contract projection must be a mapping")
    return projected


def _canonical_contract_value(value: Any, *, field_name: str = "") -> Any:
    if isinstance(value, Mapping):
        result = {
            str(key): _canonical_contract_value(item, field_name=str(key))
            for key, item in value.items()
            if str(key) not in {"confidence", "constraint_id"}
        }
        constraints = result.get("constraints")
        if isinstance(constraints, list):
            result["constraints"] = sorted(
                constraints,
                key=_canonical_json_sort_key,
            )
        evidence = result.get("evidence")
        if isinstance(evidence, list):
            result["evidence"] = sorted(evidence, key=_canonical_json_sort_key)
        return result
    if isinstance(value, (tuple, list)):
        items = [
            _canonical_contract_value(item, field_name=field_name)
            for item in value
        ]
        if field_name in {"capability_requirements", "language_sources"}:
            return sorted(items, key=_canonical_json_sort_key)
        return items
    return _json_value(value)


def _canonical_json_sort_key(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "MAX_CONTRACT_CONSTRAINTS",
    "MAX_CONTRACT_OPERATIONS",
    "ConstraintSource",
    "ContractConstraint",
    "ContractDecisionSource",
    "ContractDisposition",
    "ContractOperator",
    "ContractSearchAttempt",
    "ContractStatus",
    "EditContractError",
    "EditContractIR",
    "EditContractReport",
    "MetricCheck",
    "MetricEvaluationContext",
    "MetricMeasurement",
    "ReferenceMode",
    "ThresholdSource",
    "compute_contract_hash",
]
