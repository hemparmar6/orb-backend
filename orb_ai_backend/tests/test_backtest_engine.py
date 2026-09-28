"""Unit tests for the ORB backtest engine.

Feeds deterministic hand-crafted candles into ``BacktestEngine`` and asserts
each documented behavior: OR build, long/short breakout entry, SL/TP exit,
trailing stop, EOD square-off, daily caps, and re-entry rules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.engine.backtest.engine import BacktestEngine
from app.engine.backtest.synthetic_candles import generate_intraday_candles
from app.engine.market_data.base import Candle, Interval
from app.engine.strategy.orb_params import OrbParams

IST = ZoneInfo("Asia/Kolkata")


def _c(symbol: str, ts_local: datetime, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(
        symbol=symbol,
        exchange="NSE",
        interval=Interval.ONE_MIN,
        ts=ts_local.astimezone(timezone.utc),
        open=o, high=h, low=low, close=c, volume=100,
    )


def _flat(symbol: str, ts_local: datetime, price: float) -> Candle:
    """Zero-range candle — used inside the OR window so OR_high == max close."""
    return _c(symbol, ts_local, price, price, price, price)


def _mk_day(base: datetime) -> list[Candle]:
    """A vanilla long-breakout day:
    - 09:15-09:29: OR forms between 100 and 105
    - 09:30: breakout above 105 → LONG entry
    - subsequent candles: price marches to 108 (target) then reverses back.
    """
    candles = []
    # Minute 0..14 form the OR: range 100-105 (flat candles).
    for i, price in enumerate([100, 101, 102, 105, 104, 103, 102, 101,
                                100, 101, 102, 103, 104, 105, 105]):
        candles.append(_flat("NIFTY", base + timedelta(minutes=i), price))
    # Minute 15+ (breakout window).
    # Minute 15 breaks above OR_high (105) — entry at 105.
    for i, price in enumerate([106, 107, 108, 107, 106, 105]):
        ts = base + timedelta(minutes=15 + i)
        candles.append(_c("NIFTY", ts, price, price + 0.5, price - 0.2, price))
    return candles


def test_long_breakout_reaches_target():
    base_local = datetime(2026, 6, 1, 9, 15, tzinfo=IST)
    candles = _mk_day(base_local)

    params = OrbParams.from_dict({
        "symbols": ["NIFTY"],
        "opening_range_minutes": 15,
        "session_start": "09:15",
        "session_end": "15:15",
        "enable_long": True,
        "enable_short": False,
        "stop_loss_pct": 1.0,   # SL at 105 * 0.99 = 103.95
        "target_pct": 2.0,      # TP at 105 * 1.02 = 107.1
        "quantity": 10,
    })
    result = BacktestEngine(params, initial_capital=100_000).run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "long"
    assert abs(trade.entry_price - 105.0) < 0.01
    # Target = 105 * 1.02 = 107.1 → candle high of 108.5 crosses it, exit at TP
    assert abs(trade.exit_price - 107.1) < 0.01
    assert trade.exit_reason == "target"
    assert trade.pnl > 0
    # Metrics sanity
    assert result.summary["number_of_trades"] == 1
    assert result.summary["win_rate_pct"] == 100.0
    assert result.summary["net_profit"] > 0


def test_short_breakdown_reaches_stop_loss():
    """Build an OR then drop below — short entry, then price rebounds past SL."""
    base_local = datetime(2026, 6, 2, 9, 15, tzinfo=IST)
    candles = []
    # OR 100-105 (flat candles).
    for i, price in enumerate([100, 101, 102, 105, 104, 103, 102, 101,
                                100, 101, 102, 103, 104, 105, 100]):
        candles.append(_flat("NIFTY", base_local + timedelta(minutes=i), price))
    # Break below OR_low 100 — short entry at 100 (slippage 0).
    # Then price rebounds to 102 → hits SL at 100 * 1.01 = 101.
    for i, price in enumerate([99, 100, 102, 103]):
        ts = base_local + timedelta(minutes=15 + i)
        candles.append(_c("NIFTY", ts, price, price + 0.5, price - 0.5, price))

    params = OrbParams.from_dict({
        "symbols": ["NIFTY"],
        "opening_range_minutes": 15,
        "enable_long": False,
        "enable_short": True,
        "stop_loss_pct": 1.0,   # SL for short at 100 * 1.01 = 101
        "target_pct": 5.0,      # target far away
        "quantity": 10,
    })
    result = BacktestEngine(params, initial_capital=100_000).run(candles)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "short"
    assert abs(trade.entry_price - 100.0) < 0.01
    assert abs(trade.exit_price - 101.0) < 0.01
    assert trade.exit_reason == "stop_loss"
    assert trade.pnl < 0


def test_eod_square_off_closes_open_position():
    """A long entry that never hits SL / TP should be closed at session_end."""
    base_local = datetime(2026, 6, 3, 9, 15, tzinfo=IST)
    candles = []
    # OR (flat candles).
    for i, price in enumerate([100, 101, 102, 103, 104, 105] * 3):
        candles.append(_flat("NIFTY", base_local + timedelta(minutes=i), price))
    # After OR: hover between 106 and 107 all day, never triggers TP@115.5 or SL@99.75
    hour_offsets = list(range(20, 380, 5))  # from ~09:35 → ~15:35
    for offset in hour_offsets:
        ts = base_local + timedelta(minutes=offset)
        candles.append(_c("NIFTY", ts, 106, 107, 105.5, 106.5))

    params = OrbParams.from_dict({
        "symbols": ["NIFTY"],
        "opening_range_minutes": 15,
        "session_start": "09:15",
        "session_end": "15:15",
        "enable_long": True,
        "enable_short": False,
        "stop_loss_pct": 5.0,
        "target_pct": 10.0,
        "quantity": 5,
    })
    result = BacktestEngine(params, initial_capital=100_000).run(candles)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "eod"


def test_max_trades_per_day_caps_entries():
    """With max_trades_per_day=1, a re-entry attempt should not fire."""
    base_local = datetime(2026, 6, 4, 9, 15, tzinfo=IST)
    candles = _mk_day(base_local)
    # After target hit, add more breakout-style candles to try re-entry.
    for i, price in enumerate([106, 107, 108, 110]):
        ts = base_local + timedelta(minutes=25 + i)
        candles.append(_c("NIFTY", ts, price, price + 1, price - 0.5, price))

    params = OrbParams.from_dict({
        "symbols": ["NIFTY"],
        "opening_range_minutes": 15,
        "enable_long": True,
        "enable_short": False,
        "stop_loss_pct": 1.0,
        "target_pct": 2.0,
        "quantity": 1,
        "max_trades_per_day": 1,
        "re_entry_enabled": False,
    })
    result = BacktestEngine(params, initial_capital=100_000).run(candles)
    assert len(result.trades) == 1


def test_daily_loss_limit_halts_the_day():
    """If a single big loss exceeds daily_loss_limit, no re-entry even if enabled."""
    base_local = datetime(2026, 6, 5, 9, 15, tzinfo=IST)
    candles = []
    # OR 100-105 (flat).
    for i, price in enumerate([100, 101, 102, 105, 104, 103, 102, 101,
                                100, 101, 102, 103, 104, 105, 105]):
        candles.append(_flat("NIFTY", base_local + timedelta(minutes=i), price))
    # Breakout at 105 → LONG entry at 106.5 candle. Immediately dumps to 90 → hits SL.
    post_prices = [106, 90, 91, 92, 100, 105]
    for i, price in enumerate(post_prices):
        ts = base_local + timedelta(minutes=15 + i)
        candles.append(_c("NIFTY", ts, price, price + 0.5, price - 0.5, price))

    params = OrbParams.from_dict({
        "symbols": ["NIFTY"],
        "opening_range_minutes": 15,
        "enable_long": True,
        "enable_short": False,
        "stop_loss_pct": 5.0,       # SL at 99.75 -> low of 89.5 blows through
        "target_pct": 10.0,
        "quantity": 100,             # big enough size to cross the loss limit
        "max_trades_per_day": 5,
        "re_entry_enabled": True,
        "max_re_entries_per_day": 5,
        "daily_loss_limit": 100,    # tiny — first loss halts
    })
    result = BacktestEngine(params, initial_capital=100_000).run(candles)
    assert len(result.trades) == 1
    assert result.trades[0].pnl < 0


def test_trailing_stop_locks_in_gains():
    """With trailing stop enabled the exit SL moves up as price rises."""
    base_local = datetime(2026, 6, 6, 9, 15, tzinfo=IST)
    candles = []
    for i, price in enumerate([100, 101, 102, 105, 104, 103, 102, 101,
                                100, 101, 102, 103, 104, 105, 105]):
        candles.append(_flat("NIFTY", base_local + timedelta(minutes=i), price))
    # Break out then climb to 120 then reverse to 118. Without trailing, price
    # never touches SL(103.95) or TP(126). With trailing @ 5% the SL moves
    # to 120*0.95=114 → we exit ~114 profitably.
    for i, price in enumerate([106, 110, 115, 120, 118, 116, 114, 113]):
        ts = base_local + timedelta(minutes=15 + i)
        candles.append(_c("NIFTY", ts, price, price + 0.5, price - 0.5, price))

    params = OrbParams.from_dict({
        "symbols": ["NIFTY"],
        "opening_range_minutes": 15,
        "enable_long": True,
        "enable_short": False,
        "stop_loss_pct": 1.0,
        "target_pct": 20.0,        # very far
        "trailing_stop_pct": 5.0,
        "quantity": 10,
    })
    result = BacktestEngine(params, initial_capital=100_000).run(candles)
    assert len(result.trades) == 1
    t = result.trades[0]
    # Trailing must have kicked in — exit price > entry + a chunk.
    assert t.exit_price > 110
    assert t.exit_reason == "stop_loss"  # trailing SL fires as a stop
    assert t.pnl > 0


def test_synthetic_candles_are_reproducible_and_ordered():
    from datetime import date
    c1 = generate_intraday_candles(
        symbol="NIFTY", start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), seed=99
    )
    c2 = generate_intraday_candles(
        symbol="NIFTY", start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), seed=99
    )
    assert len(c1) == len(c2) > 0
    for a, b in zip(c1, c2):
        assert a.ts == b.ts and a.close == b.close
    # Strictly ascending timestamps.
    for prev, curr in zip(c1, c1[1:]):
        assert prev.ts <= curr.ts
