"""Report run ORM model (Module 8)."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ReportType(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    PORTFOLIO = "portfolio"
    TRADE_HISTORY = "trade_history"
    BACKTEST = "backtest"
    STRATEGY_PERFORMANCE = "strategy_performance"
    RISK = "risk"
    BROKER_ACTIVITY = "broker_activity"
    PNL = "pnl"


class ReportFormat(str, enum.Enum):
    PDF = "pdf"
    CSV = "csv"
    JSON = "json"
    XLSX = "xlsx"


class ReportStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATED = "generated"
    FAILED = "failed"


class ReportRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_runs"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_type: Mapped[ReportType] = mapped_column(
        Enum(ReportType, name="report_type", native_enum=False, length=32),
        nullable=False,
        index=True,
    )
    report_format: Mapped[ReportFormat] = mapped_column(
        Enum(ReportFormat, name="report_format", native_enum=False, length=8),
        default=ReportFormat.PDF,
        nullable=False,
    )
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status", native_enum=False, length=16),
        default=ReportStatus.PENDING,
        nullable=False,
    )
    params: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    row_count: Mapped[int] = mapped_column(default=0, nullable=False)
    byte_size: Mapped[int] = mapped_column(default=0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    filename: Mapped[Optional[str]] = mapped_column(String(255))
