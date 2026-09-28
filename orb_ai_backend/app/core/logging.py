"""Structured logging configuration.

- JSON output in production (LOG_JSON=true), human-readable in dev.
- Every log record can be enriched with a `request_id` via the middleware
  in `app/middleware/request_id.py`.
- Optional rotating file handler (Module 10) via LOG_FILE_ENABLED.
- In-memory ring buffer (Module 10) so the Admin Dashboard log viewer
  can tail recent events without a persistent log store.
"""
from __future__ import annotations

import logging
import os
import sys
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from typing import Any

from pythonjsonlogger import json as jsonlogger

from app.core.config import settings

# Context var populated by the RequestIDMiddleware per-request.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    """Attach the current request_id (if any) to every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.request_id = request_id_ctx.get() or "-"
        return True


class _JsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that always emits a stable set of keys."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record.setdefault("request_id", getattr(record, "request_id", "-"))


def configure_logging() -> None:
    """Configure the root logger. Idempotent."""
    root = logging.getLogger()
    # Reset any pre-existing handlers (e.g. from uvicorn) so we control output.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())

    if settings.LOG_JSON:
        formatter: logging.Formatter = _JsonFormatter(
            "%(asctime)s %(level)s %(logger)s %(request_id)s %(message)s",
            rename_fields={"asctime": "timestamp"},
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(name)s] [rid=%(request_id)s] %(message)s"
        )

    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL)

    # Optional rotating file handler (Module 10)
    if settings.LOG_FILE_ENABLED:
        try:
            log_dir = os.path.dirname(settings.LOG_FILE_PATH)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            file_handler = RotatingFileHandler(
                settings.LOG_FILE_PATH,
                maxBytes=settings.LOG_FILE_MAX_BYTES,
                backupCount=settings.LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.addFilter(RequestIdFilter())
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except Exception:  # pragma: no cover — never block startup on log FS
            root.warning("log_file_handler_disabled_io_error", exc_info=True)

    # In-memory ring buffer used by the Admin Dashboard log viewer.
    # Imported lazily to avoid a circular import (monitoring imports logging).
    try:
        from app.monitoring.log_buffer import log_buffer
        # Ensure we don't double-register on hot reload.
        if not any(isinstance(h, type(log_buffer)) for h in root.handlers):
            log_buffer.addFilter(RequestIdFilter())
            log_buffer.setLevel(logging.DEBUG)  # capture everything then filter on read
            root.addHandler(log_buffer)
    except Exception:  # pragma: no cover
        pass

    # Tame noisy libraries in production.
    for noisy in ("uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(
            "WARNING" if settings.APP_ENV == "production" else settings.LOG_LEVEL
        )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
