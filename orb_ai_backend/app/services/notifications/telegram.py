"""Telegram bot notification provider."""
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


class TelegramProvider(NotificationProvider):
    name = "telegram"

    def is_configured(self) -> bool:
        return bool(settings.TELEGRAM_BOT_TOKEN)

    async def send(self, message: NotificationMessage) -> ProviderResult:
        if not self.is_configured():
            return ProviderResult(ok=False, detail="not_configured")
        chat_id = message.to_telegram_chat_id or settings.TELEGRAM_CHAT_ID
        if not chat_id:
            return ProviderResult(ok=False, detail="missing_chat_id")
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        text = f"*{message.title}*\n{message.body}" if message.body else message.title
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                })
            if 200 <= r.status_code < 300:
                return ProviderResult(ok=True, detail=str(r.status_code))
            return ProviderResult(ok=False, detail=f"http_{r.status_code}:{r.text[:120]}")
        except Exception as e:  # noqa: BLE001
            logger.warning("telegram_send_failed", extra={"error": str(e)})
            return ProviderResult(ok=False, detail=f"exception:{e}")
