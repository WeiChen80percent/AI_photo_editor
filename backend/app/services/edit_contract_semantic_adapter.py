"""Registry-driven semantic boundary for verifiable edit contracts.

Pure edit prompts are not re-parsed or rewritten here: when no contract
evidence exists the adapter returns ``not_contract`` and the caller can pass
the original prompt to the established production pipeline.  Once a possible
constraint is present, every operation and hard condition must resolve or the
whole request fails closed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.edit_contract_registry import (
    AliasMatch,
    MetricCapabilityRegistry,
    MetricDefinition,
    MetricRegistryValidationError,
    get_default_metric_registry,
    normalize_contract_alias,
)
from app.services.edit_contract_schema import (
    CONTRACT_SCHEMA_VERSION,
    MAX_CONTRACT_CONSTRAINTS,
    ContractConstraint,
    ContractDisposition,
    EditContractError,
    EditContractIR,
)
from app.services.edit_schema import default_mask_type_for_region
from app.services.llm_semantic_adapter import (
    GroundedLLMError,
    adapt_grounded_llm_candidate,
)
from app.services.semantic_ir import RawSpanEvidence, SemanticIR
from app.services.semantic_normalizer import normalize_semantic_text
from app.services.semantic_parser import parse_semantic_prompt
from app.services.semantic_parser import SemanticParseAttempt
from app.services.semantic_registry import (
    DEFAULT_PARAMETER_REGISTRY,
    ParameterRegistry,
)


CONTRACT_SEMANTIC_PARSER_VERSION = "edit_contract_semantic_v1"

_TOP_LEVEL_FIELDS = frozenset({"operations", "constraints", "confidence"})
_CONSTRAINT_FIELDS = frozenset(
    {
        "metric",
        "subject_region",
        "operator",
        "threshold",
        "unit",
        "profile",
        "evidence",
    }
)
_CONSTRAINT_EVIDENCE_FIELDS = frozenset(
    {
        "metric",
        "subject_region",
        "operator",
        "threshold",
        "unit",
        "profile",
        "clause",
    }
)
_SPAN_FIELDS = frozenset(
    {"start", "end", "raw_text", "language", "confidence"}
)
_UNSAFE_FIELDS = frozenset(
    {
        "engine",
        "engine_parameters",
        "opencv_parameters",
        "parameter_values",
        "parameters",
        "processor",
        "render_parameters",
        "measurement",
        "measurements",
        "metric_result",
        "metric_results",
        "passed",
        "applied_scale",
        "actual_parameters",
    }
)
_NUMBER_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_CLAUSE_BOUNDARY_PATTERN = re.compile(
    r"[,，;；。.!?]|\bbut\b|\bhowever\b|\band(?=\s+(?:keep|avoid|do\s+not|don't))|"
    r"但是|但|不過|並且|以及|同時(?=\s*(?:保持|避免|不要))",
    re.IGNORECASE,
)
_IGNORABLE_RESIDUE = re.compile(
    r"^(?:\s|[,，;；。.!?%％'’]|the|a|an|s|to|of|in|on|for|and|or|"
    r"please|讓|把|的|要|再|且|並|和|與|於|在)*$",
    re.IGNORECASE,
)

GroundedContractProvider = Callable[[str, Mapping[str, Any]], object]


@dataclass(frozen=True, slots=True)
class ContractSemanticAttempt:
    raw_prompt: str
    disposition: ContractDisposition
    contract_ir: EditContractIR | None = None
    operation_semantic_attempt: SemanticParseAttempt | None = None
    error: EditContractError | None = None
    parser_version: str = CONTRACT_SEMANTIC_PARSER_VERSION

    def __post_init__(self) -> None:
        if not str(self.raw_prompt).strip():
            raise ValueError("raw_prompt must not be empty")
        if self.disposition == "accepted":
            if (
                self.contract_ir is None
                or self.operation_semantic_attempt is None
                or self.operation_semantic_attempt.accepted_ir is None
                or self.error is not None
            ):
                raise ValueError(
                    "accepted attempts require a released operation semantic attempt"
                )
        elif self.disposition == "not_contract":
            if (
                self.contract_ir is not None
                or self.operation_semantic_attempt is not None
                or self.error is not None
            ):
                raise ValueError("not_contract attempts carry no result")
        elif (
            self.error is None
            or self.contract_ir is not None
            or self.operation_semantic_attempt is not None
        ):
            raise ValueError("failed attempts require only a structured error")

    @property
    def accepted(self) -> bool:
        return self.contract_ir is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_prompt": self.raw_prompt,
            "disposition": self.disposition,
            "contract_ir": (
                None if self.contract_ir is None else self.contract_ir.as_dict()
            ),
            "operation_semantic_attempt": (
                None
                if self.operation_semantic_attempt is None
                else {
                    "parser_version": self.operation_semantic_attempt.parser_version,
                    "disposition": self.operation_semantic_attempt.disposition,
                    "semantic_ir": self.operation_semantic_attempt.accepted_ir.as_dict(),
                }
            ),
            "error": None if self.error is None else self.error.as_dict(),
            "parser_version": self.parser_version,
        }


def parse_edit_contract_prompt(
    raw_prompt: str,
    *,
    metric_registry: MetricCapabilityRegistry | None = None,
    parameter_registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
    grounded_provider: GroundedContractProvider | None = None,
) -> ContractSemanticAttempt:
    """Parse a possible operation + hard-constraint prompt.

    ``not_contract`` is returned only when the new registry sees no credible
    constraint relation.  A recognized or structurally possible constraint
    that cannot be completed is an error, never legacy fallback.
    """

    prompt = str(raw_prompt)
    if not prompt.strip():
        error = _error(
            "contract_invalid_prompt",
            "The edit prompt must not be empty.",
            "rejected",
            status_code=400,
            reason="empty_prompt",
        )
        return _attempt_from_error(prompt or " ", error)
    try:
        registry = metric_registry or get_default_metric_registry()
        matches = registry.match_aliases(prompt)
    except (TypeError, ValueError, MetricRegistryValidationError) as exc:
        error = _error(
            "contract_registry_unavailable",
            "The edit contract registry is unavailable.",
            "rejected",
            status_code=500,
            reason="registry_error",
            detail=str(exc),
        )
        return _attempt_from_error(prompt, error)

    metric_matches = tuple(item for item in matches if item.kind == "metric")
    relation_matches = tuple(
        item
        for item in matches
        if item.kind in {"operator", "profile_signal", "protection_signal"}
    )
    has_structural_signal = _has_released_operation_before_relation(
        prompt,
        relation_matches=relation_matches,
        parameter_registry=parameter_registry,
        engine=engine,
    )
    has_known_contract = any(
        _same_clause(prompt, metric, relation)
        for metric in metric_matches
        for relation in relation_matches
    )
    if not has_known_contract:
        if has_structural_signal:
            if grounded_provider is not None:
                grounded = invoke_grounded_contract_candidate(
                    prompt,
                    grounded_provider,
                    metric_registry=registry,
                    parameter_registry=parameter_registry,
                    engine=engine,
                )
                if isinstance(grounded, EditContractIR):
                    operation_attempt = _released_operation_attempt_for_contract(
                        prompt,
                        grounded,
                        parameter_registry=parameter_registry,
                        engine=engine,
                    )
                    if isinstance(operation_attempt, EditContractError):
                        return _attempt_from_error(prompt, operation_attempt)
                    return ContractSemanticAttempt(
                        raw_prompt=prompt,
                        disposition="accepted",
                        contract_ir=grounded,
                        operation_semantic_attempt=operation_attempt,
                    )
                return _attempt_from_error(prompt, grounded)
            error = _error(
                "contract_metric_unsupported",
                "A protection condition was found, but its metric is unsupported.",
                "unsupported",
                reason="constraint_relation_without_supported_metric",
            )
            return _attempt_from_error(prompt, error)
        return ContractSemanticAttempt(
            raw_prompt=prompt,
            disposition="not_contract",
        )
    if _is_standalone_qualitative_metric_intent(
        prompt,
        metric_matches=metric_matches,
        relation_matches=relation_matches,
    ):
        return ContractSemanticAttempt(
            raw_prompt=prompt,
            disposition="not_contract",
        )

    deterministic = _parse_deterministic_contract(
        prompt,
        matches=matches,
        metric_registry=registry,
        parameter_registry=parameter_registry,
        engine=engine,
    )
    if isinstance(deterministic, tuple):
        deterministic_ir, operation_attempt = deterministic
        return ContractSemanticAttempt(
            raw_prompt=prompt,
            disposition="accepted",
            contract_ir=deterministic_ir,
            operation_semantic_attempt=operation_attempt,
        )
    if grounded_provider is None or deterministic.disposition == "unsupported":
        return _attempt_from_error(prompt, deterministic)

    grounded = invoke_grounded_contract_candidate(
        prompt,
        grounded_provider,
        metric_registry=registry,
        parameter_registry=parameter_registry,
        engine=engine,
    )
    if isinstance(grounded, EditContractIR):
        operation_attempt = _released_operation_attempt_for_contract(
            prompt,
            grounded,
            parameter_registry=parameter_registry,
            engine=engine,
        )
        if isinstance(operation_attempt, EditContractError):
            return _attempt_from_error(prompt, operation_attempt)
        return ContractSemanticAttempt(
            raw_prompt=prompt,
            disposition="accepted",
            contract_ir=grounded,
            operation_semantic_attempt=operation_attempt,
        )
    return _attempt_from_error(prompt, grounded)


def adapt_grounded_contract_candidate(
    raw_prompt: str,
    candidate: object,
    *,
    metric_registry: MetricCapabilityRegistry | None = None,
    parameter_registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
) -> EditContractIR | EditContractError:
    """Validate a schema-grounded LLM candidate without trusting render data."""

    prompt = str(raw_prompt)
    if not prompt.strip():
        return _error(
            "contract_invalid_prompt",
            "The edit prompt must not be empty.",
            "rejected",
            status_code=400,
            reason="empty_prompt",
        )
    try:
        registry = metric_registry or get_default_metric_registry()
    except (ImportError, ValueError, MetricRegistryValidationError) as exc:
        return _error(
            "contract_registry_unavailable",
            "The edit contract registry is unavailable.",
            "rejected",
            status_code=500,
            reason="registry_error",
            detail=str(exc),
        )
    if candidate is None or candidate == {}:
        return _error(
            "contract_grounded_empty_response",
            "The grounded provider returned no contract candidate.",
            "clarification_required",
            reason="empty_candidate",
        )
    if not isinstance(candidate, Mapping):
        return _error(
            "contract_grounded_malformed_response",
            "A grounded contract candidate must be a mapping.",
            "rejected",
            reason="candidate_not_mapping",
        )
    unsafe_path = _find_unsafe_field(candidate)
    if unsafe_path is not None:
        return _error(
            "contract_grounded_unsafe_output",
            "Grounded contract candidates cannot contain render or result fields.",
            "rejected",
            reason="unsafe_output_field",
            field_path=unsafe_path,
        )
    unknown = set(candidate).difference(_TOP_LEVEL_FIELDS)
    if unknown:
        return _error(
            "contract_grounded_malformed_response",
            "The grounded contract candidate contains unknown fields.",
            "rejected",
            reason="unknown_candidate_fields",
            fields=sorted(str(item) for item in unknown),
        )

    operations = candidate.get("operations")
    constraints = candidate.get("constraints")
    if not _is_sequence(operations) or not operations:
        return _error(
            "contract_operation_required",
            "An edit contract requires at least one edit operation.",
            "clarification_required",
            reason="missing_operations",
        )
    if not _is_sequence(constraints) or not constraints:
        return _error(
            "contract_constraint_required",
            "An edit contract requires at least one hard constraint.",
            "clarification_required",
            reason="missing_constraints",
        )
    if len(constraints) > MAX_CONTRACT_CONSTRAINTS:
        return _error(
            "contract_constraint_limit",
            f"At most {MAX_CONTRACT_CONSTRAINTS} hard constraints are supported.",
            "clarification_required",
            reason="constraint_limit",
            constraint_count=len(constraints),
        )
    confidence = _confidence(candidate.get("confidence", 1.0))
    if isinstance(confidence, EditContractError):
        return confidence

    parsed_constraints: list[ContractConstraint] = []
    for index, payload in enumerate(constraints):
        parsed = _parse_grounded_constraint(
            prompt,
            payload,
            index=index,
            metric_registry=registry,
            parameter_registry=parameter_registry,
        )
        if isinstance(parsed, EditContractError):
            return parsed
        parsed_constraints.append(parsed)
    duplicate_error = _duplicate_constraint_error(parsed_constraints)
    if duplicate_error is not None:
        return duplicate_error
    source_spans = [
        (constraint.source_start, constraint.source_end)
        for constraint in parsed_constraints
        if constraint.source_start is not None and constraint.source_end is not None
    ]
    operation_projection = _operation_projection(prompt, source_spans)
    projected_result = adapt_grounded_llm_candidate(
        operation_projection,
        {"operations": list(operations), "confidence": confidence},
        registry=parameter_registry,
        engine=engine,
    )
    if isinstance(projected_result, GroundedLLMError):
        return _translate_operation_error(projected_result)
    released_attempt = parse_semantic_prompt(
        operation_projection,
        registry=parameter_registry,
        engine=engine,
    )
    if released_attempt.accepted_ir is None:
        return _error(
            "contract_operation_not_released",
            "The grounded contract operation is not accepted by the released semantic parser.",
            "clarification_required",
            reason="released_operation_parse_failed",
            semantic_code=(
                None
                if released_attempt.error is None
                else released_attempt.error.code
            ),
        )
    grounded_identity = tuple(
        _operation_identity(item) for item in projected_result.operations
    )
    released_identity = tuple(
        _operation_identity(item) for item in released_attempt.accepted_ir.operations
    )
    if grounded_identity != released_identity:
        return _error(
            "contract_operation_grounding_conflict",
            "The grounded operation conflicts with the released deterministic parser.",
            "rejected",
            reason="operation_semantic_mismatch",
            grounded=list(grounded_identity),
            released=list(released_identity),
        )
    operation_result = _restore_semantic_ir(
        prompt,
        released_attempt.accepted_ir,
    )
    with_system = _add_automatic_constraints(
        parsed_constraints,
        semantic_ir=operation_result,
        metric_registry=registry,
    )
    if isinstance(with_system, EditContractError):
        return with_system
    try:
        return EditContractIR(
            raw_prompt=prompt,
            semantic_ir=operation_result,
            constraints=tuple(with_system),
            language_sources=_contract_languages(operation_result, with_system),
            decision_source="grounded_llm",
            schema_version=CONTRACT_SCHEMA_VERSION,
            semantic_registry_version=parameter_registry.registry_version,
            metric_registry_version=registry.registry_version,
            parser_version=CONTRACT_SEMANTIC_PARSER_VERSION,
            confidence=min(confidence, operation_result.confidence),
        )
    except (TypeError, ValueError) as exc:
        return _error(
            "contract_grounded_invalid_ir",
            "The grounded candidate could not form a valid edit contract.",
            "rejected",
            reason="contract_ir_validation",
            detail=str(exc),
        )


def invoke_grounded_contract_candidate(
    raw_prompt: str,
    provider: GroundedContractProvider,
    *,
    metric_registry: MetricCapabilityRegistry | None = None,
    parameter_registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
) -> EditContractIR | EditContractError:
    if not callable(provider):
        return _error(
            "contract_grounded_provider_invalid",
            "The grounded contract provider must be callable.",
            "rejected",
            status_code=500,
            reason="provider_not_callable",
        )
    try:
        registry = metric_registry or get_default_metric_registry()
        candidate = provider(
            str(raw_prompt),
            grounded_contract_candidate_schema(
                metric_registry=registry,
                parameter_registry=parameter_registry,
            ),
        )
    except TimeoutError:
        return _error(
            "contract_grounded_timeout",
            "The grounded contract provider timed out.",
            "clarification_required",
            status_code=503,
            retryable=True,
            reason="provider_timeout",
        )
    except Exception as exc:  # transport/provider boundary
        return _error(
            "contract_grounded_unavailable",
            "The grounded contract provider is unavailable.",
            "clarification_required",
            status_code=503,
            retryable=True,
            reason="provider_exception",
            exception_type=type(exc).__name__,
        )
    return adapt_grounded_contract_candidate(
        raw_prompt,
        candidate,
        metric_registry=registry,
        parameter_registry=parameter_registry,
        engine=engine,
    )


def grounded_contract_candidate_schema(
    *,
    metric_registry: MetricCapabilityRegistry,
    parameter_registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
) -> dict[str, Any]:
    """Return the allow-list supplied to a grounded semantic provider."""

    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "allowed_axes": list(parameter_registry.axis_ids),
        "allowed_regions": sorted(parameter_registry.regions),
        "metric_schema": metric_registry.as_schema_payload(),
        "max_operations": 3,
        "max_constraints": MAX_CONTRACT_CONSTRAINTS,
        "required_constraint_evidence": [
            "metric",
            "clause",
            "and exact evidence for every supplied field",
        ],
        "forbidden_output_fields": sorted(_UNSAFE_FIELDS),
    }


def _parse_deterministic_contract(
    prompt: str,
    *,
    matches: tuple[AliasMatch, ...],
    metric_registry: MetricCapabilityRegistry,
    parameter_registry: ParameterRegistry,
    engine: str,
) -> tuple[EditContractIR, SemanticParseAttempt] | EditContractError:
    metric_matches = tuple(item for item in matches if item.kind == "metric")
    relation_matches = tuple(
        item
        for item in matches
        if item.kind in {"operator", "profile_signal", "protection_signal"}
    )
    constraints: list[ContractConstraint] = []
    source_spans: list[tuple[int, int]] = []
    for metric_match in metric_matches:
        if not any(
            _same_clause(prompt, metric_match, relation)
            for relation in relation_matches
        ):
            continue
        parsed = _assemble_deterministic_constraint(
            prompt,
            metric_match,
            all_matches=matches,
            metric_registry=metric_registry,
            parameter_registry=parameter_registry,
            constraint_index=len(constraints),
        )
        if isinstance(parsed, EditContractError):
            return parsed
        constraint, source_span = parsed
        constraints.append(constraint)
        source_spans.append(source_span)
    if not constraints:
        return _error(
            "contract_constraint_unresolved",
            "The protection condition could not be resolved.",
            "clarification_required",
            reason="no_resolved_constraints",
        )
    duplicate_error = _duplicate_constraint_error(constraints)
    if duplicate_error is not None:
        return duplicate_error
    if len(constraints) > MAX_CONTRACT_CONSTRAINTS:
        return _error(
            "contract_constraint_limit",
            f"At most {MAX_CONTRACT_CONSTRAINTS} hard constraints are supported.",
            "clarification_required",
            reason="constraint_limit",
            constraint_count=len(constraints),
        )

    operation_projection = _operation_projection(prompt, source_spans)
    operation_attempt = parse_semantic_prompt(
        operation_projection,
        registry=parameter_registry,
        engine=engine,
    )
    if operation_attempt.accepted_ir is None:
        error = operation_attempt.error
        return _error(
            "contract_operation_unresolved",
            "The edit operation could not be resolved together with its constraints.",
            "clarification_required",
            reason="operation_parse_failed",
            semantic_code=(None if error is None else error.code),
            semantic_issues=([] if error is None else list(error.issues)),
        )
    semantic_ir = _restore_semantic_ir(prompt, operation_attempt.accepted_ir)
    with_system = _add_automatic_constraints(
        constraints,
        semantic_ir=semantic_ir,
        metric_registry=metric_registry,
    )
    if isinstance(with_system, EditContractError):
        return with_system
    try:
        contract_ir = EditContractIR(
            raw_prompt=prompt,
            semantic_ir=semantic_ir,
            constraints=tuple(with_system),
            language_sources=_contract_languages(semantic_ir, with_system),
            decision_source="deterministic",
            semantic_registry_version=parameter_registry.registry_version,
            metric_registry_version=metric_registry.registry_version,
            parser_version=CONTRACT_SEMANTIC_PARSER_VERSION,
            confidence=min(
                [semantic_ir.confidence]
                + [constraint.confidence for constraint in with_system]
            ),
        )
        return contract_ir, operation_attempt
    except (TypeError, ValueError) as exc:
        return _error(
            "contract_invalid_ir",
            "The deterministic interpretation failed contract validation.",
            "rejected",
            reason="contract_ir_validation",
            detail=str(exc),
        )


def _assemble_deterministic_constraint(
    prompt: str,
    metric_match: AliasMatch,
    *,
    all_matches: tuple[AliasMatch, ...],
    metric_registry: MetricCapabilityRegistry,
    parameter_registry: ParameterRegistry,
    constraint_index: int,
) -> tuple[ContractConstraint, tuple[int, int]] | EditContractError:
    definition = metric_registry.get(metric_match.concept_id)
    clause_start, clause_end = _clause_bounds(prompt, metric_match.start)
    clause_matches = tuple(
        item
        for item in all_matches
        if clause_start <= item.start and item.end <= clause_end
    )
    operators = tuple(item for item in clause_matches if item.kind == "operator")
    profiles = tuple(
        item
        for item in metric_registry.match_profile_aliases(
            definition.metric_id,
            prompt,
        )
        if clause_start <= item.start and item.end <= clause_end
    )
    numeric_matches = tuple(
        _numeric_matches(prompt, clause_start, clause_end)
    )
    unit_matches = tuple(item for item in clause_matches if item.kind == "unit")

    evidence: list[RawSpanEvidence] = [
        _evidence(metric_match, "constraint_metric", definition.metric_id)
    ]
    profile_id: str | None = None
    if numeric_matches:
        comparator_values = {
            item.concept_id for item in operators if item.concept_id == "<="
        }
        if comparator_values != {"<="} or len(numeric_matches) != 1:
            return _error(
                "contract_threshold_ambiguous",
                "An explicit threshold requires one unambiguous <= relation.",
                "clarification_required",
                reason="ambiguous_numeric_relation",
                metric_id=definition.metric_id,
            )
        if len(unit_matches) != 1:
            return _error(
                "contract_unit_required",
                "An explicit ratio threshold requires an exact unit such as percent.",
                "clarification_required",
                reason="missing_or_ambiguous_unit",
                metric_id=definition.metric_id,
            )
        operator_match = next(
            item for item in operators if item.concept_id == "<="
        )
        numeric_start, numeric_end, numeric_value = numeric_matches[0]
        unit_match = unit_matches[0]
        resolved_unit = metric_registry.resolve_input_unit_alias(unit_match.raw_text)
        if resolved_unit is None or resolved_unit[0] != definition.unit:
            return _error(
                "contract_unit_unsupported",
                "The explicit threshold unit is unsupported for this metric.",
                "unsupported",
                reason="unit_metric_mismatch",
                metric_id=definition.metric_id,
                unit_text=unit_match.raw_text,
            )
        try:
            threshold = metric_registry.normalize_explicit_threshold(
                definition.metric_id,
                numeric_value,
                resolved_unit[1],
            )
        except MetricRegistryValidationError as exc:
            return _error(
                "contract_threshold_out_of_range",
                "The explicit threshold is outside the supported range.",
                "clarification_required",
                reason="threshold_range",
                detail=str(exc),
            )
        operator = "<="
        threshold_source = "user_explicit"
        reference_mode = "absolute_outcome"
        evidence.extend(
            (
                _evidence(operator_match, "constraint_operator", operator),
                RawSpanEvidence(
                    start=numeric_start,
                    end=numeric_end,
                    raw_text=prompt[numeric_start:numeric_end],
                    slot="constraint_threshold",
                    concept_id="numeric_literal",
                    language="und",
                ),
                _evidence(
                    unit_match,
                    "constraint_unit",
                    resolved_unit[1],
                ),
            )
        )
        # Qualitative framing such as "keep ... below 1%" is not the
        # threshold source, but it is still authoritative contract text and
        # must be consumed rather than leaking into the operation projection.
        profile_ids = {item.concept_id for item in profiles}
        if len(profile_ids) == 1:
            selected_profile_id = next(iter(profile_ids))
            evidence.extend(
                _evidence(item, "constraint_relation", selected_profile_id)
                for item in profiles
                if item.concept_id == selected_profile_id
            )
    else:
        profile_ids = {item.concept_id for item in profiles}
        no_worse = any(
            item.concept_id == "no_worse_than_baseline" for item in operators
        )
        if not profile_ids and no_worse and len(definition.profiles) == 1:
            profile_ids = {definition.profiles[0].profile_id}
        if len(profile_ids) != 1:
            return _error(
                "contract_profile_ambiguous",
                "The qualitative protection profile is missing or ambiguous.",
                "clarification_required",
                reason="profile_resolution",
                metric_id=definition.metric_id,
            )
        profile_id = next(iter(profile_ids))
        profile = definition.get_profile(profile_id)
        threshold = profile.threshold
        operator = profile.operator
        threshold_source = "policy_default"
        reference_mode = profile.reference_mode
        selected_profile_evidence = tuple(
            item for item in profiles if item.concept_id == profile_id
        )
        if selected_profile_evidence:
            evidence.extend(
                _evidence(item, "constraint_profile", profile_id)
                for item in selected_profile_evidence
            )
        else:
            chosen_operator = next(
                item
                for item in operators
                if item.concept_id == "no_worse_than_baseline"
            )
            evidence.append(
                _evidence(
                    chosen_operator,
                    "constraint_profile",
                    profile_id,
                )
            )

    subject_region, region_evidence = _resolve_subject_region(
        prompt,
        clause_start,
        clause_end,
        definition,
        parameter_registry,
    )
    if isinstance(subject_region, EditContractError):
        return subject_region
    if region_evidence is not None:
        evidence.append(region_evidence)
    mask_type = _mask_type_for_definition(definition, subject_region)
    source_start = min(item.start for item in evidence)
    source_end = max(item.end for item in evidence)
    source_text = prompt[source_start:source_end]
    residue = _constraint_residue(source_text, evidence, source_start)
    if residue:
        return _error(
            "contract_constraint_residue",
            "Part of the protection condition could not be grounded.",
            "clarification_required",
            reason="authoritative_residue",
            raw_text=residue,
            metric_id=definition.metric_id,
        )
    language = _combined_language(item.language for item in evidence)
    source_evidence = RawSpanEvidence(
        start=source_start,
        end=source_end,
        raw_text=source_text,
        slot="constraint_clause",
        concept_id=definition.metric_id,
        language=language,
        confidence=min(item.confidence for item in evidence),
    )
    constraint = ContractConstraint(
        constraint_id=f"constraint_{constraint_index + 1}",
        metric_id=definition.metric_id,
        metric_version=definition.metric_version,
        subject_region=subject_region,
        mask_type=mask_type,
        capability_requirements=tuple(sorted(definition.capability_requirements)),
        operator=operator,
        threshold=threshold,
        unit=definition.unit,
        threshold_source=threshold_source,
        reference_mode=reference_mode,
        profile_id=profile_id,
        source_evidence=source_evidence,
        evidence=tuple(evidence),
    )
    try:
        metric_registry.validate_constraint(constraint)
    except (KeyError, MetricRegistryValidationError) as exc:
        return _error(
            "contract_capability_invalid",
            "The resolved constraint is unsupported by the metric registry.",
            "unsupported",
            reason="constraint_registry_validation",
            detail=str(exc),
        )
    return constraint, (source_start, source_end)


def _parse_grounded_constraint(
    prompt: str,
    payload: object,
    *,
    index: int,
    metric_registry: MetricCapabilityRegistry,
    parameter_registry: ParameterRegistry,
) -> ContractConstraint | EditContractError:
    path = f"candidate.constraints[{index}]"
    if not isinstance(payload, Mapping):
        return _error(
            "contract_grounded_malformed_response",
            "Each grounded constraint must be a mapping.",
            "rejected",
            reason="constraint_not_mapping",
            constraint_index=index,
        )
    unknown = set(payload).difference(_CONSTRAINT_FIELDS)
    if unknown:
        return _error(
            "contract_grounded_malformed_response",
            "A grounded constraint contains unknown fields.",
            "rejected",
            reason="unknown_constraint_fields",
            field_path=path,
            fields=sorted(str(item) for item in unknown),
        )
    metric_id = str(payload.get("metric", "")).strip()
    try:
        definition = metric_registry.get(metric_id)
    except KeyError:
        return _error(
            "contract_metric_unsupported",
            "The grounded provider selected an unsupported metric.",
            "unsupported",
            reason="unknown_metric",
            metric_id=metric_id,
        )
    subject_region = str(payload.get("subject_region", "")).strip()
    if not subject_region:
        if definition.supported_subject_regions == frozenset({"all"}):
            subject_region = "all"
        else:
            return _error(
                "contract_scope_required",
                "This constraint requires an explicit subject region.",
                "clarification_required",
                reason="missing_subject_region",
                metric_id=metric_id,
            )
    if subject_region not in definition.supported_subject_regions:
        return _error(
            "contract_scope_unsupported",
            "The selected subject region is unsupported for this metric.",
            "unsupported",
            reason="unsupported_subject_region",
            metric_id=metric_id,
            subject_region=subject_region,
        )
    raw_evidence = payload.get("evidence")
    if not isinstance(raw_evidence, Mapping):
        return _error(
            "contract_grounded_missing_evidence",
            "Every grounded constraint requires field evidence.",
            "rejected",
            reason="missing_evidence_mapping",
            field_path=f"{path}.evidence",
        )
    unknown_evidence = set(raw_evidence).difference(_CONSTRAINT_EVIDENCE_FIELDS)
    if unknown_evidence:
        return _error(
            "contract_grounded_malformed_response",
            "Constraint evidence contains unknown fields.",
            "rejected",
            reason="unknown_evidence_fields",
            fields=sorted(str(item) for item in unknown_evidence),
        )

    metric_evidence = _candidate_span(
        prompt,
        raw_evidence,
        "metric",
        concept_id=metric_id,
        path=path,
    )
    if isinstance(metric_evidence, EditContractError):
        return metric_evidence
    known_metrics = {
        item.concept_id
        for item in metric_registry.match_aliases(metric_evidence.raw_text)
        if item.kind == "metric"
    }
    if known_metrics and metric_id not in known_metrics:
        return _evidence_conflict(path, "metric", metric_id, known_metrics)

    evidence = [metric_evidence]
    if subject_region != "all":
        region_evidence = _candidate_span(
            prompt,
            raw_evidence,
            "subject_region",
            concept_id=subject_region,
            path=path,
        )
        if isinstance(region_evidence, EditContractError):
            return region_evidence
        known_regions = _known_region_values(
            region_evidence.raw_text,
            parameter_registry,
        )
        if known_regions and subject_region not in known_regions:
            return _evidence_conflict(
                path,
                "subject_region",
                subject_region,
                known_regions,
            )
        evidence.append(region_evidence)

    profile_id = payload.get("profile")
    explicit_fields = {
        "operator",
        "threshold",
        "unit",
    }.intersection(payload)
    if profile_id is not None and explicit_fields:
        return _error(
            "contract_grounded_malformed_response",
            "A constraint cannot mix a policy profile with explicit threshold fields.",
            "rejected",
            reason="profile_explicit_conflict",
            field_path=path,
        )
    if profile_id is not None:
        try:
            profile = definition.get_profile(str(profile_id))
        except KeyError:
            return _error(
                "contract_profile_unsupported",
                "The grounded provider selected an unsupported policy profile.",
                "unsupported",
                reason="unknown_profile",
                metric_id=metric_id,
                profile_id=str(profile_id),
            )
        profile_evidence = _candidate_span(
            prompt,
            raw_evidence,
            "profile",
            concept_id=profile.profile_id,
            path=path,
        )
        if isinstance(profile_evidence, EditContractError):
            return profile_evidence
        known_profiles = {
            item.concept_id
            for item in metric_registry.match_profile_aliases(
                metric_id,
                profile_evidence.raw_text,
            )
        }
        if known_profiles and profile.profile_id not in known_profiles:
            return _evidence_conflict(
                path,
                "profile",
                profile.profile_id,
                known_profiles,
            )
        evidence.append(profile_evidence)
        operator = profile.operator
        threshold = profile.threshold
        threshold_source = "policy_default"
        reference_mode = profile.reference_mode
        normalized_profile_id: str | None = profile.profile_id
    else:
        if explicit_fields != {"operator", "threshold", "unit"}:
            return _error(
                "contract_grounded_malformed_response",
                "Explicit constraints require operator, threshold, and unit.",
                "rejected",
                reason="incomplete_explicit_threshold",
                field_path=path,
            )
        operator = str(payload["operator"]).strip()
        if operator != "<=":
            return _error(
                "contract_operator_unsupported",
                "Explicit thresholds support only the <= operator in v1.",
                "unsupported",
                reason="explicit_operator",
                operator=operator,
            )
        operator_evidence = _candidate_span(
            prompt,
            raw_evidence,
            "operator",
            concept_id=operator,
            path=path,
        )
        if isinstance(operator_evidence, EditContractError):
            return operator_evidence
        known_operator = metric_registry.resolve_operator_alias(
            operator_evidence.raw_text
        )
        if known_operator is not None and known_operator != operator:
            return _evidence_conflict(
                path,
                "operator",
                operator,
                {known_operator},
            )
        threshold_value = payload["threshold"]
        if isinstance(threshold_value, bool) or not isinstance(
            threshold_value,
            (int, float),
        ):
            return _malformed_value(path, "threshold")
        threshold_evidence = _candidate_span(
            prompt,
            raw_evidence,
            "threshold",
            concept_id="numeric_literal",
            path=path,
        )
        if isinstance(threshold_evidence, EditContractError):
            return threshold_evidence
        numeric = _parse_exact_numeric(threshold_evidence.raw_text)
        if numeric is None or not math.isclose(
            numeric,
            float(threshold_value),
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            return _error(
                "contract_grounded_invalid_evidence",
                "Threshold evidence must equal the candidate numeric value.",
                "rejected",
                reason="numeric_evidence_mismatch",
                field_path=f"{path}.threshold",
            )
        source_unit = str(payload["unit"]).strip().lower()
        unit_evidence = _candidate_span(
            prompt,
            raw_evidence,
            "unit",
            concept_id=source_unit,
            path=path,
        )
        if isinstance(unit_evidence, EditContractError):
            return unit_evidence
        resolved_unit = metric_registry.resolve_input_unit_alias(
            unit_evidence.raw_text
        )
        if (
            resolved_unit is None
            or resolved_unit[0] != definition.unit
            or resolved_unit[1] != source_unit
        ):
            return _error(
                "contract_unit_unsupported",
                "The threshold unit evidence is unsupported for this metric.",
                "unsupported",
                reason="unit_evidence_mismatch",
                field_path=f"{path}.unit",
            )
        try:
            threshold = metric_registry.normalize_explicit_threshold(
                metric_id,
                float(threshold_value),
                source_unit,
            )
        except MetricRegistryValidationError as exc:
            return _error(
                "contract_threshold_out_of_range",
                "The explicit threshold is outside the supported range.",
                "clarification_required",
                reason="threshold_range",
                detail=str(exc),
            )
        evidence.extend((operator_evidence, threshold_evidence, unit_evidence))
        threshold_source = "user_explicit"
        reference_mode = "absolute_outcome"
        normalized_profile_id = None

    clause_evidence = _candidate_span(
        prompt,
        raw_evidence,
        "clause",
        concept_id=metric_id,
        path=path,
    )
    if isinstance(clause_evidence, EditContractError):
        return clause_evidence
    if any(
        item.start < clause_evidence.start or item.end > clause_evidence.end
        for item in evidence
    ):
        return _error(
            "contract_grounded_invalid_evidence",
            "Constraint field evidence must lie inside its clause evidence.",
            "rejected",
            reason="evidence_outside_clause",
            field_path=path,
        )
    # A provider cannot make a second authoritative clause disappear by
    # stretching the evidence span of the first constraint across a boundary.
    # The released operation parser separately rejects any authoritative text
    # left outside the supplied constraint spans.
    if _CLAUSE_BOUNDARY_PATTERN.search(clause_evidence.raw_text):
        return _error(
            "contract_constraint_residue",
            "Part of the grounded protection condition could not be validated.",
            "clarification_required",
            reason="authoritative_residue",
            raw_text=clause_evidence.raw_text,
            metric_id=definition.metric_id,
        )
    residue_evidence = list(evidence)
    residue_evidence.extend(
        _grounded_registry_evidence(
            prompt,
            clause_evidence=clause_evidence,
            metric_registry=metric_registry,
            metric_id=definition.metric_id,
            operator=operator,
            canonical_unit=definition.unit,
        )
    )
    residue = _constraint_residue(
        clause_evidence.raw_text,
        residue_evidence,
        clause_evidence.start,
    )
    if residue:
        return _error(
            "contract_constraint_residue",
            "Part of the grounded protection condition could not be validated.",
            "clarification_required",
            reason="authoritative_residue",
            raw_text=residue,
            metric_id=definition.metric_id,
        )
    mask_type = _mask_type_for_definition(definition, subject_region)
    constraint = ContractConstraint(
        constraint_id=f"constraint_{index + 1}",
        metric_id=metric_id,
        metric_version=definition.metric_version,
        subject_region=subject_region,
        mask_type=mask_type,
        capability_requirements=tuple(sorted(definition.capability_requirements)),
        operator=operator,
        threshold=threshold,
        unit=definition.unit,
        threshold_source=threshold_source,
        reference_mode=reference_mode,
        profile_id=normalized_profile_id,
        source_evidence=RawSpanEvidence(
            start=clause_evidence.start,
            end=clause_evidence.end,
            raw_text=clause_evidence.raw_text,
            slot="constraint_clause",
            concept_id=metric_id,
            language=clause_evidence.language,
            confidence=clause_evidence.confidence,
        ),
        evidence=tuple(evidence),
    )
    try:
        return metric_registry.validate_constraint(constraint)
    except (KeyError, MetricRegistryValidationError) as exc:
        return _error(
            "contract_capability_invalid",
            "The grounded constraint is unsupported by the metric registry.",
            "unsupported",
            reason="constraint_registry_validation",
            detail=str(exc),
        )


def _resolve_subject_region(
    prompt: str,
    clause_start: int,
    clause_end: int,
    definition: MetricDefinition,
    parameter_registry: ParameterRegistry,
) -> tuple[str | EditContractError, RawSpanEvidence | None]:
    if definition.supported_subject_regions == frozenset({"all"}):
        return "all", None
    if definition.supported_subject_regions == frozenset({"outside_edit_scope"}):
        return "outside_edit_scope", None
    matches = [
        item
        for item in _region_matches(prompt, parameter_registry)
        if clause_start <= item.start and item.end <= clause_end
        and item.concept_id in definition.supported_subject_regions
    ]
    regions = {item.concept_id for item in matches}
    if len(regions) != 1:
        return (
            _error(
                "contract_scope_required",
                "The protection condition needs one supported subject region.",
                "clarification_required",
                reason="subject_region_resolution",
                metric_id=definition.metric_id,
                regions=sorted(regions),
            ),
            None,
        )
    selected = next(item for item in matches if item.concept_id in regions)
    return selected.concept_id, _evidence(
        selected,
        "constraint_region",
        selected.concept_id,
    )


def _region_matches(
    prompt: str,
    parameter_registry: ParameterRegistry,
) -> tuple[AliasMatch, ...]:
    normalized = normalize_semantic_text(prompt)
    candidates: list[tuple[int, int, str, str]] = []
    for definition in parameter_registry.regions.values():
        for alias in definition.aliases:
            surface = alias.normalized_text
            cursor = 0
            while cursor < len(normalized.text):
                start = normalized.text.find(surface, cursor)
                if start < 0:
                    break
                end = start + len(surface)
                if _alias_boundaries(normalized.text, surface, start, end):
                    candidates.append(
                        (start, end, definition.region_id, alias.language)
                    )
                cursor = start + 1
    selected: list[tuple[int, int, str, str]] = []
    for candidate in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(candidate[0] < item[1] and candidate[1] > item[0] for item in selected):
            continue
        selected.append(candidate)
    results: list[AliasMatch] = []
    for start, end, region_id, language in selected:
        raw_span = normalized.restore_span(start, end)
        results.append(
            AliasMatch(
                start=raw_span.start,
                end=raw_span.end,
                raw_text=prompt[raw_span.start : raw_span.end],
                normalized_text=normalized.text[start:end],
                language=language,
                kind="region",
                concept_id=region_id,
                value=region_id,
            )
        )
    return tuple(results)


def _known_region_values(
    raw_text: str,
    parameter_registry: ParameterRegistry,
) -> set[str]:
    return {
        item.concept_id
        for item in _region_matches(raw_text, parameter_registry)
        if item.start == 0 and item.end == len(raw_text)
    }


def _add_automatic_constraints(
    constraints: Sequence[ContractConstraint],
    *,
    semantic_ir: SemanticIR,
    metric_registry: MetricCapabilityRegistry,
) -> list[ContractConstraint] | EditContractError:
    result = list(constraints)
    if semantic_ir.region == "all":
        return result
    existing_metrics = {item.metric_id for item in result}
    for definition in metric_registry.metrics.values():
        if (
            definition.automatic_policy_trigger != "local_edit"
            or definition.metric_id in existing_metrics
        ):
            continue
        if len(result) >= MAX_CONTRACT_CONSTRAINTS:
            return _error(
                "contract_constraint_limit",
                "The local-edit safety policy would exceed the v1 constraint limit.",
                "clarification_required",
                reason="automatic_policy_constraint_limit",
            )
        profile = definition.get_profile(str(definition.automatic_profile_id))
        mask_type = default_mask_type_for_region(semantic_ir.region)
        if mask_type not in definition.required_mask_types:
            return _error(
                "contract_local_scope_unsupported",
                "The local edit mask cannot be verified for spillover.",
                "unsupported",
                reason="automatic_policy_mask_unsupported",
                region=semantic_ir.region,
                mask_type=mask_type,
            )
        result.append(
            ContractConstraint(
                constraint_id=f"constraint_{len(result) + 1}",
                metric_id=definition.metric_id,
                metric_version=definition.metric_version,
                subject_region="outside_edit_scope",
                mask_type=mask_type,
                capability_requirements=tuple(
                    sorted(definition.capability_requirements)
                ),
                operator=profile.operator,
                threshold=profile.threshold,
                unit=profile.unit,
                threshold_source="policy_default",
                reference_mode=profile.reference_mode,
                source="system_policy",
                profile_id=profile.profile_id,
                source_evidence=None,
                evidence=(),
            )
        )
    return result


def _restore_semantic_ir(prompt: str, masked: SemanticIR) -> SemanticIR:
    return SemanticIR(
        raw_prompt=prompt,
        operations=masked.operations,
        region=masked.region,
        language_sources=masked.language_sources,
        decision_source=masked.decision_source,
        evidence=masked.evidence,
        unresolved_spans=(),
        normalized_prompt=None,
        parser_version=f"{CONTRACT_SEMANTIC_PARSER_VERSION}_operation_projection",
        confidence=masked.confidence,
    )


def _released_operation_attempt_for_contract(
    prompt: str,
    contract_ir: EditContractIR,
    *,
    parameter_registry: ParameterRegistry,
    engine: str,
) -> SemanticParseAttempt | EditContractError:
    source_spans = [
        (constraint.source_start, constraint.source_end)
        for constraint in contract_ir.constraints
        if constraint.source == "user"
        and constraint.source_start is not None
        and constraint.source_end is not None
    ]
    attempt = parse_semantic_prompt(
        _operation_projection(prompt, source_spans),
        registry=parameter_registry,
        engine=engine,
    )
    if attempt.accepted_ir is None:
        return _error(
            "contract_operation_not_released",
            "The grounded contract operation is not accepted by the released semantic parser.",
            "clarification_required",
            reason="released_operation_parse_failed",
            semantic_code=(None if attempt.error is None else attempt.error.code),
        )
    expected = tuple(_operation_identity(item) for item in contract_ir.operations)
    released = tuple(
        _operation_identity(item) for item in attempt.accepted_ir.operations
    )
    if expected != released:
        return _error(
            "contract_operation_grounding_conflict",
            "The grounded operation conflicts with the released deterministic parser.",
            "rejected",
            reason="operation_semantic_mismatch",
            grounded=list(expected),
            released=list(released),
        )
    return attempt


def _operation_identity(operation: Any) -> tuple[object, ...]:
    return (
        operation.axis_id,
        operation.operation_type,
        operation.operation_kind,
        operation.direction,
        operation.strength,
        operation.value,
        operation.region,
        operation.target_group_intent,
    )


def _operation_projection(
    prompt: str,
    spans: Sequence[tuple[int, int]],
) -> str:
    chars = list(prompt)
    for start, end in spans:
        for index in range(max(0, start), min(len(chars), end)):
            chars[index] = " "
    # Find structural separators on the untouched source.  A look-ahead such
    # as ``and keep`` would disappear if the constraint span were blanked
    # first, leaving a dangling conjunction for the released operation parser.
    for match in _CLAUSE_BOUNDARY_PATTERN.finditer(prompt):
        for index in range(match.start(), match.end()):
            chars[index] = " "
    return "".join(chars)


def _clause_bounds(prompt: str, position: int) -> tuple[int, int]:
    start = 0
    end = len(prompt)
    for match in _CLAUSE_BOUNDARY_PATTERN.finditer(prompt):
        if match.end() <= position:
            start = match.end()
            continue
        if match.start() >= position:
            end = match.start()
            break
    while start < end and prompt[start].isspace():
        start += 1
    while end > start and prompt[end - 1].isspace():
        end -= 1
    return start, end


def _same_clause(prompt: str, left: AliasMatch, right: AliasMatch) -> bool:
    left_bounds = _clause_bounds(prompt, left.start)
    return left_bounds[0] <= right.start and right.end <= left_bounds[1]


def _is_standalone_qualitative_metric_intent(
    prompt: str,
    *,
    metric_matches: Sequence[AliasMatch],
    relation_matches: Sequence[AliasMatch],
) -> bool:
    """Keep a single qualitative edit intent on the established prompt path.

    Metric vocabulary is shared by ordinary edit language and hard contracts.
    A qualitative protection phrase that starts the request does not by itself
    create a separate operation-plus-constraint structure.  Explicit operators
    or numbers remain fail-closed contract evidence, while meaningful edit text
    before the typed protection phrase establishes the separate operation.
    """

    paired_relations = tuple(
        relation
        for relation in relation_matches
        if any(_same_clause(prompt, metric, relation) for metric in metric_matches)
    )
    if not paired_relations:
        return False
    if any(item.kind == "operator" for item in paired_relations):
        return False
    if _NUMBER_PATTERN.search(prompt):
        return False
    constraint_start = min(
        [item.start for item in metric_matches]
        + [item.start for item in paired_relations]
    )
    return _IGNORABLE_RESIDUE.fullmatch(prompt[:constraint_start]) is not None


def _has_released_operation_before_relation(
    prompt: str,
    *,
    relation_matches: Sequence[AliasMatch],
    parameter_registry: ParameterRegistry,
    engine: str,
) -> bool:
    """Detect compound structure from released operation semantics.

    The boundary is derived from typed protection/operator evidence, not an
    enumerated connector list.  Trying progressively shorter prefixes lets the
    released parser discard ordinary conjunction framing while still requiring
    a real, accepted edit operation before the protection relation.
    """

    for relation in sorted(relation_matches, key=lambda item: item.start):
        for end in range(relation.start, 0, -1):
            if not prompt[:end].strip():
                continue
            attempt = parse_semantic_prompt(
                prompt[:end],
                registry=parameter_registry,
                engine=engine,
            )
            accepted = attempt.accepted_ir
            if accepted is None or not accepted.operations:
                continue
            if all(
                evidence.end <= relation.start
                for operation in accepted.operations
                for evidence in operation.evidence
            ):
                return True
    return False


def _numeric_matches(
    prompt: str,
    start: int,
    end: int,
) -> list[tuple[int, int, float]]:
    results: list[tuple[int, int, float]] = []
    for match in _NUMBER_PATTERN.finditer(prompt[start:end]):
        absolute_start = start + match.start()
        absolute_end = start + match.end()
        numeric = float(match.group(0))
        if math.isfinite(numeric):
            results.append((absolute_start, absolute_end, numeric))
    return results


def _constraint_residue(
    source_text: str,
    evidence: Sequence[RawSpanEvidence],
    source_start: int,
) -> str:
    chars = list(source_text)
    for item in evidence:
        for index in range(item.start - source_start, item.end - source_start):
            if 0 <= index < len(chars):
                chars[index] = " "
    residue = "".join(chars).strip()
    return "" if _IGNORABLE_RESIDUE.fullmatch(residue) else residue


def _grounded_registry_evidence(
    prompt: str,
    *,
    clause_evidence: RawSpanEvidence,
    metric_registry: MetricCapabilityRegistry,
    metric_id: str,
    operator: str,
    canonical_unit: str,
) -> tuple[RawSpanEvidence, ...]:
    """Return only registry-grounded function spans allowed in this clause.

    Field evidence remains authoritative.  These supplemental spans account
    for generic protection framing such as ``keep`` that is not a candidate
    output field, while unrelated metrics, operators, modifiers, or trailing
    conditions remain visible to the shared residue checker.
    """

    accepted: list[RawSpanEvidence] = []
    for match in metric_registry.match_aliases(clause_evidence.raw_text):
        allowed = (
            match.kind in {"profile_signal", "protection_signal"}
            or (match.kind == "metric" and match.concept_id == metric_id)
            or (match.kind == "operator" and match.concept_id == operator)
            or (match.kind == "unit" and match.value == canonical_unit)
        )
        if not allowed:
            continue
        start = clause_evidence.start + match.start
        end = clause_evidence.start + match.end
        accepted.append(
            RawSpanEvidence(
                start=start,
                end=end,
                raw_text=prompt[start:end],
                slot="constraint_registry_relation",
                concept_id=match.concept_id,
                language=match.language,
            )
        )
    return tuple(accepted)


def _mask_type_for_definition(
    definition: MetricDefinition,
    subject_region: str,
) -> str:
    if subject_region == "all":
        return "none"
    if subject_region == "person":
        return "semantic_person"
    if len(definition.required_mask_types) == 1:
        return next(iter(definition.required_mask_types))
    # Explicit outside-scope constraints are completed after the operation
    # region is known; this placeholder remains a declared mask capability.
    return sorted(definition.required_mask_types)[0]


def _candidate_span(
    prompt: str,
    evidence_map: Mapping[object, object],
    field: str,
    *,
    concept_id: str,
    path: str,
) -> RawSpanEvidence | EditContractError:
    payload = evidence_map.get(field)
    if not isinstance(payload, Mapping):
        return _error(
            "contract_grounded_missing_evidence",
            "A grounded constraint field is missing exact source evidence.",
            "rejected",
            reason="missing_field_evidence",
            field_path=f"{path}.{field}",
        )
    unknown = set(payload).difference(_SPAN_FIELDS)
    if unknown or not {"start", "end", "raw_text"}.issubset(payload):
        return _error(
            "contract_grounded_malformed_response",
            "Evidence requires only start, end, raw_text, language, and confidence.",
            "rejected",
            reason="invalid_evidence_shape",
            field_path=f"{path}.evidence.{field}",
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
        or start < 0
        or end <= start
        or end > len(prompt)
        or prompt[start:end] != raw_text
        or not raw_text.strip()
    ):
        return _error(
            "contract_grounded_invalid_evidence",
            "Evidence must exactly match a non-empty raw prompt slice.",
            "rejected",
            reason="evidence_text_or_offset_mismatch",
            field_path=f"{path}.evidence.{field}",
        )
    confidence = _confidence(payload.get("confidence", 1.0))
    if isinstance(confidence, EditContractError):
        return confidence
    language = str(payload.get("language") or _guess_language(raw_text)).strip()
    return RawSpanEvidence(
        start=start,
        end=end,
        raw_text=raw_text,
        slot=f"constraint_{field}",
        concept_id=concept_id,
        language=language or "und",
        confidence=confidence,
    )


def _evidence(
    match: AliasMatch,
    slot: str,
    concept_id: str,
) -> RawSpanEvidence:
    return RawSpanEvidence(
        start=match.start,
        end=match.end,
        raw_text=match.raw_text,
        slot=slot,
        concept_id=concept_id,
        language=match.language,
    )


def _duplicate_constraint_error(
    constraints: Sequence[ContractConstraint],
) -> EditContractError | None:
    seen: set[tuple[str, str]] = set()
    for constraint in constraints:
        key = (constraint.metric_id, constraint.subject_region)
        if key in seen:
            return _error(
                "contract_duplicate_constraint",
                "The same metric and subject region cannot be constrained twice.",
                "clarification_required",
                reason="duplicate_metric_scope",
                metric_id=constraint.metric_id,
                subject_region=constraint.subject_region,
            )
        seen.add(key)
    return None


def _translate_operation_error(error: GroundedLLMError) -> EditContractError:
    disposition: ContractDisposition = (
        "unsupported"
        if error.code in {"grounded_llm_unknown_axis", "grounded_llm_unknown_region"}
        else "rejected"
        if error.code in {
            "grounded_llm_unsafe_output",
            "grounded_llm_invalid_evidence",
            "grounded_llm_evidence_conflict",
            "grounded_llm_malformed_response",
        }
        else "clarification_required"
    )
    return EditContractError(
        code="contract_operation_invalid",
        message=error.message,
        disposition=disposition,
        issues=tuple(
            [{"semantic_code": error.code}]
            + [dict(issue) for issue in error.issues]
        ),
        status_code=error.status_code,
        retryable=error.retryable,
    )


def _candidate_language(value: object) -> str:
    language = str(value).strip().lower()
    return language or "und"


def _combined_language(languages: Sequence[str] | Any) -> str:
    values = tuple(dict.fromkeys(_candidate_language(item) for item in languages))
    return values[0] if len(values) == 1 else "mixed"


def _contract_languages(
    semantic_ir: SemanticIR,
    constraints: Sequence[ContractConstraint],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            tuple(semantic_ir.language_sources)
            + tuple(
                evidence.language
                for constraint in constraints
                for evidence in constraint.evidence
            )
        )
    ) or ("und",)


def _parse_exact_numeric(raw_text: str) -> float | None:
    normalized = normalize_contract_alias(raw_text)
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", normalized):
        return None
    value = float(normalized)
    return value if math.isfinite(value) else None


def _confidence(value: object) -> float | EditContractError:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _error(
            "contract_grounded_malformed_response",
            "Candidate confidence must be numeric.",
            "rejected",
            reason="invalid_confidence",
        )
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        return _error(
            "contract_grounded_malformed_response",
            "Candidate confidence must be finite and between zero and one.",
            "rejected",
            reason="invalid_confidence",
        )
    return numeric


def _find_unsafe_field(value: object, path: str = "candidate") -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            child = f"{path}.{key}"
            if normalized in _UNSAFE_FIELDS:
                return child
            nested = _find_unsafe_field(item, child)
            if nested is not None:
                return nested
    elif _is_sequence(value):
        for index, item in enumerate(value):
            nested = _find_unsafe_field(item, f"{path}[{index}]")
            if nested is not None:
                return nested
    return None


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )


def _guess_language(raw_text: str) -> str:
    has_cjk = any("\u3400" <= char <= "\u9fff" for char in raw_text)
    has_ascii = any(char.isascii() and char.isalpha() for char in raw_text)
    if has_cjk and has_ascii:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_ascii:
        return "en"
    return "und"


def _alias_boundaries(text: str, alias: str, start: int, end: int) -> bool:
    def word(char: str) -> bool:
        return char == "_" or (char.isascii() and char.isalnum())

    return (
        start == 0 or not word(text[start - 1]) or not word(alias[0])
    ) and (
        end == len(text) or not word(text[end]) or not word(alias[-1])
    )


def _malformed_value(path: str, field: str) -> EditContractError:
    return _error(
        "contract_grounded_malformed_response",
        f"Grounded constraint {field} has an invalid value.",
        "rejected",
        reason="invalid_field_value",
        field_path=f"{path}.{field}",
    )


def _evidence_conflict(
    path: str,
    field: str,
    expected: object,
    known: set[object],
) -> EditContractError:
    return _error(
        "contract_grounded_evidence_conflict",
        "Evidence text has a conflicting registered meaning.",
        "rejected",
        reason="registered_evidence_conflict",
        field_path=f"{path}.{field}",
        expected=expected,
        registered=sorted(known, key=str),
    )


def _attempt_from_error(
    prompt: str,
    error: EditContractError,
) -> ContractSemanticAttempt:
    return ContractSemanticAttempt(
        raw_prompt=prompt,
        disposition=error.disposition,
        error=error,
    )


def _error(
    code: str,
    message: str,
    disposition: ContractDisposition,
    *,
    status_code: int = 422,
    retryable: bool = False,
    **issue: Any,
) -> EditContractError:
    return EditContractError(
        code=code,
        message=message,
        disposition=disposition,
        issues=(issue,) if issue else (),
        status_code=status_code,
        retryable=retryable,
    )


__all__ = [
    "CONTRACT_SEMANTIC_PARSER_VERSION",
    "ContractSemanticAttempt",
    "GroundedContractProvider",
    "adapt_grounded_contract_candidate",
    "grounded_contract_candidate_schema",
    "invoke_grounded_contract_candidate",
    "parse_edit_contract_prompt",
]
