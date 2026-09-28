"""Module 10 — Enterprise & Production.

Revision ID: 0009_module10
Revises: 0008_module9_ai
Create Date: 2026-02-15 12:00:00

Adds 4 monitoring tables (backup_records, restore_records,
login_activity, rate_limit_events). No existing table is modified.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_module10"
down_revision: Union[str, None] = "0008_module9_ai"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- backup_records ----
    op.create_table(
        "backup_records",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("filename", sa.String(255)),
        sa.Column("local_path", sa.Text()),
        sa.Column("s3_key", sa.String(512)),
        sa.Column("s3_bucket", sa.String(255)),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("uploaded_to_s3", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_message", sa.Text()),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_backup_records_status", "backup_records", ["status"])
    op.create_index("ix_backup_records_created_at", "backup_records", ["created_at"])

    # ---- restore_records ----
    op.create_table(
        "restore_records",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("backup_id", sa.String(), sa.ForeignKey("backup_records.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(16), nullable=False, server_default="local"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("initiated_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- login_activity ----
    op.create_table(
        "login_activity",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String(64)),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("user_agent", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_login_activity_email", "login_activity", ["email"])
    op.create_index("ix_login_activity_user_id", "login_activity", ["user_id"])
    op.create_index("ix_login_activity_ip", "login_activity", ["ip_address"])
    op.create_index("ix_login_activity_created_at", "login_activity", ["created_at"])

    # ---- rate_limit_events ----
    op.create_table(
        "rate_limit_events",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("method", sa.String(16), nullable=False, server_default="GET"),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("retry_after_seconds", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rate_limit_events_key", "rate_limit_events", ["key"])
    op.create_index("ix_rate_limit_events_ip", "rate_limit_events", ["ip_address"])
    op.create_index("ix_rate_limit_events_created_at", "rate_limit_events", ["created_at"])


def downgrade() -> None:
    for idx in (
        "ix_rate_limit_events_created_at",
        "ix_rate_limit_events_ip",
        "ix_rate_limit_events_key",
        "ix_login_activity_created_at",
        "ix_login_activity_ip",
        "ix_login_activity_user_id",
        "ix_login_activity_email",
        "ix_backup_records_created_at",
        "ix_backup_records_status",
    ):
        try:
            op.drop_index(idx)
        except Exception:
            pass

    for t in ("rate_limit_events", "login_activity", "restore_records", "backup_records"):
        try:
            op.drop_table(t)
        except Exception:
            pass
