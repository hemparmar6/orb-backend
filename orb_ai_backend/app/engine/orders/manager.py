"""OrderManager — paper order lifecycle.

Public methods (all async):
- ``place(intent)``          — creates a PaperOrder (PENDING/OPEN)
- ``modify(order_id, ...)``  — mutate PENDING/OPEN orders
- ``cancel(order_id)``       — cancel PENDING/OPEN orders
- ``get(order_id)``, ``list(...)``
- ``on_quote(quote)``        — feed a market tick; fills eligible orders,
                                updates positions via PortfolioManager,
                                and logs paper trades via TradeLogger.

Auto-generated child orders:
- If ``stop_loss`` and/or ``target_price`` are set on an entry order, then
  when that entry fills we automatically create the paired SL_M / LIMIT
  exit orders (tagged "sl" / "target"). Whichever fills first, the other
  is auto-cancelled (OCO).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    InvalidOrderError,
    OrderNotFoundError,
)
from app.core.logging import get_logger
from app.engine.logger import TradeLogger
from app.engine.market_data.base import Quote
from app.engine.orders.executor import Fill, PaperExecutor
from app.engine.orders.executor_interface import Executor
from app.engine.orders.paper_executor_adapter import PaperExecutorAdapter
from app.engine.portfolio.manager import PortfolioManager
from app.models.engine import (
    ExecutionMode,
    OrderProduct,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class OrderIntentRequest:
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    product: OrderProduct = OrderProduct.MIS
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    exchange: str = "MOCK"
    tag: Optional[str] = None
    strategy_name: str = ""
    parent_order_id: Optional[str] = None  # internal: for auto-generated SL/TP


@dataclass(slots=True)
class FillReport:
    order: PaperOrder
    fill: Fill
    realized_pnl_delta: float = 0.0
    child_orders_created: list[PaperOrder] = field(default_factory=list)
    child_orders_cancelled: list[PaperOrder] = field(default_factory=list)
    # Task 4: set True when we could NOT cancel/reduce a contingent sibling
    # exit at the broker. The position must then NOT be treated as safely
    # reconciled — a resting duplicate exit may still be live at the broker.
    sibling_cancel_failed: bool = False


# Orders that still count as an "in-flight" exit (may still execute).
_LIVE_ORDER_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.OPEN,
    OrderStatus.PARTIALLY_FILLED,
)
_OCO_EXIT_TAGS = ("sl", "target")
_EPS = 1e-9


# ---- OCO linkage: entry.id -> {"sl": order_id, "tp": order_id} ------------
# Tracked in-memory per OrderManager instance. Because the OrderManager is
# scoped to a single engine session task, this is fine. If the session is
# restarted, OCO relations are rediscovered from paper_orders.tag + parent
# lookup (see _rehydrate_oco).


class OrderManager:
    def __init__(
        self,
        session: AsyncSession,
        *,
        engine_session_id: str,
        user_id: str,
        portfolio: PortfolioManager,
        trade_logger: TradeLogger,
        executor: Executor | PaperExecutor | None = None,
    ) -> None:
        self.session = session
        self.engine_session_id = engine_session_id
        self.user_id = user_id
        self.portfolio = portfolio
        self.trade_logger = trade_logger
        # Accept either a full Executor OR the legacy PaperExecutor (Module 2 API).
        if executor is None:
            self.executor: Executor = PaperExecutorAdapter()
        elif isinstance(executor, PaperExecutor):
            self.executor = PaperExecutorAdapter(executor)
        else:
            self.executor = executor
        # entry_order_id -> {"sl": order_id | None, "tp": order_id | None}
        self._oco: dict[str, dict[str, str | None]] = {}

    # ---- CRUD -------------------------------------------------------------

    async def place(self, intent: OrderIntentRequest) -> PaperOrder:
        self._validate(intent)

        # ---- Task 4: idempotent exit guard ---------------------------
        # Prevent a live position from generating two *effective* exits. A
        # NON-OCO closing/reducing order (e.g. a strategy-level SL/target
        # market exit, or a repeated exit signal) is suppressed when the
        # remaining position is already fully covered by in-flight exit orders
        # (the broker-side OCO legs, or an earlier still-open exit). The OCO
        # legs themselves (tag "sl"/"target") bypass this guard — they are the
        # sanctioned contingent pair, and the kill switch never routes through
        # here so it always retains priority.
        if intent.tag not in _OCO_EXIT_TAGS:
            closing, net_abs = await self._closing_intent(intent)
            if closing and net_abs > _EPS:
                covered = await self._inflight_exit_quantity(intent)
                if covered >= net_abs - _EPS:
                    raise InvalidOrderError(
                        "An exit for this position is already in-flight",
                        code="duplicate_exit_suppressed",
                    )

        # ---- Milestone 9: Risk Management gate ------------------------
        # Runs for BOTH paper and live execution — enforces the user's
        # personal risk limits (daily loss, max trades/day, exposure,
        # position size, trading hours, live-trading-enabled, etc.).
        # If it denies, we raise so the caller records the rejection.
        _is_live_exec = getattr(self.executor, "execution_mode", "paper") == "live"
        if _is_live_exec:
            # Defence in depth for any engine path that constructs an executor
            # directly instead of going through /trading/start.
            from app.services.trading_mode_service import TradingModeService

            await TradingModeService(self.session).assert_live_enabled()
        try:
            from app.services.risk_management_service import (
                RiskManagementService,
                RiskOrderContext,
            )
            rm = RiskManagementService(self.session)
            rm_ctx = RiskOrderContext(
                user_id=self.user_id,
                bot_id=None,  # discovered below if we're inside a bot session
                symbol=intent.symbol,
                side=intent.side.value if hasattr(intent.side, "value") else str(intent.side),
                quantity=float(intent.quantity),
                price=float(intent.price) if intent.price is not None else None,
                execution_mode="live" if _is_live_exec else "paper",
                is_exit=bool(intent.tag in ("sl", "target")),
            )
            # Best-effort bot discovery
            try:
                from app.models.bot import Bot as _Bot
                from sqlalchemy import select as _sel
                _bot = (await self.session.execute(
                    _sel(_Bot).where(_Bot.engine_session_id == self.engine_session_id)
                )).scalar_one_or_none()
                if _bot is not None:
                    rm_ctx.bot_id = _bot.id
            except Exception:  # pragma: no cover
                pass
            rm_decision = await rm.check_order(rm_ctx)
            if not rm_decision.allowed:
                raise InvalidOrderError(
                    rm_decision.reason or "Order blocked by risk management",
                    code=(
                        f"risk_management_"
                        f"{rm_decision.event_type.value if rm_decision.event_type else 'blocked'}"
                    ),
                )
        except InvalidOrderError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.exception("risk_management_check_failed")

        # ---- Milestone 8: Execution Safety gate ------------------------
        # Applied only for LIVE execution (paper trades never touch a
        # broker so the rate/dup guards would be counter-productive). The
        # user's spec is explicit: "Final execution protection before any
        # order reaches the broker."
        _is_live = _is_live_exec
        if _is_live:
            from app.services.execution_safety_service import (
                ExecutionSafetyAction,
                ExecutionSafetyService,
                SafetyContext,
            )

            # Discover the bot_id + broker for richer audit rows. Best-effort.
            bot_id: str | None = None
            broker: str | None = None
            try:
                from app.models.bot import Bot
                from sqlalchemy import select as _sel
                b = (await self.session.execute(
                    _sel(Bot).where(Bot.engine_session_id == self.engine_session_id)
                )).scalar_one_or_none()
                if b is not None:
                    bot_id = b.id
                    broker = str(b.execution_mode)
            except Exception:  # pragma: no cover - defensive
                bot_id = None

            ctx = SafetyContext(
                user_id=self.user_id,
                bot_id=bot_id,
                broker=broker,
                engine_session_id=self.engine_session_id,
                symbol=intent.symbol,
                side=intent.side.value if hasattr(intent.side, "value") else str(intent.side),
                quantity=float(intent.quantity),
                price=float(intent.price) if intent.price is not None else None,
                order_type=intent.order_type.value if hasattr(intent.order_type, "value") else str(intent.order_type),
            )
            safety = ExecutionSafetyService(self.session)
            decision = await safety.check_order(ctx)
            if decision.action in (
                ExecutionSafetyAction.REJECTED,
                ExecutionSafetyAction.DUPLICATE_BLOCKED,
                ExecutionSafetyAction.KILL_SWITCH_ACTIVATED,
            ):
                raise InvalidOrderError(
                    decision.reason or "Order blocked by execution safety",
                    code=f"execution_safety_{decision.action.value}",
                )
            # QUEUED and ALLOWED both proceed synchronously here — QUEUED
            # is informational so the audit trail records how close we
            # were to the limit.

        now = datetime.now(timezone.utc)
        order = PaperOrder(
            user_id=self.user_id,
            engine_session_id=self.engine_session_id,
            symbol=intent.symbol,
            exchange=intent.exchange,
            side=intent.side,
            order_type=intent.order_type,
            product=intent.product,
            quantity=intent.quantity,
            price=intent.price,
            trigger_price=intent.trigger_price,
            stop_loss=intent.stop_loss,
            target_price=intent.target_price,
            status=OrderStatus.OPEN if intent.order_type != OrderType.MARKET else OrderStatus.PENDING,
            strategy_name=intent.strategy_name,
            tag=intent.tag,
            placed_at=now,
        )
        self.session.add(order)
        await self.session.flush()
        # Delegate to the executor (paper=no-op; live=submit to broker).
        try:
            await self.executor.on_place(order)
        except Exception as exc:
            # Broker rejected — mark REJECTED and re-raise so the caller
            # (StrategyContext.place_order → runner) can persist it.
            order.status = OrderStatus.REJECTED
            order.rejection_reason = str(exc)
            await self.session.flush()
            logger.exception("executor_on_place_failed",
                             extra={"order_id": order.id, "executor": self.executor.execution_mode})
            raise
        await self.session.flush()
        logger.info(
            "paper_order_placed",
            extra={
                "order_id": order.id,
                "symbol": order.symbol,
                "side": order.side.value,
                "type": order.order_type.value,
                "qty": float(order.quantity),
                "price": float(order.price) if order.price is not None else None,
                "tag": order.tag,
            },
        )
        return order

    async def modify(
        self,
        order_id: str,
        *,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        target_price: Optional[float] = None,
    ) -> PaperOrder:
        order = await self._get_or_raise(order_id)
        if order.status not in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED):
            raise InvalidOrderError(
                f"Cannot modify order in status {order.status.value}",
                code="order_not_modifiable",
            )
        if quantity is not None:
            if quantity <= float(order.filled_quantity):
                raise InvalidOrderError(
                    "New quantity must be greater than already-filled quantity",
                    code="invalid_quantity",
                )
            order.quantity = quantity
        if price is not None:
            order.price = price
        if trigger_price is not None:
            order.trigger_price = trigger_price
        if stop_loss is not None:
            order.stop_loss = stop_loss
        if target_price is not None:
            order.target_price = target_price
        # Notify executor (live: forwards to broker; paper: no-op).
        await self.executor.on_modify(
            order,
            {"quantity": quantity, "price": price, "trigger_price": trigger_price},
        )
        await self.session.flush()
        logger.info("paper_order_modified", extra={"order_id": order.id})
        return order

    async def cancel(self, order_id: str) -> PaperOrder:
        order = await self._get_or_raise(order_id)
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            raise InvalidOrderError(
                f"Cannot cancel order in status {order.status.value}",
                code="order_not_cancellable",
            )
        # Live executor cancels at the broker first — paper mode is a no-op.
        await self.executor.on_cancel(order)
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.now(timezone.utc)
        await self.session.flush()
        logger.info("paper_order_cancelled", extra={"order_id": order.id})
        return order

    async def get(self, order_id: str) -> PaperOrder:
        return await self._get_or_raise(order_id)

    async def list_orders(
        self,
        *,
        status: OrderStatus | None = None,
        symbol: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[PaperOrder]:
        stmt = select(PaperOrder).where(PaperOrder.engine_session_id == self.engine_session_id)
        if status is not None:
            stmt = stmt.where(PaperOrder.status == status)
        if symbol is not None:
            stmt = stmt.where(PaperOrder.symbol == symbol)
        stmt = stmt.order_by(PaperOrder.created_at.desc()).offset(offset).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    # ---- fill engine ------------------------------------------------------

    async def on_quote(self, quote: Quote) -> list[FillReport]:
        """Feed a quote. Fills eligible orders and returns fill reports."""
        stmt = (
            select(PaperOrder)
            .where(PaperOrder.engine_session_id == self.engine_session_id)
            .where(PaperOrder.symbol == quote.symbol)
            .where(PaperOrder.exchange == quote.exchange)
            .where(PaperOrder.status.in_((OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)))
            .order_by(PaperOrder.placed_at.asc())
        )
        candidates = (await self.session.execute(stmt)).scalars().all()

        reports: list[FillReport] = []
        for order in candidates:
            fill = self.executor.try_fill_from_quote(order, quote)
            if fill is None:
                continue
            report = await self._apply_fill(order, fill)
            reports.append(report)
        return reports

    async def _apply_fill(self, order: PaperOrder, fill: Fill) -> FillReport:
        now = datetime.now(timezone.utc)

        # Update the order.
        already_filled = float(order.filled_quantity)
        new_total = already_filled + fill.quantity
        prev_avg = float(order.average_fill_price) if order.average_fill_price is not None else 0.0
        # weighted avg for partial fills
        if new_total > 0:
            new_avg = (prev_avg * already_filled + fill.price * fill.quantity) / new_total
        else:
            new_avg = fill.price
        order.filled_quantity = new_total
        order.average_fill_price = round(new_avg, 4)
        if new_total >= float(order.quantity) - 1e-9:
            order.status = OrderStatus.FILLED
            order.filled_at = now
        else:
            order.status = OrderStatus.PARTIALLY_FILLED
        await self.session.flush()

        # Update position + realized P&L.
        _pos, realized_delta = await self.portfolio.apply_fill(
            user_id=self.user_id,
            engine_session_id=self.engine_session_id,
            symbol=order.symbol,
            exchange=order.exchange,
            product=order.product,
            side=order.side,
            quantity=fill.quantity,
            price=fill.price,
        )

        # Log the trade.
        await self.trade_logger.log(
            user_id=self.user_id,
            engine_session_id=self.engine_session_id,
            paper_order_id=order.id,
            symbol=order.symbol,
            exchange=order.exchange,
            side=order.side,
            quantity=fill.quantity,
            price=fill.price,
            realized_pnl_delta=realized_delta,
            strategy_name=order.strategy_name,
            executed_at=now,
        )

        report = FillReport(order=order, fill=fill, realized_pnl_delta=realized_delta)

        # On full fill of an ENTRY (has SL/TP hints and no tag), spawn OCO
        # children — but only if we haven't already (idempotent across
        # duplicate broker fill events and strategy restarts/recovery).
        if (
            order.status == OrderStatus.FILLED
            and order.tag is None
            and (order.stop_loss is not None or order.target_price is not None)
        ):
            if not await self._has_active_exit_orders(order):
                children = await self._spawn_oco_children(order)
                report.child_orders_created = children

        # An exit leg (sl/target) filled — fully OR partially. Reconcile the
        # remaining contingent exit(s) against the ACTUAL position so we never
        # exit more than we hold and never leave a duplicate exit resting at
        # the broker. Idempotent: rerunning with the same net is a no-op.
        if order.tag in _OCO_EXIT_TAGS and order.status in (
            OrderStatus.FILLED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            await self._reconcile_oco_after_exit_fill(order, report)

        return report

    # ---- Task 4: durable, broker-routed OCO reconciliation --------------

    async def _has_active_exit_orders(self, entry: PaperOrder) -> bool:
        """True if a non-terminal sl/target order already exists for the same
        position — used to make OCO child spawning idempotent."""
        siblings = await self._open_exit_legs(entry)
        return len(siblings) > 0

    async def _open_exit_legs(
        self, ref: PaperOrder, *, exclude_id: str | None = None
    ) -> list[PaperOrder]:
        stmt = (
            select(PaperOrder)
            .where(PaperOrder.engine_session_id == self.engine_session_id)
            .where(PaperOrder.symbol == ref.symbol)
            .where(PaperOrder.exchange == ref.exchange)
            .where(PaperOrder.product == ref.product)
            .where(PaperOrder.tag.in_(_OCO_EXIT_TAGS))
            .where(PaperOrder.status.in_(_LIVE_ORDER_STATUSES))
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        if exclude_id is not None:
            rows = [r for r in rows if r.id != exclude_id]
        return rows

    async def _reconcile_oco_after_exit_fill(
        self, filled_leg: PaperOrder, report: FillReport
    ) -> None:
        """After an exit leg fills, align the remaining sibling exit(s) with the
        actual net position (broker is source of truth via the executor).

        - net flat  → cancel the sibling(s) at the broker.
        - net > 0   → reduce the sibling(s) to the remaining quantity.

        A broker cancel/modify failure is NOT swallowed as success: the sibling
        is left untouched and ``report.sibling_cancel_failed`` is set so callers
        do not treat the position as safely closed (fail closed).
        """
        pos = await self.portfolio.get_position(
            self.engine_session_id,
            filled_leg.symbol,
            filled_leg.exchange,
            filled_leg.product,
        )
        net_abs = abs(float(pos.net_quantity)) if pos is not None else 0.0
        siblings = await self._open_exit_legs(filled_leg, exclude_id=filled_leg.id)
        now = datetime.now(timezone.utc)

        for s in siblings:
            if net_abs <= _EPS:
                # Fully flat — cancel the redundant contingent exit.
                try:
                    await self.executor.on_cancel(s)
                except Exception as exc:  # broker refused — do NOT mark closed
                    report.sibling_cancel_failed = True
                    logger.warning(
                        "oco_sibling_cancel_failed",
                        extra={"order_id": s.id, "error": str(exc)},
                    )
                    continue
                if s.status not in (
                    OrderStatus.CANCELLED,
                    OrderStatus.FILLED,
                    OrderStatus.REJECTED,
                ):
                    s.status = OrderStatus.CANCELLED
                    s.cancelled_at = now
                report.child_orders_cancelled.append(s)
            else:
                # Partial exit — shrink the sibling so total exits never exceed
                # what we still hold.
                if abs(float(s.quantity) - net_abs) > _EPS and net_abs > float(
                    s.filled_quantity
                ):
                    try:
                        await self.executor.on_modify(s, {"quantity": net_abs})
                    except Exception as exc:  # broker refused the resize
                        report.sibling_cancel_failed = True
                        logger.warning(
                            "oco_sibling_modify_failed",
                            extra={"order_id": s.id, "error": str(exc)},
                        )
                        continue
                    s.quantity = net_abs
        await self.session.flush()

    async def _closing_intent(self, intent: OrderIntentRequest) -> tuple[bool, float]:
        """Return (is_closing, abs(net)) for the position this intent targets."""
        pos = await self.portfolio.get_position(
            self.engine_session_id, intent.symbol, intent.exchange, intent.product
        )
        net = float(pos.net_quantity) if pos is not None else 0.0
        closing = (net > _EPS and intent.side == OrderSide.SELL) or (
            net < -_EPS and intent.side == OrderSide.BUY
        )
        return closing, abs(net)

    async def _inflight_exit_quantity(self, intent: OrderIntentRequest) -> float:
        """Remaining quantity of in-flight orders that would close this
        position (same closing side), across OCO legs and prior exits."""
        stmt = (
            select(PaperOrder)
            .where(PaperOrder.engine_session_id == self.engine_session_id)
            .where(PaperOrder.symbol == intent.symbol)
            .where(PaperOrder.exchange == intent.exchange)
            .where(PaperOrder.product == intent.product)
            .where(PaperOrder.side == intent.side)
            .where(PaperOrder.status.in_(_LIVE_ORDER_STATUSES))
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        # OCO legs are a full-size pair; counting the max leg is enough to know
        # the position is covered without double-counting the sl+target pair.
        oco_cover = 0.0
        other_cover = 0.0
        for o in rows:
            remaining = float(o.quantity) - float(o.filled_quantity)
            if remaining <= _EPS:
                continue
            if o.tag in _OCO_EXIT_TAGS:
                oco_cover = max(oco_cover, remaining)
            else:
                other_cover += remaining
        return oco_cover + other_cover

    async def _spawn_oco_children(self, entry: PaperOrder) -> list[PaperOrder]:
        exit_side = OrderSide.SELL if entry.side == OrderSide.BUY else OrderSide.BUY
        children: list[PaperOrder] = []
        sl_id: str | None = None
        tp_id: str | None = None

        if entry.stop_loss is not None:
            sl = await self.place(
                OrderIntentRequest(
                    symbol=entry.symbol,
                    exchange=entry.exchange,
                    side=exit_side,
                    quantity=float(entry.quantity),
                    order_type=OrderType.SL_M,
                    product=entry.product,
                    trigger_price=float(entry.stop_loss),
                    strategy_name=entry.strategy_name,
                    tag="sl",
                    parent_order_id=entry.id,
                )
            )
            children.append(sl)
            sl_id = sl.id

        if entry.target_price is not None:
            tp = await self.place(
                OrderIntentRequest(
                    symbol=entry.symbol,
                    exchange=entry.exchange,
                    side=exit_side,
                    quantity=float(entry.quantity),
                    order_type=OrderType.LIMIT,
                    product=entry.product,
                    price=float(entry.target_price),
                    strategy_name=entry.strategy_name,
                    tag="target",
                    parent_order_id=entry.id,
                )
            )
            children.append(tp)
            tp_id = tp.id

        self._oco[entry.id] = {"sl": sl_id, "tp": tp_id}
        return children

    async def _sibling_of(self, child: PaperOrder) -> PaperOrder | None:
        # Locate the entry this child belongs to by walking OCO map first.
        for entry_id, pair in self._oco.items():
            if child.id in (pair.get("sl"), pair.get("tp")):
                sibling_id = pair["tp"] if child.tag == "sl" else pair["sl"]
                if sibling_id is None:
                    return None
                return await self.session.get(PaperOrder, sibling_id)
        return None

    # ---- live-mode: reconcile updates from the broker's WS/poll stream --

    async def apply_broker_update(self, update) -> Optional[FillReport]:
        """Apply a ``BrokerOrderResult`` to the matching PaperOrder.

        Idempotent: if we've already seen ``filled_quantity`` at or above the
        broker's report, nothing happens. Any delta is treated as a fill and
        goes through the same code path as paper (position + P&L + trade log).

        Returns a FillReport if a fill delta was applied, else None.
        """
        from app.brokers.base import BrokerOrderResult, BrokerOrderStatus
        from app.engine.orders.live_executor import to_platform_status

        assert isinstance(update, BrokerOrderResult)

        # Look up by broker_order_id + session.
        stmt = (
            select(PaperOrder)
            .where(PaperOrder.engine_session_id == self.engine_session_id)
            .where(PaperOrder.broker_order_id == update.broker_order_id)
        )
        order = (await self.session.execute(stmt)).scalar_one_or_none()
        if order is None:
            # If client_order_id was echoed back, try that as a fallback.
            if update.client_order_id:
                order = await self.session.get(PaperOrder, update.client_order_id)
                if order is not None and order.engine_session_id != self.engine_session_id:
                    order = None
            if order is None:
                logger.warning(
                    "broker_update_no_matching_order",
                    extra={"broker_order_id": update.broker_order_id},
                )
                return None

        # Compute fill delta.
        prev_filled = float(order.filled_quantity)
        new_filled = float(update.filled_quantity)
        delta = new_filled - prev_filled

        # Apply the fill diff (if any) first, then status transitions.
        fill_report: Optional[FillReport] = None
        if delta > 1e-9 and update.average_fill_price is not None:
            # Broker's avg is over the ENTIRE filled quantity so we need to
            # back out the marginal-fill price for the delta.
            fill_price = _implied_delta_price(
                prev_filled=prev_filled,
                prev_avg=float(order.average_fill_price) if order.average_fill_price is not None else 0.0,
                new_filled=new_filled,
                new_avg=float(update.average_fill_price),
            )
            fill_report = await self._apply_fill(order, Fill(quantity=delta, price=fill_price))
        else:
            # No fill delta — just persist the new status / rejection reason.
            new_status = to_platform_status(update.status)
            order.status = new_status
            if update.status == BrokerOrderStatus.REJECTED and update.rejection_reason:
                order.rejection_reason = update.rejection_reason
            if new_status == OrderStatus.CANCELLED and order.cancelled_at is None:
                order.cancelled_at = datetime.now(timezone.utc)
            await self.session.flush()

        return fill_report

    async def open_broker_order_ids(self) -> list[str]:
        """List broker_order_id values for orders still in-flight — used by
        ``BrokerOrderStream`` polling fallback."""
        stmt = (
            select(PaperOrder.broker_order_id)
            .where(PaperOrder.engine_session_id == self.engine_session_id)
            .where(PaperOrder.broker_order_id.is_not(None))
            .where(
                PaperOrder.status.in_(
                    (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)
                )
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [r for r in rows if r]

    # ---- helpers ----------------------------------------------------------

    async def _get_or_raise(self, order_id: str) -> PaperOrder:
        order = await self.session.get(PaperOrder, order_id)
        if order is None or order.engine_session_id != self.engine_session_id:
            raise OrderNotFoundError()
        return order

    def _validate(self, intent: OrderIntentRequest) -> None:
        if intent.quantity <= 0:
            raise InvalidOrderError("Quantity must be positive")
        if intent.order_type == OrderType.LIMIT and intent.price is None:
            raise InvalidOrderError("LIMIT order requires price")
        if intent.order_type in (OrderType.SL, OrderType.SL_M) and intent.trigger_price is None:
            raise InvalidOrderError("SL / SL_M orders require trigger_price")


# ---- utility --------------------------------------------------------------


def _implied_delta_price(
    *, prev_filled: float, prev_avg: float, new_filled: float, new_avg: float
) -> float:
    """Given the previous and new (avg, filled_qty) reported by the broker,
    return the implied fill price of the marginal ``new_filled - prev_filled``
    quantity so we can post a single fill to the PortfolioManager.

    total_notional_new = new_filled * new_avg
    total_notional_old = prev_filled * prev_avg
    delta_notional     = total_notional_new - total_notional_old
    delta_qty          = new_filled - prev_filled
    fill_price         = delta_notional / delta_qty
    """
    delta_qty = new_filled - prev_filled
    if delta_qty <= 0:
        return new_avg
    delta_notional = (new_filled * new_avg) - (prev_filled * prev_avg)
    return delta_notional / delta_qty
