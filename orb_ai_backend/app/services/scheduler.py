"""Background scheduler (Module 8).

Uses APScheduler's AsyncIOScheduler. Wired into the FastAPI lifespan
so jobs run in-process. Fully env-configurable and gracefully disabled
by default so tests / one-off scripts aren't affected.

Configurable knobs (env / Settings):
    SCHEDULER_ENABLED               (default: False)
    SCHEDULER_TIMEZONE              (default: UTC)
    SCHEDULER_SNAPSHOT_CRON         (default: "0 18 * * *"  — 18:00 UTC daily)
    SCHEDULER_WEEKLY_DIGEST_CRON    (default: "0 6 * * MON" — 06:00 UTC Monday)
    SCHEDULER_NOTIFICATION_RETRY_CRON (default: "*/5 * * * *" — every 5 min)
"""
from __future__ import annotations

from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def _make_trigger(expr: str) -> CronTrigger:
    return CronTrigger.from_crontab(expr, timezone=settings.SCHEDULER_TIMEZONE)


def _register_jobs(sched: AsyncIOScheduler) -> None:
    from app.services.tasks import snapshot_all_users
    from app.services.tasks.weekly_digest import run_weekly_digest
    from app.services.tasks.notification_retry import retry_failed_notifications

    if settings.SCHEDULER_SNAPSHOT_CRON:
        sched.add_job(
            snapshot_all_users,
            trigger=_make_trigger(settings.SCHEDULER_SNAPSHOT_CRON),
            id="daily_portfolio_snapshot",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("scheduler_job_registered",
                    extra={"job": "daily_portfolio_snapshot", "cron": settings.SCHEDULER_SNAPSHOT_CRON})

    if settings.SCHEDULER_WEEKLY_DIGEST_CRON:
        sched.add_job(
            run_weekly_digest,
            trigger=_make_trigger(settings.SCHEDULER_WEEKLY_DIGEST_CRON),
            id="weekly_pnl_digest",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("scheduler_job_registered",
                    extra={"job": "weekly_pnl_digest", "cron": settings.SCHEDULER_WEEKLY_DIGEST_CRON})

    if settings.SCHEDULER_NOTIFICATION_RETRY_CRON:
        sched.add_job(
            retry_failed_notifications,
            trigger=_make_trigger(settings.SCHEDULER_NOTIFICATION_RETRY_CRON),
            id="notification_retry",
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info("scheduler_job_registered",
                    extra={"job": "notification_retry", "cron": settings.SCHEDULER_NOTIFICATION_RETRY_CRON})

    # ---- Milestone 9 — audit-retention job -----------------------------
    if getattr(settings, "RISK_RETENTION_CRON", None):
        from app.services.tasks.retention_prune import prune_all as retention_prune_all
        sched.add_job(
            retention_prune_all,
            trigger=_make_trigger(settings.RISK_RETENTION_CRON),
            id="retention_prune",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        logger.info("scheduler_job_registered",
                    extra={"job": "retention_prune", "cron": settings.RISK_RETENTION_CRON})

    # ---- Trading Master Switch — auto PAPER revert ---------------------
    if getattr(settings, "TRADING_MODE_AUTO_REVERT_CRON", None):
        from app.services.tasks.trading_mode_revert import run_auto_revert_tick
        sched.add_job(
            run_auto_revert_tick,
            trigger=_make_trigger(settings.TRADING_MODE_AUTO_REVERT_CRON),
            id="trading_mode_auto_revert",
            replace_existing=True,
            misfire_grace_time=600,
        )
        logger.info("scheduler_job_registered",
                    extra={"job": "trading_mode_auto_revert",
                           "cron": settings.TRADING_MODE_AUTO_REVERT_CRON})


def get_scheduler() -> Optional[AsyncIOScheduler]:
    return _scheduler


async def start_scheduler() -> None:
    """Start the scheduler if enabled. Idempotent."""
    global _scheduler
    if not settings.SCHEDULER_ENABLED:
        logger.info("scheduler_disabled")
        return
    if _scheduler is not None:
        return
    sched = AsyncIOScheduler(timezone=settings.SCHEDULER_TIMEZONE)
    _register_jobs(sched)
    sched.start()
    _scheduler = sched
    logger.info("scheduler_started", extra={"tz": settings.SCHEDULER_TIMEZONE})


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:  # pragma: no cover
        logger.exception("scheduler_shutdown_failed")
    _scheduler = None
    logger.info("scheduler_stopped")
