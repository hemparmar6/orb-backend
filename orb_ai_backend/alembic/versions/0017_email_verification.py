"""Alembic revision 0017 — Email Verification (reconciliation / no-op).

WHY THIS FILE EXISTS
--------------------
The production database's ``alembic_version.version_num`` is stamped at
``0017_email_verification``, but the migration script for that revision was
lost from source (the v3.0.1 snapshot's history stops at
``0016_m9_followup_risk_audit``). On boot the entrypoint runs
``alembic upgrade head``; Alembic reads the DB's current revision
(``0017_email_verification``), cannot find a matching script in
``alembic/versions/``, and aborts with::

    ERROR: Can't locate revision identified by '0017_email_verification'

Because the entrypoint uses ``set -euo pipefail``, that non-zero exit kills
the container *before* uvicorn starts, so ``/api/v1/health`` never becomes
reachable and Railway reports "1/1 replicas never became healthy".

WHY THE upgrade() IS A NO-OP
----------------------------
Restoring this revision re-aligns the source migration graph with the
recorded production revision so ``alembic upgrade head`` can locate the DB's
current revision again. The body is intentionally empty because the current
source contains **no** email-verification schema that is unaccounted for:

  * The only verification-related column, ``users.is_verified``, is created
    by ``0001_initial`` and is therefore already present in every database
    (dev, test, and production alike).
  * A full ``alembic revision --autogenerate`` run against a database
    migrated to ``0016`` detects **no** email-verification tables or columns
    missing from the model metadata — i.e. nothing that a ``0017`` body would
    need to (re)create.
  * No application code, model, schema, or endpoint references any
    ``0017``-specific column or table.

The original DDL applied to production by ``0017`` is already committed there,
and Alembic will never re-run this revision on production (it is already
recorded as applied). On a *fresh* database the current models/code do not
depend on anything ``0017`` might have added, so an empty body produces a
schema that fully satisfies the running application. Inventing speculative
DDL here would risk colliding with production's real columns and would create
phantom, code-less schema — hence a verified, documented no-op is the only
faithful reconstruction.
"""
from __future__ import annotations

# revision identifiers, used by Alembic.
# NOTE: kept <=32 chars to fit alembic_version.version_num VARCHAR(32).
# ("0017_email_verification" is 23 chars — well within the limit.)
revision = "0017_email_verification"
down_revision = "0016_m9_followup_risk_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op — see module docstring. Bridges the version graph only."""
    pass


def downgrade() -> None:
    """No-op — nothing to revert (this revision adds no source schema)."""
    pass
