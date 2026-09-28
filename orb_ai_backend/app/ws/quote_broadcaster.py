"""QuoteBroadcaster — a single process-scoped ``MockMarketDataProvider``
whose ticks are fanned out to any number of subscribed WebSocket clients.

Design:
- Exactly one provider runs for the whole process.
- Symbols currently in the union of all clients' subscription sets are
  subscribed on the provider; the rest are lazily added/removed on demand.
- Each client has an ``asyncio.Queue`` that this broadcaster pushes matching
  ticks into. The WS handler drains that queue and writes to the wire.
- Backpressure: if a client's queue exceeds ``QUEUE_MAX``, we drop the oldest
  tick for that client only — one slow client never blocks the fan-out.
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.engine.market_data.base import Quote
from app.engine.market_data.mock import MockMarketDataProvider

logger = get_logger(__name__)

QUEUE_MAX = 1000


class QuoteBroadcaster:
    def __init__(self) -> None:
        self._provider: MockMarketDataProvider | None = None
        self._pump_task: asyncio.Task | None = None
        # client_id -> {"queue": Queue, "symbols": set[str]}
        self._clients: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self._provider is not None:
            return
        self._provider = MockMarketDataProvider(
            tick_interval_ms=settings.MOCK_TICK_INTERVAL_MS,
            seed=settings.MOCK_RANDOM_SEED,
        )
        await self._provider.start()
        self._pump_task = asyncio.create_task(self._pump(), name="quote-broadcaster-pump")

    async def stop(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except (asyncio.CancelledError, Exception):
                pass
            self._pump_task = None
        if self._provider is not None:
            await self._provider.stop()
            self._provider = None
        self._clients.clear()

    # ---- client subscriptions ------------------------------------------

    async def connect(self, client_id: int) -> asyncio.Queue:
        async with self._lock:
            self._clients[client_id] = {"queue": asyncio.Queue(maxsize=QUEUE_MAX), "symbols": set()}
            return self._clients[client_id]["queue"]

    async def disconnect(self, client_id: int) -> None:
        async with self._lock:
            self._clients.pop(client_id, None)
            await self._reconcile_provider_subscriptions()

    async def subscribe(self, client_id: int, symbols: list[str]) -> list[str]:
        async with self._lock:
            entry = self._clients.get(client_id)
            if entry is None:
                return []
            entry["symbols"].update(symbols)
            await self._reconcile_provider_subscriptions()
            return sorted(entry["symbols"])

    async def unsubscribe(self, client_id: int, symbols: list[str]) -> list[str]:
        async with self._lock:
            entry = self._clients.get(client_id)
            if entry is None:
                return []
            entry["symbols"].difference_update(symbols)
            await self._reconcile_provider_subscriptions()
            return sorted(entry["symbols"])

    # ---- internals ------------------------------------------------------

    async def _reconcile_provider_subscriptions(self) -> None:
        if self._provider is None:
            return
        needed: set[str] = set()
        for entry in self._clients.values():
            needed.update(entry["symbols"])
        # Provider is idempotent; just re-declare the full set.
        current = {s for (s, _ex) in self._provider._subscribed}
        to_add = needed - current
        to_remove = current - needed
        if to_add:
            await self._provider.subscribe(list(to_add))
        if to_remove:
            await self._provider.unsubscribe(list(to_remove))

    async def _pump(self) -> None:
        assert self._provider is not None
        try:
            async for quote in self._provider.stream():
                self._dispatch(quote)
        except asyncio.CancelledError:
            return

    def _dispatch(self, quote: Quote) -> None:
        # Called from the async context of `_pump`.
        for entry in self._clients.values():
            if quote.symbol not in entry["symbols"]:
                continue
            q: asyncio.Queue = entry["queue"]
            if q.full():
                try:
                    q.get_nowait()  # drop oldest to make room
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(quote)


# ---- process-scoped singleton --------------------------------------------

quote_broadcaster = QuoteBroadcaster()
