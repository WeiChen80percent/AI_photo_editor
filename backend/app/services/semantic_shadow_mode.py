"""Observe grounded-LLM semantic candidates without changing edit decisions.

The deterministic parser remains authoritative.  This module is an explicitly
opt-in development observer: it invokes a schema-constrained candidate
provider, validates the candidate through ``llm_semantic_adapter``, compares
the validated semantic meaning with the deterministic attempt, and returns
bounded telemetry.  The telemetry is never fed back into the compiler,
controller, renderer, or history store.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.llm_semantic_adapter import (
    CandidateProvider,
    GroundedLLMError,
    invoke_grounded_llm_candidate,
)
from app.services.semantic_ir import SemanticIR
from app.services.semantic_parser import SemanticParseAttempt
from app.services.semantic_registry import DEFAULT_PARAMETER_REGISTRY


logger = logging.getLogger(__name__)

SEMANTIC_SHADOW_VERSION = "semantic_grounded_shadow_v1"
SEMANTIC_SHADOW_ENV = "AI_PHOTO_SEMANTIC_LLM_SHADOW"
_ENABLED_VALUES = frozenset({"1", "true", "yes", "on", "observe"})
_MAX_CAPTURE_DEPTH = 5
_MAX_CAPTURE_ITEMS = 24
_MAX_CAPTURE_TEXT = 240


@dataclass(frozen=True, slots=True)
class SemanticShadowObservation:
    """Bounded, JSON-safe observer result for one prompt."""

    enabled: bool
    status: str
    parity: str
    provider_invoked: bool
    production: Mapping[str, Any]
    candidate: Mapping[str, Any] | None = None
    rejection: Mapping[str, Any] | None = None
    captured_candidate: object | None = None
    elapsed_ms: float = 0.0
    version: str = SEMANTIC_SHADOW_VERSION

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "enabled": self.enabled,
            "status": self.status,
            "parity": self.parity,
            "provider_invoked": self.provider_invoked,
            "production": dict(self.production),
            "candidate": (
                dict(self.candidate) if self.candidate is not None else None
            ),
            "rejection": (
                dict(self.rejection) if self.rejection is not None else None
            ),
            "captured_candidate": self.captured_candidate,
            "elapsed_ms": round(max(0.0, float(self.elapsed_ms)), 3),
            "version": self.version,
        }
        return payload


def semantic_shadow_is_enabled() -> bool:
    return os.getenv(SEMANTIC_SHADOW_ENV, "0").strip().lower() in _ENABLED_VALUES


def observe_grounded_semantic_shadow(
    *,
    prompt: str,
    deterministic_attempt: SemanticParseAttempt,
    engine: str = "opencv",
    provider: CandidateProvider | None = None,
    enabled: bool | None = None,
) -> SemanticShadowObservation:
    """Run one fail-isolated semantic observation.

    ``provider`` is dependency-injected for tests.  With no provider, the
    observer uses the existing local Ollama endpoint only when both semantic
    shadow mode and ``AI_PHOTO_USE_LLM`` are enabled.
    """

    should_observe = semantic_shadow_is_enabled() if enabled is None else enabled
    production = _production_summary(deterministic_attempt)
    if not should_observe:
        return SemanticShadowObservation(
            enabled=False,
            status="disabled",
            parity="not_evaluated",
            provider_invoked=False,
            production=production,
        )

    effective_provider = provider or _get_default_grounded_provider()
    if effective_provider is None:
        return SemanticShadowObservation(
            enabled=True,
            status="provider_disabled",
            parity="not_evaluated",
            provider_invoked=False,
            production=production,
            rejection={
                "code": "grounded_llm_disabled",
                "reason": "llm_transport_disabled",
            },
        )

    provider_invoked = False
    captured: object | None = None

    def capture_provider(raw_prompt: str) -> object:
        nonlocal provider_invoked, captured
        provider_invoked = True
        captured = effective_provider(raw_prompt)
        return captured

    started = time.perf_counter()
    try:
        result = invoke_grounded_llm_candidate(
            prompt,
            capture_provider,
            deterministic=deterministic_attempt.extraction,
            engine=engine,
        )
    except Exception as exc:  # observer bugs must never affect production
        logger.exception("Semantic shadow observer failed unexpectedly.")
        observation = SemanticShadowObservation(
            enabled=True,
            status="observer_error",
            parity="not_evaluated",
            provider_invoked=provider_invoked,
            production=production,
            rejection={
                "code": "semantic_shadow_observer_error",
                "reason": "observer_exception",
                "exception_type": type(exc).__name__,
            },
            captured_candidate=_bounded_json_value(captured),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
        _log_observation(observation)
        return observation

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if isinstance(result, GroundedLLMError):
        status = (
            "skipped"
            if result.code == "grounded_llm_not_needed"
            else "rejected"
        )
        observation = SemanticShadowObservation(
            enabled=True,
            status=status,
            parity=(
                "deterministic_authoritative"
                if status == "skipped"
                else "not_comparable"
            ),
            provider_invoked=provider_invoked,
            production=production,
            rejection=_bounded_json_value(result.as_dict()),
            captured_candidate=_bounded_json_value(captured),
            elapsed_ms=elapsed_ms,
        )
        _log_observation(observation)
        return observation

    candidate = _ir_summary(result)
    parity = _semantic_parity(deterministic_attempt.accepted_ir, result)
    observation = SemanticShadowObservation(
        enabled=True,
        status="accepted",
        parity=parity,
        provider_invoked=provider_invoked,
        production=production,
        candidate=candidate,
        captured_candidate=_bounded_json_value(captured),
        elapsed_ms=elapsed_ms,
    )
    _log_observation(observation)
    return observation


def _semantic_parity(
    deterministic_ir: SemanticIR | None,
    candidate_ir: SemanticIR,
) -> str:
    if deterministic_ir is None:
        return "candidate_only"
    return (
        "match"
        if _meaning_signature(deterministic_ir)
        == _meaning_signature(candidate_ir)
        else "mismatch"
    )


def _meaning_signature(ir: SemanticIR) -> tuple[object, ...]:
    operations = tuple(
        (
            operation.axis_id,
            operation.operation_type,
            operation.operation_kind,
            operation.direction,
            operation.strength,
            operation.value,
            operation.region,
            operation.target_group_intent,
        )
        for operation in ir.operations
    )
    return ir.region, operations


def _production_summary(attempt: SemanticParseAttempt) -> dict[str, Any]:
    result: dict[str, Any] = {
        "disposition": attempt.disposition,
        "parser_version": attempt.parser_version,
    }
    if attempt.accepted_ir is not None:
        result["semantic"] = _ir_summary(attempt.accepted_ir)
    elif attempt.error is not None:
        result["error"] = {
            "code": attempt.error.code,
            "message": attempt.error.message,
            "issues": _bounded_json_value(attempt.error.issues),
        }
    return result


def _ir_summary(ir: SemanticIR) -> dict[str, Any]:
    return {
        "decision_source": ir.decision_source,
        "parser_version": ir.parser_version,
        "confidence": ir.confidence,
        "region": ir.region,
        "operations": [
            {
                "axis": operation.axis_id,
                "type": operation.operation_type,
                "kind": operation.operation_kind,
                "direction": operation.direction,
                "strength": operation.strength,
                "value": operation.value,
                "region": operation.region,
                "target_group_intent": operation.target_group_intent,
                "evidence": [
                    evidence.as_dict() for evidence in operation.evidence
                ],
            }
            for operation in ir.operations
        ],
        "unresolved_spans": [
            span.as_dict() for span in ir.unresolved_spans
        ],
    }


def _bounded_json_value(value: object, *, depth: int = 0) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_CAPTURE_TEXT]
    if depth >= _MAX_CAPTURE_DEPTH:
        return {"truncated": True, "type": type(value).__name__}
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_CAPTURE_ITEMS:
                result["__truncated__"] = True
                break
            result[str(key)[:80]] = _bounded_json_value(
                item,
                depth=depth + 1,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        items = [
            _bounded_json_value(item, depth=depth + 1)
            for item in value[:_MAX_CAPTURE_ITEMS]
        ]
        if len(value) > _MAX_CAPTURE_ITEMS:
            items.append({"truncated": True})
        return items
    return {"type": type(value).__name__}


def _get_default_grounded_provider() -> CandidateProvider | None:
    enabled = os.getenv("AI_PHOTO_USE_LLM", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None
    return _call_ollama_grounded_candidate


def _call_ollama_grounded_candidate(prompt: str) -> object:
    url = os.getenv(
        "AI_PHOTO_OLLAMA_URL",
        "http://127.0.0.1:11434/api/generate",
    )
    model = os.getenv("AI_PHOTO_OLLAMA_MODEL", "llama3.2:3b")
    timeout = float(os.getenv("AI_PHOTO_OLLAMA_TIMEOUT", "8"))
    num_predict = int(os.getenv("AI_PHOTO_OLLAMA_NUM_PREDICT", "384"))
    payload = {
        "model": model,
        "prompt": _grounded_candidate_prompt(prompt),
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_payload = json.loads(response.read().decode("utf-8"))
    response_text = response_payload.get("response")
    if not isinstance(response_text, str):
        raise ValueError("Ollama response did not include a response string.")
    candidate = json.loads(response_text)
    if not isinstance(candidate, Mapping):
        raise ValueError("Grounded Ollama response must be a JSON object.")
    return candidate


def _grounded_candidate_prompt(user_prompt: str) -> str:
    axes = ", ".join(DEFAULT_PARAMETER_REGISTRY.axes)
    regions = ", ".join(DEFAULT_PARAMETER_REGISTRY.regions)
    return f"""
You are a shadow-only semantic candidate extractor for photo edits.
Return exactly one JSON object and no markdown.
Allowed axes: {axes}.
Allowed regions: {regions}.
Use 1 to 3 operations. Never invent an operation that is not explicitly
requested. Do not output engine parameters or final render values.

Each operation may contain only:
axis, kind, direction, strength, numeric, reset, region, evidence.
Allowed kinds: explicit_axis, macro, observation, relative_numeric, absolute,
reset. Direction is -1 or 1. Strength is subtle, normal, or strong.
Evidence is an object keyed by axis, direction, strength, numeric,
numeric_relation, reset, or region. Every evidence value must contain exact
zero-based start/end offsets and raw_text copied exactly from the user prompt.
Optional evidence fields are language and confidence.

Example shape:
{{"operations":[{{"axis":"saturation","kind":"macro","direction":1,
"region":"background","evidence":{{"axis":{{"start":25,"end":34,
"raw_text":"saturated"}},"direction":{{"start":20,"end":24,
"raw_text":"more"}},"region":{{"start":9,"end":19,
"raw_text":"background"}}}}}}],"confidence":0.9}}

User prompt:
{user_prompt}
""".strip()


def _log_observation(observation: SemanticShadowObservation) -> None:
    logger.info(
        "semantic_shadow=%s",
        json.dumps(
            observation.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


__all__ = [
    "SEMANTIC_SHADOW_ENV",
    "SEMANTIC_SHADOW_VERSION",
    "SemanticShadowObservation",
    "observe_grounded_semantic_shadow",
    "semantic_shadow_is_enabled",
]
