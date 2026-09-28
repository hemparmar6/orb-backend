"""Notifications provider package (Module 8)."""

from app.services.notifications.base import (
    NotificationMessage,
    NotificationProvider,
    ProviderResult,
)
from app.services.notifications.email_resend import ResendEmailProvider
from app.services.notifications.email_smtp import SMTPEmailProvider
from app.services.notifications.push_emergent import EmergentPushProvider
from app.services.notifications.telegram import TelegramProvider

__all__ = [
    "NotificationMessage",
    "NotificationProvider",
    "ProviderResult",
    "ResendEmailProvider",
    "SMTPEmailProvider",
    "TelegramProvider",
    "EmergentPushProvider",
]
