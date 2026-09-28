"""Scheduler admin endpoints (Module 8)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminUser
from app.services.scheduler import get_scheduler
from app.services.tasks import snapshot_all_users
from app.services.tasks.notification_retry import retry_failed_notifications
from app.services.tasks.weekly_digest import run_weekly_digest

router = APIRouter()


@router.get("/status", summary="Scheduler status + registered jobs")
async def scheduler_status(_: AdminUser) -> dict[str, Any]:
    sched = get_scheduler()
    if sched is None:
        return {"enabled": False, "jobs": []}
    return {
        "enabled": True,
        "jobs": [
            {
                "id": j.id,
                "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
                "trigger": str(j.trigger),
            }
            for j in sched.get_jobs()
        ],
    }


@router.post("/trigger/{job}", summary="Run a scheduler job immediately (admin)")
async def trigger_job(job: str, _: AdminUser) -> dict[str, Any]:
    if job == "daily_portfolio_snapshot":
        n = await snapshot_all_users()
        return {"job": job, "processed": n}
    if job == "weekly_pnl_digest":
        return {"job": job, **await run_weekly_digest()}
    if job == "notification_retry":
        n = await retry_failed_notifications()
        return {"job": job, "retried": n}
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown job '{job}'. Options: daily_portfolio_snapshot, weekly_pnl_digest, notification_retry",
    )
