"""v1.1.0 Phase 2 — Commerce (marketplace, coupons, wallet, revenue KPIs).

Revision ID: 0011_v110_phase2_commerce
Revises: 0010_v110_foundation
Create Date: 2026-02-15 20:00:00

All changes are additive: new tables only, existing schema untouched.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_v110_phase2_commerce"
down_revision: Union[str, None] = "0010_v110_foundation"
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
    # ---- coupons ------------------------------------------------------------
    op.create_table(
        "coupons",
        _uuid_pk(),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("discount_type", sa.String(length=16), nullable=False),
        sa.Column("discount_value", sa.Integer(), nullable=False),
        sa.Column("max_discount_cents", sa.Integer(), nullable=True),
        sa.Column("min_purchase_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible_plans", sa.JSON(), nullable=True),
        sa.Column("eligible_order_kinds", sa.JSON(), nullable=True),
        sa.Column("trial_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_purchase_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("one_time_per_user", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_stacking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_combined_discount_cents", sa.Integer(), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("redemptions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("coupon_metadata", sa.JSON(), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("code", name="uq_coupon_code"),
    )
    op.create_index("ix_coupons_status", "coupons", ["status"])
    op.create_index("ix_coupons_code", "coupons", ["code"])

    # ---- orders ------------------------------------------------------------
    op.create_table(
        "orders",
        _uuid_pk(),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wallet_debit_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("target_ref", sa.String(length=128), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="mock"),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("payment_reference", sa.String(length=255), nullable=True),
        sa.Column("coupon_code", sa.String(length=64), nullable=True),
        sa.Column(
            "coupon_id", sa.String(length=36),
            sa.ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_amount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("order_metadata", sa.JSON(), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_orders_user_id", "orders", ["user_id"])
    op.create_index("ix_orders_kind", "orders", ["kind"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_payment_reference", "orders", ["payment_reference"])

    # ---- coupon_redemptions -----------------------------------------------
    op.create_table(
        "coupon_redemptions",
        _uuid_pk(),
        sa.Column(
            "coupon_id", sa.String(length=36),
            sa.ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "order_id", sa.String(length=36),
            sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("discount_applied_cents", sa.Integer(), nullable=False, server_default="0"),
        *_ts_cols(),
    )
    op.create_index("ix_coupon_redemptions_coupon_id", "coupon_redemptions", ["coupon_id"])
    op.create_index("ix_coupon_redemptions_user_id", "coupon_redemptions", ["user_id"])

    # ---- wallets + wallet_transactions ------------------------------------
    op.create_table(
        "wallets",
        _uuid_pk(),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("balance_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("lifetime_earned_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lifetime_spent_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_ts_cols(),
        sa.UniqueConstraint("user_id", name="uq_wallet_user"),
    )
    op.create_table(
        "wallet_transactions",
        _uuid_pk(),
        sa.Column(
            "wallet_id", sa.String(length=36),
            sa.ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("balance_after_cents", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("reference_type", sa.String(length=64), nullable=True),
        sa.Column("reference_id", sa.String(length=64), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_wallet_txn_wallet_id", "wallet_transactions", ["wallet_id"])
    op.create_index("ix_wallet_txn_reason", "wallet_transactions", ["reason"])

    # ---- marketplace_listings ---------------------------------------------
    op.create_table(
        "marketplace_listings",
        _uuid_pk(),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column("tagline", sa.String(length=255), nullable=True),
        sa.Column("description_md", sa.String(length=4000), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allow_trial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trial_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("preview_image_url", sa.String(length=500), nullable=True),
        sa.Column("features", sa.JSON(), nullable=True),
        sa.Column("total_purchases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_revenue_cents", sa.Integer(), nullable=False, server_default="0"),
        *_ts_cols(),
        sa.UniqueConstraint("strategy_key", name="uq_marketplace_strategy_key"),
    )

    # ---- strategy_purchases -----------------------------------------------
    op.create_table(
        "strategy_purchases",
        _uuid_pk(),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("strategy_key", sa.String(length=64), nullable=False),
        sa.Column(
            "order_id", sa.String(length=36),
            sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_lifetime", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        *_ts_cols(),
    )
    op.create_index("ix_strategy_purchases_user_id", "strategy_purchases", ["user_id"])
    op.create_index("ix_strategy_purchases_strategy_key", "strategy_purchases", ["strategy_key"])

    # ---- revenue_snapshots ------------------------------------------------
    op.create_table(
        "revenue_snapshots",
        _uuid_pk(),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period", sa.String(length=24), nullable=False),
        sa.Column("mrr_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("arr_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_subscribers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_subscribers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("churned_subscribers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("arpu_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trial_signups", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trial_activations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trial_expirations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trial_conversions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trial_conversion_revenue_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coupon_redemptions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coupon_discount_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strategy_purchases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strategy_revenue_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan_distribution", sa.JSON(), nullable=True),
        sa.Column("top_coupons", sa.JSON(), nullable=True),
        sa.Column("top_strategies", sa.JSON(), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_revenue_snapshots_captured_at", "revenue_snapshots", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_revenue_snapshots_captured_at", table_name="revenue_snapshots")
    op.drop_table("revenue_snapshots")

    op.drop_index("ix_strategy_purchases_strategy_key", table_name="strategy_purchases")
    op.drop_index("ix_strategy_purchases_user_id", table_name="strategy_purchases")
    op.drop_table("strategy_purchases")

    op.drop_table("marketplace_listings")

    op.drop_index("ix_wallet_txn_reason", table_name="wallet_transactions")
    op.drop_index("ix_wallet_txn_wallet_id", table_name="wallet_transactions")
    op.drop_table("wallet_transactions")
    op.drop_table("wallets")

    op.drop_index("ix_coupon_redemptions_user_id", table_name="coupon_redemptions")
    op.drop_index("ix_coupon_redemptions_coupon_id", table_name="coupon_redemptions")
    op.drop_table("coupon_redemptions")

    op.drop_index("ix_orders_payment_reference", table_name="orders")
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_index("ix_orders_kind", table_name="orders")
    op.drop_index("ix_orders_user_id", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_coupons_code", table_name="coupons")
    op.drop_index("ix_coupons_status", table_name="coupons")
    op.drop_table("coupons")
