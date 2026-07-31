"""Production transport for schema-grounded edit-contract candidates.

The provider only proposes typed semantic fields and exact source spans.  The
contract adapter remains authoritative and rejects invented evidence, unknown
capabilities, render values, measurements, and pass/fail claims.
"""

from __future__ import annotations

import json
import math
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any


GroundedContractProvider = Callable[[str, Mapping[str, Any]], object]


def get_default_grounded_contract_provider() -> GroundedContractProvider | None:
    enabled = os.getenv("AI_PHOTO_USE_LLM", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return None
    return call_ollama_grounded_contract_candidate


def call_ollama_grounded_contract_candidate(
    raw_prompt: str,
    candidate_schema: Mapping[str, Any],
) -> object:
    """Request one JSON candidate from the configured local Ollama endpoint."""

    prompt = str(raw_prompt)
    if not prompt.strip():
        raise ValueError("Grounded contract prompt must not be empty")
    if not isinstance(candidate_schema, Mapping):
        raise TypeError("Grounded contract candidate schema must be a mapping")

    url = os.getenv(
        "AI_PHOTO_OLLAMA_URL",
        "http://127.0.0.1:11434/api/generate",
    )
    model = os.getenv("AI_PHOTO_OLLAMA_MODEL", "llama3.2:3b")
    timeout = _positive_float_env("AI_PHOTO_OLLAMA_TIMEOUT", 8.0)
    num_predict = _positive_int_env("AI_PHOTO_OLLAMA_NUM_PREDICT", 768)
    payload = {
        "model": model,
        "prompt": _candidate_prompt(prompt, candidate_schema),
        "stream": False,
        "format": _ollama_response_schema(candidate_schema),
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except (socket.timeout, TimeoutError) as exc:
        raise TimeoutError("Grounded contract provider timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise TimeoutError("Grounded contract provider timed out") from exc
        raise
    if not isinstance(response_payload, Mapping):
        raise ValueError("Ollama response must be a JSON object")
    response_text = response_payload.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        raise ValueError("Ollama response did not contain a JSON candidate")
    candidate = json.loads(response_text)
    if not isinstance(candidate, Mapping):
        raise ValueError("Grounded contract candidate must be a JSON object")
    return _normalize_evidence_spans(prompt, dict(candidate))


def _candidate_prompt(
    raw_prompt: str,
    candidate_schema: Mapping[str, Any],
) -> str:
    schema_json = json.dumps(
        candidate_schema,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"""
You extract typed photo-edit operations and measurable hard constraints.
Return exactly one JSON object and no markdown. The response schema enforces
the allowed fields and identifiers. The object has this logical shape:

{{
  "operations": [
    {{
      "axis": "one allowed_axes id",
      "kind": "explicit_axis or macro or observation",
      "direction": -1 or 1,
      "strength": "subtle or normal or strong (omit if not explicit)",
      "region": "one allowed_regions id",
      "evidence": {{
        "axis": "exact words from USER_PROMPT",
        "direction": "exact words from USER_PROMPT",
        "strength": "exact words from USER_PROMPT",
        "region": "exact words from USER_PROMPT"
      }}
    }}
  ],
  "constraints": [
    {{
      "metric": "one metric_id",
      "subject_region": "a region supported by that metric",
      "profile": "one profile_id for a qualitative condition",
      "evidence": {{
        "metric": "exact words from USER_PROMPT",
        "subject_region": "exact words from USER_PROMPT",
        "profile": "exact words from USER_PROMPT",
        "clause": "the exact complete constraint clause"
      }}
    }}
  ],
  "confidence": 0.0
}}

For an explicit numeric constraint, replace "profile" with all three fields
"operator", "threshold", and "unit" and provide matching evidence entries
for operator, threshold, and unit. Use only <= for an explicit upper bound.
Copy the user's numeric value and input unit; deterministic validation will
normalize it. Omit subject_region and its evidence only when it is "all".
Omit strength and its evidence when strength is not explicitly stated.

Use only identifiers, profiles, units, and supported regions present in
CANDIDATE_SCHEMA. Every semantic field must cite exact evidence from
USER_PROMPT. Do not infer an unstated operation or constraint. Do not copy
identifiers into raw_text unless those exact identifier characters occur in
USER_PROMPT. Do not output engine parameters, render values, policy
thresholds, measurements, pass/fail, or applied scale. Do not copy this
template literally.

CANDIDATE_SCHEMA:
{schema_json}

USER_PROMPT:
{raw_prompt}
""".strip()


def _ollama_response_schema(
    candidate_schema: Mapping[str, Any],
) -> dict[str, Any]:
    axes = [str(item) for item in candidate_schema.get("allowed_axes", ())]
    regions = [str(item) for item in candidate_schema.get("allowed_regions", ())]
    metric_schema = candidate_schema.get("metric_schema")
    metrics = (
        metric_schema.get("metrics", ())
        if isinstance(metric_schema, Mapping)
        else ()
    )
    units = (
        metric_schema.get("units", ())
        if isinstance(metric_schema, Mapping)
        else ()
    )
    metric_ids = [
        str(item.get("metric_id"))
        for item in metrics
        if isinstance(item, Mapping) and item.get("metric_id")
    ]
    profile_ids = [
        str(profile.get("profile_id"))
        for metric in metrics
        if isinstance(metric, Mapping)
        for profile in metric.get("profiles", ())
        if isinstance(profile, Mapping) and profile.get("profile_id")
    ]
    unit_ids = list(
        dict.fromkeys(
            [
                str(item.get("unit_id"))
                for item in units
                if isinstance(item, Mapping) and item.get("unit_id")
            ]
            + [
                str(input_unit.get("unit_id"))
                for item in units
                if isinstance(item, Mapping)
                for input_unit in item.get("input_units", ())
                if isinstance(input_unit, Mapping) and input_unit.get("unit_id")
            ]
        )
    )
    evidence_schema = {
        "type": "object",
        "additionalProperties": {"type": "string", "minLength": 1},
    }
    operation_properties: dict[str, Any] = {
        "axis": {"type": "string", "enum": axes},
        "kind": {
            "type": "string",
            "enum": ["explicit_axis", "macro", "observation"],
        },
        "direction": {"type": "integer", "enum": [-1, 1]},
        "strength": {
            "type": "string",
            "enum": ["subtle", "normal", "strong"],
        },
        "region": {"type": "string", "enum": regions},
        "evidence": evidence_schema,
    }
    constraint_properties: dict[str, Any] = {
        "metric": {"type": "string", "enum": metric_ids},
        "subject_region": {"type": "string", "enum": regions},
        "profile": {"type": "string", "enum": profile_ids},
        "operator": {"type": "string", "enum": ["<="]},
        "threshold": {"type": "number"},
        "unit": {"type": "string", "enum": unit_ids},
        "evidence": evidence_schema,
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": int(candidate_schema.get("max_operations", 3)),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": operation_properties,
                    "required": ["axis", "kind", "direction", "evidence"],
                },
            },
            "constraints": {
                "type": "array",
                "minItems": 1,
                "maxItems": int(candidate_schema.get("max_constraints", 3)),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": constraint_properties,
                    "required": ["metric", "evidence"],
                },
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["operations", "constraints", "confidence"],
    }


def _normalize_evidence_spans(
    raw_prompt: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Convert exact evidence quotes into deterministic source spans.

    The model selects semantics and quotes source text; transport code only
    locates that quote. Ambiguous or invented quotes remain unnormalized and
    are rejected by the authoritative adapter.
    """

    for collection_name in ("operations", "constraints"):
        collection = candidate.get(collection_name)
        if not isinstance(collection, list):
            continue
        for item in collection:
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence")
            if not isinstance(evidence, dict):
                continue
            clause_bounds: tuple[int, int] | None = None
            clause = evidence.get("clause")
            if isinstance(clause, str):
                clause_bounds = _unique_quote_bounds(raw_prompt, clause)
                if clause_bounds is not None:
                    evidence["clause"] = _span_payload(
                        raw_prompt,
                        *clause_bounds,
                    )
            for field, value in tuple(evidence.items()):
                if isinstance(value, Mapping):
                    continue
                if not isinstance(value, str):
                    continue
                bounds = _unique_quote_bounds(
                    raw_prompt,
                    value,
                    within=clause_bounds,
                )
                if bounds is not None:
                    evidence[field] = _span_payload(raw_prompt, *bounds)
    return candidate


def _unique_quote_bounds(
    raw_prompt: str,
    quote: str,
    *,
    within: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    if not quote.strip():
        return None
    start_limit, end_limit = within or (0, len(raw_prompt))
    haystack = raw_prompt[start_limit:end_limit]
    positions: list[int] = []
    cursor = 0
    while True:
        found = haystack.find(quote, cursor)
        if found < 0:
            break
        positions.append(start_limit + found)
        cursor = found + 1
    if not positions:
        folded_haystack = haystack.casefold()
        folded_quote = quote.casefold()
        cursor = 0
        while True:
            found = folded_haystack.find(folded_quote, cursor)
            if found < 0:
                break
            positions.append(start_limit + found)
            cursor = found + 1
    if len(positions) != 1:
        return None
    start = positions[0]
    return start, start + len(quote)


def _span_payload(raw_prompt: str, start: int, end: int) -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "raw_text": raw_prompt[start:end],
    }


def _positive_float_env(name: str, fallback: float) -> float:
    value = float(os.getenv(name, str(fallback)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def _positive_int_env(name: str, fallback: int) -> int:
    value = int(os.getenv(name, str(fallback)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


__all__ = [
    "GroundedContractProvider",
    "call_ollama_grounded_contract_candidate",
    "get_default_grounded_contract_provider",
]
