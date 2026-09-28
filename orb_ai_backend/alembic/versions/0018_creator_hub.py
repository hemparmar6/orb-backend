"""Creator Hub — creators, creator strategies, coupon attribution.

Revision ID: 0018_creator_hub
Revises: 0017_email_verification
Create Date: 2026-09-08 14:00:00

Additive-only: 3 new tables (creators, creator_strategies,
creator_coupon_assignments). No existing tables are mutated and no
existing coupon/redemption data is touched.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_creator_hub"
down_revision: Union[str, None] = "0017_email_verification"
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
    # ---- creators ------------------------------------------------------
    op.create_table(
        "creators",
        _uuid_pk(),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("bio", sa.String(length=2000), nullable=True),
        sa.Column("social_links", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.String(length=500), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("user_id", name="uq_creator_user"),
    )
    op.create_index("ix_creators_status", "creators", ["status"])

    # ---- creator_strategies ---------------------------------------------
    op.create_table(
        "creator_strategies",
        _uuid_pk(),
        sa.Column(
            "creator_id", sa.String(length=36),
            sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("short_description", sa.String(length=500), nullable=False),
        sa.Column("trading_style", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=64), nullable=False),
        sa.Column("timeframe", sa.String(length=32), nullable=False),
        sa.Column("entry_conditions", sa.String(length=2000), nullable=False),
        sa.Column("exit_conditions", sa.String(length=2000), nullable=False),
        sa.Column("risk_management", sa.String(length=2000), nullable=False),
        sa.Column("reference_link", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_creator_strategies_creator", "creator_strategies", ["creator_id"])
    op.create_index("ix_creator_strategies_status", "creator_strategies", ["status"])

    # ---- creator_coupon_assignments --------------------------------------
    op.create_table(
        "creator_coupon_assignments",
        _uuid_pk(),
        sa.Column(
            "creator_id", sa.String(length=36),
            sa.ForeignKey("creators.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "strategy_id", sa.String(length=36),
            sa.ForeignKey("creator_strategies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "coupon_id", sa.String(length=36),
            sa.ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("assigned_by", sa.String(length=36), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("strategy_id", name="uq_creator_coupon_strategy"),
        sa.UniqueConstraint("coupon_id", name="uq_creator_coupon_coupon"),
    )
    op.create_index(
        "ix_creator_coupon_creator", "creator_coupon_assignments", ["creator_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_creator_coupon_creator", table_name="creator_coupon_assignments")
    op.drop_table("creator_coupon_assignments")
    op.drop_index("ix_creator_strategies_status", table_name="creator_strategies")
    op.drop_index("ix_creator_strategies_creator", table_name="creator_strategies")
    op.drop_table("creator_strategies")
    op.drop_index("ix_creators_status", table_name="creators")
    op.drop_table("creators")
