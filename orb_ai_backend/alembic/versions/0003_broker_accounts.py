"""Broker accounts + live-mode columns on engine_sessions & paper_orders.

Revision ID: 0003_broker_accounts
Revises: 0002_trading_engine
Create Date: 2026-03-01 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_broker_accounts"
down_revision: Union[str, None] = "0002_trading_engine"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- broker_accounts ----
    op.create_table(
        "broker_accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("broker_type", sa.String(length=32), nullable=False),
        sa.Column("alias", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("credentials_ciphertext", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_broker_accounts_id", "broker_accounts", ["id"])
    op.create_index("ix_broker_accounts_user_id", "broker_accounts", ["user_id"])

    # ---- engine_sessions: add execution_mode + broker_account_id ----
    op.add_column(
        "engine_sessions",
        sa.Column("execution_mode", sa.String(length=8), nullable=False, server_default="paper"),
    )
    op.add_column(
        "engine_sessions",
        sa.Column(
            "broker_account_id",
            sa.String(length=36),
            sa.ForeignKey("broker_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_engine_sessions_execution_mode", "engine_sessions", ["execution_mode"])
    op.create_index("ix_engine_sessions_broker_account_id", "engine_sessions", ["broker_account_id"])

    # ---- paper_orders: add broker_account_id + broker_order_id ----
    op.add_column(
        "paper_orders",
        sa.Column(
            "broker_account_id",
            sa.String(length=36),
            sa.ForeignKey("broker_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("paper_orders", sa.Column("broker_order_id", sa.String(length=128), nullable=True))
    op.create_index("ix_paper_orders_broker_account_id", "paper_orders", ["broker_account_id"])
    op.create_index("ix_paper_orders_broker_order_id", "paper_orders", ["broker_order_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_orders_broker_order_id", table_name="paper_orders")
    op.drop_index("ix_paper_orders_broker_account_id", table_name="paper_orders")
    op.drop_column("paper_orders", "broker_order_id")
    op.drop_column("paper_orders", "broker_account_id")

    op.drop_index("ix_engine_sessions_broker_account_id", table_name="engine_sessions")
    op.drop_index("ix_engine_sessions_execution_mode", table_name="engine_sessions")
    op.drop_column("engine_sessions", "broker_account_id")
    op.drop_column("engine_sessions", "execution_mode")

    op.drop_index("ix_broker_accounts_user_id", table_name="broker_accounts")
    op.drop_index("ix_broker_accounts_id", table_name="broker_accounts")
    op.drop_table("broker_accounts")
