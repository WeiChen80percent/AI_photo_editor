from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.services.edit_engines import create_engine_result, normalize_engine_name
from app.services.edit_history import EditHistoryNotFound, EditHistoryStore
from app.services.edit_intent_resolver import resolve_edit_intent
from app.services.edit_plan import build_reference_edit_plan

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
RESULTS_DIR = BASE_DIR / "storage" / "results"
SESSIONS_DIR = BASE_DIR / "storage" / "sessions"
HISTORY_STORE = EditHistoryStore(SESSIONS_DIR)


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
    has_reference = reference_image is not None
    requested_session_id = session_id.strip() if session_id else None
    requested_parent_edit_id = parent_edit_id.strip() if parent_edit_id else None
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
    upload_task_dir.mkdir(parents=True, exist_ok=True)

    parent_record = None
    if requested_parent_edit_id:
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
        original_path = BASE_DIR / original_saved_path
        base_path = BASE_DIR / base_saved_path
    else:
        if original_image is None:
            raise HTTPException(
                status_code=400,
                detail="請上傳原始圖片，或提供 session_id 與 parent_edit_id 延續修圖。",
            )

        original_extension = Path(original_image.filename or "").suffix
        original_path = upload_task_dir / f"original{original_extension}"
        original_bytes = await original_image.read()

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

    reference_extension = Path(reference_image.filename).suffix if reference_image else ""
    reference_path = (
        upload_task_dir / f"reference{reference_extension}" if reference_image else None
    )

    if reference_image and reference_path:
        reference_bytes = await reference_image.read()
        with open(reference_path, "wb") as f:
            f.write(reference_bytes)

    result_path = RESULTS_DIR / effective_session_id / edit_id / "result.png"
    if edit_mode == "prompt":
        prompt_result = resolve_edit_intent(prompt_text)
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
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create {engine_name} result: {str(e)}",
        )

    result_saved_path = result_path.relative_to(BASE_DIR).as_posix()
    reference_saved_path = (
        reference_path.relative_to(BASE_DIR).as_posix() if reference_path else None
    )
    explanation = (
        f"{prompt_result['explanation']} "
        f"{process_result['explanation']}"
    )

    history_record = HISTORY_STORE.build_record(
        session_id=effective_session_id,
        edit_id=edit_id,
        parent_edit_id=requested_parent_edit_id,
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
        explanation=explanation,
        parser_source=prompt_result["parser_source"],
        fallback_reason=prompt_result["fallback_reason"],
        preset_name=prompt_result.get("preset_name"),
    )
    HISTORY_STORE.save_edit(history_record)

    return {
        "message": "Images uploaded and processed successfully",
        "task_id": task_id,
        "session_id": effective_session_id,
        "edit_id": edit_id,
        "parent_edit_id": requested_parent_edit_id,
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
        "prompt": prompt_result["prompt"],
        "resolved_intent": prompt_result["resolved_intent"],
        "preset_name": prompt_result.get("preset_name"),
        "parser_source": prompt_result["parser_source"],
        "fallback_reason": prompt_result["fallback_reason"],
        "explanation": explanation,
    }


@router.get("/edit/sessions/{session_id}")
def get_edit_session(session_id: str):
    try:
        return HISTORY_STORE.load_session(session_id)
    except EditHistoryNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
