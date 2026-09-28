"""In-memory ring-buffer log store consumed by the Admin Dashboard.

Wired as a ``logging.Handler`` in ``app.core.logging.configure_logging``.
Records are kept per-category (application / error / security / ai /
audit / trade) so the dashboard can filter without scanning every event.

Persistent long-term storage is the responsibility of the platform's
log shipper (JSON stdout → journald / fluent-bit / loki / cloudwatch).
This buffer is only a short-term operator convenience.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Deque, Iterable


_DEFAULT_CAPACITY = 2000
_CATEGORIES = ("application", "error", "security", "ai", "audit", "trade", "access")


class LogBuffer(logging.Handler):
    """Bounded ring-buffer log handler."""

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        super().__init__()
        self._lock = RLock()
        self._buffers: dict[str, Deque[dict[str, Any]]] = {
            cat: deque(maxlen=capacity) for cat in _CATEGORIES
        }

    # ------------------------------------------------------------------ emit

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            category = self._categorise(record)
            entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": getattr(record, "request_id", "-"),
                "category": category,
            }
            # Attach any structured extras (skip built-in LogRecord slots)
            skip = {
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "module", "msecs",
                "message", "msg", "name", "pathname", "process", "processName",
                "relativeCreated", "stack_info", "thread", "threadName",
                "taskName", "request_id",
            }
            for k, v in record.__dict__.items():
                if k in skip or k.startswith("_"):
                    continue
                try:
                    entry[k] = v if _jsonable(v) else str(v)
                except Exception:
                    continue
            with self._lock:
                self._buffers[category].append(entry)
                if category != "application":
                    self._buffers["application"].append(entry)
        except Exception:  # pragma: no cover
            self.handleError(record)

    def _categorise(self, record: logging.LogRecord) -> str:
        explicit = getattr(record, "category", None)
        if isinstance(explicit, str) and explicit in _CATEGORIES:
            return explicit
        if record.levelno >= logging.ERROR:
            return "error"
        name = (record.name or "").lower()
        if "security" in name or "auth" in name:
            return "security"
        if "audit" in name:
            return "audit"
        if name.startswith("app.ai") or "openai" in name or "llm" in name:
            return "ai"
        if "trade" in name or "engine" in name or "broker" in name:
            return "trade"
        if name.endswith(".access"):
            return "access"
        return "application"

    # ------------------------------------------------------------------ read

    def tail(
        self,
        category: str = "application",
        limit: int = 200,
        level: str | None = None,
        contains: str | None = None,
    ) -> list[dict[str, Any]]:
        category = category if category in _CATEGORIES else "application"
        with self._lock:
            items = list(self._buffers[category])
        # Filter (newest last → reverse to newest-first)
        items.reverse()
        if level:
            level = level.upper()
            items = [e for e in items if e.get("level", "").upper() == level]
        if contains:
            needle = contains.lower()
            items = [e for e in items if needle in (e.get("message") or "").lower()]
        return items[: max(1, min(limit, 1000))]

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {cat: len(buf) for cat, buf in self._buffers.items()}

    def clear(self) -> None:
        with self._lock:
            for buf in self._buffers.values():
                buf.clear()

    def categories(self) -> Iterable[str]:
        return _CATEGORIES


def _jsonable(v: Any) -> bool:
    return isinstance(v, (str, int, float, bool, list, dict, tuple, type(None)))


# Process-scoped singleton
log_buffer = LogBuffer()
