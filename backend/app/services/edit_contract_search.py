"""Generic bounded magnitude search for verifiable edit contracts.

The searcher knows nothing about image metrics or edit parameters.  A caller
renders one scale from the immutable anchor, evaluates every hard constraint,
and returns a typed candidate.  This module then chooses the largest observed
safe positive scale and performs a mandatory final revalidation.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from app.services.edit_contract_schema import ContractSearchAttempt, MetricCheck


SEARCH_POLICY_VERSION = "bounded_largest_safe_v2"
SearchStatus = Literal["passed", "adjusted", "unsatisfied", "no_change"]

PayloadT = TypeVar("PayloadT")


class EditContractSearchError(ValueError):
    """The search policy or callback result violates the search contract."""

    def __init__(self, code: str, message: str, *, scale: float | None = None):
        self.code = str(code)
        self.scale = scale
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ScaleCandidateEvaluation(Generic[PayloadT]):
    payload: PayloadT
    checks: tuple[MetricCheck, ...]
    has_effect: bool
    render_ms: float = 0.0
    verification_ms: float = 0.0
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        checks = tuple(self.checks)
        if not all(isinstance(check, MetricCheck) for check in checks):
            raise TypeError("checks must contain MetricCheck values")
        object.__setattr__(self, "checks", checks)
        if not isinstance(self.has_effect, bool):
            raise TypeError("has_effect must be boolean")
        for name in ("render_ms", "verification_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, numeric)
        if not isinstance(self.diagnostics, Mapping):
            raise TypeError("diagnostics must be a mapping")


@dataclass(frozen=True, slots=True)
class ScaleSearchPolicy:
    version: str = SEARCH_POLICY_VERSION
    coarse_scales: tuple[float, ...] = (
        1.0,
        0.875,
        0.75,
        0.625,
        0.5,
        0.375,
        0.25,
        0.125,
        0.0,
    )
    refinement_steps: int = 2
    # Service execution performs one lineage preflight before entering this
    # search.  Eleven search renders therefore keep the end-to-end default at
    # no more than twelve renders, including mandatory final revalidation.
    max_render_count: int = 11
    timeout_seconds: float = 12.0
    scale_precision: int = 6

    def __post_init__(self) -> None:
        version = str(self.version or "").strip()
        if not version:
            raise ValueError("search policy version must not be empty")
        object.__setattr__(self, "version", version)
        if not isinstance(self.coarse_scales, tuple) or not self.coarse_scales:
            raise ValueError("coarse_scales must be a non-empty tuple")
        normalized: list[float] = []
        for raw in self.coarse_scales:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise TypeError("coarse scales must be numeric")
            scale = float(raw)
            if not math.isfinite(scale) or not 0.0 <= scale <= 1.0:
                raise ValueError("coarse scales must be finite values in [0, 1]")
            normalized.append(scale)
        if normalized[0] != 1.0:
            raise ValueError("coarse scales must begin at 1.0")
        if any(left <= right for left, right in zip(normalized, normalized[1:])):
            raise ValueError("coarse scales must be strictly descending")
        if len({round(value, 12) for value in normalized}) != len(normalized):
            raise ValueError("coarse scales must be unique")
        object.__setattr__(self, "coarse_scales", tuple(normalized))
        for name, minimum in (
            ("refinement_steps", 0),
            ("max_render_count", 2),
            ("scale_precision", 1),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.scale_precision > 12:
            raise ValueError("scale_precision must be <= 12")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or float(self.timeout_seconds) <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


@dataclass(frozen=True, slots=True)
class ScaleSearchResult(Generic[PayloadT]):
    status: SearchStatus
    selected_scale: float | None
    payload: PayloadT | None
    final_checks: tuple[MetricCheck, ...]
    attempts: tuple[ContractSearchAttempt, ...]
    policy_version: str
    stop_reason: str
    render_count: int
    elapsed_ms: float

    @property
    def succeeded(self) -> bool:
        return self.status in {"passed", "adjusted"}


EvaluationCallback = Callable[[float], ScaleCandidateEvaluation[PayloadT]]


def search_largest_safe_scale(
    evaluate_scale: EvaluationCallback[PayloadT],
    *,
    required_constraint_ids: Collection[str],
    policy: ScaleSearchPolicy | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ScaleSearchResult[PayloadT]:
    """Find and finally revalidate the largest observed safe positive scale."""

    if not callable(evaluate_scale):
        raise TypeError("evaluate_scale must be callable")
    if not callable(clock):
        raise TypeError("clock must be callable")
    resolved_policy = policy or ScaleSearchPolicy()
    required = _normalize_required_ids(required_constraint_ids)
    started = clock()
    if not isinstance(started, (int, float)) or not math.isfinite(float(started)):
        raise EditContractSearchError(
            "invalid_clock",
            "Search clock must return finite numeric values.",
        )
    started = float(started)
    deadline = started + resolved_policy.timeout_seconds
    attempts: list[ContractSearchAttempt] = []
    best_scale: float | None = None
    best_evaluation: ScaleCandidateEvaluation[PayloadT] | None = None
    previous_unsafe_scale: float | None = None
    stop_reason: str | None = None

    def elapsed_ms() -> float:
        now = _clock_value(clock)
        return max(0.0, (now - started) * 1000.0)

    def run_evaluation(
        scale: float,
        *,
        reserve_final: bool,
        final: bool = False,
    ) -> tuple[ScaleCandidateEvaluation[PayloadT] | None, bool]:
        nonlocal stop_reason
        limit = resolved_policy.max_render_count - (1 if reserve_final else 0)
        if len(attempts) >= limit:
            stop_reason = "render_limit"
            return None, False
        if _clock_value(clock) >= deadline:
            stop_reason = "timeout"
            return None, False
        try:
            evaluation = evaluate_scale(scale)
        except Exception as exc:
            raise EditContractSearchError(
                "candidate_evaluation_failed",
                f"Candidate evaluation failed at scale {scale:g}: {exc}",
                scale=scale,
            ) from exc
        if not isinstance(evaluation, ScaleCandidateEvaluation):
            raise EditContractSearchError(
                "invalid_candidate_evaluation",
                "evaluate_scale must return ScaleCandidateEvaluation.",
                scale=scale,
            )
        if evaluation.payload is None:
            raise EditContractSearchError(
                "missing_candidate_payload",
                "A rendered candidate evaluation must include its payload.",
                scale=scale,
            )
        passed_all = _validate_and_reduce_checks(evaluation.checks, required)
        has_succeeded = scale > 0.0 and passed_all and evaluation.has_effect
        failure_reason = None
        if not passed_all:
            failure_reason = "constraint_failed"
        elif scale <= 0.0:
            failure_reason = "baseline_diagnostic_only"
        elif not evaluation.has_effect:
            failure_reason = "no_effect"
        if final and not has_succeeded:
            failure_reason = "final_revalidation_failed"
        attempts.append(
            ContractSearchAttempt(
                scale=scale,
                checks=evaluation.checks,
                passed=has_succeeded,
                render_ms=evaluation.render_ms,
                verification_ms=evaluation.verification_ms,
                failure_reason=failure_reason,
            )
        )
        if _clock_value(clock) >= deadline:
            stop_reason = "timeout"
            return None, False
        return evaluation, has_succeeded

    # Full requested magnitude gets a fast path, while still requiring the
    # same mandatory final render and verification as an adjusted result.
    full_evaluation, full_safe = run_evaluation(
        1.0,
        reserve_final=True,
    )
    if stop_reason is not None:
        return _failed_result(
            attempts,
            resolved_policy,
            elapsed_ms(),
            stop_reason,
        )
    if full_safe:
        return _finalize_success(
            status="passed",
            scale=1.0,
            evaluate=run_evaluation,
            attempts=attempts,
            policy=resolved_policy,
            elapsed_ms=elapsed_ms,
            get_stop_reason=lambda: stop_reason,
        )
    previous_unsafe_scale = 1.0

    zero_requested = False
    for configured_scale in resolved_policy.coarse_scales[1:]:
        if configured_scale == 0.0:
            zero_requested = True
            continue
        scale = round(configured_scale, resolved_policy.scale_precision)
        evaluation, safe = run_evaluation(scale, reserve_final=True)
        if stop_reason is not None:
            return _failed_result(
                attempts,
                resolved_policy,
                elapsed_ms(),
                stop_reason,
            )
        if safe:
            best_scale = scale
            best_evaluation = evaluation
            break
        previous_unsafe_scale = scale

    if best_scale is None or best_evaluation is None:
        if zero_requested:
            run_evaluation(0.0, reserve_final=False)
        if stop_reason is not None:
            return _failed_result(
                attempts,
                resolved_policy,
                elapsed_ms(),
                stop_reason,
            )
        status: SearchStatus = (
            "no_change"
            if any(
                attempt.scale > 0.0
                and attempt.failure_reason == "no_effect"
                for attempt in attempts
            )
            else "unsatisfied"
        )
        return ScaleSearchResult(
            status=status,
            selected_scale=None,
            payload=None,
            final_checks=(),
            attempts=tuple(attempts),
            policy_version=resolved_policy.version,
            stop_reason="no_effect" if status == "no_change" else "no_safe_positive_scale",
            render_count=len(attempts),
            elapsed_ms=elapsed_ms(),
        )

    # The fixed 1/8 grid deliberately samples the whole positive range instead
    # of assuming that only powers-of-two can expose a safe interval.  The
    # first passing point is the largest *observed* safe coarse scale because
    # the schedule is descending.  Refinement stays inside its adjacent
    # observed unsafe bracket and never assumes a metric-specific shape.
    upper = float(previous_unsafe_scale)
    lower = best_scale
    for _ in range(resolved_policy.refinement_steps):
        midpoint = round((upper + lower) / 2.0, resolved_policy.scale_precision)
        if midpoint <= lower or midpoint >= upper:
            break
        evaluation, safe = run_evaluation(midpoint, reserve_final=True)
        if stop_reason is not None:
            return _failed_result(
                attempts,
                resolved_policy,
                elapsed_ms(),
                stop_reason,
            )
        if safe:
            lower = midpoint
            if midpoint > best_scale:
                best_scale = midpoint
                best_evaluation = evaluation
        else:
            upper = midpoint

    return _finalize_success(
        status="adjusted",
        scale=best_scale,
        evaluate=run_evaluation,
        attempts=attempts,
        policy=resolved_policy,
        elapsed_ms=elapsed_ms,
        get_stop_reason=lambda: stop_reason,
    )


def _finalize_success(
    *,
    status: Literal["passed", "adjusted"],
    scale: float,
    evaluate: Callable[..., tuple[ScaleCandidateEvaluation[PayloadT] | None, bool]],
    attempts: list[ContractSearchAttempt],
    policy: ScaleSearchPolicy,
    elapsed_ms: Callable[[], float],
    get_stop_reason: Callable[[], str | None],
) -> ScaleSearchResult[PayloadT]:
    final_evaluation, final_safe = evaluate(
        scale,
        reserve_final=False,
        final=True,
    )
    if not final_safe or final_evaluation is None:
        reason = get_stop_reason() or "final_revalidation_failed"
        return _failed_result(
            attempts,
            policy,
            elapsed_ms(),
            reason,
        )
    return ScaleSearchResult(
        status=status,
        selected_scale=scale,
        payload=final_evaluation.payload,
        final_checks=final_evaluation.checks,
        attempts=tuple(attempts),
        policy_version=policy.version,
        stop_reason="full_scale_verified" if status == "passed" else "adjusted_scale_verified",
        render_count=len(attempts),
        elapsed_ms=elapsed_ms(),
    )


def _failed_result(
    attempts: list[ContractSearchAttempt],
    policy: ScaleSearchPolicy,
    elapsed_ms: float,
    reason: str,
) -> ScaleSearchResult[Any]:
    return ScaleSearchResult(
        status="unsatisfied",
        selected_scale=None,
        payload=None,
        final_checks=(),
        attempts=tuple(attempts),
        policy_version=policy.version,
        stop_reason=reason,
        render_count=len(attempts),
        elapsed_ms=elapsed_ms,
    )


def _normalize_required_ids(values: Collection[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise TypeError("required_constraint_ids must be a collection of IDs")
    normalized = tuple(str(value or "").strip().lower() for value in values)
    if not normalized or any(not value for value in normalized):
        raise EditContractSearchError(
            "missing_required_constraints",
            "At least one non-empty required constraint ID is needed.",
        )
    if len(normalized) != len(set(normalized)):
        raise EditContractSearchError(
            "duplicate_required_constraints",
            "Required constraint IDs must be unique.",
        )
    return frozenset(normalized)


def _validate_and_reduce_checks(
    checks: tuple[MetricCheck, ...],
    required: frozenset[str],
) -> bool:
    identifiers = tuple(check.constraint_id for check in checks)
    if len(identifiers) != len(set(identifiers)):
        raise EditContractSearchError(
            "duplicate_constraint_check",
            "A candidate returned duplicate constraint checks.",
        )
    actual = frozenset(identifiers)
    if actual != required:
        raise EditContractSearchError(
            "constraint_check_coverage_mismatch",
            "Candidate checks do not exactly cover every required hard constraint; "
            f"missing={sorted(required - actual)!r}, extra={sorted(actual - required)!r}.",
        )
    return all(check.passed for check in checks)


def _clock_value(clock: Callable[[], float]) -> float:
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditContractSearchError(
            "invalid_clock",
            "Search clock must return finite numeric values.",
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise EditContractSearchError(
            "invalid_clock",
            "Search clock must return finite numeric values.",
        )
    return numeric


__all__ = [
    "EditContractSearchError",
    "EvaluationCallback",
    "SEARCH_POLICY_VERSION",
    "ScaleCandidateEvaluation",
    "ScaleSearchPolicy",
    "ScaleSearchResult",
    "SearchStatus",
    "search_largest_safe_scale",
]
