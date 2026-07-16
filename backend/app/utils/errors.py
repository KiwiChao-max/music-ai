"""Safe error handling utilities.

Every API endpoint should use these helpers instead of ``str(exc)`` in
HTTP response details.  They log the full internal error server-side
but return a stable, generic message to the client --- no infrastructure
details (broker addresses, DNS names, file paths, stack traces) ever
leak to the frontend.
"""
from __future__ import annotations

import logging
import traceback as _tb

from fastapi import HTTPException, status

logger = logging.getLogger("api.errors")

# ---------------------------------------------------------------------------
# User-safe messages --- returned to the client
# ---------------------------------------------------------------------------

MSG_INTERNAL_ERROR = "An unexpected error occurred. Please try again later."
MSG_UPLOAD_TOO_LARGE = "Upload exceeds the allowed size limit."
MSG_INVALID_INPUT = "Invalid input."
MSG_SERVICE_UNAVAILABLE = "Service temporarily unavailable. Please try again."
MSG_SESSION_EXPIRED = "Session expired. Please log in again."
MSG_INVALID_CREDENTIALS = "Invalid credentials."
MSG_EMAIL_IN_USE = "Email already in use."
MSG_USERNAME_TAKEN = "Username already taken."
MSG_INVALID_ZIP = "Invalid zip archive."
MSG_TASK_DISPATCH_FAILED = (
    "Task could not be dispatched. The task has been reset --- please try again."
)
MSG_TASK_PROCESSING_FAILED = "Task processing failed. Please try again."

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log_error(exc: Exception, *, context: str = "") -> None:
    """Log the full exception with traceback server-side.

    Call this in every ``except`` block *before* returning a generic
    message to the client.
    """
    tb = _tb.format_exception(type(exc), exc, exc.__traceback__)
    detail = "".join(tb).rstrip()
    if context:
        logger.error("%s\n%s", context, detail)
    else:
        logger.error(detail)


def safe_detail(exc: Exception, *, context: str = "") -> str:
    """Log the error and return *only* the generic message safe for the client.

    Example::

        try:
            ...
        except SomeServiceError as exc:
            raise HTTPException(
                status_code=400,
                detail=safe_detail(exc, context="library creation failed"),
            ) from exc
    """
    log_error(exc, context=context)
    return MSG_INTERNAL_ERROR


def raise_400(exc: Exception, *, context: str = "") -> HTTPException:
    """Log the error and return a 400 with a generic message."""
    log_error(exc, context=context)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=MSG_INVALID_INPUT)


def raise_503(exc: Exception, *, context: str = "") -> HTTPException:
    """Log the error and return a 503 with a generic message."""
    log_error(exc, context=context)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=MSG_SERVICE_UNAVAILABLE,
    )