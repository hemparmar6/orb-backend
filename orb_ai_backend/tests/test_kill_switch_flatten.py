"""LIVE kill switch must genuinely flatten live positions.

Covers the Task-2 requirements against ``EmergencyFlattenService`` (the muscle
behind the kill switch) plus one end-to-end test through the kill-switch API +
``StrategyManager.emergency_flatten_all``:

- LIVE long position   → opposite-side (SELL) close submitted, verified flat.
- LIVE short position  → opposite-side (BUY) close submitted, verified flat.
- LIVE zero position   → no order generated.
- Pending live order   → cancellation attempted.
- Called twice         → no duplicate flattening order (idempotent).
- Broker rejection     → reports failed/partial, never "flat".
- Partial fill         → not reported flat until the broker confirms.
- Broker already flat  → succeeds without creating an order.
- End-to-end via API   → running live session is flattened + status persisted.
"""
from __future__ import annotations

import uuid

import pytest

from app.brokers.base import (
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
)
from app.brokers.mock_live import MockLiveBroker
from app.models.engine import OrderSide, OrderStatus, PaperOrder
from app.services.emergency_flatten_service import (
    KILL_SWITCH_TAG,
    EmergencyFlattenService,
)
from sqlalchemy import select


SESSION_ID = "flatten-sess-1"
USER_ID = "flatten-user-1"


def _seed_position(adapter: MockLiveBroker, symbol: str, net: float,
                   *, exchange: str = "NSE", product: str = "mis",
                   price: float = 100.0) -> None:
    adapter._positions[(symbol, exchange, product)] = BrokerPositionSnapshot(
        symbol=symbol, exchange=exchange, product=product,
        net_quantity=net, average_price=price, last_price=price,
    )


async def _kill_switch_orders(db, session_id=SESSION_ID) -> list[PaperOrder]:
    stmt = select(PaperOrder).where(
        PaperOrder.engine_session_id == session_id,
        PaperOrder.tag == KILL_SWITCH_TAG,
    )
    return list((await db.execute(stmt)).scalars().all())


# ---- fake brokers for rejection / partial-fill --------------------------


class RejectingBroker(MockLiveBroker):
    """place_order always rejects — position is never reduced."""

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderResult:
        return BrokerOrderResult(
            broker_order_id=f"REJ-{uuid.uuid4().hex[:8]}",
            status=BrokerOrderStatus.REJECTED,
            rejection_reason="insufficient margin",
            client_order_id=req.client_order_id,
        )


class PartialBroker(MockLiveBroker):
    """Closing order only fills half — position stays non-zero."""

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderResult:
        key = (req.symbol, req.exchange, req.product)
        pos = self._positions.get(key)
        if pos is not None:
            signed = req.quantity if req.side == "buy" else -req.quantity
            pos.net_quantity = round(pos.net_quantity + signed / 2.0, 4)
        return BrokerOrderResult(
            broker_order_id=f"PF-{uuid.uuid4().hex[:8]}",
            status=BrokerOrderStatus.PARTIALLY_FILLED,
            filled_quantity=req.quantity / 2.0,
            average_fill_price=100.0,
            client_order_id=req.client_order_id,
        )


# ---- 1. long position → SELL close, verified flat -----------------------


@pytest.mark.asyncio
async def test_long_position_is_flattened_with_sell(db_session):
    adapter = MockLiveBroker({"auto_fill": True, "simulated_ltp": {"A": 100.0}})
    await adapter.start()
    _seed_position(adapter, "A", 10)

    res = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()

    assert res.status == "flat"
    assert res.verified_flat is True
    assert len(res.symbols) == 1
    s = res.symbols[0]
    assert s.action == "closed"
    assert s.side == "sell"
    assert s.quantity == 10
    orders = await _kill_switch_orders(db_session)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.SELL


# ---- 2. short position → BUY close --------------------------------------


@pytest.mark.asyncio
async def test_short_position_is_flattened_with_buy(db_session):
    adapter = MockLiveBroker({"auto_fill": True, "simulated_ltp": {"B": 50.0}})
    await adapter.start()
    _seed_position(adapter, "B", -8)

    res = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()

    assert res.status == "flat"
    assert res.verified_flat is True
    s = res.symbols[0]
    assert s.side == "buy"
    assert s.quantity == 8
    orders = await _kill_switch_orders(db_session)
    assert len(orders) == 1
    assert orders[0].side == OrderSide.BUY


# ---- 3. zero position → no order ----------------------------------------


@pytest.mark.asyncio
async def test_zero_position_creates_no_order(db_session):
    adapter = MockLiveBroker({"auto_fill": True})
    await adapter.start()

    res = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()

    assert res.status == "flat"
    assert res.verified_flat is True
    assert res.symbols == []
    assert await _kill_switch_orders(db_session) == []


# ---- 4. pending live order → cancellation attempted ---------------------


@pytest.mark.asyncio
async def test_pending_order_is_cancelled(db_session):
    adapter = MockLiveBroker({"auto_fill": False})
    await adapter.start()
    # A resting strategy order at the broker (no position, stays OPEN).
    placed = await adapter.place_order(BrokerOrderRequest(
        client_order_id="strat-order-1", symbol="A", exchange="NSE",
        side="buy", order_type="limit", product="mis", quantity=5, price=99.0,
    ))

    res = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()

    assert placed.broker_order_id in res.cancelled_order_ids
    # Broker confirms the order is cancelled.
    after = await adapter.get_order(placed.broker_order_id)
    assert after.status == BrokerOrderStatus.CANCELLED
    # No position existed → verified flat.
    assert res.verified_flat is True


# ---- 5. called twice → no duplicate closing order -----------------------


@pytest.mark.asyncio
async def test_idempotent_no_duplicate_closing_order(db_session):
    # auto_fill disabled so the first closing order stays pending and the
    # position remains open between the two activations.
    adapter = MockLiveBroker({"auto_fill": False})
    await adapter.start()
    _seed_position(adapter, "A", 5)

    svc = EmergencyFlattenService(db_session)
    res1 = await svc.flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()
    # Not flat yet (closing order pending, not filled).
    assert res1.verified_flat is False
    assert res1.status == "partial"

    res2 = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()
    # Second run must NOT create a duplicate closing order.
    assert res2.symbols[0].action == "skipped_existing"
    orders = await _kill_switch_orders(db_session)
    assert len(orders) == 1


# ---- 6. broker rejection → failed/partial, never flat -------------------


@pytest.mark.asyncio
async def test_broker_rejection_reports_failure(db_session):
    adapter = RejectingBroker({"auto_fill": False})
    await adapter.start()
    _seed_position(adapter, "A", 5)

    res = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()

    assert res.status != "flat"
    assert res.verified_flat is False
    assert res.symbols[0].action == "close_failed"
    assert res.symbols[0].error
    # position still open at broker
    assert res.remaining_positions


# ---- 7. partial fill → not reported flat until verified -----------------


@pytest.mark.asyncio
async def test_partial_fill_not_reported_flat(db_session):
    adapter = PartialBroker({"auto_fill": False})
    await adapter.start()
    _seed_position(adapter, "A", 10)

    res = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()

    assert res.status != "flat"
    assert res.verified_flat is False
    # broker still reports a residual position
    assert any(abs(p["net_quantity"]) > 0 for p in res.remaining_positions)


# ---- 8. broker already flat → success, no order -------------------------


@pytest.mark.asyncio
async def test_already_flat_succeeds_without_order(db_session):
    adapter = MockLiveBroker({"auto_fill": True})
    await adapter.start()
    _seed_position(adapter, "A", 0)  # explicitly zero net

    res = await EmergencyFlattenService(db_session).flatten(
        adapter=adapter, engine_session_id=SESSION_ID, user_id=USER_ID,
    )
    await db_session.commit()

    assert res.status == "flat"
    assert res.verified_flat is True
    assert res.symbols[0].action == "already_flat"
    assert await _kill_switch_orders(db_session) == []


# ---- 9. manager ignores paper / no live sessions ------------------------


@pytest.mark.asyncio
async def test_manager_flatten_noop_without_live_sessions():
    from app.engine.strategy.manager import manager as strategy_manager
    # No runners registered → nothing to flatten (paper carries no exposure).
    results = await strategy_manager.emergency_flatten_all(reason="test")
    assert results == []


# ---- 10. end-to-end: kill-switch API flattens a running live session ----


REG = {"email": "kill-trader@example.com", "password": "very-secret-1",
       "full_name": "Kill Trader"}


async def _login(client) -> str:
    await client.post("/api/v1/auth/register", json=REG)
    r = await client.post("/api/v1/auth/login",
                          json={"email": REG["email"], "password": REG["password"]})
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_kill_switch_endpoint_flattens_live_session(client, admin_headers):
    from app.engine.strategy.manager import manager as strategy_manager

    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    # Global master switch must be LIVE before a live session can start.
    mode = await client.post(
        "/api/v1/trading/mode",
        headers=admin_headers,
        json={"mode": "live",
              "confirmation": "I UNDERSTAND THIS CAN PLACE REAL-MONEY ORDERS."},
    )
    assert mode.status_code == 200, mode.text

    conn = await client.post("/api/v1/brokers/connect", headers=hdr, json={
        "broker_type": "mock_live", "alias": "kill-1",
        "credentials": {"initial_funds": 200000, "auto_fill": True,
                        "simulated_ltp": {"A": 100.0}},
    })
    assert conn.status_code == 201, conn.text
    broker_account_id = conn.json()["id"]

    start = await client.post("/api/v1/trading/start", headers=hdr, json={
        "strategy_name": "demo_ma_cross", "symbols": ["A"],
        "params": {"fast": 2, "slow": 4, "quantity": 5},
        "initial_capital": 200000,
        "risk_config": {"max_position_size": 100, "max_risk_per_trade_pct": 100},
        "execution_mode": "live", "broker_account_id": broker_account_id,
    })
    assert start.status_code == 200, start.text
    session_id = start.json()["id"]

    # Seed a real broker LONG position on the session's live adapter.
    handle = strategy_manager._runners.get(session_id)
    assert handle is not None and handle.broker_adapter is not None
    _seed_position(handle.broker_adapter, "A", 7)

    # Admin activates the kill switch → should flatten the live position.
    ks = await client.post("/api/v1/execution-safety/kill-switch",
                           headers=admin_headers,
                           json={"active": True, "reason": "emergency"})
    assert ks.status_code == 200, ks.text

    # Broker reports the position flat.
    positions = await handle.broker_adapter.list_positions()
    assert all(abs(float(p.net_quantity)) <= 1e-9 for p in positions), positions

    # A kill-switch closing order (SELL 7) was persisted.
    from app.db.session import async_session_factory
    async with async_session_factory() as db:
        orders = await _kill_switch_orders(db, session_id)
    assert len(orders) == 1, orders
    assert orders[0].side == OrderSide.SELL
    assert abs(float(orders[0].quantity) - 7.0) <= 1e-9
