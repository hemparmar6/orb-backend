"""Portfolio snapshot ORM model (Module 8)."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import JSON, Date, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PortfolioSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Daily portfolio snapshot per user — used for historical analytics."""

    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "snapshot_date", name="uq_portfolio_snapshot_user_date"),
        Index("ix_portfolio_snap_user_date", "user_id", "snapshot_date"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    equity: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    day_pnl: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    exposure: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    open_positions: Mapped[int] = mapped_column(Numeric(6, 0), default=0, nullable=False)

    holdings: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
