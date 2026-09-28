# syntax=docker/dockerfile:1.6
# --------------------------------------------------------------------------
# Multi-stage build for the ORB AI backend + Module 7 Admin Dashboard.
#
# Build with the *repository root* as context so both `orb_ai_backend/` and
# `admin-web/` are visible:
#
#   docker build -t orb_ai_api -f orb_ai_backend/Dockerfile .
#
# `docker-compose.prod.yml` handles this automatically.
# --------------------------------------------------------------------------

# ---- Stage 1: build admin dashboard bundle ------------------------------
FROM node:20-alpine AS admin-web-builder
WORKDIR /web
RUN corepack enable && corepack prepare yarn@1.22.22 --activate
COPY admin-web/package.json admin-web/yarn.lock ./
RUN yarn install --frozen-lockfile --non-interactive
COPY admin-web/ ./
RUN yarn build

# ---- Stage 2: Python runtime -------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY orb_ai_backend/requirements.txt .
COPY orb_ai_backend/requirements-ai.txt .
# 1) Core dependencies — public PyPI only. Must succeed.
RUN pip install -r requirements.txt
# 2) Optional AI provider (Module 9). Fetches from the Emergent index.
#    If unreachable (air-gapped builds, mirror outage, forked repos),
#    the build continues and the AIService falls back to rule-based at
#    runtime — every AI endpoint keeps working.
RUN pip install -r requirements-ai.txt \
    || echo "[orb-ai] AI extras skipped — rule-based fallback will be used at runtime"

# App source (all backend code)
COPY orb_ai_backend/ .

# Pre-built admin dashboard SPA
COPY --from=admin-web-builder /web/dist /app/admin_web_dist

# Non-root user
RUN groupadd -r orb && useradd -r -g orb orb \
    && chown -R orb:orb /app \
    && chmod +x /app/scripts/entrypoint.sh
USER orb

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/api/v1/health" || exit 1

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
# Shell-form so Railway's dynamic $PORT is honoured (falls back to 8000
# locally / when PORT is unset). `exec` keeps uvicorn as PID 1's child so
# signals (SIGTERM on deploy) propagate correctly.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
