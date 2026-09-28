"""LIVE cooldown regression suite.

Verifies the post-arm cooldown that the Trading Master Switch enforces
server-side once PAPER → LIVE is armed.  These tests are the sole callers
that opt into a non-zero cooldown; the rest of the suite pins the setting
to 0s via conftest so no legacy LIVE test is coupled to real time.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security import hash_password
from app.models.execution_safety import ExecutionSafetySetting
from app.models.user import User, UserRole
from app.services.execution_safety_service import GLOBAL_SETTINGS_ID
from app.services.trading_mode_service import (
    LIVE,
    LIVE_CONFIRMATION,
    LiveCooldownActiveError,
    LiveTradingDisabledError,
    TradingModeService,
)


pytestmark = pytest.mark.asyncio


async def _admin(db) -> User:
    u = User(
        email=f"cooldown-admin-{id(db)}@example.com",
        hashed_password=hash_password("x"),
        full_name="Cooldown Admin",
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
    )
    db.add(u)
    await db.flush()
    return u


def _set_cooldown(monkeypatch, seconds: int) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRADING_MODE_LIVE_COOLDOWN_SECONDS", seconds)


async def test_live_blocked_during_cooldown(db_session, monkeypatch):
    """Order execution must be rejected while the cooldown window is open."""
    _set_cooldown(monkeypatch, 3600)
    admin = await _admin(db_session)
    svc = TradingModeService(db_session)

    await svc.set_mode(target=LIVE, operator=admin, confirmation=LIVE_CONFIRMATION)
    await db_session.commit()

    with pytest.raises(LiveCooldownActiveError) as ei:
        await svc.assert_live_enabled()
    assert ei.value.code == "live_cooldown_active"

    snap = await svc.snapshot()
    assert snap["mode"] == LIVE
    assert snap["cooldown_active"] is True
    assert snap["cooldown_seconds"] == 3600
    assert snap["cooldown_expires_at"] is not None
    assert snap["live_gate"] == "cooldown_active"


async def test_live_allowed_after_cooldown_expiry(db_session, monkeypatch):
    _set_cooldown(monkeypatch, 3600)
    admin = await _admin(db_session)
    svc = TradingModeService(db_session)
    await svc.set_mode(target=LIVE, operator=admin, confirmation=LIVE_CONFIRMATION)
    await db_session.commit()

    # Fast-forward the persisted ``armed_at`` past the cooldown horizon.
    row = await db_session.get(ExecutionSafetySetting, GLOBAL_SETTINGS_ID)
    extra = dict(row.extra or {})
    bag = dict(extra["trading_mode"])
    bag["armed_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    extra["trading_mode"] = bag
    row.extra = extra
    db_session.add(row)
    await db_session.commit()

    # Now it must succeed — the gate reads only from the DB.
    await svc.assert_live_enabled()
    snap = await svc.snapshot()
    assert snap["cooldown_active"] is False
    assert snap["live_gate"] == "requires_existing_live_safety_checks"


async def test_restart_does_not_bypass_cooldown(db_session, monkeypatch):
    """Simulate a process restart by using a fresh TradingModeService against
    the same DB row and verify the cooldown is still enforced."""
    _set_cooldown(monkeypatch, 3600)
    admin = await _admin(db_session)
    await TradingModeService(db_session).set_mode(
        target=LIVE, operator=admin, confirmation=LIVE_CONFIRMATION,
    )
    await db_session.commit()

    # Fresh service instance == fresh process startup.  DB state is the
    # only source of truth.
    fresh = TradingModeService(db_session)
    with pytest.raises(LiveCooldownActiveError):
        await fresh.assert_live_enabled()


async def test_concurrent_requests_cannot_race_past_cooldown(db_session, monkeypatch):
    """Fire N ``assert_live_enabled`` calls concurrently and confirm every
    one is rejected — the DB-backed check makes a race impossible."""
    _set_cooldown(monkeypatch, 3600)
    admin = await _admin(db_session)
    svc = TradingModeService(db_session)
    await svc.set_mode(target=LIVE, operator=admin, confirmation=LIVE_CONFIRMATION)
    await db_session.commit()

    async def _try() -> str:
        try:
            await TradingModeService(db_session).assert_live_enabled()
            return "allowed"
        except LiveCooldownActiveError:
            return "cooldown"
        except LiveTradingDisabledError:
            return "disabled"

    results = await asyncio.gather(*[_try() for _ in range(25)])
    assert results.count("cooldown") == 25
    assert "allowed" not in results


async def test_paper_to_live_confirmation_still_required_under_cooldown(db_session, monkeypatch):
    """Enabling the cooldown must not weaken the explicit-confirmation gate."""
    _set_cooldown(monkeypatch, 60)
    admin = await _admin(db_session)
    svc = TradingModeService(db_session)
    from app.services.trading_mode_service import LiveConfirmationRequiredError
    with pytest.raises(LiveConfirmationRequiredError):
        await svc.set_mode(target=LIVE, operator=admin, confirmation="nope")
    snap = await svc.snapshot()
    assert snap["mode"] == "paper"  # never armed
