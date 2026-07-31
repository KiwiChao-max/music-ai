"""Unified logging configuration.

Provides:
- ``setup_logging()``  - one-call initialisation from Settings (level, JSON/text).
- ``RequestIdFilter``   - injects ``request_id`` and ``user_id`` into log records.
- ``get_request_id()``  - contextvar accessor used by the middleware.

JSON mode emits one JSON object per line (ELK/Loki-friendly) without any
third-party dependency (uses stdlib ``json``). Text mode uses a concise
coloured format suitable for local development.
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from .config import settings

# Context variable carries the current request id within the async task.
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_user_id_var: ContextVar[str] = ContextVar("user_id", default="")


def get_request_id() -> str:
    """Return the request id for the current async context, or ''."""
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


def reset_request_id() -> None:
    _request_id_var.set("")


def set_user_id(user_id: Any) -> None:
    _user_id_var.set(str(user_id) if user_id is not None else "")


def reset_user_id() -> None:
    _user_id_var.set("")


def new_request_id() -> str:
    """Generate a short (8-char) request id."""
    return uuid.uuid4().hex[:8]


class RequestIdFilter(logging.Filter):
    """Logging filter that attaches request_id / user_id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get() or "-"
        record.user_id = _user_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter (stdlib-only, no external dependency)."""

    _LEVEL_MAP = {
        logging.CRITICAL: "FATAL",
        logging.ERROR: "ERROR",
        logging.WARNING: "WARN",
        logging.INFO: "INFO",
        logging.DEBUG: "DEBUG",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": self._LEVEL_MAP.get(record.levelno, record.levelname),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Include any extra attributes attached to the record.
        _skip = {
            "args", "asctime", "created", "exc_info", "exc_text",
            "filename", "funcName", "levelname", "levelno", "lineno",
            "module", "msecs", "message", "msg", "name", "pathname",
            "process", "processName", "relativeCreated", "stack_info",
            "thread", "threadName", "request_id", "user_id",
            "taskName",  # Python 3.12+ asyncio task name; not useful
        }
        for key, value in record.__dict__.items():
            if key not in _skip:
                try:
                    json.dumps(value)  # ensure serialisable
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    _COLORS = {
        logging.DEBUG: "\033[37m",     # gray
        logging.INFO: "\033[36m",      # cyan
        logging.WARNING: "\033[33m",   # yellow
        logging.ERROR: "\033[31m",     # red
        logging.CRITICAL: "\033[1;31m",  # bold red
    }
    _RESET = "\033[0m"

    def __init__(self, use_color: bool = True) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-5s [%(name)s] %(request_id)s %(user_id)s %(message)s",
            datefmt="%H:%M:%S",
        )
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        if self.use_color and record.levelno in self._COLORS:
            record.levelname = f"{self._COLORS[record.levelno]}{record.levelname:5s}{self._RESET}"
        return super().format(record)


def setup_logging() -> None:
    """Configure the root logger according to Settings.

    Call this once at application startup (API main.py, worker entry points).
    Safe to call multiple times; replaces existing handlers.
    """
    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any pre-existing handlers to avoid duplicate logs when called
    # multiple times (e.g. in tests).
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(level)
    stream.addFilter(RequestIdFilter())

    if settings.log_json:
        stream.setFormatter(JsonFormatter())
    else:
        # Auto-detect colour support: enable ANSI codes when attached to a TTY.
        use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        stream.setFormatter(TextFormatter(use_color=use_color))

    root.addHandler(stream)

    # Quiet overly chatty third-party loggers regardless of root level.
    for noisy in ("uvicorn.access", "multipart", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root.debug(
        "logging configured",
        extra={"level": level_name, "json": settings.log_json},
    )
