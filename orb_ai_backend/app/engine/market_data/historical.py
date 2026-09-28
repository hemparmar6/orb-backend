"""In-memory historical candle provider (seedable)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable

from app.engine.market_data.base import Candle, Interval


class HistoricalCandleProvider:
    """Simple in-memory candle store.

    Real providers (Dhan, Kotak) will implement the same public API in
    Module 3+. Tests seed candles via ``seed()``.
    """

    def __init__(self) -> None:
        # key: (symbol, exchange, interval) -> list[Candle] sorted by ts
        self._store: dict[tuple[str, str, Interval], list[Candle]] = defaultdict(list)

    def seed(self, candles: Iterable[Candle]) -> None:
        for c in candles:
            key = (c.symbol, c.exchange, c.interval)
            self._store[key].append(c)
        # Keep sorted per key.
        for k in list(self._store.keys()):
            self._store[k].sort(key=lambda c: c.ts)

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: datetime,
        exchange: str = "MOCK",
    ) -> list[Candle]:
        key = (symbol, exchange, interval)
        return [c for c in self._store.get(key, []) if start <= c.ts < end]

    def clear(self) -> None:
        self._store.clear()
