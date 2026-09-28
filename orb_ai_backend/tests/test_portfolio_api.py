"""Portfolio API tests (Module 8)."""
from __future__ import annotations

import pytest

from tests._module8_helpers import register_and_login, seed_engine_data


@pytest.mark.asyncio
async def test_portfolio_summary_and_holdings(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)

    r = await client.get("/api/v1/portfolio/summary", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # Realized (from PaperPosition rows): AAPL 100 + MSFT 0 = 100
    assert body["realized_pnl"] == pytest.approx(100.0, abs=1e-6)
    # Unrealized: MSFT (5 * (210-200)) = 50
    assert body["unrealized_pnl"] == pytest.approx(50.0, abs=1e-6)
    assert body["total_pnl"] == pytest.approx(150.0, abs=1e-6)
    assert body["equity"] == pytest.approx(100_150.0, abs=1e-6)
    assert body["open_positions"] == 1

    r2 = await client.get("/api/v1/portfolio/holdings", headers=headers)
    assert r2.status_code == 200
    holdings = r2.json()
    assert len(holdings) == 1
    assert holdings[0]["symbol"] == "MSFT"
    assert holdings[0]["side"] == "long"


@pytest.mark.asyncio
async def test_portfolio_allocation(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/portfolio/allocation", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "MSFT"
    assert body[0]["weight"] == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_portfolio_daily_and_monthly(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    daily = await client.get("/api/v1/portfolio/performance/daily?days=30", headers=headers)
    assert daily.status_code == 200
    assert isinstance(daily.json(), list)
    monthly = await client.get("/api/v1/portfolio/performance/monthly", headers=headers)
    assert monthly.status_code == 200
    assert isinstance(monthly.json(), list)


@pytest.mark.asyncio
async def test_portfolio_requires_auth(client):
    r = await client.get("/api/v1/portfolio/summary")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_portfolio_snapshot(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.post("/api/v1/portfolio/snapshot", headers=headers)
    assert r.status_code == 200
    assert r.json()["equity"] == pytest.approx(100_150.0, abs=1e-6)
