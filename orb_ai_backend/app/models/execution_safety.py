"""ORB AI 2.0 — Milestone 8: Execution Safety persistence models.

Three new tables + one extension:

* ``execution_safety_settings`` — singleton row (id='global') with the
  admin-configurable limits. Priority: DB row → env vars → safe defaults.
* ``execution_safety_events`` — per-order audit log for every enforcement
  decision (Allowed / Queued / Rejected / Duplicate / BotPaused / KillSwitch).
* ``execution_safety_config_audit`` — every admin change to the settings
  row (previous value, new value, admin id, reason).

Notes:
- These are additive tables. No mutations to existing tables → zero
  regression risk for M5/M6/M7.
- SQLite-friendly types are used so the pytest in-memory suite runs
  unchanged.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


# ---- enums ---------------------------------------------------------------

class ExecutionLimitType(str, enum.Enum):
    """Every distinct enforcement type. Keep in sync with the service."""

    TRADES_PER_SECOND = "trades_per_second"
    ORDERS_PER_MINUTE = "orders_per_minute"
    ORDERS_PER_HOUR = "orders_per_hour"
    DUPLICATE_ORDER = "duplicate_order"
    GLOBAL_PLATFORM_LIMIT = "global_platform_limit"
    QUEUE_FULL = "queue_full"
    EMERGENCY_STOP = "emergency_stop"
    KILL_SWITCH = "kill_switch"


class ExecutionSafetyAction(str, enum.Enum):
    """What the enforcer decided to do."""

    ALLOWED = "allowed"
    QUEUED = "queued"
    REJECTED = "rejected"
    DUPLICATE_BLOCKED = "duplicate_blocked"
    BOT_PAUSED = "bot_paused"
    KILL_SWITCH_ACTIVATED = "kill_switch_activated"


# ---- settings -----------------------------------------------------------

class ExecutionSafetySetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Singleton row with the admin-configurable execution-safety limits.

    Always addressed by ``id = 'global'`` (see service). Storing it as a
    normal row (not env-only) so admins can change values from the panel
    without a restart. Environment variables provide deployment defaults;
    if this row is absent, the service falls back to env → safe defaults.
    """

    __tablename__ = "execution_safety_settings"

    # Per-user limits
    trades_per_second: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    orders_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    orders_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)

    # Global platform limits (across all users, protects broker fan-in)
    global_orders_per_second: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    global_orders_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)

    # Duplicate detection
    duplicate_window_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    duplicate_action: Mapped[str] = mapped_column(String(16), nullable=False, default="reject")
    # "reject" | "queue"

    # Queue manager
    queue_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    queue_max_size: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    queue_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=30.0)

    # Bot auto-pause
    auto_pause_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_pause_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    auto_pause_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    # Kill switch
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kill_switch_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    kill_switch_activated_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    kill_switch_activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Event retention (days) — used by the pruning task
    event_retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)

    # Free-form extension bag for future settings without a schema change
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


# ---- events / audit -----------------------------------------------------

class ExecutionSafetyEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per enforcement decision — the audit log for the admin
    monitoring dashboard.
    """

    __tablename__ = "execution_safety_events"

    user_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True,
    )
    bot_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("bots.id", ondelete="SET NULL"), index=True, nullable=True,
    )
    strategy_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    engine_session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    broker: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    order_type: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    endpoint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    limit_type: Mapped[ExecutionLimitType] = mapped_column(
        Enum(ExecutionLimitType, name="execution_limit_type", native_enum=False, length=32),
        nullable=False,
        index=True,
    )
    action: Mapped[ExecutionSafetyAction] = mapped_column(
        Enum(ExecutionSafetyAction, name="execution_safety_action", native_enum=False, length=32),
        nullable=False,
        index=True,
    )

    current_counter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    configured_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retry_after_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    fingerprint: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ExecutionSafetyConfigAudit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per admin change to ``ExecutionSafetySetting``."""

    __tablename__ = "execution_safety_config_audit"

    admin_user_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
