"""Trade ORM model."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.strategy import Strategy
    from app.models.user import User


class TradeSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(str, enum.Enum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Trade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single trade record.

    Broker order IDs and live execution flow will be added in a later module;
    this table stores the *logical* trade as tracked by ORB AI.
    """

    __tablename__ = "trades"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    side: Mapped[TradeSide] = mapped_column(
        Enum(TradeSide, name="trade_side", native_enum=False, length=8),
        nullable=False,
    )
    quantity: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)

    entry_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    exit_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    stop_loss: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    take_profit: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))

    pnl: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))

    status: Mapped[TradeStatus] = mapped_column(
        Enum(TradeStatus, name="trade_status", native_enum=False, length=16),
        default=TradeStatus.PENDING,
        nullable=False,
        index=True,
    )

    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    broker: Mapped[Optional[str]] = mapped_column(String(64))
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    notes: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="trades")
    strategy: Mapped[Optional["Strategy"]] = relationship(back_populates="trades")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Trade id={self.id} symbol={self.symbol} side={self.side} "
            f"status={self.status}>"
        )
