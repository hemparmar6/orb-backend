"""QuoteBroadcaster for mock quotes and already-running live broker streams.

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
from app.engine.market_data.broker_ws import BrokerWSMarketDataProvider
from app.engine.market_data.mock import MockMarketDataProvider
from app.monitoring.metrics import metrics

logger = get_logger(__name__)

QUEUE_MAX = 1000


class QuoteBroadcaster:
    def __init__(self) -> None:
        self._provider: MockMarketDataProvider | None = None
        self._pump_task: asyncio.Task | None = None
        # id(provider) -> (provider, owner user id, listener)
        self._real_providers: dict[int, tuple[BrokerWSMarketDataProvider, str, Any]] = {}
        # client_id -> {"queue": Queue, "symbols": set[str]}
        self._clients: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        # Real feeds are owned and started by the trading engine. Never create
        # a chart-specific broker connection (or fall back to mock in real mode).
        if (settings.MARKET_DATA_PROVIDER or "").strip().lower() != "mock":
            await self._stop_mock_provider()
            return
        if self._provider is not None:
            return
        self._provider = MockMarketDataProvider(
            tick_interval_ms=settings.MOCK_TICK_INTERVAL_MS,
            seed=settings.MOCK_RANDOM_SEED,
        )
        await self._provider.start()
        self._pump_task = asyncio.create_task(self._pump(), name="quote-broadcaster-pump")

    async def stop(self) -> None:
        await self._stop_mock_provider()
        for provider, _user_id, listener in self._real_providers.values():
            provider.remove_quote_listener(listener)
        self._real_providers.clear()
        self._clients.clear()

    async def _stop_mock_provider(self) -> None:
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

    def attach_provider(self, provider: Any, user_id: str) -> bool:
        """Fan out a live runner's existing provider ticks without new sockets."""
        if not isinstance(provider, BrokerWSMarketDataProvider):
            return False
        key = id(provider)
        if key in self._real_providers:
            return True
        owner_id = str(user_id)

        def _on_quote(quote: Quote) -> None:
            self._dispatch(quote, owner_user_id=owner_id)

        provider.add_quote_listener(_on_quote)
        self._real_providers[key] = (provider, owner_id, _on_quote)
        return True

    def detach_provider(self, provider: Any) -> None:
        entry = self._real_providers.pop(id(provider), None)
        if entry is not None:
            entry[0].remove_quote_listener(entry[2])

    def has_real_provider(self, user_id: str | int) -> bool:
        owner_id = str(user_id)
        return any(
            provider._running and owner == owner_id
            for provider, owner, _listener in self._real_providers.values()
        )

    # ---- client subscriptions ------------------------------------------

    async def connect(self, client_id: int, user_id: str | int | None = None) -> asyncio.Queue:
        async with self._lock:
            self._clients[client_id] = {
                "queue": asyncio.Queue(maxsize=QUEUE_MAX),
                "symbols": set(),
                "user_id": str(user_id) if user_id is not None else None,
            }
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

    def _dispatch(self, quote: Quote, *, owner_user_id: str | None = None) -> None:
        # Called from the async context of `_pump`.
        metrics.record_market_data("quote_ticks_forwarded")
        for entry in self._clients.values():
            if owner_user_id is not None and entry["user_id"] != owner_user_id:
                continue
            if quote.symbol not in entry["symbols"]:
                continue
            q: asyncio.Queue = entry["queue"]
            if q.full():
                try:
                    q.get_nowait()  # drop oldest to make room
                    metrics.record_market_data("quote_ticks_dropped")
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(quote)
            except asyncio.QueueFull:
                metrics.record_market_data("quote_ticks_dropped")


# ---- process-scoped singleton --------------------------------------------

quote_broadcaster = QuoteBroadcaster()
