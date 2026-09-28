"""StrategyCatalog model — Strategy Marketplace metadata (v1.1.0).

This table holds the *catalog* view of every strategy shipped with the
platform. It is orthogonal to :class:`app.models.strategy.Strategy`
(user-authored strategy configurations). The engine's in-process
strategy registry (``app.engine.strategy.registry``) supplies the
executable class; this table supplies subscription metadata and admin
toggles.

A pending / not-yet-implemented strategy remains in the catalog with
``status = IMPLEMENTATION_PENDING`` and never executes live trades —
the PermissionService (Phase 1) refuses to start any bot whose strategy
is not ACTIVE.
"""
from __future__ import annotations

import enum
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StrategyStatus(str, enum.Enum):
    ACTIVE = "active"
    IMPLEMENTATION_PENDING = "implementation_pending"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class StrategyRiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StrategyDifficulty(str, enum.Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class StrategyCatalog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_catalog"
    __table_args__ = (
        UniqueConstraint("key", name="uq_strategy_catalog_key"),
    )

    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(2000))
    category: Mapped[str] = mapped_column(String(64), default="general", nullable=False)
    difficulty: Mapped[StrategyDifficulty] = mapped_column(
        String(16), default=StrategyDifficulty.INTERMEDIATE.value, nullable=False
    )
    risk_level: Mapped[StrategyRiskLevel] = mapped_column(
        String(16), default=StrategyRiskLevel.MEDIUM.value, nullable=False
    )
    supported_markets: Mapped[Optional[list[str]]] = mapped_column(JSON)
    supported_timeframes: Mapped[Optional[list[str]]] = mapped_column(JSON)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    # Minimum plan required. Values map to PlanTier ("free"|"standard"|"starter"|"pro"|"elite").
    # ORB AI 2.0 defaults new templates to "standard" (the entry paid tier).
    min_plan_tier: Mapped[str] = mapped_column(String(16), default="standard", nullable=False)
    automation_supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_compatible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paper_trading_supported: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    live_trading_supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[StrategyStatus] = mapped_column(
        String(24), default=StrategyStatus.ACTIVE.value, nullable=False
    )
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Free-form performance snapshot (win rate, avg return, sharpe, etc.).
    performance_stats: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    # Free-form default parameters (mirrors what the executable class expects).
    default_params: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
