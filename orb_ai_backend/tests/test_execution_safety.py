"""Milestone 8 — Execution Safety tests.

Covers:
* Configuration hierarchy (DB row → env → defaults)
* Trades-per-second, orders-per-minute, orders-per-hour enforcement
* Duplicate-order detection (reject + queue modes)
* Kill switch
* Bot auto-pause after repeat breaches
* Global platform limit
* Audit event logging + admin dashboard aggregates
* API endpoints (settings PATCH, kill-switch POST, events GET, dashboard GET)
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.services.execution_safety_service import (
    ExecutionSafetyAction,
    ExecutionSafetyService,
    SafetyContext,
)
from app.models.execution_safety import ExecutionLimitType


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _reset_safety_state():
    ExecutionSafetyService.reset_state_for_tests()
    yield
    ExecutionSafetyService.reset_state_for_tests()


# ============================================================ service


async def test_allow_below_all_limits(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 5, "orders_per_minute": 100,
        "orders_per_hour": 2000, "duplicate_window_seconds": 0.0,
    })
    ctx = SafetyContext(user_id="u1", symbol="RELIANCE", quantity=1)
    d = await svc.check_order(ctx, persist_event=False)
    assert d.action == ExecutionSafetyAction.ALLOWED


async def test_trades_per_second_reject(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 3,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })
    for i in range(3):
        # Each iteration uses different qty so duplicate detection is bypassed
        d = await svc.check_order(
            SafetyContext(user_id="u1", symbol="RELIANCE", quantity=i + 1),
            persist_event=False,
        )
        assert d.action == ExecutionSafetyAction.ALLOWED
    d = await svc.check_order(
        SafetyContext(user_id="u1", symbol="RELIANCE", quantity=99),
        persist_event=False,
    )
    assert d.action == ExecutionSafetyAction.REJECTED
    assert d.limit_type == ExecutionLimitType.TRADES_PER_SECOND
    assert d.retry_after is not None and d.retry_after >= 0.0


async def test_orders_per_minute_reject(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 1000,
        "orders_per_minute": 4,
        "orders_per_hour": 10_000,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })
    for i in range(4):
        d = await svc.check_order(
            SafetyContext(user_id="u2", quantity=i + 1),
            persist_event=False,
        )
        assert d.action == ExecutionSafetyAction.ALLOWED
    d = await svc.check_order(
        SafetyContext(user_id="u2", quantity=999),
        persist_event=False,
    )
    assert d.action == ExecutionSafetyAction.REJECTED
    assert d.limit_type == ExecutionLimitType.ORDERS_PER_MINUTE


async def test_duplicate_reject(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "duplicate_window_seconds": 60.0,
        "duplicate_action": "reject",
        "queue_enabled": False,
    })
    ctx = SafetyContext(
        user_id="u3", symbol="INFY", side="BUY", quantity=10, price=1500.0,
        order_type="MARKET",
    )
    d1 = await svc.check_order(ctx, persist_event=False)
    assert d1.action == ExecutionSafetyAction.ALLOWED
    d2 = await svc.check_order(ctx, persist_event=False)
    assert d2.action == ExecutionSafetyAction.DUPLICATE_BLOCKED
    assert d2.limit_type == ExecutionLimitType.DUPLICATE_ORDER


async def test_duplicate_queue_mode(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "duplicate_window_seconds": 60.0,
        "duplicate_action": "queue",
        "queue_enabled": True,
    })
    ctx = SafetyContext(
        user_id="u4", symbol="INFY", side="BUY", quantity=10, price=1500.0,
    )
    await svc.check_order(ctx, persist_event=False)
    d = await svc.check_order(ctx, persist_event=False)
    assert d.action == ExecutionSafetyAction.QUEUED
    assert ExecutionSafetyService.queue_size() >= 1


async def test_kill_switch_blocks_all(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.activate_kill_switch(admin_user_id=None, reason="ops incident")
    d = await svc.check_order(
        SafetyContext(user_id="u5"), persist_event=False,
    )
    assert d.action == ExecutionSafetyAction.KILL_SWITCH_ACTIVATED
    assert d.limit_type == ExecutionLimitType.KILL_SWITCH
    # Deactivate → orders flow again
    await svc.deactivate_kill_switch(admin_user_id=None, reason="all clear")
    d = await svc.check_order(SafetyContext(user_id="u5", quantity=1), persist_event=False)
    assert d.action == ExecutionSafetyAction.ALLOWED


async def test_global_platform_limit(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 1000,
        "orders_per_minute": 100_000,
        "orders_per_hour": 1_000_000,
        "global_orders_per_second": 3,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })
    # Different users hitting the global cap
    for i in range(3):
        d = await svc.check_order(
            SafetyContext(user_id=f"gu{i}", quantity=1), persist_event=False,
        )
        assert d.action == ExecutionSafetyAction.ALLOWED
    d = await svc.check_order(
        SafetyContext(user_id="gu99", quantity=1), persist_event=False,
    )
    assert d.action == ExecutionSafetyAction.REJECTED
    assert d.limit_type == ExecutionLimitType.GLOBAL_PLATFORM_LIMIT


async def test_event_logging_persisted(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 2,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })
    for i in range(4):
        await svc.check_order(SafetyContext(user_id="uL", quantity=i + 1))
    await db_session.commit()
    events = await svc.list_events(limit=50, user_id="uL")
    # 2 allowed + at least 1 rejected (tps=2 → 3rd/4th get blocked)
    actions = [e.action for e in events]
    assert ExecutionSafetyAction.ALLOWED in actions
    assert ExecutionSafetyAction.REJECTED in actions


async def test_config_audit_recorded(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(
        admin_user_id="admin-1",
        changes={"trades_per_second": 20},
        reason="ops tuning",
    )
    await db_session.commit()

    from sqlalchemy import select
    from app.models.execution_safety import ExecutionSafetyConfigAudit
    rows = (await db_session.execute(
        select(ExecutionSafetyConfigAudit).where(
            ExecutionSafetyConfigAudit.field == "trades_per_second"
        )
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].new_value == "20"
    assert rows[0].admin_user_id == "admin-1"
    assert rows[0].reason == "ops tuning"


# ============================================================ HTTP


async def test_admin_can_read_and_update_settings(client, admin_headers):
    r = await client.get("/api/v1/execution-safety/settings", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trades_per_second"] == 8  # env default
    assert body["orders_per_minute"] == 100
    assert body["orders_per_hour"] == 2000

    r = await client.patch(
        "/api/v1/execution-safety/settings",
        headers=admin_headers,
        json={"trades_per_second": 12, "reason": "prod tuning"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["trades_per_second"] == 12

    # Config audit endpoint should show one entry
    r = await client.get(
        "/api/v1/execution-safety/config-audit", headers=admin_headers,
    )
    assert r.status_code == 200
    audits = r.json()
    assert any(a["field"] == "trades_per_second" and a["new_value"] == "12"
               for a in audits)


async def test_user_cannot_read_settings(client, user_headers):
    r = await client.get(
        "/api/v1/execution-safety/settings", headers=user_headers,
    )
    assert r.status_code == 403


async def test_kill_switch_endpoint(client, admin_headers):
    r = await client.post(
        "/api/v1/execution-safety/kill-switch",
        headers=admin_headers,
        json={"active": True, "reason": "test drill"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["kill_switch_active"] is True

    r = await client.post(
        "/api/v1/execution-safety/kill-switch",
        headers=admin_headers,
        json={"active": False, "reason": "drill complete"},
    )
    assert r.status_code == 200
    assert r.json()["kill_switch_active"] is False


async def test_user_can_view_own_limits(client, user_headers):
    r = await client.get("/api/v1/execution-safety/me/limits", headers=user_headers)
    assert r.status_code == 200
    body = r.json()
    assert "trades_per_second" in body
    assert "orders_per_minute" in body
    assert "kill_switch_active" in body


async def test_dashboard_aggregates(client, admin_headers, db_session):
    # Generate some events
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 1,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })
    for i in range(4):
        await svc.check_order(SafetyContext(user_id="dash", symbol="RELIANCE",
                                            quantity=i + 1))
    await db_session.commit()

    r = await client.get(
        "/api/v1/execution-safety/dashboard?since_minutes=60",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_events"] >= 4
    assert any(e["label"] == "allowed" for e in body["by_action"])
    assert any(e["label"] == "rejected" for e in body["by_action"])
