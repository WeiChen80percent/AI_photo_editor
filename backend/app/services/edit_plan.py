from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.services.edit_schema import (
    default_mask_type_for_region,
    validate_edit_mask_type,
    validate_edit_region,
)


EditIntentStrength = tuple[str, str]


def build_single_edit_plan(
    *,
    prompt: str,
    intent: str,
    strength: str,
    region: str | None = None,
    mask_type: str | None = None,
) -> dict[str, Any]:
    return build_compound_edit_plan(
        prompt=prompt,
        intent_strengths=[(intent, strength)],
        region=region,
        mask_type=mask_type,
    )


def build_compound_edit_plan(
    *,
    prompt: str,
    intent_strengths: Iterable[EditIntentStrength],
    region: str | None = None,
    mask_type: str | None = None,
) -> dict[str, Any]:
    edits = [
        {"intent": intent, "strength": strength}
        for intent, strength in intent_strengths
    ]
    resolved_region = validate_edit_region(region)
    resolved_mask_type = _resolve_mask_type(resolved_region, mask_type)
    return {
        "type": "edits",
        "prompt": prompt,
        "edits": edits,
        "preset_name": None,
        "raw_parameters": None,
        "region": resolved_region,
        "mask_type": resolved_mask_type,
    }


def build_preset_edit_plan(
    *,
    prompt: str,
    preset_name: str,
    region: str | None = None,
    mask_type: str | None = None,
) -> dict[str, Any]:
    resolved_region = validate_edit_region(region)
    resolved_mask_type = _resolve_mask_type(resolved_region, mask_type)
    return {
        "type": "preset",
        "prompt": prompt,
        "edits": [],
        "preset_name": preset_name,
        "raw_parameters": None,
        "region": resolved_region,
        "mask_type": resolved_mask_type,
    }


def build_raw_parameter_edit_plan(
    *,
    prompt: str,
    parameters: Mapping[str, Any] | None,
    region: str | None = None,
    mask_type: str | None = None,
) -> dict[str, Any]:
    resolved_region = validate_edit_region(region)
    resolved_mask_type = _resolve_mask_type(resolved_region, mask_type)
    return {
        "type": "raw_parameters",
        "prompt": prompt,
        "edits": [],
        "preset_name": None,
        "raw_parameters": dict(parameters or {}),
        "region": resolved_region,
        "mask_type": resolved_mask_type,
    }


def build_reference_edit_plan() -> dict[str, Any]:
    return {
        "type": "reference",
        "prompt": "",
        "edits": [],
        "preset_name": None,
        "raw_parameters": None,
        "region": "all",
        "mask_type": "none",
    }


def _resolve_mask_type(region: str, mask_type: str | None) -> str:
    resolved_mask_type = validate_edit_mask_type(mask_type)
    if resolved_mask_type != "none":
        return resolved_mask_type
    return default_mask_type_for_region(region)
