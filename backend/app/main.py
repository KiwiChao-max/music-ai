"""music-ai backend application entry."""
from fastapi import FastAPI

app = FastAPI(title="music-ai", version="0.1.0")


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "music-ai backend is running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
