"""FastAPI application entry point."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.audio import router as audio_router
from app.api.deps import refresh_settings_check
from app.api.health import router as health_router
from app.api.instruments import router as instruments_router
from app.api.tasks import router as tasks_router
from app.api.ws import router as ws_router
from app.config import settings

app = FastAPI(title="music-ai", version="0.1.0")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(audio_router)
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(tasks_router)
app.include_router(instruments_router)
app.include_router(ws_router)

settings.storage_dir.mkdir(parents=True, exist_ok=True)

# Expose on-disk artifacts (uploads + worker outputs) at /storage/* so the
# frontend can `GET /storage/outputs/task_<id>/drums.wav` after the worker
# finishes. Path resolution in `app/api/tasks.py` builds URLs off this mount.
app.mount(
    "/storage",
    StaticFiles(directory=str(settings.storage_dir)),
    name="storage",
)


@app.on_event("startup")
def _startup_checks() -> None:
    """Refuse to boot with the placeholder JWT secret in production,
    and seed a default admin user for local development so the SPA
    has something to log in with on a fresh install.
    """
    refresh_settings_check()
    _seed_bootstrap_admin()


def _seed_bootstrap_admin() -> None:
    """Idempotently create a default admin user on startup.

    The credentials come from settings (`bootstrap_admin_*`); leaving
    the email empty in env disables the seed. If the user already
    exists, we do nothing — never overwrite a password that an
    operator may have changed.
    """
    if not settings.bootstrap_admin_email:
        return
    from app.db.session import SessionLocal
    from app.services import user_service

    db = SessionLocal()
    try:
        existing = user_service.get_user_by_email(
            db, settings.bootstrap_admin_email
        )
        if existing is not None:
            return
        try:
            user = user_service.create_user(
                db,
                email=settings.bootstrap_admin_email,
                username=settings.bootstrap_admin_username,
                password=settings.bootstrap_admin_password,
                full_name=settings.bootstrap_admin_full_name,
            )
            user.role = "admin"
            db.add(user)
            db.commit()
            logger.info(
                "bootstrapped admin user %s (password: %s)",
                settings.bootstrap_admin_email,
                settings.bootstrap_admin_password,
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.warning("bootstrap admin seed failed: %s", exc)
    finally:
        db.close()


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "music-ai backend is running"}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
