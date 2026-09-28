"""Deep health probes for every runtime dependency.

Each probe returns a small dict with ``status`` ("ok" | "degraded" |
"unavailable" | "disabled"), ``latency_ms`` and (optionally) ``detail``.

All probes are wrapped in try/except so a failing dependency never
propagates an exception to the caller — critical for the /monitoring
endpoints which must always be reachable, even during outages.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def _timed(coro) -> tuple[Any, float]:
    start = time.perf_counter()
    result = await coro
    return result, round((time.perf_counter() - start) * 1000, 2)


# ------------------------------------------------------------------ Database

async def check_database(session: AsyncSession) -> dict[str, Any]:
    try:
        _, latency = await _timed(session.execute(text("SELECT 1")))
        return {"status": "ok", "latency_ms": latency}
    except Exception as exc:  # pragma: no cover — DB outages
        return {"status": "unavailable", "latency_ms": None, "detail": str(exc)}


# ------------------------------------------------------------------ Redis

async def check_redis() -> dict[str, Any]:
    if not settings.REDIS_ENABLED:
        return {"status": "disabled", "latency_ms": None}
    try:
        from app.core.redis import get_redis
        client = await get_redis()
        if client is None:
            return {"status": "unavailable", "latency_ms": None, "detail": "client not connected"}
        _, latency = await _timed(client.ping())
        return {"status": "ok", "latency_ms": latency}
    except Exception as exc:
        return {"status": "unavailable", "latency_ms": None, "detail": str(exc)}


# ------------------------------------------------------------------ AI service

async def check_ai_service() -> dict[str, Any]:
    """Reports the configured AI provider and whether a key is present.

    We do NOT make a live LLM call here — that would be expensive and
    subject to rate limits. The AIService itself already falls back to
    the deterministic rule-based provider when the key is missing.
    """
    if not settings.AI_ENABLED:
        return {"status": "disabled", "provider": None, "model": None}
    has_key = bool(settings.EMERGENT_LLM_KEY)
    if settings.AI_PROVIDER == "gpt52" and not has_key:
        return {
            "status": "degraded",
            "provider": settings.AI_PROVIDER,
            "model": settings.AI_MODEL,
            "detail": "EMERGENT_LLM_KEY missing — using rule-based fallback",
        }
    return {
        "status": "ok",
        "provider": settings.AI_PROVIDER,
        "model": settings.AI_MODEL,
    }


# ------------------------------------------------------------------ Brokers

async def check_broker_connectivity(session: AsyncSession) -> dict[str, Any]:
    """Reports counts of active broker accounts + registered broker adapters.

    A live ping per broker would require decrypted credentials and would
    be expensive, so we only report configuration state. The mobile app
    and Admin Dashboard already expose per-account "test connection".
    """
    try:
        from app.models.broker import BrokerAccount
        from sqlalchemy import func, select

        total = (await session.execute(select(func.count(BrokerAccount.id)))).scalar_one()
        active = (
            await session.execute(
                select(func.count(BrokerAccount.id)).where(BrokerAccount.is_active.is_(True))
            )
        ).scalar_one()

        registered: list[str] = []
        try:
            from app.brokers.registry import list_brokers  # type: ignore
            registered = sorted(list_brokers())
        except Exception:
            try:
                from app.brokers import registry as _br  # type: ignore
                registered = sorted(getattr(_br, "_registry", {}).keys())
            except Exception:
                registered = []

        return {
            "status": "ok",
            "accounts_total": int(total),
            "accounts_active": int(active),
            "adapters": registered,
        }
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)}


# ------------------------------------------------------------------ WS

async def check_websockets() -> dict[str, Any]:
    try:
        from app.ws import order_broadcaster, quote_broadcaster
        return {
            "status": "ok",
            "quote_broadcaster": getattr(quote_broadcaster, "is_running", lambda: True)(),
            "order_broadcaster": getattr(order_broadcaster, "is_running", lambda: True)(),
        }
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)}


# ------------------------------------------------------------------ Scheduler

async def check_scheduler() -> dict[str, Any]:
    if not settings.SCHEDULER_ENABLED:
        return {"status": "disabled"}
    try:
        from app.services.scheduler import scheduler_status
        payload = await scheduler_status()
        return {"status": "ok", **payload}
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)}


# ------------------------------------------------------------------ Aggregate

async def aggregate_health(session: AsyncSession) -> dict[str, Any]:
    """Snapshot of every subsystem, safe to expose to admin dashboards."""
    db = await check_database(session)
    redis_ = await check_redis()
    ai = await check_ai_service()
    brokers = await check_broker_connectivity(session)
    ws = await check_websockets()
    sched = await check_scheduler()

    # Overall = worst non-disabled status
    def _rank(s: str) -> int:
        return {"ok": 0, "disabled": 0, "degraded": 1, "unavailable": 2}.get(s, 2)

    worst = max(
        (_rank(x["status"]) for x in (db, redis_, ai, brokers, ws, sched)),
        default=0,
    )
    overall = {0: "ok", 1: "degraded", 2: "unavailable"}[worst]

    return {
        "overall": overall,
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "components": {
            "api": {"status": "ok"},
            "database": db,
            "redis": redis_,
            "ai_service": ai,
            "brokers": brokers,
            "websockets": ws,
            "scheduler": sched,
        },
    }
