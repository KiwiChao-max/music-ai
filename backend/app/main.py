"""music-ai backend application entry."""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.audio import router as audio_router
from app.api.tasks import router as tasks_router
from app.config import settings

app = FastAPI(title="music-ai", version="0.1.0")
app.include_router(audio_router)
app.include_router(tasks_router)

# Expose on-disk artifacts (uploads + worker outputs) at /storage/* so the
# frontend can `GET /storage/outputs/task_<id>/drums.wav` after the worker
# finishes. Path resolution in `app/api/tasks.py` builds URLs off this mount.
app.mount(
    "/storage",
    StaticFiles(directory=str(settings.storage_dir)),
    name="storage",
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "music-ai backend is running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
