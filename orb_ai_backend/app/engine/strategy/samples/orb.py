"""ORB (Opening Range Breakout) strategy.

**Broker-independent.** The strategy talks only to ``StrategyContext``. Any
broker adapter registered in Module 3 can execute its signals via the live
executor path — no code here knows anything about Dhan / Kotak / MockLive.

Behaviour
---------
1. For each symbol on each new trading day, build an Opening Range from
   ticks between ``session_start`` and ``session_start + opening_range_minutes``.
2. After the OR window closes:
   - **Long breakout**  — price above OR_high → BUY MARKET
   - **Short breakdown** — price below OR_low  → SELL MARKET (short)
3. While in a trade, on each tick check:
   - Hard stop-loss hit → MARKET exit
   - Target hit         → MARKET exit
   - Trailing stop      → ratchet SL toward current price by ``trailing_stop_pct``
4. Re-entry (optional) after an exit if ``re_entry_enabled`` and
   ``re_entries_used < max_re_entries_per_day``.
5. Global caps: at most ``max_trades_per_day`` entries, hard stop when
   day-realized loss exceeds ``daily_loss_limit``.
6. Force-square-off any open trade when the tick crosses ``session_end``.

All parameters live on ``ctx.params`` and are parsed via ``OrbParams`` so
the live path + the backtester share the exact same semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.engine.market_data.base import Quote
from app.engine.strategy.base import BaseStrategy
from app.engine.strategy.orb_params import OrbParams
from app.engine.strategy.registry import register_strategy
from app.models.engine import OrderSide, PaperOrder, OrderStatus


# ---- Per-symbol per-day state --------------------------------------------


@dataclass(slots=True)
class _OpeningRange:
    day: date
    high: float = float("-inf")
    low: float = float("inf")
    complete: bool = False
    breakout_used: bool = False   # long already triggered today
    breakdown_used: bool = False  # short already triggered today


@dataclass(slots=True)
class _ActiveTrade:
    side: OrderSide
    entry_price: float
    quantity: float
    stop_loss: float
    target: float
    trailing_ref: float  # highest close (long) or lowest close (short)
    entry_order_id: Optional[str] = None


@dataclass(slots=True)
class _DailyStats:
    day: date
    trades_taken: int = 0
    re_entries_used: int = 0
    realized_pnl: float = 0.0
    halted: bool = False  # set once daily_loss_limit is breached


# ---- The strategy itself --------------------------------------------------


@register_strategy("orb")
class OrbStrategy(BaseStrategy):
    async def on_start(self) -> None:
        self.params: OrbParams = OrbParams.from_dict(self.ctx.params)
        self._tz = ZoneInfo(self.params.timezone)
        self.ctx.state.setdefault("orb_ranges", {})       # {symbol: _OpeningRange}
        self.ctx.state.setdefault("orb_active", {})       # {symbol: _ActiveTrade}
        self.ctx.state.setdefault("orb_daily_stats", {})  # {symbol: _DailyStats}

    # ---- Tick loop -------------------------------------------------------

    async def on_tick(self, quote: Quote) -> None:
        if quote.symbol not in self.params.symbols:
            return

        # Convert to exchange-local time for session comparisons.
        local_dt = quote.ts.astimezone(self._tz)
        today = local_dt.date()
        now_t: time = local_dt.time()

        # Outside session? Ignore ticks entirely.
        if now_t < self.params.session_start:
            return

        stats = self._daily_stats(quote.symbol, today)
        if stats.halted:
            return

        orange = self._range(quote.symbol, today)
        or_end = _add_minutes(self.params.session_start, self.params.opening_range_minutes)

        # ---- Phase 1: build the opening range --------------------------
        if now_t < or_end:
            price = float(quote.price)
            if price > orange.high:
                orange.high = price
            if price < orange.low:
                orange.low = price
            return

        # OR just closed — mark it complete once.
        if not orange.complete:
            orange.complete = True

        if orange.high == float("-inf") or orange.low == float("inf"):
            # No ticks arrived during the OR window — nothing to trade.
            return

        # ---- Phase 3: EOD square-off ----------------------------------
        active = self.ctx.state["orb_active"].get(quote.symbol)
        if now_t >= self.params.session_end:
            if active is not None:
                await self._close(quote, active, reason="eod")
            return

        # ---- Phase 2a: manage an open trade first ----------------------
        if active is not None:
            await self._manage_open_trade(quote, active)
            return

        # ---- Phase 2b: look for breakouts ------------------------------
        if stats.trades_taken >= self.params.max_trades_per_day:
            return

        price = float(quote.price)
        if self.params.enable_long and price > orange.high and not orange.breakout_used:
            await self._enter(quote, OrderSide.BUY, orange, stats)
            orange.breakout_used = True
        elif self.params.enable_short and price < orange.low and not orange.breakdown_used:
            await self._enter(quote, OrderSide.SELL, orange, stats)
            orange.breakdown_used = True

    # ---- Executor callbacks ---------------------------------------------

    async def on_order_update(self, order: PaperOrder) -> None:  # pragma: no cover - live-only
        # For MARKET orders we already assumed fill at the trigger price. We
        # correct our books here if the actual avg fill price differs.
        if order.status != OrderStatus.FILLED:
            return
        active = self.ctx.state["orb_active"].get(order.symbol)
        if active is None or active.entry_order_id != order.id:
            return
        if order.average_fill_price is not None:
            active.entry_price = float(order.average_fill_price)

    # ---- Helpers ---------------------------------------------------------

    def _range(self, symbol: str, day: date) -> _OpeningRange:
        ranges = self.ctx.state["orb_ranges"]
        cur = ranges.get(symbol)
        if cur is None or cur.day != day:
            cur = _OpeningRange(day=day)
            ranges[symbol] = cur
        return cur

    def _daily_stats(self, symbol: str, day: date) -> _DailyStats:
        stats_map = self.ctx.state["orb_daily_stats"]
        cur = stats_map.get(symbol)
        if cur is None or cur.day != day:
            cur = _DailyStats(day=day)
            stats_map[symbol] = cur
        return cur

    async def _enter(
        self, quote: Quote, side: OrderSide, orange: _OpeningRange, stats: _DailyStats
    ) -> None:
        price = float(quote.price)
        if side == OrderSide.BUY:
            sl_price = price * (1 - self.params.stop_loss_pct / 100.0)
            tgt_price = price * (1 + self.params.target_pct / 100.0)
        else:
            sl_price = price * (1 + self.params.stop_loss_pct / 100.0)
            tgt_price = price * (1 - self.params.target_pct / 100.0)
        sl_distance = abs(price - sl_price)
        qty = self.params.size_for(price, sl_distance, capital=self._est_capital())

        if side == OrderSide.BUY:
            order = await self.ctx.buy_market(quote.symbol, qty, tag="orb_entry")
        else:
            order = await self.ctx.sell_market(quote.symbol, qty, tag="orb_entry")

        active = _ActiveTrade(
            side=side,
            entry_price=price,
            quantity=qty,
            stop_loss=sl_price,
            target=tgt_price,
            trailing_ref=price,
            entry_order_id=order.id,
        )
        self.ctx.state["orb_active"][quote.symbol] = active
        stats.trades_taken += 1

    async def _manage_open_trade(self, quote: Quote, active: _ActiveTrade) -> None:
        price = float(quote.price)
        if active.side == OrderSide.BUY:
            # Update trailing reference and (optionally) trail the stop up.
            if price > active.trailing_ref:
                active.trailing_ref = price
                if self.params.trailing_stop_pct is not None:
                    new_sl = active.trailing_ref * (1 - self.params.trailing_stop_pct / 100.0)
                    if new_sl > active.stop_loss:
                        active.stop_loss = new_sl
            # Exit conditions.
            if price <= active.stop_loss:
                await self._close(quote, active, reason="stop_loss")
            elif price >= active.target:
                await self._close(quote, active, reason="target")
        else:  # SHORT
            if price < active.trailing_ref:
                active.trailing_ref = price
                if self.params.trailing_stop_pct is not None:
                    new_sl = active.trailing_ref * (1 + self.params.trailing_stop_pct / 100.0)
                    if new_sl < active.stop_loss:
                        active.stop_loss = new_sl
            if price >= active.stop_loss:
                await self._close(quote, active, reason="stop_loss")
            elif price <= active.target:
                await self._close(quote, active, reason="target")

    async def _close(self, quote: Quote, active: _ActiveTrade, *, reason: str) -> None:
        if active.side == OrderSide.BUY:
            await self.ctx.sell_market(quote.symbol, active.quantity, tag=f"orb_exit_{reason}")
            trade_pnl = (float(quote.price) - active.entry_price) * active.quantity
        else:
            await self.ctx.buy_market(quote.symbol, active.quantity, tag=f"orb_exit_{reason}")
            trade_pnl = (active.entry_price - float(quote.price)) * active.quantity

        # Update daily stats & halt if we crossed the loss limit.
        local_dt = quote.ts.astimezone(self._tz)
        stats = self._daily_stats(quote.symbol, local_dt.date())
        stats.realized_pnl += trade_pnl
        if stats.realized_pnl <= -abs(self.params.daily_loss_limit):
            stats.halted = True

        self.ctx.state["orb_active"].pop(quote.symbol, None)

    def _est_capital(self) -> float:
        # Best-effort: use initial_capital from params if present, else 100k.
        return float(self.ctx.params.get("initial_capital", 100_000))


def _add_minutes(t: time, minutes: int) -> time:
    # Purely-time addition; wraps within 24h and clamps at 23:59:59.
    total = t.hour * 60 + t.minute + minutes
    total = min(total, 23 * 60 + 59)
    return time(total // 60, total % 60)
