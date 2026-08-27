from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from app.services.edit_history import (
    EditHistoryConflict,
    EditHistoryInvalidIdentifier,
    EditHistoryNotFound,
    EditHistoryStore,
)
from app.services.photo_git_graph import (
    PhotoGitGraph,
    PhotoGitGraphError,
)
from app.services.photo_git_planner import (
    PhotoGitPlanningError,
    build_photo_git_plan,
)
from app.services.photo_git_recipe import PhotoGitRecipeError
from app.services.photo_git_renderer import (
    PhotoGitRenderError,
    PhotoGitRenderer,
)
from app.services.photo_git_resolver import PhotoGitResolutionError
from app.services.photo_git_schema import (
    PHOTO_GIT_RENDERER_VERSION,
    PHOTO_GIT_SCHEMA_VERSION,
    PhotoGitCommitRequest,
    PhotoGitExecutionRequest,
    PhotoGitPlanRequest,
)


_SESSION_ID_PATTERN = re.compile(r"^session_[0-9a-f]{32}$")
_EDIT_ID_PATTERN = re.compile(r"^edit_[0-9a-f]{32}$")


class PhotoGitError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


class PhotoGitService:
    def __init__(
        self,
        *,
        backend_dir: Path,
        history_store: EditHistoryStore,
        preview_root: Path,
        results_root: Path,
    ):
        self.backend_dir = Path(backend_dir).resolve()
        self.history_store = history_store
        self.renderer = PhotoGitRenderer(
            backend_dir=self.backend_dir,
            preview_root=preview_root,
            results_root=results_root,
        )

    def plan(self, request: PhotoGitPlanRequest) -> dict[str, Any]:
        graph = self._graph(request)
        try:
            plan = build_photo_git_plan(graph, request)
        except (
            PhotoGitGraphError,
            PhotoGitRecipeError,
            PhotoGitResolutionError,
            PhotoGitPlanningError,
        ) as exc:
            raise self._domain_error(exc) from exc
        target = graph.record(request.target_edit_id)
        plan["session_id"] = request.session_id
        plan["target_result_path"] = target.get("result_image_path")
        plan["target_result_url"] = (
            f"/{target['result_image_path']}"
            if target.get("result_image_path")
            else None
        )
        return plan

    def preview(
        self,
        request: PhotoGitExecutionRequest,
    ) -> dict[str, Any]:
        plan = self._validated_execution_plan(request)
        try:
            response = self.renderer.preview(
                session_id=request.session_id,
                plan=plan,
            )
        except PhotoGitRenderError as exc:
            raise self._domain_error(exc) from exc
        response.update(
            {
                "status": "ready",
                "operation": plan["operation"],
                "session_id": request.session_id,
                "target_edit_id": request.target_edit_id,
                "target_result_path": plan.get("target_result_path"),
                "target_result_url": plan.get("target_result_url"),
                "source_edit_ids": plan.get("source_edit_ids") or [],
                "revert_edit_id": plan.get("revert_edit_id"),
                "common_ancestor_edit_id": plan.get(
                    "common_ancestor_edit_id"
                ),
                "applied_contributions": plan.get(
                    "applied_contributions"
                )
                or [],
                "removed_contributions": plan.get(
                    "removed_contributions"
                )
                or [],
                "conflicts": plan.get("conflicts") or [],
            }
        )
        return response

    def commit(
        self,
        request: PhotoGitCommitRequest,
        *,
        command_provenance: Mapping[str, Any] | None = None,
        command_provenance_loader: Callable[[], Mapping[str, Any] | None]
        | None = None,
    ) -> dict[str, Any]:
        plan = self._validated_execution_plan(request)
        existing = self._find_idempotent_record(
            session_id=request.session_id,
            client_request_id=request.client_request_id,
        )
        if existing is not None:
            metadata = existing.get("photo_git")
            if (
                not isinstance(metadata, dict)
                or metadata.get("plan_hash") != request.plan_hash
            ):
                raise PhotoGitError(
                    "photo_git_idempotency_conflict",
                    "client_request_id 已用於另一個 Photo Git plan。",
                    status_code=409,
                )
            return self._record_response(existing, idempotent_replay=True)

        if command_provenance_loader is not None:
            command_provenance = command_provenance_loader()

        graph = self._graph(
            PhotoGitPlanRequest.model_validate(
                request.model_dump(
                    exclude={"plan_hash", "client_request_id"}
                )
            )
        )
        target = graph.record(request.target_edit_id)
        edit_id = self.history_store.new_edit_id()
        try:
            rendered = self.renderer.commit_render(
                session_id=request.session_id,
                edit_id=edit_id,
                plan=plan,
            )
            metadata = self._photo_git_metadata(
                request=request,
                plan=plan,
            )
            command_metadata = (
                copy.deepcopy(dict(command_provenance))
                if command_provenance is not None
                else None
            )
            if command_metadata is not None:
                command_metadata["execution_client_request_id"] = (
                    request.client_request_id
                )
                command_metadata["photo_git_plan_hash"] = request.plan_hash
            edit_mode = (
                "photo_git_merge"
                if request.operation == "merge"
                else "photo_git_revert"
            )
            resolved_intent = (
                "photo_git_merge"
                if request.operation == "merge"
                else "photo_git_selective_revert"
            )
            edit_plan = {
                "type": "photo_git",
                "prompt": request.instruction.strip(),
                "anchor_image_path": plan["anchor_image_path"],
                "scopes": copy.deepcopy(plan["recipe"]["layers"]),
                "region": "all",
                "mask_type": "none",
                "photo_git": {
                    "schema_version": PHOTO_GIT_SCHEMA_VERSION,
                    "operation": request.operation,
                    "plan_hash": request.plan_hash,
                },
            }
            record = self.history_store.build_record(
                session_id=request.session_id,
                edit_id=edit_id,
                parent_edit_id=request.target_edit_id,
                edit_mode=edit_mode,
                original_image_path=str(
                    target.get("original_image_path") or ""
                ),
                base_image_path=str(plan["anchor_image_path"]),
                result_image_path=str(rendered["result_saved_path"]),
                reference_image_path=None,
                user_prompt=request.instruction.strip(),
                resolved_intent=resolved_intent,
                parameters=dict(rendered.get("parameters") or {}),
                engine="opencv",
                edit_plan=edit_plan,
                engine_parameters=dict(rendered.get("parameters") or {}),
                mask_info=dict(rendered.get("mask_info") or {}),
                explanation=(
                    f"{plan['message']} {rendered.get('explanation') or ''}"
                ).strip(),
                parser_source="photo_git_deterministic",
                fallback_reason=None,
                preset_name=None,
                processing_timings=dict(
                    rendered.get("timings_ms") or {}
                ),
                adaptive=None,
                style=None,
                photo_git=metadata,
                command=command_metadata,
            )
            try:
                _, persisted, created = (
                    self.history_store.save_edit_idempotent(
                        record,
                        client_request_id=request.client_request_id,
                        plan_hash=request.plan_hash,
                    )
                )
            except EditHistoryConflict as exc:
                raise PhotoGitError(
                    "photo_git_idempotency_conflict",
                    str(exc),
                    status_code=409,
                ) from exc
            if not created:
                self.renderer.cleanup_commit(
                    session_id=request.session_id,
                    edit_id=edit_id,
                )
                return self._record_response(
                    persisted,
                    idempotent_replay=True,
                )
            return self._record_response(
                persisted,
                idempotent_replay=False,
            )
        except PhotoGitError:
            self.renderer.cleanup_commit(
                session_id=request.session_id,
                edit_id=edit_id,
            )
            raise
        except Exception as exc:
            self.renderer.cleanup_commit(
                session_id=request.session_id,
                edit_id=edit_id,
            )
            if isinstance(exc, PhotoGitRenderError):
                raise self._domain_error(exc) from exc
            raise PhotoGitError(
                "photo_git_commit_failed",
                f"Photo Git commit 失敗：{exc}",
                status_code=500,
            ) from exc

    def _validated_execution_plan(
        self,
        request: PhotoGitExecutionRequest,
    ) -> dict[str, Any]:
        plan_request = PhotoGitPlanRequest.model_validate(
            request.model_dump(
                exclude={"plan_hash", "client_request_id"}
            )
        )
        plan = self.plan(plan_request)
        if plan["plan_hash"] != request.plan_hash:
            raise PhotoGitError(
                "photo_git_plan_stale",
                "Photo Git plan 已變更，請重新分析後再預覽或建立版本。",
                status_code=409,
            )
        if plan["status"] == "conflict":
            raise PhotoGitError(
                "photo_git_conflict",
                plan["message"],
                status_code=409,
                details={"conflicts": plan.get("conflicts") or []},
            )
        if plan["status"] != "ready":
            raise PhotoGitError(
                "photo_git_no_change",
                plan["message"],
                status_code=422,
            )
        return plan

    def _graph(self, request: PhotoGitPlanRequest) -> PhotoGitGraph:
        self._validate_request_identifiers(request)
        try:
            session = self.history_store.load_session(request.session_id)
        except EditHistoryInvalidIdentifier as exc:
            raise PhotoGitError(
                "photo_git_session_invalid",
                str(exc),
                status_code=400,
            ) from exc
        except EditHistoryNotFound as exc:
            raise PhotoGitError(
                "photo_git_session_not_found",
                str(exc),
                status_code=404,
            ) from exc
        try:
            return PhotoGitGraph.from_session(session)
        except PhotoGitGraphError as exc:
            raise self._domain_error(exc) from exc

    @staticmethod
    def _validate_request_identifiers(
        request: PhotoGitPlanRequest,
    ) -> None:
        if _SESSION_ID_PATTERN.fullmatch(request.session_id) is None:
            raise PhotoGitError(
                "photo_git_session_invalid",
                "Invalid Photo Git session_id.",
                status_code=400,
            )
        for label, value in (
            ("target_edit_id", request.target_edit_id),
            ("source_edit_id", request.source_edit_id),
            ("revert_edit_id", request.revert_edit_id),
        ):
            if value is not None and _EDIT_ID_PATTERN.fullmatch(value) is None:
                raise PhotoGitError(
                    "photo_git_edit_id_invalid",
                    f"Invalid {label}.",
                    status_code=400,
                )

    def _find_idempotent_record(
        self,
        *,
        session_id: str,
        client_request_id: str,
    ) -> dict[str, Any] | None:
        try:
            session = self.history_store.load_session(session_id)
        except (EditHistoryInvalidIdentifier, EditHistoryNotFound):
            return None
        for record in session.get("edits", []):
            metadata = (
                record.get("photo_git")
                if isinstance(record, dict)
                else None
            )
            if (
                isinstance(metadata, dict)
                and metadata.get("client_request_id")
                == client_request_id
            ):
                return record
        return None

    @staticmethod
    def _photo_git_metadata(
        *,
        request: PhotoGitCommitRequest,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": PHOTO_GIT_SCHEMA_VERSION,
            "renderer_version": PHOTO_GIT_RENDERER_VERSION,
            "operation": request.operation,
            "target_edit_id": request.target_edit_id,
            "source_edit_ids": list(plan.get("source_edit_ids") or []),
            "reverted_edit_id": request.revert_edit_id,
            "common_ancestor_edit_id": plan.get(
                "common_ancestor_edit_id"
            ),
            "instruction": request.instruction.strip(),
            "selectors": copy.deepcopy(plan.get("selectors") or []),
            "applied_contributions": copy.deepcopy(
                plan.get("applied_contributions") or []
            ),
            "removed_contributions": copy.deepcopy(
                plan.get("removed_contributions") or []
            ),
            "conflicts": copy.deepcopy(plan.get("conflicts") or []),
            "resolutions": dict(request.resolutions),
            "anchor_image_path": plan["anchor_image_path"],
            "recipe": copy.deepcopy(plan["recipe"]),
            "plan_hash": request.plan_hash,
            "client_request_id": request.client_request_id,
            "command_plan_hash": request.command_plan_hash,
        }

    @staticmethod
    def _record_response(
        record: dict[str, Any],
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        response = copy.deepcopy(record)
        result_path = str(record.get("result_image_path") or "")
        original_path = str(record.get("original_image_path") or "")
        response.update(
            {
                "message": "Photo Git version created",
                "task_id": record.get("edit_id"),
                "result_saved_path": result_path,
                "result_url": f"/{result_path}" if result_path else None,
                "original_saved_path": original_path,
                "original_url": f"/{original_path}"
                if original_path
                else None,
                "prompt": record.get("user_prompt") or "",
                "idempotent_replay": idempotent_replay,
            }
        )
        return response

    @staticmethod
    def _domain_error(exc: Exception) -> PhotoGitError:
        code = getattr(exc, "code", "photo_git_invalid")
        status_code = getattr(exc, "status_code", 422)
        if code in {
            "photo_git_version_not_found",
            "photo_git_parent_missing",
            "photo_git_original_missing",
            "photo_git_anchor_not_found",
        }:
            status_code = 404
        elif code in {
            "photo_git_history_invalid",
            "photo_git_history_cycle",
            "photo_git_scope_invalid",
            "photo_git_parameter_unsupported",
        }:
            status_code = 422
        details = getattr(exc, "details", None)
        return PhotoGitError(
            code,
            str(exc),
            status_code=status_code,
            details=details if isinstance(details, dict) else None,
        )
