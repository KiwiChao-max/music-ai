"""FastAPI application entry point."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from app.api.auth import router as auth_router
from app.api.audio import router as audio_router
from app.api.health import router as health_router
from app.api.instruments import instruments_router
from app.api.tasks import router as tasks_router
from app.api.ws import router as ws_router
from app.config import settings
from app.middleware.csp import CSPMiddleware
from app.middleware.csrf import CSRFTokenMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.utils.errors import MSG_INTERNAL_ERROR

logger = logging.getLogger(__name__)

app = FastAPI(title="music-ai", version="0.1.0")

# Middleware order (last-added-first-executed for request, first-added-
# last-executed for response):
#   1. CSP --- adds Content-Security-Policy header to every response
#   2. CSRF --- rejects state-changing requests that lack the X-CSRF-Token header
#   3. Rate limiting --- counts requests and enforces per-IP limits
#   4. CORS --- adds CORS headers (including on 429 / 403 responses)
app.add_middleware(CSPMiddleware)
app.add_middleware(CSRFTokenMiddleware)
app.add_middleware(RateLimitMiddleware)

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


# ---- global exception handler ----------------------------------------------
# Catches any unhandled exception that escapes an endpoint.  Logs the full
# traceback server-side but returns only a generic message to the client ---
# no infrastructure details (file paths, broker addresses, stack traces) ever
# leak to the frontend.

from fastapi import Request
from fastapi.responses import JSONResponse


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": MSG_INTERNAL_ERROR},
    )

settings.storage_dir.mkdir(parents=True, exist_ok=True)

# Never mount the storage root as static files. It holds raw uploads and task
# outputs, so a static mount would bypass the ownership checks in the API.
# `app.api.tasks` exposes specific artifacts through a task-scoped endpoint.


@app.on_event("startup")
def _startup_checks() -> None:
    """Seed a default admin user for local development so the SPA
    has something to log in with on a fresh install.

    Production security checks (JWT secret, DB password, etc.) are
    enforced by Settings.model_validator at config-load time, so the
    app won't even reach this point if they fail.
    """
    _seed_bootstrap_admin()


def _seed_bootstrap_admin() -> None:
    """Idempotently create a default admin user on startup.

    The credentials come from settings (`bootstrap_admin_*`); leaving
    the email empty in env disables the seed. If the user already
    exists, we do nothing --- never overwrite a password that an
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
                "bootstrapped admin user %s", settings.bootstrap_admin_email,
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
