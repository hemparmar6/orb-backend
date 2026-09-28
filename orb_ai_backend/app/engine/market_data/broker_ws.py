"""Shared base for broker WebSocket market-data providers.

Concrete providers (Dhan, Kotak Neo, …) inherit from
:class:`BrokerWSMarketDataProvider` and only supply broker-specific bits:

- ``_resolve_url()`` — final WS URL (may need to run auth first)
- ``_build_subscribe_frame(items)`` — subscribe payload for the given internal
  broker-native items (already resolved from user-facing symbols)
- ``_build_unsubscribe_frame(items)`` — unsubscribe payload
- ``_decode_frame(raw)`` — turn a raw frame into a list of normalised
  :class:`Quote` objects (empty list for control frames / heartbeats)
- ``_extra_headers()`` (optional) — headers injected into ``websockets.connect``
- ``_authenticate()`` (optional) — one-time auth prior to connecting

The base class handles everything else:

- Idempotent subscribe / unsubscribe with a user-supplied symbol → broker-item map
- Auto-reconnect + auto-resubscribe via :class:`ReconnectingWSClient`
- ``stream()`` async iterator backed by an :class:`asyncio.Queue`
- Snapshot cache (last known price per symbol) for ``snapshot()``
- Latency + counter monitoring (see :meth:`get_stats`)
- Robust error handling — decode / handler errors are counted, never propagate

Provider registration lives in :mod:`app.engine.market_data.registry` — concrete
subclasses decorate themselves with ``@register_provider("<name>")``.
"""
from __future__ import annotations

import asyncio
from abc import abstractmethod
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable, Optional

from app.brokers.websocket.reconnect import ReconnectingWSClient
from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.engine.market_data.base import MarketDataProvider, Quote

logger = get_logger(__name__)


class BrokerWSMarketDataProvider(MarketDataProvider):
    """Base class for WebSocket-driven broker market-data providers.

    Subclasses must implement:
        * :meth:`_resolve_url`
        * :meth:`_build_subscribe_frame`
        * :meth:`_build_unsubscribe_frame`
        * :meth:`_decode_frame`

    Subclasses may override:
        * :meth:`_extra_headers`
        * :meth:`_authenticate`
        * :meth:`_encode_send` (default: JSON-encode)
        * :attr:`default_exchange`
    """

    #: Name to use when the provider is registered. Concrete subclasses set this
    #: via ``@register_provider(name)``.
    name: str = "broker_ws_abstract"

    #: Default exchange returned in Quote objects when the broker frame omits it.
    default_exchange: str = ""

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        symbol_map: Optional[dict[str, Any]] = None,
        ping_interval_s: Optional[float] = 20.0,
        ping_timeout_s: Optional[float] = 10.0,
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 30.0,
        max_consecutive_failures: Optional[int] = None,
        queue_maxsize: int = 10_000,
    ) -> None:
        self._credentials = dict(credentials or {})
        # user_symbol -> broker-native item (e.g. security_id / (token, segment))
        self._symbol_map: dict[str, Any] = dict(symbol_map or {})
        # canonical broker key -> (user_symbol, exchange) so we can reverse a
        # tick's identifiers back to the caller's symbol space.
        self._reverse_map: dict[str, tuple[str, str]] = {}

        self._ping_interval_s = ping_interval_s
        self._ping_timeout_s = ping_timeout_s
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._max_consecutive_failures = max_consecutive_failures

        # currently-subscribed (symbol, exchange) tuples
        self._subscribed: set[tuple[str, str]] = set()
        self._snapshot_cache: dict[tuple[str, str], Quote] = {}

        self._queue: asyncio.Queue[Quote] = asyncio.Queue(maxsize=queue_maxsize)
        self._client: Optional[ReconnectingWSClient] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._running = False

        # ---- observability ----
        self._stats = {
            "ticks_received": 0,
            "ticks_dropped_full_queue": 0,
            "decode_errors": 0,
            "unknown_ticks": 0,  # tick for a symbol we're not subscribed to
            "reconnects": 0,
            "latency_ms_last": 0.0,
            "latency_ms_min": None,  # type: Optional[float]
            "latency_ms_max": 0.0,
            "latency_ms_sum": 0.0,
            "latency_ms_count": 0,
        }

    # ------------------------------------------------------------------ hooks

    @abstractmethod
    async def _resolve_url(self) -> str:
        """Return the final WS URL, including any auth query params."""

    @abstractmethod
    def _build_subscribe_frame(self, items: list[Any]) -> Any:
        """Return a payload to send after connect / on new subscribe."""

    @abstractmethod
    def _build_unsubscribe_frame(self, items: list[Any]) -> Any:
        """Return a payload to send when symbols are unsubscribed."""

    @abstractmethod
    def _decode_frame(self, raw: Any) -> list[Quote]:
        """Turn a raw broker frame into zero or more normalised Quote objects.

        Return an empty list for heartbeats / acks / any non-tick frame.
        """

    def _extra_headers(self) -> dict[str, str]:
        """Headers injected into ``websockets.connect``."""
        return {}

    async def _authenticate(self) -> None:
        """One-time authentication before the first connect.

        Default no-op. Kotak Neo overrides this to log in and obtain ``sid``.
        """

    def _encode_send(self, frame: Any) -> Any:
        """Turn a subscribe/unsubscribe frame into what ``ws.send`` accepts.

        Default: pass strings and bytes through, JSON-encode dicts/lists.
        """
        import json

        if isinstance(frame, (str, bytes, bytearray)):
            return frame
        return json.dumps(frame)

    # ---------------------------------------------------------- symbol resolution

    def add_symbols(self, mapping: dict[str, Any]) -> None:
        """Extend the symbol → broker-item map at runtime.

        Callers with an instrument master (Dhan security-id CSV, Kotak scrip
        master) can seed the provider once and forget about it.
        """
        self._symbol_map.update(mapping)
        # Recompute reverse map lazily on subscribe.

    def _resolve_symbol(self, symbol: str) -> Any:
        if symbol not in self._symbol_map:
            raise EngineError(
                f"{self.name}: symbol '{symbol}' is not in the provider's symbol_map",
                code="unknown_symbol",
            )
        return self._symbol_map[symbol]

    def _canonical_key(self, item: Any) -> str:
        """Stringify a broker item for use as a reverse-map key."""
        if isinstance(item, (list, tuple)):
            return "|".join(str(x) for x in item)
        return str(item)

    # -------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._running:
            return
        await self._authenticate()
        self._running = True
        self._client = ReconnectingWSClient(
            url_provider=self._resolve_url,
            broker=self.name,
            on_connect=self._on_connect,
            decode=lambda raw: raw,  # decode inside the consumer to count errors
            ping_interval_s=self._ping_interval_s,
            ping_timeout_s=self._ping_timeout_s,
            backoff_base_s=self._backoff_base_s,
            backoff_max_s=self._backoff_max_s,
            max_consecutive_failures=self._max_consecutive_failures,
            extra_headers=self._extra_headers(),
        )
        self._consumer_task = asyncio.create_task(
            self._consume(), name=f"{self.name}-market-data-consumer"
        )

    async def stop(self) -> None:
        self._running = False
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.close()
            except Exception:  # pragma: no cover
                pass
        task = self._consumer_task
        self._consumer_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    # ---------------------------------------------------------- subscribe API

    async def subscribe(self, symbols: Iterable[str], exchange: str = "") -> None:
        exch = exchange or self.default_exchange
        new: list[Any] = []
        for s in symbols:
            key = (s, exch)
            if key in self._subscribed:
                continue
            item = self._resolve_symbol(s)
            self._subscribed.add(key)
            self._reverse_map[self._canonical_key(item)] = (s, exch)
            new.append(item)
        if new and self._client and self._client.is_connected():
            try:
                await self._client.send(
                    self._encode_send(self._build_subscribe_frame(new))
                )
            except Exception:
                logger.exception(
                    "market_data_subscribe_send_failed",
                    extra={"provider": self.name, "count": len(new)},
                )

    async def unsubscribe(self, symbols: Iterable[str], exchange: str = "") -> None:
        exch = exchange or self.default_exchange
        removed: list[Any] = []
        for s in symbols:
            key = (s, exch)
            if key not in self._subscribed:
                continue
            self._subscribed.discard(key)
            self._snapshot_cache.pop(key, None)
            item = self._symbol_map.get(s)
            if item is not None:
                removed.append(item)
                self._reverse_map.pop(self._canonical_key(item), None)
        if removed and self._client and self._client.is_connected():
            try:
                await self._client.send(
                    self._encode_send(self._build_unsubscribe_frame(removed))
                )
            except Exception:
                logger.exception(
                    "market_data_unsubscribe_send_failed",
                    extra={"provider": self.name, "count": len(removed)},
                )

    # -------------------------------------------------------------- streaming

    async def stream(self) -> AsyncIterator[Quote]:
        while self._running or not self._queue.empty():
            try:
                q = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if not self._running:
                    return
                continue
            yield q

    async def snapshot(self, symbol: str, exchange: str = "") -> Quote | None:
        exch = exchange or self.default_exchange
        return self._snapshot_cache.get((symbol, exch))

    # ----------------------------------------------------------- observability

    def get_stats(self) -> dict[str, Any]:
        """Snapshot of counters + latency statistics (safe to call anytime)."""
        s = dict(self._stats)
        count = s["latency_ms_count"] or 1
        s["latency_ms_avg"] = round(s["latency_ms_sum"] / count, 3) if s["latency_ms_count"] else 0.0
        s["subscribed_symbols"] = sorted(sym for sym, _ex in self._subscribed)
        s["is_running"] = self._running
        s["is_connected"] = bool(self._client and self._client.is_connected())
        return s

    def reset_stats(self) -> None:
        for k in (
            "ticks_received",
            "ticks_dropped_full_queue",
            "decode_errors",
            "unknown_ticks",
            "reconnects",
            "latency_ms_sum",
            "latency_ms_count",
            "latency_ms_max",
        ):
            self._stats[k] = 0
        self._stats["latency_ms_last"] = 0.0
        self._stats["latency_ms_min"] = None

    # ------------------------------------------------------------ internals

    async def _on_connect(self, ws: Any) -> None:
        """Called by ReconnectingWSClient after each successful (re)connect.

        Sends a fresh subscribe frame for every currently-subscribed symbol.
        """
        # A reconnect count of >0 means this is not the first connect.
        # (The client resets its own failure counter here.)
        if self._stats["ticks_received"] > 0 or self._stats["reconnects"] > 0:
            # Every connect after the very first is a reconnect.
            pass  # counter is bumped in the consumer loop when a close is observed
        if not self._subscribed:
            return
        items = [self._symbol_map[s] for s, _ex in self._subscribed if s in self._symbol_map]
        if not items:
            return
        try:
            await ws.send(self._encode_send(self._build_subscribe_frame(items)))
        except Exception:
            logger.exception(
                "market_data_on_connect_subscribe_failed",
                extra={"provider": self.name, "count": len(items)},
            )

    async def _consume(self) -> None:
        assert self._client is not None
        was_connected = False
        try:
            async for raw in self._client:
                # Track transitions from disconnected → connected as reconnects.
                if self._client.is_connected() and not was_connected:
                    if self._stats["ticks_received"] > 0:
                        # We had already been up once — this connect is a reconnect.
                        self._stats["reconnects"] += 1
                    was_connected = True
                elif not self._client.is_connected():
                    was_connected = False
                try:
                    quotes = self._decode_frame(raw)
                except Exception:
                    self._stats["decode_errors"] += 1
                    logger.exception(
                        "market_data_decode_error",
                        extra={"provider": self.name},
                    )
                    continue
                if not quotes:
                    continue
                for quote in quotes:
                    self._handle_quote(quote)
        except asyncio.CancelledError:
            return
        except Exception:  # pragma: no cover
            logger.exception(
                "market_data_consumer_crashed",
                extra={"provider": self.name},
            )

    def _handle_quote(self, quote: Quote) -> None:
        key = (quote.symbol, quote.exchange or self.default_exchange)
        if self._subscribed and key not in self._subscribed:
            self._stats["unknown_ticks"] += 1
            return
        self._snapshot_cache[key] = quote
        self._stats["ticks_received"] += 1
        # Latency: ts is the exchange timestamp, now is process time.
        try:
            now = datetime.now(timezone.utc)
            lag_ms = max(0.0, (now - quote.ts).total_seconds() * 1000.0)
            self._stats["latency_ms_last"] = round(lag_ms, 3)
            self._stats["latency_ms_sum"] += lag_ms
            self._stats["latency_ms_count"] += 1
            if lag_ms > self._stats["latency_ms_max"]:
                self._stats["latency_ms_max"] = round(lag_ms, 3)
            prev_min = self._stats["latency_ms_min"]
            if prev_min is None or lag_ms < prev_min:
                self._stats["latency_ms_min"] = round(lag_ms, 3)
        except Exception:  # pragma: no cover — never let stats break a tick
            pass
        try:
            self._queue.put_nowait(quote)
        except asyncio.QueueFull:
            self._stats["ticks_dropped_full_queue"] += 1
