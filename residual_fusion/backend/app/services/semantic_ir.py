from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable


_OPERATION_TYPES = frozenset({"relative", "absolute", "reset"})
_OPERATION_KIND_TO_TYPE = {
    "explicit_axis": "relative",
    "macro": "relative",
    "observation": "relative",
    "context_feedback": "relative",
    "group_feedback": "relative",
    "absolute": "absolute",
    "relative_numeric": "relative",
    "reset": "reset",
}
_DIRECTIONS = frozenset({-1, 1})
_STRENGTHS = frozenset({"subtle", "normal", "strong"})
_TERMINAL_INTENTS = frozenset({"global_reset", "satisfied"})
_DECISION_SOURCES = frozenset(
    {
        "deterministic",
        "semantic_registry",
        "grounded_llm",
        "legacy",
        "hybrid",
    }
)
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _non_empty(value: object, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _language_tuple(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        language = _non_empty(value, "language source").lower()
        if language not in normalized:
            normalized.append(language)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class RawSpanEvidence:
    """A grounded semantic decision tied to an exact slice of the raw prompt."""

    start: int
    end: int
    raw_text: str
    slot: str
    concept_id: str
    language: str = "und"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.start, bool) or not isinstance(self.start, int):
            raise TypeError("span start must be an integer")
        if isinstance(self.end, bool) or not isinstance(self.end, int):
            raise TypeError("span end must be an integer")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("span must satisfy 0 <= start < end")
        raw_text = str(self.raw_text)
        if len(raw_text) != self.end - self.start:
            raise ValueError("raw_text length must equal end - start")
        object.__setattr__(self, "raw_text", raw_text)
        object.__setattr__(self, "slot", _non_empty(self.slot, "slot"))
        object.__setattr__(
            self,
            "concept_id",
            _non_empty(self.concept_id, "concept_id"),
        )
        object.__setattr__(
            self,
            "language",
            _non_empty(self.language, "language").lower(),
        )
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be finite and between 0 and 1")
        object.__setattr__(self, "confidence", confidence)

    def matches(self, source_text: str) -> bool:
        return source_text[self.start : self.end] == self.raw_text

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "raw_text": self.raw_text,
            "slot": self.slot,
            "concept_id": self.concept_id,
            "language": self.language,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class UnresolvedSpan:
    """An exact prompt slice that prevented a fully grounded interpretation."""

    start: int
    end: int
    raw_text: str
    reason: str
    language: str = "und"

    def __post_init__(self) -> None:
        if isinstance(self.start, bool) or not isinstance(self.start, int):
            raise TypeError("span start must be an integer")
        if isinstance(self.end, bool) or not isinstance(self.end, int):
            raise TypeError("span end must be an integer")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("span must satisfy 0 <= start < end")
        raw_text = str(self.raw_text)
        if len(raw_text) != self.end - self.start:
            raise ValueError("raw_text length must equal end - start")
        object.__setattr__(self, "raw_text", raw_text)
        object.__setattr__(self, "reason", _non_empty(self.reason, "reason"))
        object.__setattr__(
            self,
            "language",
            _non_empty(self.language, "language").lower(),
        )

    def matches(self, source_text: str) -> bool:
        return source_text[self.start : self.end] == self.raw_text

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "raw_text": self.raw_text,
            "reason": self.reason,
            "language": self.language,
        }


@dataclass(frozen=True, slots=True)
class SemanticOperation:
    """Language-neutral edit operation assembled from grounded semantic slots."""

    axis_id: str
    operation_type: str = "relative"
    operation_kind: str = "explicit_axis"
    direction: int | None = None
    strength: str | None = "normal"
    value: float | None = None
    region: str = "all"
    target_group_intent: str | None = None
    evidence: tuple[RawSpanEvidence, ...] = ()
    language_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        axis_id = _non_empty(self.axis_id, "axis_id")
        if not _IDENTIFIER_PATTERN.fullmatch(axis_id):
            raise ValueError(
                f"axis_id must match {_IDENTIFIER_PATTERN.pattern}"
            )
        object.__setattr__(self, "axis_id", axis_id)
        operation_type = _non_empty(
            self.operation_type,
            "operation_type",
        ).lower()
        if operation_type not in _OPERATION_TYPES:
            raise ValueError(
                f"operation_type must be one of {sorted(_OPERATION_TYPES)}"
            )
        object.__setattr__(self, "operation_type", operation_type)
        operation_kind = _non_empty(
            self.operation_kind,
            "operation_kind",
        ).lower()
        expected_type = _OPERATION_KIND_TO_TYPE.get(operation_kind)
        if expected_type is None:
            raise ValueError(
                "operation_kind must be one of "
                f"{sorted(_OPERATION_KIND_TO_TYPE)}"
            )
        if operation_type != expected_type:
            raise ValueError(
                f"operation_kind {operation_kind!r} requires "
                f"operation_type {expected_type!r}"
            )
        object.__setattr__(self, "operation_kind", operation_kind)
        object.__setattr__(self, "region", _non_empty(self.region, "region"))
        if self.target_group_intent is not None:
            target_group_intent = _non_empty(
                self.target_group_intent,
                "target_group_intent",
            )
            if not _IDENTIFIER_PATTERN.fullmatch(target_group_intent):
                raise ValueError(
                    "target_group_intent must be a stable semantic identifier"
                )
            object.__setattr__(
                self,
                "target_group_intent",
                target_group_intent,
            )
        evidence = tuple(self.evidence)
        if not all(isinstance(item, RawSpanEvidence) for item in evidence):
            raise TypeError(
                "operation evidence must contain RawSpanEvidence objects"
            )
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(
            self,
            "language_sources",
            _language_tuple(self.language_sources),
        )

        direction = self.direction
        strength = self.strength
        value = self.value
        if operation_type == "relative":
            if direction not in _DIRECTIONS:
                raise ValueError("relative operations require direction -1 or 1")
            if operation_kind == "relative_numeric":
                if strength is not None:
                    raise ValueError(
                        "relative_numeric operations cannot carry a strength"
                    )
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(
                        "relative_numeric operations require a numeric delta"
                    )
                numeric_value = float(value)
                if not math.isfinite(numeric_value) or numeric_value == 0:
                    raise ValueError(
                        "relative_numeric delta must be finite and non-zero"
                    )
                if (numeric_value > 0) != (direction > 0):
                    raise ValueError(
                        "relative_numeric direction must match delta sign"
                    )
                object.__setattr__(self, "value", numeric_value)
            else:
                if strength not in _STRENGTHS:
                    raise ValueError(
                        f"relative strength must be one of {sorted(_STRENGTHS)}"
                    )
                if value is not None:
                    raise ValueError(
                        "non-numeric relative operations cannot carry a value"
                    )
        elif operation_type == "absolute":
            if direction is not None:
                raise ValueError("absolute operations cannot carry a direction")
            if strength is not None:
                raise ValueError("absolute operations cannot carry a strength")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("absolute operations require a numeric value")
            numeric_value = float(value)
            if not math.isfinite(numeric_value):
                raise ValueError("absolute operation value must be finite")
            object.__setattr__(self, "value", numeric_value)
        else:
            if direction is not None or value is not None:
                raise ValueError(
                    f"{operation_type} operations cannot carry direction/value"
                )
            if strength is not None:
                raise ValueError(
                    f"{operation_type} operations cannot carry a strength"
                )

    def as_dict(self) -> dict[str, Any]:
        return {
            "axis_id": self.axis_id,
            "operation_type": self.operation_type,
            "operation_kind": self.operation_kind,
            "direction": self.direction,
            "strength": self.strength,
            "value": self.value,
            "region": self.region,
            "target_group_intent": self.target_group_intent,
            "evidence": [item.as_dict() for item in self.evidence],
            "language_sources": list(self.language_sources),
        }


@dataclass(frozen=True, slots=True)
class SemanticIR:
    """Immutable, language-neutral semantic interpretation of one raw prompt."""

    raw_prompt: str
    operations: tuple[SemanticOperation, ...]
    region: str
    language_sources: tuple[str, ...]
    decision_source: str
    terminal_intent: str | None = None
    evidence: tuple[RawSpanEvidence, ...] = ()
    unresolved_spans: tuple[UnresolvedSpan, ...] = ()
    normalized_prompt: str | None = None
    parser_version: str = "semantic_ir_v1"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        raw_prompt = str(self.raw_prompt)
        if not raw_prompt.strip():
            raise ValueError("raw_prompt must not be empty")
        object.__setattr__(self, "raw_prompt", raw_prompt)
        operations = tuple(self.operations)
        if not all(
            isinstance(operation, SemanticOperation)
            for operation in operations
        ):
            raise TypeError(
                "operations must contain SemanticOperation objects"
            )
        object.__setattr__(self, "operations", operations)
        object.__setattr__(self, "region", _non_empty(self.region, "region"))
        object.__setattr__(
            self,
            "language_sources",
            _language_tuple(self.language_sources),
        )
        decision_source = _non_empty(
            self.decision_source,
            "decision_source",
        ).lower()
        if decision_source not in _DECISION_SOURCES:
            raise ValueError(
                f"decision_source must be one of {sorted(_DECISION_SOURCES)}"
            )
        object.__setattr__(self, "decision_source", decision_source)
        terminal_intent = (
            _non_empty(self.terminal_intent, "terminal_intent").lower()
            if self.terminal_intent is not None
            else None
        )
        if (
            terminal_intent is not None
            and terminal_intent not in _TERMINAL_INTENTS
        ):
            raise ValueError(
                "terminal_intent must be one of "
                f"{sorted(_TERMINAL_INTENTS)}"
            )
        object.__setattr__(self, "terminal_intent", terminal_intent)
        evidence = tuple(self.evidence)
        if not all(isinstance(item, RawSpanEvidence) for item in evidence):
            raise TypeError("evidence must contain RawSpanEvidence objects")
        object.__setattr__(self, "evidence", evidence)
        unresolved_spans = tuple(self.unresolved_spans)
        if not all(
            isinstance(item, UnresolvedSpan) for item in unresolved_spans
        ):
            raise TypeError(
                "unresolved_spans must contain UnresolvedSpan objects"
            )
        object.__setattr__(
            self,
            "unresolved_spans",
            unresolved_spans,
        )
        if self.normalized_prompt is not None:
            object.__setattr__(
                self,
                "normalized_prompt",
                str(self.normalized_prompt),
            )
        object.__setattr__(
            self,
            "parser_version",
            _non_empty(self.parser_version, "parser_version"),
        )
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be finite and between 0 and 1")
        object.__setattr__(self, "confidence", confidence)

        grounded_spans = list(self.evidence)
        for operation in self.operations:
            grounded_spans.extend(operation.evidence)
        for span in grounded_spans:
            if not span.matches(raw_prompt):
                raise ValueError(
                    "evidence raw_text must match its raw_prompt slice"
                )
        for span in self.unresolved_spans:
            if not span.matches(raw_prompt):
                raise ValueError(
                    "unresolved raw_text must match its raw_prompt slice"
                )

    @property
    def is_fully_resolved(self) -> bool:
        has_operations = bool(self.operations)
        has_terminal = self.terminal_intent is not None
        return has_operations != has_terminal and not self.unresolved_spans

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "raw_prompt": self.raw_prompt,
            "normalized_prompt": self.normalized_prompt,
            "operations": [operation.as_dict() for operation in self.operations],
            "region": self.region,
            "language_sources": list(self.language_sources),
            "decision_source": self.decision_source,
            "evidence": [item.as_dict() for item in self.evidence],
            "unresolved_spans": [
                item.as_dict() for item in self.unresolved_spans
            ],
            "parser_version": self.parser_version,
            "confidence": self.confidence,
            "is_fully_resolved": self.is_fully_resolved,
        }
        if self.terminal_intent is not None:
            payload["terminal_intent"] = self.terminal_intent
        return payload
