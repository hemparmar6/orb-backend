"""Notification retry task (Module 8).

Picks up notifications whose last dispatch failed on at least one
provider and re-attempts delivery. Bounded by ``delivery_attempts``.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.notification import Notification, NotificationEvent, NotificationSeverity

logger = get_logger(__name__)


async def retry_failed_notifications() -> int:
    """Return the number of notifications retried."""
    from app.services.notification_service import NotificationService
    from app.models.user import User

    async with async_session_factory() as session:
        stmt = select(Notification).where(
            Notification.delivery_attempts < settings.NOTIFICATIONS_MAX_RETRIES * 2
        )
        rows = list((await session.execute(stmt.limit(50))).scalars().all())
        retried = 0
        for n in rows:
            status = n.channel_status or {}
            needs_retry = any(
                isinstance(v, str) and v.startswith("failed:")
                for v in status.values()
            )
            if not needs_retry:
                continue
            user = await session.get(User, n.user_id)
            if user is None:
                continue
            try:
                await NotificationService(session).notify(
                    user=user,
                    event=NotificationEvent(n.event),
                    title=n.title,
                    body=n.body,
                    severity=NotificationSeverity(n.severity),
                    payload=n.payload,
                )
                retried += 1
            except Exception:  # pragma: no cover
                logger.exception("retry_failed", extra={"notification_id": n.id})
        await session.commit()
    if retried:
        logger.info("notifications_retried", extra={"count": retried})
    return retried


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(retry_failed_notifications())
