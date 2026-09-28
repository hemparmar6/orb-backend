#!/usr/bin/env bash
# ------------------------------------------------------------
# Entrypoint: wait for Postgres, run Alembic migrations, optionally
# run the admin seeder, then exec CMD (uvicorn).
# ------------------------------------------------------------
set -euo pipefail

: "${POSTGRES_HOST:=}"
: "${POSTGRES_PORT:=}"

# Resolve the DB endpoint to wait for. Prefer explicit POSTGRES_HOST/PORT,
# otherwise derive them from DATABASE_URL so the wait target always matches
# the real database (e.g. Railway's *.railway.internal). This prevents a boot
# failure when only DATABASE_URL is provided (the common Railway/managed case).
python - <<'PY'
import os, socket, time, sys
from urllib.parse import urlparse

host = os.environ.get("POSTGRES_HOST") or ""
port = os.environ.get("POSTGRES_PORT") or ""
if not host:
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn:
        clean = (dsn.replace("+asyncpg", "").replace("+psycopg2", "")
                    .replace("+psycopg", ""))
        parsed = urlparse(clean)
        host = parsed.hostname or "postgres"
        if not port and parsed.port:
            port = str(parsed.port)
    else:
        host = "postgres"
port = int(port or "5432")

print(f"[entrypoint] Waiting for Postgres at {host}:{port}...")
deadline = time.time() + 60
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[entrypoint] Postgres reachable at {host}:{port}")
            sys.exit(0)
    except OSError:
        time.sleep(1)
print(f"[entrypoint] Timed out waiting for Postgres at {host}:{port}", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] Running Alembic migrations..."
alembic upgrade head

# Seed the default admin account exactly once per boot (idempotent).
# Enabled by default in production compose; disable by setting
# ADMIN_SEED_ON_STARTUP=false.
#
# NOTE: `app.main._maybe_run_admin_seeder()` also honours this flag from
# the FastAPI lifespan, so if you enable it here AND leave the env var
# set for the running process, the seeder will run twice. Both invocations
# are idempotent so this is harmless — we prefer running it here (before
# uvicorn) so any seeder crash surfaces in the entrypoint logs.
case "${ADMIN_SEED_ON_STARTUP:-true}" in
    1|true|True|yes|YES|on|ON)
        echo "[entrypoint] Seeding admin account (idempotent)..."
        # Normalise APP_ENV for the environment check below.
        _app_env="$(printf '%s' "${APP_ENV:-development}" | tr '[:upper:]' '[:lower:]')"
        if [ "$_app_env" = "production" ]; then
            # PRODUCTION: seeding is required. A failure (missing/insecure
            # admin credentials, DB error) MUST abort startup instead of
            # booting without a known-good admin. `set -euo pipefail` makes
            # this non-zero exit propagate — no `|| echo` swallowing here.
            # seed_admin.py never prints the password, so no secret leaks.
            echo "[entrypoint] Production admin seeding (fail-fast on error)..."
            python scripts/seed_admin.py
        else
            # DEVELOPMENT / STAGING / TEST: keep the historical convenience —
            # a seeder hiccup should not block a local/CI boot.
            python scripts/seed_admin.py || echo "[entrypoint] admin seeder failed (continuing)"
        fi
        # Prevent the FastAPI lifespan hook from re-running the same seeder.
        # (Preserves the Task 5 `_maybe_run_admin_seeder()` protection: it is
        # gated on this flag and also fails fast in production on its own.)
        export ADMIN_SEED_ON_STARTUP=false
        ;;
    *)
        echo "[entrypoint] Skipping admin seeder (ADMIN_SEED_ON_STARTUP=${ADMIN_SEED_ON_STARTUP:-unset})"
        ;;
esac

echo "[entrypoint] Starting: $*"
exec "$@"
