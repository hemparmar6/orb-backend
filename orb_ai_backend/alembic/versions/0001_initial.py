"""Initial schema: users, user_settings, strategies, trades.

Revision ID: 0001_initial
Revises:
Create Date: 2026-02-01 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- users ----
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_refresh_jti", sa.String(length=64), nullable=True),
        sa.Column("refresh_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ---- user_settings ----
    op.create_table(
        "user_settings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="INR"),
        sa.Column("theme", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("notifications_email", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notifications_push", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_broker", sa.String(length=64), nullable=True),
        sa.Column("max_daily_loss", sa.Numeric(18, 4), nullable=True),
        sa.Column("max_position_size", sa.Numeric(18, 4), nullable=True),
        sa.Column("risk_per_trade_pct", sa.Numeric(6, 3), nullable=True),
        sa.Column("preferences", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_user_settings_id", "user_settings", ["id"])
    op.create_index("ix_user_settings_user_id", "user_settings", ["user_id"], unique=True)

    # ---- strategies ----
    op.create_table(
        "strategies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_strategies_id", "strategies", ["id"])
    op.create_index("ix_strategies_user_id", "strategies", ["user_id"])

    # ---- trades ----
    op.create_table(
        "trades",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("exit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("stop_loss", sa.Numeric(18, 4), nullable=True),
        sa.Column("take_profit", sa.Numeric(18, 4), nullable=True),
        sa.Column("pnl", sa.Numeric(18, 4), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker", sa.String(length=64), nullable=True),
        sa.Column("broker_order_id", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_trades_id", "trades", ["id"])
    op.create_index("ix_trades_user_id", "trades", ["user_id"])
    op.create_index("ix_trades_strategy_id", "trades", ["strategy_id"])
    op.create_index("ix_trades_symbol", "trades", ["symbol"])
    op.create_index("ix_trades_status", "trades", ["status"])
    op.create_index("ix_trades_broker_order_id", "trades", ["broker_order_id"])


def downgrade() -> None:
    op.drop_index("ix_trades_broker_order_id", table_name="trades")
    op.drop_index("ix_trades_status", table_name="trades")
    op.drop_index("ix_trades_symbol", table_name="trades")
    op.drop_index("ix_trades_strategy_id", table_name="trades")
    op.drop_index("ix_trades_user_id", table_name="trades")
    op.drop_index("ix_trades_id", table_name="trades")
    op.drop_table("trades")

    op.drop_index("ix_strategies_user_id", table_name="strategies")
    op.drop_index("ix_strategies_id", table_name="strategies")
    op.drop_table("strategies")

    op.drop_index("ix_user_settings_user_id", table_name="user_settings")
    op.drop_index("ix_user_settings_id", table_name="user_settings")
    op.drop_table("user_settings")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
