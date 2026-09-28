"""RiskBroadcaster — Milestone 9 follow-up.

Dedicated real-time WebSocket fan-out for risk & execution events:

    - Risk breach (any RiskEventType)
    - Bot auto-pause
    - Daily loss limit reached
    - Execution safety events (rate limits, kill switch, duplicates)
    - Broker disconnected
    - Emergency kill switch

Two audiences per event:

    ``publish_user(user_id, payload)`` — only owning user's clients
    ``publish_admin(payload)``          — every connected admin client

In-memory, per-process, best-effort fan-out. Same design pattern as
``NotificationBroadcaster`` (single-worker friendly). Multi-worker
scale-out is achievable by piping through Redis pub/sub — the interface
is designed to allow that upgrade later without changing callers.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

QUEUE_MAX = 200


class RiskBroadcaster:
    """Bounded per-user + admin fan-out for risk & execution events."""

    def __init__(self) -> None:
        # user_id -> set[asyncio.Queue]
        self._user_queues: dict[str, set[asyncio.Queue]] = {}
        # admin queues (all admins receive every event)
        self._admin_queues: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    # ---- user subscriptions ------------------------------------------

    async def subscribe_user(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        async with self._lock:
            self._user_queues.setdefault(user_id, set()).add(q)
        return q

    async def unsubscribe_user(self, user_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            qs = self._user_queues.get(user_id)
            if qs and q in qs:
                qs.discard(q)
                if not qs:
                    self._user_queues.pop(user_id, None)

    # ---- admin subscriptions ------------------------------------------

    async def subscribe_admin(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        async with self._lock:
            self._admin_queues.add(q)
        return q

    async def unsubscribe_admin(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._admin_queues.discard(q)

    # ---- publish ------------------------------------------------------

    async def publish_user(self, user_id: str, payload: dict[str, Any]) -> int:
        """Send to every connected client for ``user_id``.

        Returns the number of queues that received the payload. Never
        raises — a broken queue is dropped silently."""
        async with self._lock:
            qs = list(self._user_queues.get(user_id, ()))
        return _fanout(qs, payload)

    async def publish_admin(self, payload: dict[str, Any]) -> int:
        """Send to every connected admin WebSocket client."""
        async with self._lock:
            qs = list(self._admin_queues)
        return _fanout(qs, payload)

    async def publish(
        self, user_id: str, payload: dict[str, Any],
        *, also_admin: bool = True,
    ) -> tuple[int, int]:
        """Convenience — publish to both user + admin in one call."""
        u = await self.publish_user(user_id, payload)
        a = await self.publish_admin(payload) if also_admin else 0
        return u, a

    def serialize(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, default=str)

    # ---- introspection ------------------------------------------------

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            n_users = len(self._user_queues)
            n_user_queues = sum(len(v) for v in self._user_queues.values())
            n_admin_queues = len(self._admin_queues)
        return {
            "users_connected": n_users,
            "user_queues": n_user_queues,
            "admin_queues": n_admin_queues,
        }


def _fanout(queues: list[asyncio.Queue], payload: dict[str, Any]) -> int:
    delivered = 0
    for q in queues:
        try:
            if q.full():
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    pass
            q.put_nowait(payload)
            delivered += 1
        except Exception:  # pragma: no cover
            continue
    return delivered


# process-scoped singleton
risk_broadcaster = RiskBroadcaster()
