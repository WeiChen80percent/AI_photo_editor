"""Fail-closed adapter for grounded LLM semantic candidates.

This module is intentionally not an LLM transport.  It accepts an already
produced candidate mapping, grounds every semantic field in an exact slice of
the raw prompt, reconciles it with deterministic lexical facts, and sends the
result through the shared semantic validator.

The candidate contract contains semantic intent only.  Engine names, render
parameters, and processor-specific values are forbidden here.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.semantic_ir import RawSpanEvidence, SemanticIR, SemanticOperation
from app.services.semantic_normalizer import normalize_semantic_text
from app.services.semantic_registry import (
    DEFAULT_PARAMETER_REGISTRY,
    ParameterRegistry,
    normalize_alias_text,
)
from app.services.semantic_slot_extractor import (
    SlotExtraction,
    extract_semantic_slots,
)
from app.services.semantic_validator import (
    MAX_SEMANTIC_OPERATIONS,
    SemanticValidationError,
    validate_semantic_ir,
)


GROUNDED_LLM_PARSER_VERSION = "grounded_llm_semantic_v1"

_TOP_LEVEL_FIELDS = frozenset({"operations", "confidence"})
_OPERATION_FIELDS = frozenset(
    {
        "axis",
        "kind",
        "direction",
        "strength",
        "numeric",
        "reset",
        "region",
        "evidence",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {"start", "end", "raw_text", "language", "confidence"}
)
_EVIDENCE_SLOTS = frozenset(
    {
        "axis",
        "direction",
        "strength",
        "numeric",
        "numeric_relation",
        "reset",
        "region",
    }
)
_OPERATION_KINDS = frozenset(
    {
        "explicit_axis",
        "macro",
        "observation",
        "relative_numeric",
        "absolute",
        "reset",
    }
)
_STRENGTHS = frozenset({"subtle", "normal", "strong"})
_UNSAFE_OUTPUT_FIELDS = frozenset(
    {
        "engine",
        "engine_parameters",
        "opencv_parameters",
        "parameter_values",
        "parameters",
        "processor",
        "render_parameters",
    }
)
_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$"
)


@dataclass(frozen=True, slots=True)
class GroundedLLMError(ValueError):
    """Structured, returnable rejection from the grounded LLM boundary."""

    code: str
    message: str
    issues: tuple[dict[str, Any], ...] = ()
    status_code: int = 422
    retryable: bool = False

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "issues": [dict(issue) for issue in self.issues],
            "status_code": self.status_code,
            "retryable": self.retryable,
        }


@dataclass(frozen=True, slots=True)
class _CandidateFailure(Exception):
    error: GroundedLLMError


@dataclass(frozen=True, slots=True)
class _DeterministicFacts:
    axes: frozenset[str]
    axis_directions: Mapping[str, frozenset[int]]
    shared_directions: frozenset[int]
    regions: frozenset[str]


GroundedLLMResult = SemanticIR | GroundedLLMError
CandidateProvider = Callable[[str], object]


def deterministic_semantics_are_complete(
    extraction: SlotExtraction | None,
    *,
    raw_prompt: str | None = None,
) -> bool:
    """Return whether deterministic slots already fully specify a safe edit.

    This is deliberately conservative.  A complete result must be fully
    lexed, unambiguous, contain one to three axes, have at most one region,
    and provide either reset, a numeric relation, or a direction for every
    axis.  When true, an LLM must not be called or allowed to override it.
    """

    if extraction is None or not isinstance(extraction, SlotExtraction):
        return False
    if raw_prompt is not None and extraction.normalized.raw_text != raw_prompt:
        return False
    if not extraction.is_fully_lexed:
        return False

    slots = tuple(
        slot
        for slot in extraction.slots
        if not slot.is_ambiguous and slot.interpretation is not None
    )
    axes = {
        str(slot.concept_id)
        for slot in slots
        if slot.namespace == "axis"
    }
    if not 1 <= len(axes) <= MAX_SEMANTIC_OPERATIONS:
        return False

    regions = {
        str(slot.value)
        for slot in slots
        if slot.slot == "region"
    }
    if len(regions) > 1:
        return False

    if any(slot.slot in {"guard", "negation", "terminal"} for slot in slots):
        return False

    reset_count = sum(
        slot.slot == "operation" and slot.value == "reset" for slot in slots
    )
    if reset_count:
        return reset_count == 1 and not any(
            slot.slot in {"direction", "numeric", "numeric_relation"}
            for slot in slots
        )

    numeric_values = [slot for slot in slots if slot.slot == "numeric"]
    numeric_relations = [
        slot for slot in slots if slot.slot == "numeric_relation"
    ]
    if numeric_values or numeric_relations:
        return (
            len(axes) == 1
            and len(numeric_values) == 1
            and len(numeric_relations) == 1
        )

    shared_directions = {
        int(slot.value)
        for slot in slots
        if slot.slot == "direction" and slot.value in {-1, 1}
    }
    if len(shared_directions) > 1:
        return False
    shared_direction = next(iter(shared_directions), None)
    axis_directions: dict[str, set[int]] = {}
    for slot in slots:
        if (
            slot.namespace == "axis"
            and slot.requested_direction in {-1, 1}
        ):
            axis_directions.setdefault(
                str(slot.concept_id),
                set(),
            ).add(
                int(slot.requested_direction)
            )
    if any(len(directions) > 1 for directions in axis_directions.values()):
        return False
    return all(
        bool(axis_directions.get(axis_id)) or shared_direction is not None
        for axis_id in axes
    )


def adapt_grounded_llm_candidate(
    raw_prompt: str,
    candidate: object,
    *,
    deterministic: SlotExtraction | None = None,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
) -> GroundedLLMResult:
    """Convert one candidate mapping into validated, grounded ``SemanticIR``.

    All expected user/data errors are returned as ``GroundedLLMError`` rather
    than raised.  A successful return has already crossed
    ``validate_semantic_ir``.
    """

    prompt = str(raw_prompt)
    if not prompt.strip():
        return _error(
            "grounded_llm_invalid_prompt",
            "The raw prompt must not be empty.",
            reason="empty_raw_prompt",
        )

    deterministic_error = _check_deterministic_input(
        prompt,
        deterministic,
    )
    if deterministic_error is not None:
        return deterministic_error
    if deterministic_semantics_are_complete(
        deterministic,
        raw_prompt=prompt,
    ):
        return _error(
            "grounded_llm_not_needed",
            (
                "Deterministic semantics are already complete; the LLM "
                "candidate was not allowed to override them."
            ),
            reason="deterministic_semantics_complete",
        )

    if candidate is None or candidate == {}:
        return _error(
            "grounded_llm_empty_response",
            "The grounded LLM returned no semantic candidate.",
            reason="empty_candidate",
        )
    if not isinstance(candidate, Mapping):
        return _error(
            "grounded_llm_malformed_response",
            "The grounded LLM candidate must be a mapping.",
            reason="candidate_not_mapping",
            actual_type=type(candidate).__name__,
        )

    unsafe_path = _find_unsafe_output_field(candidate)
    if unsafe_path is not None:
        return _error(
            "grounded_llm_unsafe_output",
            "LLM semantic candidates cannot contain render parameters.",
            reason="render_parameter_output_forbidden",
            field_path=unsafe_path,
        )

    try:
        _require_only_fields(candidate, _TOP_LEVEL_FIELDS, "candidate")
        raw_operations = candidate.get("operations")
        if raw_operations is None or (
            _is_sequence(raw_operations) and len(raw_operations) == 0
        ):
            _fail(
                "grounded_llm_empty_response",
                "The grounded LLM returned no semantic operations.",
                reason="empty_operations",
            )
        if not _is_sequence(raw_operations):
            _fail(
                "grounded_llm_malformed_response",
                "Candidate operations must be a list.",
                reason="operations_not_list",
            )
        assert isinstance(raw_operations, Sequence)
        if len(raw_operations) > MAX_SEMANTIC_OPERATIONS:
            _fail(
                "grounded_llm_operation_limit",
                (
                    "A grounded LLM candidate may contain at most "
                    f"{MAX_SEMANTIC_OPERATIONS} operations."
                ),
                reason="operation_limit",
                operation_count=len(raw_operations),
            )

        confidence = _confidence(
            candidate.get("confidence", 1.0),
            field_path="candidate.confidence",
        )
        operations = tuple(
            _parse_operation(
                prompt,
                operation,
                index=index,
                registry=registry,
            )
            for index, operation in enumerate(raw_operations)
        )
        regions = {operation.region for operation in operations}
        if len(regions) != 1:
            _fail(
                "grounded_llm_multi_region",
                "All grounded operations must use the same edit region.",
                reason="multiple_regions",
                regions=sorted(regions),
            )
        region = next(iter(regions))

        deterministic_conflict = _compare_deterministic_facts(
            operations,
            deterministic,
        )
        if deterministic_conflict is not None:
            return deterministic_conflict

        languages = tuple(
            dict.fromkeys(
                evidence.language
                for operation in operations
                for evidence in operation.evidence
            )
        ) or ("und",)
        ir = SemanticIR(
            raw_prompt=prompt,
            operations=operations,
            region=region,
            language_sources=languages,
            decision_source="grounded_llm",
            parser_version=GROUNDED_LLM_PARSER_VERSION,
            confidence=confidence,
        )
        return validate_semantic_ir(ir, registry=registry, engine=engine)
    except _CandidateFailure as exc:
        return exc.error
    except SemanticValidationError as exc:
        return GroundedLLMError(
            code=exc.code,
            message=exc.message,
            issues=tuple(dict(issue) for issue in exc.issues),
            status_code=exc.status_code,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return _error(
            "grounded_llm_malformed_response",
            "The grounded LLM candidate does not match the semantic schema.",
            reason="invalid_candidate_value",
            detail=str(exc),
        )


def invoke_grounded_llm_candidate(
    raw_prompt: str,
    provider: CandidateProvider,
    *,
    deterministic: SlotExtraction | None = None,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
) -> GroundedLLMResult:
    """Call an injected candidate provider behind a fail-closed boundary.

    The provider is supplied by the caller; this module performs no transport
    or network activity.  Timeout and provider failures become structured,
    retryable errors.  A complete deterministic extraction short-circuits
    before the provider is invoked.
    """

    prompt = str(raw_prompt)
    deterministic_error = _check_deterministic_input(prompt, deterministic)
    if deterministic_error is not None:
        return deterministic_error
    if deterministic_semantics_are_complete(
        deterministic,
        raw_prompt=prompt,
    ):
        return _error(
            "grounded_llm_not_needed",
            (
                "Deterministic semantics are already complete; the LLM "
                "provider was not invoked."
            ),
            reason="deterministic_semantics_complete",
        )
    if not callable(provider):
        return _error(
            "grounded_llm_malformed_provider",
            "The grounded LLM provider must be callable.",
            reason="provider_not_callable",
        )
    try:
        candidate = provider(prompt)
    except TimeoutError as exc:
        return _error(
            "grounded_llm_timeout",
            "The grounded LLM provider timed out; no edit was applied.",
            status_code=503,
            retryable=True,
            reason="provider_timeout",
            detail=str(exc),
        )
    except Exception as exc:  # transport/provider boundary is fail-closed
        return _error(
            "grounded_llm_unavailable",
            "The grounded LLM provider failed; no edit was applied.",
            status_code=503,
            retryable=True,
            reason="provider_exception",
            exception_type=type(exc).__name__,
        )
    return adapt_grounded_llm_candidate(
        prompt,
        candidate,
        deterministic=deterministic,
        registry=registry,
        engine=engine,
    )


def _parse_operation(
    prompt: str,
    payload: object,
    *,
    index: int,
    registry: ParameterRegistry,
) -> SemanticOperation:
    path = f"candidate.operations[{index}]"
    if not isinstance(payload, Mapping):
        _fail(
            "grounded_llm_malformed_response",
            "Each grounded operation must be a mapping.",
            reason="operation_not_mapping",
            operation_index=index,
        )
    _require_only_fields(payload, _OPERATION_FIELDS, path)

    axis = _required_identifier(payload, "axis", path)
    if axis not in registry.axes:
        _fail(
            "grounded_llm_unknown_axis",
            "The grounded LLM selected an axis outside the registry.",
            reason="unknown_axis",
            operation_index=index,
            axis=axis,
        )
    kind = _required_identifier(payload, "kind", path)
    if kind not in _OPERATION_KINDS:
        _fail(
            "grounded_llm_invalid_operation",
            "The grounded LLM selected an unsupported operation kind.",
            reason="unknown_operation_kind",
            operation_index=index,
            kind=kind,
        )

    raw_evidence = payload.get("evidence")
    if not isinstance(raw_evidence, Mapping):
        _fail(
            "grounded_llm_missing_evidence",
            "Each operation needs a field-by-field evidence mapping.",
            reason="missing_evidence_mapping",
            operation_index=index,
        )
    _require_only_fields(raw_evidence, _EVIDENCE_SLOTS, f"{path}.evidence")

    region_explicit = "region" in payload
    region = str(payload.get("region", "all")).strip()
    if not region:
        _fail(
            "grounded_llm_malformed_response",
            "Operation region must not be empty.",
            reason="empty_region",
            operation_index=index,
        )
    if region not in registry.regions:
        _fail(
            "grounded_llm_unknown_region",
            "The grounded LLM selected a region outside the registry.",
            reason="unknown_region",
            operation_index=index,
            region=region,
        )

    evidence: list[RawSpanEvidence] = [
        _parse_evidence(
            prompt,
            raw_evidence,
            field="axis",
            concept_id=axis,
            path=path,
            registry=registry,
            expected_value=axis,
        )
    ]
    if region_explicit:
        evidence.append(
            _parse_evidence(
                prompt,
                raw_evidence,
                field="region",
                concept_id=region,
                path=path,
                registry=registry,
                expected_value=region,
            )
        )
    elif "region" in raw_evidence:
        _fail(
            "grounded_llm_malformed_response",
            "Region evidence cannot appear without a region field.",
            reason="orphan_evidence",
            operation_index=index,
            evidence_field="region",
        )

    if kind in {"explicit_axis", "macro", "observation"}:
        direction = _direction(payload, path)
        evidence.append(
            _parse_evidence(
                prompt,
                raw_evidence,
                field="direction",
                concept_id=(
                    "direction_positive"
                    if direction > 0
                    else "direction_negative"
                ),
                path=path,
                registry=registry,
                expected_value=direction,
            )
        )
        strength = str(payload.get("strength", "normal")).strip().lower()
        if strength not in _STRENGTHS:
            _fail(
                "grounded_llm_invalid_operation",
                "Relative operation strength is outside the semantic schema.",
                reason="invalid_strength",
                operation_index=index,
                strength=strength,
            )
        if "strength" in payload:
            evidence.append(
                _parse_evidence(
                    prompt,
                    raw_evidence,
                    field="strength",
                    concept_id=f"strength_{strength}",
                    path=path,
                    registry=registry,
                    expected_value=strength,
                )
            )
        elif "strength" in raw_evidence:
            _orphan_evidence(index, "strength")
        _forbid_fields(payload, raw_evidence, index, "numeric", "reset")
        return SemanticOperation(
            axis_id=axis,
            operation_kind=kind,
            direction=direction,
            strength=strength,
            region=region,
            evidence=tuple(evidence),
            language_sources=_evidence_languages(evidence),
        )

    if kind == "relative_numeric":
        direction = _direction(payload, path)
        numeric = _numeric(payload, path)
        if numeric == 0 or (numeric > 0) != (direction > 0):
            _fail(
                "grounded_llm_invalid_operation",
                "Relative numeric direction must match a non-zero delta.",
                reason="numeric_direction_mismatch",
                operation_index=index,
                direction=direction,
                numeric=numeric,
            )
        evidence.extend(
            (
                _parse_evidence(
                    prompt,
                    raw_evidence,
                    field="direction",
                    concept_id=(
                        "direction_positive"
                        if direction > 0
                        else "direction_negative"
                    ),
                    path=path,
                    registry=registry,
                    expected_value=direction,
                ),
                _parse_evidence(
                    prompt,
                    raw_evidence,
                    field="numeric",
                    concept_id="numeric_literal",
                    path=path,
                    registry=registry,
                    expected_value=numeric,
                ),
            )
        )
        if "numeric_relation" in raw_evidence:
            evidence.append(
                _parse_evidence(
                    prompt,
                    raw_evidence,
                    field="numeric_relation",
                    concept_id="numeric_relative",
                    path=path,
                    registry=registry,
                    expected_value="relative",
                )
            )
        _forbid_fields(payload, raw_evidence, index, "strength", "reset")
        return SemanticOperation(
            axis_id=axis,
            operation_kind="relative_numeric",
            direction=direction,
            strength=None,
            value=numeric,
            region=region,
            evidence=tuple(evidence),
            language_sources=_evidence_languages(evidence),
        )

    if kind == "absolute":
        numeric = _numeric(payload, path)
        evidence.append(
            _parse_evidence(
                prompt,
                raw_evidence,
                field="numeric",
                concept_id="numeric_literal",
                path=path,
                registry=registry,
                expected_value=numeric,
            )
        )
        if "numeric_relation" in raw_evidence:
            evidence.append(
                _parse_evidence(
                    prompt,
                    raw_evidence,
                    field="numeric_relation",
                    concept_id="numeric_absolute",
                    path=path,
                    registry=registry,
                    expected_value="absolute",
                )
            )
        _forbid_fields(
            payload,
            raw_evidence,
            index,
            "direction",
            "strength",
            "reset",
        )
        return SemanticOperation(
            axis_id=axis,
            operation_type="absolute",
            operation_kind="absolute",
            direction=None,
            strength=None,
            value=numeric,
            region=region,
            evidence=tuple(evidence),
            language_sources=_evidence_languages(evidence),
        )

    reset = payload.get("reset")
    if reset is not True:
        _fail(
            "grounded_llm_invalid_operation",
            "Reset operations require the literal boolean reset=true.",
            reason="invalid_reset_marker",
            operation_index=index,
        )
    evidence.append(
        _parse_evidence(
            prompt,
            raw_evidence,
            field="reset",
            concept_id="axis_reset",
            path=path,
            registry=registry,
            expected_value="reset",
        )
    )
    _forbid_fields(
        payload,
        raw_evidence,
        index,
        "direction",
        "strength",
        "numeric",
    )
    return SemanticOperation(
        axis_id=axis,
        operation_type="reset",
        operation_kind="reset",
        direction=None,
        strength=None,
        region=region,
        evidence=tuple(evidence),
        language_sources=_evidence_languages(evidence),
    )


def _parse_evidence(
    prompt: str,
    evidence_map: Mapping[object, object],
    *,
    field: str,
    concept_id: str,
    path: str,
    registry: ParameterRegistry,
    expected_value: object,
) -> RawSpanEvidence:
    payload = evidence_map.get(field)
    if not isinstance(payload, Mapping):
        _fail(
            "grounded_llm_missing_evidence",
            "A semantic field is missing exact source evidence.",
            reason="missing_field_evidence",
            field_path=f"{path}.{field}",
        )
    _require_only_fields(
        payload,
        _EVIDENCE_FIELDS,
        f"{path}.evidence.{field}",
    )
    missing = {"start", "end", "raw_text"}.difference(payload)
    if missing:
        _fail(
            "grounded_llm_missing_evidence",
            "Evidence must contain start, end, and raw_text.",
            reason="incomplete_evidence",
            field_path=f"{path}.{field}",
            missing=sorted(missing),
        )

    start = payload["start"]
    end = payload["end"]
    raw_text = payload["raw_text"]
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or not isinstance(raw_text, str)
    ):
        _fail(
            "grounded_llm_invalid_evidence",
            "Evidence offsets must be integers and raw_text must be text.",
            reason="invalid_evidence_types",
            field_path=f"{path}.{field}",
        )
    if start < 0 or end <= start or end > len(prompt):
        _fail(
            "grounded_llm_invalid_evidence",
            "Evidence offsets lie outside the raw prompt.",
            reason="evidence_out_of_bounds",
            field_path=f"{path}.{field}",
            start=start,
            end=end,
        )
    if prompt[start:end] != raw_text or not raw_text.strip():
        _fail(
            "grounded_llm_invalid_evidence",
            "Evidence raw_text must exactly match its raw prompt slice.",
            reason="evidence_text_mismatch",
            field_path=f"{path}.{field}",
            start=start,
            end=end,
        )

    language = str(payload.get("language") or _guess_language(raw_text)).strip()
    if not language:
        language = "und"
    confidence = _confidence(
        payload.get("confidence", 1.0),
        field_path=f"{path}.evidence.{field}.confidence",
    )
    _validate_known_evidence_meaning(
        raw_text,
        field=field,
        expected_value=expected_value,
        registry=registry,
        field_path=f"{path}.{field}",
    )
    concept_id = _grounded_concept_id(
        raw_text,
        field=field,
        expected_value=expected_value,
        registry=registry,
        fallback=concept_id,
    )
    return RawSpanEvidence(
        start=start,
        end=end,
        raw_text=raw_text,
        slot=field,
        concept_id=concept_id,
        language=language,
        confidence=confidence,
    )


def _validate_known_evidence_meaning(
    raw_text: str,
    *,
    field: str,
    expected_value: object,
    registry: ParameterRegistry,
    field_path: str,
) -> None:
    normalized = normalize_alias_text(raw_text)
    known_values: set[object] = set()
    if field == "axis":
        for definition in registry.axes.values():
            if any(
                alias.normalized_text == normalized
                for alias in definition.aliases
            ):
                known_values.add(definition.axis_id)
    elif field == "region":
        for definition in registry.regions.values():
            if any(
                alias.normalized_text == normalized
                for alias in definition.aliases
            ):
                known_values.add(definition.region_id)
    elif field in {"direction", "strength"}:
        for definition in registry.shared_concepts.values():
            if definition.slot != field:
                continue
            if any(
                alias.normalized_text == normalized
                for alias in definition.aliases
            ):
                known_values.add(definition.value)
    elif field == "numeric_relation":
        for definition in registry.shared_concepts.values():
            if definition.slot != "numeric_relation":
                continue
            if any(
                alias.normalized_text == normalized
                for alias in definition.aliases
            ):
                known_values.add(definition.value)
    elif field == "reset":
        for definition in registry.shared_concepts.values():
            if definition.slot != "operation":
                continue
            if any(
                alias.normalized_text == normalized
                for alias in definition.aliases
            ):
                known_values.add(definition.value)
        expected_value = "reset"
    elif field == "numeric":
        if not _NUMBER_PATTERN.fullmatch(normalized):
            _fail(
                "grounded_llm_invalid_evidence",
                "Numeric evidence must be the exact numeric token.",
                reason="numeric_evidence_not_literal",
                field_path=field_path,
                raw_text=raw_text,
            )
        parsed = float(normalized)
        if not math.isclose(
            parsed,
            float(expected_value),
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            _fail(
                "grounded_llm_invalid_evidence",
                "Numeric evidence does not equal the candidate numeric value.",
                reason="numeric_evidence_value_mismatch",
                field_path=field_path,
                evidence_value=parsed,
                candidate_value=expected_value,
            )
        return

    if known_values and expected_value not in known_values:
        _fail(
            "grounded_llm_evidence_conflict",
            "Evidence text has a conflicting registered meaning.",
            reason="registered_evidence_conflict",
            field_path=field_path,
            expected=expected_value,
            registered=sorted(known_values, key=str),
        )


def _grounded_concept_id(
    raw_text: str,
    *,
    field: str,
    expected_value: object,
    registry: ParameterRegistry,
    fallback: str,
) -> str:
    extraction = extract_semantic_slots(
        normalize_semantic_text(raw_text),
        registry=registry,
    )
    candidates: list[str] = []
    for slot in extraction.slots:
        if slot.is_ambiguous:
            continue
        if (
            slot.evidence.start != 0
            or slot.evidence.end != len(raw_text)
        ):
            continue
        matches = False
        if field == "axis":
            matches = (
                slot.namespace == "axis"
                and slot.concept_id == expected_value
            )
        elif field == "region":
            matches = (
                slot.namespace == "region"
                and slot.value == expected_value
            ) or (
                slot.namespace == "axis"
                and slot.concept_id == expected_value
                and expected_value in registry.regions
            )
        elif field == "direction":
            matches = (
                (
                    slot.slot == "direction"
                    and slot.value == expected_value
                )
                # Observation and surface-action concepts describe the
                # source phrase, not a canonical +/-1 value on their own.
                # Their final correction polarity depends on the bound axis
                # and is reconstructed independently by semantic_validator.
                # Preserve the exact registered concept identity here so a
                # correct grounded candidate cannot forge a generic
                # direction concept for the same source span.
                or slot.slot in {
                    "observation_modifier",
                    "surface_action",
                }
            ) or (
                slot.namespace == "axis"
                and slot.requested_direction == expected_value
            )
        elif field == "strength":
            matches = slot.slot == "strength" and slot.value == expected_value
        elif field == "numeric":
            matches = slot.namespace == "numeric"
        elif field == "numeric_relation":
            matches = (
                slot.slot == "numeric_relation"
                and slot.value == expected_value
            )
        elif field == "reset":
            matches = slot.slot == "operation" and slot.value == "reset"
        if matches and slot.concept_id is not None:
            candidates.append(str(slot.concept_id))
    unique = tuple(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else fallback


def _check_deterministic_input(
    raw_prompt: str,
    deterministic: SlotExtraction | None,
) -> GroundedLLMError | None:
    if deterministic is None:
        return None
    if not isinstance(deterministic, SlotExtraction):
        return _error(
            "grounded_llm_malformed_deterministic_context",
            "Deterministic context must be a SlotExtraction.",
            reason="invalid_deterministic_type",
            actual_type=type(deterministic).__name__,
        )
    if deterministic.normalized.raw_text != raw_prompt:
        return _error(
            "grounded_llm_deterministic_conflict",
            "Deterministic evidence belongs to a different raw prompt.",
            reason="deterministic_prompt_mismatch",
        )
    return None


def _deterministic_facts(
    extraction: SlotExtraction,
) -> _DeterministicFacts:
    axes: set[str] = set()
    axis_direction_sets: dict[str, set[int]] = {}
    shared_directions: set[int] = set()
    regions: set[str] = set()
    for slot in extraction.slots:
        item = slot.interpretation
        if item is None:
            continue
        if item.namespace == "axis":
            axis_id = str(item.concept_id)
            axes.add(axis_id)
            if item.requested_direction in {-1, 1}:
                axis_direction_sets.setdefault(axis_id, set()).add(
                    int(item.requested_direction)
                )
        elif item.slot == "direction" and item.value in {-1, 1}:
            shared_directions.add(int(item.value))
        elif item.slot == "region":
            regions.add(str(item.value))
    return _DeterministicFacts(
        axes=frozenset(axes),
        axis_directions={
            axis_id: frozenset(directions)
            for axis_id, directions in axis_direction_sets.items()
        },
        shared_directions=frozenset(shared_directions),
        regions=frozenset(regions),
    )


def _compare_deterministic_facts(
    operations: tuple[SemanticOperation, ...],
    deterministic: SlotExtraction | None,
) -> GroundedLLMError | None:
    if deterministic is None:
        return None
    facts = _deterministic_facts(deterministic)
    candidate_axes = frozenset(operation.axis_id for operation in operations)
    if facts.axes and candidate_axes != facts.axes:
        return _error(
            "grounded_llm_deterministic_conflict",
            "The LLM candidate omitted or changed a deterministic axis.",
            reason="deterministic_axis_mismatch",
            deterministic_axes=sorted(facts.axes),
            candidate_axes=sorted(candidate_axes),
        )

    candidate_regions = frozenset(
        operation.region for operation in operations
    )
    if facts.regions and candidate_regions != facts.regions:
        return _error(
            "grounded_llm_deterministic_conflict",
            "The LLM candidate omitted or changed a deterministic region.",
            reason="deterministic_region_mismatch",
            deterministic_regions=sorted(facts.regions),
            candidate_regions=sorted(candidate_regions),
        )

    for operation in operations:
        expected_directions = set(facts.shared_directions)
        expected_directions.update(
            facts.axis_directions.get(operation.axis_id, ())
        )
        if len(expected_directions) > 1:
            return _error(
                "grounded_llm_deterministic_conflict",
                "Deterministic evidence contains conflicting directions.",
                reason="deterministic_direction_conflict",
                axis=operation.axis_id,
                deterministic_directions=sorted(expected_directions),
            )
        expected = next(iter(expected_directions), None)
        if expected is not None and operation.direction != expected:
            return _error(
                "grounded_llm_deterministic_conflict",
                "The LLM candidate changed a deterministic direction.",
                reason="deterministic_direction_mismatch",
                axis=operation.axis_id,
                deterministic_direction=expected,
                candidate_direction=operation.direction,
            )
    return None


def _direction(payload: Mapping[object, object], path: str) -> int:
    if "direction" not in payload:
        _fail(
            "grounded_llm_malformed_response",
            "Relative operations require direction -1 or 1.",
            reason="missing_direction",
            field_path=f"{path}.direction",
        )
    value = payload["direction"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in {-1, 1}:
        _fail(
            "grounded_llm_invalid_operation",
            "Relative direction must be the integer -1 or 1.",
            reason="invalid_direction",
            field_path=f"{path}.direction",
            value=value,
        )
    return value


def _numeric(payload: Mapping[object, object], path: str) -> float:
    if "numeric" not in payload:
        _fail(
            "grounded_llm_malformed_response",
            "Numeric operations require a numeric value.",
            reason="missing_numeric",
            field_path=f"{path}.numeric",
        )
    value = payload["numeric"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            "grounded_llm_invalid_operation",
            "The numeric field must contain a finite number.",
            reason="invalid_numeric",
            field_path=f"{path}.numeric",
            value=value,
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        _fail(
            "grounded_llm_invalid_operation",
            "The numeric field must contain a finite number.",
            reason="invalid_numeric",
            field_path=f"{path}.numeric",
            value=value,
        )
    return numeric


def _required_identifier(
    payload: Mapping[object, object],
    field: str,
    path: str,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail(
            "grounded_llm_malformed_response",
            f"Operation {field} must be a non-empty string.",
            reason=f"invalid_{field}",
            field_path=f"{path}.{field}",
        )
    return value.strip().lower()


def _forbid_fields(
    payload: Mapping[object, object],
    evidence: Mapping[object, object],
    operation_index: int,
    *fields: str,
) -> None:
    for field in fields:
        if field in payload:
            _fail(
                "grounded_llm_invalid_operation",
                "Operation fields do not match the selected operation kind.",
                reason="forbidden_operation_field",
                operation_index=operation_index,
                field=field,
            )
        if field in evidence:
            _orphan_evidence(operation_index, field)


def _orphan_evidence(operation_index: int, field: str) -> None:
    _fail(
        "grounded_llm_malformed_response",
        "Evidence cannot appear without its semantic field.",
        reason="orphan_evidence",
        operation_index=operation_index,
        evidence_field=field,
    )


def _require_only_fields(
    payload: Mapping[object, object],
    allowed: frozenset[str],
    path: str,
) -> None:
    invalid = sorted(str(key) for key in payload if key not in allowed)
    if invalid:
        _fail(
            "grounded_llm_malformed_response",
            "The grounded LLM candidate contains unsupported fields.",
            reason="unknown_candidate_fields",
            field_path=path,
            fields=invalid,
        )


def _find_unsafe_output_field(
    value: object,
    *,
    path: str = "candidate",
) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}"
            if key_text.strip().lower() in _UNSAFE_OUTPUT_FIELDS:
                return nested_path
            found = _find_unsafe_output_field(nested, path=nested_path)
            if found is not None:
                return found
    elif _is_sequence(value):
        assert isinstance(value, Sequence)
        for index, nested in enumerate(value):
            found = _find_unsafe_output_field(
                nested,
                path=f"{path}[{index}]",
            )
            if found is not None:
                return found
    return None


def _confidence(value: object, *, field_path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            "grounded_llm_malformed_response",
            "Confidence must be a finite number between 0 and 1.",
            reason="invalid_confidence",
            field_path=field_path,
        )
    confidence = float(value)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        _fail(
            "grounded_llm_malformed_response",
            "Confidence must be a finite number between 0 and 1.",
            reason="invalid_confidence",
            field_path=field_path,
        )
    return confidence


def _evidence_languages(
    evidence: Sequence[RawSpanEvidence],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.language for item in evidence))


def _guess_language(raw_text: str) -> str:
    if any("\u3400" <= char <= "\u9fff" for char in raw_text):
        return "zh"
    if any(char.isascii() and char.isalpha() for char in raw_text):
        return "en"
    return "und"


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _error(
    code: str,
    message: str,
    *,
    status_code: int = 422,
    retryable: bool = False,
    **issue: Any,
) -> GroundedLLMError:
    return GroundedLLMError(
        code=code,
        message=message,
        issues=(dict(issue),) if issue else (),
        status_code=status_code,
        retryable=retryable,
    )


def _fail(code: str, message: str, **issue: Any) -> None:
    raise _CandidateFailure(_error(code, message, **issue))


__all__ = [
    "CandidateProvider",
    "GROUNDED_LLM_PARSER_VERSION",
    "GroundedLLMError",
    "GroundedLLMResult",
    "adapt_grounded_llm_candidate",
    "deterministic_semantics_are_complete",
    "invoke_grounded_llm_candidate",
]
