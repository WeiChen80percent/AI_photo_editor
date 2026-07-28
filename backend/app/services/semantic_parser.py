"""Public deterministic pipeline for registry-driven prompt semantics.

This module is intentionally small: language normalization, lexical matching,
scope resolution, and operation assembly remain separate testable layers.  The
facade adds only a migration disposition so the production compiler can
distinguish a successful semantic parse from an authoritative safety rejection
and a deliberately unsupported legacy-only intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.semantic_ir import SemanticIR
from app.services.semantic_normalizer import (
    NormalizedText,
    normalize_semantic_text,
)
from app.services.semantic_operation_assembler import (
    SemanticAssemblyError,
    assemble_semantic_ir,
)
from app.services.semantic_registry import (
    DEFAULT_PARAMETER_REGISTRY,
    ParameterRegistry,
)
from app.services.semantic_scope_resolver import (
    SemanticScopeResolution,
    resolve_semantic_scope,
)
from app.services.semantic_slot_extractor import (
    SlotExtraction,
    extract_semantic_slots,
)


SEMANTIC_PARSER_VERSION = "semantic_parser_v1"

SemanticDisposition = Literal["accepted", "legacy_fallback", "rejected"]

_LEGACY_ONLY_CODES = frozenset(
    {
        "assembler_context_feedback_unsupported",
        "assembler_no_operation",
    }
)
_AUTHORITATIVE_CODES = frozenset(
    {
        "assembler_ambiguous_scope",
        "assembler_guard_rejected",
        "assembler_scope_rejected",
        "assembler_operation_limit_exceeded",
        "assembler_duplicate_axis",
        "assembler_unknown_axis",
        "assembler_unknown_region",
        "assembler_multiple_regions",
        "assembler_operation_conflict",
        "assembler_unsupported_operation",
        "assembler_numeric_out_of_range",
        "assembler_invalid_numeric",
        "assembler_validation_failed",
        "assembler_invalid_contract",
        "assembler_invalid_scope",
    }
)


@dataclass(frozen=True, slots=True)
class SemanticParseAttempt:
    raw_prompt: str
    normalized: NormalizedText
    extraction: SlotExtraction
    resolution: SemanticScopeResolution
    result: SemanticIR | SemanticAssemblyError
    disposition: SemanticDisposition
    parser_version: str = SEMANTIC_PARSER_VERSION

    @property
    def accepted_ir(self) -> SemanticIR | None:
        return self.result if isinstance(self.result, SemanticIR) else None

    @property
    def error(self) -> SemanticAssemblyError | None:
        return (
            self.result
            if isinstance(self.result, SemanticAssemblyError)
            else None
        )


def parse_semantic_prompt(
    raw_prompt: str,
    *,
    registry: ParameterRegistry = DEFAULT_PARAMETER_REGISTRY,
    engine: str = "opencv",
) -> SemanticParseAttempt:
    """Run the deterministic semantic pipeline exactly once."""

    normalized = normalize_semantic_text(raw_prompt)
    extraction = extract_semantic_slots(normalized, registry=registry)
    resolution = resolve_semantic_scope(extraction, registry=registry)
    result = assemble_semantic_ir(
        resolution,
        registry=registry,
        engine=engine,
    )
    disposition = _classify_result(result, extraction)
    return SemanticParseAttempt(
        raw_prompt=raw_prompt,
        normalized=normalized,
        extraction=extraction,
        resolution=resolution,
        result=result,
        disposition=disposition,
    )


def _classify_result(
    result: SemanticIR | SemanticAssemblyError,
    extraction: SlotExtraction,
) -> SemanticDisposition:
    if isinstance(result, SemanticIR):
        return "accepted"
    if _is_parent_dependent_feedback(result, extraction):
        # These prompts intentionally omit an axis because their referent is
        # the active operation on the selected history branch.  The stateless
        # semantic parser cannot resolve that parent, so the stateful legacy
        # controller remains the authoritative boundary during takeover.
        return "legacy_fallback"
    has_surface_action = any(
        not slot.is_ambiguous and slot.slot == "surface_action"
        for slot in extraction.slots
    )
    if result.code == "assembler_no_operation" and has_surface_action:
        return "rejected"
    if result.code in _LEGACY_ONLY_CODES:
        return "legacy_fallback"
    if result.code in _AUTHORITATIVE_CODES:
        return "rejected"
    if result.code == "assembler_unresolved_text":
        # A wholly unknown style/preset may still belong to the frozen legacy
        # path.  Once a concrete edit concept was found, residue must not be
        # allowed to disappear through fallback.
        has_edit_semantics = any(
            slot.namespace in {"axis", "region", "numeric"}
            or slot.slot
            in {
                "operation",
                "guard",
                "negation",
                "surface_action",
                "terminal",
            }
            for slot in extraction.slots
            if not slot.is_ambiguous
        )
        return "rejected" if has_edit_semantics else "legacy_fallback"
    if result.code == "assembler_unbound_semantic_slot":
        return "rejected"
    return "rejected"


def _is_parent_dependent_feedback(
    result: SemanticAssemblyError,
    extraction: SlotExtraction,
) -> bool:
    """Recognize typed, axis-free feedback that needs one parent operation.

    This is deliberately structural: it does not enumerate sentences and it
    cannot turn partially understood edits into fallback.  The downstream
    stateful controller still decides whether the selected branch actually
    supplies exactly one compatible active operation.
    """

    if result.code != "assembler_scope_rejected":
        return False
    scope_codes = {
        str(issue.get("scope_code", "")).strip()
        for issue in result.issues
        if isinstance(issue, dict)
    }
    if not scope_codes or not scope_codes.issubset(
        {"unresolved_return_relation", "dangling_function_word"}
    ):
        return False
    slots = tuple(
        slot for slot in extraction.slots if not slot.is_ambiguous
    )
    if len(slots) != len(extraction.slots) or extraction.residue_spans:
        return False
    if any(
        slot.namespace in {"axis", "region", "numeric"}
        or slot.slot
        in {
            "operation",
            "guard",
            "negation",
            "surface_action",
        }
        for slot in slots
    ):
        return False

    slot_names = {slot.slot for slot in slots}
    return_feedback = (
        "return_relation" in slot_names
        and (
            "direction" in slot_names
            or any(
                slot.slot == "generic_action"
                and slot.value == "return_negative"
                for slot in slots
            )
        )
    )
    observation_feedback = (
        "observation_modifier" in slot_names
        and "state_link" in slot_names
        and "function_word" in slot_names
        and any(
            slot.slot == "observation_modifier"
            and slot.value == "too_much"
            for slot in slots
        )
    )
    return return_feedback or observation_feedback


__all__ = [
    "SEMANTIC_PARSER_VERSION",
    "SemanticDisposition",
    "SemanticParseAttempt",
    "parse_semantic_prompt",
]
