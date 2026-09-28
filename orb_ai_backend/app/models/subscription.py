"""Subscription plan and user-subscription models.

v1.1.0 additions are **strictly additive**: every new column is nullable
(or has a server-side default) so existing v1.0.0 rows load unchanged.

PlanTier gains STARTER and ELITE (string enum — additive, safe).
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
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlanTier(str, enum.Enum):
    # v1.0.0
    FREE = "free"          # deprecated in ORB AI 2.0 — retained for backward-compat
    PRO = "pro"
    ENTERPRISE = "enterprise"
    # v1.1.0 — commercialization tiers
    STARTER = "starter"
    ELITE = "elite"
    # ORB AI 2.0 — canonical tiers (post subscription-update)
    STANDARD = "standard"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"
    INCOMPLETE = "incomplete"
    # v1.1.0
    GRACE_PERIOD = "grace_period"
    TRIAL_EXPIRED = "trial_expired"


class SubscriptionPlan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A plan describes a tier and the feature flags it enables."""

    __tablename__ = "subscription_plans"
    __table_args__ = (
        UniqueConstraint("key", name="uq_plan_key"),
    )

    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[PlanTier] = mapped_column(
        Enum(PlanTier, name="plan_tier", native_enum=False, length=16),
        nullable=False,
    )
    price_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    interval: Mapped[str] = mapped_column(String(16), default="monthly", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    provider: Mapped[str] = mapped_column(String(32), default="noop", nullable=False)
    provider_price_id: Mapped[Optional[str]] = mapped_column(String(255))
    features: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # ---- v1.1.0 plan limits (all nullable/defaulted for backward-compat) ----
    description: Mapped[Optional[str]] = mapped_column(String(2000))
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_running_bots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    paper_trading_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ai_features_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # -1 means unlimited (admin-configurable). Applies to bots & positions.
    unlimited_bots: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UserSubscription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_subscription_user"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("subscription_plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status", native_enum=False, length=24),
        default=SubscriptionStatus.ACTIVE,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), default="noop", nullable=False)
    provider_customer_id: Mapped[Optional[str]] = mapped_column(String(255))
    provider_subscription_id: Mapped[Optional[str]] = mapped_column(String(255))

    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    feature_overrides: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # ---- v1.1.0 trial state (all nullable — backward-compat) ----
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    trial_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    trial_consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    trial_credit_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grace_period_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Per-user runtime limit overrides (admin can override plan defaults)
    limit_overrides: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
