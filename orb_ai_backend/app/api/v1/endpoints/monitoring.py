"""Module 10 — Monitoring, logs, security and backup endpoints.

All endpoints require ``UserRole.ADMIN``. They read from in-process
collectors (metrics + log_buffer + health checks) and the persistent
tables (``backup_records``, ``login_activity``, ``rate_limit_events``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, func, select

from app.api.deps import AdminUser, DBSession
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.core.logging import get_logger
from app.models.monitoring import BackupRecord, LoginActivity, RateLimitEvent
from app.monitoring import aggregate_health, log_buffer, metrics
from app.services.audit_service import AuditService
from app.services.backup_service import BackupService

logger = get_logger("app.monitoring.api")
router = APIRouter()


# ------------------------------------------------------------------ health

@router.get("/health", summary="Aggregated health of every subsystem")
async def get_health(_admin: AdminUser, session: DBSession) -> dict[str, Any]:
    return await aggregate_health(session)


# ------------------------------------------------------------------ metrics

@router.get("/metrics", summary="Runtime metrics snapshot (JSON)")
async def get_metrics(_admin: AdminUser) -> dict[str, Any]:
    return metrics.snapshot()


@router.get(
    "/metrics/prometheus",
    summary="Metrics in Prometheus text exposition format",
    response_class=PlainTextResponse,
    include_in_schema=False,
)
async def prometheus_metrics() -> PlainTextResponse:
    return PlainTextResponse(metrics.prometheus(), media_type="text/plain; version=0.0.4")


# ------------------------------------------------------------------ logs

@router.get("/logs", summary="Tail recent structured log entries")
async def get_logs(
    _admin: AdminUser,
    category: str = Query("application", pattern="^(application|error|security|ai|audit|trade|access)$"),
    level: Optional[str] = Query(None, pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
    limit: int = Query(200, ge=1, le=1000),
    contains: Optional[str] = None,
) -> dict[str, Any]:
    entries = log_buffer.tail(category=category, limit=limit, level=level, contains=contains)
    return {
        "category": category,
        "count": len(entries),
        "categories_available": list(log_buffer.categories()),
        "stats": log_buffer.stats(),
        "entries": entries,
    }


# ------------------------------------------------------------------ performance

@router.get("/performance", summary="Per-endpoint latency + error breakdown")
async def get_performance(_admin: AdminUser) -> dict[str, Any]:
    snap = metrics.snapshot()
    return {
        "uptime_s": snap["uptime_s"],
        "total_requests": snap["total_requests"],
        "error_rate": snap["error_rate"],
        "status_buckets": snap["status_buckets"],
        "endpoints": snap["endpoints"][:100],
        "slow_request_threshold_ms": settings.SLOW_REQUEST_THRESHOLD_MS,
    }


# ------------------------------------------------------------------ security

@router.get("/security/summary", summary="Security overview")
async def security_summary(
    _admin: AdminUser,
    session: DBSession,
    window_hours: int = Query(24, ge=1, le=24 * 30),
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    success = (
        await session.execute(
            select(func.count(LoginActivity.id))
            .where(LoginActivity.created_at >= since)
            .where(LoginActivity.success.is_(True))
        )
    ).scalar_one()
    failed = (
        await session.execute(
            select(func.count(LoginActivity.id))
            .where(LoginActivity.created_at >= since)
            .where(LoginActivity.success.is_(False))
        )
    ).scalar_one()
    rate_limits = (
        await session.execute(
            select(func.count(RateLimitEvent.id)).where(RateLimitEvent.created_at >= since)
        )
    ).scalar_one()

    return {
        "window_hours": window_hours,
        "login_success": int(success),
        "login_failed": int(failed),
        "rate_limit_events": int(rate_limits),
        "process_counters": {
            "auth_success": metrics.snapshot()["auth_success"],
            "auth_failures": metrics.snapshot()["auth_failures"],
            "rate_limit_hits": metrics.snapshot()["rate_limit_hits"],
        },
        "config": {
            "rate_limit_enabled": settings.RATE_LIMIT_ENABLED,
            "rate_limit_per_minute": settings.RATE_LIMIT_PER_MINUTE,
            "rate_limit_burst": settings.RATE_LIMIT_BURST,
            "hsts_enabled": settings.SECURITY_HSTS_ENABLED,
            "csp_enabled": settings.SECURITY_CSP_ENABLED,
        },
    }


@router.get("/security/login-activity", summary="Recent login attempts")
async def login_activity(
    _admin: AdminUser,
    session: DBSession,
    success: Optional[bool] = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(LoginActivity).order_by(desc(LoginActivity.created_at)).limit(limit)
    if success is not None:
        stmt = stmt.where(LoginActivity.success.is_(success))
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "email": r.email,
            "user_id": r.user_id,
            "success": r.success,
            "reason": r.reason,
            "ip_address": r.ip_address,
            "user_agent": r.user_agent,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/security/rate-limits", summary="Recent rate-limit trip events")
async def rate_limit_events(
    _admin: AdminUser,
    session: DBSession,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(RateLimitEvent).order_by(desc(RateLimitEvent.created_at)).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": r.id,
            "key": r.key,
            "path": r.path,
            "method": r.method,
            "ip_address": r.ip_address,
            "retry_after_seconds": r.retry_after_seconds,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ------------------------------------------------------------------ backups

@router.get("/backups", summary="Backup history")
async def list_backups(
    _admin: AdminUser,
    session: DBSession,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    svc = BackupService(session)
    rows = await svc.list_backups(limit=limit)
    last_ok = await svc.last_successful()
    return {
        "config": {
            "enabled": settings.BACKUP_ENABLED,
            "local_dir": settings.BACKUP_LOCAL_DIR,
            "retention_days": settings.BACKUP_RETENTION_DAYS,
            "s3_enabled": settings.BACKUP_S3_ENABLED,
            "s3_bucket": settings.BACKUP_S3_BUCKET,
            "schedule_cron": settings.BACKUP_SCHEDULE_CRON,
        },
        "last_successful": _backup_to_dto(last_ok) if last_ok else None,
        "items": [_backup_to_dto(r) for r in rows],
    }


@router.post("/backups/run", summary="Trigger a manual backup now")
async def run_backup(
    request: Request,
    admin: AdminUser,
    session: DBSession,
) -> dict[str, Any]:
    if not settings.BACKUP_ENABLED:
        raise ConflictError(
            "BACKUP_ENABLED is false",
            code="backups_disabled",
        )
    svc = BackupService(session)
    record = await svc.create_backup(kind="manual")
    await AuditService(session).record(
        action="backup.run",
        target_type="backup_record",
        target_id=record.id,
        actor=admin,
        details={"status": record.status, "size_bytes": record.size_bytes},
        ip_address=_client_ip(request),
    )
    await session.commit()
    return _backup_to_dto(record)


# ------------------------------------------------------------------ helpers

def _backup_to_dto(r: BackupRecord) -> dict[str, Any]:
    return {
        "id": r.id,
        "kind": r.kind,
        "status": r.status,
        "filename": r.filename,
        "local_path": r.local_path,
        "s3_bucket": r.s3_bucket,
        "s3_key": r.s3_key,
        "size_bytes": r.size_bytes,
        "checksum_sha256": r.checksum_sha256,
        "verified": r.verified,
        "uploaded_to_s3": r.uploaded_to_s3,
        "encrypted": r.encrypted,
        "duration_ms": r.duration_ms,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "error_message": r.error_message,
        "created_at": r.created_at,
    }


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
