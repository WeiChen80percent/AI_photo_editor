from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services.image_processor import create_mock_result

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
RESULTS_DIR = BASE_DIR / "storage" / "results"


@router.post("/edit")
async def upload_images(
    original_image: UploadFile = File(...),
    reference_image: UploadFile = File(...),
):
    task_id = str(uuid4())

    upload_task_dir = UPLOAD_DIR / task_id
    upload_task_dir.mkdir(parents=True, exist_ok=True)

    original_extension = Path(original_image.filename).suffix
    reference_extension = Path(reference_image.filename).suffix

    original_path = upload_task_dir / f"original{original_extension}"
    reference_path = upload_task_dir / f"reference{reference_extension}"

    original_bytes = await original_image.read()
    reference_bytes = await reference_image.read()

    with open(original_path, "wb") as f:
        f.write(original_bytes)

    with open(reference_path, "wb") as f:
        f.write(reference_bytes)

    result_path = RESULTS_DIR / task_id / "result.png"

    try:
        create_mock_result(
            original_path=original_path,
            reference_path=reference_path,
            result_path=result_path,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create mock result: {str(e)}",
        )

    return {
        "message": "Images uploaded and processed successfully",
        "task_id": task_id,
        "original_filename": original_image.filename,
        "reference_filename": reference_image.filename,
        "original_saved_path": original_path.relative_to(BASE_DIR).as_posix(),
        "reference_saved_path": reference_path.relative_to(BASE_DIR).as_posix(),
        "result_saved_path": result_path.relative_to(BASE_DIR).as_posix(),
        "result_url": f"/{result_path.relative_to(BASE_DIR).as_posix()}",
    }