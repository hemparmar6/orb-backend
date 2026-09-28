"""Backtest runs.

Revision ID: 0004_backtest
Revises: 0003_broker_accounts
Create Date: 2026-06-01 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_backtest"
down_revision: Union[str, None] = "0003_broker_accounts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_name", sa.String(length=128), nullable=False),
        sa.Column("strategy_id", sa.String(length=36),
                  sa.ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("symbols", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("params", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("initial_capital", sa.Numeric(18, 4), nullable=False, server_default="100000"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("trades", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("equity_curve", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_backtest_runs_id", "backtest_runs", ["id"])
    op.create_index("ix_backtest_runs_user_id", "backtest_runs", ["user_id"])
    op.create_index("ix_backtest_runs_status", "backtest_runs", ["status"])
    op.create_index("ix_backtest_runs_strategy_id", "backtest_runs", ["strategy_id"])
    op.create_index("ix_backtest_runs_user_created", "backtest_runs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_user_created", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_strategy_id", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_status", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_user_id", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_id", table_name="backtest_runs")
    op.drop_table("backtest_runs")
