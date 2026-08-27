from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from app.services.edit_engines import create_engine_result
from app.services.edit_history import (
    EditHistoryConflict,
    EditHistoryNotFound,
    EditHistoryStore,
)
from app.services.edit_plan import build_raw_parameter_edit_plan
from app.services.edit_schema import (
    ManualParameterValidationError,
    require_region_mask_pair,
    validate_edit_parameters,
    validate_manual_parameter_overrides,
)
from app.services.opencv_parameter_mapper import NEUTRAL_OPENCV_PARAMETERS


MANUAL_PREVIEW_CACHE_VERSION = "manual_preview_v1"
_SESSION_ID_PATTERN = re.compile(r"^session_[0-9a-f]{32}$")
_EDIT_ID_PATTERN = re.compile(r"^edit_[0-9a-f]{32}$")


class ManualEditError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True)
class ManualEditContext:
    session_id: str
    source_edit_id: str
    source_record: dict[str, Any]
    original_path: Path
    original_saved_path: str
    base_path: Path
    base_saved_path: str
    parameter_overrides: dict[str, float]
    canonical_parameters: dict[str, Any]
    edit_plan: dict[str, Any]
    style: dict[str, Any] | None


class ManualEditService:
    def __init__(
        self,
        *,
        backend_dir: Path,
        history_store: EditHistoryStore,
        preview_root: Path,
        results_root: Path,
        preview_ttl_seconds: int = 24 * 60 * 60,
        max_previews_per_source: int = 80,
    ):
        self.backend_dir = Path(backend_dir).resolve()
        self.history_store = history_store
        self.preview_root = Path(preview_root)
        self.results_root = Path(results_root)
        self.preview_ttl_seconds = max(60, int(preview_ttl_seconds))
        self.max_previews_per_source = max(5, int(max_previews_per_source))
        self._preview_locks_guard = threading.Lock()
        self._preview_locks: dict[str, threading.Lock] = {}

    def preview(
        self,
        *,
        session_id: str,
        source_edit_id: str,
        parameter_overrides: Mapping[str, Any] | None,
        client_request_id: str | None,
    ) -> dict[str, Any]:
        request_started = time.perf_counter()
        context_started = time.perf_counter()
        context = self._build_context(
            session_id=session_id,
            source_edit_id=source_edit_id,
            parameter_overrides=parameter_overrides,
        )
        context_ms = _elapsed_ms(context_started)
        request_id = self._validate_client_request_id(client_request_id)
        preview_id = self._preview_id(context)
        preview_dir = (
            self.preview_root
            / context.session_id
            / context.source_edit_id
            / preview_id
        )
        result_path = preview_dir / "result.png"
        metadata_path = preview_dir / "metadata.json"

        with self._preview_lock(preview_id):
            cached = self._load_preview_metadata(
                metadata_path=metadata_path,
                result_path=result_path,
                preview_id=preview_id,
            )
            preview_cache_hit = cached is not None
            if cached is None:
                render_started = time.perf_counter()
                process_result = self._render(context, result_path)
                render_ms = _elapsed_ms(render_started)
                cached = self._base_response(
                    context=context,
                    result_path=result_path,
                    process_result=process_result,
                )
                cached.update(
                    {
                        "preview_id": preview_id,
                        "preview_cache_version": MANUAL_PREVIEW_CACHE_VERSION,
                        "render_ms": round(render_ms, 3),
                    }
                )
                _write_json_atomic(metadata_path, cached)
            else:
                metadata_path.touch()
                result_path.touch()

        self._cleanup_previews(preview_dir.parent, keep=preview_dir)
        response = dict(cached)
        response.update(
            {
                "message": "Manual preview created",
                "edit_mode": "manual_preview",
                "client_request_id": request_id,
                "preview_cache_hit": preview_cache_hit,
                "timings_ms": {
                    "context_and_validation": round(context_ms, 3),
                    "render": 0.0
                    if preview_cache_hit
                    else float(cached.get("render_ms") or 0.0),
                    "request_total": round(_elapsed_ms(request_started), 3),
                },
            }
        )
        return response

    def commit(
        self,
        *,
        session_id: str,
        source_edit_id: str,
        parameter_overrides: Mapping[str, Any] | None,
        client_request_id: str | None,
        instruction: str = "",
        command_plan_hash: str | None = None,
        command_provenance: Mapping[str, Any] | None = None,
        command_provenance_loader: Callable[[], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request_started = time.perf_counter()
        context_started = time.perf_counter()
        context = self._build_context(
            session_id=session_id,
            source_edit_id=source_edit_id,
            parameter_overrides=parameter_overrides,
        )
        context_ms = _elapsed_ms(context_started)
        request_id = self._validate_client_request_id(client_request_id)
        instruction_text = str(instruction or "").strip()
        plan_hash = self._validate_command_plan_hash(command_plan_hash)
        request_hash = self._manual_request_hash(
            context=context,
            instruction=instruction_text,
            command_plan_hash=plan_hash,
        )
        if request_id is not None:
            try:
                existing = self.history_store.find_edit_request_idempotent(
                    namespace="manual",
                    client_request_id=request_id,
                    request_hash=request_hash,
                    scope_session_id=context.session_id,
                )
            except EditHistoryConflict as exc:
                raise ManualEditError(
                    "manual_request_conflict",
                    str(exc),
                    status_code=409,
                ) from exc
            if existing is not None:
                _, record = existing
                return self._record_response(
                    record,
                    client_request_id=request_id,
                    idempotent_replay=True,
                    request_total_ms=_elapsed_ms(request_started),
                )

        if command_provenance_loader is not None:
            command_provenance = command_provenance_loader()

        edit_id = self.history_store.new_edit_id()
        result_path = self.results_root / context.session_id / edit_id / "result.png"
        history_committed = False
        try:
            render_started = time.perf_counter()
            process_result = self._render(context, result_path)
            render_ms = _elapsed_ms(render_started)
            result_saved_path = self._relative_backend_path(result_path)
            explanation = self._manual_explanation(context.parameter_overrides)
            processing_timings = {
                "context_and_validation": round(context_ms, 3),
                "render": round(render_ms, 3),
                "opencv": process_result.get("timings_ms"),
            }
            manual_metadata = {
                "client_request_id": request_id,
                "request_hash": request_hash,
                "source_edit_id": context.source_edit_id,
                "parameter_overrides": context.parameter_overrides,
            }
            command_metadata = None
            if instruction_text:
                command_metadata = json.loads(
                    json.dumps(
                        dict(command_provenance)
                        if command_provenance is not None
                        else {
                            "schema_version": "command_plan_v1",
                            "command_type": "manual_adjust",
                            "original_instruction": instruction_text,
                            "plan_hash": plan_hash,
                            "action": {
                                "source_edit_id": context.source_edit_id,
                                "parameter_overrides": context.parameter_overrides,
                            },
                        },
                        ensure_ascii=False,
                    )
                )
                command_metadata["execution_client_request_id"] = request_id
            parser_source = (
                str(command_metadata.get("parser_source") or "command_planner")
                if command_metadata is not None
                else "manual_parameters"
            )
            record = self.history_store.build_record(
                session_id=context.session_id,
                edit_id=edit_id,
                parent_edit_id=context.source_edit_id,
                edit_mode="manual",
                original_image_path=context.original_saved_path,
                base_image_path=context.base_saved_path,
                result_image_path=result_saved_path,
                reference_image_path=None,
                user_prompt=instruction_text,
                resolved_intent="manual_adjustment",
                parameters=process_result["parameters"],
                engine=process_result["engine"],
                edit_plan=context.edit_plan,
                engine_parameters=process_result["parameters"],
                mask_info=process_result.get("mask_info"),
                explanation=explanation,
                parser_source=parser_source,
                fallback_reason=None,
                preset_name=None,
                manual_source_edit_id=context.source_edit_id,
                parameter_overrides=context.parameter_overrides,
                processing_timings=processing_timings,
                style=context.style,
                manual=manual_metadata,
                command=command_metadata,
            )
            persisted_record = record
            created = True
            if request_id is not None:
                try:
                    _, persisted_record, created = (
                        self.history_store.save_edit_request_idempotent(
                            record,
                            namespace="manual",
                            client_request_id=request_id,
                            request_hash=request_hash,
                            scope_session_id=context.session_id,
                        )
                    )
                except EditHistoryConflict as exc:
                    raise ManualEditError(
                        "manual_request_conflict",
                        str(exc),
                        status_code=409,
                    ) from exc
            else:
                self.history_store.save_edit(record)
            history_committed = created
            return self._record_response(
                persisted_record,
                client_request_id=request_id,
                idempotent_replay=not created,
                request_total_ms=_elapsed_ms(request_started),
            )
        finally:
            if not history_committed and result_path.parent.is_dir():
                shutil.rmtree(result_path.parent, ignore_errors=True)

    def _build_context(
        self,
        *,
        session_id: str,
        source_edit_id: str,
        parameter_overrides: Mapping[str, Any] | None,
    ) -> ManualEditContext:
        normalized_session = str(session_id or "").strip()
        normalized_edit = str(source_edit_id or "").strip()
        if not _SESSION_ID_PATTERN.fullmatch(normalized_session):
            raise ManualEditError("invalid_session_id", "Invalid manual edit session_id")
        if not _EDIT_ID_PATTERN.fullmatch(normalized_edit):
            raise ManualEditError("invalid_source_edit_id", "Invalid source_edit_id")
        try:
            source = self.history_store.find_edit(normalized_session, normalized_edit)
        except EditHistoryNotFound as exc:
            raise ManualEditError(
                "manual_source_not_found",
                str(exc),
                status_code=404,
            ) from exc

        if str(source.get("engine") or "opencv").lower() != "opencv":
            raise ManualEditError(
                "manual_source_engine_unsupported",
                "Manual adjustment v1 only supports OpenCV source edits",
            )
        source_mode = str(source.get("edit_mode") or "")
        if source_mode not in {
            "prompt",
            "manual",
            "photo_git_merge",
            "photo_git_revert",
        }:
            raise ManualEditError(
                "manual_source_mode_unsupported",
                (
                    "Manual adjustment v1 supports prompt, manual, "
                    "or Photo Git source edits only"
                ),
            )

        try:
            overrides = validate_manual_parameter_overrides(parameter_overrides)
        except ManualParameterValidationError as exc:
            raise ManualEditError("invalid_manual_parameters", str(exc)) from exc

        original_saved_path = str(source.get("original_image_path") or "")
        base_saved_path = str(source.get("base_image_path") or "")
        source_plan = source.get("edit_plan")
        source_is_style = (
            isinstance(source_plan, Mapping)
            and str(source_plan.get("type") or "") == "style"
        )
        source_is_photo_git = source_mode in {
            "photo_git_merge",
            "photo_git_revert",
        }
        source_is_visual_anchor = source_is_style or source_is_photo_git
        if source_is_visual_anchor:
            # A style result becomes the immutable visual anchor for manual
            # micro-adjustments. The same rule applies to a Photo Git composite:
            # reusing its pre-render anchor would drop other merged scopes.
            base_saved_path = str(source.get("result_image_path") or "")
        original_path = self._safe_backend_path(original_saved_path, "original image")
        base_path = self._safe_backend_path(base_saved_path, "base image")

        source_parameters = source.get("engine_parameters") or source.get("parameters")
        if not isinstance(source_parameters, Mapping):
            raise ManualEditError(
                "manual_source_parameters_missing",
                "Source edit does not contain reusable OpenCV parameters",
            )
        canonical: dict[str, Any] = NEUTRAL_OPENCV_PARAMETERS.copy()
        if not source_is_visual_anchor:
            canonical.update(validate_edit_parameters(source_parameters))
        canonical.update(overrides)
        canonical["reference_tint"] = 0.0
        raw_region = (
            None
            if source_is_visual_anchor
            else source_parameters.get("region")
        ) or (
            source_plan.get("region")
            if isinstance(source_plan, Mapping)
            else None
        )
        raw_mask_type = (
            None
            if source_is_visual_anchor
            else source_parameters.get("mask_type")
        ) or (
            source_plan.get("mask_type")
            if isinstance(source_plan, Mapping)
            else None
        )
        try:
            region, mask_type = require_region_mask_pair(
                raw_region,
                raw_mask_type,
            )
        except ValueError as exc:
            raise ManualEditError(
                "manual_source_region_contract_invalid",
                (
                    "Source edit contains an invalid region/mask contract; "
                    "manual adjustment was not rendered"
                ),
            ) from exc
        canonical["region"] = region
        canonical["mask_type"] = mask_type
        edit_plan = build_raw_parameter_edit_plan(
            prompt="",
            parameters=canonical,
            region=region,
            mask_type=mask_type,
        )
        return ManualEditContext(
            session_id=normalized_session,
            source_edit_id=normalized_edit,
            source_record=dict(source),
            original_path=original_path,
            original_saved_path=original_saved_path,
            base_path=base_path,
            base_saved_path=base_saved_path,
            parameter_overrides=overrides,
            canonical_parameters=canonical,
            edit_plan=edit_plan,
            style=(
                dict(source["style"])
                if isinstance(source.get("style"), Mapping)
                else None
            ),
        )

    def _render(
        self,
        context: ManualEditContext,
        result_path: Path,
    ) -> dict[str, Any]:
        return create_engine_result(
            engine_name="opencv",
            original_path=context.base_path,
            reference_path=None,
            result_path=result_path,
            edit_plan=context.edit_plan,
            mask_source_path=context.original_path,
        )

    def _base_response(
        self,
        *,
        context: ManualEditContext,
        result_path: Path,
        process_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        result_saved_path = self._relative_backend_path(result_path)
        return {
            "session_id": context.session_id,
            "source_edit_id": context.source_edit_id,
            "manual_source_edit_id": context.source_edit_id,
            "original_saved_path": context.original_saved_path,
            "base_image_path": context.base_saved_path,
            "result_saved_path": result_saved_path,
            "result_url": f"/{result_saved_path}",
            "engine": process_result["engine"],
            "edit_plan": context.edit_plan,
            "parameter_overrides": context.parameter_overrides,
            "engine_parameters": process_result["parameters"],
            "parameters": process_result["parameters"],
            "mask_info": process_result.get("mask_info"),
            "processing_timings": process_result.get("timings_ms"),
            "style": context.style,
        }

    def _preview_id(self, context: ManualEditContext) -> str:
        digest = hashlib.sha256()
        digest.update(MANUAL_PREVIEW_CACHE_VERSION.encode("utf-8"))
        digest.update(context.session_id.encode("utf-8"))
        digest.update(context.source_edit_id.encode("utf-8"))
        digest.update(_sha256_file(context.base_path).encode("ascii"))
        digest.update(_sha256_file(context.original_path).encode("ascii"))
        digest.update(
            json.dumps(
                context.canonical_parameters,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return f"manual_preview_{digest.hexdigest()[:24]}"

    def _load_preview_metadata(
        self,
        *,
        metadata_path: Path,
        result_path: Path,
        preview_id: str,
    ) -> dict[str, Any] | None:
        if not metadata_path.is_file() or not result_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if metadata.get("preview_id") != preview_id:
            return None
        if metadata.get("preview_cache_version") != MANUAL_PREVIEW_CACHE_VERSION:
            return None
        return metadata

    def _cleanup_previews(self, source_dir: Path, *, keep: Path) -> None:
        if not source_dir.is_dir():
            return
        now = time.time()
        completed = []
        for candidate in source_dir.iterdir():
            if not candidate.is_dir() or candidate == keep:
                continue
            metadata = candidate / "metadata.json"
            result = candidate / "result.png"
            if not metadata.is_file() or not result.is_file():
                if now - candidate.stat().st_mtime > self.preview_ttl_seconds:
                    shutil.rmtree(candidate, ignore_errors=True)
                continue
            modified = max(metadata.stat().st_mtime, result.stat().st_mtime)
            if now - modified > self.preview_ttl_seconds:
                shutil.rmtree(candidate, ignore_errors=True)
            else:
                completed.append((modified, candidate))
        completed.sort(reverse=True)
        for _, candidate in completed[self.max_previews_per_source - 1 :]:
            shutil.rmtree(candidate, ignore_errors=True)

    def _safe_backend_path(self, relative_path: str, label: str) -> Path:
        if not relative_path:
            raise ManualEditError(
                "manual_source_path_missing",
                f"Source edit {label} path is missing",
            )
        candidate = (self.backend_dir / relative_path).resolve()
        try:
            candidate.relative_to(self.backend_dir)
        except ValueError as exc:
            raise ManualEditError(
                "manual_source_path_invalid",
                f"Source edit {label} path escapes backend storage",
            ) from exc
        if not candidate.is_file():
            raise ManualEditError(
                "manual_source_file_missing",
                f"Source edit {label} does not exist: {relative_path}",
                status_code=404,
            )
        return candidate

    def _relative_backend_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.backend_dir).as_posix()
        except ValueError as exc:
            raise ManualEditError(
                "manual_result_path_invalid",
                "Manual result path escapes backend storage",
                status_code=500,
            ) from exc

    def _preview_lock(self, preview_id: str) -> threading.Lock:
        with self._preview_locks_guard:
            return self._preview_locks.setdefault(preview_id, threading.Lock())

    @staticmethod
    def _manual_request_hash(
        *,
        context: ManualEditContext,
        instruction: str,
        command_plan_hash: str | None,
    ) -> str:
        payload = {
            "session_id": context.session_id,
            "source_edit_id": context.source_edit_id,
            "parameter_overrides": context.parameter_overrides,
            "instruction": instruction,
            "command_plan_hash": command_plan_hash,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _record_response(
        self,
        record: Mapping[str, Any],
        *,
        client_request_id: str | None,
        idempotent_replay: bool,
        request_total_ms: float,
    ) -> dict[str, Any]:
        result_saved_path = str(record.get("result_image_path") or "")
        parameters = record.get("engine_parameters")
        if not isinstance(parameters, Mapping):
            parameters = record.get("parameters") or {}
        source_edit_id = str(
            record.get("manual_source_edit_id")
            or record.get("parent_edit_id")
            or ""
        )
        return {
            "message": (
                "Existing manual edit returned for idempotent request"
                if idempotent_replay
                else "Manual edit committed"
            ),
            "task_id": record.get("edit_id"),
            "session_id": record.get("session_id"),
            "edit_id": record.get("edit_id"),
            "parent_edit_id": record.get("parent_edit_id"),
            "source_edit_id": source_edit_id,
            "manual_source_edit_id": source_edit_id,
            "original_saved_path": record.get("original_image_path"),
            "base_image_path": record.get("base_image_path"),
            "result_saved_path": result_saved_path,
            "result_url": f"/{result_saved_path}",
            "engine": record.get("engine"),
            "edit_mode": record.get("edit_mode"),
            "edit_plan": record.get("edit_plan"),
            "parameter_overrides": record.get("parameter_overrides") or {},
            "engine_parameters": dict(parameters),
            "parameters": dict(parameters),
            "mask_info": record.get("mask_info"),
            "processing_timings": record.get("processing_timings"),
            "style": record.get("style"),
            "client_request_id": client_request_id,
            "resolved_intent": record.get("resolved_intent"),
            "parser_source": record.get("parser_source"),
            "explanation": record.get("explanation"),
            "command": record.get("command"),
            "idempotent_replay": idempotent_replay,
            "timings_ms": {
                **dict(record.get("processing_timings") or {}),
                "request_total": round(request_total_ms, 3),
            },
        }

    @staticmethod
    def _validate_command_plan_hash(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ManualEditError(
                "invalid_command_plan_hash",
                "command_plan_hash must be a 64-character SHA-256 hash",
            )
        return normalized

    @staticmethod
    def _validate_client_request_id(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or len(normalized) > 128:
            raise ManualEditError(
                "invalid_client_request_id",
                "client_request_id must contain 1 to 128 characters",
            )
        return normalized

    @staticmethod
    def _manual_explanation(overrides: Mapping[str, float]) -> str:
        if not overrides:
            return "手動參數已確認，使用來源 edit 的完整參數重新產生結果。"
        details = ", ".join(f"{key}={value:g}" for key, value in overrides.items())
        return f"手動參數已從同一基準圖重新套用：{details}。"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(dict(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
