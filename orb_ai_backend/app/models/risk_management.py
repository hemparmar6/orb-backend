"""ORB AI 2.0 — Milestone 9: Risk Management persistence models.

Two additive tables:

* ``risk_limits`` — one row per user; the consolidated risk-profile for
  Milestone 9 (daily loss, daily profit, max trades/day, max positions,
  max exposure per symbol, trading window, live-mode-enabled, etc.).
* ``risk_breaches`` — append-only audit of every breach + remediation.

Zero mutation to existing tables → backward compatible with Milestones 1-8.
SQLite-friendly types (String/Integer/Numeric/Boolean/JSON/DateTime)
so the pytest in-memory suite runs unchanged.
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
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


# ---- enums ---------------------------------------------------------------


class RiskEventType(str, enum.Enum):
    """Every distinct M9 breach type."""

    DAILY_LOSS_LIMIT = "daily_loss_limit"
    DAILY_PROFIT_TARGET = "daily_profit_target"
    MAX_TRADES_PER_DAY = "max_trades_per_day"
    MAX_CONSECUTIVE_LOSSES = "max_consecutive_losses"
    MAX_CAPITAL_ALLOCATION = "max_capital_allocation"
    MAX_POSITION_SIZE = "max_position_size"
    MAX_OPEN_POSITIONS = "max_open_positions"
    MAX_EXPOSURE_PER_SYMBOL = "max_exposure_per_symbol"
    TRADING_SESSION_HOURS = "trading_session_hours"
    LIVE_TRADING_DISABLED = "live_trading_disabled"
    PAPER_MODE_FORCED = "paper_mode_forced"
    BOTS_PAUSED = "bots_paused"
    BOTS_RESUMED = "bots_resumed"
    MANUAL_BLOCK = "manual_block"


class RiskSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RiskAction(str, enum.Enum):
    """What the enforcement engine did about it."""

    ALLOWED = "allowed"
    BLOCKED = "blocked"
    WARNED = "warned"
    BOTS_PAUSED = "bots_paused"
    LIVE_DISABLED = "live_disabled"
    PAPER_FORCED = "paper_forced"


class RiskConfigActorType(str, enum.Enum):
    """Who initiated a RiskLimit change — used by the audit table."""

    USER = "user"        # end-user edited their own limits
    ADMIN = "admin"      # admin edited (may be their own; still audited)
    SYSTEM = "system"    # system-driven (auto-flip on breach)


# ---- risk_limits (one row per user) --------------------------------------


class RiskLimit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Consolidated per-user risk profile.

    Every field is optional — a None value means the rule is disabled for
    this user. Global defaults (env / admin config) apply only when there
    is *no* row for this user; there is no partial fallback per-field.

    ``live_trading_enabled`` flips off automatically on serious breaches
    and can be turned back on explicitly. ``force_paper_mode`` similarly
    routes all new bot orders through the paper executor.
    """

    __tablename__ = "risk_limits"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_risk_limits_user"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    # --- Money limits (in the account currency, absolute values) ----------
    daily_loss_limit: Mapped[Optional[float]] = mapped_column(Numeric(18, 4), nullable=True)
    daily_profit_target: Mapped[Optional[float]] = mapped_column(Numeric(18, 4), nullable=True)
    max_capital_allocation: Mapped[Optional[float]] = mapped_column(Numeric(18, 4), nullable=True)
    max_position_size: Mapped[Optional[float]] = mapped_column(Numeric(18, 4), nullable=True)
    max_exposure_per_symbol: Mapped[Optional[float]] = mapped_column(Numeric(18, 4), nullable=True)

    # --- Count limits ------------------------------------------------------
    max_trades_per_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_consecutive_losses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_open_positions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- Trading session window (HH:MM in the given IANA timezone) --------
    trading_session_start: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    trading_session_end: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    trading_session_timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # --- Live-trading control --------------------------------------------
    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    force_paper_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Notification routing (per-severity opt-in) -----------------------
    notify_channels: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=lambda: {
            "in_app": True, "email": True, "push": True, "telegram": False,
        },
    )
    notify_severity_min: Mapped[str] = mapped_column(
        String(16), nullable=False, default="warning",
    )

    # --- Free-form extension bag ------------------------------------------
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


# ---- risk_breaches (append-only audit) -----------------------------------


class RiskBreach(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One row per breach — even INFO-level so the admin dashboard can chart it."""

    __tablename__ = "risk_breaches"
    __table_args__ = (
        Index("ix_risk_breaches_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    bot_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("bots.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    symbol: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    event_type: Mapped[RiskEventType] = mapped_column(
        Enum(RiskEventType, name="risk_event_type", native_enum=False, length=32),
        nullable=False, index=True,
    )
    severity: Mapped[RiskSeverity] = mapped_column(
        Enum(RiskSeverity, name="risk_severity", native_enum=False, length=16),
        nullable=False, default=RiskSeverity.WARNING,
    )
    action_taken: Mapped[RiskAction] = mapped_column(
        Enum(RiskAction, name="risk_action", native_enum=False, length=24),
        nullable=False, default=RiskAction.BLOCKED,
    )

    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    triggered_value: Mapped[Optional[float]] = mapped_column(Numeric(20, 6), nullable=True)
    threshold: Mapped[Optional[float]] = mapped_column(Numeric(20, 6), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    # When resolved (e.g. next day rollover, admin override), stamp here.
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notification_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


# ---- risk_limits_audit (append-only config-change trail) -----------------


class RiskLimitAudit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Milestone 9 follow-up — every mutation of a ``RiskLimit`` field.

    One row per changed field per admin/user action. Populated by
    :class:`RiskManagementService.upsert_limit`.
    """

    __tablename__ = "risk_limits_audit"
    __table_args__ = (
        Index("ix_risk_limits_audit_user_created",
              "target_user_id", "created_at"),
    )

    # The user whose RiskLimit was changed
    target_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # Actor (nullable → SYSTEM-initiated changes)
    actor_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    actor_type: Mapped[RiskConfigActorType] = mapped_column(
        Enum(RiskConfigActorType, name="risk_config_actor_type",
             native_enum=False, length=16),
        nullable=False, default=RiskConfigActorType.USER,
    )

    field: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
