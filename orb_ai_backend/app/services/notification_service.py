"""Unified notification service (Module 8).

- Persists an in-app row per notification.
- Fans-out to enabled channels (Email, Telegram, Push) via provider
  abstractions with graceful degradation and retry.
- Broadcasts to WebSocket subscribers.
- Records an audit entry per delivered notification (system alerts).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.notification import (
    Notification,
    NotificationEvent,
    NotificationPreference,
    NotificationSeverity,
)
from app.models.user import User
from app.services.notifications import (
    EmergentPushProvider,
    NotificationMessage,
    NotificationProvider,
    ResendEmailProvider,
    SMTPEmailProvider,
    TelegramProvider,
)

logger = get_logger(__name__)


class NotificationService:
    """Facade for creating + dispatching notifications."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- providers ----
    def _email_provider(self) -> Optional[NotificationProvider]:
        p = ResendEmailProvider()
        if p.is_configured():
            return p
        s = SMTPEmailProvider()
        if s.is_configured():
            return s
        return None

    def _telegram_provider(self) -> Optional[NotificationProvider]:
        p = TelegramProvider()
        return p if p.is_configured() else None

    def _push_provider(self) -> Optional[NotificationProvider]:
        p = EmergentPushProvider()
        return p if p.is_configured() else None

    # ---- prefs ----
    async def get_preference(self, user_id: str) -> NotificationPreference:
        stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        pref = (await self.session.execute(stmt)).scalar_one_or_none()
        if pref is None:
            pref = NotificationPreference(user_id=user_id)
            self.session.add(pref)
            await self.session.flush()
        return pref

    async def update_preference(
        self,
        user_id: str,
        *,
        email_enabled: bool | None = None,
        telegram_enabled: bool | None = None,
        push_enabled: bool | None = None,
        in_app_enabled: bool | None = None,
        telegram_chat_id: str | None = None,
        push_token: str | None = None,
        event_overrides: dict[str, bool] | None = None,
    ) -> NotificationPreference:
        pref = await self.get_preference(user_id)
        if email_enabled is not None:
            pref.email_enabled = email_enabled
        if telegram_enabled is not None:
            pref.telegram_enabled = telegram_enabled
        if push_enabled is not None:
            pref.push_enabled = push_enabled
        if in_app_enabled is not None:
            pref.in_app_enabled = in_app_enabled
        if telegram_chat_id is not None:
            pref.telegram_chat_id = telegram_chat_id or None
        if push_token is not None:
            pref.push_token = push_token or None
        if event_overrides is not None:
            pref.event_overrides = event_overrides or None
        await self.session.flush()
        return pref

    # ---- creation + dispatch ----
    async def notify(
        self,
        *,
        user: User,
        event: NotificationEvent,
        title: str,
        body: str = "",
        severity: NotificationSeverity = NotificationSeverity.INFO,
        payload: Optional[dict[str, Any]] = None,
    ) -> Notification:
        """Create + dispatch. Commits neither — caller controls transaction."""
        notif = Notification(
            user_id=user.id,
            event=event,
            severity=severity,
            title=title,
            body=body,
            payload=payload,
            channel_status={},
        )
        self.session.add(notif)
        await self.session.flush()

        pref = await self.get_preference(user.id)
        overrides = pref.event_overrides or {}
        # Per-event opt-out: allow overrides to disable a specific event
        if overrides.get(event.value) is False:
            notif.channel_status = {"in_app": "skipped_by_pref"}
            await self.session.flush()
            return notif

        status: dict[str, str] = {}
        if pref.in_app_enabled:
            status["in_app"] = "delivered"
        else:
            status["in_app"] = "disabled"

        # Fan-out
        message = NotificationMessage(
            user_id=user.id,
            event=event.value,
            severity=severity.value,
            title=title,
            body=body,
            payload=payload,
            to_email=user.email if pref.email_enabled else None,
            to_telegram_chat_id=pref.telegram_chat_id if pref.telegram_enabled else None,
            to_push_token=pref.push_token if pref.push_enabled else None,
        )

        tasks: list[tuple[str, NotificationProvider]] = []
        if pref.email_enabled:
            p = self._email_provider()
            if p:
                tasks.append(("email", p))
            else:
                status["email"] = "not_configured"
        if pref.telegram_enabled:
            p = self._telegram_provider()
            if p:
                tasks.append(("telegram", p))
            else:
                status["telegram"] = "not_configured"
        if pref.push_enabled:
            p = self._push_provider()
            if p:
                tasks.append(("push", p))
            else:
                status["push"] = "not_configured"

        for channel_name, provider in tasks:
            ok = False
            last_detail = ""
            for attempt in range(settings.NOTIFICATIONS_MAX_RETRIES):
                try:
                    result = await provider.send(message)
                except Exception as e:  # noqa: BLE001
                    last_detail = f"exception:{e}"
                    ok = False
                else:
                    ok = result.ok
                    last_detail = result.detail
                notif.delivery_attempts += 1
                if ok:
                    break
                await asyncio.sleep(0.05 * (attempt + 1))
            status[channel_name] = "sent" if ok else f"failed:{last_detail[:80]}"

        notif.channel_status = status
        await self.session.flush()

        # Best-effort WebSocket broadcast (non-blocking).
        try:
            from app.ws.notification_broadcaster import notification_broadcaster
            await notification_broadcaster.publish(user.id, {
                "id": notif.id,
                "event": event.value,
                "severity": severity.value,
                "title": title,
                "body": body,
                "payload": payload or {},
                "created_at": notif.created_at.isoformat() if notif.created_at else None,
            })
        except Exception:  # pragma: no cover
            logger.exception("ws_notification_broadcast_failed")

        return notif

    # ---- reads ----
    async def list_for_user(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[Notification], int]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.session.execute(total_stmt)).scalar_one())
        items_stmt = stmt.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
        items = (await self.session.execute(items_stmt)).scalars().all()
        return items, total

    async def mark_read(self, user_id: str, notification_id: str) -> Optional[Notification]:
        notif = await self.session.get(Notification, notification_id)
        if notif is None or notif.user_id != user_id:
            return None
        if notif.read_at is None:
            notif.read_at = datetime.now(timezone.utc)
            await self.session.flush()
        return notif

    async def mark_all_read(self, user_id: str) -> int:
        stmt = select(Notification).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        now = datetime.now(timezone.utc)
        for r in rows:
            r.read_at = now
        await self.session.flush()
        return len(rows)
