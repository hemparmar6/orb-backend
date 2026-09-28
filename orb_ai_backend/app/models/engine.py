"""Trading engine ORM models."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.user import User


# ---- Enums ----------------------------------------------------------------


class EngineSessionStatus(str, enum.Enum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    # Task 3: a LIVE session whose broker state could not be safely reconciled
    # with ORB's persisted state on restart. The runner is NOT resumed and the
    # session is held for manual reconciliation (fail-closed). Stored as a
    # plain string (native_enum=False) so this adds no DB migration.
    NEEDS_RECONCILE = "needs_reconcile"


class ExecutionMode(str, enum.Enum):
    PAPER = "paper"
    LIVE = "live"


class OrderSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"
    SL = "sl"      # stop-loss with limit price
    SL_M = "sl_m"  # stop-loss market


class OrderProduct(str, enum.Enum):
    MIS = "mis"    # intraday
    CNC = "cnc"    # delivery
    NRML = "nrml"  # F&O carry


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# ---- EngineSession --------------------------------------------------------


class EngineSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A per-user, per-strategy trading session.

    The engine runner runs one asyncio task per RUNNING session.
    """

    __tablename__ = "engine_sessions"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Free-form reference — the built-in `demo_ma_cross` sample uses this without
    # linking to a Module 1 Strategy row.
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    status: Mapped[EngineSessionStatus] = mapped_column(
        Enum(EngineSessionStatus, name="engine_session_status", native_enum=False, length=16),
        default=EngineSessionStatus.STOPPED,
        nullable=False,
        index=True,
    )

    execution_mode: Mapped[ExecutionMode] = mapped_column(
        Enum(ExecutionMode, name="execution_mode", native_enum=False, length=8),
        default=ExecutionMode.PAPER,
        nullable=False,
        index=True,
    )
    broker_account_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    symbols: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    risk_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    initial_capital: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    day_pnl: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    orders: Mapped[List["PaperOrder"]] = relationship(
        back_populates="engine_session", cascade="all, delete-orphan"
    )
    positions: Mapped[List["PaperPosition"]] = relationship(
        back_populates="engine_session", cascade="all, delete-orphan"
    )
    trades: Mapped[List["PaperTrade"]] = relationship(
        back_populates="engine_session", cascade="all, delete-orphan"
    )


# ---- PaperOrder -----------------------------------------------------------


class PaperOrder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "paper_orders"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    engine_session_id: Mapped[str] = mapped_column(
        ForeignKey("engine_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(16), default="MOCK", nullable=False)

    side: Mapped[OrderSide] = mapped_column(
        Enum(OrderSide, name="order_side", native_enum=False, length=8), nullable=False
    )
    order_type: Mapped[OrderType] = mapped_column(
        Enum(OrderType, name="order_type", native_enum=False, length=8), nullable=False
    )
    product: Mapped[OrderProduct] = mapped_column(
        Enum(OrderProduct, name="order_product", native_enum=False, length=8),
        default=OrderProduct.MIS,
        nullable=False,
    )

    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))          # limit price
    trigger_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))  # SL trigger

    stop_loss: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    target_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status", native_enum=False, length=24),
        default=OrderStatus.PENDING,
        nullable=False,
        index=True,
    )
    filled_quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    average_fill_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))

    # Module 3: live-broker fields (nullable — paper orders leave them empty)
    broker_account_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("broker_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    strategy_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    tag: Mapped[Optional[str]] = mapped_column(String(64))  # e.g. "entry", "sl", "target"

    placed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)

    engine_session: Mapped["EngineSession"] = relationship(back_populates="orders")


# ---- PaperPosition --------------------------------------------------------


class PaperPosition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "paper_positions"
    __table_args__ = (
        UniqueConstraint(
            "engine_session_id", "symbol", "exchange", "product",
            name="uq_paper_position",
        ),
        Index("ix_paper_positions_session_symbol", "engine_session_id", "symbol"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    engine_session_id: Mapped[str] = mapped_column(
        ForeignKey("engine_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), default="MOCK", nullable=False)
    product: Mapped[OrderProduct] = mapped_column(
        Enum(OrderProduct, name="position_product", native_enum=False, length=8),
        default=OrderProduct.MIS,
        nullable=False,
    )

    # Signed: +ve = long, -ve = short, 0 = flat (closed).
    net_quantity: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    average_price: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    last_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))

    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    engine_session: Mapped["EngineSession"] = relationship(back_populates="positions")


# ---- PaperTrade (execution log; one row per fill) -------------------------


class PaperTrade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "paper_trades"
    __table_args__ = (
        Index("ix_paper_trades_session_time", "engine_session_id", "executed_at"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    engine_session_id: Mapped[str] = mapped_column(
        ForeignKey("engine_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_order_id: Mapped[str] = mapped_column(
        ForeignKey("paper_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )

    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(16), default="MOCK", nullable=False)
    side: Mapped[OrderSide] = mapped_column(
        Enum(OrderSide, name="trade_side_engine", native_enum=False, length=8), nullable=False
    )
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    realized_pnl_delta: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    strategy_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    engine_session: Mapped["EngineSession"] = relationship(back_populates="trades")
