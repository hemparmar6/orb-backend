"""Analytics API tests (Module 8)."""
from __future__ import annotations

import pytest

from tests._module8_helpers import register_and_login, seed_engine_data


@pytest.mark.asyncio
async def test_analytics_summary(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)

    r = await client.get("/api/v1/analytics/summary", headers=headers)
    assert r.status_code == 200
    body = r.json()
    # 2 closed trades: +100 win, -40 loss
    assert body["total_trades"] == 2
    assert body["winning_trades"] == 1
    assert body["losing_trades"] == 1
    assert body["win_rate"] == pytest.approx(0.5, abs=1e-6)
    assert body["gross_profit"] == pytest.approx(100.0, abs=1e-6)
    assert body["gross_loss"] == pytest.approx(-40.0, abs=1e-6)
    assert body["net_pnl"] == pytest.approx(60.0, abs=1e-6)
    assert body["profit_factor"] == pytest.approx(2.5, abs=1e-6)
    assert body["average_rr"] == pytest.approx(2.5, abs=1e-6)


@pytest.mark.asyncio
async def test_analytics_equity_curve_and_drawdown(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    eq = await client.get("/api/v1/analytics/equity-curve", headers=headers)
    assert eq.status_code == 200
    assert len(eq.json()) >= 2
    dd = await client.get("/api/v1/analytics/drawdown", headers=headers)
    assert dd.status_code == 200
    # After +100 then -40, drawdown should be -40 from peak 100
    dds = dd.json()
    assert dds[-1]["drawdown"] <= 0


@pytest.mark.asyncio
async def test_analytics_journal(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/analytics/journal?page_size=10", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5  # 5 trades total
    assert len(body["items"]) == 5


@pytest.mark.asyncio
async def test_analytics_monthly(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/analytics/monthly", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["trades"] == 5


@pytest.mark.asyncio
async def test_analytics_requires_auth(client):
    r = await client.get("/api/v1/analytics/summary")
    assert r.status_code == 401
