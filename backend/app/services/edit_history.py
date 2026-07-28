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


_SESSION_ID_PATTERN = re.compile(r"^session_[0-9a-f]{32}$")
_EDIT_ID_PATTERN = re.compile(r"^edit_[0-9a-f]{32}$")


class EditHistoryStore:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir
        self._locks_guard = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}

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
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            session_path = self._session_path(session_id)
            temp_path = session_path.with_suffix(".json.tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
            temp_path.replace(session_path)
            return session

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
