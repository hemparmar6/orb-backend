"""Commerce domain models — Phase 2 (v1.1.0).

Adds Marketplace, Coupon, Wallet, Order, StrategyPurchase, and
RevenueSnapshot models. All new tables — no existing tables are
mutated so backward compatibility with Phase 1 is total.
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
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class OrderKind(str, enum.Enum):
    """What the user is buying."""

    SUBSCRIPTION = "subscription"
    TRIAL = "trial"
    STRATEGY = "strategy"
    WALLET_TOPUP = "wallet_topup"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class CouponDiscountType(str, enum.Enum):
    PERCENT = "percent"    # value ∈ [0, 100]
    FLAT = "flat"          # amount in cents (INR paise)


class CouponStatus(str, enum.Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    EXHAUSTED = "exhausted"
    EXPIRED = "expired"


class WalletTxnDirection(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class WalletTxnReason(str, enum.Enum):
    TRIAL_CREDIT = "trial_credit"
    COUPON = "coupon"
    REFUND = "refund"
    TOPUP = "topup"
    PURCHASE = "purchase"
    ADJUSTMENT = "adjustment"
    AFFILIATE_COMMISSION = "affiliate_commission"


# ---------------------------------------------------------------------------
# Order — unified record for every paid interaction
# ---------------------------------------------------------------------------


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_user_id", "user_id"),
        Index("ix_orders_kind", "kind"),
        Index("ix_orders_status", "status"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[OrderKind] = mapped_column(
        Enum(OrderKind, name="order_kind", native_enum=False, length=24),
        nullable=False,
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, name="order_status", native_enum=False, length=24),
        default=OrderStatus.PENDING,
        nullable=False,
    )

    # Amount snapshot (immutable once paid).
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wallet_debit_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")

    # Target reference (subscription plan_key, strategy_key, ...).
    target_ref: Mapped[Optional[str]] = mapped_column(String(128))

    # Payment provider details.
    provider: Mapped[str] = mapped_column(String(32), default="mock", nullable=False)
    provider_session_id: Mapped[Optional[str]] = mapped_column(String(255))
    payment_reference: Mapped[Optional[str]] = mapped_column(String(255), index=True)

    # Denormalised coupon fields (audit trail).
    coupon_code: Mapped[Optional[str]] = mapped_column(String(64))
    coupon_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True
    )

    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    refund_amount_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    order_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# Coupon + redemption
# ---------------------------------------------------------------------------


class Coupon(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("code", name="uq_coupon_code"),
        Index("ix_coupons_status", "status"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(500))

    discount_type: Mapped[CouponDiscountType] = mapped_column(
        Enum(CouponDiscountType, name="coupon_discount_type", native_enum=False, length=16),
        nullable=False,
    )
    discount_value: Mapped[int] = mapped_column(Integer, nullable=False)
    max_discount_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_purchase_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Eligibility knobs (admin-configurable).
    eligible_plans: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)   # ["starter", "pro"]
    eligible_order_kinds: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)  # ["subscription","trial","strategy"]
    trial_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_purchase_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    one_time_per_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Stacking config.
    allow_stacking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_combined_discount_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Global limits.
    max_redemptions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    redemptions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    status: Mapped[CouponStatus] = mapped_column(
        Enum(CouponStatus, name="coupon_status", native_enum=False, length=16),
        default=CouponStatus.ACTIVE,
        nullable=False,
    )
    coupon_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class CouponRedemption(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "coupon_redemptions"
    __table_args__ = (
        Index("ix_coupon_redemptions_coupon_id", "coupon_id"),
        Index("ix_coupon_redemptions_user_id", "user_id"),
    )

    coupon_id: Mapped[str] = mapped_column(
        ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    discount_applied_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# Wallet — user-level credit ledger
# ---------------------------------------------------------------------------


class Wallet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_wallet_user"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    balance_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    lifetime_earned_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifetime_spent_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class WalletTransaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        Index("ix_wallet_txn_wallet_id", "wallet_id"),
        Index("ix_wallet_txn_reason", "reason"),
    )

    wallet_id: Mapped[str] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[WalletTxnDirection] = mapped_column(
        Enum(WalletTxnDirection, name="wallet_txn_direction", native_enum=False, length=16),
        nullable=False,
    )
    reason: Mapped[WalletTxnReason] = mapped_column(
        Enum(WalletTxnReason, name="wallet_txn_reason", native_enum=False, length=32),
        nullable=False,
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500))
    reference_type: Mapped[Optional[str]] = mapped_column(String(64))
    reference_id: Mapped[Optional[str]] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# Strategy marketplace
# ---------------------------------------------------------------------------


class MarketplaceListing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketplace_listings"
    __table_args__ = (
        UniqueConstraint("strategy_key", name="uq_marketplace_strategy_key"),
    )

    strategy_key: Mapped[str] = mapped_column(String(64), nullable=False)
    tagline: Mapped[Optional[str]] = mapped_column(String(255))
    description_md: Mapped[Optional[str]] = mapped_column(String(4000))
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    allow_trial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    preview_image_url: Mapped[Optional[str]] = mapped_column(String(500))
    features: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    total_purchases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StrategyPurchase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "strategy_purchases"
    __table_args__ = (
        Index("ix_strategy_purchases_user_id", "user_id"),
        Index("ix_strategy_purchases_strategy_key", "strategy_key"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    strategy_key: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_lifetime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )


# ---------------------------------------------------------------------------
# Revenue snapshot — precomputed KPIs for the admin dashboard
# ---------------------------------------------------------------------------


class RevenueSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "revenue_snapshots"
    __table_args__ = (
        Index("ix_revenue_snapshots_captured_at", "captured_at"),
    )

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period: Mapped[str] = mapped_column(String(24), nullable=False)   # "daily"|"weekly"|"monthly"

    mrr_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    arr_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_subscribers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_subscribers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    churned_subscribers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    arpu_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    trial_signups: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trial_activations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trial_expirations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trial_conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trial_conversion_revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    coupon_redemptions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coupon_discount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    strategy_purchases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strategy_revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    plan_distribution: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    top_coupons: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    top_strategies: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)


__all__ = [
    "Order", "OrderKind", "OrderStatus",
    "Coupon", "CouponRedemption", "CouponDiscountType", "CouponStatus",
    "Wallet", "WalletTransaction", "WalletTxnDirection", "WalletTxnReason",
    "MarketplaceListing", "StrategyPurchase",
    "RevenueSnapshot",
]
