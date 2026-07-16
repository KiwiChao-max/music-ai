"""CSRF protection middleware.

Compares the ``X-CSRF-Token`` request header with the CSRF token stored
in a non-HttpOnly cookie.  Requests that do not match are rejected with
403, preventing cross-site request forgery on state-changing endpoints.

Skips:
  * GET / HEAD / OPTIONS (safe methods)
  * ``/api/auth/`` endpoints (login/register don't have a CSRF cookie yet)
  * ``/ws/`` endpoints (WebSocket connections use JWT, not CSRF)
"""
from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


class CSRFTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        if request.url.path.startswith("/api/auth/"):
            return await call_next(request)
        if request.url.path.startswith("/ws/"):
            return await call_next(request)

        # When auth is disabled (e.g. E2E tests, local dev without auth),
        # there are no user sessions to protect, so CSRF validation is
        # unnecessary and would block unauthenticated uploads.
        if settings.auth_required is False:
            return await call_next(request)

        cookie_token = request.cookies.get(settings.csrf_cookie_name)
        header_token = request.headers.get(
            settings.csrf_header_name.lower(), ""
        )

        if not cookie_token or not header_token:
            return Response(
                content='{"detail":"CSRF token missing"}',
                status_code=403,
                media_type="application/json",
            )
        if not secrets.compare_digest(cookie_token, header_token):
            return Response(
                content='{"detail":"CSRF token mismatch"}',
                status_code=403,
                media_type="application/json",
            )

        return await call_next(request)