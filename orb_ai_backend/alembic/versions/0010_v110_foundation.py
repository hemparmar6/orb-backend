"""v1.1.0 Foundation — plan limits, trial state, strategy catalog.

Revision ID: 0010_v110_foundation
Revises: 0009_module10
Create Date: 2026-02-15 18:00:00

All changes are additive:
* Extends subscription_plans with limit + description columns.
* Extends user_subscriptions with trial columns and limit_overrides.
* Adds new table strategy_catalog.

Existing v1.0.0 rows continue to work — every new column is nullable
or has a server-side default.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_v110_foundation"
down_revision: Union[str, None] = "0009_module10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- subscription_plans: additive columns ----
    with op.batch_alter_table("subscription_plans") as batch:
        batch.add_column(sa.Column("description", sa.String(length=2000), nullable=True))
        batch.add_column(sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_running_bots", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_open_positions", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("automation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("paper_trading_only", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("ai_features_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("unlimited_bots", sa.Boolean(), nullable=False, server_default=sa.false()))

    # ---- user_subscriptions: trial state + overrides ----
    with op.batch_alter_table("user_subscriptions") as batch:
        batch.add_column(sa.Column("is_trial", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("trial_consumed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("trial_credit_cents", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("grace_period_ends_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("limit_overrides", sa.JSON(), nullable=True))

    # ---- strategy_catalog: new table ----
    op.create_table(
        "strategy_catalog",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="general"),
        sa.Column("difficulty", sa.String(length=16), nullable=False, server_default="intermediate"),
        sa.Column("risk_level", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("supported_markets", sa.JSON(), nullable=True),
        sa.Column("supported_timeframes", sa.JSON(), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="1.0.0"),
        sa.Column("min_plan_tier", sa.String(length=16), nullable=False, server_default="free"),
        sa.Column("automation_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ai_compatible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paper_trading_supported", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("live_trading_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("performance_stats", sa.JSON(), nullable=True),
        sa.Column("default_params", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("key", name="uq_strategy_catalog_key"),
    )
    op.create_index("ix_strategy_catalog_key", "strategy_catalog", ["key"])
    op.create_index("ix_strategy_catalog_status", "strategy_catalog", ["status"])
    op.create_index("ix_strategy_catalog_min_plan_tier", "strategy_catalog", ["min_plan_tier"])


def downgrade() -> None:
    op.drop_index("ix_strategy_catalog_min_plan_tier", table_name="strategy_catalog")
    op.drop_index("ix_strategy_catalog_status", table_name="strategy_catalog")
    op.drop_index("ix_strategy_catalog_key", table_name="strategy_catalog")
    op.drop_table("strategy_catalog")

    with op.batch_alter_table("user_subscriptions") as batch:
        batch.drop_column("limit_overrides")
        batch.drop_column("grace_period_ends_at")
        batch.drop_column("trial_credit_cents")
        batch.drop_column("trial_consumed_at")
        batch.drop_column("trial_ends_at")
        batch.drop_column("trial_started_at")
        batch.drop_column("is_trial")

    with op.batch_alter_table("subscription_plans") as batch:
        batch.drop_column("unlimited_bots")
        batch.drop_column("ai_features_enabled")
        batch.drop_column("paper_trading_only")
        batch.drop_column("automation_enabled")
        batch.drop_column("max_open_positions")
        batch.drop_column("max_running_bots")
        batch.drop_column("display_order")
        batch.drop_column("description")
