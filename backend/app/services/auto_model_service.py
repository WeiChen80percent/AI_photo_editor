from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.auto_model_schema import (
    AUTO_MODEL_COMPARISON_SCHEMA_VERSION,
    AUTO_MODEL_HISTORY_SCHEMA_VERSION,
    AUTO_MODEL_KEYS,
    VISUAL_ANCHOR_SCHEMA_VERSION,
    AutoEnhanceAdapter,
    AutoModelError,
    AutoModelResult,
)
from app.services.edit_history import (
    EditHistoryConflict,
    EditHistoryNotFound,
    EditHistoryStore,
)


ORIGINAL_SOURCE_ID = "original"
COMPARISON_ID_PATTERN = re.compile(r"^comparison_[0-9a-f]{32}$")
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_IMAGE_PIXELS = 80_000_000


@dataclass(frozen=True)
class _SourceContext:
    session_id: str | None
    source_edit_id: str
    parent_edit_id: str | None
    original_saved_path: str | None
    base_saved_path: str | None
    original_path: Path | None
    base_path: Path | None
    source_sha256: str
    upload_bytes: bytes | None
    upload_suffix: str | None


class AutoModelComparisonService:
    def __init__(
        self,
        *,
        backend_dir: Path,
        history_store: EditHistoryStore,
        adapters: Mapping[str, AutoEnhanceAdapter],
        results_root: Path,
        uploads_root: Path,
        comparisons_root: Path,
    ):
        self.backend_dir = Path(backend_dir).resolve()
        self.history_store = history_store
        self.adapters = dict(adapters)
        missing = [key for key in AUTO_MODEL_KEYS if key not in self.adapters]
        if missing:
            raise ValueError(f"Missing auto-model adapters: {missing}")
        self.results_root = Path(results_root)
        self.uploads_root = Path(uploads_root)
        self.comparisons_root = Path(comparisons_root)
        self._locks_guard = threading.Lock()
        self._comparison_locks: dict[str, threading.Lock] = {}

    def health(self) -> dict[str, Any]:
        models: dict[str, Any] = {}
        for key in AUTO_MODEL_KEYS:
            try:
                models[key] = self.adapters[key].health()
            except Exception as exc:
                models[key] = {
                    "model_key": key,
                    "status": "unavailable",
                    "code": "health_check_failed",
                    "message": str(exc),
                }
        return {
            "schema_version": AUTO_MODEL_COMPARISON_SCHEMA_VERSION,
            "execution_mode": "sequential",
            "models": models,
        }

    def compare(
        self,
        *,
        original_bytes: bytes | None,
        original_filename: str | None,
        session_id: str | None,
        source_edit_id: str | None,
        client_request_id: str,
        schema_version: str,
    ) -> dict[str, Any]:
        request_started = time.perf_counter()
        normalized_request_id = self._validate_client_request_id(client_request_id)
        if schema_version != AUTO_MODEL_COMPARISON_SCHEMA_VERSION:
            raise AutoModelError(
                "auto_model_schema_unsupported",
                f"Unsupported auto-model comparison schema: {schema_version}",
                status_code=400,
            )
        source = self._resolve_source_request(
            original_bytes=original_bytes,
            original_filename=original_filename,
            session_id=session_id,
            source_edit_id=source_edit_id,
        )
        request_hash = self._request_hash(source, schema_version=schema_version)
        comparison_id = self._comparison_id(normalized_request_id)

        with self._comparison_lock(comparison_id):
            return self._compare_locked(
                source=source,
                client_request_id=normalized_request_id,
                request_hash=request_hash,
                comparison_id=comparison_id,
                request_started=request_started,
            )

    def _compare_locked(
        self,
        *,
        source: _SourceContext,
        client_request_id: str,
        request_hash: str,
        comparison_id: str,
        request_started: float,
    ) -> dict[str, Any]:
        manifest_path = self.comparisons_root / f"{comparison_id}.json"
        manifest = self._load_manifest(manifest_path)
        if manifest is not None and manifest.get("request_hash") != request_hash:
            raise AutoModelError(
                "auto_model_request_conflict",
                "client_request_id was already used for another auto-model source.",
                status_code=409,
            )

        existing_session: dict[str, Any] | None = None
        try:
            existing = self.history_store.find_edit_request_idempotent(
                namespace="auto_model",
                client_request_id=client_request_id,
                request_hash=request_hash,
            )
        except EditHistoryConflict as exc:
            raise AutoModelError(
                "auto_model_request_conflict",
                str(exc),
                status_code=409,
            ) from exc
        if existing is not None:
            existing_session = existing[0]

        effective_source = self._materialize_source_context(
            source=source,
            manifest=manifest,
            existing_session=existing_session,
            comparison_id=comparison_id,
        )
        assert effective_source.session_id is not None
        assert effective_source.original_saved_path is not None
        assert effective_source.base_saved_path is not None
        assert effective_source.original_path is not None
        assert effective_source.base_path is not None

        source_fingerprint = self._source_fingerprint(effective_source)
        existing_records = self._comparison_records(
            effective_source.session_id,
            comparison_id,
        )
        existing_by_model = {
            str(record.get("auto_model", {}).get("model_key") or ""): record
            for record in existing_records
            if isinstance(record.get("auto_model"), Mapping)
        }

        candidates: dict[str, dict[str, Any]] = {}
        new_records: list[dict[str, Any]] = []
        new_result_dirs: list[Path] = []
        model_timings: dict[str, float] = {}
        for model_key in AUTO_MODEL_KEYS:
            persisted = existing_by_model.get(model_key)
            if persisted is not None:
                candidates[model_key] = self._success_candidate(
                    persisted,
                    idempotent_replay=True,
                )
                continue
            adapter = self.adapters[model_key]
            edit_id = self.history_store.new_edit_id()
            result_dir = self.results_root / effective_source.session_id / edit_id
            result_path = result_dir / "result.png"
            temporary_path = result_dir / ".result.tmp.png"
            model_started = time.perf_counter()
            try:
                result_dir.mkdir(parents=True, exist_ok=False)
                result = adapter.enhance(effective_source.base_path, temporary_path)
                self._validate_output(
                    source_path=effective_source.base_path,
                    result_path=temporary_path,
                    model_key=model_key,
                )
                os.replace(temporary_path, result_path)
                result_saved_path = self._relative_backend_path(result_path)
                record = self._build_record(
                    source=effective_source,
                    source_fingerprint=source_fingerprint,
                    result=result,
                    result_saved_path=result_saved_path,
                    edit_id=edit_id,
                    comparison_id=comparison_id,
                    client_request_id=client_request_id,
                    request_hash=request_hash,
                    asset_identity=adapter.asset_identity(),
                )
                new_records.append(record)
                new_result_dirs.append(result_dir)
                candidates[model_key] = self._success_candidate(
                    record,
                    idempotent_replay=False,
                )
            except Exception as exc:
                shutil.rmtree(result_dir, ignore_errors=True)
                candidates[model_key] = self._error_candidate(
                    model_key,
                    exc,
                )
            model_timings[model_key] = round(
                (time.perf_counter() - model_started) * 1000.0,
                3,
            )

        if new_records:
            try:
                self.history_store.save_edits_atomic(new_records)
            except Exception:
                for directory in new_result_dirs:
                    shutil.rmtree(directory, ignore_errors=True)
                if source.upload_bytes is not None and not existing_records:
                    self._cleanup_new_upload(effective_source)
                raise

        successful_count = sum(
            candidate.get("status") == "success"
            for candidate in candidates.values()
        )
        if successful_count == 0 and source.upload_bytes is not None:
            self._cleanup_new_upload(effective_source)

        status = (
            "success"
            if successful_count == len(AUTO_MODEL_KEYS)
            else "partial_success"
            if successful_count
            else "error"
        )
        response = {
            "schema_version": AUTO_MODEL_COMPARISON_SCHEMA_VERSION,
            "comparison_id": comparison_id,
            "status": status,
            "session_id": effective_source.session_id,
            "source_edit_id": effective_source.source_edit_id,
            "parent_edit_id": effective_source.parent_edit_id,
            "source_history_fingerprint": source_fingerprint,
            "source": {
                "saved_path": effective_source.base_saved_path,
                "url": f"/{effective_source.base_saved_path}",
                "sha256": effective_source.source_sha256,
            },
            "candidates": candidates,
            "execution_mode": "sequential",
            "idempotent_replay": bool(existing_records) and not new_records,
            "batch_timings_ms": {
                "models": model_timings,
                "request_total": round(
                    (time.perf_counter() - request_started) * 1000.0,
                    3,
                ),
            },
        }
        manifest_payload = {
            "schema_version": AUTO_MODEL_COMPARISON_SCHEMA_VERSION,
            "comparison_id": comparison_id,
            "client_request_id": client_request_id,
            "request_hash": request_hash,
            "session_id": effective_source.session_id,
            "source_edit_id": effective_source.source_edit_id,
            "parent_edit_id": effective_source.parent_edit_id,
            "original_saved_path": effective_source.original_saved_path,
            "base_saved_path": effective_source.base_saved_path,
            "source_sha256": effective_source.source_sha256,
            "source_history_fingerprint": source_fingerprint,
            "status": status,
            "candidates": {
                key: {
                    "status": value.get("status"),
                    "edit_id": value.get("edit_id"),
                    "error": value.get("error"),
                }
                for key, value in candidates.items()
            },
        }
        try:
            self._write_manifest_atomic(manifest_path, manifest_payload)
        except OSError as exc:
            response["warning_flags"] = ["comparison_manifest_write_failed"]
            response["manifest_error"] = str(exc)
        return response

    def _resolve_source_request(
        self,
        *,
        original_bytes: bytes | None,
        original_filename: str | None,
        session_id: str | None,
        source_edit_id: str | None,
    ) -> _SourceContext:
        normalized_session = str(session_id or "").strip() or None
        normalized_source = str(source_edit_id or "").strip() or ORIGINAL_SOURCE_ID
        if normalized_session is None:
            if normalized_source != ORIGINAL_SOURCE_ID:
                raise AutoModelError(
                    "auto_model_source_invalid",
                    "source_edit_id requires session_id.",
                    status_code=400,
                )
            if original_bytes is None:
                raise AutoModelError(
                    "auto_model_original_required",
                    "Upload an original image or select an existing session version.",
                    status_code=400,
                )
            suffix = self._validate_upload(original_bytes, original_filename)
            return _SourceContext(
                session_id=None,
                source_edit_id=ORIGINAL_SOURCE_ID,
                parent_edit_id=None,
                original_saved_path=None,
                base_saved_path=None,
                original_path=None,
                base_path=None,
                source_sha256=_sha256_bytes(original_bytes),
                upload_bytes=original_bytes,
                upload_suffix=suffix,
            )
        if original_bytes is not None:
            raise AutoModelError(
                "auto_model_source_ambiguous",
                "Do not upload an image when selecting an existing session source.",
                status_code=400,
            )
        try:
            session = self.history_store.load_session(normalized_session)
        except EditHistoryNotFound as exc:
            raise AutoModelError(
                "auto_model_session_not_found",
                str(exc),
                status_code=404,
            ) from exc
        edits = session.get("edits")
        if not isinstance(edits, list) or not edits:
            raise AutoModelError(
                "auto_model_original_not_found",
                "The selected session has no committed original image.",
                status_code=404,
            )
        if normalized_source == ORIGINAL_SOURCE_ID:
            original_saved = str(edits[0].get("original_image_path") or "")
            original_path = self._safe_backend_path(original_saved, "session original")
            return _SourceContext(
                session_id=normalized_session,
                source_edit_id=ORIGINAL_SOURCE_ID,
                parent_edit_id=None,
                original_saved_path=original_saved,
                base_saved_path=original_saved,
                original_path=original_path,
                base_path=original_path,
                source_sha256=_sha256_file(original_path),
                upload_bytes=None,
                upload_suffix=None,
            )
        try:
            source_record = self.history_store.find_edit(
                normalized_session,
                normalized_source,
            )
        except EditHistoryNotFound as exc:
            raise AutoModelError(
                "auto_model_source_not_found",
                str(exc),
                status_code=404,
            ) from exc
        original_saved = str(source_record.get("original_image_path") or "")
        base_saved = str(source_record.get("result_image_path") or "")
        original_path = self._safe_backend_path(original_saved, "source original")
        base_path = self._safe_backend_path(base_saved, "source result")
        return _SourceContext(
            session_id=normalized_session,
            source_edit_id=normalized_source,
            parent_edit_id=normalized_source,
            original_saved_path=original_saved,
            base_saved_path=base_saved,
            original_path=original_path,
            base_path=base_path,
            source_sha256=_sha256_file(base_path),
            upload_bytes=None,
            upload_suffix=None,
        )

    def _materialize_source_context(
        self,
        *,
        source: _SourceContext,
        manifest: dict[str, Any] | None,
        existing_session: dict[str, Any] | None,
        comparison_id: str,
    ) -> _SourceContext:
        if source.upload_bytes is None:
            return source
        session_id = (
            str(manifest.get("session_id") or "")
            if manifest is not None
            else str(existing_session.get("session_id") or "")
            if existing_session is not None
            else self.history_store.new_session_id()
        )
        original_saved = (
            str(manifest.get("original_saved_path") or "")
            if manifest is not None
            else ""
        )
        if not original_saved and existing_session is not None:
            edits = existing_session.get("edits")
            if isinstance(edits, list) and edits:
                original_saved = str(edits[0].get("original_image_path") or "")
        if not original_saved:
            original_saved = (
                self.uploads_root
                / session_id
                / comparison_id
                / f"original{source.upload_suffix or '.png'}"
            ).relative_to(self.backend_dir).as_posix()
        original_path = self._safe_backend_destination(original_saved)
        if original_path.is_file():
            if _sha256_file(original_path) != source.source_sha256:
                raise AutoModelError(
                    "auto_model_source_hash_mismatch",
                    "Stored comparison original no longer matches the request.",
                    status_code=409,
                )
        else:
            self._write_bytes_atomic(original_path, source.upload_bytes)
        return _SourceContext(
            session_id=session_id,
            source_edit_id=ORIGINAL_SOURCE_ID,
            parent_edit_id=None,
            original_saved_path=original_saved,
            base_saved_path=original_saved,
            original_path=original_path,
            base_path=original_path,
            source_sha256=source.source_sha256,
            upload_bytes=source.upload_bytes,
            upload_suffix=source.upload_suffix,
        )

    def _build_record(
        self,
        *,
        source: _SourceContext,
        source_fingerprint: str,
        result: AutoModelResult,
        result_saved_path: str,
        edit_id: str,
        comparison_id: str,
        client_request_id: str,
        request_hash: str,
        asset_identity: dict[str, Any],
    ) -> dict[str, Any]:
        assert source.session_id is not None
        assert source.original_saved_path is not None
        assert source.base_saved_path is not None
        auto_model = {
            "schema_version": AUTO_MODEL_HISTORY_SCHEMA_VERSION,
            "comparison_id": comparison_id,
            "model_key": result.model_key,
            "model_family": result.model_family,
            "asset_identity": asset_identity,
            "source_edit_id": source.source_edit_id,
            "source_history_fingerprint": source_fingerprint,
            "source_sha256": source.source_sha256,
            "client_request_id": client_request_id,
            "request_hash": request_hash,
            "runtime_metadata": result.runtime_metadata,
            "timings_ms": result.timings_ms,
            "warning_flags": list(result.warning_flags),
        }
        visual_anchor = {
            "schema_version": VISUAL_ANCHOR_SCHEMA_VERSION,
            "kind": "auto_model",
            "anchor_edit_id": edit_id,
            "comparison_id": comparison_id,
            "model_key": result.model_key,
        }
        return self.history_store.build_record(
            session_id=source.session_id,
            edit_id=edit_id,
            parent_edit_id=source.parent_edit_id,
            edit_mode="auto_model",
            original_image_path=source.original_saved_path,
            base_image_path=source.base_saved_path,
            result_image_path=result_saved_path,
            reference_image_path=None,
            user_prompt="",
            resolved_intent="auto_enhance",
            parameters={},
            engine="auto_model",
            edit_plan={
                "schema_version": "auto_model_edit_plan_v1",
                "type": "auto_model",
                "model_key": result.model_key,
            },
            engine_parameters={},
            mask_info=None,
            explanation=(
                "Created an immutable automatic enhancement visual anchor "
                f"with {result.model_key}."
            ),
            parser_source="auto_model_adapter",
            fallback_reason=None,
            processing_timings=result.timings_ms,
            auto_model=auto_model,
            visual_anchor=visual_anchor,
        )

    @staticmethod
    def _success_candidate(
        record: Mapping[str, Any],
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        result_saved = str(record.get("result_image_path") or "")
        metadata = record.get("auto_model")
        return {
            "status": "success",
            "model_key": (
                metadata.get("model_key")
                if isinstance(metadata, Mapping)
                else None
            ),
            "edit_id": record.get("edit_id"),
            "parent_edit_id": record.get("parent_edit_id"),
            "result_saved_path": result_saved,
            "result_url": f"/{result_saved}",
            "auto_model": dict(metadata) if isinstance(metadata, Mapping) else None,
            "visual_anchor": record.get("visual_anchor"),
            "idempotent_replay": idempotent_replay,
        }

    @staticmethod
    def _error_candidate(model_key: str, exc: Exception) -> dict[str, Any]:
        error = (
            exc
            if isinstance(exc, AutoModelError)
            else AutoModelError(
                "auto_model_inference_failed",
                f"Automatic enhancement failed: {exc}",
                model_key=model_key,
                status_code=503,
                retryable=True,
                details={"exception_type": type(exc).__name__},
            )
        )
        return {
            "status": "error",
            "model_key": model_key,
            "error": error.as_dict(),
        }

    def _comparison_records(
        self,
        session_id: str,
        comparison_id: str,
    ) -> list[dict[str, Any]]:
        try:
            session = self.history_store.load_session(session_id)
        except EditHistoryNotFound:
            return []
        result: list[dict[str, Any]] = []
        for record in session.get("edits", []):
            if not isinstance(record, dict):
                continue
            metadata = record.get("auto_model")
            if (
                isinstance(metadata, Mapping)
                and metadata.get("comparison_id") == comparison_id
            ):
                result.append(record)
        return result

    @staticmethod
    def _request_hash(
        source: _SourceContext,
        *,
        schema_version: str,
    ) -> str:
        payload = {
            "schema_version": schema_version,
            "session_id": source.session_id,
            "source_edit_id": source.source_edit_id,
            "source_sha256": source.source_sha256,
            "model_keys": list(AUTO_MODEL_KEYS),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _source_fingerprint(source: _SourceContext) -> str:
        payload = {
            "session_id": source.session_id,
            "source_edit_id": source.source_edit_id,
            "parent_edit_id": source.parent_edit_id,
            "base_image_path": source.base_saved_path,
            "source_sha256": source.source_sha256,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _comparison_id(client_request_id: str) -> str:
        digest = hashlib.sha256(client_request_id.encode("utf-8")).hexdigest()
        return f"comparison_{digest[:32]}"

    def _validate_output(
        self,
        *,
        source_path: Path,
        result_path: Path,
        model_key: str,
    ) -> None:
        if not result_path.is_file() or result_path.stat().st_size == 0:
            raise AutoModelError(
                "auto_model_output_missing",
                "Model did not produce an output image.",
                model_key=model_key,
                status_code=503,
                retryable=True,
            )
        try:
            with Image.open(source_path) as source_image:
                source_size = ImageOps.exif_transpose(source_image).size
            with Image.open(result_path) as output_image:
                output_image.load()
                output_size = output_image.size
                output_image.convert("RGB").getextrema()
        except (OSError, UnidentifiedImageError) as exc:
            raise AutoModelError(
                "auto_model_output_invalid",
                f"Model output cannot be decoded: {exc}",
                model_key=model_key,
                status_code=503,
                retryable=True,
            ) from exc
        if output_size != source_size:
            raise AutoModelError(
                "auto_model_output_size_mismatch",
                f"Model output size {output_size} does not match source {source_size}.",
                model_key=model_key,
                status_code=503,
                retryable=True,
            )

    def _validate_upload(self, data: bytes, filename: str | None) -> str:
        if not data:
            raise AutoModelError(
                "auto_model_original_empty",
                "Uploaded original image is empty.",
                status_code=400,
            )
        if len(data) > MAX_UPLOAD_BYTES:
            raise AutoModelError(
                "auto_model_original_too_large",
                "Uploaded original exceeds the 40 MB limit.",
                status_code=413,
            )
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as image:
                width, height = ImageOps.exif_transpose(image).size
                image_format = str(image.format or "").lower()
        except (OSError, UnidentifiedImageError) as exc:
            raise AutoModelError(
                "auto_model_original_invalid",
                f"Uploaded original cannot be decoded: {exc}",
                status_code=400,
            ) from exc
        if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
            raise AutoModelError(
                "auto_model_original_dimensions_invalid",
                "Uploaded original has unsupported dimensions.",
                status_code=400,
            )
        suffix = Path(filename or "").suffix.lower()
        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            suffix = {
                "jpeg": ".jpg",
                "png": ".png",
                "webp": ".webp",
                "bmp": ".bmp",
                "tiff": ".tiff",
            }.get(image_format, ".png")
        return suffix

    def _safe_backend_path(self, relative_path: str, label: str) -> Path:
        if not relative_path:
            raise AutoModelError(
                "auto_model_source_path_missing",
                f"Selected {label} path is missing.",
                status_code=404,
            )
        path = self._safe_backend_destination(relative_path)
        if not path.is_file():
            raise AutoModelError(
                "auto_model_source_file_missing",
                f"Selected {label} file does not exist.",
                status_code=404,
                details={"path": relative_path},
            )
        return path

    def _safe_backend_destination(self, relative_path: str) -> Path:
        candidate = (self.backend_dir / relative_path).resolve()
        try:
            candidate.relative_to(self.backend_dir)
        except ValueError as exc:
            raise AutoModelError(
                "auto_model_source_path_invalid",
                "Auto-model path escapes backend storage.",
                status_code=400,
            ) from exc
        return candidate

    def _relative_backend_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.backend_dir).as_posix()
        except ValueError as exc:
            raise AutoModelError(
                "auto_model_result_path_invalid",
                "Auto-model result path escapes backend storage.",
                status_code=500,
            ) from exc

    def _cleanup_new_upload(self, source: _SourceContext) -> None:
        if source.original_path is None:
            return
        parent = source.original_path.parent
        try:
            source.original_path.unlink(missing_ok=True)
        except OSError:
            return
        try:
            parent.rmdir()
            parent.parent.rmdir()
        except OSError:
            pass

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AutoModelError(
                "auto_model_manifest_invalid",
                "Stored comparison manifest is unreadable.",
                status_code=409,
                details={"path": str(path), "error": str(exc)},
            ) from exc
        if not isinstance(payload, dict):
            raise AutoModelError(
                "auto_model_manifest_invalid",
                "Stored comparison manifest is not an object.",
                status_code=409,
            )
        return payload

    @staticmethod
    def _write_manifest_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_bytes_atomic(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _comparison_lock(self, comparison_id: str) -> threading.Lock:
        if COMPARISON_ID_PATTERN.fullmatch(comparison_id) is None:
            raise AutoModelError(
                "auto_model_comparison_id_invalid",
                "Invalid auto-model comparison identifier.",
                status_code=400,
            )
        with self._locks_guard:
            return self._comparison_locks.setdefault(comparison_id, threading.Lock())

    @staticmethod
    def _validate_client_request_id(value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized or len(normalized) > 128:
            raise AutoModelError(
                "auto_model_client_request_id_invalid",
                "client_request_id must contain 1 to 128 characters.",
                status_code=400,
            )
        return normalized


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()
