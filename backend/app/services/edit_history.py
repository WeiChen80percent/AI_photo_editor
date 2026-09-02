from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class EditHistoryError(ValueError):
    pass


class EditHistoryNotFound(EditHistoryError):
    pass


class EditHistoryInvalidIdentifier(EditHistoryError):
    pass


class EditHistoryConflict(EditHistoryError):
    pass


_SESSION_ID_PATTERN = re.compile(r"^session_[0-9a-f]{32}$")
_EDIT_ID_PATTERN = re.compile(r"^edit_[0-9a-f]{32}$")


class EditHistoryStore:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self._locks_guard = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}
        self._idempotency_lock = threading.Lock()

    def new_session_id(self) -> str:
        return f"session_{uuid4().hex}"

    def new_edit_id(self) -> str:
        return f"edit_{uuid4().hex}"

    def load_session(self, session_id: str) -> dict[str, Any]:
        session_path = self._session_path(session_id)
        if not session_path.exists():
            raise EditHistoryNotFound(f"Unknown edit session: {session_id}")

        with open(session_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_edit(self, record: dict[str, Any]) -> dict[str, Any]:
        session_id = self._validate_session_id(record.get("session_id"))
        self._validate_edit_id(record.get("edit_id"))
        parent_edit_id = record.get("parent_edit_id")
        if parent_edit_id is not None:
            self._validate_edit_id(parent_edit_id)
        with self._session_lock(session_id):
            try:
                session = self.load_session(session_id)
            except EditHistoryNotFound:
                session = {
                    "session_id": session_id,
                    "created_at": record["created_at"],
                    "edits": [],
                }

            session["updated_at"] = record["created_at"]
            session["edits"].append(record)
            self._write_session_atomic(session_id, session)
            return session

    def save_edits_atomic(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Append one sibling batch with a single atomic session replace."""

        if not records:
            raise EditHistoryConflict("At least one edit record is required")
        session_ids = {
            self._validate_session_id(record.get("session_id"))
            for record in records
        }
        if len(session_ids) != 1:
            raise EditHistoryConflict(
                "Atomic edit records must belong to the same session"
            )
        session_id = next(iter(session_ids))
        edit_ids = [
            self._validate_edit_id(record.get("edit_id"))
            for record in records
        ]
        if len(set(edit_ids)) != len(edit_ids):
            raise EditHistoryConflict("Atomic edit records contain duplicate edit IDs")
        for record in records:
            parent_edit_id = record.get("parent_edit_id")
            if parent_edit_id is not None:
                self._validate_edit_id(parent_edit_id)

        with self._session_lock(session_id):
            try:
                session = self.load_session(session_id)
            except EditHistoryNotFound:
                session = {
                    "session_id": session_id,
                    "created_at": records[0]["created_at"],
                    "edits": [],
                }
            existing_ids = {
                str(record.get("edit_id") or "")
                for record in session.get("edits", [])
                if isinstance(record, dict)
            }
            overlap = existing_ids.intersection(edit_ids)
            if overlap:
                raise EditHistoryConflict(
                    "Atomic edit batch contains an existing edit ID"
                )
            valid_parent_ids = existing_ids.union(edit_ids)
            for record in records:
                parent_edit_id = record.get("parent_edit_id")
                if parent_edit_id is not None and parent_edit_id not in valid_parent_ids:
                    raise EditHistoryConflict(
                        f"Atomic edit parent is missing: {parent_edit_id}"
                    )
            session.setdefault("edits", []).extend(records)
            session["updated_at"] = records[-1]["created_at"]
            self._write_session_atomic(session_id, session)
            return session

    def save_edit_idempotent(
        self,
        record: dict[str, Any],
        *,
        client_request_id: str,
        plan_hash: str,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Append once for one Photo Git client request.

        Returns ``(session, persisted_record, created)``. Repeating the same
        client request and plan returns the original record without appending.
        Reusing a request id for a different plan is rejected.
        """

        session_id = self._validate_session_id(record.get("session_id"))
        self._validate_edit_id(record.get("edit_id"))
        parent_edit_id = record.get("parent_edit_id")
        if parent_edit_id is not None:
            self._validate_edit_id(parent_edit_id)
        normalized_request_id = str(client_request_id or "").strip()
        normalized_plan_hash = str(plan_hash or "").strip()
        if not normalized_request_id or not normalized_plan_hash:
            raise EditHistoryConflict(
                "Photo Git idempotency metadata is required"
            )

        with self._session_lock(session_id):
            try:
                session = self.load_session(session_id)
            except EditHistoryNotFound:
                session = {
                    "session_id": session_id,
                    "created_at": record["created_at"],
                    "edits": [],
                }
            for existing in session.get("edits", []):
                metadata = (
                    existing.get("photo_git")
                    if isinstance(existing, dict)
                    else None
                )
                if (
                    not isinstance(metadata, dict)
                    or str(metadata.get("client_request_id") or "")
                    != normalized_request_id
                ):
                    continue
                if str(metadata.get("plan_hash") or "") != normalized_plan_hash:
                    raise EditHistoryConflict(
                        "client_request_id was already used for another Photo Git plan"
                    )
                return session, existing, False

            session["updated_at"] = record["created_at"]
            session.setdefault("edits", []).append(record)
            self._write_session_atomic(session_id, session)
            return session, record, True

    def find_edit_request_idempotent(
        self,
        *,
        namespace: str,
        client_request_id: str,
        request_hash: str,
        scope_session_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Find one previously committed request across edit sessions.

        Contract uploads can be retried before the client has received a
        session id, so lookup cannot be limited to a caller-supplied session.
        Reusing the same request id with different immutable inputs is a
        conflict instead of silently returning unrelated output.
        """

        normalized_namespace = self._validate_metadata_namespace(namespace)
        normalized_request_id = self._require_idempotency_text(
            client_request_id,
            "client_request_id",
        )
        normalized_hash = self._require_idempotency_text(
            request_hash,
            "request_hash",
        )
        normalized_scope_session_id = (
            self._validate_session_id(scope_session_id)
            if scope_session_id is not None
            else None
        )
        with self._idempotency_lock:
            return self._find_edit_request_idempotent_locked(
                namespace=normalized_namespace,
                client_request_id=normalized_request_id,
                request_hash=normalized_hash,
                scope_session_id=normalized_scope_session_id,
            )

    def save_edit_request_idempotent(
        self,
        record: dict[str, Any],
        *,
        namespace: str,
        client_request_id: str,
        request_hash: str,
        scope_session_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Atomically append one namespaced request across all sessions."""

        session_id = self._validate_session_id(record.get("session_id"))
        self._validate_edit_id(record.get("edit_id"))
        parent_edit_id = record.get("parent_edit_id")
        if parent_edit_id is not None:
            self._validate_edit_id(parent_edit_id)
        normalized_namespace = self._validate_metadata_namespace(namespace)
        normalized_request_id = self._require_idempotency_text(
            client_request_id,
            "client_request_id",
        )
        normalized_hash = self._require_idempotency_text(
            request_hash,
            "request_hash",
        )
        normalized_scope_session_id = (
            self._validate_session_id(scope_session_id)
            if scope_session_id is not None
            else None
        )
        if (
            normalized_scope_session_id is not None
            and normalized_scope_session_id != session_id
        ):
            raise EditHistoryConflict(
                "Idempotency session scope does not match the record"
            )
        metadata = record.get(normalized_namespace)
        if not isinstance(metadata, dict):
            raise EditHistoryConflict(
                f"{normalized_namespace} idempotency metadata is required"
            )
        if (
            str(metadata.get("client_request_id") or "")
            != normalized_request_id
            or self._metadata_request_hash(metadata) != normalized_hash
        ):
            raise EditHistoryConflict(
                f"{normalized_namespace} idempotency metadata does not match request"
            )

        with self._idempotency_lock:
            existing = self._find_edit_request_idempotent_locked(
                namespace=normalized_namespace,
                client_request_id=normalized_request_id,
                request_hash=normalized_hash,
                scope_session_id=normalized_scope_session_id,
            )
            if existing is not None:
                session, persisted = existing
                return session, persisted, False
            with self._session_lock(session_id):
                try:
                    session = self.load_session(session_id)
                except EditHistoryNotFound:
                    session = {
                        "session_id": session_id,
                        "created_at": record["created_at"],
                        "edits": [],
                    }
                session["updated_at"] = record["created_at"]
                session.setdefault("edits", []).append(record)
                self._write_session_atomic(session_id, session)
                return session, record, True

    def find_edit(self, session_id: str, edit_id: str) -> dict[str, Any]:
        edit_id = self._validate_edit_id(edit_id)
        session = self.load_session(session_id)
        for edit in session["edits"]:
            if edit["edit_id"] == edit_id:
                return edit
        raise EditHistoryNotFound(
            f"Unknown edit in session {session_id}: {edit_id}"
        )

    def build_record(
        self,
        *,
        session_id: str,
        edit_id: str,
        parent_edit_id: str | None,
        edit_mode: str,
        original_image_path: str,
        base_image_path: str,
        result_image_path: str,
        user_prompt: str,
        resolved_intent: str,
        parameters: dict[str, Any],
        explanation: str,
        parser_source: str,
        fallback_reason: str | None,
        reference_image_path: str | None = None,
        preset_name: str | None = None,
        engine: str | None = None,
        edit_plan: dict[str, Any] | None = None,
        engine_parameters: dict[str, Any] | None = None,
        mask_info: dict[str, Any] | None = None,
        manual_source_edit_id: str | None = None,
        parameter_overrides: dict[str, Any] | None = None,
        processing_timings: dict[str, Any] | None = None,
        adaptive: dict[str, Any] | None = None,
        style: dict[str, Any] | None = None,
        photo_git: dict[str, Any] | None = None,
        edit_contract: dict[str, Any] | None = None,
        manual: dict[str, Any] | None = None,
        command: dict[str, Any] | None = None,
        auto_model: dict[str, Any] | None = None,
        visual_anchor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "edit_id": edit_id,
            "parent_edit_id": parent_edit_id,
            "edit_mode": edit_mode,
            "original_image_path": original_image_path,
            "base_image_path": base_image_path,
            "result_image_path": result_image_path,
            "reference_image_path": reference_image_path,
            "user_prompt": user_prompt,
            "resolved_intent": resolved_intent,
            "engine": engine,
            "edit_plan": edit_plan,
            "engine_parameters": engine_parameters,
            "mask_info": mask_info,
            "manual_source_edit_id": manual_source_edit_id,
            "parameter_overrides": parameter_overrides,
            "processing_timings": processing_timings,
            "adaptive": adaptive,
            "style": style,
            "photo_git": photo_git,
            "edit_contract": edit_contract,
            "manual": manual,
            "command": command,
            "auto_model": auto_model,
            "visual_anchor": visual_anchor,
            "parameters": parameters,
            "preset_name": preset_name,
            "explanation": explanation,
            "parser_source": parser_source,
            "fallback_reason": fallback_reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _session_path(self, session_id: str) -> Path:
        normalized = self._validate_session_id(session_id)
        return self.storage_dir / f"{normalized}.json"

    def _write_session_atomic(
        self,
        session_id: str,
        session: dict[str, Any],
    ) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        session_path = self._session_path(session_id)
        temp_path = session_path.with_suffix(".json.tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
            temp_path.replace(session_path)
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

    @staticmethod
    def _validate_session_id(session_id: Any) -> str:
        normalized = str(session_id or "").strip()
        if _SESSION_ID_PATTERN.fullmatch(normalized) is None:
            raise EditHistoryInvalidIdentifier("Invalid edit session_id format")
        return normalized

    @staticmethod
    def _validate_edit_id(edit_id: Any) -> str:
        normalized = str(edit_id or "").strip()
        if _EDIT_ID_PATTERN.fullmatch(normalized) is None:
            raise EditHistoryInvalidIdentifier("Invalid edit_id format")
        return normalized

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._session_locks.setdefault(session_id, threading.Lock())

    def _find_edit_request_idempotent_locked(
        self,
        *,
        namespace: str,
        client_request_id: str,
        request_hash: str,
        scope_session_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not self.storage_dir.exists():
            return None
        session_paths = (
            [self._session_path(scope_session_id)]
            if scope_session_id is not None
            else sorted(self.storage_dir.glob("session_*.json"))
        )
        for session_path in session_paths:
            if not session_path.exists():
                continue
            session_id = session_path.stem
            if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
                continue
            session = self.load_session(session_id)
            for existing in session.get("edits", []):
                metadata = (
                    existing.get(namespace)
                    if isinstance(existing, dict)
                    else None
                )
                if (
                    not isinstance(metadata, dict)
                    or str(metadata.get("client_request_id") or "")
                    != client_request_id
                ):
                    continue
                if self._metadata_request_hash(metadata) != request_hash:
                    raise EditHistoryConflict(
                        "client_request_id was already used for another "
                        f"{namespace} request"
                    )
                return session, existing
        return None

    @staticmethod
    def _metadata_request_hash(metadata: dict[str, Any]) -> str:
        """Read the generic hash while remaining compatible with contract v1."""

        return str(
            metadata.get("request_hash")
            or metadata.get("contract_hash")
            or ""
        )

    @staticmethod
    def _validate_metadata_namespace(namespace: Any) -> str:
        normalized = str(namespace or "").strip()
        if re.fullmatch(r"[a-z][a-z0-9_]*", normalized) is None:
            raise EditHistoryConflict("Invalid idempotency namespace")
        return normalized

    @staticmethod
    def _require_idempotency_text(value: Any, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise EditHistoryConflict(f"{field_name} is required")
        return normalized
