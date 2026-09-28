"""Demo MA-cross strategy.

Intent: demonstrate the framework end-to-end. This is NOT the ORB strategy —
that comes in a later module.

Parameters (from ``ctx.params``):
- ``fast`` (int, default 5)    — fast moving-average window
- ``slow`` (int, default 20)   — slow moving-average window
- ``quantity`` (float, def. 1) — units per entry
- ``stop_loss_pct`` (float, def. None)   — SL as % below/above entry
- ``target_pct`` (float, def. None)      — TP as % above/below entry

Behaviour:
- Maintains per-symbol rolling window of prices.
- BUY MARKET on fast-crosses-above-slow while flat.
- SELL MARKET (close) on fast-crosses-below-slow while long.
- If SL/TP percentages are set, entry order carries stop_loss & target_price
  so the OrderManager auto-spawns OCO children.
"""
from __future__ import annotations

from collections import deque

from app.engine.market_data.base import Quote
from app.engine.strategy.base import BaseStrategy
from app.engine.strategy.registry import register_strategy


@register_strategy("demo_ma_cross")
class DemoMaCross(BaseStrategy):
    async def on_start(self) -> None:
        self.fast_n = int(self.ctx.params.get("fast", 5))
        self.slow_n = int(self.ctx.params.get("slow", 20))
        self.qty = float(self.ctx.params.get("quantity", 1))
        self.sl_pct = _maybe_float(self.ctx.params.get("stop_loss_pct"))
        self.tp_pct = _maybe_float(self.ctx.params.get("target_pct"))
        self.ctx.state.setdefault("windows", {})
        self.ctx.state.setdefault("last_cross", {})

    async def on_tick(self, quote: Quote) -> None:
        windows = self.ctx.state["windows"]
        last_cross = self.ctx.state["last_cross"]
        w = windows.setdefault(quote.symbol, deque(maxlen=self.slow_n))
        w.append(float(quote.price))
        if len(w) < self.slow_n:
            return

        fast = sum(list(w)[-self.fast_n :]) / self.fast_n
        slow = sum(w) / self.slow_n
        prev = last_cross.get(quote.symbol)
        cross = "up" if fast > slow else ("down" if fast < slow else prev)

        if cross == prev or cross is None:
            last_cross[quote.symbol] = cross
            return
        last_cross[quote.symbol] = cross

        positions = await self.ctx.get_positions()
        net = positions.get(quote.symbol, 0.0)

        if cross == "up" and net == 0:
            sl = float(quote.price) * (1 - self.sl_pct / 100.0) if self.sl_pct else None
            tp = float(quote.price) * (1 + self.tp_pct / 100.0) if self.tp_pct else None
            await self.ctx.buy_market(quote.symbol, self.qty, stop_loss=sl, target_price=tp)
        elif cross == "down" and net > 0:
            await self.ctx.sell_market(quote.symbol, abs(net))


def _maybe_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
