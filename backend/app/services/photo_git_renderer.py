from __future__ import annotations

import json
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.opencv_processor import create_opencv_composite_result
from app.services.semantic_mask_service import SemanticTargetNotFoundError


PHOTO_GIT_PREVIEW_CACHE_VERSION = "photo_git_preview_v1"


class PhotoGitRenderError(ValueError):
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


class PhotoGitRenderer:
    def __init__(
        self,
        *,
        backend_dir: Path,
        preview_root: Path,
        results_root: Path,
    ):
        self.backend_dir = Path(backend_dir).resolve()
        self.preview_root = Path(preview_root)
        self.results_root = Path(results_root)

    def preview(
        self,
        *,
        session_id: str,
        plan: Mapping[str, Any],
    ) -> dict[str, Any]:
        plan_hash = str(plan.get("plan_hash") or "")
        preview_dir = self.preview_root / session_id / plan_hash
        result_path = preview_dir / "result.png"
        metadata_path = preview_dir / "metadata.json"
        cached = self._load_cache(
            metadata_path=metadata_path,
            result_path=result_path,
            plan_hash=plan_hash,
        )
        if cached is not None:
            response = dict(cached)
            response["preview_cache_hit"] = True
            return response

        try:
            rendered = self._render_plan(
                plan=plan,
                result_path=result_path,
            )
            response = {
                "preview_cache_version": PHOTO_GIT_PREVIEW_CACHE_VERSION,
                "preview_cache_hit": False,
                "plan_hash": plan_hash,
                "result_saved_path": self._relative_path(result_path),
                "result_url": f"/{self._relative_path(result_path)}",
                "target_edit_id": plan["target_edit_id"],
                "target_result_path": None,
                "recipe": plan["recipe"],
                "processing_timings": rendered.get("timings_ms"),
                "mask_info": rendered.get("mask_info"),
                "rendered_scopes": rendered.get("scopes"),
            }
            self._write_json_atomic(metadata_path, response)
            return response
        except Exception:
            if preview_dir.exists():
                shutil.rmtree(preview_dir, ignore_errors=True)
            raise

    def commit_render(
        self,
        *,
        session_id: str,
        edit_id: str,
        plan: Mapping[str, Any],
    ) -> dict[str, Any]:
        result_path = self.results_root / session_id / edit_id / "result.png"
        rendered = self._render_plan(plan=plan, result_path=result_path)
        rendered["result_saved_path"] = self._relative_path(result_path)
        rendered["result_url"] = f"/{rendered['result_saved_path']}"
        return rendered

    def cleanup_commit(self, *, session_id: str, edit_id: str) -> None:
        target = (self.results_root / session_id / edit_id).resolve()
        expected_root = self.results_root.resolve()
        try:
            target.relative_to(expected_root)
        except ValueError:
            return
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    def _render_plan(
        self,
        *,
        plan: Mapping[str, Any],
        result_path: Path,
    ) -> dict[str, Any]:
        if str(plan.get("status") or "") != "ready":
            raise PhotoGitRenderError(
                "photo_git_plan_not_ready",
                "Photo Git plan must be ready before rendering.",
                status_code=409,
            )
        recipe = plan.get("recipe")
        if not isinstance(recipe, Mapping):
            raise PhotoGitRenderError(
                "photo_git_recipe_invalid",
                "Photo Git plan has no render recipe.",
            )
        scopes = recipe.get("layers")
        if not isinstance(scopes, list) or not scopes:
            raise PhotoGitRenderError(
                "photo_git_no_change",
                "Photo Git recipe has no effective scopes to render.",
            )
        anchor_saved_path = str(recipe.get("anchor_image_path") or "")
        anchor_path = self._safe_backend_path(
            anchor_saved_path,
            "Photo Git anchor",
        )
        pending_path = result_path.with_name("result.pending.png")
        if pending_path.exists():
            pending_path.unlink()
        started = time.perf_counter()
        try:
            rendered = create_opencv_composite_result(
                anchor_path=anchor_path,
                result_path=pending_path,
                scopes=[dict(item) for item in scopes if isinstance(item, Mapping)],
                mask_source_path=anchor_path,
            )
            result_path.parent.mkdir(parents=True, exist_ok=True)
            pending_path.replace(result_path)
        except SemanticTargetNotFoundError as exc:
            if pending_path.exists():
                pending_path.unlink()
            if result_path.exists():
                result_path.unlink()
            raise PhotoGitRenderError(
                "semantic_target_not_found",
                str(exc),
                status_code=422,
                details={"mask_info": dict(exc.mask_info)},
            ) from exc
        except Exception:
            if pending_path.exists():
                pending_path.unlink()
            if result_path.exists():
                result_path.unlink()
            raise
        timings = dict(rendered.get("timings_ms") or {})
        timings["photo_git_total"] = round(
            (time.perf_counter() - started) * 1000,
            3,
        )
        rendered["timings_ms"] = timings
        return rendered

    def _safe_backend_path(self, relative_path: str, label: str) -> Path:
        candidate = (self.backend_dir / relative_path).resolve()
        try:
            candidate.relative_to(self.backend_dir)
        except ValueError as exc:
            raise PhotoGitRenderError(
                "photo_git_path_invalid",
                f"{label} is outside backend storage.",
                status_code=400,
            ) from exc
        if not candidate.is_file():
            raise PhotoGitRenderError(
                "photo_git_anchor_not_found",
                f"{label} does not exist.",
                status_code=404,
            )
        return candidate

    def _relative_path(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.backend_dir).as_posix()
        except ValueError as exc:
            raise PhotoGitRenderError(
                "photo_git_path_invalid",
                "Photo Git result path is outside backend storage.",
                status_code=500,
            ) from exc

    @staticmethod
    def _load_cache(
        *,
        metadata_path: Path,
        result_path: Path,
        plan_hash: str,
    ) -> dict[str, Any] | None:
        if not metadata_path.is_file() or not result_path.is_file():
            return None
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("preview_cache_version")
            != PHOTO_GIT_PREVIEW_CACHE_VERSION
            or payload.get("plan_hash") != plan_hash
        ):
            return None
        return payload

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass
