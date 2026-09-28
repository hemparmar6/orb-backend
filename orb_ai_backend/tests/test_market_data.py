"""MarketDataProvider — mock + historical."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.market_data import (
    Candle,
    HistoricalCandleProvider,
    Interval,
    MockMarketDataProvider,
    Quote,
    get_provider,
)


@pytest.mark.asyncio
async def test_mock_provider_is_deterministic():
    p1 = MockMarketDataProvider(seed=123, tick_interval_ms=10)
    p2 = MockMarketDataProvider(seed=123, tick_interval_ms=10)
    await p1.subscribe(["A", "B"])
    await p2.subscribe(["A", "B"])
    seq1 = [p1.next_tick("A") for _ in range(20)]
    seq2 = [p2.next_tick("A") for _ in range(20)]
    assert [q.price for q in seq1] == [q.price for q in seq2]


@pytest.mark.asyncio
async def test_mock_provider_snapshot():
    p = MockMarketDataProvider(seed=1, tick_interval_ms=10)
    await p.subscribe(["X"])
    assert (await p.snapshot("UNKNOWN")) is None
    q = p.next_tick("X")
    snap = await p.snapshot("X")
    assert snap is not None
    assert snap.price == q.price


def test_registry_default_is_mock():
    p = get_provider("mock", tick_interval_ms=10)
    assert isinstance(p, MockMarketDataProvider)


@pytest.mark.asyncio
async def test_historical_candle_provider_filters_by_range():
    provider = HistoricalCandleProvider()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    provider.seed(
        [
            Candle("SYM", "MOCK", Interval.ONE_MIN, base + timedelta(minutes=i),
                   open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=10)
            for i in range(5)
        ]
    )
    result = await provider.get_candles(
        "SYM", Interval.ONE_MIN, base + timedelta(minutes=1), base + timedelta(minutes=4)
    )
    assert [c.close for c in result] == [101, 102, 103]


@pytest.mark.asyncio
async def test_mock_provider_synthetic_candles_shape():
    p = MockMarketDataProvider(seed=7)
    await p.subscribe(["SYM"])
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)
    candles = await p.historical_candles("SYM", Interval.ONE_MIN, start, end)
    assert len(candles) == 5
    for c in candles:
        assert c.low <= c.open <= c.high
        assert c.low <= c.close <= c.high
