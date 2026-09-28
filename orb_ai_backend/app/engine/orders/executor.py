"""PaperExecutor — pure fill simulation.

- MARKET  → fill immediately at LTP (or the ``mock_market_price`` fallback).
- LIMIT   → fill when LTP crosses the limit price:
    * BUY:  fill if LTP <= limit
    * SELL: fill if LTP >= limit
- SL      → trigger when LTP crosses ``trigger_price``, then behaves like LIMIT
- SL_M    → trigger when LTP crosses ``trigger_price``, then behaves like MARKET

Trigger direction:
    * BUY  triggers when LTP >= trigger_price (breakout entry)
    * SELL triggers when LTP <= trigger_price (breakdown entry / stop on long)

The executor is a pure function of (order, quote) → optional fill.
It does NOT mutate DB rows — the OrderManager persists everything.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.engine.market_data.base import Quote
from app.models.engine import OrderSide, OrderStatus, OrderType, PaperOrder


@dataclass(slots=True)
class Fill:
    quantity: float
    price: float


class PaperExecutor:
    def try_fill(self, order: PaperOrder, quote: Quote) -> Optional[Fill]:
        """Return a Fill if ``order`` should fill against ``quote``, else None.

        Assumes ``order.status`` is PENDING or OPEN. Callers are responsible
        for skipping already-terminal orders.
        """
        if order.symbol != quote.symbol or order.exchange != quote.exchange:
            return None
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            return None

        ltp = float(quote.price)
        side = order.side
        qty = float(order.quantity) - float(order.filled_quantity)
        if qty <= 0:
            return None

        # ---- SL / SL_M: check trigger first ---------------------------------
        if order.order_type in (OrderType.SL, OrderType.SL_M):
            if order.trigger_price is None:
                return None
            trigger = float(order.trigger_price)
            triggered = (side == OrderSide.BUY and ltp >= trigger) or (
                side == OrderSide.SELL and ltp <= trigger
            )
            if not triggered:
                return None
            if order.order_type == OrderType.SL_M:
                return Fill(quantity=qty, price=ltp)
            # SL (limit) — still needs price crossing
            if order.price is None:
                return Fill(quantity=qty, price=ltp)
            return self._limit_fill(side, qty, ltp, float(order.price))

        # ---- MARKET ---------------------------------------------------------
        if order.order_type == OrderType.MARKET:
            return Fill(quantity=qty, price=ltp)

        # ---- LIMIT ----------------------------------------------------------
        if order.order_type == OrderType.LIMIT:
            if order.price is None:
                return None
            return self._limit_fill(side, qty, ltp, float(order.price))

        return None

    @staticmethod
    def _limit_fill(side: OrderSide, qty: float, ltp: float, limit: float) -> Optional[Fill]:
        if side == OrderSide.BUY and ltp <= limit:
            return Fill(quantity=qty, price=min(ltp, limit))
        if side == OrderSide.SELL and ltp >= limit:
            return Fill(quantity=qty, price=max(ltp, limit))
        return None
