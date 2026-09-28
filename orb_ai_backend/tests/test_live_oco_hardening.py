"""Task 4 — Harden LIVE SL/Target/OCO and prevent duplicate exits.

The engine reaches the broker only through ``BrokerAdapter``; these tests drive
the LIVE reconciliation path deterministically by feeding ``BrokerOrderResult``
updates straight into ``OrderManager.apply_broker_update`` (exactly what
``BrokerOrderStream`` does), with a real ``LiveBrokerExecutor`` bound to a
``MockLiveBroker`` so sibling cancel/resize actually hit the broker.

Covered:
- SL fills           -> target cancelled at broker.
- Target fills       -> SL cancelled at broker.
- Duplicate fill event (WS + poll) -> processed once.
- Strategy SL + broker SL          -> second exit suppressed.
- Repeated exit signal             -> idempotent (suppressed).
- Partial SL fill                  -> sibling resized to remaining qty.
- Broker rejects sibling cancel    -> NOT falsely marked closed.
- Kill switch while OCO exists      -> safe flatten, no duplicate exit.
- Recovery with existing SL/target -> no duplicate orders.
- First exit with nothing in-flight -> allowed (guard helpers).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.brokers.base import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
)
from app.brokers.mock_live import MockLiveBroker
from app.core.exceptions import BrokerError, InvalidOrderError
from app.core.security import hash_password
from app.engine.logger import TradeLogger
from app.engine.market_data.mock import MockMarketDataProvider
from app.engine.orders.live_executor import LiveBrokerExecutor
from app.engine.orders.manager import OrderIntentRequest, OrderManager
from app.engine.portfolio.manager import PortfolioManager
from app.engine.strategy.manager import StrategyManager
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderProduct,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
)
from app.models.user import User
from app.services.emergency_flatten_service import (
    KILL_SWITCH_TAG,
    EmergencyFlattenService,
)
from app.services.live_recovery_service import LiveRecoveryService

ACCOUNT_ID = "oco-acct-1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _seed(db, *, strategy: str = "unit") -> tuple[User, EngineSession]:
    user = User(
        email=f"oco-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("password123"),
        full_name="OCO User",
    )
    db.add(user)
    await db.flush()
    sess = EngineSession(
        user_id=user.id,
        strategy_name=strategy,
        status=EngineSessionStatus.RUNNING,
        execution_mode=ExecutionMode.LIVE,
        broker_account_id=ACCOUNT_ID,
        symbols=["A"],
        params={},
        risk_config={},
        initial_capital=100_000,
        started_at=_now(),
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return user, sess


async def _adapter(auto_fill: bool = False) -> MockLiveBroker:
    a = MockLiveBroker({"auto_fill": auto_fill, "simulated_ltp": {"A": 100.0}})
    await a.start()
    return a


def _om(db, user, sess, adapter) -> OrderManager:
    return OrderManager(
        db,
        engine_session_id=sess.id,
        user_id=user.id,
        portfolio=PortfolioManager(db),
        trade_logger=TradeLogger(db),
        executor=LiveBrokerExecutor(adapter, broker_account_id=ACCOUNT_ID),
    )


async def _open_long(db, user, sess, qty=10, price=100.0):
    await PortfolioManager(db).apply_fill(
        user_id=user.id, engine_session_id=sess.id, symbol="A",
        exchange="NSE", product=OrderProduct.MIS,
        side=OrderSide.BUY, quantity=qty, price=price,
    )
    await db.commit()


async def _rest_exit(db, om, adapter, user, sess, *, tag, qty=10):
    """Create a resting exit leg (SELL) at the broker + DB, gate-free."""
    order = PaperOrder(
        user_id=user.id,
        engine_session_id=sess.id,
        symbol="A",
        exchange="NSE",
        side=OrderSide.SELL,
        order_type=OrderType.SL_M if tag == "sl" else OrderType.LIMIT,
        product=OrderProduct.MIS,
        quantity=qty,
        price=None if tag == "sl" else 110.0,
        trigger_price=95.0 if tag == "sl" else None,
        status=OrderStatus.OPEN,
        tag=tag,
        strategy_name="unit",
        placed_at=_now(),
    )
    db.add(order)
    await db.flush()
    await om.executor.on_place(order)  # submit to broker → sets broker_order_id
    await db.commit()
    await db.refresh(order)
    return order


def _fill_event(order: PaperOrder, qty: float, price: float, *, status=BrokerOrderStatus.FILLED):
    return BrokerOrderResult(
        broker_order_id=order.broker_order_id,
        status=status,
        filled_quantity=qty,
        average_fill_price=price,
        client_order_id=order.id,
    )


# ==========================================================================
# 1. SL fills -> target cancelled at broker.
# ==========================================================================


@pytest.mark.asyncio
async def test_sl_fill_cancels_target(db_session):
    user, sess = await _seed(db_session)
    adapter = await _adapter()
    om = _om(db_session, user, sess, adapter)
    await _open_long(db_session, user, sess, 10)
    sl = await _rest_exit(db_session, om, adapter, user, sess, tag="sl")
    tp = await _rest_exit(db_session, om, adapter, user, sess, tag="target")

    report = await om.apply_broker_update(_fill_event(sl, 10, 95.0))
    await db_session.commit()

    await db_session.refresh(sl)
    await db_session.refresh(tp)
    assert sl.status == OrderStatus.FILLED
    assert tp.status == OrderStatus.CANCELLED
    assert report is not None and report.sibling_cancel_failed is False
    # broker confirms target cancelled
    assert (await adapter.get_order(tp.broker_order_id)).status == BrokerOrderStatus.CANCELLED
    assert float((await PortfolioManager(db_session).get_position(sess.id, "A", "NSE")).net_quantity) == 0


# ==========================================================================
# 2. Target fills -> SL cancelled.
# ==========================================================================


@pytest.mark.asyncio
async def test_target_fill_cancels_sl(db_session):
    user, sess = await _seed(db_session)
    adapter = await _adapter()
    om = _om(db_session, user, sess, adapter)
    await _open_long(db_session, user, sess, 10)
    sl = await _rest_exit(db_session, om, adapter, user, sess, tag="sl")
    tp = await _rest_exit(db_session, om, adapter, user, sess, tag="target")

    await om.apply_broker_update(_fill_event(tp, 10, 110.0))
    await db_session.commit()

    await db_session.refresh(sl)
    await db_session.refresh(tp)
    assert tp.status == OrderStatus.FILLED
    assert sl.status == OrderStatus.CANCELLED


# ==========================================================================
# 3. Duplicate fill event (WS + polling) -> processed once.
# ==========================================================================


@pytest.mark.asyncio
async def test_duplicate_fill_event_processed_once(db_session):
    user, sess = await _seed(db_session)
    adapter = await _adapter()
    om = _om(db_session, user, sess, adapter)
    await _open_long(db_session, user, sess, 10)
    sl = await _rest_exit(db_session, om, adapter, user, sess, tag="sl")
    tp = await _rest_exit(db_session, om, adapter, user, sess, tag="target")

    evt = _fill_event(sl, 10, 95.0)
    r1 = await om.apply_broker_update(evt)      # WS
    await db_session.commit()
    r2 = await om.apply_broker_update(evt)      # polling — same event
    await db_session.commit()

    await db_session.refresh(sl)
    assert r1 is not None and r2 is None        # no second fill applied
    assert float(sl.filled_quantity) == 10      # not double-counted
    # exactly one exit executed → net flat, target cancelled once
    assert float((await PortfolioManager(db_session).get_position(sess.id, "A", "NSE")).net_quantity) == 0
    trades = (await db_session.execute(
        select(PaperOrder).where(PaperOrder.engine_session_id == sess.id)
    )).scalars().all()
    assert sum(1 for o in trades if o.status == OrderStatus.FILLED) == 1


# ==========================================================================
# 4. Strategy-level SL + broker-side SL -> second exit suppressed.
# ==========================================================================


@pytest.mark.asyncio
async def test_strategy_exit_suppressed_when_broker_oco_resting(db_session):
    user, sess = await _seed(db_session)
    adapter = await _adapter()
    om = _om(db_session, user, sess, adapter)
    await _open_long(db_session, user, sess, 10)
    await _rest_exit(db_session, om, adapter, user, sess, tag="sl")
    await _rest_exit(db_session, om, adapter, user, sess, tag="target")

    with pytest.raises(InvalidOrderError) as exc:
        await om.place(OrderIntentRequest(
            symbol="A", exchange="NSE", side=OrderSide.SELL,
            quantity=10, order_type=OrderType.MARKET,
        ))
    assert exc.value.code == "duplicate_exit_suppressed"


# ==========================================================================
# 5. Repeated exit signal -> idempotent (suppressed).
# ==========================================================================


@pytest.mark.asyncio
async def test_repeated_exit_signal_is_idempotent(db_session):
    user, sess = await _seed(db_session)
    adapter = await _adapter()
    om = _om(db_session, user, sess, adapter)
    await _open_long(db_session, user, sess, 10)
    # First exit already resting (a non-OCO strategy exit).
    first = PaperOrder(
        user_id=user.id, engine_session_id=sess.id, symbol="A", exchange="NSE",
        side=OrderSide.SELL, order_type=OrderType.MARKET, product=OrderProduct.MIS,
        quantity=10, status=OrderStatus.OPEN, tag="orb_exit_stop_loss",
        strategy_name="orb", placed_at=_now(),
    )
    db_session.add(first)
    await db_session.commit()

    with pytest.raises(InvalidOrderError) as exc:
        await om.place(OrderIntentRequest(
            symbol="A", exchange="NSE", side=OrderSide.SELL,
            quantity=10, order_type=OrderType.MARKET, tag="orb_exit_stop_loss",
        ))
    assert exc.value.code == "duplicate_exit_suppressed"


# ==========================================================================
# 6. First exit with nothing in-flight -> allowed (guard helpers).
# ==========================================================================


@pytest.mark.asyncio
async def test_first_exit_not_suppressed(db_session):
    user, sess = await _seed(db_session)
    adapter = await _adapter()
    om = _om(db_session, user, sess, adapter)
    await _open_long(db_session, user, sess, 10)

    intent = OrderIntentRequest(
        symbol="A", exchange="NSE", side=OrderSide.SELL,
        quantity=10, order_type=OrderType.MARKET,
    )
    closing, net_abs = await om._closing_intent(intent)
    assert closing is True and net_abs == 10
    assert await om._inflight_exit_quantity(intent) == 0  # nothing covers it yet


# ==========================================================================
# 7. Partial SL fill -> sibling resized to remaining quantity.
# ==========================================================================


@pytest.mark.asyncio
async def test_partial_sl_fill_resizes_sibling(db_session):
    user, sess = await _seed(db_session)
    adapter = await _adapter()
    om = _om(db_session, user, sess, adapter)
    await _open_long(db_session, user, sess, 10)
    sl = await _rest_exit(db_session, om, adapter, user, sess, tag="sl")
    tp = await _rest_exit(db_session, om, adapter, user, sess, tag="target")

    await om.apply_broker_update(_fill_event(sl, 4, 95.0, status=BrokerOrderStatus.PARTIALLY_FILLED))
    await db_session.commit()

    await db_session.refresh(sl)
    await db_session.refresh(tp)
    assert sl.status == OrderStatus.PARTIALLY_FILLED
    pos = await PortfolioManager(db_session).get_position(sess.id, "A", "NSE")
    assert float(pos.net_quantity) == 6            # 10 - 4
    assert tp.status == OrderStatus.OPEN
    assert float(tp.quantity) == 6                 # sibling shrunk to remaining


# ==========================================================================
# 8. Broker rejects sibling cancellation -> NOT falsely marked safe.
# ==========================================================================


class _CancelRejectingBroker(MockLiveBroker):
    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        raise BrokerError("cancel refused")


@pytest.mark.asyncio
async def test_sibling_cancel_failure_not_falsely_safe(db_session):
    user, sess = await _seed(db_session)
    adapter = _CancelRejectingBroker({"auto_fill": False})
    await adapter.start()
    om = _om(db_session, user, sess, adapter)
    await _open_long(db_session, user, sess, 10)
    sl = await _rest_exit(db_session, om, adapter, user, sess, tag="sl")
    tp = await _rest_exit(db_session, om, adapter, user, sess, tag="target")

    report = await om.apply_broker_update(_fill_event(sl, 10, 95.0))
    await db_session.commit()

    await db_session.refresh(tp)
    assert report is not None
    assert report.sibling_cancel_failed is True     # flagged for reconciliation
    assert tp.status == OrderStatus.OPEN             # NOT falsely cancelled


# ==========================================================================
# 9. Kill switch while OCO orders exist -> safe flatten, no duplicate exit.
# ==========================================================================


def _inject_open_broker_order(adapter, oid, side, qty, *, symbol="A", exchange="NSE", product="mis"):
    adapter._orders[oid] = {
        "broker_order_id": oid, "client_order_id": None, "symbol": symbol,
        "exchange": exchange, "side": side, "order_type": "limit",
        "product": product, "quantity": float(qty), "price": None,
        "trigger_price": None, "tag": None, "status": BrokerOrderStatus.OPEN,
        "filled_quantity": 0.0, "average_fill_price": None,
        "rejection_reason": None, "placed_at": _now(),
    }


@pytest.mark.asyncio
async def test_kill_switch_with_oco_orders_no_duplicate_exit(db_session):
    user, sess = await _seed(db_session)
    adapter = MockLiveBroker({"auto_fill": True, "simulated_ltp": {"A": 100.0}})
    await adapter.start()
    # Real broker position + two resting OCO legs at the broker.
    adapter._positions[("A", "NSE", "mis")] = BrokerPositionSnapshot(
        "A", "NSE", "mis", 10, 100.0, last_price=100.0
    )
    _inject_open_broker_order(adapter, "BO-SL", "sell", 10)
    _inject_open_broker_order(adapter, "BO-TP", "sell", 10)

    res = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=sess.id, user_id=user.id,
        broker_account_id=ACCOUNT_ID,
    )
    await db_session.commit()

    assert res.status == "flat"
    assert res.verified_flat is True
    # Both OCO legs were cancelled (never left resting → no duplicate exit).
    assert "BO-SL" in res.cancelled_order_ids and "BO-TP" in res.cancelled_order_ids
    # Exactly ONE kill-switch closing order was created.
    ks = (await db_session.execute(
        select(PaperOrder).where(
            PaperOrder.engine_session_id == sess.id,
            PaperOrder.tag == KILL_SWITCH_TAG,
        )
    )).scalars().all()
    assert len(ks) == 1


# ==========================================================================
# 10. Recovery with existing SL/target orders -> no duplicate orders.
# ==========================================================================


@pytest_asyncio.fixture
async def factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def rec_manager(factory):
    m = StrategyManager(session_factory=factory)
    yield m
    await m.shutdown()


@pytest.mark.asyncio
async def test_recovery_with_existing_oco_no_duplicate(factory, rec_manager):
    async with factory() as db:
        user, sess = await _seed(db, strategy="demo_ma_cross")
    async with factory() as db:
        # Fresh OrderManager on a live executor bound to a stateful adapter.
        adapter = MockLiveBroker({"auto_fill": False})
        await adapter.start()
        adapter._positions[("A", "NSE", "mis")] = BrokerPositionSnapshot(
            "A", "NSE", "mis", 10, 100.0, last_price=100.0
        )
        om = _om(db, user, sess, adapter)
        await PortfolioManager(db).apply_fill(
            user_id=user.id, engine_session_id=sess.id, symbol="A",
            exchange="NSE", product=OrderProduct.MIS,
            side=OrderSide.BUY, quantity=10, price=100.0,
        )
        await db.commit()
        sl = await _rest_exit(db, om, adapter, user, sess, tag="sl")
        tp = await _rest_exit(db, om, adapter, user, sess, tag="target")

    broker_orders_before = len(await adapter.list_orders())
    async with factory() as db:
        orb_orders_before = len((await db.execute(
            select(PaperOrder).where(PaperOrder.engine_session_id == sess.id)
        )).scalars().all())

    svc = LiveRecoveryService(session_factory=factory, manager=rec_manager)
    res = await svc.recover_session(sess.id, adapter=adapter, provider=MockMarketDataProvider(tick_interval_ms=60_000))

    assert res.action == "resumed"
    assert rec_manager.is_running(sess.id)
    # Existing SL/target recognised, not duplicated.
    assert len(await adapter.list_orders()) == broker_orders_before
    async with factory() as db:
        orb_orders_after = len((await db.execute(
            select(PaperOrder).where(PaperOrder.engine_session_id == sess.id)
        )).scalars().all())
    assert orb_orders_after == orb_orders_before
