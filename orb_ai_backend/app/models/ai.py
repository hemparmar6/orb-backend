"""Module 9 — AI Trading Intelligence ORM models.

All models use the project's UUID PK / timestamp mixins and reference
existing tables (``users.id``, ``trades.id``, ``strategies.id``) — no
existing schema is altered.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AITradeReview(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_trade_reviews"

    trade_id: Mapped[str] = mapped_column(
        ForeignKey("trades.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    trade_quality_score: Mapped[Optional[float]] = mapped_column(Float)
    entry_quality: Mapped[Optional[float]] = mapped_column(Float)
    exit_quality: Mapped[Optional[float]] = mapped_column(Float)
    risk_management_score: Mapped[Optional[float]] = mapped_column(Float)
    rule_compliance: Mapped[Optional[float]] = mapped_column(Float)

    emotional_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    improvements: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary: Mapped[Optional[str]] = mapped_column(Text)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="primary")

    __table_args__ = (
        UniqueConstraint("trade_id", "prompt_version", name="uq_ai_trade_review_trade_ver"),
    )


class AIRecommendation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_recommendations"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="medium")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    acted_on_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_ai_rec_user_status", "user_id", "status"),
    )


class AIAuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_audit_log"

    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    request_type: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # SQLAlchemy attribute is ``meta_data`` because ``metadata`` is reserved.
    meta_data: Mapped[dict] = mapped_column("meta", JSON, nullable=False, default=dict)


class OptimisationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "optim_jobs"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    params_space: Mapped[dict] = mapped_column(JSON, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[Optional[str]] = mapped_column(Text)


class OptimisationResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "optim_results"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("optim_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_best: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_optim_results_job_rank", "job_id", "rank"),
    )


class MarketIntelligence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mi_snapshots"

    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, default="D1")
    regime: Mapped[str] = mapped_column(String(16), nullable=False)
    trend_strength: Mapped[float] = mapped_column(Float, nullable=False)
    volatility_regime: Mapped[str] = mapped_column(String(16), nullable=False)
    liquidity: Mapped[str] = mapped_column(String(16), nullable=False)
    gap_behaviour: Mapped[Optional[str]] = mapped_column(String(16))
    session_stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )


class AIAnalyticsSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_analytics_snapshots"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_ref_id: Mapped[Optional[str]] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    equity_curve: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )
