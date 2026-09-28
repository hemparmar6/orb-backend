"""Deterministic mock market-data provider.

- Deterministic random walk per symbol (seeded).
- Yields ticks at ``tick_interval_ms`` cadence in ``stream()``.
- Also exposes ``push(quote)`` and ``next_tick(symbol)`` for tests that want
  frame-by-frame control instead of a real-time loop.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterable

from app.engine.market_data.base import Candle, Interval, MarketDataProvider, Quote


class MockMarketDataProvider(MarketDataProvider):
    name = "mock"

    def __init__(
        self,
        *,
        tick_interval_ms: int = 250,
        seed: int = 42,
        initial_prices: dict[str, float] | None = None,
        volatility: float = 0.5,
    ) -> None:
        self._interval = max(0.001, tick_interval_ms / 1000.0)
        self._rng = random.Random(seed)
        self._prices: dict[str, float] = dict(initial_prices or {})
        self._volatility = volatility

        self._subscribed: set[tuple[str, str]] = set()
        self._queue: asyncio.Queue[Quote] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._running = False

    # ---- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="mock-market-data")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ---- subscription -----------------------------------------------------

    async def subscribe(self, symbols: Iterable[str], exchange: str = "MOCK") -> None:
        for s in symbols:
            self._subscribed.add((s, exchange))
            self._prices.setdefault(s, 100.0)

    async def unsubscribe(self, symbols: Iterable[str], exchange: str = "MOCK") -> None:
        for s in symbols:
            self._subscribed.discard((s, exchange))

    # ---- streaming --------------------------------------------------------

    async def stream(self) -> AsyncIterator[Quote]:
        while self._running or not self._queue.empty():
            try:
                q = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if not self._running:
                    return
                continue
            yield q

    async def _loop(self) -> None:
        try:
            while self._running:
                for symbol, exchange in list(self._subscribed):
                    q = self._advance(symbol, exchange)
                    await self._queue.put(q)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            return

    def _advance(self, symbol: str, exchange: str) -> Quote:
        last = self._prices.get(symbol, 100.0)
        drift = (self._rng.random() - 0.5) * 2 * self._volatility
        new_price = max(0.01, round(last + drift, 4))
        self._prices[symbol] = new_price
        return Quote(
            symbol=symbol,
            exchange=exchange,
            price=new_price,
            volume=float(self._rng.randint(1, 100)),
            ts=datetime.now(timezone.utc),
        )

    # ---- test helpers -----------------------------------------------------

    def push(self, quote: Quote) -> None:
        """Enqueue a specific quote (used by tests for deterministic replays)."""
        self._prices[quote.symbol] = quote.price
        self._queue.put_nowait(quote)

    def next_tick(self, symbol: str, exchange: str = "MOCK") -> Quote:
        """Synchronously advance one tick for `symbol` and return it."""
        q = self._advance(symbol, exchange)
        self._queue.put_nowait(q)
        return q

    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = float(price)

    # ---- snapshots + candles ---------------------------------------------

    async def snapshot(self, symbol: str, exchange: str = "MOCK") -> Quote | None:
        if symbol not in self._prices:
            return None
        return Quote(
            symbol=symbol,
            exchange=exchange,
            price=self._prices[symbol],
            volume=0.0,
            ts=datetime.now(timezone.utc),
        )

    async def historical_candles(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: datetime,
        exchange: str = "MOCK",
    ) -> list[Candle]:
        """Synthetic OHLCV series generated from the same seeded RNG.

        Useful for demos / backtests. Not intended to match any real market.
        """
        step = _interval_seconds(interval)
        candles: list[Candle] = []
        cursor = start
        price = self._prices.get(symbol, 100.0)
        rng = random.Random((hash(symbol) ^ int(start.timestamp())) & 0xFFFFFFFF)
        while cursor < end:
            o = price
            h = o + rng.random() * self._volatility * 2
            l_ = o - rng.random() * self._volatility * 2
            c = round(o + (rng.random() - 0.5) * self._volatility * 2, 4)
            price = max(0.01, c)
            candles.append(
                Candle(
                    symbol=symbol,
                    exchange=exchange,
                    interval=interval,
                    ts=cursor,
                    open=round(o, 4),
                    high=round(max(o, h, c), 4),
                    low=round(min(o, l_, c), 4),
                    close=round(c, 4),
                    volume=float(rng.randint(100, 10_000)),
                )
            )
            cursor += timedelta(seconds=step)
        return candles


_SECONDS = {
    Interval.ONE_MIN: 60,
    Interval.THREE_MIN: 180,
    Interval.FIVE_MIN: 300,
    Interval.FIFTEEN_MIN: 900,
    Interval.ONE_HOUR: 3600,
    Interval.ONE_DAY: 86400,
}


def _interval_seconds(i: Interval) -> int:
    return _SECONDS[i]
