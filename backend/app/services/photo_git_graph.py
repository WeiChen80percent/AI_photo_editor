from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


VIRTUAL_ORIGINAL_ID = "original"


class PhotoGitGraphError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PhotoGitGraph:
    session_id: str
    records: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        index: dict[str, dict[str, Any]] = {}
        for raw in self.records:
            record = dict(raw)
            edit_id = str(record.get("edit_id") or "")
            if not edit_id or edit_id in index:
                raise PhotoGitGraphError(
                    "photo_git_history_invalid",
                    "History contains a missing or duplicate edit identifier.",
                )
            if str(record.get("session_id") or "") != self.session_id:
                raise PhotoGitGraphError(
                    "photo_git_history_invalid",
                    "History contains an edit from a different session.",
                )
            index[edit_id] = record
        object.__setattr__(self, "_index", index)

    @classmethod
    def from_session(cls, session: Mapping[str, Any]) -> "PhotoGitGraph":
        session_id = str(session.get("session_id") or "")
        edits = session.get("edits")
        if not session_id or not isinstance(edits, list):
            raise PhotoGitGraphError(
                "photo_git_history_invalid",
                "Session history is missing its identifier or edits.",
            )
        records = tuple(dict(item) for item in edits if isinstance(item, Mapping))
        if len(records) != len(edits):
            raise PhotoGitGraphError(
                "photo_git_history_invalid",
                "Session history contains an invalid edit record.",
            )
        return cls(session_id=session_id, records=records)

    def record(self, edit_id: str) -> dict[str, Any]:
        try:
            return dict(self._index[edit_id])
        except KeyError as exc:
            raise PhotoGitGraphError(
                "photo_git_version_not_found",
                f"Unknown Photo Git version: {edit_id}",
            ) from exc

    def parent_id(self, edit_id: str) -> str | None:
        record = self.record(edit_id)
        parent = record.get("parent_edit_id")
        return str(parent) if parent else None

    def lineage(self, edit_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        current: str | None = edit_id
        while current is not None:
            if current in seen:
                raise PhotoGitGraphError(
                    "photo_git_history_cycle",
                    "History contains a parent cycle.",
                )
            seen.add(current)
            record = self.record(current)
            result.append(record)
            parent = record.get("parent_edit_id")
            if parent is not None and str(parent) not in self._index:
                raise PhotoGitGraphError(
                    "photo_git_parent_missing",
                    f"History parent is missing: {parent}",
                )
            current = str(parent) if parent else None
        result.reverse()
        return result

    def is_ancestor(self, ancestor_edit_id: str, edit_id: str) -> bool:
        return any(
            str(record.get("edit_id")) == ancestor_edit_id
            for record in self.lineage(edit_id)
        )

    def common_ancestor(self, target_edit_id: str, source_edit_id: str) -> str:
        target_lineage = self.lineage(target_edit_id)
        source_lineage = self.lineage(source_edit_id)
        source_ids = {
            str(record.get("edit_id")) for record in source_lineage
        }
        common = [
            str(record.get("edit_id"))
            for record in target_lineage
            if str(record.get("edit_id")) in source_ids
        ]
        if common:
            return common[-1]

        target_original = str(
            target_lineage[0].get("original_image_path") or ""
        )
        source_original = str(
            source_lineage[0].get("original_image_path") or ""
        )
        if target_original and target_original == source_original:
            return VIRTUAL_ORIGINAL_ID
        raise PhotoGitGraphError(
            "photo_git_common_ancestor_missing",
            "The selected versions do not share the same original image.",
        )

    def original_path(self, edit_id: str) -> str:
        lineage = self.lineage(edit_id)
        path = str(lineage[0].get("original_image_path") or "")
        if not path:
            raise PhotoGitGraphError(
                "photo_git_original_missing",
                "The selected version has no reusable original image.",
            )
        for record in lineage:
            if str(record.get("original_image_path") or "") != path:
                raise PhotoGitGraphError(
                    "photo_git_original_mismatch",
                    "A branch contains edits from different original images.",
                )
        return path
