"""Notification WebSocket broadcaster (Module 8).

In-memory, per-process fan-out of notifications to authenticated clients.
Falls back gracefully if no clients are connected — publish is best-effort.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

QUEUE_MAX = 200


class NotificationBroadcaster:
    def __init__(self) -> None:
        # user_id -> set[asyncio.Queue]
        self._queues: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        async with self._lock:
            self._queues.setdefault(user_id, set()).add(q)
        return q

    async def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            qs = self._queues.get(user_id)
            if qs and q in qs:
                qs.discard(q)
                if not qs:
                    self._queues.pop(user_id, None)

    async def publish(self, user_id: str, payload: dict[str, Any]) -> int:
        """Publish to every connected client for `user_id`. Returns delivered count."""
        async with self._lock:
            qs = list(self._queues.get(user_id, ()))
        delivered = 0
        for q in qs:
            try:
                if q.full():
                    _ = q.get_nowait()  # drop oldest
                q.put_nowait(payload)
                delivered += 1
            except Exception:  # pragma: no cover
                continue
        return delivered

    def serialize(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, default=str)


notification_broadcaster = NotificationBroadcaster()
