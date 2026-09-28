"""Historical candle fetching — abstraction + registry + synthetic fallback.

Live trading and backtesting SHOULD run against the same 1-minute (or coarser)
candles. This module defines the fetch abstraction (``HistoricalCandleFetcher``)
that broker fetchers implement, and ships a deterministic synthetic fallback
(``SyntheticHistoricalFetcher``) that wraps the existing generator so CI stays
green without any real broker credentials.

Concrete broker implementations live alongside their market-data cousins:

- :mod:`app.engine.market_data.dhan_historical`
- :mod:`app.engine.market_data.kotak_neo_historical`

The registry lets callers select a fetcher by name — same pattern used for
real-time market-data providers::

    fetcher = get_historical(
        "dhan",
        credentials={"client_id": "...", "access_token": "..."},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
    )
    candles = await fetcher.get_candles("TCS", Interval.ONE_MIN, start, end)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, time
from typing import Any, Callable, Iterable, Type

from app.core.exceptions import EngineError
from app.engine.backtest.synthetic_candles import generate_intraday_candles
from app.engine.market_data.base import Candle, Interval


class HistoricalCandleFetcher(ABC):
    """Return candles for ``(symbol, interval, [start, end))``.

    ``exchange`` is optional — providers may ignore it if the symbol map
    already carries the exchange segment.
    """

    #: Name used in the registry.
    name: str = "abstract_historical"

    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: datetime,
        exchange: str = "",
    ) -> list[Candle]: ...

    async def close(self) -> None:
        """Release any transient resources (HTTP clients, etc.)."""


# --------------------------------------------------------------------- registry

_registry: dict[str, Type[HistoricalCandleFetcher]] = {}


def register_historical(
    name: str,
) -> Callable[[Type[HistoricalCandleFetcher]], Type[HistoricalCandleFetcher]]:
    def _decorator(
        cls: Type[HistoricalCandleFetcher],
    ) -> Type[HistoricalCandleFetcher]:
        _registry[name.lower()] = cls
        cls.name = name
        return cls

    return _decorator


def get_historical(name: str, **kwargs: Any) -> HistoricalCandleFetcher:
    key = name.lower()
    if key not in _registry:
        raise EngineError(
            f"Historical candle provider '{name}' is not registered",
            code="unknown_historical_provider",
        )
    return _registry[key](**kwargs)


def list_historical() -> list[str]:
    return sorted(_registry.keys())


# ------------------------------------------------------------- synthetic fallback


@register_historical("synthetic")
class SyntheticHistoricalFetcher(HistoricalCandleFetcher):
    """Deterministic synthetic candles — same generator the backtester has
    always used. Kept as the default so the pre-existing backtest test-suite
    stays green without any real broker.
    """

    name = "synthetic"

    def __init__(
        self,
        *,
        base_price: float = 20_000.0,
        seed: int = 42,
        session_start: time = time(9, 15),
        session_end: time = time(15, 15),
        timezone: str = "Asia/Kolkata",
    ) -> None:
        self._base_price = base_price
        self._seed = seed
        self._session_start = session_start
        self._session_end = session_end
        self._timezone = timezone

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: datetime,
        exchange: str = "",
    ) -> list[Candle]:
        # Existing generator emits 1-minute candles across the session window.
        # For non-1-minute intervals we still return the finest resolution;
        # callers that need aggregation can post-process.
        return generate_intraday_candles(
            symbol=symbol,
            start_date=start.date(),
            end_date=end.date(),
            session_start=self._session_start,
            session_end=self._session_end,
            timezone=self._timezone,
            exchange=exchange or "NSE",
            base_price=self._base_price,
            seed=self._seed,
        )


def load_candles_multi(
    fetcher: HistoricalCandleFetcher,
    *,
    symbols: Iterable[str],
    interval: Interval,
    start: datetime,
    end: datetime,
    exchange: str = "",
) -> Any:
    """Convenience: coroutine that fetches candles for many symbols and
    concatenates the results. Kept out of the ABC to keep the surface small.
    """

    async def _run() -> list[Candle]:
        out: list[Candle] = []
        for s in symbols:
            out.extend(await fetcher.get_candles(s, interval, start, end, exchange))
        return out

    return _run()
