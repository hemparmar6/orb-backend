"""SMTP email provider (fallback)."""
from __future__ import annotations

from email.message import EmailMessage

from app.core.config import settings
from app.core.logging import get_logger
from app.services.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    ProviderResult,
)

logger = get_logger(__name__)


class SMTPEmailProvider(NotificationProvider):
    name = "smtp"

    def is_configured(self) -> bool:
        return bool(
            settings.SMTP_HOST
            and settings.SMTP_USERNAME
            and settings.SMTP_PASSWORD
            and (settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME)
        )

    async def send(self, message: NotificationMessage) -> ProviderResult:
        if not self.is_configured():
            return ProviderResult(ok=False, detail="not_configured")
        if not message.to_email:
            return ProviderResult(ok=False, detail="missing_email")
        try:
            import aiosmtplib  # noqa: WPS433
        except Exception:  # pragma: no cover
            return ProviderResult(ok=False, detail="aiosmtplib_missing")

        msg = EmailMessage()
        msg["From"] = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
        msg["To"] = message.to_email
        msg["Subject"] = message.title
        msg.set_content(message.body or message.title)

        try:
            await aiosmtplib.send(
                msg,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USERNAME,
                password=settings.SMTP_PASSWORD,
                start_tls=settings.SMTP_USE_TLS,
                timeout=10,
            )
            return ProviderResult(ok=True, detail="delivered")
        except Exception as e:  # noqa: BLE001
            logger.warning("smtp_send_failed", extra={"error": str(e)})
            return ProviderResult(ok=False, detail=f"exception:{e}")
