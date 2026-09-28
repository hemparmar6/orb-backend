"""EmergencyFlattenService — the muscle behind the LIVE kill switch.

When the global kill switch is activated, blocking *new* strategy orders is not
enough for a genuine emergency stop. This service performs the real work for a
single live engine session, using the broker as the **source of truth**:

1. Cancel outstanding/pending live orders at the broker (except our own
   kill-switch closing orders).
2. Fetch the *actual* broker positions (never trust the DB position).
3. Submit opposite-side MARKET closing orders for every non-zero net position
   (handles both long and short; no order for flat/zero positions).
4. Re-fetch broker positions and VERIFY the account is flat.
5. Persist the closing orders (as ``PaperOrder`` rows) and a structured result.

Safety guarantees:
- Never places a closing order without first reading the broker position.
- Idempotent: a symbol that already has an open/filled kill-switch closing order
  (from a previous activation) is skipped — no duplicate flattening orders.
- Fails closed: if any cancel/close/verification step fails, the result is
  ``partial`` or ``failed`` and NEVER reports ``flat`` unless the broker
  confirms zero positions and there were no failures.

This service intentionally routes everything through the ``BrokerAdapter``
abstraction, so it works unchanged for Dhan, Kotak Neo, and the mock-live
broker without any broker-specific logic here.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerAdapter, BrokerOrderStatus
from app.core.logging import get_logger
from app.engine.orders.live_executor import LiveBrokerExecutor
from app.models.engine import (
    OrderProduct,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
)

logger = get_logger(__name__)

# Tag stamped on every kill-switch closing order so we can (a) recognise our
# own orders when cancelling and (b) enforce idempotency across activations.
KILL_SWITCH_TAG = "kill_switch_flat"

_EPS = 1e-9

_OPEN_BROKER_STATUSES = {
    BrokerOrderStatus.PENDING,
    BrokerOrderStatus.OPEN,
    BrokerOrderStatus.PARTIALLY_FILLED,
}

_OPEN_OR_DONE_ORDER_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.OPEN,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.FILLED,
)


@dataclass
class SymbolFlattenResult:
    symbol: str
    exchange: str
    product: str
    broker_net_before: float
    side: Optional[str] = None
    quantity: float = 0.0
    action: str = "already_flat"  # already_flat|closed|skipped_existing|close_failed
    closing_order_id: Optional[str] = None
    closing_broker_order_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class SessionFlattenResult:
    engine_session_id: str
    broker_account_id: Optional[str] = None
    broker: Optional[str] = None
    cancelled_order_ids: list[str] = field(default_factory=list)
    cancel_failures: list[dict[str, str]] = field(default_factory=list)
    symbols: list[SymbolFlattenResult] = field(default_factory=list)
    verified_flat: bool = False
    remaining_positions: list[dict[str, Any]] = field(default_factory=list)
    status: str = "failed"  # flat|partial|failed
    reason: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EmergencyFlattenService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def flatten(
        self,
        *,
        adapter: BrokerAdapter,
        engine_session_id: str,
        user_id: str,
        broker_account_id: Optional[str] = None,
        reason: str = "kill_switch",
        verify_attempts: int = 6,
        verify_delay: float = 0.02,
    ) -> SessionFlattenResult:
        """Cancel open orders + flatten broker positions for ONE live session."""
        result = SessionFlattenResult(
            engine_session_id=engine_session_id,
            broker_account_id=broker_account_id,
            broker=getattr(adapter, "broker_type", None),
            reason=reason,
        )

        # Broker order ids of our own (existing) kill-switch closing orders —
        # never cancel these, and use them for idempotency below.
        existing = await self._existing_kill_switch_orders(engine_session_id)
        existing_broker_ids = {
            o.broker_order_id for o in existing if o.broker_order_id
        }
        symbols_with_open_closer = {
            o.symbol
            for o in existing
            if o.status in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED)
        }

        # ---- 1. Cancel outstanding/pending broker orders -----------------
        try:
            broker_orders = await adapter.list_orders()
        except Exception as exc:  # broker unreachable → fail closed
            result.error = f"list_orders failed: {exc}"
            result.status = "failed"
            logger.exception("kill_switch_list_orders_failed",
                             extra={"engine_session_id": engine_session_id})
            await self._log_event(result)
            return result

        for bo in broker_orders:
            if bo.status not in _OPEN_BROKER_STATUSES:
                continue
            if bo.broker_order_id in existing_broker_ids:
                continue  # our own closing order — leave it working
            try:
                await adapter.cancel_order(bo.broker_order_id)
                result.cancelled_order_ids.append(bo.broker_order_id)
            except Exception as exc:
                result.cancel_failures.append(
                    {"broker_order_id": bo.broker_order_id, "error": str(exc)}
                )
                logger.exception("kill_switch_cancel_failed",
                                 extra={"broker_order_id": bo.broker_order_id})

        # Also reflect our DB-side open (strategy) orders as cancelled so the
        # engine's view matches the broker. Best-effort; broker is truth.
        await self._cancel_db_open_orders(engine_session_id, existing_broker_ids)

        # ---- 2. Determine ACTUAL broker positions (source of truth) ------
        try:
            positions = await adapter.list_positions()
        except Exception as exc:
            result.error = f"list_positions failed: {exc}"
            result.status = "failed"
            logger.exception("kill_switch_list_positions_failed",
                             extra={"engine_session_id": engine_session_id})
            await self._log_event(result)
            return result

        # ---- 3. Submit opposite-side closing orders ----------------------
        executor = LiveBrokerExecutor(
            adapter, broker_account_id=broker_account_id or ""
        )
        for pos in positions:
            net = float(pos.net_quantity)
            sym_res = SymbolFlattenResult(
                symbol=pos.symbol,
                exchange=pos.exchange,
                product=str(getattr(pos, "product", "") or ""),
                broker_net_before=net,
            )
            if abs(net) <= _EPS:
                sym_res.action = "already_flat"
                result.symbols.append(sym_res)
                continue

            # Idempotency: never place a second closing order for a symbol that
            # already has an open/filled kill-switch closing order.
            if pos.symbol in symbols_with_open_closer:
                sym_res.action = "skipped_existing"
                result.symbols.append(sym_res)
                continue

            side = OrderSide.SELL if net > 0 else OrderSide.BUY
            qty = abs(net)
            sym_res.side = side.value
            sym_res.quantity = qty
            try:
                order = await self._place_closing_order(
                    executor=executor,
                    user_id=user_id,
                    engine_session_id=engine_session_id,
                    symbol=pos.symbol,
                    exchange=pos.exchange,
                    product_str=sym_res.product,
                    side=side,
                    quantity=qty,
                )
                sym_res.closing_order_id = order.id
                sym_res.closing_broker_order_id = order.broker_order_id
                if order.status == OrderStatus.REJECTED:
                    sym_res.action = "close_failed"
                    sym_res.error = order.rejection_reason or "broker rejected closing order"
                else:
                    sym_res.action = "closed"
                    # Mark that this symbol now has an in-flight closer so a
                    # second call within the same run stays idempotent too.
                    symbols_with_open_closer.add(pos.symbol)
            except Exception as exc:
                sym_res.action = "close_failed"
                sym_res.error = str(exc)
                logger.exception("kill_switch_close_failed",
                                 extra={"symbol": pos.symbol})
            result.symbols.append(sym_res)

        # ---- 4. Verify the broker reports flat ---------------------------
        remaining = await self._verify_flat(adapter, verify_attempts, verify_delay)
        if remaining is None:
            result.error = "verification failed: could not read broker positions"
            result.status = "failed"
            result.verified_flat = False
            await self._log_event(result)
            return result

        result.remaining_positions = [
            {
                "symbol": p.symbol,
                "exchange": p.exchange,
                "net_quantity": float(p.net_quantity),
            }
            for p in remaining
        ]
        result.verified_flat = len(remaining) == 0

        # ---- 5. Final status -------------------------------------------
        close_failures = [s for s in result.symbols if s.action == "close_failed"]
        had_failures = bool(result.cancel_failures or close_failures)
        if result.verified_flat and not had_failures:
            result.status = "flat"
        elif not result.verified_flat and had_failures and not result.cancelled_order_ids \
                and not any(s.action == "closed" for s in result.symbols):
            result.status = "failed"
        else:
            # Either not verified flat yet, or there were failures alongside
            # partial progress. NEVER report flat in this branch.
            result.status = "partial"

        await self._log_event(result)
        return result

    # ---- helpers --------------------------------------------------------

    async def _existing_kill_switch_orders(self, engine_session_id: str) -> list[PaperOrder]:
        stmt = select(PaperOrder).where(
            PaperOrder.engine_session_id == engine_session_id,
            PaperOrder.tag == KILL_SWITCH_TAG,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _cancel_db_open_orders(
        self, engine_session_id: str, keep_broker_ids: set[str]
    ) -> None:
        """Mark our own strategy PaperOrders (still open) as CANCELLED so the
        engine's DB view matches the broker after the emergency stop."""
        stmt = select(PaperOrder).where(
            PaperOrder.engine_session_id == engine_session_id,
            PaperOrder.status.in_(
                (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)
            ),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        now = datetime.now(timezone.utc)
        for o in rows:
            if o.tag == KILL_SWITCH_TAG:
                continue  # keep our closing orders working
            if o.broker_order_id in keep_broker_ids:
                continue
            o.status = OrderStatus.CANCELLED
            o.cancelled_at = now
        await self.session.flush()

    async def _place_closing_order(
        self,
        *,
        executor: LiveBrokerExecutor,
        user_id: str,
        engine_session_id: str,
        symbol: str,
        exchange: str,
        product_str: str,
        side: OrderSide,
        quantity: float,
    ) -> PaperOrder:
        try:
            product = OrderProduct(product_str)
        except ValueError:
            product = OrderProduct.MIS
        order = PaperOrder(
            user_id=user_id,
            engine_session_id=engine_session_id,
            symbol=symbol,
            exchange=exchange,
            side=side,
            order_type=OrderType.MARKET,
            product=product,
            quantity=quantity,
            price=None,
            status=OrderStatus.PENDING,
            strategy_name="kill_switch",
            tag=KILL_SWITCH_TAG,
            placed_at=datetime.now(timezone.utc),
        )
        self.session.add(order)
        await self.session.flush()
        # Route through the broker abstraction (sets broker_order_id + status).
        await executor.on_place(order)
        await self.session.flush()
        return order

    async def _verify_flat(
        self, adapter: BrokerAdapter, attempts: int, delay: float
    ):
        remaining = None
        for i in range(max(1, attempts)):
            try:
                positions = await adapter.list_positions()
            except Exception:
                logger.exception("kill_switch_verify_positions_failed")
                return None
            remaining = [p for p in positions if abs(float(p.net_quantity)) > _EPS]
            if not remaining:
                return remaining
            if i < attempts - 1:
                await asyncio.sleep(delay)
        return remaining

    async def _log_event(self, result: SessionFlattenResult) -> None:
        """Persist the emergency-action result to the execution-safety audit
        log (existing table — no schema change)."""
        try:
            from app.models.execution_safety import (
                ExecutionLimitType,
                ExecutionSafetyAction,
                ExecutionSafetyEvent,
            )

            evt = ExecutionSafetyEvent(
                user_id=None,
                engine_session_id=result.engine_session_id,
                broker=result.broker,
                limit_type=ExecutionLimitType.EMERGENCY_STOP,
                action=ExecutionSafetyAction.KILL_SWITCH_ACTIVATED,
                reason=f"emergency_flatten:{result.status}",
                meta=result.to_dict(),
            )
            self.session.add(evt)
            await self.session.flush()
        except Exception:  # pragma: no cover - never break the flatten
            logger.exception("kill_switch_flatten_log_failed")
