"""Content-Security-Policy middleware.

Adds a strict CSP header to every response so the browser refuses to
execute inline scripts or load resources from untrusted origins.  This
is the primary defence against XSS: even if an attacker manages to
inject a ``<script>`` tag, the browser won't execute it.

In production mode the policy is strict (no ``unsafe-inline``, no
``unsafe-eval``).  In development mode we relax ``style-src`` to allow
``unsafe-inline`` (Tailwind JIT + HMR) and ``connect-src`` to allow
``ws://`` for the Vite dev server.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


class CSPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        if settings.production_mode:
            directive = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "media-src 'self' blob:; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            )
        else:
            # Allow Vite dev server (HMR over WebSocket) and inline
            # styles for Tailwind JIT.
            directive = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self' ws://localhost:* wss://localhost:*; "
                "media-src 'self' blob:; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            )

        response.headers["Content-Security-Policy"] = directive
        return response
