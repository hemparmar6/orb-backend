"""Phase 4 — Bot Management, Kill Switch, Circuit Breaker ORM models (v1.1.0)."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


# ----- Enums --------------------------------------------------------------

class BotStatus(str, enum.Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    KILLED = "killed"
    BREAKER_TRIPPED = "breaker_tripped"


class BreakerLevel(str, enum.Enum):
    USER = "user"
    STRATEGY = "strategy"
    BROKER = "broker"
    GLOBAL = "global"


class BreakerType(str, enum.Enum):
    # user-level
    MAX_DAILY_LOSS = "max_daily_loss"
    MAX_DAILY_PROFIT = "max_daily_profit"
    MAX_RUNNING_BOTS = "max_running_bots"
    MAX_OPEN_POSITIONS = "max_open_positions"
    MAX_CONSECUTIVE_LOSSES = "max_consecutive_losses"
    TRADING_WINDOW = "trading_window"
    # strategy-level
    MAX_DRAWDOWN = "max_drawdown"
    STRATEGY_COOLDOWN = "strategy_cooldown"
    STRATEGY_AUTO_PAUSE = "strategy_auto_pause"
    # broker-level
    API_FAILURE_THRESHOLD = "api_failure_threshold"
    DISCONNECT_DETECTION = "disconnect_detection"
    ORDER_REJECTION_THRESHOLD = "order_rejection_threshold"
    BROKER_HEALTH_MONITORING = "broker_health_monitoring"
    # global
    GLOBAL_KILL_SWITCH = "global_kill_switch"
    HOLIDAY_LOCK = "holiday_lock"
    EMERGENCY_STOP = "emergency_stop"
    MAINTENANCE_MODE = "maintenance_mode"


class KillSwitchScope(str, enum.Enum):
    BOT = "bot"
    USER = "user"
    GLOBAL = "global"


# ----- Bot ---------------------------------------------------------------

class Bot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user-owned automation wrapper around an ``EngineSession``.

    A Bot is the operational unit surfaced to end-users. It holds a
    friendly name, tag set, target strategy + symbols + params, current
    aggregate state, and a link to the running EngineSession (0-or-1
    live sessions at any time per bot).
    """

    __tablename__ = "bots"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_bot_user_name"),
        Index("ix_bots_user_status", "user_id", "status"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    strategy_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbols: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    risk_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    execution_mode: Mapped[str] = mapped_column(String(16), default="paper", nullable=False)
    broker_account_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    initial_capital: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    status: Mapped[BotStatus] = mapped_column(
        Enum(BotStatus, name="bot_status", native_enum=False, length=24),
        default=BotStatus.IDLE, nullable=False, index=True,
    )
    engine_session_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("engine_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # aggregate KPIs (rolled up from paper_trades)
    day_pnl_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_pnl_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trades_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    peak_pnl_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # for drawdown

    tags: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    # Kill switch flag (fast in-DB gate; the KillSwitchEvent row is the log).
    is_killed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ----- CircuitBreakerConfig ---------------------------------------------

class CircuitBreakerConfig(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Admin-configurable breaker.

    Scope precedence when multiple configs match:
    ``bot > user > strategy > broker > global``.

    ``value`` semantics per ``breaker_type`` (units in the ``unit`` col
    for docs; the service enforces numeric/JSON as appropriate):
      - max_daily_loss / max_daily_profit: cents (int)
      - max_running_bots / max_open_positions / max_consecutive_losses: count (int)
      - trading_window: {"start": "HH:MM", "end": "HH:MM", "tz": "..."}
      - max_drawdown: percent (float, 0-100)
      - strategy_cooldown / disconnect_detection: seconds (int)
      - api_failure_threshold / order_rejection_threshold: count/window seconds
    """

    __tablename__ = "circuit_breaker_configs"
    __table_args__ = (
        Index("ix_cbc_level_type", "level", "breaker_type"),
    )

    level: Mapped[BreakerLevel] = mapped_column(
        Enum(BreakerLevel, name="breaker_level", native_enum=False, length=16),
        nullable=False, index=True,
    )
    breaker_type: Mapped[BreakerType] = mapped_column(
        Enum(BreakerType, name="breaker_type", native_enum=False, length=48),
        nullable=False, index=True,
    )

    # Optional targeting (nullable = applies to all in that level).
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    strategy_key: Mapped[Optional[str]] = mapped_column(String(64))
    broker_type: Mapped[Optional[str]] = mapped_column(String(32))
    bot_id: Mapped[Optional[str]] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    value_num: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    value_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    unit: Mapped[Optional[str]] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32), default="pause", nullable=False)
    # "pause" | "stop" | "kill" | "block_new" | "warn"

    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notify: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    notes: Mapped[Optional[str]] = mapped_column(Text)


# ----- CircuitBreakerEvent (audit trail) --------------------------------

class CircuitBreakerEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "circuit_breaker_events"
    __table_args__ = (
        Index("ix_cbe_time", "created_at"),
        Index("ix_cbe_user_time", "user_id", "created_at"),
    )

    config_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("circuit_breaker_configs.id", ondelete="SET NULL")
    )
    level: Mapped[BreakerLevel] = mapped_column(
        Enum(BreakerLevel, name="breaker_level_ev", native_enum=False, length=16),
        nullable=False,
    )
    breaker_type: Mapped[BreakerType] = mapped_column(
        Enum(BreakerType, name="breaker_type_ev", native_enum=False, length=48),
        nullable=False,
    )
    user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    bot_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    strategy_key: Mapped[Optional[str]] = mapped_column(String(64))
    broker_type: Mapped[Optional[str]] = mapped_column(String(32))

    triggered_value: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    threshold: Mapped[Optional[float]] = mapped_column(Numeric(20, 6))
    action_taken: Mapped[str] = mapped_column(String(32), default="pause", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    event_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ----- KillSwitchEvent --------------------------------------------------

class KillSwitchEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "kill_switch_events"
    __table_args__ = (
        Index("ix_kse_scope_time", "scope", "created_at"),
    )

    scope: Mapped[KillSwitchScope] = mapped_column(
        Enum(KillSwitchScope, name="kill_switch_scope", native_enum=False, length=16),
        nullable=False, index=True,
    )
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    target_user_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    target_bot_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    close_positions: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bots_stopped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    positions_closed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    event_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)


# ----- BotAuditLog (append-only, per-bot lifecycle log) -----------------

class BotAuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bot_audit_logs"
    __table_args__ = (
        Index("ix_bal_bot_time", "bot_id", "created_at"),
    )

    bot_id: Mapped[str] = mapped_column(
        ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_user_id: Mapped[Optional[str]] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    # e.g. "create","start","pause","resume","stop","kill","breaker_trip",
    # "breaker_resolve","update_config","auto_recover","error"
    previous_status: Mapped[Optional[str]] = mapped_column(String(24))
    new_status: Mapped[Optional[str]] = mapped_column(String(24))
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
