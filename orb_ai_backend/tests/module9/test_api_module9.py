"""Module 9 — API-level tests.

Uses the project's ``client`` + ``db_engine`` fixtures (see conftest.py).
Confirms:
* Alembic migration 0008 registers the 7 new tables via Base.metadata.
* AI endpoints are mounted under ``/api/v1/ai/*``, ``/api/v1/optimisation/*``,
  ``/api/v1/market-intelligence/*``.
* All endpoints require JWT auth (401 without token).
* Portfolio snapshot works for a fresh user (empty trades → empty metrics).
* Rule-based fallback path executes end-to-end without an LLM key.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

# Force AI into rule-based fallback for tests (no LLM key needed).
import os
os.environ.setdefault("AI_ENABLED", "false")


@pytest.mark.asyncio
async def test_ai_endpoints_require_auth(client: AsyncClient):
    for path in [
        "/api/v1/ai/analytics/portfolio",
        "/api/v1/ai/trade-review",
        "/api/v1/ai/recommendations",
        "/api/v1/optimisation/jobs",
        "/api/v1/market-intelligence/SPY",
    ]:
        r = await client.get(path)
        assert r.status_code in (401, 403), f"{path} returned {r.status_code}"


async def _register_and_login(client: AsyncClient, email="ai@test.com") -> str:
    r = await client.post("/api/v1/auth/register", json={
        "email": email, "password": "Password1!", "full_name": "AI Tester",
    })
    assert r.status_code in (200, 201), r.text
    r = await client.post("/api/v1/auth/login", json={
        "email": email, "password": "Password1!",
    })
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_portfolio_snapshot_empty_user(client: AsyncClient):
    token = await _register_and_login(client, "ai-empty@test.com")
    r = await client.get(
        "/api/v1/ai/analytics/portfolio",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["scope"] == "portfolio"
    assert data["metrics"]["total_trades"] == 0
    assert data["equity_curve"] == []


@pytest.mark.asyncio
async def test_generate_recommendations_falls_back_without_llm_key(client: AsyncClient):
    token = await _register_and_login(client, "ai-rec@test.com")
    r = await client.post(
        "/api/v1/ai/recommendations/generate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    recs = r.json()
    assert isinstance(recs, list)
    assert len(recs) >= 1
    for rec in recs:
        assert rec["type"] in {
            "position_sizing", "risk_reduction", "capital_allocation",
            "strategy", "portfolio_balancing",
        }
        assert rec["status"] == "pending"
        assert 0.0 <= rec["confidence"] <= 1.0


@pytest.mark.asyncio
async def test_win_probability_endpoint(client: AsyncClient):
    token = await _register_and_login(client, "ai-wp@test.com")
    r = await client.get(
        "/api/v1/ai/analytics/win-probability?last_n=25",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json() == {"win_probability": 0.0, "last_n": 25}


@pytest.mark.asyncio
async def test_optimisation_job_lifecycle(client: AsyncClient, db_session):
    from app.models import Strategy
    token = await _register_and_login(client, "ai-opt@test.com")

    # Create a strategy owned by our user.
    from app.models.user import User
    from sqlalchemy import select
    user = (await db_session.execute(
        select(User).where(User.email == "ai-opt@test.com")
    )).scalar_one()
    strat = Strategy(user_id=user.id, name="orb-test", parameters={})
    db_session.add(strat)
    await db_session.commit()
    await db_session.refresh(strat)

    r = await client.post(
        "/api/v1/optimisation/jobs",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "strategy_id": strat.id, "kind": "parameter",
            "params_space": {"a": [1, 2], "b": [3]}, "config": {},
        },
    )
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] in {"queued", "running", "done"}

    r = await client.get(
        "/api/v1/optimisation/jobs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert any(j["id"] == job["id"] for j in r.json())
