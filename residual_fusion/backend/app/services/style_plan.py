from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.style_registry import (
    STYLE_RENDERER_VERSION,
    StyleDefinition,
    StyleVersionMismatchError,
    get_style_registry,
)


@dataclass(frozen=True, slots=True)
class ResolvedStylePlan:
    style: StyleDefinition
    strength: float


def resolve_style_plan(
    edit_plan: Mapping[str, Any],
) -> ResolvedStylePlan:
    if str(edit_plan.get("type") or "") != "style":
        raise ValueError("Edit plan is not a style plan")
    if (
        str(edit_plan.get("region") or "all") != "all"
        or str(edit_plan.get("mask_type") or "none") != "none"
    ):
        raise ValueError("Style renderer v1 only supports whole-image styles")

    style = get_style_registry().resolve(
        edit_plan.get("style_id"),
        edit_plan.get("style_version"),
    )
    expected = {
        "style_recipe_hash": style.recipe_hash,
        "style_asset_hash": style.asset_hash,
        "style_renderer_version": STYLE_RENDERER_VERSION,
    }
    mismatches = {
        field: {
            "plan": str(edit_plan.get(field) or ""),
            "catalog": catalog_value,
        }
        for field, catalog_value in expected.items()
        if str(edit_plan.get(field) or "") != catalog_value
    }
    if mismatches:
        raise StyleVersionMismatchError(
            f"Immutable style plan no longer matches {style.key}: {mismatches}"
        )
    strength = style.validate_strength(edit_plan.get("style_strength"))
    return ResolvedStylePlan(style=style, strength=strength)


__all__ = ["ResolvedStylePlan", "resolve_style_plan"]
