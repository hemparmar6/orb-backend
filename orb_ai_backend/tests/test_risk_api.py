"""Risk API tests (Module 8)."""
from __future__ import annotations

import pytest

from tests._module8_helpers import register_and_login, seed_engine_data


@pytest.mark.asyncio
async def test_risk_dashboard(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/risk/dashboard", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "exposure_by_symbol" in body
    assert "exposure_by_broker" in body
    assert "daily_risk" in body
    assert "max_drawdown" in body
    assert "margin" in body
    assert "position_sizing" in body


@pytest.mark.asyncio
async def test_risk_exposure_symbol(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/risk/exposure/symbol", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "MSFT"
    # 5 * 210 = 1050
    assert body[0]["long_value"] == pytest.approx(1050.0, abs=1e-6)


@pytest.mark.asyncio
async def test_risk_margin(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/risk/margin", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["capital"] == pytest.approx(100_000.0, abs=1e-6)
    assert body["gross_exposure"] == pytest.approx(1050.0, abs=1e-6)


@pytest.mark.asyncio
async def test_risk_requires_auth(client):
    r = await client.get("/api/v1/risk/dashboard")
    assert r.status_code == 401
