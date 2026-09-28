"""Shared helpers for Module 8 tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderSide,
    PaperOrder,
    PaperPosition,
    PaperTrade,
    OrderProduct,
    OrderStatus,
    OrderType,
)


REG = {
    "email": "trader8@example.com",
    "password": "s3cret-password!",
    "full_name": "Trader Eight",
}


async def register_and_login(client) -> tuple[str, dict[str, str]]:
    await client.post("/api/v1/auth/register", json=REG)
    r = await client.post("/api/v1/auth/login", json={
        "email": REG["email"], "password": REG["password"]
    })
    assert r.status_code == 200, r.text
    tokens = r.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = await client.get("/api/v1/users/me", headers=headers)
    return me.json()["id"], headers


async def seed_engine_data(db: AsyncSession, user_id: str) -> str:
    """Create one engine session with 3 paper trades and one open position."""
    now = datetime.now(timezone.utc)
    sess = EngineSession(
        user_id=user_id,
        strategy_name="test_strat",
        status=EngineSessionStatus.RUNNING,
        execution_mode=ExecutionMode.PAPER,
        symbols=["AAPL", "MSFT"],
        params={}, risk_config={},
        initial_capital=100_000,
    )
    db.add(sess)
    await db.flush()

    # Trade 1: BUY AAPL 10 @ 100 (opens long)
    order1 = PaperOrder(
        user_id=user_id, engine_session_id=sess.id,
        symbol="AAPL", side=OrderSide.BUY,
        order_type=OrderType.MARKET, product=OrderProduct.MIS,
        quantity=10, filled_quantity=10, average_fill_price=100,
        status=OrderStatus.FILLED, strategy_name="test_strat",
    )
    db.add(order1)
    await db.flush()

    t1 = PaperTrade(
        user_id=user_id, engine_session_id=sess.id, paper_order_id=order1.id,
        symbol="AAPL", side=OrderSide.BUY, quantity=10, price=100,
        realized_pnl_delta=0, strategy_name="test_strat", executed_at=now,
    )
    db.add(t1)
    await db.flush()

    # Trade 2: SELL AAPL 10 @ 110 (closes with +100 profit)
    order2 = PaperOrder(
        user_id=user_id, engine_session_id=sess.id,
        symbol="AAPL", side=OrderSide.SELL,
        order_type=OrderType.MARKET, product=OrderProduct.MIS,
        quantity=10, filled_quantity=10, average_fill_price=110,
        status=OrderStatus.FILLED, strategy_name="test_strat",
    )
    db.add(order2)
    await db.flush()
    t2 = PaperTrade(
        user_id=user_id, engine_session_id=sess.id, paper_order_id=order2.id,
        symbol="AAPL", side=OrderSide.SELL, quantity=10, price=110,
        realized_pnl_delta=100, strategy_name="test_strat",
        executed_at=now,
    )
    db.add(t2)

    # Trade 3: BUY MSFT 5 @ 200 (open long, no realized yet)
    order3 = PaperOrder(
        user_id=user_id, engine_session_id=sess.id,
        symbol="MSFT", side=OrderSide.BUY,
        order_type=OrderType.MARKET, product=OrderProduct.MIS,
        quantity=5, filled_quantity=5, average_fill_price=200,
        status=OrderStatus.FILLED, strategy_name="test_strat",
    )
    db.add(order3)
    await db.flush()
    t3 = PaperTrade(
        user_id=user_id, engine_session_id=sess.id, paper_order_id=order3.id,
        symbol="MSFT", side=OrderSide.BUY, quantity=5, price=200,
        realized_pnl_delta=0, strategy_name="test_strat", executed_at=now,
    )
    db.add(t3)

    # A losing trade to exercise loss metrics: BUY/SELL TSLA
    order4 = PaperOrder(
        user_id=user_id, engine_session_id=sess.id,
        symbol="TSLA", side=OrderSide.BUY, order_type=OrderType.MARKET,
        product=OrderProduct.MIS, quantity=2, filled_quantity=2,
        average_fill_price=300, status=OrderStatus.FILLED,
        strategy_name="test_strat",
    )
    db.add(order4)
    await db.flush()
    t4 = PaperTrade(
        user_id=user_id, engine_session_id=sess.id, paper_order_id=order4.id,
        symbol="TSLA", side=OrderSide.BUY, quantity=2, price=300,
        realized_pnl_delta=0, strategy_name="test_strat", executed_at=now,
    )
    db.add(t4)
    order5 = PaperOrder(
        user_id=user_id, engine_session_id=sess.id,
        symbol="TSLA", side=OrderSide.SELL, order_type=OrderType.MARKET,
        product=OrderProduct.MIS, quantity=2, filled_quantity=2,
        average_fill_price=280, status=OrderStatus.FILLED,
        strategy_name="test_strat",
    )
    db.add(order5)
    await db.flush()
    t5 = PaperTrade(
        user_id=user_id, engine_session_id=sess.id, paper_order_id=order5.id,
        symbol="TSLA", side=OrderSide.SELL, quantity=2, price=280,
        realized_pnl_delta=-40, strategy_name="test_strat", executed_at=now,
    )
    db.add(t5)

    # Open position: MSFT 5 @ 200 (last_price 210 => +50 unrealized)
    pos = PaperPosition(
        user_id=user_id, engine_session_id=sess.id, symbol="MSFT",
        exchange="MOCK", product=OrderProduct.MIS,
        net_quantity=5, average_price=200, realized_pnl=0, last_price=210,
    )
    db.add(pos)

    # Closed position (AAPL) — net 0, realized 100
    pos_closed = PaperPosition(
        user_id=user_id, engine_session_id=sess.id, symbol="AAPL",
        exchange="MOCK", product=OrderProduct.MIS,
        net_quantity=0, average_price=100, realized_pnl=100, last_price=110,
    )
    db.add(pos_closed)

    await db.commit()
    return sess.id
