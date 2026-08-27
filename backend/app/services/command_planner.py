from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import time
import unicodedata
from collections.abc import Mapping
from typing import Any

from app.services.command_number_normalizer import (
    NormalizedNumber,
    find_number_spans,
    find_percentage,
    find_version_references,
)
from app.services.command_schema import (
    COMMAND_SCHEMA_VERSION,
    CommandPlanRequest,
    ResolvedCommandPlan,
)
from app.services.edit_history import (
    EditHistoryInvalidIdentifier,
    EditHistoryNotFound,
    EditHistoryStore,
)
from app.services.edit_schema import EDIT_PARAMETER_SPECS, MANUAL_PARAMETER_KEYS
from app.services.grounded_command_provider import GroundedCommandProvider
from app.services.photo_git_schema import PhotoGitPlanRequest, PhotoGitSelector
from app.services.photo_git_service import PhotoGitError, PhotoGitService
from app.services.style_registry import StyleCatalogError, get_style_registry
from app.services.style_selector import StyleSelectionError, try_resolve_style_prompt


_MERGE_PATTERN = re.compile(
    r"合併|合并|融合|合成|merge|combine|bring\s+together",
    re.IGNORECASE,
)
_REVERT_PATTERN = re.compile(
    r"撤回|撤銷|撤销|還原.*(?:步|版)|revert|undo",
    re.IGNORECASE,
)
_VERSION_HINT_PATTERN = re.compile(r"版本|第[^\s]{0,8}版|versions?\b", re.IGNORECASE)
_STYLE_HINT_PATTERN = re.compile(r"風格|style|look\b", re.IGNORECASE)
_RESET_PATTERNS = (
    re.compile(r"重設|重置|恢復預設|恢复默认"),
    re.compile(r"\b(?:reset|default)\b", re.IGNORECASE),
)
_DELTA_POSITIVE_PATTERNS = (
    re.compile(r"增加|提高|提升|加|調高|调高"),
    re.compile(r"\b(?:increase|raise|add|boost)\b", re.IGNORECASE),
    re.compile(r"(?<![\w.])\+(?=\s*[-+\d])"),
)
_DELTA_NEGATIVE_PATTERNS = (
    re.compile(r"降低|減少|减少|減|减"),
    re.compile(r"\b(?:decrease|lower|reduce|subtract)\b", re.IGNORECASE),
    re.compile(r"(?<![\w.])-(?=\s*\d)"),
)
_ABSOLUTE_PATTERNS = (
    re.compile(r"調到|调到|設成|设成|設定為|设置为|改成|調整到|调整到|改為|改为|調整成|调整成|調整為|调整为|調整|调整"),
    re.compile(r"\b(?:set|change)\b(?:\s+\w+){0,2}\s+\bto\b", re.IGNORECASE),
)

_PARAMETER_EXTRA_ALIASES: dict[str, tuple[str, ...]] = {
    "exposure": ("曝光值","曝光"),
    "brightness": ("明亮度", "亮度"),
    "contrast": ("對比度", "对比度"),
    "highlights": ("高光",),
    "shadows": ("陰影", "阴影"),
    "whites": ("白色色階", "白色阶"),
    "blacks": ("黑色色階", "黑色阶"),
    "saturation": ("色彩飽和度", "色彩饱和度"),
    "vibrance": ("鮮豔度", "鲜艳度", "自然饱和度"),
    "temperature": ("冷暖","色溫"),
    "white_balance_tint": ("白平衡色偏", "白平衡色调","白平衡"),
    "sharpen": ("銳利度", "锐化", "锐利度"),
    "clarity": ("清晰","清晰度"),
    "dehaze": ("除霧", "去雾", "除雾"),
    "vignette": ("暗角強度", "暗角强度","暗角"),
}


class CommandPlanCacheError(ValueError):
    pass


class CommandPlanner:
    def __init__(
        self,
        *,
        history_store: EditHistoryStore,
        photo_git_service: PhotoGitService,
        candidate_provider: GroundedCommandProvider | None = None,
    ):
        self.history_store = history_store
        self.photo_git_service = photo_git_service
        self.candidate_provider = candidate_provider
        self._plan_cache_lock = threading.Lock()
        self._plan_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._plan_cache_ttl_seconds = 20 * 60
        self._plan_cache_max_entries = 512

    def require_cached_plan(
        self,
        *,
        plan_hash: str,
        instruction: str,
        session_id: str | None,
        selected_edit_id: str | None,
        command_type: str,
    ) -> dict[str, Any]:
        normalized_hash = str(plan_hash or "").strip().lower()
        now = time.monotonic()
        with self._plan_cache_lock:
            self._prune_plan_cache_locked(now)
            cached = self._plan_cache.get(normalized_hash)
            if cached is None:
                raise CommandPlanCacheError(
                    "Command plan is missing or expired; plan the instruction again"
                )
            plan = copy.deepcopy(cached[1])
        normalized_session = str(session_id or "").strip() or None
        normalized_selected = str(selected_edit_id or "").strip() or None
        if (
            plan.get("original_instruction") != str(instruction or "").strip()
            or plan.get("session_id") != normalized_session
            or plan.get("selected_edit_id") != normalized_selected
            or plan.get("command_type") != command_type
            or plan.get("disposition") not in {"ready", "conflict"}
        ):
            raise CommandPlanCacheError(
                "Command plan no longer matches the requested action"
            )
        current_session = self._load_session(normalized_session)
        if plan.get("history_fingerprint") != _history_fingerprint(current_session):
            raise CommandPlanCacheError(
                "Command history changed; plan the instruction again"
            )
        return plan

    def plan(self, request: CommandPlanRequest) -> dict[str, Any]:
        instruction = request.instruction.strip()
        session_id = str(request.session_id or "").strip() or None
        selected_edit_id = str(request.selected_edit_id or "").strip() or None
        merge_signal = _MERGE_PATTERN.search(instruction) is not None
        revert_signal = _REVERT_PATTERN.search(instruction) is not None
        style_candidates = get_style_registry().prompt_alias_candidates(instruction)
        style_signal = bool(style_candidates)
        manual = self._parse_manual_surface(instruction)
        manual_signal = manual.get("axis") is not None and (
            manual.get("mode") is not None or manual.get("numbers")
        )
        manual_action_signal = manual_signal and (
            not (merge_signal or revert_signal)
            or manual.get("mode") is not None
        )

        command_signals = sum(
            bool(value)
            for value in (
                merge_signal or revert_signal,
                style_signal,
                manual_action_signal,
            )
        )
        if merge_signal and revert_signal:
            return self._unsupported(
                request,
                command_type="unknown",
                code="command_multiple_actions",
                zh="一次只能執行合併或撤回其中一種版本操作，請拆成兩句。",
                en="Run either merge or revert in one command, then send the other separately.",
            )
        if command_signals > 1:
            return self._unsupported(
                request,
                command_type="unknown",
                code="command_multiple_actions",
                zh="第一版一次只執行一種工具動作，請把這句拆開後分別套用。",
                en="P0 executes one tool action at a time. Split this request into separate commands.",
            )
        if merge_signal:
            return self._plan_merge(request, parser_source="command_rules")
        if revert_signal:
            return self._plan_revert(request, parser_source="command_rules")
        if style_signal:
            return self._plan_style(request, parser_source="command_rules")
        if manual_signal:
            return self._plan_manual(
                request,
                manual=manual,
                parser_source="command_rules",
            )

        llm_type = self._grounded_command_type(instruction)
        if llm_type == "photo_git_merge":
            return self._plan_merge(request, parser_source="command_llm")
        if llm_type == "photo_git_revert":
            return self._plan_revert(request, parser_source="command_llm")
        if llm_type == "apply_style":
            return self._plan_style(request, parser_source="command_llm")
        if llm_type == "manual_adjust":
            return self._plan_manual(
                request,
                manual=manual,
                parser_source="command_llm",
            )
        if (
            _VERSION_HINT_PATTERN.search(instruction)
            or _STYLE_HINT_PATTERN.search(instruction)
        ) and llm_type != "edit_prompt":
            return self._clarification(
                request,
                command_type="unknown",
                code="command_action_unclear",
                zh="我無法唯一判斷要使用哪一項工具，請明確說明要合併、撤回、套風格或調整參數。",
                en="I could not determine one tool action. Say whether to merge, revert, apply a style, or adjust a parameter.",
                parser_source="command_llm" if llm_type else "command_rules",
            )
        return self._ready_edit_prompt(
            request,
            parser_source="command_llm" if llm_type == "edit_prompt" else "command_rules",
        )

    def _plan_style(
        self,
        request: CommandPlanRequest,
        *,
        parser_source: str,
    ) -> dict[str, Any]:
        instruction = request.instruction.strip()
        try:
            result = try_resolve_style_prompt(instruction)
        except StyleSelectionError as exc:
            return self._clarification(
                request,
                command_type="apply_style",
                code=exc.code,
                zh=str(exc),
                en="Choose one catalog style and one strength, then apply it again.",
                parser_source=parser_source,
            )
        except StyleCatalogError as exc:
            return self._unsupported(
                request,
                command_type="apply_style",
                code=getattr(exc, "code", "style_catalog_invalid"),
                zh=str(exc),
                en="The requested style or strength is outside the approved catalog.",
                parser_source=parser_source,
            )
        if result is None:
            return self._clarification(
                request,
                command_type="apply_style",
                code="command_style_required",
                zh="請說出風格目錄中的一個風格名稱。",
                en="Name one style from the approved style catalog.",
                parser_source=parser_source,
            )
        edit_plan = result["edit_plan"]
        style_id = str(edit_plan["style_id"])
        style = get_style_registry().resolve(style_id, edit_plan.get("style_version"))
        style_span = _find_style_span(instruction, style)
        evidence = {"style": style_span} if style_span is not None else {}
        percentage = find_percentage(instruction)
        if percentage is not None:
            evidence["strength"] = _evidence(percentage, instruction)
        strength = float(edit_plan["style_strength"])
        summary = {
            "zh": f"套用「{style.display_name_zh}」，強度 {round(strength * 100)}%。",
            "en": f"Apply {style.display_name_en} at {round(strength * 100)}% strength.",
        }
        return self._finalize(
            request=request,
            disposition="ready",
            command_type="apply_style",
            normalized_slots={"style_id": style_id, "strength": strength},
            evidence=evidence,
            action={
                "prompt": instruction,
                "style_id": style_id,
                "style_version": edit_plan.get("style_version"),
                "strength": strength,
            },
            confirmation_policy="execute_after_apply",
            parser_source=parser_source,
            summary=summary,
        )

    def _plan_manual(
        self,
        request: CommandPlanRequest,
        *,
        manual: dict[str, Any],
        parser_source: str,
    ) -> dict[str, Any]:
        axis = manual.get("axis")
        if axis is None:
            return self._clarification(
                request,
                command_type="manual_adjust",
                code="command_parameter_required",
                zh="請指定一個手動參數，例如亮度、對比或飽和度。",
                en="Name one manual parameter, such as brightness, contrast, or saturation.",
                parser_source=parser_source,
            )
        if manual.get("multiple_axes"):
            return self._unsupported(
                request,
                command_type="manual_adjust",
                code="command_multiple_parameters",
                zh="精確手動調整 P0 一次只處理一個參數，請拆成兩句。",
                en="Exact manual adjustment handles one parameter per command in P0.",
                parser_source=parser_source,
            )
        mode = manual.get("mode")
        if mode is None:
            return self._clarification(
                request,
                command_type="manual_adjust",
                code="command_numeric_mode_required",
                zh="請說明是相對增加／減少，還是調到指定數值，例如「亮度加十」或「亮度調到十」。",
                en="Say whether to add/subtract or set an absolute value, for example 'increase brightness by ten' or 'set brightness to ten'.",
                parser_source=parser_source,
            )
        numbers: list[NormalizedNumber] = list(manual.get("numbers") or [])
        if mode != "reset" and len(numbers) != 1:
            return self._clarification(
                request,
                command_type="manual_adjust",
                code="command_numeric_value_required",
                zh="請為這個參數提供一個明確數值。",
                en="Provide exactly one numeric value for this parameter.",
                parser_source=parser_source,
            )
        if mode == "reset" and numbers:
            return self._clarification(
                request,
                command_type="manual_adjust",
                code="command_reset_value_conflict",
                zh="重設不需要數值；請移除數值或改成「調到」。",
                en="Reset does not take a value. Remove the number or use 'set to'.",
                parser_source=parser_source,
            )

        session, selected = self._selected_record(request)
        if session is None or selected is None:
            return self._clarification(
                request,
                command_type="manual_adjust",
                code="command_manual_source_required",
                zh="精確參數需要一個已完成且目前選中的 OpenCV 版本。",
                en="Exact parameters require a completed, currently selected OpenCV version.",
                parser_source=parser_source,
            )
        if str(selected.get("edit_mode") or "") not in {
            "prompt",
            "manual",
            "photo_git_merge",
            "photo_git_revert",
        }:
            return self._unsupported(
                request,
                command_type="manual_adjust",
                code="manual_source_mode_unsupported",
                zh="目前選中的版本不支援精確手動調整。",
                en="The selected version cannot be used for exact manual adjustment.",
                parser_source=parser_source,
            )
        spec = EDIT_PARAMETER_SPECS[str(axis)]
        source_parameters = selected.get("engine_parameters") or selected.get("parameters")
        current_raw = (
            source_parameters.get(axis)
            if isinstance(source_parameters, Mapping)
            else None
        )
        current = (
            float(current_raw)
            if isinstance(current_raw, (int, float)) and not isinstance(current_raw, bool)
            else float(spec["neutral"])
        )
        requested_value = None if mode == "reset" else float(numbers[0].value)
        if requested_value is not None and not math.isfinite(requested_value):
            return self._clarification(
                request,
                command_type="manual_adjust",
                code="command_numeric_value_invalid",
                zh="數值必須是有限數字。",
                en="The value must be a finite number.",
                parser_source=parser_source,
            )
        if mode == "delta_positive":
            if requested_value is not None and requested_value < 0:
                return self._clarification(
                    request,
                    command_type="manual_adjust",
                    code="command_numeric_direction_conflict",
                    zh="增加與負數互相矛盾，請改成正數或使用「減少」。",
                    en="Increase conflicts with a negative value. Use a positive value or say decrease.",
                    parser_source=parser_source,
                )
            target = current + abs(float(requested_value or 0))
            normalized_mode = "delta"
            delta = abs(float(requested_value or 0))
        elif mode == "delta_negative":
            target = current - abs(float(requested_value or 0))
            normalized_mode = "delta"
            delta = -abs(float(requested_value or 0))
        elif mode == "absolute":
            target = float(requested_value or 0)
            normalized_mode = "absolute"
            delta = target - current
        else:
            target = float(spec["neutral"])
            normalized_mode = "reset"
            delta = target - current
        minimum = float(spec["minimum"])
        maximum = float(spec["maximum"])
        if target < minimum or target > maximum:
            return self._clarification(
                request,
                command_type="manual_adjust",
                code="command_parameter_out_of_range",
                zh=f"{spec['label']}結果會超出合法範圍 {minimum:g}～{maximum:g}，請改用較小數值。",
                en=f"The resulting {spec['label_en']} would be outside {minimum:g} to {maximum:g}. Use a smaller value.",
                parser_source=parser_source,
            )
        target = _quantize(float(target), minimum, float(spec["step"]))
        if math.isclose(target, current, abs_tol=float(spec["step"]) / 10):
            return self._unsupported(
                request,
                command_type="manual_adjust",
                code="command_no_change",
                zh="這個精確調整不會改變目前參數，因此不建立重複版本。",
                en="This exact adjustment would not change the current value, so no duplicate version will be created.",
                parser_source=parser_source,
            )
        axis_evidence = manual.get("axis_evidence")
        mode_evidence = manual.get("mode_evidence")
        evidence = {
            key: value
            for key, value in {
                "axis": axis_evidence,
                "mode": mode_evidence,
                "value": _evidence(numbers[0], request.instruction)
                if numbers
                else mode_evidence,
            }.items()
            if value is not None
        }
        if normalized_mode == "reset":
            zh_summary = f"將{spec['label']}重設為 {target:g}。"
            en_summary = f"Reset {spec['label_en']} to {target:g}."
        elif normalized_mode == "absolute":
            zh_summary = f"將{spec['label']}從 {current:g} 調到 {target:g}。"
            en_summary = f"Set {spec['label_en']} from {current:g} to {target:g}."
        else:
            zh_summary = f"將{spec['label']}從 {current:g} 調整 {delta:+g}，結果為 {target:g}。"
            en_summary = f"Adjust {spec['label_en']} by {delta:+g}, from {current:g} to {target:g}."
        return self._finalize(
            request=request,
            disposition="ready",
            command_type="manual_adjust",
            target_edit_id=str(selected["edit_id"]),
            normalized_slots={
                "axis": axis,
                "mode": normalized_mode,
                "requested_value": requested_value,
                "current_value": current,
                "delta": delta,
                "target_value": target,
            },
            evidence=evidence,
            action={
                "source_edit_id": str(selected["edit_id"]),
                "parameter_overrides": {str(axis): target},
            },
            confirmation_policy="execute_after_apply",
            parser_source=parser_source,
            summary={"zh": zh_summary, "en": en_summary},
            session=session,
        )

    def _plan_merge(
        self,
        request: CommandPlanRequest,
        *,
        parser_source: str,
    ) -> dict[str, Any]:
        session = self._load_session(request.session_id)
        if session is None:
            return self._clarification(
                request,
                command_type="photo_git_merge",
                code="command_session_required",
                zh="版本合併需要目前 session 的歷史紀錄。",
                en="Version merge requires the current session history.",
                parser_source=parser_source,
            )
        references = find_version_references(request.instruction)
        indexes = _unique_version_indexes(references)
        if len(indexes) != 2:
            return self._clarification(
                request,
                command_type="photo_git_merge",
                code="command_merge_versions_required",
                zh="請明確指定兩個不同版本，例如「合併版本四和版本六」。",
                en="Name two different versions, for example 'merge version four and version six'.",
                parser_source=parser_source,
                session=session,
            )
        resolved = self._resolve_version_indexes(session, indexes)
        if resolved is None:
            return self._clarification(
                request,
                command_type="photo_git_merge",
                code="command_version_not_found",
                zh=f"目前歷史沒有完整包含版本 {indexes[0]} 與版本 {indexes[1]}。",
                en=f"The current history does not contain both version {indexes[0]} and version {indexes[1]}.",
                parser_source=parser_source,
                session=session,
            )
        first_id, second_id = resolved
        selected = str(request.selected_edit_id or "").strip()
        if selected not in {first_id, second_id}:
            edits = session.get("edits") or []
            options = []
            for version, edit_id in zip(indexes, resolved):
                options.append(
                    {
                        "option_id": f"target_version_{version}",
                        "label": {
                            "zh": f"以版本 {version} 作為目標",
                            "en": f"Use version {version} as target",
                        },
                        "action": {"select_target_edit_id": edit_id},
                    }
                )
            return self._clarification(
                request,
                command_type="photo_git_merge",
                code="command_merge_target_required",
                zh="合併需要一個 target。請選擇要保留哪一版作為主要版本。",
                en="Merge needs a target. Choose which version remains the primary version.",
                parser_source=parser_source,
                options=options,
                session=session,
            )
        target_id = selected
        source_id = second_id if target_id == first_id else first_id
        photo_request = PhotoGitPlanRequest(
            session_id=str(session["session_id"]),
            operation="merge",
            target_edit_id=target_id,
            source_edit_id=source_id,
            instruction=request.instruction.strip(),
            selectors=[PhotoGitSelector(all_contributions=True)],
        )
        return self._photo_git_plan(
            request=request,
            session=session,
            photo_request=photo_request,
            command_type="photo_git_merge",
            references=references,
            parser_source=parser_source,
        )

    def _plan_revert(
        self,
        request: CommandPlanRequest,
        *,
        parser_source: str,
    ) -> dict[str, Any]:
        session = self._load_session(request.session_id)
        if session is None:
            return self._clarification(
                request,
                command_type="photo_git_revert",
                code="command_session_required",
                zh="選擇性撤回需要目前 session 的歷史紀錄。",
                en="Selective revert requires the current session history.",
                parser_source=parser_source,
            )
        selected_id = str(request.selected_edit_id or "").strip()
        if not any(
            isinstance(item, Mapping) and item.get("edit_id") == selected_id
            for item in session.get("edits", [])
        ):
            return self._clarification(
                request,
                command_type="photo_git_revert",
                code="command_revert_target_required",
                zh="請先選中要保留並繼續修改的 target 版本。",
                en="Select the target version that should remain and receive the revert.",
                parser_source=parser_source,
                session=session,
            )
        references = find_version_references(request.instruction)
        indexes = _unique_version_indexes(references)
        if len(indexes) != 1:
            return self._clarification(
                request,
                command_type="photo_git_revert",
                code="command_revert_version_required",
                zh="請指定一個要撤回的祖先版本。",
                en="Name one ancestor version whose contribution should be reverted.",
                parser_source=parser_source,
                session=session,
            )
        resolved = self._resolve_version_indexes(session, indexes)
        if resolved is None:
            return self._clarification(
                request,
                command_type="photo_git_revert",
                code="command_version_not_found",
                zh=f"目前歷史中找不到版本 {indexes[0]}。",
                en=f"Version {indexes[0]} does not exist in the current history.",
                parser_source=parser_source,
                session=session,
            )
        photo_request = PhotoGitPlanRequest(
            session_id=str(session["session_id"]),
            operation="selective_revert",
            target_edit_id=selected_id,
            revert_edit_id=resolved[0],
            instruction=request.instruction.strip(),
        )
        return self._photo_git_plan(
            request=request,
            session=session,
            photo_request=photo_request,
            command_type="photo_git_revert",
            references=references,
            parser_source=parser_source,
        )

    def _photo_git_plan(
        self,
        *,
        request: CommandPlanRequest,
        session: dict[str, Any],
        photo_request: PhotoGitPlanRequest,
        command_type: str,
        references: list[NormalizedNumber],
        parser_source: str,
    ) -> dict[str, Any]:
        try:
            photo_plan = self.photo_git_service.plan(photo_request)
        except PhotoGitError as exc:
            if exc.code in {
                "photo_git_scope_required",
                "photo_git_scope_unclear",
                "photo_git_scope_ambiguous",
            }:
                return self._clarification(
                    request,
                    command_type=command_type,
                    code=exc.code,
                    zh="請指定要撤回的區域或參數，例如「撤回版本五的背景飽和度」。",
                    en="Specify the region or parameter to revert, such as 'revert background saturation from version five'.",
                    parser_source=parser_source,
                    session=session,
                )
            return self._unsupported(
                request,
                command_type=command_type,
                code=exc.code,
                zh=str(exc),
                en="This version operation cannot be planned safely with the current history.",
                parser_source=parser_source,
                session=session,
            )
        evidence = {
            f"version_{index + 1}": _evidence(reference, request.instruction)
            for index, reference in enumerate(references)
        }
        disposition = (
            "conflict"
            if photo_plan.get("status") == "conflict"
            else "ready"
            if photo_plan.get("status") == "ready"
            else "unsupported"
        )
        if command_type == "photo_git_merge":
            applied = len(photo_plan.get("applied_contributions") or [])
            conflicts = len(photo_plan.get("conflicts") or [])
            summary = {
                "zh": f"版本合併計畫：{applied} 項可加入、{conflicts} 項衝突；確認預覽後才建立版本。",
                "en": f"Merge plan: {applied} contribution(s), {conflicts} conflict(s). A version is created only after preview confirmation.",
            }
        else:
            removed = len(photo_plan.get("removed_contributions") or [])
            conflicts = len(photo_plan.get("conflicts") or [])
            summary = {
                "zh": f"選擇性撤回計畫：{removed} 項將移除、{conflicts} 項相依衝突；確認預覽後才建立版本。",
                "en": f"Selective revert plan: {removed} contribution(s) removed, {conflicts} dependency conflict(s). A version is created only after preview confirmation.",
            }
        if disposition == "unsupported":
            summary = {
                "zh": str(photo_plan.get("message") or "這次版本操作不會產生變更。"),
                "en": "This version operation would not produce a change.",
            }
        return self._finalize(
            request=request,
            disposition=disposition,
            command_type=command_type,
            target_edit_id=photo_request.target_edit_id,
            source_edit_id=photo_request.source_edit_id,
            revert_edit_id=photo_request.revert_edit_id,
            normalized_slots={
                "operation": photo_request.operation,
                "version_indexes": _unique_version_indexes(references),
            },
            evidence=evidence,
            action={
                "photo_git_request": photo_request.model_dump(mode="json"),
                "photo_git_plan": photo_plan,
            },
            confirmation_policy="preview_then_confirm",
            parser_source=parser_source,
            summary=summary,
            session=session,
        )

    def _ready_edit_prompt(
        self,
        request: CommandPlanRequest,
        *,
        parser_source: str,
    ) -> dict[str, Any]:
        instruction = request.instruction.strip()
        return self._finalize(
            request=request,
            disposition="ready",
            command_type="edit_prompt",
            evidence={"instruction": _span(instruction, 0, len(instruction))},
            action={"prompt": instruction},
            confirmation_policy="execute_after_apply",
            parser_source=parser_source,
            summary={
                "zh": "以一般自然語言修圖流程套用這段指令。",
                "en": "Apply this instruction through the standard natural-language edit flow.",
            },
        )

    def _parse_manual_surface(self, instruction: str) -> dict[str, Any]:
        axis_matches = _find_axis_matches(instruction)
        axes = list(dict.fromkeys(match[2] for match in axis_matches))
        axis = axes[0] if len(axes) == 1 else None
        axis_evidence = None
        if axis is not None:
            match = next(item for item in axis_matches if item[2] == axis)
            axis_evidence = _span(instruction, match[0], match[1])
        mode_matches: list[tuple[int, int, str]] = []
        for mode, patterns in (
            ("reset", _RESET_PATTERNS),
            ("delta_positive", _DELTA_POSITIVE_PATTERNS),
            ("delta_negative", _DELTA_NEGATIVE_PATTERNS),
            ("absolute", _ABSOLUTE_PATTERNS),
        ):
            for pattern in patterns:
                for match in pattern.finditer(instruction):
                    mode_matches.append((match.start(), match.end(), mode))
        modes = list(dict.fromkeys(item[2] for item in sorted(mode_matches)))
        mode = modes[0] if len(modes) == 1 else None
        mode_evidence = None
        if mode is not None:
            match = next(item for item in mode_matches if item[2] == mode)
            mode_evidence = _span(instruction, match[0], match[1])
        return {
            "axis": axis,
            "axis_evidence": axis_evidence,
            "multiple_axes": len(axes) > 1,
            "mode": mode,
            "mode_evidence": mode_evidence,
            "multiple_modes": len(modes) > 1,
            "numbers": find_number_spans(instruction),
        }

    def _grounded_command_type(self, instruction: str) -> str | None:
        if self.candidate_provider is None:
            return None
        if not (
            _VERSION_HINT_PATTERN.search(instruction)
            or _STYLE_HINT_PATTERN.search(instruction)
        ):
            return None
        try:
            candidate = self.candidate_provider(
                instruction,
                {
                    "allowed_command_types": [
                        "edit_prompt",
                        "manual_adjust",
                        "apply_style",
                        "photo_git_merge",
                        "photo_git_revert",
                        "unknown",
                    ]
                },
            )
        except Exception:
            return None
        if not isinstance(candidate, Mapping):
            return None
        command_type = str(candidate.get("command_type") or "").strip()
        evidence = str(candidate.get("evidence") or "")
        try:
            confidence = float(candidate.get("confidence"))
        except (TypeError, ValueError):
            return None
        if command_type not in {
            "edit_prompt",
            "manual_adjust",
            "apply_style",
            "photo_git_merge",
            "photo_git_revert",
            "unknown",
        }:
            return None
        if not math.isfinite(confidence) or confidence < 0.75:
            return None
        if not evidence.strip() or instruction.casefold().count(evidence.casefold()) != 1:
            return None
        return command_type

    def _selected_record(
        self,
        request: CommandPlanRequest,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        session = self._load_session(request.session_id)
        selected_id = str(request.selected_edit_id or "").strip()
        if session is None or not selected_id or selected_id == "original":
            return session, None
        for item in session.get("edits", []):
            if isinstance(item, Mapping) and item.get("edit_id") == selected_id:
                return session, dict(item)
        return session, None

    def _load_session(self, session_id: str | None) -> dict[str, Any] | None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return None
        try:
            return self.history_store.load_session(normalized)
        except (EditHistoryInvalidIdentifier, EditHistoryNotFound):
            return None

    @staticmethod
    def _resolve_version_indexes(
        session: Mapping[str, Any],
        indexes: list[int],
    ) -> list[str] | None:
        edits = session.get("edits")
        if not isinstance(edits, list):
            return None
        result: list[str] = []
        for index in indexes:
            if index < 1 or index > len(edits):
                return None
            item = edits[index - 1]
            if not isinstance(item, Mapping) or not item.get("edit_id"):
                return None
            result.append(str(item["edit_id"]))
        return result

    def _clarification(
        self,
        request: CommandPlanRequest,
        *,
        command_type: str,
        code: str,
        zh: str,
        en: str,
        parser_source: str = "command_rules",
        options: list[dict[str, Any]] | None = None,
        session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._finalize(
            request=request,
            disposition="clarification_required",
            command_type=command_type,
            confirmation_policy="none",
            parser_source=parser_source,
            summary={"zh": zh, "en": en},
            clarification={
                "code": code,
                "question": {"zh": zh, "en": en},
                "options": options or [],
            },
            session=session,
        )

    def _unsupported(
        self,
        request: CommandPlanRequest,
        *,
        command_type: str,
        code: str,
        zh: str,
        en: str,
        parser_source: str = "command_rules",
        session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._finalize(
            request=request,
            disposition="unsupported",
            command_type=command_type,
            normalized_slots={"reason": code},
            confirmation_policy="none",
            parser_source=parser_source,
            summary={"zh": zh, "en": en},
            session=session,
        )

    def _finalize(
        self,
        *,
        request: CommandPlanRequest,
        disposition: str,
        command_type: str,
        confirmation_policy: str,
        parser_source: str,
        summary: dict[str, str],
        target_edit_id: str | None = None,
        source_edit_id: str | None = None,
        revert_edit_id: str | None = None,
        normalized_slots: dict[str, Any] | None = None,
        evidence: dict[str, dict[str, Any]] | None = None,
        action: dict[str, Any] | None = None,
        clarification: dict[str, Any] | None = None,
        session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_session = session or self._load_session(request.session_id)
        payload: dict[str, Any] = {
            "schema_version": COMMAND_SCHEMA_VERSION,
            "disposition": disposition,
            "command_type": command_type,
            "original_instruction": request.instruction.strip(),
            "session_id": str(request.session_id or "").strip() or None,
            "selected_edit_id": str(request.selected_edit_id or "").strip() or None,
            "target_edit_id": target_edit_id,
            "source_edit_id": source_edit_id,
            "revert_edit_id": revert_edit_id,
            "normalized_slots": normalized_slots or {},
            "evidence": evidence or {},
            "action": action or {},
            "confirmation_policy": confirmation_policy,
            "history_fingerprint": _history_fingerprint(resolved_session),
            "parser_source": parser_source,
            "summary": summary,
            "clarification": clarification,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload["plan_hash"] = hashlib.sha256(encoded).hexdigest()
        resolved = ResolvedCommandPlan.model_validate(payload).model_dump(mode="json")
        self._remember_plan(resolved)
        return resolved

    def _remember_plan(self, plan: dict[str, Any]) -> None:
        now = time.monotonic()
        plan_hash = str(plan.get("plan_hash") or "")
        with self._plan_cache_lock:
            self._prune_plan_cache_locked(now)
            self._plan_cache[plan_hash] = (now, copy.deepcopy(plan))
            if len(self._plan_cache) > self._plan_cache_max_entries:
                oldest = min(
                    self._plan_cache.items(),
                    key=lambda item: item[1][0],
                )[0]
                self._plan_cache.pop(oldest, None)

    def _prune_plan_cache_locked(self, now: float) -> None:
        expired = [
            plan_hash
            for plan_hash, (created_at, _) in self._plan_cache.items()
            if now - created_at > self._plan_cache_ttl_seconds
        ]
        for plan_hash in expired:
            self._plan_cache.pop(plan_hash, None)


def _find_axis_matches(instruction: str) -> list[tuple[int, int, str, str]]:
    normalized = unicodedata.normalize("NFKC", instruction).casefold()
    candidates: list[tuple[int, int, str, str]] = []
    for axis in MANUAL_PARAMETER_KEYS:
        spec = EDIT_PARAMETER_SPECS[axis]
        aliases = {
            axis,
            str(spec["label"]),
            str(spec["label_en"]),
            *_PARAMETER_EXTRA_ALIASES.get(axis, ()),
        }
        for alias in aliases:
            lowered = unicodedata.normalize("NFKC", alias).casefold()
            pattern = (
                rf"(?<![a-z0-9_]){re.escape(lowered)}(?![a-z0-9_])"
                if re.search(r"[a-z]", lowered)
                else re.escape(lowered)
            )
            for match in re.finditer(pattern, normalized):
                candidates.append((match.start(), match.end(), axis, match.group(0)))
    selected: list[tuple[int, int, str, str]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-(item[1] - item[0]), item[0], item[2]),
    ):
        start, end, axis, _ = candidate
        if any(start < chosen[1] and chosen[0] < end for chosen in selected):
            continue
        if any(chosen[2] == axis for chosen in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item[0])


def _find_style_span(instruction: str, style: Any) -> dict[str, Any] | None:
    normalized = unicodedata.normalize("NFKC", instruction).casefold()
    matches: list[tuple[int, int]] = []
    for surface in (
        style.style_id,
        style.display_name_zh,
        style.display_name_en,
        *style.aliases,
    ):
        candidate = unicodedata.normalize("NFKC", str(surface)).casefold()
        start = normalized.find(candidate)
        if start >= 0:
            matches.append((start, start + len(candidate)))
    if not matches:
        return None
    start, end = max(matches, key=lambda item: item[1] - item[0])
    return _span(instruction, start, end)


def _unique_version_indexes(references: list[NormalizedNumber]) -> list[int]:
    result: list[int] = []
    for reference in references:
        if not float(reference.value).is_integer():
            continue
        value = int(reference.value)
        if value not in result:
            result.append(value)
    return result


def _evidence(number: NormalizedNumber, instruction: str) -> dict[str, Any]:
    return _span(instruction, number.start, number.end)


def _span(instruction: str, start: int, end: int) -> dict[str, Any]:
    bounded_start = max(0, min(len(instruction), start))
    bounded_end = max(bounded_start, min(len(instruction), end))
    raw = instruction[bounded_start:bounded_end]
    return {"start": bounded_start, "end": bounded_end, "raw_text": raw}


def _quantize(value: float, minimum: float, step: float) -> float:
    position = round((value - minimum) / step)
    quantized = minimum + position * step
    decimals = max(
        0,
        len(f"{step:.12f}".rstrip("0").split(".")[-1])
        if "." in f"{step:.12f}".rstrip("0")
        else 0,
    )
    return round(quantized, decimals)


def _history_fingerprint(session: Mapping[str, Any] | None) -> str | None:
    if not isinstance(session, Mapping):
        return None
    edits = session.get("edits")
    if not isinstance(edits, list):
        return None
    payload = [
        {
            "edit_id": item.get("edit_id"),
            "parent_edit_id": item.get("parent_edit_id"),
            "created_at": item.get("created_at"),
            "result_image_path": item.get("result_image_path"),
        }
        for item in edits
        if isinstance(item, Mapping)
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["CommandPlanCacheError", "CommandPlanner"]
