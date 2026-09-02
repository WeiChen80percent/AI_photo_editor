from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from app.services.edit_schema import EDIT_PARAMETER_SPECS
from app.services.photo_git_graph import PhotoGitGraph
from app.services.photo_git_recipe import (
    build_ancestor_recipe,
    build_recipe_from_contributions,
    build_version_recipe,
    effective_contributions,
    make_scope_key,
)
from app.services.photo_git_resolver import (
    resolve_selectors,
    selector_matches,
)
from app.services.photo_git_schema import (
    PHOTO_GIT_RENDERER_VERSION,
    PHOTO_GIT_SCHEMA_VERSION,
    PhotoGitPlanRequest,
)


class PhotoGitPlanningError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def build_photo_git_plan(
    graph: PhotoGitGraph,
    request: PhotoGitPlanRequest,
) -> dict[str, Any]:
    selectors = resolve_selectors(
        instruction=request.instruction,
        selectors=request.selectors,
    )
    if request.operation == "merge":
        plan = _plan_merge(graph, request, selectors)
    else:
        plan = _plan_selective_revert(graph, request, selectors)
    plan["schema_version"] = PHOTO_GIT_SCHEMA_VERSION
    plan["renderer_version"] = PHOTO_GIT_RENDERER_VERSION
    plan["instruction"] = request.instruction.strip()
    plan["selectors"] = selectors
    plan["resolutions"] = dict(request.resolutions)
    plan["plan_hash"] = _plan_hash(plan)
    return plan


def _plan_merge(
    graph: PhotoGitGraph,
    request: PhotoGitPlanRequest,
    selectors: list[dict[str, Any]],
) -> dict[str, Any]:
    source_edit_id = str(request.source_edit_id or "")
    if not source_edit_id:
        raise PhotoGitPlanningError(
            "photo_git_source_required",
            "合併版本時必須選擇 source。",
        )
    if request.revert_edit_id is not None:
        raise PhotoGitPlanningError(
            "photo_git_request_invalid",
            "合併版本不可同時指定 revert_edit_id.",
            status_code=400,
        )
    if source_edit_id == request.target_edit_id:
        raise PhotoGitPlanningError(
            "photo_git_source_equals_target",
            "Source 與 target 不可為同一版本。",
        )

    ancestor_id = graph.common_ancestor(
        request.target_edit_id,
        source_edit_id,
    )
    ancestor_recipe = build_ancestor_recipe(
        graph,
        ancestor_id,
        related_edit_id=request.target_edit_id,
    )
    target_recipe = build_version_recipe(graph, request.target_edit_id)
    source_recipe = build_version_recipe(graph, source_edit_id)
    _require_same_anchor(ancestor_recipe, target_recipe, source_recipe)

    ancestor = effective_contributions(ancestor_recipe)
    target = effective_contributions(target_recipe)
    source = effective_contributions(source_recipe)
    source_diff = {
        key: value
        for key, value in source.items()
        if not _same_effective(value, ancestor.get(key))
        and any(selector_matches(selector, value) for selector in selectors)
    }
    if not source_diff:
        return _base_plan(
            status="no_change",
            operation="merge",
            target_edit_id=request.target_edit_id,
            source_edit_id=source_edit_id,
            revert_edit_id=None,
            common_ancestor_edit_id=ancestor_id,
            target_recipe=target_recipe,
            recipe=target_recipe,
            applied=[],
            removed=[],
            conflicts=[],
            message="Source 在指定範圍內沒有可帶入的變更。",
        )

    target_diff = {
        key: value
        for key, value in target.items()
        if not _same_effective(value, ancestor.get(key))
    }
    conflicts: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    next_contributions = copy.deepcopy(
        list(target_recipe.get("contributions") or [])
    )
    unresolved = False

    for key in sorted(source_diff):
        source_value = source_diff[key]
        target_value = target.get(key)
        conflict = (
            key in target_diff
            and target_value is not None
            and not _same_effective(target_value, source_value)
        )
        conflict_id = f"merge:{key}"
        if conflict:
            choice = str(request.resolutions.get(conflict_id) or "")
            conflict_payload = _merge_conflict(
                conflict_id=conflict_id,
                ancestor=ancestor.get(key),
                target=target_value,
                source=source_value,
                resolved_choice=choice or None,
            )
            conflicts.append(conflict_payload)
            if not choice:
                unresolved = True
                continue
            if choice not in {"target", "source"}:
                raise PhotoGitPlanningError(
                    "photo_git_resolution_invalid",
                    f"不支援的衝突選擇：{choice}。",
                    status_code=400,
                )
            if choice == "target":
                continue

        if _same_effective(target_value, source_value):
            continue
        contribution = _merge_contribution(
            target=target_value,
            source=source_value,
            target_edit_id=request.target_edit_id,
        )
        next_contributions.append(contribution)
        applied.append(copy.deepcopy(contribution))

    if unresolved:
        recipe = target_recipe
        status = "conflict"
        message = "選取範圍內有衝突，請先逐項選擇保留 target 或採用 source。"
    else:
        recipe = build_recipe_from_contributions(
            anchor_image_path=str(target_recipe["anchor_image_path"]),
            contributions=next_contributions,
            operation={
                "operation": "merge",
                "target_edit_id": request.target_edit_id,
                "source_edit_ids": [source_edit_id],
                "common_ancestor_edit_id": ancestor_id,
            },
        )
        status = "ready" if applied else "no_change"
        message = (
            "版本合併計畫已就緒。"
            if applied
            else "選擇後的結果與 target 相同，不會建立重複版本。"
        )
    return _base_plan(
        status=status,
        operation="merge",
        target_edit_id=request.target_edit_id,
        source_edit_id=source_edit_id,
        revert_edit_id=None,
        common_ancestor_edit_id=ancestor_id,
        target_recipe=target_recipe,
        recipe=recipe,
        applied=applied,
        removed=[],
        conflicts=conflicts,
        message=message,
    )


def _plan_selective_revert(
    graph: PhotoGitGraph,
    request: PhotoGitPlanRequest,
    selectors: list[dict[str, Any]],
) -> dict[str, Any]:
    revert_edit_id = str(request.revert_edit_id or "")
    if not revert_edit_id:
        raise PhotoGitPlanningError(
            "photo_git_revert_version_required",
            "選擇性撤銷時必須選擇一個祖先步驟。",
        )
    if request.source_edit_id is not None:
        raise PhotoGitPlanningError(
            "photo_git_request_invalid",
            "選擇性撤銷不可同時指定 source_edit_id.",
            status_code=400,
        )
    if not graph.is_ancestor(revert_edit_id, request.target_edit_id):
        raise PhotoGitPlanningError(
            "photo_git_revert_not_ancestor",
            "只能撤銷 target 祖先鏈中的步驟。",
        )
    revert_record = graph.record(revert_edit_id)
    if str(revert_record.get("edit_mode") or "") not in {"prompt", "manual"}:
        raise PhotoGitPlanningError(
            "photo_git_version_unsupported",
            "Photo Git v1 只能選擇性撤銷 prompt 或 manual 步驟。",
        )

    target_recipe = build_version_recipe(graph, request.target_edit_id)
    contributions = copy.deepcopy(
        list(target_recipe.get("contributions") or [])
    )
    selected = [
        item
        for item in contributions
        if str(item.get("source_edit_id") or "") == revert_edit_id
        and any(selector_matches(selector, item) for selector in selectors)
    ]
    if not selected:
        return _base_plan(
            status="no_change",
            operation="selective_revert",
            target_edit_id=request.target_edit_id,
            source_edit_id=None,
            revert_edit_id=revert_edit_id,
            common_ancestor_edit_id=revert_record.get("parent_edit_id")
            or "original",
            target_recipe=target_recipe,
            recipe=target_recipe,
            applied=[],
            removed=[],
            conflicts=[],
            message="指定步驟在選取範圍內沒有可撤銷的 contribution。",
        )

    selected_ids = {
        str(item["contribution_id"]) for item in selected
    }
    selected_keys = {
        str(item["scope_key"]) for item in selected
    }
    index_by_id = {
        str(item["contribution_id"]): index
        for index, item in enumerate(contributions)
    }
    dependent_keys: dict[str, list[dict[str, Any]]] = {}
    for key in selected_keys:
        last_selected_index = max(
            index_by_id[str(item["contribution_id"])]
            for item in selected
            if item["scope_key"] == key
        )
        later = [
            item
            for index, item in enumerate(contributions)
            if index > last_selected_index
            and item.get("scope_key") == key
            and item.get("contribution_id") not in selected_ids
        ]
        if later:
            dependent_keys[key] = later

    conflicts: list[dict[str, Any]] = []
    unresolved = False
    remove_keys: set[str] = set(selected_keys)
    replay_keys: set[str] = set()
    for key, later in dependent_keys.items():
        conflict_id = f"dependency:{key}"
        choice = str(request.resolutions.get(conflict_id) or "")
        conflict = {
            "conflict_id": conflict_id,
            "type": "dependency",
            "scope_key": key,
            "region": selected[0]["region"]
            if len(selected_keys) == 1
            else key.split("|", 1)[0],
            "parameter": key.rsplit("|", 1)[-1],
            "later_edit_ids": list(
                dict.fromkeys(
                    str(item.get("source_edit_id") or "")
                    for item in later
                )
            ),
            "allowed_choices": ["target", "replay"],
            "resolved_choice": choice or None,
        }
        conflicts.append(conflict)
        if not choice:
            unresolved = True
            continue
        if choice == "target":
            remove_keys.discard(key)
        elif choice == "replay":
            replay_keys.add(key)
        else:
            raise PhotoGitPlanningError(
                "photo_git_resolution_invalid",
                f"不支援的相依衝突選擇：{choice}。",
                status_code=400,
            )

    if unresolved:
        return _base_plan(
            status="conflict",
            operation="selective_revert",
            target_edit_id=request.target_edit_id,
            source_edit_id=None,
            revert_edit_id=revert_edit_id,
            common_ancestor_edit_id=revert_record.get("parent_edit_id")
            or "original",
            target_recipe=target_recipe,
            recipe=target_recipe,
            applied=[],
            removed=selected,
            conflicts=conflicts,
            message="後續版本修改了相同參數，請選擇保留 target 或重播後續變更。",
        )

    next_contributions = _remove_and_replay(
        contributions,
        selected_ids=selected_ids,
        remove_keys=remove_keys,
        replay_keys=replay_keys,
    )
    removed = [
        item for item in selected if item["scope_key"] in remove_keys
    ]
    if not removed:
        status = "no_change"
        recipe = target_recipe
        message = "所有相依衝突都選擇保留 target，不會建立重複版本。"
    else:
        status = "ready"
        recipe = build_recipe_from_contributions(
            anchor_image_path=str(target_recipe["anchor_image_path"]),
            contributions=next_contributions,
            operation={
                "operation": "selective_revert",
                "target_edit_id": request.target_edit_id,
                "reverted_edit_id": revert_edit_id,
            },
        )
        message = "選擇性撤銷計畫已就緒。"
    return _base_plan(
        status=status,
        operation="selective_revert",
        target_edit_id=request.target_edit_id,
        source_edit_id=None,
        revert_edit_id=revert_edit_id,
        common_ancestor_edit_id=revert_record.get("parent_edit_id")
        or "original",
        target_recipe=target_recipe,
        recipe=recipe,
        applied=[],
        removed=removed,
        conflicts=conflicts,
        message=message,
    )


def _remove_and_replay(
    contributions: list[dict[str, Any]],
    *,
    selected_ids: set[str],
    remove_keys: set[str],
    replay_keys: set[str],
) -> list[dict[str, Any]]:
    current: dict[str, float] = {}
    result: list[dict[str, Any]] = []
    removal_seen: set[str] = set()
    for original in contributions:
        item = copy.deepcopy(original)
        key = str(item["scope_key"])
        axis = str(item["parameter"])
        if (
            str(item["contribution_id"]) in selected_ids
            and key in remove_keys
        ):
            removal_seen.add(key)
            continue
        if key in replay_keys and key in removal_seen:
            before = current.get(
                key,
                float(EDIT_PARAMETER_SPECS[axis]["neutral"]),
            )
            delta = float(item["after_value"]) - float(item["before_value"])
            after = _quantize(axis, before + delta)
            item["before_value"] = before
            item["after_value"] = after
            item["replayed_without"] = sorted(
                contribution_id
                for contribution_id in selected_ids
                if any(
                    selected["contribution_id"] == contribution_id
                    and selected["scope_key"] == key
                    for selected in contributions
                )
            )
        current[key] = float(item["after_value"])
        result.append(item)
    return result


def _merge_contribution(
    *,
    target: Mapping[str, Any] | None,
    source: Mapping[str, Any],
    target_edit_id: str,
) -> dict[str, Any]:
    before = (
        float(target["after_value"])
        if target is not None
        else float(
            EDIT_PARAMETER_SPECS[str(source["parameter"])]["neutral"]
        )
    )
    payload = {
        "source_edit_id": str(source["source_edit_id"]),
        "source_parent_edit_id": target_edit_id,
        "source_operation_id": str(source["source_operation_id"]),
        "source_group_id": source.get("source_group_id"),
        "region": source["region"],
        "mask_type": source["mask_type"],
        "parameter": source["parameter"],
        "before_value": before,
        "after_value": float(source["after_value"]),
        "role": "merge",
        "user_prompt": str(source.get("user_prompt") or ""),
    }
    seed = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["contribution_id"] = (
        "pgm_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    )
    payload["scope_key"] = make_scope_key(
        str(payload["region"]),
        str(payload["mask_type"]),
        str(payload["parameter"]),
    )
    payload["order"] = 0
    payload["merged_from_contribution_id"] = source["contribution_id"]
    return payload


def _merge_conflict(
    *,
    conflict_id: str,
    ancestor: Mapping[str, Any] | None,
    target: Mapping[str, Any],
    source: Mapping[str, Any],
    resolved_choice: str | None,
) -> dict[str, Any]:
    return {
        "conflict_id": conflict_id,
        "type": "merge",
        "scope_key": source["scope_key"],
        "region": source["region"],
        "mask_type": source["mask_type"],
        "parameter": source["parameter"],
        "ancestor_value": _effective_number(ancestor, str(source["parameter"])),
        "target_value": float(target["after_value"]),
        "source_value": float(source["after_value"]),
        "allowed_choices": ["target", "source"],
        "resolved_choice": resolved_choice,
    }


def _base_plan(
    *,
    status: str,
    operation: str,
    target_edit_id: str,
    source_edit_id: str | None,
    revert_edit_id: str | None,
    common_ancestor_edit_id: str,
    target_recipe: Mapping[str, Any],
    recipe: Mapping[str, Any],
    applied: list[Mapping[str, Any]],
    removed: list[Mapping[str, Any]],
    conflicts: list[Mapping[str, Any]],
    message: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "operation": operation,
        "target_edit_id": target_edit_id,
        "source_edit_id": source_edit_id,
        "source_edit_ids": [source_edit_id] if source_edit_id else [],
        "revert_edit_id": revert_edit_id,
        "common_ancestor_edit_id": common_ancestor_edit_id,
        "anchor_image_path": recipe["anchor_image_path"],
        "target_recipe_hash": target_recipe["recipe_hash"],
        "recipe": copy.deepcopy(dict(recipe)),
        "applied_contributions": copy.deepcopy(applied),
        "removed_contributions": copy.deepcopy(removed),
        "conflicts": copy.deepcopy(conflicts),
        "message": message,
    }


def _same_effective(
    left: Mapping[str, Any] | None,
    right: Mapping[str, Any] | None,
) -> bool:
    if left is None and right is None:
        return True
    sample = left or right
    if sample is None:
        return True
    axis = str(sample["parameter"])
    return math.isclose(
        _effective_number(left, axis),
        _effective_number(right, axis),
        abs_tol=float(EDIT_PARAMETER_SPECS[axis]["step"]) / 10,
    )


def _effective_number(
    contribution: Mapping[str, Any] | None,
    axis: str,
) -> float:
    if contribution is None:
        return float(EDIT_PARAMETER_SPECS[axis]["neutral"])
    return float(contribution["after_value"])


def _require_same_anchor(*recipes: Mapping[str, Any]) -> None:
    anchors = {
        str(recipe.get("anchor_image_path") or "")
        for recipe in recipes
    }
    if len(anchors) != 1 or not next(iter(anchors), ""):
        raise PhotoGitPlanningError(
            "photo_git_anchor_mismatch",
            "所選版本使用不相容的 visual anchor，無法安全重算或合併。",
        )


def _quantize(axis: str, value: float) -> float:
    spec = EDIT_PARAMETER_SPECS[axis]
    minimum = float(spec["minimum"])
    maximum = float(spec["maximum"])
    step = float(spec["step"])
    bounded = min(maximum, max(minimum, value))
    position = round((bounded - minimum) / step)
    quantized = minimum + position * step
    decimals = max(
        0,
        len(f"{step:.12f}".rstrip("0").split(".")[-1])
        if "." in f"{step:.12f}".rstrip("0")
        else 0,
    )
    return round(quantized, decimals)


def _plan_hash(plan: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in plan.items()
        if key != "plan_hash"
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
