"""Alembic revision 0013 — Phase 4 Bot Management & Circuit Breakers (v1.1.0).

Adds 5 new tables. Zero mutations to existing tables → full backward compat.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_v110_phase4_bots"
down_revision = "0012_v110_phase3_affiliate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- bots ------------------------------------------------------------
    op.create_table(
        "bots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("strategy_key", sa.String(64), nullable=False, index=True),
        sa.Column("symbols", sa.JSON(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("risk_config", sa.JSON(), nullable=False),
        sa.Column("execution_mode", sa.String(16), nullable=False, server_default="paper"),
        sa.Column("broker_account_id", sa.String(64), sa.ForeignKey("broker_accounts.id", ondelete="SET NULL")),
        sa.Column("initial_capital", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="idle", index=True),
        sa.Column("engine_session_id", sa.String(64), sa.ForeignKey("engine_sessions.id", ondelete="SET NULL")),
        sa.Column("day_pnl_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_pnl_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trades_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("peak_pnl_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("is_killed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("user_id", "name", name="uq_bot_user_name"),
    )
    op.create_index("ix_bots_user_status", "bots", ["user_id", "status"])

    # ---- circuit_breaker_configs ----------------------------------------
    op.create_table(
        "circuit_breaker_configs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, index=True),
        sa.Column("breaker_type", sa.String(48), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("strategy_key", sa.String(64)),
        sa.Column("broker_type", sa.String(32)),
        sa.Column("bot_id", sa.String(64), sa.ForeignKey("bots.id", ondelete="CASCADE")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("value_num", sa.Numeric(20, 6)),
        sa.Column("value_json", sa.JSON()),
        sa.Column("unit", sa.String(32)),
        sa.Column("action", sa.String(32), nullable=False, server_default="pause"),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notify", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_user_id", sa.String(64)),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("ix_cbc_level_type", "circuit_breaker_configs", ["level", "breaker_type"])

    # ---- circuit_breaker_events -----------------------------------------
    op.create_table(
        "circuit_breaker_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_id", sa.String(64), sa.ForeignKey("circuit_breaker_configs.id", ondelete="SET NULL")),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("breaker_type", sa.String(48), nullable=False),
        sa.Column("user_id", sa.String(64), index=True),
        sa.Column("bot_id", sa.String(64), index=True),
        sa.Column("strategy_key", sa.String(64)),
        sa.Column("broker_type", sa.String(32)),
        sa.Column("triggered_value", sa.Numeric(20, 6)),
        sa.Column("threshold", sa.Numeric(20, 6)),
        sa.Column("action_taken", sa.String(32), nullable=False, server_default="pause"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("event_metadata", sa.JSON()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_cbe_time", "circuit_breaker_events", ["created_at"])
    op.create_index("ix_cbe_user_time", "circuit_breaker_events", ["user_id", "created_at"])

    # ---- kill_switch_events ---------------------------------------------
    op.create_table(
        "kill_switch_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, index=True),
        sa.Column("actor_user_id", sa.String(64)),
        sa.Column("target_user_id", sa.String(64), index=True),
        sa.Column("target_bot_id", sa.String(64), index=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("close_positions", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("bots_stopped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("positions_closed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("event_metadata", sa.JSON()),
    )
    op.create_index("ix_kse_scope_time", "kill_switch_events", ["scope", "created_at"])

    # ---- bot_audit_logs -------------------------------------------------
    op.create_table(
        "bot_audit_logs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bot_id", sa.String(64), sa.ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("actor_user_id", sa.String(64)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("previous_status", sa.String(24)),
        sa.Column("new_status", sa.String(24)),
        sa.Column("details", sa.JSON()),
    )
    op.create_index("ix_bal_bot_time", "bot_audit_logs", ["bot_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_bal_bot_time", table_name="bot_audit_logs")
    op.drop_table("bot_audit_logs")
    op.drop_index("ix_kse_scope_time", table_name="kill_switch_events")
    op.drop_table("kill_switch_events")
    op.drop_index("ix_cbe_user_time", table_name="circuit_breaker_events")
    op.drop_index("ix_cbe_time", table_name="circuit_breaker_events")
    op.drop_table("circuit_breaker_events")
    op.drop_index("ix_cbc_level_type", table_name="circuit_breaker_configs")
    op.drop_table("circuit_breaker_configs")
    op.drop_index("ix_bots_user_status", table_name="bots")
    op.drop_table("bots")
