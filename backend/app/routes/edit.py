import asyncio
import copy
import hashlib
import json
import re
import shutil
from collections.abc import Mapping
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
from app.services.auto_model_adapters import build_default_auto_model_adapters
from app.services.auto_model_schema import (
    AUTO_MODEL_COMPARISON_SCHEMA_VERSION,
    AutoModelError,
)
from app.services.auto_model_service import AutoModelComparisonService
from app.services.command_planner import CommandPlanCacheError, CommandPlanner
from app.services.command_schema import CommandPlanRequest
from app.services.edit_engines import create_engine_result, normalize_engine_name
from app.services.edit_contract_registry import get_default_metric_registry
from app.services.edit_contract_schema import EditContractError
from app.services.edit_contract_semantic_adapter import (
    ContractSemanticAttempt,
    parse_edit_contract_prompt,
)
from app.services.edit_contract_service import EditContractService
from app.services.edit_history import (
    EditHistoryConflict,
    EditHistoryInvalidIdentifier,
    EditHistoryNotFound,
    EditHistoryStore,
)
from app.services.english_prompt_contract import MAX_ENGLISH_PROMPT_LENGTH
from app.services.grounded_contract_provider import (
    get_default_grounded_contract_provider,
)
from app.services.grounded_command_provider import (
    get_default_grounded_command_provider,
)
from app.services.edit_intent_resolver import resolve_edit_intent
from app.services.edit_plan import build_reference_edit_plan
from app.services.edit_schema import manual_parameter_schema
from app.services.manual_edit_service import ManualEditError, ManualEditService
from app.services.photo_git_schema import (
    PhotoGitCommitRequest,
    PhotoGitExecutionRequest,
    PhotoGitPlanRequest,
)
from app.services.photo_git_service import PhotoGitError, PhotoGitService
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

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
RESULTS_DIR = BASE_DIR / "storage" / "results"
SESSIONS_DIR = BASE_DIR / "storage" / "sessions"
MANUAL_PREVIEWS_DIR = BASE_DIR / "storage" / "manual_previews"
PHOTO_GIT_PREVIEWS_DIR = BASE_DIR / "storage" / "photo_git_previews"
AUTO_MODEL_COMPARISONS_DIR = BASE_DIR / "storage" / "auto_model_comparisons"
ORIGINAL_PARENT_SENTINEL = "original"
SESSION_ID_PATTERN = re.compile(r"^session_[0-9a-f]{32}$")
EDIT_ID_PATTERN = re.compile(r"^edit_[0-9a-f]{32}$")
COMMAND_EXECUTION_TYPES = {"edit_prompt", "apply_style"}
HISTORY_STORE = EditHistoryStore(SESSIONS_DIR)
MANUAL_EDIT_SERVICE = ManualEditService(
    backend_dir=BASE_DIR,
    history_store=HISTORY_STORE,
    preview_root=MANUAL_PREVIEWS_DIR,
    results_root=RESULTS_DIR,
)
PHOTO_GIT_SERVICE = PhotoGitService(
    backend_dir=BASE_DIR,
    history_store=HISTORY_STORE,
    preview_root=PHOTO_GIT_PREVIEWS_DIR,
    results_root=RESULTS_DIR,
)
COMMAND_PLANNER = CommandPlanner(
    history_store=HISTORY_STORE,
    photo_git_service=PHOTO_GIT_SERVICE,
    candidate_provider=get_default_grounded_command_provider(),
)
EDIT_CONTRACT_REGISTRY = get_default_metric_registry()
EDIT_CONTRACT_SERVICE = EditContractService(registry=EDIT_CONTRACT_REGISTRY)
GROUNDED_CONTRACT_PROVIDER = get_default_grounded_contract_provider()
AUTO_MODEL_SERVICE = AutoModelComparisonService(
    backend_dir=BASE_DIR,
    history_store=HISTORY_STORE,
    adapters=build_default_auto_model_adapters(),
    results_root=RESULTS_DIR,
    uploads_root=UPLOAD_DIR,
    comparisons_root=AUTO_MODEL_COMPARISONS_DIR,
)


class ManualEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=80)
    source_edit_id: str = Field(min_length=1, max_length=80)
    parameter_overrides: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = Field(default=None, max_length=128)
    instruction: str = Field(default="", max_length=500)
    command_plan_hash: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )


@router.post("/edit")
async def upload_images(
    original_image: UploadFile | None = File(None),
    reference_image: UploadFile | None = File(None),
    prompt: str = Form(""),
    session_id: str | None = Form(None),
    parent_edit_id: str | None = Form(None),
    engine: str = Form("opencv"),
    client_request_id: str | None = Form(None),
    command_type: str | None = Form(None),
    command_plan_hash: str | None = Form(None),
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
    requested_client_request_id = (
        client_request_id.strip() if client_request_id else None
    )
    requested_command_type = str(command_type or "").strip() or None
    requested_command_plan_hash = (
        str(command_plan_hash or "").strip().lower() or None
    )
    if requested_client_request_id is not None and not (
        1 <= len(requested_client_request_id) <= 128
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_client_request_id",
                "message": "client_request_id 長度必須介於 1 到 128 字元。",
            },
        )
    if (requested_command_type is None) != (requested_command_plan_hash is None):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "command_provenance_incomplete",
                "message": "command_type 與 command_plan_hash 必須一起提供。",
            },
        )
    if (
        requested_command_type is not None
        and requested_command_type not in COMMAND_EXECUTION_TYPES
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "command_type_invalid",
                "message": "此 /edit 執行路徑不支援指定的 command_type。",
            },
        )
    if (
        requested_command_plan_hash is not None
        and re.fullmatch(r"[0-9a-f]{64}", requested_command_plan_hash) is None
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "command_plan_hash_invalid",
                "message": "command_plan_hash 必須是 64 字元 SHA-256。",
            },
        )
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

        command_request_hash = None
        command_execution_plan = None
        if requested_command_plan_hash is not None:
            command_request_hash = _command_edit_request_hash(
                prompt=prompt_text,
                requested_session_id=requested_session_id,
                requested_parent_edit_id=requested_parent_edit_id,
                engine=engine_name,
                command_type=requested_command_type or "",
                command_plan_hash=requested_command_plan_hash,
                original_path=original_path,
                base_path=base_path,
                reference_path=reference_path,
            )
            if requested_client_request_id is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "command_client_request_id_required",
                        "message": "指令執行必須提供 client_request_id。",
                    },
                )
            try:
                existing_command = HISTORY_STORE.find_edit_request_idempotent(
                    namespace="command",
                    client_request_id=requested_client_request_id,
                    request_hash=command_request_hash,
                )
            except EditHistoryConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "command_request_conflict",
                        "message": str(exc),
                    },
                ) from exc
            if existing_command is not None:
                _, persisted_record = existing_command
                return _history_record_response(
                    persisted_record,
                    idempotent_replay=True,
                )
            try:
                command_execution_plan = COMMAND_PLANNER.require_cached_plan(
                    plan_hash=requested_command_plan_hash,
                    instruction=prompt_text,
                    session_id=requested_session_id,
                    selected_edit_id=(
                        None
                        if requested_parent_edit_id == ORIGINAL_PARENT_SENTINEL
                        else requested_parent_edit_id
                    ),
                    command_type=requested_command_type or "",
                )
            except CommandPlanCacheError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "command_plan_stale",
                        "message": str(exc),
                    },
                ) from exc

        contract_attempt: ContractSemanticAttempt | None = None
        if edit_mode == "prompt":
            contract_attempt = parse_edit_contract_prompt(
                prompt_text,
                metric_registry=EDIT_CONTRACT_REGISTRY,
                engine=engine_name,
                grounded_provider=GROUNDED_CONTRACT_PROVIDER,
            )
            if contract_attempt.error is not None:
                raise HTTPException(
                    status_code=contract_attempt.error.status_code,
                    detail=contract_attempt.error.as_dict(),
                )

        selected_target_saved_path = base_saved_path
        selected_target_path = base_path
        selected_target_edit_id = (
            requested_parent_edit_id
            if requested_parent_edit_id
            else ORIGINAL_PARENT_SENTINEL
        )
        is_contract_prompt = bool(
            contract_attempt is not None and contract_attempt.accepted
        )

        adaptive = None
        adaptive_explanation = None
        semantic_shadow_payload = None
        if edit_mode == "prompt":
            try:
                style_prompt_result = (
                    None
                    if is_contract_prompt
                    else try_resolve_style_prompt(prompt_text)
                )
            except StyleSelectionError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail={
                        "code": exc.code,
                        "message": str(exc),
                        "candidates": list(exc.candidates),
                    },
                )
            if style_prompt_result is not None:
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
                if is_contract_prompt:
                    if (
                        contract_attempt is None
                        or contract_attempt.contract_ir is None
                        or contract_attempt.operation_semantic_attempt is None
                    ):
                        raise HTTPException(
                            status_code=500,
                            detail={
                                "code": "contract_semantic_state_invalid",
                                "message": "合約語意狀態不完整，未建立修圖版本。",
                            },
                        )
                    semantic_attempt = contract_attempt.operation_semantic_attempt
                    semantic_ir = semantic_attempt.accepted_ir
                    prompt_result = {
                        "prompt": prompt_text,
                        "parser_source": "edit_contract_semantic",
                        "fallback_reason": None,
                        "semantic_ir": semantic_ir.as_dict(),
                        "semantic_parser_version": semantic_ir.parser_version,
                        "semantic_decision_source": semantic_ir.decision_source,
                    }
                else:
                    semantic_preflight = preflight_adaptive_semantic_prompt(
                        prompt=prompt_text,
                        engine_name=engine_name,
                    )
                    semantic_attempt = semantic_preflight.semantic_attempt
                    if semantic_attempt is not None:
                        semantic_shadow = observe_grounded_semantic_shadow(
                            prompt=prompt_text,
                            deterministic_attempt=semantic_attempt,
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
                        semantic_attempt=semantic_attempt,
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
                if is_contract_prompt:
                    if contract_attempt is None or contract_attempt.contract_ir is None:
                        raise HTTPException(
                            status_code=500,
                            detail={"code": "contract_semantic_state_invalid"},
                        )
                    contract_semantic_ir = (
                        contract_attempt.contract_ir.semantic_ir.as_dict()
                    )
                    prompt_result["semantic_ir"] = copy.deepcopy(
                        contract_semantic_ir
                    )
                    prompt_result["semantic_parser_version"] = (
                        contract_attempt.contract_ir.semantic_ir.parser_version
                    )
                    prompt_result["semantic_decision_source"] = (
                        contract_attempt.contract_ir.semantic_ir.decision_source
                    )
                    edit_plan = prompt_result.get("edit_plan")
                    if isinstance(edit_plan, dict):
                        edit_plan["semantic_ir"] = copy.deepcopy(
                            contract_semantic_ir
                        )
                        edit_plan["semantic_parser_version"] = prompt_result[
                            "semantic_parser_version"
                        ]
                        edit_plan["semantic_decision_source"] = prompt_result[
                            "semantic_decision_source"
                        ]
                        plan_adaptation = edit_plan.get("adaptation")
                        if isinstance(plan_adaptation, dict):
                            plan_adaptation["semantic_ir"] = copy.deepcopy(
                                contract_semantic_ir
                            )
                            plan_adaptation["semantic_parser_version"] = (
                                prompt_result["semantic_parser_version"]
                            )
                            plan_adaptation["semantic_decision_source"] = (
                                prompt_result["semantic_decision_source"]
                            )
                    if isinstance(adaptive, dict):
                        adaptive["semantic_ir"] = copy.deepcopy(
                            contract_semantic_ir
                        )
                        adaptive["semantic_parser_version"] = prompt_result[
                            "semantic_parser_version"
                        ]
                        adaptive["semantic_decision_source"] = prompt_result[
                            "semantic_decision_source"
                        ]
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

        contract_report = None
        try:
            if is_contract_prompt:
                if contract_attempt is None or contract_attempt.contract_ir is None:
                    raise EditContractError(
                        code="contract_semantic_state_invalid",
                        message="合約語意狀態不完整，未建立修圖版本。",
                        disposition="rejected",
                        status_code=500,
                    )
                contract_execution = EDIT_CONTRACT_SERVICE.execute(
                    contract_ir=contract_attempt.contract_ir,
                    prompt_result=prompt_result,
                    adaptive=adaptive,
                    selected_target_path=selected_target_path,
                    selected_target_saved_path=selected_target_saved_path,
                    target_edit_id=selected_target_edit_id,
                    render_anchor_path=base_path,
                    render_anchor_saved_path=base_saved_path,
                    mask_source_path=original_path,
                    mask_source_saved_path=original_saved_path,
                    result_path=result_path,
                    engine_name=engine_name,
                )
                prompt_result = contract_execution.prompt_result
                adaptive = contract_execution.adaptive
                process_result = contract_execution.process_result
                contract_report = contract_execution.report
                adaptive_explanation = prompt_result.get("explanation")
            else:
                process_result = create_engine_result(
                    engine_name=engine_name,
                    original_path=base_path,
                    reference_path=reference_path,
                    result_path=result_path,
                    edit_plan=prompt_result["edit_plan"],
                    mask_source_path=original_path,
                )
            _validate_completed_render(result_path, process_result)
        except EditContractError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.as_dict(),
            ) from exc
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

        edit_contract_metadata = None
        if contract_report is not None:
            edit_contract_metadata = {
                **contract_report.as_dict(),
                "client_request_id": requested_client_request_id,
            }
        command_metadata = (
            {
                **copy.deepcopy(command_execution_plan),
                "client_request_id": requested_client_request_id,
                "request_hash": command_request_hash,
                "executed_parent_edit_id": history_parent_edit_id,
            }
            if command_execution_plan is not None
            else None
        )

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
            edit_contract=edit_contract_metadata,
            command=command_metadata,
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
            "edit_contract": edit_contract_metadata,
            "command": command_metadata,
            "parser_source": prompt_result["parser_source"],
            "fallback_reason": prompt_result["fallback_reason"],
            "explanation": explanation,
        }
        if semantic_shadow_payload is not None:
            # Development-only shadow telemetry is intentionally response-only:
            # it must never become controller input or immutable edit history.
            response_payload["semantic_shadow"] = semantic_shadow_payload

        if command_metadata is not None and requested_client_request_id:
            try:
                _, persisted_record, created = (
                    HISTORY_STORE.save_edit_request_idempotent(
                        history_record,
                        namespace="command",
                        client_request_id=requested_client_request_id,
                        request_hash=command_request_hash or "",
                    )
                )
            except EditHistoryConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "command_request_conflict",
                        "message": str(exc),
                    },
                ) from exc
            if not created:
                return _history_record_response(
                    persisted_record,
                    idempotent_replay=True,
                )
        elif edit_contract_metadata is not None and requested_client_request_id:
            try:
                _, persisted_record, created = (
                    HISTORY_STORE.save_edit_request_idempotent(
                        history_record,
                        namespace="edit_contract",
                        client_request_id=requested_client_request_id,
                        request_hash=contract_report.contract_hash,
                    )
                )
            except EditHistoryConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "contract_request_conflict",
                        "message": str(exc),
                    },
                ) from exc
            if not created:
                return _history_record_response(
                    persisted_record,
                    idempotent_replay=True,
                )
        else:
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


@router.get("/edit/contracts/schema")
def get_edit_contract_schema():
    return EDIT_CONTRACT_REGISTRY.as_schema_payload()


@router.get("/edit/auto-models/health")
def get_auto_model_health():
    return AUTO_MODEL_SERVICE.health()


@router.post("/edit/auto-models/compare")
async def compare_auto_models(
    original_image: UploadFile | None = File(None),
    session_id: str | None = Form(None),
    source_edit_id: str | None = Form(None),
    client_request_id: str = Form(...),
    comparison_schema_version: str = Form(
        AUTO_MODEL_COMPARISON_SCHEMA_VERSION
    ),
):
    original_bytes = (
        await original_image.read()
        if original_image is not None
        else None
    )
    try:
        return await asyncio.to_thread(
            AUTO_MODEL_SERVICE.compare,
            original_bytes=original_bytes,
            original_filename=(
                original_image.filename
                if original_image is not None
                else None
            ),
            session_id=session_id,
            source_edit_id=source_edit_id,
            client_request_id=client_request_id,
            schema_version=comparison_schema_version,
        )
    except AutoModelError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.as_dict(),
        ) from exc


@router.post("/edit/commands/plan")
def plan_editor_command(request: CommandPlanRequest):
    return COMMAND_PLANNER.plan(request)


@router.post("/edit/photo-git/plan")
def plan_photo_git(request: PhotoGitPlanRequest):
    try:
        _photo_git_command_provenance(request)
        return PHOTO_GIT_SERVICE.plan(request)
    except PhotoGitError as exc:
        raise _photo_git_http_error(exc) from exc


@router.post("/edit/photo-git/preview")
def preview_photo_git(request: PhotoGitExecutionRequest):
    try:
        _photo_git_command_provenance(request)
        return PHOTO_GIT_SERVICE.preview(request)
    except PhotoGitError as exc:
        raise _photo_git_http_error(exc) from exc


@router.post("/edit/photo-git/commit")
def commit_photo_git(request: PhotoGitCommitRequest):
    try:
        return PHOTO_GIT_SERVICE.commit(
            request,
            command_provenance_loader=(
                lambda: _photo_git_command_provenance(request)
            ),
        )
    except PhotoGitError as exc:
        raise _photo_git_http_error(exc) from exc


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
        command_provenance_loader = None
        if request.command_plan_hash is not None or request.instruction.strip():
            if request.command_plan_hash is None or not request.instruction.strip():
                raise ManualEditError(
                    "command_provenance_incomplete",
                    "instruction and command_plan_hash must be provided together",
                )

            def load_command_provenance() -> Mapping[str, Any]:
                try:
                    command_provenance = COMMAND_PLANNER.require_cached_plan(
                        plan_hash=request.command_plan_hash or "",
                        instruction=request.instruction,
                        session_id=request.session_id,
                        selected_edit_id=request.source_edit_id,
                        command_type="manual_adjust",
                    )
                except CommandPlanCacheError as exc:
                    raise ManualEditError(
                        "command_plan_stale",
                        str(exc),
                        status_code=409,
                    ) from exc
                action = command_provenance.get("action")
                if (
                    not isinstance(action, Mapping)
                    or action.get("source_edit_id") != request.source_edit_id
                    or dict(action.get("parameter_overrides") or {})
                    != request.parameter_overrides
                ):
                    raise ManualEditError(
                        "command_action_mismatch",
                        "Manual execution does not match the resolved command plan",
                        status_code=409,
                    )
                return command_provenance

            command_provenance_loader = load_command_provenance
        return MANUAL_EDIT_SERVICE.commit(
            session_id=request.session_id,
            source_edit_id=request.source_edit_id,
            parameter_overrides=request.parameter_overrides,
            client_request_id=request.client_request_id,
            instruction=request.instruction,
            command_plan_hash=request.command_plan_hash,
            command_provenance_loader=command_provenance_loader,
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


def _photo_git_http_error(exc: PhotoGitError) -> HTTPException:
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": str(exc),
    }
    detail.update(exc.details)
    return HTTPException(status_code=exc.status_code, detail=detail)


def _photo_git_command_provenance(
    request: PhotoGitPlanRequest,
) -> dict[str, Any] | None:
    if request.command_plan_hash is None:
        return None
    command_type = (
        "photo_git_merge"
        if request.operation == "merge"
        else "photo_git_revert"
    )
    try:
        plan = COMMAND_PLANNER.require_cached_plan(
            plan_hash=request.command_plan_hash,
            instruction=request.instruction,
            session_id=request.session_id,
            selected_edit_id=request.target_edit_id,
            command_type=command_type,
        )
    except CommandPlanCacheError as exc:
        raise PhotoGitError(
            "command_plan_stale",
            str(exc),
            status_code=409,
        ) from exc
    action = plan.get("action")
    planned_request = (
        action.get("photo_git_request")
        if isinstance(action, Mapping)
        else None
    )
    if not isinstance(planned_request, Mapping):
        raise PhotoGitError(
            "command_action_mismatch",
            "Command plan does not contain a Photo Git request",
            status_code=409,
        )
    actual_selectors = [
        selector.model_dump(mode="json")
        for selector in request.selectors
    ]
    expected_fields = {
        "operation": request.operation,
        "target_edit_id": request.target_edit_id,
        "source_edit_id": request.source_edit_id,
        "revert_edit_id": request.revert_edit_id,
        "instruction": request.instruction.strip(),
        "selectors": actual_selectors,
    }
    for key, actual in expected_fields.items():
        expected = planned_request.get(key)
        if key == "instruction":
            expected = str(expected or "").strip()
        if expected != actual:
            raise PhotoGitError(
                "command_action_mismatch",
                "Photo Git execution does not match the resolved command plan",
                status_code=409,
            )
    return plan


def _semantic_target_http_error(exc: SemanticTargetNotFoundError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "code": "semantic_target_not_found",
            "message": str(exc),
            "mask_info": exc.mask_info,
        },
    )


def _history_record_response(
    record: Mapping[str, Any],
    *,
    idempotent_replay: bool,
) -> dict[str, Any]:
    result_saved_path = str(record.get("result_image_path") or "")
    parameters = record.get("engine_parameters")
    if not isinstance(parameters, dict):
        parameters = dict(record.get("parameters") or {})
    return {
        **copy.deepcopy(dict(record)),
        "message": "Existing verified edit returned for idempotent request",
        "task_id": record.get("edit_id"),
        "original_filename": None,
        "reference_filename": None,
        "original_saved_path": record.get("original_image_path"),
        "base_image_path": record.get("base_image_path"),
        "reference_saved_path": record.get("reference_image_path"),
        "result_saved_path": result_saved_path,
        "result_url": f"/{result_saved_path}",
        "engine_parameters": parameters,
        "parameters": parameters,
        "prompt": record.get("user_prompt"),
        "idempotent_replay": idempotent_replay,
    }


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


def _command_edit_request_hash(
    *,
    prompt: str,
    requested_session_id: str | None,
    requested_parent_edit_id: str | None,
    engine: str,
    command_type: str,
    command_plan_hash: str,
    original_path: Path,
    base_path: Path,
    reference_path: Path | None,
) -> str:
    payload = {
        "prompt": prompt,
        "requested_session_id": requested_session_id,
        "requested_parent_edit_id": requested_parent_edit_id,
        "engine": engine,
        "command_type": command_type,
        "command_plan_hash": command_plan_hash,
        "original_sha256": _sha256_file(original_path),
        "base_sha256": _sha256_file(base_path),
        "reference_sha256": (
            _sha256_file(reference_path) if reference_path is not None else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
