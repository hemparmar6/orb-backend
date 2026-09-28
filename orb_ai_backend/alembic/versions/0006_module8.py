"""Module 8 — Notifications, Portfolio snapshots, Reports.

Revision ID: 0006_module8
Revises: 0005_audit_log
Create Date: 2026-05-01 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_module8"
down_revision: Union[str, None] = "0005_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event", sa.String(length=32), nullable=False, index=True),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("channel_status", sa.JSON(), nullable=True),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
        sa.Column("push_token", sa.String(length=255), nullable=True),
        sa.Column("event_overrides", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_notif_pref_user"),
    )

    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("equity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("day_pnl", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("exposure", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("open_positions", sa.Numeric(6, 0), nullable=False, server_default="0"),
        sa.Column("holdings", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("user_id", "snapshot_date", name="uq_portfolio_snapshot_user_date"),
    )
    op.create_index("ix_portfolio_snap_user_date", "portfolio_snapshots", ["user_id", "snapshot_date"])

    op.create_table(
        "report_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("report_type", sa.String(length=32), nullable=False, index=True),
        sa.Column("report_format", sa.String(length=8), nullable=False, server_default="pdf"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("report_runs")
    op.drop_index("ix_portfolio_snap_user_date", table_name="portfolio_snapshots")
    op.drop_table("portfolio_snapshots")
    op.drop_table("notification_preferences")
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
