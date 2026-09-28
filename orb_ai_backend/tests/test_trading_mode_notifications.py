"""Trading Master Switch — admin notification regression suite.

Ensures every successful PAPER ↔ LIVE transition produces a best-effort
``system_alert`` notification, that failed notifications never roll back
the mode change, and that denied transitions do NOT produce a normal
mode-change notification (they remain auditable via the audit trail).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.notification import (
    Notification,
    NotificationEvent,
    NotificationSeverity,
)
from app.models.user import User, UserRole
from app.services.trading_mode_service import (
    LIVE,
    LIVE_CONFIRMATION,
    PAPER,
    LiveConfirmationRequiredError,
    TradingModeService,
)


pytestmark = pytest.mark.asyncio


async def _admin(db, email: str = "notif-admin@example.com") -> User:
    u = User(
        email=email,
        hashed_password=hash_password("x"),
        full_name="Notif Admin",
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
    )
    db.add(u)
    await db.flush()
    return u


async def _mode_change_notifications(db) -> list[Notification]:
    stmt = select(Notification).where(
        Notification.event == NotificationEvent.SYSTEM_ALERT,
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [n for n in rows if (n.payload or {}).get("new_mode")]


async def test_paper_to_live_notifies_admin(db_session):
    admin = await _admin(db_session, "notif-admin-live@example.com")
    await TradingModeService(db_session).set_mode(
        target=LIVE, operator=admin, confirmation=LIVE_CONFIRMATION, reason="drill",
    )
    await db_session.commit()

    notifs = await _mode_change_notifications(db_session)
    assert len(notifs) == 1
    n = notifs[0]
    assert n.user_id == admin.id
    assert n.severity == NotificationSeverity.WARNING
    assert n.title == "Trading mode → LIVE"
    assert n.payload["previous_mode"] == PAPER
    assert n.payload["new_mode"] == LIVE
    assert n.payload["operator_id"] == admin.id
    assert n.payload["reason"] == "drill"
    assert n.payload["changed_at"] is not None


async def test_live_to_paper_notifies_admin(db_session):
    admin = await _admin(db_session, "notif-admin-paper@example.com")
    svc = TradingModeService(db_session)
    await svc.set_mode(target=LIVE, operator=admin, confirmation=LIVE_CONFIRMATION)
    await svc.set_mode(target=PAPER, operator=admin, reason="close for day")
    await db_session.commit()

    notifs = await _mode_change_notifications(db_session)
    # Two transitions ⇒ two admin notifications (paper→live, live→paper).
    assert len(notifs) == 2
    latest = max(notifs, key=lambda n: n.created_at)
    assert latest.title == "Trading mode → PAPER"
    assert latest.severity == NotificationSeverity.INFO
    assert latest.payload["previous_mode"] == LIVE
    assert latest.payload["new_mode"] == PAPER


async def test_denied_transitions_do_not_produce_mode_change_notification(db_session):
    admin = await _admin(db_session, "notif-admin-denied@example.com")
    svc = TradingModeService(db_session)
    with pytest.raises(LiveConfirmationRequiredError):
        await svc.set_mode(target=LIVE, operator=admin, confirmation="wrong")
    # The denial commits its own audit row; refresh state and check.
    notifs = await _mode_change_notifications(db_session)
    assert notifs == []


async def test_notification_failure_does_not_roll_back_mode_change(db_session):
    """If NotificationService.notify() throws, the mode change must remain."""
    admin = await _admin(db_session, "notif-admin-fail@example.com")
    svc = TradingModeService(db_session)

    with patch(
        "app.services.notification_service.NotificationService.notify",
        side_effect=RuntimeError("SMTP boom"),
    ):
        snap = await svc.set_mode(target=LIVE, operator=admin, confirmation=LIVE_CONFIRMATION)
        await db_session.commit()

    assert snap["mode"] == LIVE
    # Fresh service to re-read the row after commit.
    fresh_snap = await TradingModeService(db_session).snapshot()
    assert fresh_snap["mode"] == LIVE
    # No mode-change notification persisted because the send threw.
    notifs = await _mode_change_notifications(db_session)
    assert notifs == []
