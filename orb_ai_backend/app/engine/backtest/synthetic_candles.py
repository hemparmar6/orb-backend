"""Synthetic candle generator for backtesting.

Until a real market-data adapter (Dhan/Kotak historical) is wired,
backtests need candles to replay. This module produces deterministic,
reproducible 1-minute OHLCV candles for a symbol using a seedable random
walk that always spans a trading session between ``session_start`` and
``session_end`` on each business day in the requested window.

Real historical providers (from Module 3+) can be swapped in transparently
because the return type is the same :class:`Candle`.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from app.engine.market_data.base import Candle, Interval


def _daterange(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            yield d
        d += timedelta(days=1)


def generate_intraday_candles(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    session_start: time = time(9, 15),
    session_end: time = time(15, 30),
    timezone: str = "Asia/Kolkata",
    exchange: str = "NSE",
    base_price: float = 20000.0,
    volatility_pct: float = 0.25,
    seed: int = 42,
) -> list[Candle]:
    """Deterministic 1-minute candles for a symbol across a date range.

    Parameters
    ----------
    volatility_pct
        Per-candle 1-sigma percentage move (default 0.25 %).
    """
    rng = random.Random(f"{seed}-{symbol}")
    tz = ZoneInfo(timezone)
    price = base_price
    candles: list[Candle] = []

    for d in _daterange(start_date, end_date):
        # Slight open-price gap to make days differ from each other.
        price = price * (1 + rng.uniform(-0.005, 0.005))
        minutes_in_session = int(
            (
                datetime.combine(d, session_end) - datetime.combine(d, session_start)
            ).total_seconds()
            // 60
        )
        for m in range(minutes_in_session):
            ts_local = datetime.combine(d, session_start) + timedelta(minutes=m)
            ts_utc = ts_local.replace(tzinfo=tz).astimezone(ZoneInfo("UTC"))
            step = price * (volatility_pct / 100.0) * rng.gauss(0, 1)
            open_ = price
            close = max(0.01, open_ + step)
            high = max(open_, close) * (1 + abs(rng.gauss(0, 1)) * 0.0005)
            low = min(open_, close) * (1 - abs(rng.gauss(0, 1)) * 0.0005)
            vol = abs(rng.gauss(1000, 300))
            candles.append(
                Candle(
                    symbol=symbol,
                    exchange=exchange,
                    interval=Interval.ONE_MIN,
                    ts=ts_utc,
                    open=round(open_, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=round(close, 2),
                    volume=round(vol, 2),
                )
            )
            price = close
    return candles
