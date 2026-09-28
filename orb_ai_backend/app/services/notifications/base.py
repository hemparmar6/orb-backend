"""Notification provider abstraction."""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class NotificationMessage:
    """Provider-agnostic message envelope."""

    user_id: str
    event: str
    severity: str
    title: str
    body: str
    payload: Optional[dict[str, Any]] = None
    to_email: Optional[str] = None
    to_telegram_chat_id: Optional[str] = None
    to_push_token: Optional[str] = None


@dataclass
class ProviderResult:
    ok: bool
    detail: str = ""


class NotificationProvider(abc.ABC):
    """Base class for all notification providers."""

    name: str = "base"

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """Return True if this provider has enough config to run."""

    @abc.abstractmethod
    async def send(self, message: NotificationMessage) -> ProviderResult:
        """Send the message. Must not raise; return ProviderResult."""
