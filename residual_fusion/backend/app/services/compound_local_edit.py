from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from app.services.adaptive_adjustment import (
    AdaptiveAdjustmentError,
    preflight_adaptive_semantic_prompt,
    resolve_adaptive_adjustment,
)
from app.services.semantic_normalizer import normalize_semantic_text
from app.services.semantic_slot_extractor import extract_semantic_slots


COMPOUND_LOCAL_SCHEMA_VERSION = "compound_local_prompt_v2"
_ALLOWED_REGIONS = frozenset({"sky", "person", "background"})
_EXPLICIT_SEPARATOR = re.compile(
    r"(?:\s+\+\s+|\s*(?:;|\uFF1B|[\r\n]+)\s*)"
)
_NATURAL_BOUNDARY = re.compile(
    r"(?:"
    r"[,\uFF0C\u3002\uFF01\uFF1F\u3001]+"
    r"|(?<![0-9])\.(?![0-9])|[!?]+"
    r"|\u4e26\u4e14|\u800c\u4e14|\u540c\u6642|\u53e6\u5916"
    r"|\u7136\u5f8c|\u63a5\u8457|\u518d\u4f86|\u9084\u6709|\u9084\u8981"
    r"|\u4ee5\u53ca|\u4e26|\u800c|\u518d|\u53c8|\u4e5f"
    r"|(?<![A-Za-z])(?:and|then|also|while|but)(?![A-Za-z])"
    r")",
    re.IGNORECASE,
)
_SHORT_BOUNDARY = re.compile(
    r"(?:\u548c|\u8ddf|\u8207)"
    r"(?=\s*(?:\u8acb|\u628a|\u8b93|\u5c07)?\s*$)"
)
_POST_REGION_PARTICLE = re.compile(
    r"^\s*(?:\u4e5f|\u5247|also)\s*",
    re.IGNORECASE,
)
_CLAUSE_EDGE_PUNCTUATION = " \t\r\n,\uFF0C\u3002.!\uFF01?\uFF1F\u3001;\uFF1B+"
_MAX_OPERATIONS = 4


class CompoundLocalEditError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        issues: list[dict[str, Any]] | None = None,
    ):
        self.code = code
        self.status_code = 422
        self.issues = copy.deepcopy(issues or [])
        super().__init__(message)


@dataclass(frozen=True)
class CompoundLocalResolution:
    prompt_result: dict[str, Any]
    adaptive: dict[str, Any]
    render_base_image_path: str
    explanation: str


@dataclass(frozen=True)
class CompoundPromptSplit:
    clauses: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class _RegionMention:
    region: str
    start: int
    end: int


def split_explicit_compound_prompt(prompt: str) -> tuple[str, ...]:
    text = str(prompt or "").strip()
    if not text or _EXPLICIT_SEPARATOR.search(text) is None:
        return ()
    clauses = tuple(part.strip() for part in _EXPLICIT_SEPARATOR.split(text))
    return _validate_split_clauses(clauses)


def split_compound_prompt(prompt: str) -> tuple[str, ...]:
    """Split explicit or conversational multi-region local instructions."""

    return _split_compound_prompt(prompt).clauses


def _split_compound_prompt(prompt: str) -> CompoundPromptSplit:
    text = str(prompt or "").strip()
    if not text:
        return CompoundPromptSplit((), "none")

    explicit = split_explicit_compound_prompt(text)
    if explicit:
        return CompoundPromptSplit(explicit, "explicit")

    mentions = _extract_region_mentions(text)
    if len(mentions) < 2 or len({item.region for item in mentions}) < 2:
        return CompoundPromptSplit((), "none")
    if len(mentions) > _MAX_OPERATIONS:
        raise CompoundLocalEditError(
            "compound_local_operation_count",
            "Compound local edit must contain two to four instructions.",
            issues=[{"operation_count": len(mentions)}],
        )

    clause_starts = [0]
    clause_ends: list[int] = []
    boundary_kinds: set[str] = set()
    for previous, current in zip(mentions, mentions[1:]):
        boundary_start, boundary_end, boundary_kind = _find_region_boundary(
            text,
            previous_end=previous.end,
            current_start=current.start,
        )
        clause_ends.append(boundary_start)
        clause_starts.append(boundary_end)
        boundary_kinds.add(boundary_kind)
    clause_ends.append(len(text))

    clauses = tuple(
        _clean_natural_clause(text[start:end])
        for start, end in zip(clause_starts, clause_ends)
    )
    source = (
        next(iter(boundary_kinds))
        if len(boundary_kinds) == 1
        else "mixed_natural"
    )
    return CompoundPromptSplit(_validate_split_clauses(clauses), source)


def _validate_split_clauses(clauses: tuple[str, ...]) -> tuple[str, ...]:
    if any(not clause for clause in clauses):
        raise CompoundLocalEditError(
            "compound_local_empty_clause",
            "Compound local edit contains an empty instruction.",
        )
    if not 2 <= len(clauses) <= _MAX_OPERATIONS:
        raise CompoundLocalEditError(
            "compound_local_operation_count",
            "Compound local edit must contain two to four instructions.",
            issues=[{"operation_count": len(clauses)}],
        )
    return clauses


def _extract_region_mentions(text: str) -> tuple[_RegionMention, ...]:
    extraction = extract_semantic_slots(normalize_semantic_text(text))
    mentions: list[_RegionMention] = []
    for slot in extraction.slots:
        interpretation = slot.interpretation
        if interpretation is None or interpretation.namespace != "region":
            continue
        region = str(interpretation.value)
        if region not in _ALLOWED_REGIONS:
            continue
        mentions.append(
            _RegionMention(
                region=region,
                start=slot.evidence.start,
                end=slot.evidence.end,
            )
        )
    return tuple(mentions)


def _find_region_boundary(
    text: str,
    *,
    previous_end: int,
    current_start: int,
) -> tuple[int, int, str]:
    between = text[previous_end:current_start]
    matches = (
        *tuple(_NATURAL_BOUNDARY.finditer(between)),
        *tuple(_SHORT_BOUNDARY.finditer(between)),
    )
    if not matches:
        return current_start, current_start, "region_transition"
    marker = max(matches, key=lambda item: (item.start(), item.end()))
    start = previous_end + marker.start()
    end = previous_end + marker.end()
    raw_marker = marker.group(0)
    kind = (
        "punctuation"
        if all(char in _CLAUSE_EDGE_PUNCTUATION for char in raw_marker)
        else "conjunction"
    )
    return start, end, kind


def _clean_natural_clause(clause: str) -> str:
    cleaned = clause.strip(_CLAUSE_EDGE_PUNCTUATION)
    mentions = _extract_region_mentions(cleaned)
    if len(mentions) != 1:
        return cleaned
    region_end = mentions[0].end
    suffix = _POST_REGION_PARTICLE.sub("", cleaned[region_end:], count=1)
    return cleaned[:region_end] + suffix


def try_resolve_compound_local_edit(
    *,
    prompt: str,
    parent_record: Mapping[str, Any] | None,
    default_base_image_path: str,
    engine_name: str,
) -> CompoundLocalResolution | None:
    split = _split_compound_prompt(prompt)
    clauses = split.clauses
    if not clauses:
        return None

    operations: list[dict[str, Any]] = []
    regions: list[str] = []
    render_bases: set[str] = set()
    for index, clause in enumerate(clauses):
        preflight = preflight_adaptive_semantic_prompt(
            prompt=clause,
            engine_name=engine_name,
        )
        attempt = preflight.semantic_attempt
        if (
            attempt is None
            or attempt.disposition != "accepted"
            or not preflight.bypass_intent_resolver
        ):
            raise CompoundLocalEditError(
                "compound_local_clause_rejected",
                f"Compound instruction {index + 1} could not be resolved safely.",
                issues=[
                    {
                        "index": index,
                        "clause": clause,
                        "disposition": getattr(attempt, "disposition", None),
                    }
                ],
            )
        try:
            resolved = resolve_adaptive_adjustment(
                prompt_result=preflight.prompt_result or {},
                prompt=clause,
                parent_record=None,
                default_base_image_path=default_base_image_path,
                engine_name=engine_name,
                semantic_attempt=attempt,
            )
        except AdaptiveAdjustmentError as exc:
            raise CompoundLocalEditError(
                "compound_local_clause_rejected",
                f"Compound instruction {index + 1} could not be resolved safely.",
                issues=[
                    {
                        "index": index,
                        "clause": clause,
                        "code": exc.code,
                        "message": str(exc),
                    }
                ],
            ) from exc

        edit_plan = resolved.prompt_result.get("edit_plan")
        if not isinstance(edit_plan, dict):
            raise CompoundLocalEditError(
                "compound_local_plan_missing",
                f"Compound instruction {index + 1} produced no edit plan.",
            )
        region = str(edit_plan.get("region") or "")
        if region not in _ALLOWED_REGIONS:
            raise CompoundLocalEditError(
                "compound_local_region_unsupported",
                "Compound mode currently supports only sky, person, and background.",
                issues=[{"index": index, "clause": clause, "region": region}],
            )
        regions.append(region)
        render_bases.add(str(resolved.render_base_image_path))
        operations.append(
            {
                "index": index,
                "clause": clause,
                "region": region,
                "mask_type": edit_plan.get("mask_type"),
                "edit_plan": copy.deepcopy(edit_plan),
            }
        )

    if len(set(regions)) != len(regions):
        raise CompoundLocalEditError(
            "compound_local_duplicate_region",
            "Each compound instruction must target a different local region.",
            issues=[{"regions": regions}],
        )
    if len(render_bases) != 1:
        raise CompoundLocalEditError(
            "compound_local_anchor_conflict",
            "Compound instructions resolved to different image anchors.",
            issues=[{"render_bases": sorted(render_bases)}],
        )

    adaptive = {
        "schema_version": COMPOUND_LOCAL_SCHEMA_VERSION,
        "applied": True,
        "operation_count": len(operations),
        "regions": regions,
        "segmentation_source": split.source,
        "operations": [
            {
                key: copy.deepcopy(item[key])
                for key in ("index", "clause", "region", "mask_type")
            }
            for item in operations
        ],
    }
    edit_plan = {
        "type": "compound_local",
        "prompt": str(prompt).strip(),
        "operations": operations,
        "region": "multiple",
        "mask_type": "multiple",
    }
    prompt_result = {
        "prompt": str(prompt).strip(),
        "resolved_intent": "compound_local",
        "preset_name": None,
        "edit_plan": edit_plan,
        "explanation": f"Resolved {len(operations)} atomic local instructions.",
        "parser_source": "semantic_registry_compound",
        "fallback_reason": None,
    }
    return CompoundLocalResolution(
        prompt_result=prompt_result,
        adaptive=adaptive,
        render_base_image_path=next(iter(render_bases)),
        explanation=f"Atomically rendered {len(operations)} local regions.",
    )
