"""Trading engine tables: engine_sessions, paper_orders, paper_positions, paper_trades.

Revision ID: 0002_trading_engine
Revises: 0001_initial
Create Date: 2026-02-15 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_trading_engine"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- engine_sessions ----
    op.create_table(
        "engine_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_name", sa.String(length=128), nullable=False),
        sa.Column("strategy_id", sa.String(length=36), sa.ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="stopped"),
        sa.Column("symbols", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("params", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("risk_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("initial_capital", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("day_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_engine_sessions_id", "engine_sessions", ["id"])
    op.create_index("ix_engine_sessions_user_id", "engine_sessions", ["user_id"])
    op.create_index("ix_engine_sessions_strategy_id", "engine_sessions", ["strategy_id"])
    op.create_index("ix_engine_sessions_status", "engine_sessions", ["status"])

    # ---- paper_orders ----
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("engine_session_id", sa.String(length=36), sa.ForeignKey("engine_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False, server_default="MOCK"),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("order_type", sa.String(length=8), nullable=False),
        sa.Column("product", sa.String(length=8), nullable=False, server_default="mis"),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=True),
        sa.Column("trigger_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("stop_loss", sa.Numeric(18, 4), nullable=True),
        sa.Column("target_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("filled_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("average_fill_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("strategy_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("tag", sa.String(length=64), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_paper_orders_id", "paper_orders", ["id"])
    op.create_index("ix_paper_orders_user_id", "paper_orders", ["user_id"])
    op.create_index("ix_paper_orders_engine_session_id", "paper_orders", ["engine_session_id"])
    op.create_index("ix_paper_orders_symbol", "paper_orders", ["symbol"])
    op.create_index("ix_paper_orders_status", "paper_orders", ["status"])

    # ---- paper_positions ----
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("engine_session_id", sa.String(length=36), sa.ForeignKey("engine_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False, server_default="MOCK"),
        sa.Column("product", sa.String(length=8), nullable=False, server_default="mis"),
        sa.Column("net_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("average_price", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("last_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("engine_session_id", "symbol", "exchange", "product", name="uq_paper_position"),
    )
    op.create_index("ix_paper_positions_id", "paper_positions", ["id"])
    op.create_index("ix_paper_positions_user_id", "paper_positions", ["user_id"])
    op.create_index("ix_paper_positions_engine_session_id", "paper_positions", ["engine_session_id"])
    op.create_index("ix_paper_positions_session_symbol", "paper_positions", ["engine_session_id", "symbol"])

    # ---- paper_trades ----
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("engine_session_id", sa.String(length=36), sa.ForeignKey("engine_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_order_id", sa.String(length=36), sa.ForeignKey("paper_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False, server_default="MOCK"),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("realized_pnl_delta", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("strategy_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_paper_trades_id", "paper_trades", ["id"])
    op.create_index("ix_paper_trades_user_id", "paper_trades", ["user_id"])
    op.create_index("ix_paper_trades_engine_session_id", "paper_trades", ["engine_session_id"])
    op.create_index("ix_paper_trades_paper_order_id", "paper_trades", ["paper_order_id"])
    op.create_index("ix_paper_trades_symbol", "paper_trades", ["symbol"])
    op.create_index("ix_paper_trades_session_time", "paper_trades", ["engine_session_id", "executed_at"])


def downgrade() -> None:
    op.drop_index("ix_paper_trades_session_time", table_name="paper_trades")
    op.drop_index("ix_paper_trades_symbol", table_name="paper_trades")
    op.drop_index("ix_paper_trades_paper_order_id", table_name="paper_trades")
    op.drop_index("ix_paper_trades_engine_session_id", table_name="paper_trades")
    op.drop_index("ix_paper_trades_user_id", table_name="paper_trades")
    op.drop_index("ix_paper_trades_id", table_name="paper_trades")
    op.drop_table("paper_trades")

    op.drop_index("ix_paper_positions_session_symbol", table_name="paper_positions")
    op.drop_index("ix_paper_positions_engine_session_id", table_name="paper_positions")
    op.drop_index("ix_paper_positions_user_id", table_name="paper_positions")
    op.drop_index("ix_paper_positions_id", table_name="paper_positions")
    op.drop_table("paper_positions")

    op.drop_index("ix_paper_orders_status", table_name="paper_orders")
    op.drop_index("ix_paper_orders_symbol", table_name="paper_orders")
    op.drop_index("ix_paper_orders_engine_session_id", table_name="paper_orders")
    op.drop_index("ix_paper_orders_user_id", table_name="paper_orders")
    op.drop_index("ix_paper_orders_id", table_name="paper_orders")
    op.drop_table("paper_orders")

    op.drop_index("ix_engine_sessions_status", table_name="engine_sessions")
    op.drop_index("ix_engine_sessions_strategy_id", table_name="engine_sessions")
    op.drop_index("ix_engine_sessions_user_id", table_name="engine_sessions")
    op.drop_index("ix_engine_sessions_id", table_name="engine_sessions")
    op.drop_table("engine_sessions")
