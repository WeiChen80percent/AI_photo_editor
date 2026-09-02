from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from app.services.edit_schema import (
    EDIT_PARAMETER_SPECS,
    PUBLIC_PARAMETER_KEYS,
    require_region_mask_pair,
)
from app.services.photo_git_graph import PhotoGitGraph, VIRTUAL_ORIGINAL_ID
from app.services.photo_git_schema import PHOTO_GIT_SCHEMA_VERSION


class PhotoGitRecipeError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def empty_recipe(anchor_image_path: str) -> dict[str, Any]:
    if not anchor_image_path:
        raise PhotoGitRecipeError(
            "photo_git_anchor_missing",
            "Photo Git requires a reusable original image anchor.",
        )
    return build_recipe_from_contributions(
        anchor_image_path=anchor_image_path,
        contributions=[],
    )


def build_version_recipe(
    graph: PhotoGitGraph,
    edit_id: str,
) -> dict[str, Any]:
    lineage = graph.lineage(edit_id)
    anchor = graph.original_path(edit_id)
    replay_start = 0
    for index, record in enumerate(lineage):
        if not _is_style_anchor_boundary(record):
            continue
        anchor = str(record.get("result_image_path") or "")
        if not anchor:
            raise PhotoGitRecipeError(
                "photo_git_anchor_missing",
                "The style boundary has no reusable result image.",
            )
        replay_start = index + 1

    recipe = empty_recipe(anchor)
    for record in lineage[replay_start:]:
        stored = _stored_photo_git_recipe(record)
        if stored is not None:
            if str(stored.get("anchor_image_path") or "") != anchor:
                raise PhotoGitRecipeError(
                    "photo_git_anchor_mismatch",
                    "Stored Photo Git recipe uses a different original anchor.",
                )
            recipe = normalize_recipe(stored)
            continue
        changes = extract_record_contributions(record, recipe)
        if changes:
            recipe = build_recipe_from_contributions(
                anchor_image_path=anchor,
                contributions=[
                    *recipe.get("contributions", []),
                    *changes,
                ],
            )
    return recipe


def build_ancestor_recipe(
    graph: PhotoGitGraph,
    ancestor_edit_id: str,
    *,
    related_edit_id: str,
) -> dict[str, Any]:
    if ancestor_edit_id == VIRTUAL_ORIGINAL_ID:
        return empty_recipe(graph.original_path(related_edit_id))
    return build_version_recipe(graph, ancestor_edit_id)


def extract_record_contributions(
    record: Mapping[str, Any],
    parent_recipe: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mode = str(record.get("edit_mode") or "")
    plan_type = _record_plan_type(record)
    if (
        mode == "reference"
        or plan_type == "reference"
    ):
        raise PhotoGitRecipeError(
            "photo_git_version_unsupported",
            "Reference recipes cannot be decomposed in Photo Git v1.",
        )
    if _is_style_anchor_boundary(record):
        raise PhotoGitRecipeError(
            "photo_git_version_unsupported",
            "Style recipes can only be used as flattened Photo Git anchors.",
        )
    if mode not in {"prompt", "manual"}:
        raise PhotoGitRecipeError(
            "photo_git_version_unsupported",
            f"Photo Git v1 cannot decompose edit mode: {mode or 'unknown'}.",
        )

    region, mask_type = _record_scope(record)
    edit_id = str(record.get("edit_id") or "")
    if not edit_id:
        raise PhotoGitRecipeError(
            "photo_git_history_invalid",
            "A history record is missing its edit identifier.",
        )
    current = effective_contributions(parent_recipe)
    changes: list[dict[str, Any]] = []
    seen_axes: set[str] = set()
    adaptive = record.get("adaptive")

    if isinstance(adaptive, Mapping):
        operations = adaptive.get("operations")
        group_ids: set[str] = set()
        if isinstance(operations, list):
            for index, raw in enumerate(operations):
                if not isinstance(raw, Mapping):
                    continue
                axis = str(raw.get("axis") or "")
                before = _finite(raw.get("current_value"))
                after = _finite(raw.get("next_value"))
                if (
                    axis not in PUBLIC_PARAMETER_KEYS
                    or before is None
                    or after is None
                    or math.isclose(before, after, abs_tol=1e-10)
                ):
                    continue
                operation_region, operation_mask = require_region_mask_pair(
                    raw.get("region") or region,
                    raw.get("mask_type") or mask_type,
                )
                group_id = str(raw.get("group_id") or "")
                if group_id:
                    group_ids.add(group_id)
                changes.append(
                    _contribution(
                        record=record,
                        axis=axis,
                        region=operation_region,
                        mask_type=operation_mask,
                        before=before,
                        after=after,
                        role="primary",
                        source_operation_id=str(
                            raw.get("operation_id") or f"operation_{index}"
                        ),
                        source_group_id=group_id or None,
                    )
                )
                seen_axes.add(axis)

        ledger = adaptive.get("contribution_ledger")
        if isinstance(ledger, list) and group_ids:
            for index, raw in enumerate(ledger):
                if (
                    not isinstance(raw, Mapping)
                    or str(raw.get("group_id") or "") not in group_ids
                    or bool(raw.get("suppressed"))
                    or raw.get("applied") is False
                ):
                    continue
                axis = str(raw.get("axis") or "")
                role = str(raw.get("role") or "")
                if axis in seen_axes or axis not in PUBLIC_PARAMETER_KEYS:
                    continue
                before = _finite(raw.get("before_value"))
                after = _finite(
                    raw.get("merged_value")
                    if raw.get("merged_value") is not None
                    else raw.get("proposed_value")
                )
                if (
                    before is None
                    or after is None
                    or math.isclose(before, after, abs_tol=1e-10)
                ):
                    continue
                changes.append(
                    _contribution(
                        record=record,
                        axis=axis,
                        region=region,
                        mask_type=mask_type,
                        before=before,
                        after=after,
                        role=(
                            "companion"
                            if role in {"companion", "legacy_companion"}
                            else "primary"
                        ),
                        source_operation_id=str(
                            raw.get("contribution_id")
                            or f"ledger_{index}"
                        ),
                        source_group_id=str(raw.get("group_id") or "") or None,
                    )
                )
                seen_axes.add(axis)

    parameters = record.get("engine_parameters") or record.get("parameters")
    parameter_map = parameters if isinstance(parameters, Mapping) else {}
    overrides = record.get("parameter_overrides")
    if mode == "manual" and isinstance(overrides, Mapping):
        for index, (raw_axis, raw_value) in enumerate(overrides.items()):
            axis = str(raw_axis)
            after = _finite(raw_value)
            if axis not in PUBLIC_PARAMETER_KEYS or after is None:
                continue
            scope_key = make_scope_key(region, mask_type, axis)
            before = _effective_value(current.get(scope_key), axis)
            if math.isclose(before, after, abs_tol=1e-10):
                continue
            changes.append(
                _contribution(
                    record=record,
                    axis=axis,
                    region=region,
                    mask_type=mask_type,
                    before=before,
                    after=after,
                    role="manual",
                    source_operation_id=f"manual_{index}_{axis}",
                    source_group_id=None,
                )
            )
            seen_axes.add(axis)

    # The adaptive ledger is the preferred source, but a guarded parameter
    # delta completes legacy prompt records and any public companion axis that
    # older metadata did not expose. Values must still be finite and validated
    # against the public schema before the recipe can be rendered.
    for axis in PUBLIC_PARAMETER_KEYS:
        if axis in seen_axes:
            continue
        after = _finite(parameter_map.get(axis))
        if after is None:
            continue
        scope_key = make_scope_key(region, mask_type, axis)
        before = _effective_value(current.get(scope_key), axis)
        if math.isclose(before, after, abs_tol=1e-10):
            continue
        _validate_axis_value(axis, after)
        changes.append(
            _contribution(
                record=record,
                axis=axis,
                region=region,
                mask_type=mask_type,
                before=before,
                after=after,
                role="derived",
                source_operation_id=f"derived_{axis}",
                source_group_id=None,
            )
        )
        seen_axes.add(axis)

    return changes


def build_recipe_from_contributions(
    *,
    anchor_image_path: str,
    contributions: list[Mapping[str, Any]],
    operation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(contributions):
        contribution = normalize_contribution(raw, order=index)
        normalized.append(contribution)
    recipe: dict[str, Any] = {
        "schema_version": PHOTO_GIT_SCHEMA_VERSION,
        "anchor_image_path": anchor_image_path,
        "contributions": normalized,
        "layers": compile_layers(normalized),
    }
    if operation is not None:
        recipe["operation"] = copy.deepcopy(dict(operation))
    recipe["recipe_hash"] = recipe_hash(recipe)
    return recipe


def normalize_recipe(raw: Mapping[str, Any]) -> dict[str, Any]:
    if str(raw.get("schema_version") or "") != PHOTO_GIT_SCHEMA_VERSION:
        raise PhotoGitRecipeError(
            "photo_git_recipe_unsupported",
            "Stored Photo Git recipe uses an unsupported schema.",
        )
    anchor = str(raw.get("anchor_image_path") or "")
    contributions = raw.get("contributions")
    if not anchor or not isinstance(contributions, list):
        raise PhotoGitRecipeError(
            "photo_git_recipe_invalid",
            "Stored Photo Git recipe is incomplete.",
        )
    operation = raw.get("operation")
    return build_recipe_from_contributions(
        anchor_image_path=anchor,
        contributions=[
            item for item in contributions if isinstance(item, Mapping)
        ],
        operation=operation if isinstance(operation, Mapping) else None,
    )


def compile_layers(
    contributions: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    layers: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for contribution in contributions:
        region = str(contribution["region"])
        mask_type = str(contribution["mask_type"])
        axis = str(contribution["parameter"])
        scope_id = make_scope_id(region, mask_type)
        layer = layers.get(scope_id)
        if layer is None:
            layer = {
                "scope_id": scope_id,
                "region": region,
                "mask_type": mask_type,
                "parameters": {
                    key: float(spec["neutral"])
                    for key, spec in EDIT_PARAMETER_SPECS.items()
                    if key in PUBLIC_PARAMETER_KEYS
                },
                "provenance": {},
            }
            layers[scope_id] = layer
        layer["parameters"][axis] = float(contribution["after_value"])
        layer["provenance"][axis] = {
            "contribution_id": contribution["contribution_id"],
            "source_edit_id": contribution["source_edit_id"],
            "source_operation_id": contribution["source_operation_id"],
            "role": contribution["role"],
        }

    result: list[dict[str, Any]] = []
    for layer in layers.values():
        if all(
            math.isclose(
                float(layer["parameters"][axis]),
                float(EDIT_PARAMETER_SPECS[axis]["neutral"]),
                abs_tol=1e-10,
            )
            for axis in PUBLIC_PARAMETER_KEYS
        ):
            continue
        result.append(copy.deepcopy(layer))
    return result


def effective_contributions(
    recipe: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    contributions = recipe.get("contributions")
    if not isinstance(contributions, list):
        return result
    for raw in contributions:
        if not isinstance(raw, Mapping):
            continue
        contribution = dict(raw)
        key = make_scope_key(
            str(contribution.get("region") or ""),
            str(contribution.get("mask_type") or ""),
            str(contribution.get("parameter") or ""),
        )
        result[key] = contribution
    return result


def make_scope_id(region: str, mask_type: str) -> str:
    return f"{region}|{mask_type}"


def make_scope_key(region: str, mask_type: str, parameter: str) -> str:
    return f"{make_scope_id(region, mask_type)}|{parameter}"


def recipe_hash(recipe: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in recipe.items()
        if key != "recipe_hash"
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stored_photo_git_recipe(
    record: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    metadata = record.get("photo_git")
    if not isinstance(metadata, Mapping):
        return None
    recipe = metadata.get("recipe")
    return recipe if isinstance(recipe, Mapping) else None


def _record_plan_type(record: Mapping[str, Any]) -> str:
    plan = record.get("edit_plan")
    return (
        str(plan.get("type") or "")
        if isinstance(plan, Mapping)
        else ""
    )


def _is_style_anchor_boundary(record: Mapping[str, Any]) -> bool:
    return (
        _record_plan_type(record) == "style"
        or str(record.get("resolved_intent") or "") == "apply_style"
        or str(record.get("edit_mode") or "") == "auto_model"
        or (
            isinstance(record.get("visual_anchor"), Mapping)
            and str(record["visual_anchor"].get("kind") or "")
            == "auto_model"
        )
    )


def _record_scope(record: Mapping[str, Any]) -> tuple[str, str]:
    adaptive = record.get("adaptive")
    parameters = record.get("engine_parameters") or record.get("parameters")
    plan = record.get("edit_plan")
    region = (
        adaptive.get("region")
        if isinstance(adaptive, Mapping)
        else None
    ) or (
        parameters.get("region")
        if isinstance(parameters, Mapping)
        else None
    ) or (
        plan.get("region")
        if isinstance(plan, Mapping)
        else None
    )
    mask_type = (
        adaptive.get("mask_type")
        if isinstance(adaptive, Mapping)
        else None
    ) or (
        parameters.get("mask_type")
        if isinstance(parameters, Mapping)
        else None
    ) or (
        plan.get("mask_type")
        if isinstance(plan, Mapping)
        else None
    )
    try:
        return require_region_mask_pair(region, mask_type)
    except ValueError as exc:
        raise PhotoGitRecipeError(
            "photo_git_scope_invalid",
            "A history record has an invalid region and mask contract.",
        ) from exc


def _contribution(
    *,
    record: Mapping[str, Any],
    axis: str,
    region: str,
    mask_type: str,
    before: float,
    after: float,
    role: str,
    source_operation_id: str,
    source_group_id: str | None,
) -> dict[str, Any]:
    _validate_axis_value(axis, before)
    _validate_axis_value(axis, after)
    source_edit_id = str(record.get("edit_id") or "")
    seed = (
        f"{source_edit_id}|{source_operation_id}|{region}|"
        f"{mask_type}|{axis}|{before:.12g}|{after:.12g}"
    )
    contribution_id = "pgc_" + hashlib.sha256(
        seed.encode("utf-8")
    ).hexdigest()[:24]
    return {
        "contribution_id": contribution_id,
        "scope_key": make_scope_key(region, mask_type, axis),
        "source_edit_id": source_edit_id,
        "source_parent_edit_id": record.get("parent_edit_id"),
        "source_operation_id": source_operation_id,
        "source_group_id": source_group_id,
        "region": region,
        "mask_type": mask_type,
        "parameter": axis,
        "before_value": float(before),
        "after_value": float(after),
        "role": role,
        "user_prompt": str(record.get("user_prompt") or ""),
        "order": 0,
    }


def normalize_contribution(
    raw: Mapping[str, Any],
    *,
    order: int,
) -> dict[str, Any]:
    axis = str(raw.get("parameter") or "")
    if axis not in PUBLIC_PARAMETER_KEYS:
        raise PhotoGitRecipeError(
            "photo_git_parameter_unsupported",
            f"Unsupported Photo Git parameter: {axis or 'missing'}",
        )
    try:
        region, mask_type = require_region_mask_pair(
            raw.get("region"),
            raw.get("mask_type"),
        )
    except ValueError as exc:
        raise PhotoGitRecipeError(
            "photo_git_scope_invalid",
            "Photo Git contribution has an invalid region and mask.",
        ) from exc
    before = _finite(raw.get("before_value"))
    after = _finite(raw.get("after_value"))
    if before is None or after is None:
        raise PhotoGitRecipeError(
            "photo_git_value_invalid",
            "Photo Git contribution contains a non-numeric value.",
        )
    _validate_axis_value(axis, before)
    _validate_axis_value(axis, after)
    contribution = copy.deepcopy(dict(raw))
    contribution.update(
        {
            "scope_key": make_scope_key(region, mask_type, axis),
            "region": region,
            "mask_type": mask_type,
            "parameter": axis,
            "before_value": float(before),
            "after_value": float(after),
            "order": order,
        }
    )
    for required in (
        "contribution_id",
        "source_edit_id",
        "source_operation_id",
        "role",
    ):
        if not str(contribution.get(required) or ""):
            raise PhotoGitRecipeError(
                "photo_git_provenance_missing",
                f"Photo Git contribution is missing {required}.",
            )
    return contribution


def _effective_value(
    contribution: Mapping[str, Any] | None,
    axis: str,
) -> float:
    if contribution is None:
        return float(EDIT_PARAMETER_SPECS[axis]["neutral"])
    value = _finite(contribution.get("after_value"))
    if value is None:
        raise PhotoGitRecipeError(
            "photo_git_value_invalid",
            "Photo Git effective value is invalid.",
        )
    return value


def _validate_axis_value(axis: str, value: float) -> None:
    spec = EDIT_PARAMETER_SPECS.get(axis)
    if spec is None or axis not in PUBLIC_PARAMETER_KEYS:
        raise PhotoGitRecipeError(
            "photo_git_parameter_unsupported",
            f"Unsupported Photo Git parameter: {axis}",
        )
    minimum = float(spec["minimum"])
    maximum = float(spec["maximum"])
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise PhotoGitRecipeError(
            "photo_git_value_out_of_range",
            f"{axis} is outside its supported range.",
        )


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
