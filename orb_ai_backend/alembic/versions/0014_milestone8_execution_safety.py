"""Alembic revision 0014 — ORB AI 2.0 Milestone 8 (Execution Safety).

Adds three additive tables:

* ``execution_safety_settings`` — singleton row (id='global')
* ``execution_safety_events``   — one row per enforcement decision
* ``execution_safety_config_audit`` — one row per admin config change

Zero mutations to existing tables → full backward compatibility with all
prior milestones.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_milestone8_execution_safety"
down_revision = "0013_v110_phase4_bots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- execution_safety_settings ---------------------------------------
    op.create_table(
        "execution_safety_settings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("trades_per_second", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("orders_per_minute", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("orders_per_hour", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("global_orders_per_second", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("global_orders_per_minute", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("duplicate_window_seconds", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("duplicate_action", sa.String(16), nullable=False, server_default="reject"),
        sa.Column("queue_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("queue_max_size", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("queue_timeout_seconds", sa.Float(), nullable=False, server_default="30.0"),
        sa.Column("auto_pause_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_pause_violations", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("auto_pause_window_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("kill_switch_reason", sa.Text(), nullable=True),
        sa.Column("kill_switch_activated_by", sa.String(64), nullable=True),
        sa.Column("kill_switch_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_retention_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("extra", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # ---- execution_safety_events -----------------------------------------
    op.create_table(
        "execution_safety_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("bot_id", sa.String(64),
                  sa.ForeignKey("bots.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("strategy_id", sa.String(64), nullable=True, index=True),
        sa.Column("engine_session_id", sa.String(64), nullable=True, index=True),
        sa.Column("broker", sa.String(32), nullable=True),
        sa.Column("symbol", sa.String(64), nullable=True, index=True),
        sa.Column("order_type", sa.String(24), nullable=True),
        sa.Column("endpoint", sa.String(255), nullable=True),
        sa.Column("limit_type", sa.String(32), nullable=False, index=True),
        sa.Column("action", sa.String(32), nullable=False, index=True),
        sa.Column("current_counter", sa.Integer(), nullable=True),
        sa.Column("configured_limit", sa.Integer(), nullable=True),
        sa.Column("retry_after_seconds", sa.Float(), nullable=True),
        sa.Column("fingerprint", sa.String(128), nullable=True, index=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # ---- execution_safety_config_audit -----------------------------------
    op.create_table(
        "execution_safety_config_audit",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("admin_user_id", sa.String(64),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("execution_safety_config_audit")
    op.drop_table("execution_safety_events")
    op.drop_table("execution_safety_settings")
