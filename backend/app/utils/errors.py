"""Safe error handling utilities.

Every API endpoint should use these helpers instead of ``str(exc)`` in
HTTP response details.  They log the full internal error server-side
but return a stable, generic message to the client --- no infrastructure
details (broker addresses, DNS names, file paths, stack traces) ever
leak to the frontend.

Business errors raised by the service layer should inherit from
:class:`AppError`. The global exception handler catches ``AppError``
subclasses and returns the correct HTTP status code automatically, so
API endpoints don't need repetitive try/except blocks for expected
business failures.
"""

from __future__ import annotations

import logging
import traceback as _tb
from typing import Any

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
MSG_NOT_FOUND = "Resource not found."
MSG_FORBIDDEN = "You do not have permission to access this resource."
MSG_RATE_LIMITED = "Too many requests. Please slow down."


# ---------------------------------------------------------------------------
# Unified business error hierarchy
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base class for expected business errors.

    Attributes:
        status_code: HTTP status code to return to the client.
        code:        Machine-readable error code (e.g. ``"email_taken"``).
        message:     Human-readable message safe to return to the client.
        details:     Optional structured details (validation errors, etc.).
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = MSG_INTERNAL_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Any = None,
        log_message: str | None = None,
    ) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        self.details = details
        self._log_message = log_message
        super().__init__(self.message)

    @property
    def log_message(self) -> str:
        """Internal detail logged server-side (never sent to client)."""
        return self._log_message or self.message

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a client-safe JSON payload."""
        payload: dict[str, Any] = {
            "detail": self.message,
            "code": self.code,
        }
        if self.details is not None:
            payload["details"] = self.details
        return payload


class ClientError(AppError):
    """Base for 4xx errors caused by the client."""

    status_code = status.HTTP_400_BAD_REQUEST


class ValidationError(ClientError):
    """Input validation failure (400)."""

    code = "validation_error"
    message = MSG_INVALID_INPUT

    def __init__(
        self,
        message: str | None = None,
        *,
        field_errors: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            message,
            details={"fields": field_errors} if field_errors else kwargs.get("details"),
            **kwargs,
        )
        if field_errors and "details" not in kwargs:
            self.details = {"fields": field_errors}


class NotFoundError(ClientError):
    """Requested resource does not exist (404)."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = MSG_NOT_FOUND


class AuthError(ClientError):
    """Authentication required or token invalid (401)."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "auth_error"
    message = MSG_SESSION_EXPIRED


class ForbiddenError(ClientError):
    """Authenticated but not allowed (403)."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = MSG_FORBIDDEN


class ConflictError(ClientError):
    """Resource state conflict (409), e.g. duplicate email."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class RateLimitError(ClientError):
    """Too many requests (429)."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = MSG_RATE_LIMITED


class UploadError(ClientError):
    """Upload-specific failure (400/413)."""

    code = "upload_error"


class ServerError(AppError):
    """Base for 5xx server-side errors."""


class ServiceUnavailableError(ServerError):
    """Downstream dependency unavailable (503)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = MSG_SERVICE_UNAVAILABLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log_error(exc: Exception, *, context: str = "") -> None:
    """Log the full exception with traceback server-side.

    Call this in every ``except`` block *before* returning a generic
    message to the client.
    """
    # For AppError subclasses, log the internal message at WARNING level;
    # unexpected exceptions get the full traceback at ERROR.
    if isinstance(exc, AppError):
        msg = f"{context}: {exc.log_message}" if context else exc.log_message
        logger.warning("business error [%s]: %s", exc.code, msg)
        return
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
    if isinstance(exc, AppError):
        return exc.message
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
