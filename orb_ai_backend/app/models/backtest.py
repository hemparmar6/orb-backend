"""Backtest run ORM model.

A backtest is a self-contained, read-only replay of a strategy against a
finite window of historical candles. It is deliberately decoupled from
``engine_sessions`` — no orders, positions, or trades leak into the live
paper-trading tables.

Trades, the equity curve, and the metrics summary are stored as JSON on the
same row to keep migrations simple and reads cheap.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BacktestStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class BacktestRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("ix_backtest_runs_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    symbols: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_capital: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=100000)

    status: Mapped[BacktestStatus] = mapped_column(
        Enum(BacktestStatus, name="backtest_status", native_enum=False, length=16),
        default=BacktestStatus.PENDING,
        nullable=False,
        index=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # ---- results (populated when status == COMPLETE) ----
    # Metrics summary — a flat dict of numbers (total_return, sharpe, etc).
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Trades — [{symbol, side, entry_time, entry_price, exit_time, exit_price,
    #            quantity, pnl, exit_reason, ...}]
    trades: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    # Equity curve — [{"ts": iso, "equity": float}]
    equity_curve: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
