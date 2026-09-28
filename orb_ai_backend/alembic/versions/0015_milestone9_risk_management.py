"""Alembic revision 0015 — ORB AI 2.0 Milestone 9 (Risk Management).

Adds two additive tables:

* ``risk_limits``  — one row per user, consolidated risk profile
* ``risk_breaches`` — append-only breach log

Also adds five values to the ``notification_event`` enum (stored as strings
on SQLite/Postgres because we use ``native_enum=False``, so no ALTER TYPE
is required on either backend).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_milestone9_risk_management"
down_revision = "0014_milestone8_execution_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- risk_limits -----------------------------------------------------
    op.create_table(
        "risk_limits",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),

        # Money
        sa.Column("daily_loss_limit", sa.Numeric(18, 4), nullable=True),
        sa.Column("daily_profit_target", sa.Numeric(18, 4), nullable=True),
        sa.Column("max_capital_allocation", sa.Numeric(18, 4), nullable=True),
        sa.Column("max_position_size", sa.Numeric(18, 4), nullable=True),
        sa.Column("max_exposure_per_symbol", sa.Numeric(18, 4), nullable=True),

        # Counts
        sa.Column("max_trades_per_day", sa.Integer(), nullable=True),
        sa.Column("max_consecutive_losses", sa.Integer(), nullable=True),
        sa.Column("max_open_positions", sa.Integer(), nullable=True),

        # Trading session window
        sa.Column("trading_session_start", sa.String(8), nullable=True),
        sa.Column("trading_session_end", sa.String(8), nullable=True),
        sa.Column("trading_session_timezone", sa.String(64), nullable=True),

        # Live-trading control
        sa.Column("live_trading_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("force_paper_mode", sa.Boolean(), nullable=False,
                  server_default=sa.false()),

        # Notification prefs
        sa.Column("notify_channels", sa.JSON(), nullable=False,
                  server_default='{"in_app": true, "email": true, "push": true, "telegram": false}'),
        sa.Column("notify_severity_min", sa.String(16), nullable=False,
                  server_default="warning"),

        sa.Column("extra", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_risk_limits_user"),
    )

    # ---- risk_breaches ---------------------------------------------------
    op.create_table(
        "risk_breaches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("bot_id", sa.String(64),
                  sa.ForeignKey("bots.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("symbol", sa.String(64), nullable=True, index=True),

        sa.Column("event_type", sa.String(32), nullable=False, index=True),
        sa.Column("severity", sa.String(16), nullable=False,
                  server_default="warning"),
        sa.Column("action_taken", sa.String(24), nullable=False,
                  server_default="blocked"),

        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("triggered_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("threshold", sa.Numeric(20, 6), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),

        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(64), nullable=True),
        sa.Column("notification_id", sa.String(64), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_risk_breaches_user_created", "risk_breaches",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_risk_breaches_user_created", table_name="risk_breaches")
    op.drop_table("risk_breaches")
    op.drop_table("risk_limits")
