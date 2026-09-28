"""Milestone 8 — Execution Safety STRESS tests.

These extra tests hammer the rate limiter to verify the counters behave
correctly under high volume and concurrency.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from app.models.execution_safety import ExecutionLimitType
from app.services.execution_safety_service import (
    ExecutionSafetyAction,
    ExecutionSafetyService,
    SafetyContext,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _reset_safety_state():
    ExecutionSafetyService.reset_state_for_tests()
    yield
    ExecutionSafetyService.reset_state_for_tests()


# ---- Stress: TPS -----------------------------------------------------------


async def test_stress_tps_burst_single_user(db_session):
    """Send 200 orders in a tight burst; only `trades_per_second` should
    be allowed within any 1s window."""
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 8,
        "orders_per_minute": 100_000,
        "orders_per_hour": 1_000_000,
        "global_orders_per_second": 100_000,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })

    allowed = 0
    rejected_tps = 0
    for i in range(200):
        d = await svc.check_order(
            SafetyContext(user_id="stress-tps", quantity=i + 1),
            persist_event=False,
        )
        if d.action == ExecutionSafetyAction.ALLOWED:
            allowed += 1
        elif d.action == ExecutionSafetyAction.REJECTED and (
            d.limit_type == ExecutionLimitType.TRADES_PER_SECOND
        ):
            rejected_tps += 1

    # In a synchronous tight loop everything falls within a single second,
    # so exactly `trades_per_second` orders should be allowed.
    assert allowed == 8, f"expected 8 allowed within 1s window, got {allowed}"
    assert rejected_tps == 200 - 8


# ---- Stress: Orders per minute --------------------------------------------


async def test_stress_orders_per_minute(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 100_000,
        "orders_per_minute": 50,
        "orders_per_hour": 1_000_000,
        "global_orders_per_second": 100_000,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })

    allowed = 0
    rejected_opm = 0
    for i in range(500):
        d = await svc.check_order(
            SafetyContext(user_id="stress-opm", quantity=i + 1),
            persist_event=False,
        )
        if d.action == ExecutionSafetyAction.ALLOWED:
            allowed += 1
        elif d.action == ExecutionSafetyAction.REJECTED and (
            d.limit_type == ExecutionLimitType.ORDERS_PER_MINUTE
        ):
            rejected_opm += 1

    assert allowed == 50
    assert rejected_opm == 450


# ---- Stress: Global platform limit under multi-user hammering -------------


async def test_stress_global_multi_user(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 100_000,
        "orders_per_minute": 100_000,
        "orders_per_hour": 1_000_000,
        "global_orders_per_second": 25,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })

    allowed = 0
    rejected_global = 0
    # 20 users x 10 orders each = 200 in-burst
    for u in range(20):
        for i in range(10):
            d = await svc.check_order(
                SafetyContext(user_id=f"gu-{u}", quantity=i + 1),
                persist_event=False,
            )
            if d.action == ExecutionSafetyAction.ALLOWED:
                allowed += 1
            elif (
                d.action == ExecutionSafetyAction.REJECTED
                and d.limit_type == ExecutionLimitType.GLOBAL_PLATFORM_LIMIT
            ):
                rejected_global += 1

    assert allowed == 25, f"global cap breach: allowed={allowed}"
    assert rejected_global == 200 - 25


# ---- Stress: Concurrent asyncio order flood -------------------------------


async def test_stress_concurrent_tasks(db_session):
    """Ensure counters are consistent when many orders are fired via
    asyncio.gather (still single-threaded event loop but concurrent tasks)."""
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 10,
        "orders_per_minute": 100_000,
        "orders_per_hour": 1_000_000,
        "global_orders_per_second": 100_000,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })

    async def one(i: int):
        return await svc.check_order(
            SafetyContext(user_id="concurrent-user", quantity=i + 1),
            persist_event=False,
        )

    results = await asyncio.gather(*[one(i) for i in range(150)])
    allowed = sum(1 for r in results if r.action == ExecutionSafetyAction.ALLOWED)
    rejected = sum(1 for r in results if r.action == ExecutionSafetyAction.REJECTED)

    # Only 10 orders should be allowed per 1s window; rest rejected.
    assert allowed == 10, f"expected 10 allowed, got {allowed}"
    assert rejected == 140


# ---- Duplicate flood ------------------------------------------------------


async def test_stress_duplicate_flood_reject_mode(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 100_000,
        "orders_per_minute": 100_000,
        "orders_per_hour": 1_000_000,
        "global_orders_per_second": 100_000,
        "duplicate_window_seconds": 60.0,
        "duplicate_action": "reject",
        "queue_enabled": False,
    })
    ctx = SafetyContext(
        user_id="dup-flood",
        symbol="RELIANCE",
        side="BUY",
        quantity=10,
        price=2500.0,
        order_type="MARKET",
    )

    first = await svc.check_order(ctx, persist_event=False)
    assert first.action == ExecutionSafetyAction.ALLOWED

    dup_blocked = 0
    for _ in range(100):
        d = await svc.check_order(ctx, persist_event=False)
        if d.action == ExecutionSafetyAction.DUPLICATE_BLOCKED:
            dup_blocked += 1
    assert dup_blocked == 100


# ---- Kill switch mass rejection -------------------------------------------


async def test_stress_kill_switch_blocks_flood(db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.activate_kill_switch(admin_user_id=None, reason="stress")
    blocked = 0
    for i in range(100):
        d = await svc.check_order(
            SafetyContext(user_id=f"ks-{i % 5}", quantity=i + 1),
            persist_event=False,
        )
        if d.action == ExecutionSafetyAction.KILL_SWITCH_ACTIVATED:
            blocked += 1
    assert blocked == 100

    await svc.deactivate_kill_switch(admin_user_id=None, reason="clear")
    d = await svc.check_order(
        SafetyContext(user_id="ks-post", quantity=1), persist_event=False,
    )
    assert d.action == ExecutionSafetyAction.ALLOWED
