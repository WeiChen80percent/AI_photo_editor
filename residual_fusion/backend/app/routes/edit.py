import re
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from app.services.adaptive_adjustment import (
    AdaptiveAdjustmentError,
    preflight_adaptive_semantic_prompt,
    resolve_adaptive_adjustment,
)
from app.services.edit_engines import create_engine_result, normalize_engine_name
from app.services.compound_local_edit import (
    CompoundLocalEditError,
    try_resolve_compound_local_edit,
)
from app.services.edit_history import (
    EditHistoryInvalidIdentifier,
    EditHistoryNotFound,
    EditHistoryStore,
)
from app.services.english_prompt_contract import MAX_ENGLISH_PROMPT_LENGTH
from app.services.edit_intent_resolver import resolve_edit_intent
from app.services.edit_plan import build_reference_edit_plan
from app.services.edit_schema import manual_parameter_schema
from app.services.manual_edit_service import ManualEditError, ManualEditService
from app.services.semantic_mask_service import SemanticTargetNotFoundError
from app.services.semantic_shadow_mode import observe_grounded_semantic_shadow
from app.services.style_registry import (
    StyleCatalogError,
    get_style_registry,
)
from app.services.style_selector import (
    StyleSelectionError,
    try_resolve_style_prompt,
)
from app.services.supervised_edit_pipeline import (
    resolve_supervised_prompt_edit,
    should_use_supervised_prompt_edit,
)

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
RESULTS_DIR = BASE_DIR / "storage" / "results"
SESSIONS_DIR = BASE_DIR / "storage" / "sessions"
MANUAL_PREVIEWS_DIR = BASE_DIR / "storage" / "manual_previews"
ORIGINAL_PARENT_SENTINEL = "original"
SESSION_ID_PATTERN = re.compile(r"^session_[0-9a-f]{32}$")
EDIT_ID_PATTERN = re.compile(r"^edit_[0-9a-f]{32}$")
HISTORY_STORE = EditHistoryStore(SESSIONS_DIR)
MANUAL_EDIT_SERVICE = ManualEditService(
    backend_dir=BASE_DIR,
    history_store=HISTORY_STORE,
    preview_root=MANUAL_PREVIEWS_DIR,
    results_root=RESULTS_DIR,
)


class ManualEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=80)
    source_edit_id: str = Field(min_length=1, max_length=80)
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = Field(default=None, max_length=128)


@router.post("/edit")
async def upload_images(
    original_image: UploadFile | None = File(None),
    reference_image: UploadFile | None = File(None),
    prompt: str = Form(""),
    session_id: str | None = Form(None),
    parent_edit_id: str | None = Form(None),
    engine: str = Form("opencv"),
):
    prompt_text = prompt.strip()
    if len(prompt_text) > MAX_ENGLISH_PROMPT_LENGTH:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "adaptive_prompt_too_long",
                "message": "Prompt 長度超過安全解析上限，未建立修圖版本。",
                "issues": [
                    {
                        "reason": "prompt_length_limit",
                        "maximum": MAX_ENGLISH_PROMPT_LENGTH,
                    }
                ],
            },
        )
    has_reference = reference_image is not None
    requested_session_id = session_id.strip() if session_id else None
    requested_parent_edit_id = parent_edit_id.strip() if parent_edit_id else None
    if requested_session_id and SESSION_ID_PATTERN.fullmatch(requested_session_id) is None:
        raise HTTPException(status_code=400, detail="Invalid session_id format.")
    if (
        requested_parent_edit_id
        and requested_parent_edit_id != ORIGINAL_PARENT_SENTINEL
        and EDIT_ID_PATTERN.fullmatch(requested_parent_edit_id) is None
    ):
        raise HTTPException(status_code=400, detail="Invalid parent_edit_id format.")
    try:
        engine_name = normalize_engine_name(engine)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if prompt_text and has_reference:
        raise HTTPException(
            status_code=400,
            detail="請選擇文字修圖或參考圖修圖其中一種。",
        )

    if not prompt_text and not has_reference:
        raise HTTPException(
            status_code=400,
            detail="請輸入 prompt 或選擇參考圖片。",
        )

    if requested_parent_edit_id and not requested_session_id:
        raise HTTPException(
            status_code=400,
            detail="延續修圖時必須同時提供 session_id 與 parent_edit_id。",
        )

    edit_mode = "prompt" if prompt_text else "reference"
    effective_session_id = requested_session_id or HISTORY_STORE.new_session_id()
    edit_id = HISTORY_STORE.new_edit_id()
    task_id = edit_id

    upload_task_dir = UPLOAD_DIR / effective_session_id / edit_id
    result_path = RESULTS_DIR / effective_session_id / edit_id / "result.png"
    history_committed = False
    try:
        parent_record = None
        using_existing_original = requested_parent_edit_id == ORIGINAL_PARENT_SENTINEL
        history_parent_edit_id = requested_parent_edit_id
        if using_existing_original:
            try:
                existing_session = HISTORY_STORE.load_session(effective_session_id)
            except EditHistoryNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))
            existing_edits = existing_session.get("edits")
            if not isinstance(existing_edits, list) or not existing_edits:
                raise HTTPException(
                    status_code=404,
                    detail=f"Edit session has no original image: {effective_session_id}",
                )
            original_saved_path = str(
                existing_edits[0].get("original_image_path") or ""
            )
            base_saved_path = original_saved_path
            original_path = _safe_backend_file(original_saved_path, "session original")
            base_path = original_path
            history_parent_edit_id = None
        elif requested_parent_edit_id:
            try:
                parent_record = HISTORY_STORE.find_edit(
                    effective_session_id,
                    requested_parent_edit_id,
                )
            except EditHistoryNotFound as e:
                raise HTTPException(status_code=404, detail=str(e))

        if parent_record:
            original_saved_path = parent_record["original_image_path"]
            base_saved_path = parent_record["result_image_path"]
            original_path = _safe_backend_file(original_saved_path, "original image")
            base_path = _safe_backend_file(base_saved_path, "parent result")
        elif not using_existing_original:
            if original_image is None:
                raise HTTPException(
                    status_code=400,
                    detail="請上傳原始圖片，或提供 session_id 與 parent_edit_id 延續修圖。",
                )

            original_extension = Path(original_image.filename or "").suffix
            original_path = upload_task_dir / f"original{original_extension}"
            original_bytes = await original_image.read()

            upload_task_dir.mkdir(parents=True, exist_ok=False)
            with open(original_path, "wb") as f:
                f.write(original_bytes)

            original_saved_path = original_path.relative_to(BASE_DIR).as_posix()
            base_saved_path = original_saved_path
            base_path = original_path

        if not base_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"找不到可延續修圖的基準圖片：{base_saved_path}",
            )

        reference_extension = (
            Path(reference_image.filename).suffix if reference_image else ""
        )
        reference_path = (
            upload_task_dir / f"reference{reference_extension}"
            if reference_image
            else None
        )

        if reference_image and reference_path:
            reference_bytes = await reference_image.read()
            upload_task_dir.mkdir(parents=True, exist_ok=True)
            with open(reference_path, "wb") as f:
                f.write(reference_bytes)

        adaptive = None
        adaptive_explanation = None
        semantic_shadow_payload = None
        if edit_mode == "prompt":
            try:
                compound_resolution = try_resolve_compound_local_edit(
                    prompt=prompt_text,
                    parent_record=parent_record,
                    default_base_image_path=base_saved_path,
                    engine_name=engine_name,
                )
            except CompoundLocalEditError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail={
                        "code": exc.code,
                        "message": str(exc),
                        "issues": exc.issues,
                    },
                )
            if compound_resolution is None:
                try:
                    style_prompt_result = try_resolve_style_prompt(prompt_text)
                except StyleSelectionError as exc:
                    raise HTTPException(
                        status_code=exc.status_code,
                        detail={
                            "code": exc.code,
                            "message": str(exc),
                            "candidates": list(exc.candidates),
                        },
                    )
            else:
                style_prompt_result = None

            if compound_resolution is not None:
                prompt_result = compound_resolution.prompt_result
                adaptive = compound_resolution.adaptive
                adaptive_explanation = compound_resolution.explanation
                if compound_resolution.render_base_image_path != base_saved_path:
                    base_saved_path = compound_resolution.render_base_image_path
                    base_path = _safe_backend_file(
                        base_saved_path,
                        "compound local anchor",
                    )
            elif style_prompt_result is not None:
                prompt_result = style_prompt_result
                style_plan = prompt_result["edit_plan"]
                parent_style = (
                    parent_record.get("style")
                    if isinstance(parent_record, dict)
                    and isinstance(parent_record.get("style"), dict)
                    else None
                )
                if parent_style is not None:
                    style_anchor = str(
                        parent_style.get("anchor_image_path") or ""
                    )
                    style_source_edit_id = parent_style.get("source_edit_id")
                    if style_anchor:
                        base_saved_path = style_anchor
                        base_path = _safe_backend_file(
                            style_anchor,
                            "style anchor",
                        )
                        history_parent_edit_id = (
                            str(style_source_edit_id)
                            if style_source_edit_id
                            else None
                        )
                style_plan["style_source_edit_id"] = history_parent_edit_id
                style_plan["style_anchor_image_path"] = base_saved_path
            else:
                semantic_preflight = preflight_adaptive_semantic_prompt(
                    prompt=prompt_text,
                    engine_name=engine_name,
                )
                semantic_disposition = (
                    semantic_preflight.semantic_attempt.disposition
                    if semantic_preflight.semantic_attempt is not None
                    else None
                )
                if should_use_supervised_prompt_edit(
                    prompt_text,
                    engine_name=engine_name,
                    has_parent_edit=parent_record is not None,
                    semantic_disposition=semantic_disposition,
                ):
                    prompt_result = resolve_supervised_prompt_edit(
                        prompt_text,
                        base_path,
                    )
                else:
                    if semantic_preflight.semantic_attempt is not None:
                        semantic_shadow = observe_grounded_semantic_shadow(
                            prompt=prompt_text,
                            deterministic_attempt=semantic_preflight.semantic_attempt,
                            engine=engine_name,
                        )
                        if semantic_shadow.enabled:
                            semantic_shadow_payload = semantic_shadow.as_dict()
                    prompt_result = semantic_preflight.prompt_result
                    if not semantic_preflight.bypass_intent_resolver:
                        prompt_result = resolve_edit_intent(prompt_text)
                    try:
                        adaptive_result = resolve_adaptive_adjustment(
                            prompt_result=prompt_result,
                            prompt=prompt_text,
                            parent_record=parent_record,
                            default_base_image_path=base_saved_path,
                            engine_name=engine_name,
                            semantic_attempt=semantic_preflight.semantic_attempt,
                        )
                    except AdaptiveAdjustmentError as exc:
                        detail = {"code": exc.code, "message": str(exc)}
                        if exc.issues:
                            detail["issues"] = exc.issues
                        if semantic_shadow_payload is not None:
                            detail["semantic_shadow"] = semantic_shadow_payload
                        raise HTTPException(
                            status_code=exc.status_code,
                            detail=detail,
                        )
                    prompt_result = adaptive_result.prompt_result
                    adaptive = adaptive_result.adaptive
                    adaptive_explanation = adaptive_result.explanation
                    if adaptive_result.render_base_image_path != base_saved_path:
                        base_saved_path = adaptive_result.render_base_image_path
                        base_path = _safe_backend_file(
                            base_saved_path,
                            "adaptive anchor",
                        )
        else:
            prompt_result = {
                "prompt": "",
                "resolved_intent": "reference_style",
                "preset_name": None,
                "edit_plan": build_reference_edit_plan(),
                "parameters": {},
                "explanation": "使用參考圖修圖模式，未套用文字 prompt。",
                "parser_source": "reference_mode",
                "fallback_reason": None,
            }

        try:
            process_result = create_engine_result(
                engine_name=engine_name,
                original_path=base_path,
                reference_path=reference_path,
                result_path=result_path,
                edit_plan=prompt_result["edit_plan"],
                mask_source_path=original_path,
            )
            _validate_completed_render(result_path, process_result)
        except SemanticTargetNotFoundError as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "semantic_target_not_found",
                    "message": str(e),
                    "mask_info": e.mask_info,
                },
            )
        except StyleCatalogError as e:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": getattr(e, "code", "style_catalog_invalid"),
                    "message": str(e),
                },
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create {engine_name} result: {str(e)}",
            )

        result_saved_path = result_path.relative_to(BASE_DIR).as_posix()
        reference_saved_path = (
            reference_path.relative_to(BASE_DIR).as_posix() if reference_path else None
        )
        intent_explanation = adaptive_explanation or prompt_result["explanation"]
        explanation = f"{intent_explanation} {process_result['explanation']}"
        active_style = process_result.get("style")
        if isinstance(active_style, dict):
            active_style = {
                **active_style,
                "source_edit_id": prompt_result["edit_plan"].get(
                    "style_source_edit_id"
                ),
                "anchor_image_path": prompt_result["edit_plan"].get(
                    "style_anchor_image_path"
                ),
            }
        elif (
            edit_mode == "prompt"
            and prompt_result.get("resolved_intent") != "reset_to_original"
            and isinstance(parent_record, dict)
            and isinstance(parent_record.get("style"), dict)
        ):
            active_style = dict(parent_record["style"])
        else:
            active_style = None

        history_record = HISTORY_STORE.build_record(
            session_id=effective_session_id,
            edit_id=edit_id,
            parent_edit_id=history_parent_edit_id,
            edit_mode=edit_mode,
            original_image_path=original_saved_path,
            base_image_path=base_saved_path,
            result_image_path=result_saved_path,
            reference_image_path=reference_saved_path,
            user_prompt=prompt_result["prompt"],
            resolved_intent=prompt_result["resolved_intent"],
            parameters=process_result["parameters"],
            engine=process_result["engine"],
            edit_plan=prompt_result["edit_plan"],
            engine_parameters=process_result["parameters"],
            mask_info=process_result.get("mask_info"),
            explanation=explanation,
            parser_source=prompt_result["parser_source"],
            fallback_reason=prompt_result["fallback_reason"],
            preset_name=prompt_result.get("preset_name"),
            processing_timings=process_result.get("timings_ms"),
            adaptive=adaptive,
            style=active_style,
            prompt_control=prompt_result.get("prompt_control"),
            numeric_model=prompt_result.get("numeric_model"),
            supervised_pipeline=prompt_result.get("supervised_pipeline"),
            render_safety=process_result.get("render_safety"),
        )

        response_payload = {
            "message": "Images uploaded and processed successfully",
            "task_id": task_id,
            "session_id": effective_session_id,
            "edit_id": edit_id,
            "parent_edit_id": history_parent_edit_id,
            "original_filename": original_image.filename if original_image else None,
            "reference_filename": reference_image.filename if reference_image else None,
            "original_saved_path": original_saved_path,
            "base_image_path": base_saved_path,
            "reference_saved_path": reference_saved_path,
            "result_saved_path": result_saved_path,
            "result_url": f"/{result_saved_path}",
            "engine": process_result["engine"],
            "edit_mode": edit_mode,
            "edit_plan": prompt_result["edit_plan"],
            "engine_parameters": process_result["parameters"],
            "parameters": process_result["parameters"],
            "mask_info": process_result.get("mask_info"),
            "adaptive": adaptive,
            "prompt": prompt_result["prompt"],
            "resolved_intent": prompt_result["resolved_intent"],
            "preset_name": prompt_result.get("preset_name"),
            "style": active_style,
            "prompt_control": prompt_result.get("prompt_control"),
            "numeric_model": prompt_result.get("numeric_model"),
            "supervised_pipeline": prompt_result.get("supervised_pipeline"),
            "render_profile": process_result.get("render_profile"),
            "render_variant": process_result.get("render_variant"),
            "render_safety": process_result.get("render_safety"),
            "parser_source": prompt_result["parser_source"],
            "fallback_reason": prompt_result["fallback_reason"],
            "explanation": explanation,
        }
        if semantic_shadow_payload is not None:
            # Development-only shadow telemetry is intentionally response-only:
            # it must never become controller input or immutable edit history.
            response_payload["semantic_shadow"] = semantic_shadow_payload

        HISTORY_STORE.save_edit(history_record)
        history_committed = True
        return response_payload
    finally:
        if not history_committed:
            # Files are part of the edit transaction: retain them only after the
            # corresponding immutable history record has committed successfully.
            _cleanup_failed_task(upload_task_dir, result_path.parent)


@router.get("/edit/sessions/{session_id}")
def get_edit_session(session_id: str):
    try:
        return HISTORY_STORE.load_session(session_id)
    except EditHistoryInvalidIdentifier as e:
        raise HTTPException(status_code=400, detail=str(e))
    except EditHistoryNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/edit/styles")
def get_style_catalog():
    try:
        return get_style_registry().payload()
    except StyleCatalogError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": getattr(exc, "code", "style_catalog_invalid"),
                "message": str(exc),
            },
        )


@router.get("/edit/manual/schema")
def get_manual_edit_schema():
    return manual_parameter_schema()


@router.post("/edit/manual/preview")
def preview_manual_edit(request: ManualEditRequest):
    try:
        return MANUAL_EDIT_SERVICE.preview(
            session_id=request.session_id,
            source_edit_id=request.source_edit_id,
            parameter_overrides=request.parameter_overrides,
            client_request_id=request.client_request_id,
        )
    except SemanticTargetNotFoundError as exc:
        raise _semantic_target_http_error(exc)
    except ManualEditError as exc:
        raise _manual_edit_http_error(exc)


@router.post("/edit/manual/commit")
def commit_manual_edit(request: ManualEditRequest):
    try:
        return MANUAL_EDIT_SERVICE.commit(
            session_id=request.session_id,
            source_edit_id=request.source_edit_id,
            parameter_overrides=request.parameter_overrides,
            client_request_id=request.client_request_id,
        )
    except SemanticTargetNotFoundError as exc:
        raise _semantic_target_http_error(exc)
    except ManualEditError as exc:
        raise _manual_edit_http_error(exc)


def _manual_edit_http_error(exc: ManualEditError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _semantic_target_http_error(exc: SemanticTargetNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "semantic_target_not_found",
            "message": str(exc),
            "mask_info": exc.mask_info,
        },
    )


def _safe_backend_file(relative_path: str, label: str) -> Path:
    if not relative_path:
        raise HTTPException(status_code=404, detail=f"Missing {label} path")
    candidate = (BASE_DIR / relative_path).resolve()
    try:
        candidate.relative_to(BASE_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label} path") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Missing {label}: {relative_path}")
    return candidate


def _cleanup_failed_task(upload_task_dir: Path, result_task_dir: Path) -> None:
    for task_dir in (upload_task_dir, result_task_dir):
        if task_dir.is_dir():
            shutil.rmtree(task_dir, ignore_errors=True)
        try:
            task_dir.parent.rmdir()
        except OSError:
            pass


def _validate_completed_render(
    result_path: Path,
    process_result: Any,
) -> None:
    """Reject an engine's false-success before a history record can be committed."""

    if not isinstance(process_result, dict):
        raise RuntimeError("Edit engine returned an invalid result payload.")
    if not isinstance(process_result.get("parameters"), dict):
        raise RuntimeError("Edit engine result is missing parameter metadata.")
    if not process_result.get("engine"):
        raise RuntimeError("Edit engine result is missing the engine name.")
    if not result_path.is_file() or result_path.stat().st_size <= 0:
        raise RuntimeError("Edit engine did not create a result image.")
    try:
        with Image.open(result_path) as image:
            image.verify()
            if image.width <= 0 or image.height <= 0:
                raise RuntimeError("Edit engine created an empty result image.")
    except (OSError, UnidentifiedImageError) as exc:
        raise RuntimeError("Edit engine created an unreadable result image.") from exc
