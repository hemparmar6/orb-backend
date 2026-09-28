"""Creator Hub models — influencers/strategy creators and their strategies.

Attribution chain (no duplicated commerce logic):

    Creator → CreatorStrategy → CreatorCouponAssignment → existing Coupon
        → existing CouponRedemption → existing Order / Subscription

The existing ``Coupon`` / ``CouponRedemption`` tables remain the single
source of truth for validity, discounts, expiry, usage limits, and
redemption. The assignment table below only records WHO a coupon is
attributed to, for creator reporting.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CreatorStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"  # mirrors the existing AffiliateStatus lifecycle


class CreatorStrategyStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Creator profile
# ---------------------------------------------------------------------------


class Creator(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "creators"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_creator_user"),
        Index("ix_creators_status", "status"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bio: Mapped[Optional[str]] = mapped_column(String(2000))
    social_links: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    status: Mapped[CreatorStatus] = mapped_column(
        Enum(CreatorStatus, name="creator_status", native_enum=False, length=16),
        nullable=False,
        default=CreatorStatus.PENDING,
    )
    approved_by: Mapped[Optional[str]] = mapped_column(String(36))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[Optional[str]] = mapped_column(String(500))


# ---------------------------------------------------------------------------
# Creator strategy
# ---------------------------------------------------------------------------


class CreatorStrategy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "creator_strategies"
    __table_args__ = (
        Index("ix_creator_strategies_creator", "creator_id"),
        Index("ix_creator_strategies_status", "status"),
    )

    creator_id: Mapped[str] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    short_description: Mapped[str] = mapped_column(String(500), nullable=False)
    trading_style: Mapped[str] = mapped_column(String(64), nullable=False)
    market: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_conditions: Mapped[str] = mapped_column(String(2000), nullable=False)
    exit_conditions: Mapped[str] = mapped_column(String(2000), nullable=False)
    risk_management: Mapped[str] = mapped_column(String(2000), nullable=False)
    reference_link: Mapped[Optional[str]] = mapped_column(String(500))

    status: Mapped[CreatorStrategyStatus] = mapped_column(
        Enum(CreatorStrategyStatus, name="creator_strategy_status", native_enum=False, length=16),
        nullable=False,
        default=CreatorStrategyStatus.PENDING,
    )
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(36))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[Optional[str]] = mapped_column(String(500))


# ---------------------------------------------------------------------------
# Creator ↔ existing coupon attribution
# ---------------------------------------------------------------------------


class CreatorCouponAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Links an approved creator strategy to an EXISTING coupon.

    No discount/validity/redemption data lives here — only the
    attribution relationship. A coupon maps to at most one strategy and
    a strategy has at most one assigned coupon.
    """

    __tablename__ = "creator_coupon_assignments"
    __table_args__ = (
        UniqueConstraint("strategy_id", name="uq_creator_coupon_strategy"),
        UniqueConstraint("coupon_id", name="uq_creator_coupon_coupon"),
        Index("ix_creator_coupon_creator", "creator_id"),
    )

    creator_id: Mapped[str] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("creator_strategies.id", ondelete="CASCADE"), nullable=False
    )
    coupon_id: Mapped[str] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by: Mapped[Optional[str]] = mapped_column(String(36))


__all__ = [
    "Creator",
    "CreatorStatus",
    "CreatorStrategy",
    "CreatorStrategyStatus",
    "CreatorCouponAssignment",
]
