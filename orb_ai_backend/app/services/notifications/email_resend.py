"""Resend email provider."""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    ProviderResult,
)

logger = get_logger(__name__)


class ResendEmailProvider(NotificationProvider):
    name = "resend"
    API_URL = "https://api.resend.com/emails"

    def is_configured(self) -> bool:
        return bool(settings.RESEND_API_KEY)

    async def send(self, message: NotificationMessage) -> ProviderResult:
        if not self.is_configured():
            return ProviderResult(ok=False, detail="not_configured")
        if not message.to_email:
            return ProviderResult(ok=False, detail="missing_email")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    self.API_URL,
                    headers={
                        "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": settings.RESEND_FROM_EMAIL,
                        "to": [message.to_email],
                        "subject": message.title,
                        "text": message.body,
                    },
                )
            if 200 <= r.status_code < 300:
                return ProviderResult(ok=True, detail=str(r.status_code))
            return ProviderResult(ok=False, detail=f"http_{r.status_code}:{r.text[:120]}")
        except Exception as e:  # noqa: BLE001
            logger.warning("resend_send_failed", extra={"error": str(e)})
            return ProviderResult(ok=False, detail=f"exception:{e}")
