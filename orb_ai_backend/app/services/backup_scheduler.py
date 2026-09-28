"""Module 10 — automated PostgreSQL backup scheduler.

Uses APScheduler (already a dependency for the Module 8 scheduler).
Runs a cron job on ``settings.BACKUP_SCHEDULE_CRON`` when both
``BACKUP_ENABLED`` and the scheduler engine are configured.

Startup is best-effort — if APScheduler or pg_dump is unavailable, the
subsystem stays disabled and the rest of the app boots normally.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.backup.scheduler")

_scheduler = None  # type: ignore[var-annotated]


async def start_backup_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    if not settings.BACKUP_ENABLED:
        logger.info("backup_scheduler_disabled")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except Exception:  # pragma: no cover
        logger.warning("backup_scheduler_skipped_no_apscheduler")
        return

    try:
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(
            _run_scheduled_backup,
            trigger=CronTrigger.from_crontab(settings.BACKUP_SCHEDULE_CRON, timezone="UTC"),
            id="orb_ai_backup",
            replace_existing=True,
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info(
            "backup_scheduler_started",
            extra={"cron": settings.BACKUP_SCHEDULE_CRON},
        )
    except Exception:  # pragma: no cover
        logger.exception("backup_scheduler_start_failed")


async def stop_backup_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:  # pragma: no cover
        pass
    _scheduler = None
    logger.info("backup_scheduler_stopped")


async def _run_scheduled_backup() -> None:
    """Cron entry-point.

    Opens its own DB session because APScheduler jobs run outside the
    request lifecycle.
    """
    try:
        from app.db.session import async_session_factory
        from app.services.backup_service import BackupService

        async with async_session_factory() as session:
            svc = BackupService(session)
            record = await svc.create_backup(kind="scheduled")
            logger.info(
                "scheduled_backup_finished",
                extra={
                    "category": "audit",
                    "status": record.status,
                    "size_bytes": record.size_bytes,
                    "uploaded_to_s3": record.uploaded_to_s3,
                },
            )
    except Exception:
        logger.exception("scheduled_backup_failed", extra={"category": "error"})


def get_scheduler() -> Optional[object]:
    return _scheduler
