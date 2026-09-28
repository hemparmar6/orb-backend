"""Milestone 9 — Risk Management HTTP API tests."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_and_update_my_limits(client, user_headers):
    r = await client.get("/api/v1/risk-management/me/limits", headers=user_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["live_trading_enabled"] is True
    assert body["force_paper_mode"] is False

    r = await client.put(
        "/api/v1/risk-management/me/limits",
        headers=user_headers,
        json={
            "daily_loss_limit": 2000,
            "max_trades_per_day": 20,
            "max_open_positions": 3,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert float(body["daily_loss_limit"]) == 2000
    assert body["max_trades_per_day"] == 20
    assert body["max_open_positions"] == 3


@pytest.mark.asyncio
async def test_portfolio_snapshot(client, user_headers):
    r = await client.get("/api/v1/risk-management/me/portfolio", headers=user_headers)
    assert r.status_code == 200
    data = r.json()
    # Keys required by the mobile dashboard
    for k in [
        "limits", "exposure_by_symbol", "exposure_by_broker", "daily",
        "drawdown", "margin", "position_sizing", "open_positions",
        "consecutive_losses", "live_trading_enabled", "force_paper_mode",
        "active_breaches",
    ]:
        assert k in data, f"missing {k}"


@pytest.mark.asyncio
async def test_batch_pause_resume(client, user_headers):
    # Create a couple of bots first
    for i in range(2):
        r = await client.post("/api/v1/bots", headers=user_headers, json={
            "name": f"bot_{i}", "strategy_key": "orb_intraday",
            "symbols": ["ACME"], "params": {}, "risk_config": {},
        })
        assert r.status_code in (200, 201, 409)

    # pause-all when no bot is running should return count=0
    r = await client.post("/api/v1/risk-management/me/pause-all-bots",
                          headers=user_headers, json={"reason": "test"})
    assert r.status_code == 200
    assert r.json()["action"] == "pause_all"

    r = await client.post("/api/v1/risk-management/me/resume-all-bots",
                          headers=user_headers)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_disable_and_re_enable_live(client, user_headers):
    r = await client.post(
        "/api/v1/risk-management/me/disable-live-trading",
        headers=user_headers, json={"reason": "test"},
    )
    assert r.status_code == 200
    assert r.json()["live_trading_enabled"] is False

    r = await client.get("/api/v1/risk-management/me/limits", headers=user_headers)
    assert r.json()["live_trading_enabled"] is False

    r = await client.post(
        "/api/v1/risk-management/me/enable-live-trading", headers=user_headers,
    )
    assert r.status_code == 200
    assert r.json()["live_trading_enabled"] is True


@pytest.mark.asyncio
async def test_return_to_paper_and_clear(client, user_headers):
    r = await client.post(
        "/api/v1/risk-management/me/return-to-paper-trading",
        headers=user_headers, json={"reason": "test_paper"},
    )
    assert r.status_code == 200
    assert r.json()["force_paper_mode"] is True

    r = await client.post(
        "/api/v1/risk-management/me/clear-paper-mode-force", headers=user_headers,
    )
    assert r.status_code == 200
    assert r.json()["force_paper_mode"] is False


@pytest.mark.asyncio
async def test_admin_overview_and_breaches(client, admin_headers):
    r = await client.get(
        "/api/v1/risk-management/admin/overview",
        headers=admin_headers, params={"since_minutes": 60},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_breaches"] >= 0
    assert "by_event_type" in body

    r = await client.get(
        "/api/v1/risk-management/admin/breaches",
        headers=admin_headers, params={"limit": 20},
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_admin_upsert_user_limits(client, admin_headers, user_headers):
    # Get user id via /me
    r = await client.get("/api/v1/users/me", headers=user_headers)
    assert r.status_code == 200
    uid = r.json()["id"]

    r = await client.put(
        f"/api/v1/risk-management/admin/limits/{uid}",
        headers=admin_headers,
        json={"max_open_positions": 7},
    )
    assert r.status_code == 200
    assert r.json()["max_open_positions"] == 7


@pytest.mark.asyncio
async def test_non_admin_cannot_access_admin_routes(client, user_headers):
    r = await client.get(
        "/api/v1/risk-management/admin/overview", headers=user_headers,
    )
    assert r.status_code == 403
