"""Automatic nightly PAPER-revert scheduler regression suite.

Verifies:
* disabled by default — a fresh install never auto-flips LIVE → PAPER.
* running after the configured local time flips LIVE → PAPER cleanly.
* running before the configured local time is a no-op.
* active LIVE sessions block the revert (records audit + notification).
* open LIVE positions block the revert.
* open LIVE orders block the revert.
* a second tick on the same local day is a no-op (idempotent).
* the idempotency flag survives a "restart" (fresh service instance).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.audit import AuditLog
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    PaperPosition,
)
from app.models.execution_safety import ExecutionSafetySetting
from app.models.user import User, UserRole
from app.services.execution_safety_service import GLOBAL_SETTINGS_ID
from app.services.trading_mode_service import (
    LIVE,
    LIVE_CONFIRMATION,
    PAPER,
    TradingModeService,
)


pytestmark = pytest.mark.asyncio


async def _admin(db, email: str = "revert-admin@example.com") -> User:
    u = User(
        email=email,
        hashed_password=hash_password("x"),
        full_name="Revert Admin",
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
    )
    db.add(u)
    await db.flush()
    return u


async def _arm_live(db, admin: User) -> TradingModeService:
    svc = TradingModeService(db)
    await svc.set_mode(target=LIVE, operator=admin, confirmation=LIVE_CONFIRMATION)
    await db.commit()
    return svc


def _tomorrow_1am_ist_utc() -> datetime:
    # Choose a fixed reference so tests are deterministic across midnight.
    return datetime(2026, 3, 1, 19, 30, tzinfo=timezone.utc)  # 01:00 IST next day


async def test_disabled_by_default(db_session):
    admin = await _admin(db_session, "revert-disabled@example.com")
    svc = await _arm_live(db_session, admin)
    result = await svc.run_auto_revert_if_due()
    assert result["ran"] is False
    assert result["reason"] == "disabled"
    snap = await svc.snapshot()
    assert snap["mode"] == LIVE


async def test_reverts_after_configured_time_with_no_exposure(db_session):
    admin = await _admin(db_session, "revert-happy@example.com")
    svc = await _arm_live(db_session, admin)
    await svc.set_auto_revert(
        operator=admin, enabled=True, at_time="15:30", timezone_name="Asia/Kolkata",
    )
    await db_session.commit()

    # Choose a UTC moment that is *after* 15:30 IST → 10:00 UTC same day.
    fake_now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)  # 17:30 IST
    result = await TradingModeService(db_session).run_auto_revert_if_due(now=fake_now)
    assert result["ran"] is True
    assert result["reason"] == "reverted"
    snap = await TradingModeService(db_session).snapshot()
    assert snap["mode"] == PAPER
    assert snap["auto_revert"]["last_run_result"] == "reverted"


async def test_no_op_before_configured_time(db_session):
    admin = await _admin(db_session, "revert-early@example.com")
    svc = await _arm_live(db_session, admin)
    await svc.set_auto_revert(
        operator=admin, enabled=True, at_time="23:59", timezone_name="Asia/Kolkata",
    )
    await db_session.commit()
    fake_now = datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc)  # 10:30 IST
    result = await TradingModeService(db_session).run_auto_revert_if_due(now=fake_now)
    assert result["ran"] is False
    assert result["reason"] == "not_yet"
    assert (await TradingModeService(db_session).snapshot())["mode"] == LIVE


async def _make_engine_session(db, admin: User) -> EngineSession:
    s = EngineSession(
        user_id=admin.id,
        strategy_name="revert-test",
        status=EngineSessionStatus.RUNNING,
        execution_mode=ExecutionMode.LIVE,
        symbols=["A"],
        params={}, risk_config={},
        initial_capital=100000,
        started_at=datetime.now(timezone.utc),
    )
    db.add(s)
    await db.flush()
    return s


async def test_blocked_by_active_live_session(db_session):
    admin = await _admin(db_session, "revert-sess@example.com")
    svc = await _arm_live(db_session, admin)
    await _make_engine_session(db_session, admin)
    await svc.set_auto_revert(
        operator=admin, enabled=True, at_time="15:30", timezone_name="Asia/Kolkata",
    )
    await db_session.commit()

    result = await TradingModeService(db_session).run_auto_revert_if_due(
        now=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert result["ran"] is False
    assert result["reason"] == "live_exposure"
    assert result["exposure"]["active_live_sessions"] == 1
    snap = await TradingModeService(db_session).snapshot()
    assert snap["mode"] == LIVE  # Not flipped
    assert snap["auto_revert"]["last_run_result"] == "blocked_live_exposure"


async def test_blocked_by_open_live_position(db_session):
    admin = await _admin(db_session, "revert-pos@example.com")
    svc = await _arm_live(db_session, admin)
    sess = await _make_engine_session(db_session, admin)
    # Add a nonzero live position on the running session.
    pos = PaperPosition(
        user_id=admin.id,
        engine_session_id=sess.id, symbol="A", exchange="NSE",
        product="mis", net_quantity=5, average_price=100.0, last_price=100.0,
    )
    db_session.add(pos)
    await db_session.commit()
    await svc.set_auto_revert(
        operator=admin, enabled=True, at_time="15:30", timezone_name="Asia/Kolkata",
    )
    await db_session.commit()

    result = await TradingModeService(db_session).run_auto_revert_if_due(
        now=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert result["ran"] is False
    assert result["reason"] == "live_exposure"
    assert result["exposure"]["open_live_positions"] == 1


async def test_blocked_by_open_live_order(db_session):
    admin = await _admin(db_session, "revert-order@example.com")
    svc = await _arm_live(db_session, admin)
    sess = await _make_engine_session(db_session, admin)
    order = PaperOrder(
        engine_session_id=sess.id, user_id=admin.id,
        symbol="A", exchange="NSE", side=OrderSide.BUY,
        quantity=1, filled_quantity=0, product="mis",
        order_type=OrderType.LIMIT, price=100.0, status=OrderStatus.OPEN,
    )
    db_session.add(order)
    await db_session.commit()
    await svc.set_auto_revert(
        operator=admin, enabled=True, at_time="15:30", timezone_name="Asia/Kolkata",
    )
    await db_session.commit()

    result = await TradingModeService(db_session).run_auto_revert_if_due(
        now=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert result["ran"] is False
    assert result["reason"] == "live_exposure"
    assert result["exposure"]["open_live_orders"] == 1


async def test_duplicate_tick_same_day_is_noop(db_session):
    admin = await _admin(db_session, "revert-dup@example.com")
    svc = await _arm_live(db_session, admin)
    await svc.set_auto_revert(
        operator=admin, enabled=True, at_time="15:30", timezone_name="Asia/Kolkata",
    )
    await db_session.commit()

    now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    r1 = await TradingModeService(db_session).run_auto_revert_if_due(now=now)
    assert r1["ran"] is True
    # Second tick, same day: no-op.
    r2 = await TradingModeService(db_session).run_auto_revert_if_due(
        now=now + timedelta(minutes=5),
    )
    assert r2["ran"] is False
    assert r2["reason"] == "already_ran_today"


async def test_restart_does_not_create_duplicate_transition(db_session):
    admin = await _admin(db_session, "revert-restart@example.com")
    svc = await _arm_live(db_session, admin)
    await svc.set_auto_revert(
        operator=admin, enabled=True, at_time="15:30", timezone_name="Asia/Kolkata",
    )
    await db_session.commit()
    now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    await TradingModeService(db_session).run_auto_revert_if_due(now=now)

    # Simulate a process restart — fresh service instance, same DB row.
    fresh = TradingModeService(db_session)
    r = await fresh.run_auto_revert_if_due(now=now + timedelta(minutes=1))
    assert r["ran"] is False
    assert r["reason"] == "already_ran_today"

    # Audit log has exactly ONE trading_mode.change with new_mode=paper for
    # today's revert (in addition to the initial paper→live change from
    # _arm_live).
    changes = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "trading_mode.change")
    )).scalars().all()
    paper_reverts = [
        a for a in changes
        if (a.details or {}).get("new_mode") == PAPER
    ]
    assert len(paper_reverts) == 1
