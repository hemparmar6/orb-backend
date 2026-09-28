"""Regression coverage for the global PAPER/LIVE master switch."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.engine import EngineSession, EngineSessionStatus, ExecutionMode
from app.models.user import User
from app.services.trading_mode_service import LIVE_CONFIRMATION

pytestmark = pytest.mark.asyncio


async def test_fresh_install_defaults_to_paper(client, admin_headers):
    response = await client.get("/api/v1/trading/mode", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["mode"] == "paper"


async def test_unauthorized_user_cannot_change_mode(client, user_headers):
    response = await client.post(
        "/api/v1/trading/mode",
        headers=user_headers,
        json={"mode": "live", "confirmation": LIVE_CONFIRMATION},
    )
    assert response.status_code == 403


async def test_paper_blocks_direct_live_start(client, user_headers):
    response = await client.post(
        "/api/v1/trading/start",
        headers=user_headers,
        json={"strategy_name": "demo_ma_cross", "symbols": ["A"], "execution_mode": "live"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "live_trading_disabled_by_global_mode"


async def test_admin_requires_explicit_confirmation_and_audits_denial(
    client, admin_headers, db_session
):
    denied = await client.post(
        "/api/v1/trading/mode",
        headers=admin_headers,
        json={"mode": "live", "reason": "not enough confirmation"},
    )
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "live_confirmation_required"
    rows = (await client.get(
        "/api/v1/admin/audit-logs?action=trading_mode.change_denied",
        headers=admin_headers,
    )).json()["items"]
    assert rows


async def test_admin_can_enable_live_and_audit_transition(client, admin_headers, db_session):
    response = await client.post(
        "/api/v1/trading/mode",
        headers=admin_headers,
        json={"mode": "live", "confirmation": LIVE_CONFIRMATION, "reason": "morning checks passed"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "live"
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.action == "trading_mode.change"))).scalars().all()
    assert rows and rows[-1].details["new_mode"] == "live"


async def test_live_to_paper_is_blocked_by_active_live_session(
    client, admin_headers, db_session
):
    await client.post(
        "/api/v1/trading/mode", headers=admin_headers,
        json={"mode": "live", "confirmation": LIVE_CONFIRMATION},
    )
    admin = (await db_session.execute(select(User).where(User.role == "admin"))).scalar_one()
    session = EngineSession(
        user_id=admin.id,
        strategy_name="demo_ma_cross",
        status=EngineSessionStatus.RUNNING,
        execution_mode=ExecutionMode.LIVE,
        symbols=["A"],
        params={},
        risk_config={},
        initial_capital=100000,
    )
    db_session.add(session)
    await db_session.commit()
    response = await client.post(
        "/api/v1/trading/mode", headers=admin_headers, json={"mode": "paper"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "live_exposure_blocks_paper_mode"


async def test_live_to_paper_succeeds_after_safe_shutdown(client, admin_headers):
    await client.post(
        "/api/v1/trading/mode", headers=admin_headers,
        json={"mode": "live", "confirmation": LIVE_CONFIRMATION},
    )
    response = await client.post(
        "/api/v1/trading/mode", headers=admin_headers, json={"mode": "paper"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "paper"