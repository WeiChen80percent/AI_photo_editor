import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes.edit import router as edit_router
from app.routes.health import router as health_router
from app.services.semantic_mask_service import get_default_semantic_mask_service


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    preload = os.getenv("AI_PHOTO_PRELOAD_SEGMENTATION", "1").strip().lower()
    if preload not in {"0", "false", "no", "off"}:
        try:
            info = await asyncio.to_thread(
                get_default_semantic_mask_service().warmup
            )
            logger.info("Semantic segmentation model is ready: %s", info)
        except Exception:
            logger.exception(
                "Semantic segmentation preload failed; non-semantic edits remain available."
            )
    yield


app = FastAPI(title="AI Photo Editor Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = BASE_DIR / "storage"
STYLE_PREVIEW_DIR = BASE_DIR / "app" / "style_catalog" / "previews"

app.mount("/storage", StaticFiles(directory=STORAGE_DIR), name="storage")
app.mount(
    "/style-previews",
    StaticFiles(directory=STYLE_PREVIEW_DIR),
    name="style-previews",
)

app.include_router(health_router)
app.include_router(edit_router)


@app.get("/")
def root():
    return {"message": "Backend is running"}
