"""Market data abstract interface + DTOs.

Every provider (mock now, Dhan/Kotak in Module 3) must implement this ABC.
Providers are WebSocket-shaped: `subscribe`, `unsubscribe`, and an async
`stream()` generator that yields ticks. Consumers can also `snapshot()` a
symbol for one-off reads (used by RiskEngine to size positions).
"""
from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Iterable


class Interval(str, enum.Enum):
    ONE_MIN = "1m"
    THREE_MIN = "3m"
    FIVE_MIN = "5m"
    FIFTEEN_MIN = "15m"
    ONE_HOUR = "1h"
    ONE_DAY = "1d"


@dataclass(frozen=True, slots=True)
class Quote:
    """A single market tick / LTP snapshot."""

    symbol: str
    exchange: str
    price: float
    volume: float
    ts: datetime  # timezone-aware UTC


@dataclass(frozen=True, slots=True)
class Candle:
    """OHLCV bar."""

    symbol: str
    exchange: str
    interval: Interval
    ts: datetime  # start of the candle, timezone-aware UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataProvider(ABC):
    """Abstract provider.

    Rules:
    - `subscribe`/`unsubscribe` MUST be idempotent.
    - `stream()` MUST yield only for currently-subscribed symbols; on
      unsubscribe or shutdown it MUST clean up gracefully.
    - `snapshot()` MUST return the last known price without side effects.
    - `historical_candles()` MAY raise `NotImplementedError` if unsupported.
    """

    name: str = "abstract"

    @abstractmethod
    async def start(self) -> None:
        """Perform any one-time setup (open sockets, seed state)."""

    @abstractmethod
    async def stop(self) -> None:
        """Release resources."""

    @abstractmethod
    async def subscribe(self, symbols: Iterable[str], exchange: str = "MOCK") -> None: ...

    @abstractmethod
    async def unsubscribe(self, symbols: Iterable[str], exchange: str = "MOCK") -> None: ...

    @abstractmethod
    def stream(self) -> AsyncIterator[Quote]:
        """Async iterator of ticks for currently-subscribed symbols."""

    @abstractmethod
    async def snapshot(self, symbol: str, exchange: str = "MOCK") -> Quote | None:
        """Return the last known Quote for a symbol, or None if unknown."""

    async def historical_candles(  # pragma: no cover - default not-implemented
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: datetime,
        exchange: str = "MOCK",
    ) -> list[Candle]:
        raise NotImplementedError("Historical candles not supported by this provider")
