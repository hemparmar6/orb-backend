"""Admin endpoints — RBAC, users, brokers, sessions, orders, trades, audit, health.

All endpoints require ``UserRole.ADMIN``. Regular users hit 403. Admins can
list, filter, and mutate; every mutation is captured in the audit log.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import update

from app.models.broker import BrokerAccount, BrokerType
from app.models.engine import EngineSession, EngineSessionStatus, ExecutionMode
from app.models.user import User, UserRole


ADMIN_REG = {
    "email": "root@example.com",
    "password": "adminpass1234",
    "full_name": "Root Admin",
}
USER_REG = {
    "email": "bob@example.com",
    "password": "userpass1234",
    "full_name": "Bob User",
}


async def _register_and_login(client, payload: dict[str, str]) -> str:
    r = await client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 201, r.text
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _promote_to_admin(db_session, email: str) -> None:
    await db_session.execute(
        update(User).where(User.email == email).values(role=UserRole.ADMIN)
    )
    await db_session.commit()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin_ctx(client, db_session):
    """Register two users, promote the first to admin, return both tokens."""
    admin_tok = await _register_and_login(client, ADMIN_REG)
    user_tok = await _register_and_login(client, USER_REG)
    await _promote_to_admin(db_session, ADMIN_REG["email"])
    # A logged-in admin needs to re-login for role change to reflect in JWT? No —
    # role is loaded from DB on every request via `get_current_user`. So the same
    # token works.
    return {"admin_token": admin_tok, "user_token": user_tok}


# ============================================================ RBAC


@pytest.mark.asyncio
async def test_admin_endpoints_reject_non_admin(client, admin_ctx):
    for path in (
        "/api/v1/admin/users",
        "/api/v1/admin/broker-accounts",
        "/api/v1/admin/sessions",
        "/api/v1/admin/strategies",
        "/api/v1/admin/backtests",
        "/api/v1/admin/orders",
        "/api/v1/admin/trades",
        "/api/v1/admin/audit-logs",
        "/api/v1/admin/system/health",
        "/api/v1/admin/risk-defaults",
        "/api/v1/admin/strategies/registered",
    ):
        r = await client.get(path, headers=_auth(admin_ctx["user_token"]))
        assert r.status_code == 403, f"{path} should be forbidden for non-admin, got {r.status_code}"


@pytest.mark.asyncio
async def test_admin_endpoints_require_auth(client):
    r = await client.get("/api/v1/admin/users")
    assert r.status_code == 401


# ============================================================ users


@pytest.mark.asyncio
async def test_admin_list_users_with_search_and_paginate(client, admin_ctx):
    r = await client.get("/api/v1/admin/users", headers=_auth(admin_ctx["admin_token"]))
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 2
    emails = {u["email"] for u in data["items"]}
    assert ADMIN_REG["email"] in emails
    assert USER_REG["email"] in emails

    # Search by substring.
    r = await client.get(
        "/api/v1/admin/users?q=bob", headers=_auth(admin_ctx["admin_token"])
    )
    assert r.status_code == 200
    hits = r.json()
    assert hits["total"] == 1
    assert hits["items"][0]["email"] == USER_REG["email"]

    # Filter by role.
    r = await client.get(
        "/api/v1/admin/users?role=admin", headers=_auth(admin_ctx["admin_token"])
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["email"] == ADMIN_REG["email"]

    # Pagination.
    r = await client.get(
        "/api/v1/admin/users?page=1&page_size=1", headers=_auth(admin_ctx["admin_token"])
    )
    assert len(r.json()["items"]) == 1


@pytest.mark.asyncio
async def test_admin_get_user_404(client, admin_ctx):
    r = await client.get(
        "/api/v1/admin/users/does-not-exist",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_admin_update_user_role_and_active_records_audit(client, admin_ctx, db_session):
    # Fetch Bob's id.
    r = await client.get(
        "/api/v1/admin/users?q=bob", headers=_auth(admin_ctx["admin_token"])
    )
    bob_id = r.json()["items"][0]["id"]

    # Deactivate + promote Bob.
    upd = await client.patch(
        f"/api/v1/admin/users/{bob_id}",
        headers=_auth(admin_ctx["admin_token"]),
        json={"is_active": False, "role": "admin"},
    )
    assert upd.status_code == 200, upd.text
    body = upd.json()
    assert body["is_active"] is False
    assert body["role"] == "admin"

    # Audit trail captured the change.
    audit = await client.get(
        "/api/v1/admin/audit-logs?action=user.update",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert audit.status_code == 200
    logs = audit.json()
    assert logs["total"] >= 1
    log = logs["items"][0]
    assert log["target_type"] == "user"
    assert log["target_id"] == bob_id
    assert log["details"]["role"]["to"] == "admin"
    assert log["details"]["is_active"]["from"] is True


@pytest.mark.asyncio
async def test_admin_cannot_self_demote_or_self_deactivate(client, admin_ctx):
    # Admin's own id.
    me = await client.get("/api/v1/users/me", headers=_auth(admin_ctx["admin_token"]))
    admin_id = me.json()["id"]
    r = await client.patch(
        f"/api/v1/admin/users/{admin_id}",
        headers=_auth(admin_ctx["admin_token"]),
        json={"role": "user"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "self_demote_forbidden"

    r = await client.patch(
        f"/api/v1/admin/users/{admin_id}",
        headers=_auth(admin_ctx["admin_token"]),
        json={"is_active": False},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "self_deactivate_forbidden"


# ============================================================ broker accounts


@pytest.mark.asyncio
async def test_admin_list_and_delete_broker_account(client, admin_ctx, db_session):
    # Seed a broker account for Bob directly in the DB.
    bob = (await client.get(
        "/api/v1/admin/users?q=bob", headers=_auth(admin_ctx["admin_token"])
    )).json()["items"][0]

    acct = BrokerAccount(
        user_id=bob["id"],
        broker_type=BrokerType.MOCK_LIVE,
        alias="bob-mock",
        credentials_ciphertext="not-real-ciphertext",
        is_active=True,
    )
    db_session.add(acct)
    await db_session.commit()
    await db_session.refresh(acct)

    r = await client.get(
        "/api/v1/admin/broker-accounts",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["alias"] == "bob-mock"

    # Filter by user_id.
    r = await client.get(
        f"/api/v1/admin/broker-accounts?user_id={bob['id']}",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert r.json()["total"] == 1

    # Delete.
    d = await client.delete(
        f"/api/v1/admin/broker-accounts/{acct.id}",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert d.status_code == 200

    r = await client.get(
        "/api/v1/admin/broker-accounts",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert r.json()["total"] == 0

    # Audit log has the deletion.
    audit = await client.get(
        "/api/v1/admin/audit-logs?target_type=broker_account",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert audit.json()["total"] == 1
    assert audit.json()["items"][0]["action"] == "broker_account.delete"


# ============================================================ engine sessions


@pytest.mark.asyncio
async def test_admin_list_and_stop_engine_session(client, admin_ctx, db_session):
    bob = (await client.get(
        "/api/v1/admin/users?q=bob", headers=_auth(admin_ctx["admin_token"])
    )).json()["items"][0]

    # Seed a running session directly in the DB.
    sess = EngineSession(
        user_id=bob["id"],
        strategy_name="demo_ma_cross",
        status=EngineSessionStatus.RUNNING,
        execution_mode=ExecutionMode.PAPER,
        symbols=["A"],
        params={},
        risk_config={},
        initial_capital=100_000.0,
        started_at=datetime.now(timezone.utc),
        last_heartbeat_at=datetime.now(timezone.utc),
    )
    db_session.add(sess)
    await db_session.commit()
    await db_session.refresh(sess)

    r = await client.get(
        "/api/v1/admin/sessions", headers=_auth(admin_ctx["admin_token"])
    )
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["status"] == "running"

    # Filter by status.
    r = await client.get(
        "/api/v1/admin/sessions?status=running",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert r.json()["total"] == 1

    # Force-stop.
    stopped = await client.post(
        f"/api/v1/admin/sessions/{sess.id}/stop",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"

    # Idempotent — stopping again returns the same stopped row (no error).
    again = await client.post(
        f"/api/v1/admin/sessions/{sess.id}/stop",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert again.status_code == 200

    # Audit log captured the stop.
    audit = await client.get(
        "/api/v1/admin/audit-logs?action=engine_session.stop",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert audit.json()["total"] == 1


# ============================================================ strategies + registered


@pytest.mark.asyncio
async def test_admin_registered_strategies_lists_known_names(client, admin_ctx):
    r = await client.get(
        "/api/v1/admin/strategies/registered",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert r.status_code == 200
    names = {e["name"] for e in r.json()}
    # `orb` and `demo_ma_cross` are registered at import time.
    assert "orb" in names
    assert "demo_ma_cross" in names


@pytest.mark.asyncio
async def test_admin_list_user_strategies(client, admin_ctx):
    # Bob creates a strategy through the regular user endpoint.
    r = await client.post(
        "/api/v1/strategies",
        headers=_auth(admin_ctx["user_token"]),
        json={"name": "MA-cross tuned", "kind": "demo_ma_cross", "params": {"fast": 5}},
    )
    assert r.status_code in (200, 201), r.text

    r = await client.get(
        "/api/v1/admin/strategies", headers=_auth(admin_ctx["admin_token"])
    )
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["name"] == "MA-cross tuned"


# ============================================================ system health


@pytest.mark.asyncio
async def test_admin_system_health_reports_counts(client, admin_ctx):
    r = await client.get(
        "/api/v1/admin/system/health", headers=_auth(admin_ctx["admin_token"])
    )
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["status"] in ("ok", "degraded")
    assert h["database"] == "ok"
    # Redis is disabled in tests → "disabled".
    assert h["redis"] == "disabled"
    assert h["users_total"] >= 2
    assert h["users_admins"] >= 1
    assert "dhan" in h["market_data_providers"]
    assert "kotak_neo" in h["market_data_providers"]
    assert "synthetic" in h["historical_providers"]
    assert "orb" in h["registered_strategies"]
    assert h["api_version"]


# ============================================================ risk defaults


@pytest.mark.asyncio
async def test_admin_risk_defaults_roundtrip(client, admin_ctx):
    r = await client.get(
        "/api/v1/admin/risk-defaults", headers=_auth(admin_ctx["admin_token"])
    )
    assert r.status_code == 200

    upd = await client.put(
        "/api/v1/admin/risk-defaults",
        headers=_auth(admin_ctx["admin_token"]),
        json={"max_daily_loss": 5000, "trading_session_start": "09:15"},
    )
    assert upd.status_code == 200
    body = upd.json()
    assert body["max_daily_loss"] == 5000
    assert body["trading_session_start"] == "09:15"

    # Audit log has the change.
    audit = await client.get(
        "/api/v1/admin/audit-logs?target_type=risk_defaults",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert audit.json()["total"] == 1
    assert audit.json()["items"][0]["action"] == "risk_defaults.update"


# ============================================================ audit filters


@pytest.mark.asyncio
async def test_admin_audit_log_filters(client, admin_ctx, db_session):
    # Produce a few audit entries.
    bob = (await client.get(
        "/api/v1/admin/users?q=bob", headers=_auth(admin_ctx["admin_token"])
    )).json()["items"][0]

    await client.patch(
        f"/api/v1/admin/users/{bob['id']}",
        headers=_auth(admin_ctx["admin_token"]),
        json={"is_verified": True},
    )
    await client.put(
        "/api/v1/admin/risk-defaults",
        headers=_auth(admin_ctx["admin_token"]),
        json={"max_position_size": 1000},
    )

    # Filter by target_type.
    r = await client.get(
        "/api/v1/admin/audit-logs?target_type=user",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert r.json()["total"] == 1
    r = await client.get(
        "/api/v1/admin/audit-logs?target_type=risk_defaults",
        headers=_auth(admin_ctx["admin_token"]),
    )
    assert r.json()["total"] == 1

    # Pagination.
    r = await client.get(
        "/api/v1/admin/audit-logs?page=1&page_size=1",
        headers=_auth(admin_ctx["admin_token"]),
    )
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
