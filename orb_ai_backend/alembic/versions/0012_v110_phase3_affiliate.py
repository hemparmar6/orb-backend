"""v1.1.0 Phase 3 — Affiliate platform.

Revision ID: 0012_v110_phase3_affiliate
Revises: 0011_v110_phase2_commerce
Create Date: 2026-02-15 22:00:00

Additive-only: 10 new tables, no schema mutations.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_v110_phase3_affiliate"
down_revision: Union[str, None] = "0011_v110_phase2_commerce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk():
    return sa.Column("id", sa.String(length=36), primary_key=True)


def _ts_cols():
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
    )


def upgrade() -> None:
    # ---- affiliate_program (singleton) ---------------------------------
    op.create_table(
        "affiliate_program",
        _uuid_pk(),
        sa.Column("default_commission_rate_pct", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("click_attribution_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("trial_attribution_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("paid_attribution_days", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("attribution_model", sa.String(length=16), nullable=False, server_default="last_touch"),
        sa.Column("block_self_referral", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("hold_period_days", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("min_payout_cents", sa.Integer(), nullable=False, server_default="50000"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        *_ts_cols(),
    )

    # ---- affiliates -----------------------------------------------------
    op.create_table(
        "affiliates",
        _uuid_pk(),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("application_notes", sa.String(length=2000), nullable=True),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.String(length=500), nullable=True),
        sa.Column("commission_rate_pct", sa.Integer(), nullable=True),
        sa.Column("payout_method", sa.String(length=16), nullable=False, server_default="wallet_only"),
        sa.Column("payout_details", sa.JSON(), nullable=True),
        sa.Column("total_clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_signups", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_conversions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_commission_earned_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_commission_paid_cents", sa.Integer(), nullable=False, server_default="0"),
        *_ts_cols(),
        sa.UniqueConstraint("user_id", name="uq_affiliate_user"),
        sa.UniqueConstraint("code", name="uq_affiliate_code"),
    )
    op.create_index("ix_affiliates_status", "affiliates", ["status"])

    # ---- affiliate_campaigns -------------------------------------------
    op.create_table(
        "affiliate_campaigns",
        _uuid_pk(),
        sa.Column(
            "affiliate_id", sa.String(length=36),
            sa.ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=96), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("landing_url", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("commission_rate_pct", sa.Integer(), nullable=True),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signup_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversion_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue_cents", sa.Integer(), nullable=False, server_default="0"),
        *_ts_cols(),
        sa.UniqueConstraint("slug", name="uq_campaign_slug"),
    )
    op.create_index("ix_campaigns_affiliate", "affiliate_campaigns", ["affiliate_id"])

    # ---- marketing_assets ----------------------------------------------
    op.create_table(
        "marketing_assets",
        _uuid_pk(),
        sa.Column(
            "affiliate_id", sa.String(length=36),
            sa.ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column(
            "campaign_id", sa.String(length=36),
            sa.ForeignKey("affiliate_campaigns.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False, server_default="image"),
        sa.Column("content_url", sa.String(length=2000), nullable=True),
        sa.Column("body", sa.String(length=4000), nullable=True),
        sa.Column("mime_type", sa.String(length=64), nullable=True),
        sa.Column("dimensions", sa.String(length=32), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts_cols(),
    )
    op.create_index("ix_marketing_assets_campaign", "marketing_assets", ["campaign_id"])

    # ---- referral_clicks -----------------------------------------------
    op.create_table(
        "referral_clicks",
        _uuid_pk(),
        sa.Column(
            "affiliate_id", sa.String(length=36),
            sa.ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "campaign_id", sa.String(length=36),
            sa.ForeignKey("affiliate_campaigns.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("device_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("referrer", sa.String(length=500), nullable=True),
        sa.Column("landing_url", sa.String(length=500), nullable=True),
        sa.Column("cookie_id", sa.String(length=64), nullable=True),
        sa.Column("utm_source", sa.String(length=64), nullable=True),
        sa.Column("utm_medium", sa.String(length=64), nullable=True),
        sa.Column("utm_campaign", sa.String(length=96), nullable=True),
        sa.Column("is_fraudulent", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_ts_cols(),
    )
    op.create_index("ix_referral_clicks_affiliate", "referral_clicks", ["affiliate_id"])
    op.create_index("ix_referral_clicks_campaign", "referral_clicks", ["campaign_id"])
    op.create_index("ix_referral_clicks_cookie", "referral_clicks", ["cookie_id"])

    # ---- referral_attributions -----------------------------------------
    op.create_table(
        "referral_attributions",
        _uuid_pk(),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "affiliate_id", sa.String(length=36),
            sa.ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "campaign_id", sa.String(length=36),
            sa.ForeignKey("affiliate_campaigns.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "click_id", sa.String(length=36),
            sa.ForeignKey("referral_clicks.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("model", sa.String(length=16), nullable=False, server_default="last_touch"),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("user_id", name="uq_attribution_user"),
    )
    op.create_index("ix_attribution_affiliate", "referral_attributions", ["affiliate_id"])
    op.create_index("ix_attribution_expires", "referral_attributions", ["expires_at"])

    # ---- referral_events -----------------------------------------------
    op.create_table(
        "referral_events",
        _uuid_pk(),
        sa.Column(
            "affiliate_id", sa.String(length=36),
            sa.ForeignKey("affiliates.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "campaign_id", sa.String(length=36),
            sa.ForeignKey("affiliate_campaigns.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "attribution_id", sa.String(length=36),
            sa.ForeignKey("referral_attributions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("revenue_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_metadata", sa.JSON(), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_referral_events_affiliate", "referral_events", ["affiliate_id"])
    op.create_index("ix_referral_events_user", "referral_events", ["user_id"])
    op.create_index("ix_referral_events_type", "referral_events", ["event_type"])

    # ---- commissions ---------------------------------------------------
    op.create_table(
        "commissions",
        _uuid_pk(),
        sa.Column(
            "affiliate_id", sa.String(length=36),
            sa.ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "attribution_id", sa.String(length=36),
            sa.ForeignKey("referral_attributions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "order_id", sa.String(length=36),
            sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("base_amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("scheduled_release_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_reason", sa.String(length=500), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("idempotency_key", name="uq_commission_idempotency"),
    )
    op.create_index("ix_commissions_affiliate", "commissions", ["affiliate_id"])
    op.create_index("ix_commissions_status", "commissions", ["status"])

    # ---- affiliate_payouts ---------------------------------------------
    op.create_table(
        "affiliate_payouts",
        _uuid_pk(),
        sa.Column(
            "affiliate_id", sa.String(length=36),
            sa.ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("method", sa.String(length=16), nullable=False, server_default="wallet_only"),
        sa.Column("method_details", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="requested"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transaction_ref", sa.String(length=255), nullable=True),
        sa.Column("admin_id", sa.String(length=36), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_payouts_affiliate", "affiliate_payouts", ["affiliate_id"])
    op.create_index("ix_payouts_status", "affiliate_payouts", ["status"])

    # ---- affiliate_fraud_flags -----------------------------------------
    op.create_table(
        "affiliate_fraud_flags",
        _uuid_pk(),
        sa.Column("subject_type", sa.String(length=24), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False, server_default="medium"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("details", sa.JSON(), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_fraud_flags_subject", "affiliate_fraud_flags", ["subject_type", "subject_id"])
    op.create_index("ix_fraud_flags_active", "affiliate_fraud_flags", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_fraud_flags_active", table_name="affiliate_fraud_flags")
    op.drop_index("ix_fraud_flags_subject", table_name="affiliate_fraud_flags")
    op.drop_table("affiliate_fraud_flags")

    op.drop_index("ix_payouts_status", table_name="affiliate_payouts")
    op.drop_index("ix_payouts_affiliate", table_name="affiliate_payouts")
    op.drop_table("affiliate_payouts")

    op.drop_index("ix_commissions_status", table_name="commissions")
    op.drop_index("ix_commissions_affiliate", table_name="commissions")
    op.drop_table("commissions")

    op.drop_index("ix_referral_events_type", table_name="referral_events")
    op.drop_index("ix_referral_events_user", table_name="referral_events")
    op.drop_index("ix_referral_events_affiliate", table_name="referral_events")
    op.drop_table("referral_events")

    op.drop_index("ix_attribution_expires", table_name="referral_attributions")
    op.drop_index("ix_attribution_affiliate", table_name="referral_attributions")
    op.drop_table("referral_attributions")

    op.drop_index("ix_referral_clicks_cookie", table_name="referral_clicks")
    op.drop_index("ix_referral_clicks_campaign", table_name="referral_clicks")
    op.drop_index("ix_referral_clicks_affiliate", table_name="referral_clicks")
    op.drop_table("referral_clicks")

    op.drop_index("ix_marketing_assets_campaign", table_name="marketing_assets")
    op.drop_table("marketing_assets")

    op.drop_index("ix_campaigns_affiliate", table_name="affiliate_campaigns")
    op.drop_table("affiliate_campaigns")

    op.drop_index("ix_affiliates_status", table_name="affiliates")
    op.drop_table("affiliates")

    op.drop_table("affiliate_program")
