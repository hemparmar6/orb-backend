"""LiveBrokerExecutor — routes engine orders to a ``BrokerAdapter``.

Responsibilities:
- ``on_place``    — build a ``BrokerOrderRequest`` from the ``PaperOrder`` and
                    submit it via the adapter. Store ``broker_order_id`` and
                    initial normalised status onto the same row.
- ``on_modify``   — delegate to ``adapter.modify_order``.
- ``on_cancel``   — delegate to ``adapter.cancel_order``.
- ``try_fill_from_quote`` — always returns None (live fills come via
                    ``BrokerOrderStream`` → ``OrderManager.apply_broker_update``).

Broker-side and platform-side statuses are the SAME normalised enum (see
``BrokerOrderStatus`` ↔ ``OrderStatus``) — we translate 1:1.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.brokers.base import (
    BrokerAdapter,
    BrokerOrderRequest,
    BrokerOrderStatus,
)
from app.core.logging import get_logger
from app.engine.market_data.base import Quote
from app.engine.orders.executor import Fill
from app.engine.orders.executor_interface import Executor
from app.models.engine import OrderStatus, PaperOrder

logger = get_logger(__name__)


# ---- Status mapping ------------------------------------------------------

_BROKER_TO_PLATFORM: dict[BrokerOrderStatus, OrderStatus] = {
    BrokerOrderStatus.PENDING: OrderStatus.PENDING,
    BrokerOrderStatus.OPEN: OrderStatus.OPEN,
    BrokerOrderStatus.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
    BrokerOrderStatus.FILLED: OrderStatus.FILLED,
    BrokerOrderStatus.CANCELLED: OrderStatus.CANCELLED,
    BrokerOrderStatus.REJECTED: OrderStatus.REJECTED,
}


def to_platform_status(bs: BrokerOrderStatus) -> OrderStatus:
    return _BROKER_TO_PLATFORM[bs]


# ---- Executor -----------------------------------------------------------


class LiveBrokerExecutor(Executor):
    execution_mode = "live"

    def __init__(self, adapter: BrokerAdapter, *, broker_account_id: str) -> None:
        self.adapter = adapter
        self.broker_account_id = broker_account_id

    async def on_place(self, order: PaperOrder) -> None:
        req = BrokerOrderRequest(
            client_order_id=order.id,
            symbol=order.symbol,
            exchange=order.exchange,
            side=order.side.value,
            order_type=order.order_type.value,
            product=order.product.value,
            quantity=float(order.quantity),
            price=float(order.price) if order.price is not None else None,
            trigger_price=float(order.trigger_price) if order.trigger_price is not None else None,
            tag=order.tag,
        )
        result = await self.adapter.place_order(req)
        order.broker_account_id = self.broker_account_id
        order.broker_order_id = result.broker_order_id
        order.status = to_platform_status(result.status)
        if result.status == BrokerOrderStatus.REJECTED and result.rejection_reason:
            order.rejection_reason = result.rejection_reason
        logger.info(
            "live_order_placed",
            extra={
                "order_id": order.id,
                "broker": self.adapter.broker_type,
                "broker_order_id": result.broker_order_id,
                "status": result.status.value,
            },
        )

    async def on_modify(self, order: PaperOrder, changes: dict) -> None:
        if not order.broker_order_id:
            return
        await self.adapter.modify_order(
            order.broker_order_id,
            quantity=changes.get("quantity"),
            price=changes.get("price"),
            trigger_price=changes.get("trigger_price"),
        )

    async def on_cancel(self, order: PaperOrder) -> None:
        if not order.broker_order_id:
            return
        result = await self.adapter.cancel_order(order.broker_order_id)
        order.status = to_platform_status(result.status)
        order.cancelled_at = datetime.now(timezone.utc)

    def try_fill_from_quote(self, order: PaperOrder, quote: Quote) -> Optional[Fill]:
        # In live mode, fills come from the broker's WS/poll stream.
        return None
