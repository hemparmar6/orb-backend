"""Strategy ORM model."""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import JSON, Boolean, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.trade import Trade
    from app.models.user import User


class StrategyStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Strategy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-defined trading strategy.

    Note: this is only the *definition* record. Trading logic and broker
    execution are intentionally out of scope for Module 1.
    """

    __tablename__ = "strategies"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    # Free-form JSON so the trading engine (future module) can evolve without
    # schema migrations for every rule change.
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    status: Mapped[StrategyStatus] = mapped_column(
        Enum(StrategyStatus, name="strategy_status", native_enum=False, length=16),
        default=StrategyStatus.DRAFT,
        nullable=False,
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped["User"] = relationship(back_populates="strategies")
    trades: Mapped[List["Trade"]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Strategy id={self.id} name={self.name} status={self.status}>"
