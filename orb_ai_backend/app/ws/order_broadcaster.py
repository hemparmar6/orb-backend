"""OrderBroadcaster — subscribes to Redis channel ``orders:{session_id}`` for
every engine session a WebSocket client cares about, and fans messages out.

Requires Redis (``REDIS_ENABLED=true``). If Redis is off, ``ensure_started``
raises ``EngineError`` so the WS handler can close with 1013 "try again".

One background task drains each unique Redis channel and multiplexes to any
number of WS clients subscribed to that session. Each client has an
``asyncio.Queue`` (bounded — oldest-dropped on overflow, same rule as quotes).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

QUEUE_MAX = 1000


class OrderBroadcaster:
    def __init__(self) -> None:
        # session_id -> asyncio.Task (channel drainer)
        self._channel_tasks: dict[str, asyncio.Task] = {}
        # session_id -> set[client_id]
        self._session_to_clients: dict[str, set[int]] = {}
        # client_id -> {"queue": Queue, "sessions": set[str]}
        self._clients: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._started = False

    async def ensure_started(self) -> None:
        client = await get_redis()
        if client is None:
            raise EngineError(
                "Order stream requires Redis. Enable it via REDIS_ENABLED=true and REDIS_URL.",
                code="redis_unavailable",
                status_code=503,
            )
        self._started = True

    async def stop(self) -> None:
        for task in list(self._channel_tasks.values()):
            task.cancel()
        for task in list(self._channel_tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._channel_tasks.clear()
        self._session_to_clients.clear()
        self._clients.clear()
        self._started = False

    async def connect(self, client_id: int) -> asyncio.Queue:
        async with self._lock:
            self._clients[client_id] = {"queue": asyncio.Queue(maxsize=QUEUE_MAX), "sessions": set()}
            return self._clients[client_id]["queue"]

    async def disconnect(self, client_id: int) -> None:
        async with self._lock:
            entry = self._clients.pop(client_id, None)
            if entry is None:
                return
            for sid in entry["sessions"]:
                self._session_to_clients.get(sid, set()).discard(client_id)
                if not self._session_to_clients.get(sid):
                    self._session_to_clients.pop(sid, None)
                    task = self._channel_tasks.pop(sid, None)
                    if task is not None:
                        task.cancel()

    async def subscribe(self, client_id: int, session_ids: list[str]) -> list[str]:
        async with self._lock:
            entry = self._clients.get(client_id)
            if entry is None:
                return []
            for sid in session_ids:
                entry["sessions"].add(sid)
                subs = self._session_to_clients.setdefault(sid, set())
                subs.add(client_id)
                if sid not in self._channel_tasks:
                    self._channel_tasks[sid] = asyncio.create_task(
                        self._drain_channel(sid), name=f"order-drainer-{sid}"
                    )
            return sorted(entry["sessions"])

    async def unsubscribe(self, client_id: int, session_ids: list[str]) -> list[str]:
        async with self._lock:
            entry = self._clients.get(client_id)
            if entry is None:
                return []
            for sid in session_ids:
                entry["sessions"].discard(sid)
                subs = self._session_to_clients.get(sid)
                if subs is not None:
                    subs.discard(client_id)
                    if not subs:
                        self._session_to_clients.pop(sid, None)
                        task = self._channel_tasks.pop(sid, None)
                        if task is not None:
                            task.cancel()
            return sorted(entry["sessions"])

    # ---- internals ------------------------------------------------------

    async def _drain_channel(self, session_id: str) -> None:
        client = await get_redis()
        if client is None:  # pragma: no cover - guarded by ensure_started
            return
        pubsub = client.pubsub()
        channel = f"orders:{session_id}"
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                raw = message.get("data")
                try:
                    payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                except json.JSONDecodeError:  # pragma: no cover
                    logger.warning("order_channel_bad_json",
                                   extra={"session_id": session_id})
                    continue
                self._fan_out(session_id, payload)
        except asyncio.CancelledError:
            return
        except Exception:  # pragma: no cover
            logger.exception("order_channel_drain_error", extra={"session_id": session_id})
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:  # pragma: no cover
                pass

    def _fan_out(self, session_id: str, payload: dict) -> None:
        client_ids = self._session_to_clients.get(session_id, set())
        for cid in client_ids:
            entry = self._clients.get(cid)
            if entry is None:
                continue
            q: asyncio.Queue = entry["queue"]
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(payload)


# ---- process-scoped singleton --------------------------------------------

order_broadcaster = OrderBroadcaster()
