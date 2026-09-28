"""Reports API tests (Module 8)."""
from __future__ import annotations

import pytest

from tests._module8_helpers import register_and_login, seed_engine_data


@pytest.mark.asyncio
async def test_report_preview(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/reports/preview/daily", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["report_type"] == "daily"
    assert "portfolio_summary" in body
    assert "analytics_summary" in body
    assert body["trade_count"] >= 5


@pytest.mark.asyncio
async def test_report_preview_invalid_type(client):
    _, headers = await register_and_login(client)
    r = await client.get("/api/v1/reports/preview/nope", headers=headers)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_generate_pdf(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/reports/generate/daily?format=pdf", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    # PDF magic number
    assert r.content[:4] == b"%PDF"
    assert len(r.content) > 500


@pytest.mark.asyncio
async def test_generate_csv(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    r = await client.get("/api/v1/reports/generate/monthly?format=csv", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8")
    assert "ORB AI Report" in text
    assert "-- Trades --" in text


@pytest.mark.asyncio
async def test_report_history(client, db_session):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    await client.get("/api/v1/reports/generate/daily?format=pdf", headers=headers)
    await client.get("/api/v1/reports/generate/weekly?format=csv", headers=headers)
    r = await client.get("/api/v1/reports/history", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2


@pytest.mark.asyncio
async def test_all_report_types_pdf(client, db_session):
    """Every supported report_type must generate a valid PDF.

    Some report types are gated behind a paid plan, so we bump the user
    to enterprise before running the sweep.
    """
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    # Ensure all feature flags are unlocked for this test.
    from app.models.user import User
    from app.services.subscriptions import FeatureGate
    u = await db_session.get(User, user_id)
    await FeatureGate(db_session).set_plan(u, "enterprise")
    await db_session.commit()

    types = [
        "daily", "weekly", "monthly", "portfolio",
        "trade_history", "strategy_performance", "risk",
        "broker_activity", "pnl",
    ]
    for t in types:
        r = await client.get(f"/api/v1/reports/generate/{t}?format=pdf", headers=headers)
        assert r.status_code == 200, f"{t}: {r.text}"
        assert r.content[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_reports_requires_auth(client):
    r = await client.get("/api/v1/reports/preview/daily")
    assert r.status_code == 401
    r = await client.get("/api/v1/reports/generate/daily")
    assert r.status_code == 401
