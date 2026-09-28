"""Affiliate platform models — Phase 3 (v1.1.0).

Adds Affiliate, AffiliateProgram (global config), Campaign,
MarketingAsset, ReferralClick, ReferralAttribution, ReferralEvent,
Commission, Payout, and FraudFlag models. All new tables — no
existing tables mutated so backward compatibility with Phase 1 & 2
is total.
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
# Enums
# ---------------------------------------------------------------------------


class AffiliateStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class AttributionModel(str, enum.Enum):
    FIRST_TOUCH = "first_touch"
    LAST_TOUCH = "last_touch"


class ReferralEventType(str, enum.Enum):
    CLICK = "click"
    LANDING = "landing"
    REGISTRATION = "registration"
    EMAIL_VERIFICATION = "email_verification"
    TRIAL_ACTIVATION = "trial_activation"
    COUPON_USE = "coupon_use"
    SUBSCRIPTION_PURCHASE = "subscription_purchase"
    UPGRADE = "upgrade"
    RENEWAL = "renewal"


class CommissionStatus(str, enum.Enum):
    PENDING = "pending"          # created, awaiting hold period
    APPROVED = "approved"        # cleared for payout (in wallet)
    PAID = "paid"                # settled via payout
    REVERSED = "reversed"        # refunded / fraud
    REJECTED = "rejected"        # admin-rejected


class PayoutStatus(str, enum.Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"


class PayoutMethod(str, enum.Enum):
    BANK = "bank"
    UPI = "upi"
    PAYPAL = "paypal"
    WALLET_ONLY = "wallet_only"  # stays in ORB Wallet, no external transfer


class AssetType(str, enum.Enum):
    IMAGE = "image"
    BANNER = "banner"
    PDF = "pdf"
    COPY = "copy"       # short marketing text snippet
    VIDEO = "video"
    LINK = "link"


class FraudSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Global affiliate program config
# ---------------------------------------------------------------------------


class AffiliateProgram(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Singleton row — global affiliate program config.

    We store the config as a row rather than settings so admins can
    change it at runtime through the dashboard.
    """

    __tablename__ = "affiliate_program"

    # Default commission rate applied when neither affiliate nor campaign
    # has an override. Percent of the paid amount (0-100).
    default_commission_rate_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=20)

    # Attribution windows (days).
    click_attribution_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    trial_attribution_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    paid_attribution_days: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    # first_touch vs last_touch model.
    attribution_model: Mapped[AttributionModel] = mapped_column(
        Enum(AttributionModel, name="attribution_model", native_enum=False, length=16),
        nullable=False, default=AttributionModel.LAST_TOUCH,
    )

    # Fraud + policy switches.
    block_self_referral: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    hold_period_days: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    min_payout_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=50000)  # ₹500
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")


# ---------------------------------------------------------------------------
# Affiliate profile
# ---------------------------------------------------------------------------


class Affiliate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "affiliates"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_affiliate_user"),
        UniqueConstraint("code", name="uq_affiliate_code"),
        Index("ix_affiliates_status", "status"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(128))

    status: Mapped[AffiliateStatus] = mapped_column(
        Enum(AffiliateStatus, name="affiliate_status", native_enum=False, length=16),
        nullable=False, default=AffiliateStatus.PENDING,
    )
    application_notes: Mapped[Optional[str]] = mapped_column(String(2000))
    approved_by: Mapped[Optional[str]] = mapped_column(String(36))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rejected_reason: Mapped[Optional[str]] = mapped_column(String(500))

    # Per-affiliate overrides.
    commission_rate_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Payout details (kept simple — encrypt PII at rest in production).
    payout_method: Mapped[PayoutMethod] = mapped_column(
        Enum(PayoutMethod, name="payout_method", native_enum=False, length=16),
        nullable=False, default=PayoutMethod.WALLET_ONLY,
    )
    payout_details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    # Aggregates for cheap reads (kept eventually-consistent).
    total_clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_signups: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_commission_earned_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_commission_paid_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------
# Campaigns + marketing assets
# ---------------------------------------------------------------------------


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "affiliate_campaigns"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_campaign_slug"),
        Index("ix_campaigns_affiliate", "affiliate_id"),
    )

    # nullable → global campaign (admin-owned)
    affiliate_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(2000))

    landing_url: Mapped[Optional[str]] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Optional override for commission (percent).
    commission_rate_pct: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Aggregates.
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    signup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversion_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MarketingAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "marketing_assets"
    __table_args__ = (
        Index("ix_marketing_assets_campaign", "campaign_id"),
    )

    # nullable → global asset.
    affiliate_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=True
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("affiliate_campaigns.id", ondelete="CASCADE"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(
        Enum(AssetType, name="asset_type", native_enum=False, length=16),
        nullable=False, default=AssetType.IMAGE,
    )
    # Either a URL or an inline data:// URI (base64) for small images.
    content_url: Mapped[Optional[str]] = mapped_column(String(2000))
    body: Mapped[Optional[str]] = mapped_column(String(4000))
    mime_type: Mapped[Optional[str]] = mapped_column(String(64))
    dimensions: Mapped[Optional[str]] = mapped_column(String(32))
    tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ---------------------------------------------------------------------------
# Referral tracking
# ---------------------------------------------------------------------------


class ReferralClick(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "referral_clicks"
    __table_args__ = (
        Index("ix_referral_clicks_affiliate", "affiliate_id"),
        Index("ix_referral_clicks_campaign", "campaign_id"),
        Index("ix_referral_clicks_cookie", "cookie_id"),
    )

    affiliate_id: Mapped[str] = mapped_column(
        ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("affiliate_campaigns.id", ondelete="SET NULL"), nullable=True
    )

    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(500))
    device_fingerprint: Mapped[Optional[str]] = mapped_column(String(128))
    referrer: Mapped[Optional[str]] = mapped_column(String(500))
    landing_url: Mapped[Optional[str]] = mapped_column(String(500))
    cookie_id: Mapped[Optional[str]] = mapped_column(String(64))
    utm_source: Mapped[Optional[str]] = mapped_column(String(64))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(64))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(96))

    is_fraudulent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ReferralAttribution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single user's attribution to an affiliate + campaign.

    One row per (user_id, attribution slot). Under first-touch we keep
    the earliest attribution; under last-touch we upsert with the
    latest click within the window.
    """

    __tablename__ = "referral_attributions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_attribution_user"),
        Index("ix_attribution_affiliate", "affiliate_id"),
        Index("ix_attribution_expires", "expires_at"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    affiliate_id: Mapped[str] = mapped_column(
        ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("affiliate_campaigns.id", ondelete="SET NULL"), nullable=True
    )
    click_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("referral_clicks.id", ondelete="SET NULL"), nullable=True
    )

    model: Mapped[AttributionModel] = mapped_column(
        Enum(AttributionModel, name="attribution_model_used", native_enum=False, length=16),
        nullable=False, default=AttributionModel.LAST_TOUCH,
    )
    attributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ReferralEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "referral_events"
    __table_args__ = (
        Index("ix_referral_events_affiliate", "affiliate_id"),
        Index("ix_referral_events_user", "user_id"),
        Index("ix_referral_events_type", "event_type"),
    )

    affiliate_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("affiliates.id", ondelete="SET NULL"), nullable=True
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("affiliate_campaigns.id", ondelete="SET NULL"), nullable=True
    )
    attribution_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("referral_attributions.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    event_type: Mapped[ReferralEventType] = mapped_column(
        Enum(ReferralEventType, name="referral_event_type", native_enum=False, length=32),
        nullable=False,
    )
    revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# Commission + payout
# ---------------------------------------------------------------------------


class Commission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "commissions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_commission_idempotency"),
        Index("ix_commissions_affiliate", "affiliate_id"),
        Index("ix_commissions_status", "status"),
    )

    affiliate_id: Mapped[str] = mapped_column(
        ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False
    )
    attribution_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("referral_attributions.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )

    event_type: Mapped[ReferralEventType] = mapped_column(
        Enum(ReferralEventType, name="commission_event_type", native_enum=False, length=32),
        nullable=False,
    )
    base_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")

    status: Mapped[CommissionStatus] = mapped_column(
        Enum(CommissionStatus, name="commission_status", native_enum=False, length=16),
        nullable=False, default=CommissionStatus.PENDING,
    )
    scheduled_release_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reversed_reason: Mapped[Optional[str]] = mapped_column(String(500))

    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(String(500))


class Payout(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "affiliate_payouts"
    __table_args__ = (
        Index("ix_payouts_affiliate", "affiliate_id"),
        Index("ix_payouts_status", "status"),
    )

    affiliate_id: Mapped[str] = mapped_column(
        ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    method: Mapped[PayoutMethod] = mapped_column(
        Enum(PayoutMethod, name="payout_method_used", native_enum=False, length=16),
        nullable=False, default=PayoutMethod.WALLET_ONLY,
    )
    method_details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    status: Mapped[PayoutStatus] = mapped_column(
        Enum(PayoutStatus, name="payout_status", native_enum=False, length=16),
        nullable=False, default=PayoutStatus.REQUESTED,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    transaction_ref: Mapped[Optional[str]] = mapped_column(String(255))
    admin_id: Mapped[Optional[str]] = mapped_column(String(36))
    notes: Mapped[Optional[str]] = mapped_column(String(1000))


# ---------------------------------------------------------------------------
# Fraud flags
# ---------------------------------------------------------------------------


class FraudFlag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "affiliate_fraud_flags"
    __table_args__ = (
        Index("ix_fraud_flags_subject", "subject_type", "subject_id"),
        Index("ix_fraud_flags_active", "is_active"),
    )

    subject_type: Mapped[str] = mapped_column(String(24), nullable=False)   # affiliate|user|click|commission
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[FraudSeverity] = mapped_column(
        Enum(FraudSeverity, name="fraud_severity", native_enum=False, length=8),
        nullable=False, default=FraudSeverity.MEDIUM,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


__all__ = [
    "AffiliateProgram", "Affiliate", "AffiliateStatus",
    "Campaign", "MarketingAsset", "AssetType",
    "ReferralClick", "ReferralAttribution", "ReferralEvent",
    "ReferralEventType", "AttributionModel",
    "Commission", "CommissionStatus",
    "Payout", "PayoutStatus", "PayoutMethod",
    "FraudFlag", "FraudSeverity",
]
