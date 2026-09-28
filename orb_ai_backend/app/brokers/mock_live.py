"""MockLiveBroker — fully working simulated broker for tests + demos.

Behaves like a real broker on the wire:
- ``place_order`` returns a broker_order_id immediately and enqueues a fill event.
- ``stream_order_updates()`` yields fill events (as a WebSocket would).
- ``modify_order`` / ``cancel_order`` mutate in-memory state; updates flow
  through the same stream.

Because this class simulates the WIRE (broker-side book), it deliberately
does NOT touch our DB. The engine's ``LiveBrokerExecutor`` mirrors the
broker's fills back into ``paper_orders`` / ``paper_positions`` — exactly the
same code path a real broker would exercise.

Configurable behaviour (via credentials dict):
- ``initial_funds`` (float, default 100000) — starting available cash
- ``auto_fill`` (bool, default True) — if False, orders stay OPEN until
  explicitly filled via ``simulate_fill(broker_order_id)`` from a test.
- ``simulated_ltp`` (dict[str, float]) — per-symbol LTP for MARKET fills.
  Falls back to the order's ``price``/``trigger_price`` or 100.0.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from app.brokers.base import (
    BrokerAdapter,
    BrokerFunds,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
)
from app.brokers.registry import register_broker
from app.core.exceptions import BrokerError


@register_broker("mock_live")
class MockLiveBroker(BrokerAdapter):
    """See module docstring."""

    @classmethod
    def required_credentials(cls) -> list[str]:
        # No creds required — everything is in-memory. Tests may pass hints.
        return []

    def __init__(self, credentials: dict[str, Any], *, alias: str = "") -> None:
        super().__init__(credentials, alias=alias)
        self._funds = BrokerFunds(
            available=float(credentials.get("initial_funds", 100_000.0)),
            used=0.0,
            total=float(credentials.get("initial_funds", 100_000.0)),
            currency="INR",
        )
        self._auto_fill: bool = bool(credentials.get("auto_fill", True))
        self._simulated_ltp: dict[str, float] = dict(credentials.get("simulated_ltp", {}))

        self._orders: dict[str, dict[str, Any]] = {}  # broker_order_id -> record
        self._positions: dict[tuple[str, str, str], BrokerPositionSnapshot] = {}
        self._events: asyncio.Queue[BrokerOrderResult] = asyncio.Queue()
        self._stopped = False

    # ---- lifecycle ---------------------------------------------------

    async def start(self) -> None:
        self._stopped = False

    async def stop(self) -> None:
        self._stopped = True
        # Wake any consumer.
        self._events.put_nowait(_SENTINEL)

    async def health_check(self) -> bool:
        return not self._stopped

    # ---- order lifecycle --------------------------------------------

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderResult:
        broker_order_id = f"MOCK-{uuid.uuid4().hex[:12]}"
        record = {
            "broker_order_id": broker_order_id,
            "client_order_id": req.client_order_id,
            "symbol": req.symbol,
            "exchange": req.exchange,
            "side": req.side,
            "order_type": req.order_type,
            "product": req.product,
            "quantity": float(req.quantity),
            "price": req.price,
            "trigger_price": req.trigger_price,
            "tag": req.tag,
            "status": BrokerOrderStatus.OPEN,
            "filled_quantity": 0.0,
            "average_fill_price": None,
            "rejection_reason": None,
            "placed_at": datetime.now(timezone.utc),
        }
        self._orders[broker_order_id] = record
        initial = self._as_result(record)

        if self._auto_fill:
            # Simulate an instant fill on the next event loop turn.
            asyncio.create_task(self._simulate_immediate_fill(broker_order_id))

        return initial

    async def modify_order(
        self,
        broker_order_id: str,
        *,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
    ) -> BrokerOrderResult:
        record = self._require(broker_order_id)
        if record["status"] not in (BrokerOrderStatus.OPEN, BrokerOrderStatus.PENDING, BrokerOrderStatus.PARTIALLY_FILLED):
            raise BrokerError(f"Order {broker_order_id} not modifiable in status {record['status'].value}")
        if quantity is not None:
            record["quantity"] = float(quantity)
        if price is not None:
            record["price"] = float(price)
        if trigger_price is not None:
            record["trigger_price"] = float(trigger_price)
        result = self._as_result(record)
        await self._events.put(result)
        return result

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        record = self._require(broker_order_id)
        if record["status"] in (BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED):
            raise BrokerError(f"Order {broker_order_id} not cancellable in status {record['status'].value}")
        record["status"] = BrokerOrderStatus.CANCELLED
        result = self._as_result(record)
        await self._events.put(result)
        return result

    async def get_order(self, broker_order_id: str) -> BrokerOrderResult:
        return self._as_result(self._require(broker_order_id))

    async def list_orders(self) -> list[BrokerOrderResult]:
        return [self._as_result(r) for r in self._orders.values()]

    # ---- account state ----------------------------------------------

    async def get_funds(self) -> BrokerFunds:
        return self._funds

    async def list_positions(self) -> list[BrokerPositionSnapshot]:
        return list(self._positions.values())

    # ---- streaming ---------------------------------------------------

    async def stream_order_updates(self) -> AsyncIterator[BrokerOrderResult]:
        while not self._stopped:
            item = await self._events.get()
            if item is _SENTINEL:
                return
            yield item

    # ---- test helpers -----------------------------------------------

    async def simulate_fill(
        self,
        broker_order_id: str,
        *,
        fill_price: Optional[float] = None,
        quantity: Optional[float] = None,
    ) -> BrokerOrderResult:
        """Force-fill an order — used when auto_fill is disabled."""
        record = self._require(broker_order_id)
        remaining = float(record["quantity"]) - float(record["filled_quantity"])
        qty = float(quantity if quantity is not None else remaining)
        if qty <= 0:
            raise BrokerError("Nothing to fill")
        price = fill_price if fill_price is not None else self._price_for(record)
        self._apply_fill(record, qty, price)
        result = self._as_result(record)
        await self._events.put(result)
        return result

    def set_ltp(self, symbol: str, price: float) -> None:
        self._simulated_ltp[symbol] = float(price)

    # ---- internals ---------------------------------------------------

    def _require(self, broker_order_id: str) -> dict[str, Any]:
        rec = self._orders.get(broker_order_id)
        if rec is None:
            raise BrokerError(f"Broker order {broker_order_id} not found")
        return rec

    async def _simulate_immediate_fill(self, broker_order_id: str) -> None:
        await asyncio.sleep(0)  # let the caller's turn complete first
        record = self._orders.get(broker_order_id)
        if record is None or record["status"] in (
            BrokerOrderStatus.CANCELLED,
            BrokerOrderStatus.REJECTED,
        ):
            return
        remaining = float(record["quantity"]) - float(record["filled_quantity"])
        if remaining <= 0:
            return
        fill_price = self._price_for(record)
        self._apply_fill(record, remaining, fill_price)
        await self._events.put(self._as_result(record))

    def _price_for(self, record: dict[str, Any]) -> float:
        return (
            self._simulated_ltp.get(record["symbol"])
            or record.get("price")
            or record.get("trigger_price")
            or 100.0
        )

    def _apply_fill(self, record: dict[str, Any], qty: float, price: float) -> None:
        already = float(record["filled_quantity"])
        prev_avg = record["average_fill_price"] or 0.0
        new_total = already + qty
        new_avg = ((prev_avg * already) + (price * qty)) / new_total if new_total else price
        record["filled_quantity"] = new_total
        record["average_fill_price"] = round(new_avg, 4)
        record["status"] = (
            BrokerOrderStatus.FILLED
            if new_total >= float(record["quantity"]) - 1e-9
            else BrokerOrderStatus.PARTIALLY_FILLED
        )
        # Update broker-side position.
        key = (record["symbol"], record["exchange"], record["product"])
        pos = self._positions.get(key)
        signed = qty if record["side"] == "buy" else -qty
        if pos is None:
            self._positions[key] = BrokerPositionSnapshot(
                symbol=record["symbol"],
                exchange=record["exchange"],
                product=record["product"],
                net_quantity=signed,
                average_price=price,
                last_price=price,
            )
        else:
            old_qty = pos.net_quantity
            new_qty = old_qty + signed
            if (old_qty >= 0 and signed > 0) or (old_qty <= 0 and signed < 0):
                # Same side / opening — weighted avg
                total_abs = abs(old_qty) + abs(signed)
                if total_abs > 0:
                    pos.average_price = (
                        (abs(old_qty) * pos.average_price) + (abs(signed) * price)
                    ) / total_abs
            elif abs(signed) <= abs(old_qty):
                # Reducing — realize partial P&L
                pnl_side = 1 if old_qty > 0 else -1
                pos.realized_pnl += (price - pos.average_price) * abs(signed) * pnl_side
            else:
                # Flipping through zero
                pnl_side = 1 if old_qty > 0 else -1
                pos.realized_pnl += (price - pos.average_price) * abs(old_qty) * pnl_side
                pos.average_price = price
            pos.net_quantity = round(new_qty, 4)
            pos.last_price = price

    def _as_result(self, record: dict[str, Any]) -> BrokerOrderResult:
        return BrokerOrderResult(
            broker_order_id=record["broker_order_id"],
            status=record["status"],
            filled_quantity=float(record["filled_quantity"]),
            average_fill_price=(
                float(record["average_fill_price"])
                if record["average_fill_price"] is not None
                else None
            ),
            rejection_reason=record["rejection_reason"],
            client_order_id=record["client_order_id"],
            raw={"broker": "mock_live"},
        )


_SENTINEL: Any = object()
