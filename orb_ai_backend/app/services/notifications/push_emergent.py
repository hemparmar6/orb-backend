"""Emergent-managed push provider (mobile builds only)."""
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


class EmergentPushProvider(NotificationProvider):
    name = "emergent_push"

    def is_configured(self) -> bool:
        return bool(
            settings.EMERGENT_PUSH_ENABLED
            and settings.EMERGENT_PUSH_ENDPOINT
            and settings.EMERGENT_PUSH_API_KEY
        )

    async def send(self, message: NotificationMessage) -> ProviderResult:
        if not self.is_configured():
            return ProviderResult(ok=False, detail="not_configured")
        if not message.to_push_token:
            return ProviderResult(ok=False, detail="missing_push_token")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    settings.EMERGENT_PUSH_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {settings.EMERGENT_PUSH_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "to": message.to_push_token,
                        "title": message.title,
                        "body": message.body,
                        "data": message.payload or {},
                    },
                )
            if 200 <= r.status_code < 300:
                return ProviderResult(ok=True, detail=str(r.status_code))
            return ProviderResult(ok=False, detail=f"http_{r.status_code}")
        except Exception as e:  # noqa: BLE001
            logger.warning("emergent_push_send_failed", extra={"error": str(e)})
            return ProviderResult(ok=False, detail=f"exception:{e}")
