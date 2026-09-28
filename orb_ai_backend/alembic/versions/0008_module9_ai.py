"""Module 9 — AI Trading Intelligence.

Revision ID: 0008_module9_ai
Revises: 0007_subscriptions
Create Date: 2026-05-15 12:00:00

Adds 7 new tables (ai_trade_reviews, ai_recommendations, ai_audit_log,
optim_jobs, optim_results, mi_snapshots, ai_analytics_snapshots).
No existing table is modified.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_module9_ai"
down_revision: Union[str, None] = "0007_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- ai_trade_reviews ----
    op.create_table(
        "ai_trade_reviews",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("trade_id", sa.String(), sa.ForeignKey("trades.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_quality_score", sa.Float()),
        sa.Column("entry_quality", sa.Float()),
        sa.Column("exit_quality", sa.Float()),
        sa.Column("risk_management_score", sa.Float()),
        sa.Column("rule_compliance", sa.Float()),
        sa.Column("emotional_flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("improvements", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("summary", sa.Text()),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="primary"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("trade_id", "prompt_version", name="uq_ai_trade_review_trade_ver"),
    )
    op.create_index("ix_ai_trade_reviews_trade_id", "ai_trade_reviews", ["trade_id"])
    op.create_index("ix_ai_trade_reviews_user_id", "ai_trade_reviews", ["user_id"])

    # ---- ai_recommendations ----
    op.create_table(
        "ai_recommendations",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("priority", sa.String(8), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("acted_on_at", sa.DateTime(timezone=True)),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_rec_user_status", "ai_recommendations", ["user_id", "status"])

    # ---- ai_audit_log ----
    op.create_table(
        "ai_audit_log",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("request_type", sa.String(64), nullable=False),
        sa.Column("prompt_name", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ai_audit_log_user_id", "ai_audit_log", ["user_id"])

    # ---- optim_jobs ----
    op.create_table(
        "optim_jobs",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("strategy_id", sa.String(), sa.ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("params_space", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # ---- optim_results ----
    op.create_table(
        "optim_results",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("optim_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("is_best", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_optim_results_job_rank", "optim_results", ["job_id", "rank"])

    # ---- mi_snapshots ----
    op.create_table(
        "mi_snapshots",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False, server_default="D1"),
        sa.Column("regime", sa.String(16), nullable=False),
        sa.Column("trend_strength", sa.Float(), nullable=False),
        sa.Column("volatility_regime", sa.String(16), nullable=False),
        sa.Column("liquidity", sa.String(16), nullable=False),
        sa.Column("gap_behaviour", sa.String(16)),
        sa.Column("session_stats", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_mi_symbol_generated", "mi_snapshots", ["symbol", "generated_at"])

    # ---- ai_analytics_snapshots ----
    op.create_table(
        "ai_analytics_snapshots",
        sa.Column("id", sa.String(), primary_key=True, index=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_ref_id", sa.String(64)),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("equity_curve", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_index("ix_mi_symbol_generated", table_name="mi_snapshots")
    op.drop_index("ix_optim_results_job_rank", table_name="optim_results")
    op.drop_index("ix_ai_audit_log_user_id", table_name="ai_audit_log")
    op.drop_index("ix_ai_rec_user_status", table_name="ai_recommendations")
    op.drop_index("ix_ai_trade_reviews_user_id", table_name="ai_trade_reviews")
    op.drop_index("ix_ai_trade_reviews_trade_id", table_name="ai_trade_reviews")

    for t in [
        "ai_analytics_snapshots",
        "mi_snapshots",
        "optim_results",
        "optim_jobs",
        "ai_audit_log",
        "ai_recommendations",
        "ai_trade_reviews",
    ]:
        op.drop_table(t)
