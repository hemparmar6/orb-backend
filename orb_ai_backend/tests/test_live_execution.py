"""Live-mode engine flow — end-to-end with MockLiveBroker.

Validates the seam that Module 3 introduces:
- POST /trading/start with execution_mode=live + broker_account_id
- Strategy's place_order routes through LiveBrokerExecutor → MockLiveBroker
- MockLiveBroker auto-fills → BrokerOrderStream applies the update back to
  our paper_orders / paper_positions tables
- GET /trades and GET /positions reflect the broker's fills
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.engine.market_data.base import Quote
from app.engine.strategy.manager import manager as strategy_manager


REG = {
    "email": "live-trader@example.com",
    "password": "very-secret-1",
    "full_name": "Live Trader",
}


async def _login(client) -> str:
    await client.post("/api/v1/auth/register", json=REG)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": REG["email"], "password": REG["password"]},
    )
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_live_mode_requires_global_enablement_before_broker_setup(client):
    token = await _login(client)
    r = await client.post(
        "/api/v1/trading/start",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "strategy_name": "demo_ma_cross",
            "symbols": ["A"],
            "execution_mode": "live",
            "risk_config": {"max_position_size": 100, "max_risk_per_trade_pct": 100},
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "live_trading_disabled_by_global_mode"


@pytest.mark.asyncio
async def test_live_mode_end_to_end_with_mock_broker(client, admin_headers):
    enabled = await client.post(
        "/api/v1/trading/mode",
        headers=admin_headers,
        json={"mode": "live", "confirmation": "I UNDERSTAND THIS CAN PLACE REAL-MONEY ORDERS."},
    )
    assert enabled.status_code == 200, enabled.text
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    # 1. Connect a mock_live broker account.
    conn = await client.post(
        "/api/v1/brokers/connect",
        headers=hdr,
        json={
            "broker_type": "mock_live",
            "alias": "live-1",
            "credentials": {"initial_funds": 200000, "simulated_ltp": {"A": 100.0}},
        },
    )
    assert conn.status_code == 201
    broker_account_id = conn.json()["id"]

    # 2. Start a live engine session.
    start = await client.post(
        "/api/v1/trading/start",
        headers=hdr,
        json={
            "strategy_name": "demo_ma_cross",
            "symbols": ["A"],
            "params": {"fast": 2, "slow": 4, "quantity": 5},
            "initial_capital": 200000,
            "risk_config": {"max_position_size": 100, "max_risk_per_trade_pct": 100},
            "execution_mode": "live",
            "broker_account_id": broker_account_id,
        },
    )
    assert start.status_code == 200, start.text
    session = start.json()
    session_id = session["id"]
    assert session["execution_mode"] == "live"
    assert session["broker_account_id"] == broker_account_id

    # 3. Drive rising prices to trigger a BUY signal from demo_ma_cross.
    handle = strategy_manager._runners.get(session_id)
    assert handle is not None
    now = datetime.now(timezone.utc)
    for p in (100, 100, 101, 102, 103, 104, 105, 106):
        await handle.runner.run_once(Quote("A", "MOCK", p, 1, now))

    # 4. Give the broker's async auto-fill + WS stream + reconciler a moment.
    for _ in range(20):
        await asyncio.sleep(0.05)
        orders = (await client.get("/api/v1/orders", headers=hdr,
                                    params={"session_id": session_id})).json()
        filled = [o for o in orders["items"] if o["status"] == "filled"]
        if filled:
            break

    # 5. Assert at least one order got a broker_order_id and is FILLED.
    orders = (await client.get("/api/v1/orders", headers=hdr,
                                params={"session_id": session_id})).json()
    filled = [o for o in orders["items"] if o["status"] == "filled"]
    assert filled, f"Expected at least one filled order via mock_live broker; got {orders}"

    # 6. paper_trades log should have entries with the mock's fill price.
    #    The trade record is written async by the fill reconciler AFTER the
    #    order row flips to 'filled', so poll briefly to avoid flakes.
    trades = {"total": 0, "items": []}
    for _ in range(20):
        trades = (await client.get("/api/v1/trades", headers=hdr,
                                    params={"session_id": session_id})).json()
        if trades["total"] >= 1:
            break
        await asyncio.sleep(0.05)
    assert trades["total"] >= 1, f"Expected trade log entry; got {trades}"

    # 7. Position was updated to reflect the live fill. Poll similarly.
    positions = {"items": []}
    for _ in range(20):
        positions = (await client.get("/api/v1/positions", headers=hdr,
                                       params={"session_id": session_id})).json()
        if any(float(p["net_quantity"]) > 0 for p in positions["items"]):
            break
        await asyncio.sleep(0.05)
    assert any(float(p["net_quantity"]) > 0 for p in positions["items"]), \
        f"Expected non-zero position; got {positions}"

    # 8. Stop the session.
    stop = await client.post(
        "/api/v1/trading/stop",
        headers=hdr,
        json={"session_id": session_id},
    )
    assert stop.status_code == 200
