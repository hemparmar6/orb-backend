"""Candle-driven ORB backtest engine.

Design
------
The engine consumes an iterable of ``Candle`` objects (any interval that fits
inside the opening-range window; 1m is the sweet spot) and produces a
:class:`BacktestResult` with:

- Trade log (one row per closed round-trip)
- Equity curve (updated on every closed trade + one point per day EOD)
- Metrics summary (produced via :func:`app.engine.backtest.metrics.compute_metrics`)

The engine is **broker-independent** and **synchronous** — it does not touch
the database, the OrderManager, or the risk engine. Fill semantics live
here (with configurable slippage + per-trade fees). Signal logic mirrors
:class:`OrbStrategy` exactly so live and backtest results align.

Fill rules
----------
- Entry: on the candle whose ``high`` crosses OR_high (long) or ``low``
  crosses OR_low (short). Fill price = OR level ± slippage.
- Stop-loss: candle low ≤ SL (long) or high ≥ SL (short) → fill at SL.
- Target:     candle high ≥ TP (long) or low  ≤ TP (short) → fill at TP.
- If a single candle hits both SL and TP, the SL wins (pessimistic).
- Trailing stop: adjusted on each candle's close in favor of the trade.
- End-of-day square-off: fill at the ``close`` of the last in-session candle
  on/after ``session_end``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from app.engine.backtest.metrics import compute_metrics
from app.engine.market_data.base import Candle
from app.engine.strategy.orb_params import OrbParams


# ---- Result DTOs ---------------------------------------------------------


@dataclass(slots=True)
class BacktestTrade:
    symbol: str
    side: str            # "long" | "short"
    quantity: float
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    pnl: float
    fees: float
    exit_reason: str     # "stop_loss" | "target" | "eod" | "day_halted"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": round(self.entry_price, 4),
            "exit_time": self.exit_time.isoformat(),
            "exit_price": round(self.exit_price, 4),
            "pnl": round(self.pnl, 4),
            "fees": round(self.fees, 4),
            "exit_reason": self.exit_reason,
        }


@dataclass(slots=True)
class BacktestResult:
    initial_capital: float
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": list(self.equity_curve),
        }


# ---- Per-symbol per-day state -------------------------------------------


@dataclass(slots=True)
class _DayState:
    day: date
    or_high: float = float("-inf")
    or_low: float = float("inf")
    or_complete: bool = False
    long_taken: bool = False
    short_taken: bool = False
    trades_taken: int = 0
    realized_pnl: float = 0.0
    halted: bool = False
    active: Optional["_ActiveTrade"] = None


@dataclass(slots=True)
class _ActiveTrade:
    side: str           # "long" | "short"
    quantity: float
    entry_time: datetime
    entry_price: float
    stop_loss: float
    target: float
    trailing_ref: float


# ---- The engine ----------------------------------------------------------


class BacktestEngine:
    def __init__(self, params: OrbParams, initial_capital: float = 100_000.0) -> None:
        self.params = params
        self.initial_capital = float(initial_capital)
        self._tz = ZoneInfo(params.timezone)
        # per-symbol per-date state:
        self._state: dict[str, _DayState] = {}
        # equity is updated whenever a trade closes
        self._equity = self.initial_capital
        self._trades: list[BacktestTrade] = []
        self._equity_curve: list[dict] = []
        # remember last day/symbol so we can post an EOD equity point
        self._last_day_recorded: dict[str, date] = {}

    # ---- Public API ------------------------------------------------------

    def run(self, candles: Iterable[Candle]) -> BacktestResult:
        # Group candles by (symbol, day) so we can process day-by-day in
        # strict chronological order across all symbols.
        by_ts = sorted(candles, key=lambda c: (c.ts, c.symbol))
        for candle in by_ts:
            self._process(candle)

        # Force-close any positions still open at the very end.
        for symbol, state in self._state.items():
            if state.active is not None and by_ts:
                last_c = by_ts[-1]
                self._exit(symbol, state, last_c, price=float(last_c.close), reason="eod")

        # Ensure the equity curve has at least one point.
        if not self._equity_curve:
            self._equity_curve.append({
                "ts": (by_ts[0].ts.isoformat() if by_ts else datetime.utcnow().isoformat()),
                "equity": round(self._equity, 4),
            })

        result = BacktestResult(
            initial_capital=self.initial_capital,
            trades=self._trades,
            equity_curve=self._equity_curve,
        )
        result.summary = compute_metrics(
            [t.to_dict() for t in self._trades],
            self._equity_curve,
            self.initial_capital,
        )
        return result

    # ---- Per-candle processing ------------------------------------------

    def _process(self, candle: Candle) -> None:
        if candle.symbol not in self.params.symbols:
            return
        local_ts = candle.ts.astimezone(self._tz)
        today = local_ts.date()

        state = self._state.get(candle.symbol)
        if state is None or state.day != today:
            # If we cross into a new day with an open trade, EOD-close first.
            if state and state.active is not None:
                self._exit(candle.symbol, state, candle,
                           price=float(candle.open), reason="eod")
            state = _DayState(day=today)
            self._state[candle.symbol] = state

        # Emit an EOD equity point when we transition to a new day.
        last_seen = self._last_day_recorded.get(candle.symbol)
        if last_seen is not None and last_seen != today:
            self._equity_curve.append({
                "ts": local_ts.isoformat(),
                "equity": round(self._equity, 4),
            })
        self._last_day_recorded[candle.symbol] = today

        # Filter to trading session — outside is ignored.
        t = local_ts.time()
        if t < self.params.session_start:
            return

        # ---- 1. Manage EOD square-off ---------------------------------
        if t >= self.params.session_end:
            if state.active is not None:
                self._exit(candle.symbol, state, candle,
                           price=float(candle.close), reason="eod")
            return

        # ---- 2. Build opening range ------------------------------------
        or_end = _add_minutes(self.params.session_start,
                              self.params.opening_range_minutes)
        if t < or_end:
            if candle.high > state.or_high:
                state.or_high = float(candle.high)
            if candle.low < state.or_low:
                state.or_low = float(candle.low)
            return

        # Mark OR complete once. Skip if we never saw any candles.
        if not state.or_complete:
            state.or_complete = True
        if state.or_high == float("-inf") or state.or_low == float("inf"):
            return

        # ---- 3. If already in a trade, check exits ---------------------
        if state.active is not None:
            self._check_exit(candle.symbol, state, candle)
            return

        if state.halted:
            return

        # ---- 4. Look for a fresh breakout ------------------------------
        if state.trades_taken >= self.params.max_trades_per_day:
            return

        long_ok = (
            self.params.enable_long
            and float(candle.high) > state.or_high
            and (not state.long_taken or self._can_reenter(state))
        )
        short_ok = (
            self.params.enable_short
            and float(candle.low) < state.or_low
            and (not state.short_taken or self._can_reenter(state))
        )
        # Long takes precedence when both flip in one candle (asymmetric).
        if long_ok:
            self._enter(candle.symbol, state, candle, side="long")
            state.long_taken = True
        elif short_ok:
            self._enter(candle.symbol, state, candle, side="short")
            state.short_taken = True

    # ---- Entry / exit --------------------------------------------------

    def _enter(self, symbol: str, state: _DayState, candle: Candle, *, side: str) -> None:
        # Fill at the OR level (± slippage) — this is the classic ORB fill.
        raw_entry = state.or_high if side == "long" else state.or_low
        slip = raw_entry * (self.params.slippage_pct / 100.0)
        entry = raw_entry + slip if side == "long" else raw_entry - slip

        if side == "long":
            sl = entry * (1 - self.params.stop_loss_pct / 100.0)
            tp = entry * (1 + self.params.target_pct / 100.0)
        else:
            sl = entry * (1 + self.params.stop_loss_pct / 100.0)
            tp = entry * (1 - self.params.target_pct / 100.0)

        sl_distance = abs(entry - sl)
        qty = self.params.size_for(entry, sl_distance, capital=self._equity)

        state.active = _ActiveTrade(
            side=side,
            quantity=qty,
            entry_time=candle.ts,
            entry_price=entry,
            stop_loss=sl,
            target=tp,
            trailing_ref=entry,
        )
        state.trades_taken += 1

    def _check_exit(self, symbol: str, state: _DayState, candle: Candle) -> None:
        active = state.active
        if active is None:
            return
        high, low, close = float(candle.high), float(candle.low), float(candle.close)

        # Trailing stop: adjust in favor of trade using this candle's close.
        if self.params.trailing_stop_pct is not None:
            if active.side == "long":
                if close > active.trailing_ref:
                    active.trailing_ref = close
                    new_sl = active.trailing_ref * (
                        1 - self.params.trailing_stop_pct / 100.0
                    )
                    if new_sl > active.stop_loss:
                        active.stop_loss = new_sl
            else:
                if close < active.trailing_ref:
                    active.trailing_ref = close
                    new_sl = active.trailing_ref * (
                        1 + self.params.trailing_stop_pct / 100.0
                    )
                    if new_sl < active.stop_loss:
                        active.stop_loss = new_sl

        # SL wins ties with target (pessimistic).
        if active.side == "long":
            if low <= active.stop_loss:
                self._exit(symbol, state, candle,
                           price=active.stop_loss, reason="stop_loss")
                return
            if high >= active.target:
                self._exit(symbol, state, candle,
                           price=active.target, reason="target")
                return
        else:  # short
            if high >= active.stop_loss:
                self._exit(symbol, state, candle,
                           price=active.stop_loss, reason="stop_loss")
                return
            if low <= active.target:
                self._exit(symbol, state, candle,
                           price=active.target, reason="target")
                return

    def _exit(
        self,
        symbol: str,
        state: _DayState,
        candle: Candle,
        *,
        price: float,
        reason: str,
    ) -> None:
        active = state.active
        if active is None:
            return
        slip = price * (self.params.slippage_pct / 100.0)
        exit_price = price - slip if active.side == "long" else price + slip

        if active.side == "long":
            gross = (exit_price - active.entry_price) * active.quantity
        else:
            gross = (active.entry_price - exit_price) * active.quantity

        fees = 2 * self.params.fee_per_trade  # entry + exit
        pnl = gross - fees

        trade = BacktestTrade(
            symbol=symbol,
            side=active.side,
            quantity=active.quantity,
            entry_time=active.entry_time,
            entry_price=active.entry_price,
            exit_time=candle.ts,
            exit_price=exit_price,
            pnl=pnl,
            fees=fees,
            exit_reason=reason,
        )
        self._trades.append(trade)
        self._equity += pnl
        state.realized_pnl += pnl
        state.active = None

        # Track the daily equity point at exit time.
        self._equity_curve.append({
            "ts": candle.ts.isoformat(),
            "equity": round(self._equity, 4),
        })

        if state.realized_pnl <= -abs(self.params.daily_loss_limit):
            state.halted = True

    # ---- Re-entry --------------------------------------------------------

    def _can_reenter(self, state: _DayState) -> bool:
        if not self.params.re_entry_enabled:
            return False
        if state.trades_taken >= self.params.max_trades_per_day:
            return False
        # We already used one entry, so re-entries counted = trades_taken - 1.
        used = max(0, state.trades_taken - 1)
        return used < self.params.max_re_entries_per_day


def _add_minutes(t, minutes: int):
    from datetime import time
    total = t.hour * 60 + t.minute + minutes
    total = min(total, 23 * 60 + 59)
    return time(total // 60, total % 60)
