"""Alembic revision 0016 — Milestone 9 follow-up (Risk Config Audit).

Adds the ``risk_limits_audit`` table so every mutation of a
``RiskLimit`` field is captured with previous_value / new_value / actor.
Additive only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# NOTE: Alembic stores this id in alembic_version.version_num VARCHAR(32).
# The previous id ("0016_milestone9_followup_risk_audit", 35 chars) overflowed
# that column and made `alembic upgrade head` fail on PostgreSQL. Shortened to
# fit (<=32). Migration DDL/schema is unchanged.
revision = "0016_m9_followup_risk_audit"
down_revision = "0015_milestone9_risk_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_limits_audit",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "target_user_id", sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "actor_user_id", sa.String(64),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True, index=True,
        ),
        sa.Column("actor_type", sa.String(16), nullable=False,
                  server_default="user"),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_risk_limits_audit_user_created", "risk_limits_audit",
        ["target_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_risk_limits_audit_user_created",
                  table_name="risk_limits_audit")
    op.drop_table("risk_limits_audit")
