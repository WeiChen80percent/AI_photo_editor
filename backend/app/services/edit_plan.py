from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.services.edit_schema import (
    require_region_mask_pair,
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
    resolved_region, resolved_mask_type = _resolve_mask_type(
        region,
        mask_type,
    )
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
    resolved_region, resolved_mask_type = _resolve_mask_type(
        region,
        mask_type,
    )
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
    resolved_region, resolved_mask_type = _resolve_mask_type(
        region,
        mask_type,
    )
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


def build_style_edit_plan(
    *,
    prompt: str,
    style_id: str,
    style_version: str,
    strength: float,
    recipe_hash: str,
    asset_hash: str,
    renderer_version: str,
) -> dict[str, Any]:
    return {
        "type": "style",
        "prompt": prompt,
        "edits": [],
        "preset_name": None,
        "raw_parameters": None,
        "region": "all",
        "mask_type": "none",
        "style_id": style_id,
        "style_version": style_version,
        "style_strength": strength,
        "style_recipe_hash": recipe_hash,
        "style_asset_hash": asset_hash,
        "style_renderer_version": renderer_version,
        "style_source_edit_id": None,
        "style_anchor_image_path": None,
    }


def _resolve_mask_type(
    region: str | None,
    mask_type: str | None,
) -> tuple[str, str]:
    return require_region_mask_pair(region, mask_type)
