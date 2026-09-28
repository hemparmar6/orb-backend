"""End-to-end test through the API: start → tick → orders → stop.

Uses ``strategy_manager`` directly for the tick loop (the API's start()
launches a real asyncio task that isn't deterministic under sqlite+asyncio;
so this test starts a session via the API, then drives ticks synchronously
through the runner it just created).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.engine.market_data.base import Quote
from app.engine.strategy.manager import manager as strategy_manager


REG = {
    "email": "trader@example.com",
    "password": "very-secret-1",
    "full_name": "Trader One",
}


async def _login(client) -> str:
    await client.post("/api/v1/auth/register", json=REG)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": REG["email"], "password": REG["password"]},
    )
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_status_endpoint_lists_registered_strategies(client):
    token = await _login(client)
    r = await client.get(
        "/api/v1/trading/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "demo_ma_cross" in body["registered_strategies"]
    assert body["sessions"] == []


@pytest.mark.asyncio
async def test_start_stop_and_full_pnl_flow(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    # ---- start ----
    start = await client.post(
        "/api/v1/trading/start",
        headers=hdr,
        json={
            "strategy_name": "demo_ma_cross",
            "symbols": ["A"],
            "params": {"fast": 2, "slow": 4, "quantity": 5},
            "initial_capital": 100000,
            "risk_config": {
                "max_position_size": 1000,
                "max_risk_per_trade_pct": 100,
            },
        },
    )
    assert start.status_code == 200, start.text
    session = start.json()
    session_id = session["id"]
    assert session["status"] == "running"

    # The API's background task is not deterministic under sqlite. Drive the
    # runner synchronously via run_once() to produce reproducible fills.
    handle = strategy_manager._runners.get(session_id)
    assert handle is not None
    runner = handle.runner

    # Feed rising-then-falling prices to trigger a BUY then a SELL cross.
    now = datetime.now(timezone.utc)
    ticks_up = [Quote("A", "MOCK", p, 1, now) for p in (100, 100, 101, 102, 103, 104, 105)]
    ticks_dn = [Quote("A", "MOCK", p, 1, now) for p in (105, 104, 103, 102, 101, 100, 99, 98)]

    for q in ticks_up + ticks_dn:
        await runner.run_once(q)

    # ---- orders ----
    orders = await client.get("/api/v1/orders", headers=hdr,
                              params={"session_id": session_id})
    assert orders.status_code == 200
    body = orders.json()
    assert body["total"] >= 2  # at least one buy, one sell

    # ---- positions ----
    positions = await client.get("/api/v1/positions", headers=hdr,
                                 params={"session_id": session_id})
    assert positions.status_code == 200
    # After the down-cross, position should be flat.
    flat_positions = [p for p in positions.json()["items"] if float(p["net_quantity"]) == 0]
    assert flat_positions, "Expected the demo strategy to close its long by end of the sequence"

    # ---- trades ----
    trades = await client.get("/api/v1/trades", headers=hdr,
                              params={"session_id": session_id})
    assert trades.status_code == 200
    assert trades.json()["total"] >= 2

    # ---- pnl ----
    pnl = await client.get("/api/v1/pnl", headers=hdr,
                           params={"session_id": session_id})
    assert pnl.status_code == 200
    body = pnl.json()
    assert body["engine_session_id"] == session_id
    assert "realized" in body
    assert "unrealized" in body

    # ---- stop ----
    stop = await client.post("/api/v1/trading/stop", headers=hdr,
                             json={"session_id": session_id})
    assert stop.status_code == 200
    assert "stopped" in stop.json()["message"].lower()

    # Status now reports STOPPED.
    status = await client.get("/api/v1/trading/status", headers=hdr)
    stored = [s for s in status.json()["sessions"] if s["id"] == session_id]
    assert stored and stored[0]["status"] == "stopped"


@pytest.mark.asyncio
async def test_start_unknown_strategy_returns_404(client):
    token = await _login(client)
    r = await client.post(
        "/api/v1/trading/start",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "strategy_name": "does_not_exist",
            "symbols": ["A"],
            "risk_config": {},
        },
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "strategy_not_registered"
