"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.api.audio import router as audio_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.instruments import instruments_router
from app.api.tasks import router as tasks_router
from app.api.ws import router as ws_router
from app.config import settings
from app.logging_config import (
    new_request_id,
    reset_request_id,
    reset_user_id,
    set_request_id,
    setup_logging,
)
from app.middleware.csp import CSPMiddleware
from app.middleware.csrf import CSRFTokenMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.utils.errors import MSG_INTERNAL_ERROR, AppError

# Initialize logging BEFORE creating loggers or importing modules that log.
setup_logging()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="music-ai",
    version="0.1.0",
    summary="AI-powered music processing API",
    description=(
        "Upload an audio file, run Demucs source separation (6 stems), "
        "Basic Pitch MIDI transcription, and ADTOS drum detection through "
        "a Celery-backed async pipeline. Results include separated stems, "
        "per-instrument GM/XG MIDI files, drum event lists, BPM/key/chord "
        "analysis, and Web Audio sample playback with velocity-layered "
        "user-uploaded drum libraries.\n\n"
        "## Authentication\n"
        "When `AUTH_REQUIRED=true`, all task endpoints require a Bearer JWT. "
        "Use `/api/auth/register` and `/api/auth/login` to obtain credentials. "
        "Auth is opt-in for local development.\n\n"
        "## Task Lifecycle\n"
        "1. `POST /api/audio/upload` - upload file, receive `task_id`\n"
        "2. `POST /api/tasks/{task_id}/process` - enqueue processing\n"
        "3. Connect to `WS /api/ws/tasks/{task_id}/progress` for live updates "
        "(or poll `GET /api/tasks/{task_id}` as fallback)\n"
        "4. When status is `finished`, fetch stems, MIDI, analysis, and drum events"
    ),
    contact={
        "name": "music-ai",
    },
    license_info={
        "name": "MIT",
    },
    openapi_tags=[
        {
            "name": "auth",
            "description": "Registration, login, token refresh, and user management.",
        },
        {
            "name": "audio",
            "description": "Audio file upload and validation. Returns a task id for processing.",
        },
        {
            "name": "tasks",
            "description": "Task listing, status polling, processing trigger, and artifact download (stems, MIDI, analysis, drum events).",
        },
        {
            "name": "instruments",
            "description": "Sample library management, SoundFont (SF2) upload, CSV voice table import, and sample classification.",
        },
        {
            "name": "websocket",
            "description": "WebSocket endpoint for real-time task progress streaming via Redis Pub/Sub.",
        },
        {
            "name": "health",
            "description": "Liveness, readiness, storage usage, and Prometheus metrics endpoints.",
        },
    ],
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---- Request-ID middleware --------------------------------------------------
# Generates (or inherits from ``X-Request-ID``) a short id for every request
# and injects it into the logging context so every log line produced while
# handling the request carries the same id. Also attaches ``user_id`` when
# authentication succeeds so log lines can be filtered by user.
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID", "").strip()
        request_id = incoming[:32] if incoming else new_request_id()
        set_request_id(request_id)

        # user_id will be set later by the auth dependency when a valid
        # Bearer token is present; we leave it as "" until then.
        reset_user_id()

        try:
            response = await call_next(request)
        except Exception:
            # Do NOT reset context vars here --- the global exception
            # handler still needs request_id to log the error and to
            # set the X-Request-ID response header. We reset in finally
            # after the response (or error response) is built.
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id()
            reset_user_id()


# Middleware order (last-added-first-executed for request, first-added-
# last-executed for response):
#   1. RequestId --- sets context var before any other middleware logs
#   2. CSP --- adds Content-Security-Policy header to every response
#   3. CSRF --- rejects state-changing requests that lack the X-CSRF-Token header
#   4. Rate limiting --- counts requests and enforces per-IP limits
#   5. CORS --- adds CORS headers (including on 429 / 403 responses)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(CSPMiddleware)
app.add_middleware(CSRFTokenMiddleware)
app.add_middleware(RateLimitMiddleware)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
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


@app.exception_handler(AppError)
async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle expected business errors (4xx) raised directly from services."""
    from app.logging_config import get_request_id

    request_id = get_request_id() or new_request_id()
    logger.warning(
        "business error on %s %s: [%s] %s",
        request.method,
        request.url.path,
        exc.code,
        exc.log_message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    from app.logging_config import get_request_id

    request_id = get_request_id() or new_request_id()
    logger.exception(
        "unhandled exception on %s %s",
        request.method,
        request.url.path,
        extra={"path": request.url.path, "method": request.method},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": MSG_INTERNAL_ERROR},
        headers={"X-Request-ID": request_id},
    )


settings.storage_dir.mkdir(parents=True, exist_ok=True)

# Never mount the storage root as static files. It holds raw uploads and task
# outputs, so a static mount would bypass the ownership checks in the API.
# `app.api.tasks` exposes specific artifacts through a task-scoped endpoint.


@app.on_event("startup")
def _startup_checks() -> None:
    """Run startup security warnings and seed a default admin user.

    Production security checks (JWT secret, DB password, etc.) are
    enforced by Settings.model_validator at config-load time, so the
    app won't even reach this point if they fail.
    """
    _log_security_warnings()
    _seed_bootstrap_admin()


def _log_security_warnings() -> None:
    """Emit loud WARNING logs for any insecure default configuration.

    These are non-fatal in dev mode (PRODUCTION_MODE=false) so local
    development keeps working, but they make it impossible to miss that
    production credentials have not been configured.
    """
    warnings: list[str] = []
    if settings.jwt_secret == "dev-only-secret-please-change-in-production":
        warnings.append("JWT_SECRET is using the dev default --- tokens are forgeable!")
    if settings.db_password == "postgres123":
        warnings.append("DB_PASSWORD is using the dev default ('postgres123')!")
    if settings.bootstrap_admin_password == "admin1234":
        warnings.append("BOOTSTRAP_ADMIN_PASSWORD is using the dev default ('admin1234')!")
    dev_cors = {"http://localhost:5173", "http://127.0.0.1:5173"}
    if set(settings.cors_origins) == dev_cors:
        warnings.append("CORS_ORIGINS is using dev defaults (localhost only).")
    if not settings.production_mode:
        warnings.append("PRODUCTION_MODE is false --- debug-friendly settings are active.")
    if warnings:
        logger.warning("=" * 60)
        logger.warning("SECURITY WARNINGS (non-fatal in dev mode):")
        for w in warnings:
            logger.warning("  ⚠️  %s", w)
        logger.warning("Set PRODUCTION_MODE=true to enforce strict checks.")
        logger.warning("=" * 60)


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
        existing = user_service.get_user_by_email(db, settings.bootstrap_admin_email)
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
                "bootstrapped admin user %s",
                settings.bootstrap_admin_email,
            )
        except Exception as exc:
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
