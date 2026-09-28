"""End-to-end tests for strategies, trades, and settings resources."""
from __future__ import annotations

import pytest


async def _register_and_login(client) -> str:
    payload = {
        "email": "bob@example.com",
        "password": "very-secret-1",
        "full_name": "Bob Example",
    }
    await client.post("/api/v1/auth/register", json=payload)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_strategy_crud(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Create
    resp = await client.post(
        "/api/v1/strategies",
        headers=headers,
        json={
            "name": "ORB 15-min",
            "description": "Opening range breakout, 15-minute window",
            "parameters": {"window_minutes": 15, "risk_pct": 1.0},
        },
    )
    assert resp.status_code == 201, resp.text
    strat = resp.json()
    assert strat["name"] == "ORB 15-min"
    strat_id = strat["id"]

    # List
    listing = await client.get("/api/v1/strategies", headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == strat_id

    # Update
    upd = await client.patch(
        f"/api/v1/strategies/{strat_id}",
        headers=headers,
        json={"status": "active"},
    )
    assert upd.status_code == 200
    assert upd.json()["status"] == "active"

    # Delete
    dele = await client.delete(f"/api/v1/strategies/{strat_id}", headers=headers)
    assert dele.status_code == 204


@pytest.mark.asyncio
async def test_trade_crud_and_settings(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Read default settings
    s = await client.get("/api/v1/settings/me", headers=headers)
    assert s.status_code == 200
    assert s.json()["timezone"] == "UTC"

    # Upsert settings
    s2 = await client.put(
        "/api/v1/settings/me",
        headers=headers,
        json={"timezone": "Asia/Kolkata", "theme": "dark", "risk_per_trade_pct": 1.5},
    )
    assert s2.status_code == 200
    assert s2.json()["timezone"] == "Asia/Kolkata"
    assert s2.json()["theme"] == "dark"

    # Create trade
    t = await client.post(
        "/api/v1/manual-trades",
        headers=headers,
        json={
            "symbol": "NIFTY",
            "side": "buy",
            "quantity": "50",
            "entry_price": "22000.5",
            "status": "open",
        },
    )
    assert t.status_code == 201, t.text
    trade = t.json()
    trade_id = trade["id"]
    assert trade["symbol"] == "NIFTY"

    # Filter by status
    listing = await client.get(
        "/api/v1/manual-trades",
        headers=headers,
        params={"status": "open"},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    # Update -> close
    upd = await client.patch(
        f"/api/v1/manual-trades/{trade_id}",
        headers=headers,
        json={"status": "closed", "exit_price": "22100", "pnl": "4975"},
    )
    assert upd.status_code == 200
    assert upd.json()["status"] == "closed"

    # Delete
    dele = await client.delete(f"/api/v1/manual-trades/{trade_id}", headers=headers)
    assert dele.status_code == 204
