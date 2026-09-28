"""OrderManager + PaperExecutor + PortfolioManager integration tests.

Uses the actual DB session fixture from conftest (sqlite in-memory).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.security import hash_password
from app.engine.logger import TradeLogger
from app.engine.market_data.base import Quote
from app.engine.orders.manager import OrderIntentRequest, OrderManager
from app.engine.portfolio.manager import PortfolioManager
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    OrderProduct,
    OrderSide,
    OrderStatus,
    OrderType,
)
from app.models.user import User


async def _seed_user_and_session(db):
    user = User(
        email="engine@example.com",
        hashed_password=hash_password("password123"),
        full_name="Engine User",
    )
    db.add(user)
    await db.flush()
    sess = EngineSession(
        user_id=user.id,
        strategy_name="unit",
        status=EngineSessionStatus.RUNNING,
        symbols=["A"],
        params={},
        risk_config={},
        initial_capital=100_000,
        started_at=datetime.now(timezone.utc),
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return user, sess


def _quote(symbol: str, price: float) -> Quote:
    return Quote(symbol=symbol, exchange="MOCK", price=price, volume=1.0, ts=datetime.now(timezone.utc))


def _mgrs(db, user, sess):
    portfolio = PortfolioManager(db)
    logger_ = TradeLogger(db)
    return OrderManager(
        db,
        engine_session_id=sess.id,
        user_id=user.id,
        portfolio=portfolio,
        trade_logger=logger_,
    ), portfolio


@pytest.mark.asyncio
async def test_market_order_fills_immediately_and_updates_position(db_session):
    user, sess = await _seed_user_and_session(db_session)
    om, port = _mgrs(db_session, user, sess)

    order = await om.place(
        OrderIntentRequest(symbol="A", side=OrderSide.BUY, quantity=10, order_type=OrderType.MARKET)
    )
    await db_session.commit()

    reports = await om.on_quote(_quote("A", 100.0))
    await db_session.commit()
    await db_session.refresh(order)
    assert order.status == OrderStatus.FILLED
    assert float(order.average_fill_price) == 100.0
    assert len(reports) == 1

    pos = await port.get_position(sess.id, "A")
    assert pos is not None
    assert float(pos.net_quantity) == 10
    assert float(pos.average_price) == 100.0


@pytest.mark.asyncio
async def test_limit_order_fills_only_when_price_crosses(db_session):
    user, sess = await _seed_user_and_session(db_session)
    om, _ = _mgrs(db_session, user, sess)

    order = await om.place(
        OrderIntentRequest(
            symbol="A", side=OrderSide.BUY, quantity=5,
            order_type=OrderType.LIMIT, price=99.0,
        )
    )
    await db_session.commit()

    # LTP above limit — no fill
    reports = await om.on_quote(_quote("A", 100.0))
    assert reports == []
    await db_session.refresh(order)
    assert order.status == OrderStatus.OPEN

    # LTP at/below limit — fills
    reports = await om.on_quote(_quote("A", 98.5))
    await db_session.commit()
    await db_session.refresh(order)
    assert order.status == OrderStatus.FILLED
    assert len(reports) == 1


@pytest.mark.asyncio
async def test_cancel_open_order(db_session):
    user, sess = await _seed_user_and_session(db_session)
    om, _ = _mgrs(db_session, user, sess)

    order = await om.place(
        OrderIntentRequest(symbol="A", side=OrderSide.BUY, quantity=1,
                           order_type=OrderType.LIMIT, price=1.0)
    )
    await db_session.commit()
    await om.cancel(order.id)
    await db_session.commit()
    await db_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED
    # Subsequent quote must not resurrect it.
    reports = await om.on_quote(_quote("A", 0.5))
    assert reports == []


@pytest.mark.asyncio
async def test_sl_m_triggers_and_creates_oco_children(db_session):
    user, sess = await _seed_user_and_session(db_session)
    om, port = _mgrs(db_session, user, sess)

    # Buy MARKET with SL + TP so OCO children auto-spawn on fill.
    entry = await om.place(
        OrderIntentRequest(
            symbol="A", side=OrderSide.BUY, quantity=10,
            order_type=OrderType.MARKET,
            stop_loss=95.0,
            target_price=110.0,
        )
    )
    await db_session.commit()
    await om.on_quote(_quote("A", 100.0))
    await db_session.commit()
    await db_session.refresh(entry)
    assert entry.status == OrderStatus.FILLED

    children = await om.list_orders()
    tagged = [o for o in children if o.tag in ("sl", "target")]
    assert len(tagged) == 2

    # Price hits TP → target fills, SL should be auto-cancelled.
    await om.on_quote(_quote("A", 111.0))
    await db_session.commit()

    tgt = [o for o in await om.list_orders() if o.tag == "target"][0]
    sl = [o for o in await om.list_orders() if o.tag == "sl"][0]
    assert tgt.status == OrderStatus.FILLED
    assert sl.status == OrderStatus.CANCELLED

    pos = await port.get_position(sess.id, "A")
    assert float(pos.net_quantity) == 0  # fully flat after TP


@pytest.mark.asyncio
async def test_position_averaging_and_reduce_realized_pnl(db_session):
    user, sess = await _seed_user_and_session(db_session)
    om, port = _mgrs(db_session, user, sess)

    # Two buys, weighted average.
    for price in (100.0, 110.0):
        o = await om.place(OrderIntentRequest(symbol="A", side=OrderSide.BUY, quantity=10, order_type=OrderType.MARKET))
        await db_session.commit()
        await om.on_quote(_quote("A", price))
        await db_session.commit()

    pos = await port.get_position(sess.id, "A")
    assert float(pos.net_quantity) == 20
    assert float(pos.average_price) == 105.0

    # Sell half at 120 → realized = (120-105)*10 = 150
    await om.place(OrderIntentRequest(symbol="A", side=OrderSide.SELL, quantity=10, order_type=OrderType.MARKET))
    await db_session.commit()
    await om.on_quote(_quote("A", 120.0))
    await db_session.commit()

    pos = await port.get_position(sess.id, "A")
    assert float(pos.net_quantity) == 10
    assert float(pos.realized_pnl) == 150.0

    snap = await port.pnl_snapshot(sess.id, last_prices={"A": 120.0})
    # unrealized on remaining 10 @ avg 105 vs LTP 120 = 150
    assert snap.realized == 150.0
    assert snap.unrealized == 150.0
    assert snap.winning_trades >= 1
