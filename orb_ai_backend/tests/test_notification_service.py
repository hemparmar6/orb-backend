"""Notification service unit tests."""
from __future__ import annotations

import pytest

from app.models.notification import NotificationEvent, NotificationSeverity
from app.services.notification_service import NotificationService
from app.services.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    ProviderResult,
)
from tests._module8_helpers import register_and_login


class _MockProvider(NotificationProvider):
    name = "mock"
    sent: list[NotificationMessage] = []
    should_fail: int = 0  # fail N times then succeed

    def is_configured(self) -> bool:
        return True

    async def send(self, message):
        self.sent.append(message)
        if _MockProvider.should_fail > 0:
            _MockProvider.should_fail -= 1
            return ProviderResult(ok=False, detail="mock_transient")
        return ProviderResult(ok=True, detail="ok")


@pytest.mark.asyncio
async def test_notify_persists_and_broadcasts(client, db_session):
    _, headers = await register_and_login(client)
    from app.models.user import User
    from sqlalchemy import select
    u = (await db_session.execute(select(User))).scalars().first()

    svc = NotificationService(db_session)
    notif = await svc.notify(
        user=u,
        event=NotificationEvent.TRADE_EXECUTED,
        title="Trade Filled",
        body="AAPL 10 @ 100",
        severity=NotificationSeverity.INFO,
    )
    await db_session.commit()
    assert notif.id is not None
    # No providers configured — channel_status should reflect that
    assert notif.channel_status["in_app"] == "delivered"


@pytest.mark.asyncio
async def test_notify_retries_transient_failures(client, db_session, monkeypatch):
    _, headers = await register_and_login(client)
    from app.models.user import User
    from sqlalchemy import select
    u = (await db_session.execute(select(User))).scalars().first()

    # Enable email in prefs & inject a mock email provider that fails twice
    svc = NotificationService(db_session)
    await svc.update_preference(u.id, email_enabled=True)
    await db_session.flush()

    _MockProvider.sent.clear()
    _MockProvider.should_fail = 2
    monkeypatch.setattr(svc, "_email_provider", lambda: _MockProvider())

    notif = await svc.notify(
        user=u,
        event=NotificationEvent.SYSTEM_ALERT,
        title="Retry me",
        body="test",
    )
    await db_session.commit()
    # 2 failures then success = 3 attempts. delivery_attempts counts all attempts across channels.
    assert notif.delivery_attempts >= 3
    assert notif.channel_status["email"] == "sent"
    assert len(_MockProvider.sent) == 3
