# ORB AI — Production Deployment Guide

Self-hosted production deployment of the ORB AI backend, admin dashboard, and
supporting Postgres + Redis services on an Ubuntu 22.04 / 24.04 VPS.

The Expo mobile app is published separately through Emergent Publish; this
guide covers **everything server-side**.

---

## 1. Architecture

```
                          ┌─ mobile app (Expo, deployed via Emergent)
                          │
Internet ──▶ Nginx :443 ──┼─ /api/v1/*     ──▶ orb_ai_api (container) :8000
    (TLS)                 │      │
                          │      ├─▶ postgres  (container, 127.0.0.1:5432)
                          │      └─▶ redis     (container, 127.0.0.1:6379)
                          │
                          └─ /api/admin-ui/*  ──▶ orb_ai_api serves SPA
```

The API container serves:

| Path | Purpose |
|------|---------|
| `/health`, `/api/v1/health` | Liveness probes (Nginx / Docker) |
| `/api/v1/*` | Full REST + admin API |
| `/api/v1/ws/*` | WebSocket endpoints (`/ws/quotes`, `/ws/orders`, `/ws/admin`) |
| `/api/admin-ui/*` | Vite React admin dashboard SPA (Module 7) |

---

## 2. Prerequisites

- Ubuntu 22.04+ VPS (≥ 2 vCPU, ≥ 4 GB RAM recommended).
- Domain pointed at the VPS (`api.yourdomain.com`).
- Root or sudo access.

Install Docker Engine, Docker Compose plugin, Nginx, and certbot:

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg nginx
curl -fsSL https://get.docker.com | sudo sh
sudo apt install -y docker-compose-plugin
sudo systemctl enable --now docker

sudo apt install -y python3-certbot-nginx
```

Confirm:

```bash
docker --version && docker compose version
nginx -v
certbot --version
```

---

## 3. Get the code

```bash
sudo mkdir -p /opt/orb && sudo chown "$USER" /opt/orb
cd /opt/orb
git clone <YOUR_REPO_URL> app
cd app        # /opt/orb/app is the repo root; orb_ai_backend/ and admin-web/ live inside
```

---

## 4. Configure secrets

```bash
cd /opt/orb/app/orb_ai_backend
cp .env.production.example .env
chmod 600 .env
```

Fill in every `REPLACE_ME_*` value. Generators:

```bash
# JWT secret (64+ chars)
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# Fernet encryption key for broker credentials
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Strong DB / Redis password
openssl rand -hex 32
```

Critical variables to set:

| Variable | Notes |
|----------|-------|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Postgres creds (used by the compose file to synthesise `DATABASE_URL`). |
| `REDIS_PASSWORD` | Redis password (must be strong; Redis is compose-managed but sensitive). |
| `JWT_SECRET_KEY` | 64+ char random string. Rotating this invalidates all JWTs. |
| `ENCRYPTION_KEY` | Fernet key. **Rotating this locks all existing broker credentials — export/re-encrypt before rotating.** |
| `ADMIN_SEED_EMAIL`, `ADMIN_SEED_PASSWORD` | Bootstrap admin. Change the password after first login. |
| `CORS_ORIGINS` | JSON list of your mobile app domain + admin dashboard origin. |
| `TRADING_SESSION_START/END/TIMEZONE` | Set to your exchange session (e.g. `09:15` / `15:30` / `Asia/Kolkata` for NSE). |

The compose file overrides `DATABASE_URL` and `REDIS_URL` to point at the
service-linked hostnames (`postgres`, `redis`), so you only need the raw
credentials in `.env`.

---

## 5. Build & start the stack

```bash
cd /opt/orb/app/orb_ai_backend
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

On first boot the API container will:

1. Wait for Postgres to become reachable.
2. Run `alembic upgrade head` — applies **all** migrations to a fresh DB.
3. Run `scripts/seed_admin.py` — creates `admin@orb.com` (or your override)
   as an active admin. Idempotent; does not touch an existing account.
4. Start `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
5. Mount the built admin SPA at `/api/admin-ui/`
   (`ADMIN_UI_DIST=/app/admin_web_dist`).

Verify:

```bash
# Container health
docker compose -f docker-compose.prod.yml ps
# → all services should be "healthy"

# API liveness
curl -fsS http://127.0.0.1:8000/health
# → {"status":"ok"}

# Admin login (before Nginx is up)
curl -sS -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@orb.com","password":"<your ADMIN_SEED_PASSWORD>"}'
# → {"access_token":"...","refresh_token":"...", ...}
```

---

## 6. Nginx + HTTPS

```bash
sudo cp /opt/orb/app/orb_ai_backend/deploy/nginx.prod.conf \
        /etc/nginx/sites-available/orb_ai
sudo ln -sf /etc/nginx/sites-available/orb_ai /etc/nginx/sites-enabled/orb_ai
```

Add the shared `http { }` blocks the config references (rate-limit zones +
WebSocket upgrade map). Edit `/etc/nginx/nginx.conf`, inside `http { }`:

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

limit_req_zone  $binary_remote_addr zone=orb_api:10m   rate=20r/s;
limit_req_zone  $binary_remote_addr zone=orb_auth:10m  rate=5r/s;
limit_conn_zone $binary_remote_addr zone=orb_conn:10m;
```

Replace every `api.yourdomain.com` in `nginx.prod.conf` with your real
hostname, then issue TLS:

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d api.yourdomain.com
sudo systemctl reload nginx
```

Verify end-to-end:

```bash
curl -fsS https://api.yourdomain.com/health
curl -fsS https://api.yourdomain.com/api/v1/health
# Admin dashboard:
open https://api.yourdomain.com/api/admin-ui/
```

---

## 7. Verifying WebSockets with Redis enabled

`REDIS_ENABLED=true` in the production `.env` (auto-set by compose) causes:

- `/api/v1/ws/orders` — Redis-backed order fan-out becomes fully live.
- `/api/v1/ws/quotes` — market-data fan-out shared across replicas.
- `/api/v1/ws/admin` — snapshot stream still works (this one is DB-driven
  and Redis-independent, but `health.redis` now returns `"ok"`).

Quick admin-WS smoke test from the VPS:

```bash
TOKEN=$(curl -sS -X POST https://api.yourdomain.com/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@orb.com","password":"<pw>"}' | jq -r .access_token)

python3 - <<PY
import asyncio, websockets, json, ssl
async def go():
    ctx = ssl.create_default_context()
    async with websockets.connect(
        f"wss://api.yourdomain.com/api/v1/ws/admin?token=$TOKEN&interval=1",
        ssl=ctx,
    ) as ws:
        msg = json.loads(await ws.recv())
        assert msg["type"] == "snapshot"
        assert msg["data"]["health"]["redis"] == "ok"
        print("WS admin OK, redis:", msg["data"]["health"]["redis"])
asyncio.run(go())
PY
```

---

## 8. Post-deployment checklist

- [ ] `GET https://api.yourdomain.com/health` → `{"status":"ok"}`
- [ ] `GET https://api.yourdomain.com/api/v1/health` → 200
- [ ] `POST /api/v1/auth/login` with seeded admin → 200 + token
- [ ] `GET /api/v1/users/me` with token → 200
- [ ] `GET /api/v1/admin/system/health` with admin token → 200 with
      `redis: "ok"`, `database: "ok"`
- [ ] `WS /api/v1/ws/admin` streams snapshots (see §7)
- [ ] Non-admin JWT is rejected on every `/api/v1/admin/*` route (403)
- [ ] Broker catalog reachable: `GET /api/v1/brokers/catalog`
- [ ] Admin dashboard loads at `/api/admin-ui/` and shows `LIVE` badge
- [ ] **First-login step**: sign in as admin, then
      `POST /api/v1/users/me/change-password` with a new strong password.

---

## 9. Operations

### Logs

```bash
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f postgres
docker compose -f docker-compose.prod.yml logs -f redis
```

Log rotation is bounded by the `x-log` block (`max-size: 20m`, `max-file: 5`).

### Backups (Postgres)

```bash
# Nightly dump (add to /etc/cron.daily/orb-backup)
docker exec orb_ai_postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  | gzip > /opt/orb/backups/orb_ai-$(date +%F).sql.gz
find /opt/orb/backups -name 'orb_ai-*.sql.gz' -mtime +14 -delete
```

Test restore periodically:

```bash
gunzip < /opt/orb/backups/orb_ai-YYYY-MM-DD.sql.gz \
  | docker exec -i orb_ai_postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

### Zero-downtime updates

```bash
cd /opt/orb/app
git pull
cd orb_ai_backend
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --build api
# Alembic runs automatically on container start.
```

### Rotating the admin password

Log in as admin and hit `POST /api/v1/users/me/change-password`, or:

```bash
docker exec -it orb_ai_api python -c "
import asyncio
from sqlalchemy import update
from app.core.security import hash_password
from app.db.session import async_session_factory
from app.models.user import User

async def go():
    async with async_session_factory() as s:
        await s.execute(update(User).where(User.email=='admin@orb.com')
                        .values(hashed_password=hash_password('NEW_PASSWORD')))
        await s.commit()
        print('rotated')
asyncio.run(go())
"
```

### Health & restart

Docker's healthchecks + `restart: unless-stopped` on every service handle
transient failures automatically. If the API container is unhealthy for
more than a few minutes, check `docker compose logs api` and ensure
Postgres/Redis are up first.

---

## 10. Test suite

The full pytest suite ships with the repo and runs against an in-memory
SQLite instance (unrelated to the production Postgres data):

```bash
# Inside the container
docker exec -it orb_ai_api pytest -q

# Or on the VPS host (needs venv + requirements)
cd /opt/orb/app/orb_ai_backend
python3 -m venv .venv && . .venv/bin/activate
# Core deps (public PyPI, always required)
pip install -r requirements.txt
# Optional AI provider (Module 9). Rule-based fallback runs without it.
pip install -r requirements-ai.txt || \
  echo "[orb-ai] AI extras skipped — rule-based fallback active"
pytest -q
```

The suite covers auth, RBAC, engine, broker integrations, backtest,
market-data, WebSocket admin/quotes/orders, and audit-log flows. All 186
tests must be green before promoting to production.

---

## 11. What is *not* in Module 7

Deferred to a future enhancement module (do not add these to Module 7):

- Bulk CSV export.
- Saved audit-log filter views.
- Order-fill push notifications via the Redis `/ws/orders` fan-out
  (the fan-out itself is live in production, but its consumption by the
  admin dashboard is intentionally deferred).

---

## 12. Troubleshooting

| Symptom | Fix |
|---------|-----|
| API container crash-looping | `docker compose logs api` — look for Alembic errors. Confirm `DATABASE_URL` inside the container: `docker exec orb_ai_api env \| grep DATABASE_URL`. |
| `admin_seeder_failed` in logs | Usually a race on first boot. The seeder is idempotent — check `docker compose exec api python scripts/seed_admin.py` manually. |
| `health.redis == "error"` | Wrong `REDIS_PASSWORD` between the redis container and the API's `REDIS_URL`. Both are driven from the same `.env` value in the compose file. |
| 502 from Nginx | API container not listening yet. Wait 20 s after `up -d` for the healthcheck. |
| WS closes with code 4401 | Bad / expired JWT. Re-login through `/auth/login`. |
| WS closes with code 4403 | Token is valid but user is not an admin. Promote via `/admin/users/{id}` PATCH. |

Support: <support@emergent.sh>
