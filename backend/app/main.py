from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routes.edit import router as edit_router
from app.routes.health import router as health_router

app = FastAPI(title="AI Photo Editor Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = BASE_DIR / "storage"

app.mount("/storage", StaticFiles(directory=STORAGE_DIR), name="storage")

app.include_router(health_router)
app.include_router(edit_router)


@app.get("/")
def root():
    return {"message": "Backend is running"}