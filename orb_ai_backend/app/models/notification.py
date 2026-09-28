"""Notification and NotificationPreference ORM models (Module 8)."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NotificationChannel(str, enum.Enum):
    IN_APP = "in_app"
    EMAIL = "email"
    TELEGRAM = "telegram"
    PUSH = "push"


class NotificationEvent(str, enum.Enum):
    TRADE_EXECUTED = "trade_executed"
    STOP_LOSS_HIT = "stop_loss_hit"
    TARGET_ACHIEVED = "target_achieved"
    BROKER_DISCONNECTED = "broker_disconnected"
    STRATEGY_STOPPED = "strategy_stopped"
    SYSTEM_ALERT = "system_alert"
    # ---- Milestone 9 (Risk Management) ------------------------------
    RISK_BREACH = "risk_breach"
    DAILY_LOSS_REACHED = "daily_loss_reached"
    MAX_TRADES_REACHED = "max_trades_reached"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    LIVE_TRADING_DISABLED = "live_trading_disabled"
    PAPER_MODE_FORCED = "paper_mode_forced"
    BOT_AUTO_PAUSED = "bot_auto_paused"
    EMERGENCY_KILL_SWITCH = "emergency_kill_switch"


class NotificationSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event: Mapped[NotificationEvent] = mapped_column(
        Enum(NotificationEvent, name="notification_event", native_enum=False, length=32),
        nullable=False,
        index=True,
    )
    severity: Mapped[NotificationSeverity] = mapped_column(
        Enum(NotificationSeverity, name="notification_severity", native_enum=False, length=16),
        default=NotificationSeverity.INFO,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Per-channel delivery status snapshot (updated by the dispatcher).
    channel_status: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class NotificationPreference(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_notif_pref_user"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64))
    push_token: Mapped[Optional[str]] = mapped_column(String(255))

    # Per-event opt-in overrides { event_name: bool }
    event_overrides: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
