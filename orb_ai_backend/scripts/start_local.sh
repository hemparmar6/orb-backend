#!/usr/bin/env bash
# Start ORB AI backend locally against SQLite (Emergent preview pod).
# Runs the admin seeder before booting uvicorn.
set -euo pipefail

cd "$(dirname "$0")/.."

# Load .env (best-effort)
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

echo "[start_local] Seeding admin user..."
/root/.venv/bin/python scripts/seed_admin.py || echo "[start_local] Seeder failed (continuing)"

echo "[start_local] Starting uvicorn on :8001..."
exec /root/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 1
