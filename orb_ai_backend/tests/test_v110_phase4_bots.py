"""Phase 4 tests — Bot management, Kill switch, Circuit breakers, Analytics."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_pro_user(client, admin_headers) -> dict:
    """Register a user, put them on `pro` plan (automation enabled), return auth headers.

    Also seeds a catalog entry for engine-registered strategy ``orb`` (FREE tier
    so it is available on every plan we test with).
    """
    from app.models.user import User
    from app.models.subscription import SubscriptionPlan, UserSubscription, SubscriptionStatus
    from app.models.strategy_catalog import (
        StrategyCatalog, StrategyStatus as CatalogStatus,
        StrategyDifficulty, StrategyRiskLevel,
    )
    from app.db.session import async_session_factory

    email = "bots.tester@example.com"
    password = "BotsPass123!"
    await client.post("/api/v1/auth/register",
                      json={"email": email, "password": password,
                            "full_name": "Bots Tester"})
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": password})
    assert r.status_code == 200
    tokens = r.json()

    async with async_session_factory() as db:
        u = (await db.execute(select(User).where(User.email == email))).scalar_one()
        u.is_verified = True
        # Assign PRO plan (automation enabled, unlimited bots off but 5 bots).
        plan = (await db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.key == "pro")
        )).scalar_one()
        # Ensure plan has automation flags for test.
        plan.automation_enabled = True
        plan.paper_trading_only = False
        plan.max_running_bots = 5
        plan.max_open_positions = 10
        sub = UserSubscription(
            user_id=u.id, plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE, provider="noop",
        )
        db.add(sub)

        # Seed catalog entry for engine-registered strategy "orb" if absent.
        existing = (await db.execute(
            select(StrategyCatalog).where(StrategyCatalog.key == "orb")
        )).scalar_one_or_none()
        if existing is None:
            db.add(StrategyCatalog(
                key="orb", name="ORB (engine)",
                min_plan_tier="free",
                status=CatalogStatus.ACTIVE.value,
                difficulty=StrategyDifficulty.INTERMEDIATE,
                risk_level=StrategyRiskLevel.MEDIUM,
                category="orb", description="Runtime ORB strategy",
                automation_supported=True, live_trading_supported=True,
                display_order=0,
            ))
        await db.commit()

    return {"Authorization": f"Bearer {tokens['access_token']}", "email": email,
            "user_id_query": email}


async def test_bot_crud_and_lifecycle(client, admin_headers):
    hdrs = await _make_pro_user(client, admin_headers)
    hdrs2 = {"Authorization": hdrs["Authorization"]}

    # Create
    r = await client.post("/api/v1/bots", headers=hdrs2, json={
        "name": "MyBot1",
        "strategy_key": "orb",
        "symbols": ["NIFTY"],
        "params": {"ema_fast": 9, "ema_slow": 21},
        "risk_config": {"max_daily_loss": 5000},
        "execution_mode": "paper",
        "initial_capital": 100000,
    })
    assert r.status_code == 201, r.text
    bot = r.json()
    assert bot["status"] == "idle"
    bot_id = bot["id"]

    # Duplicate name → 409
    r2 = await client.post("/api/v1/bots", headers=hdrs2, json={
        "name": "MyBot1", "strategy_key": "orb", "symbols": ["NIFTY"],
    })
    assert r2.status_code == 409

    # List
    r = await client.get("/api/v1/bots", headers=hdrs2)
    assert r.status_code == 200
    assert len(r.json()) == 1

    # Patch
    r = await client.patch(f"/api/v1/bots/{bot_id}", headers=hdrs2,
                           json={"description": "updated", "tags": ["prod"]})
    assert r.status_code == 200
    assert r.json()["description"] == "updated"
    assert r.json()["tags"] == ["prod"]

    # Delete idle bot
    r = await client.delete(f"/api/v1/bots/{bot_id}", headers=hdrs2)
    assert r.status_code == 204


async def test_bot_start_stop_pause_resume(client, admin_headers):
    hdrs = await _make_pro_user(client, admin_headers)
    hdrs2 = {"Authorization": hdrs["Authorization"]}

    r = await client.post("/api/v1/bots", headers=hdrs2, json={
        "name": "LifecycleBot", "strategy_key": "orb",
        "symbols": ["NIFTY"], "initial_capital": 100000,
    })
    assert r.status_code == 201
    bot_id = r.json()["id"]

    # Start
    r = await client.post(f"/api/v1/bots/{bot_id}/start", headers=hdrs2)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"

    # Pause
    r = await client.post(f"/api/v1/bots/{bot_id}/pause", headers=hdrs2)
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    # Resume
    r = await client.post(f"/api/v1/bots/{bot_id}/resume", headers=hdrs2)
    assert r.status_code == 200
    assert r.json()["status"] == "running"

    # Stop
    r = await client.post(f"/api/v1/bots/{bot_id}/stop", headers=hdrs2)
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"


async def test_kill_switch_bot_and_user(client, admin_headers):
    hdrs = await _make_pro_user(client, admin_headers)
    hdrs2 = {"Authorization": hdrs["Authorization"]}

    r = await client.post("/api/v1/bots", headers=hdrs2, json={
        "name": "KillMe", "strategy_key": "orb", "symbols": ["NIFTY"],
        "initial_capital": 100000,
    })
    bot_id = r.json()["id"]
    await client.post(f"/api/v1/bots/{bot_id}/start", headers=hdrs2)

    # Kill single bot
    r = await client.post("/api/v1/kill-switch/trigger", headers=hdrs2, json={
        "scope": "bot", "target_bot_id": bot_id, "reason": "test kill",
    })
    assert r.status_code == 200, r.text
    assert r.json()["bots_stopped"] >= 1

    # Confirm bot is killed and can't restart until cleared
    r = await client.get(f"/api/v1/bots/{bot_id}", headers=hdrs2)
    assert r.json()["status"] == "killed"
    assert r.json()["is_killed"] is True

    r = await client.post(f"/api/v1/bots/{bot_id}/start", headers=hdrs2)
    assert r.status_code == 403

    # Clear kill and restart
    r = await client.post(f"/api/v1/bots/{bot_id}/clear-kill", headers=hdrs2)
    assert r.status_code == 200
    assert r.json()["is_killed"] is False

    r = await client.post(f"/api/v1/bots/{bot_id}/start", headers=hdrs2)
    assert r.status_code == 200

    # User-level kill
    r = await client.post("/api/v1/kill-switch/trigger", headers=hdrs2, json={
        "scope": "user", "reason": "user emergency stop",
    })
    assert r.status_code == 200
    # New start should be blocked
    r = await client.get(f"/api/v1/bots/{bot_id}", headers=hdrs2)
    assert r.json()["is_killed"] is True


async def test_global_kill_switch_admin_only(client, admin_headers):
    hdrs = await _make_pro_user(client, admin_headers)
    hdrs2 = {"Authorization": hdrs["Authorization"]}

    # Regular user → 403
    r = await client.post("/api/v1/kill-switch/trigger", headers=hdrs2, json={
        "scope": "global", "reason": "should fail",
    })
    assert r.status_code == 403

    # Admin succeeds
    r = await client.post("/api/v1/kill-switch/trigger", headers=admin_headers,
                          json={"scope": "global", "reason": "maintenance"})
    assert r.status_code == 200
    ev = r.json()

    # New bot start under global kill → blocked
    r = await client.post("/api/v1/bots", headers=hdrs2, json={
        "name": "Blocked", "strategy_key": "orb", "symbols": ["NIFTY"],
    })
    bot_id = r.json()["id"]
    r = await client.post(f"/api/v1/bots/{bot_id}/start", headers=hdrs2)
    assert r.status_code == 403

    # Admin resolves
    r = await client.post(f"/api/v1/kill-switch/{ev['id']}/resolve",
                          headers=admin_headers)
    assert r.status_code == 200


async def test_circuit_breaker_upsert_and_list(admin_headers, client):
    r = await client.post("/api/v1/circuit-breakers/configs",
                          headers=admin_headers, json={
        "level": "global", "breaker_type": "maintenance_mode",
        "enabled": True, "action": "block_new",
    })
    assert r.status_code == 200
    cfg_id = r.json()["id"]

    r = await client.get("/api/v1/circuit-breakers/configs",
                         headers=admin_headers)
    assert r.status_code == 200
    assert any(c["id"] == cfg_id for c in r.json())

    # Upsert (same identity) updates in place
    r = await client.post("/api/v1/circuit-breakers/configs",
                          headers=admin_headers, json={
        "level": "global", "breaker_type": "maintenance_mode",
        "enabled": False, "action": "warn",
    })
    assert r.status_code == 200
    assert r.json()["id"] == cfg_id

    # Delete
    r = await client.delete(f"/api/v1/circuit-breakers/configs/{cfg_id}",
                            headers=admin_headers)
    assert r.status_code == 204


async def test_maintenance_mode_blocks_starts(admin_headers, client):
    # Admin enables maintenance_mode
    await client.post("/api/v1/circuit-breakers/configs",
                      headers=admin_headers, json={
        "level": "global", "breaker_type": "maintenance_mode",
        "enabled": True, "action": "block_new",
    })
    hdrs = await _make_pro_user(client, admin_headers)
    hdrs2 = {"Authorization": hdrs["Authorization"]}

    r = await client.post("/api/v1/bots", headers=hdrs2, json={
        "name": "MaintBlocked", "strategy_key": "orb", "symbols": ["NIFTY"],
    })
    bot_id = r.json()["id"]

    r = await client.post(f"/api/v1/bots/{bot_id}/start", headers=hdrs2)
    assert r.status_code == 403
    body = r.json()
    msg = (body.get("detail") or body.get("error", {}).get("message") or "").lower()
    assert "maintenance" in msg

    # Event recorded
    r = await client.get("/api/v1/circuit-breakers/events", headers=admin_headers)
    assert r.status_code == 200
    assert any(e["breaker_type"] == "maintenance_mode" for e in r.json())


async def test_bot_analytics_summary_and_live_monitoring(client, admin_headers):
    hdrs = await _make_pro_user(client, admin_headers)
    hdrs2 = {"Authorization": hdrs["Authorization"]}

    # Create 2 bots
    for i in range(2):
        await client.post("/api/v1/bots", headers=hdrs2, json={
            "name": f"AnBot{i}", "strategy_key": "orb",
            "symbols": ["NIFTY"], "initial_capital": 100000,
        })

    r = await client.get("/api/v1/bot-analytics/summary", headers=hdrs2)
    assert r.status_code == 200
    assert r.json()["stopped_bots"] + r.json()["running_bots"] + r.json()["paused_bots"] >= 2

    r = await client.get("/api/v1/bots/live/monitoring", headers=hdrs2)
    assert r.status_code == 200
    data = r.json()
    assert "summary" in data and "bots" in data and "kill_switch" in data


async def test_automation_monitor_admin(admin_headers, client):
    r = await client.post("/api/v1/automation-monitor/tick",
                          headers=admin_headers)
    assert r.status_code == 200
    stats = r.json()
    assert "checked" in stats and "breakers_tripped" in stats


async def test_bot_start_requires_automation(client):
    """Free-plan user cannot start a bot."""
    email = "free.user@example.com"
    password = "FreePass123!"
    await client.post("/api/v1/auth/register",
                      json={"email": email, "password": password,
                            "full_name": "Free User"})
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": password})
    hdrs = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = await client.post("/api/v1/bots", headers=hdrs, json={
        "name": "FreeBot", "strategy_key": "orb", "symbols": ["NIFTY"],
    })
    assert r.status_code == 201  # Create is allowed
    bot_id = r.json()["id"]
    r = await client.post(f"/api/v1/bots/{bot_id}/start", headers=hdrs)
    assert r.status_code == 403  # Start is blocked by permission engine
