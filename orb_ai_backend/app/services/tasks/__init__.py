"""Daily portfolio snapshot task (Module 8).

Creates a PortfolioSnapshot row per user, once per calendar day, so
long-term analytics have historical equity/exposure data. Run periodically
from a scheduler (cron/APScheduler/K8s CronJob).

Usage:
    python -m app.services.tasks.snapshot_task
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.user import User
from app.services.portfolio_service import PortfolioService

logger = get_logger(__name__)


async def snapshot_all_users() -> int:
    """Persist a daily snapshot for every active user. Returns row count."""
    count = 0
    async with async_session_factory() as session:
        users = (await session.execute(
            select(User).where(User.is_active.is_(True))
        )).scalars().all()
        for user in users:
            try:
                await PortfolioService(session).create_daily_snapshot(user.id)
                count += 1
            except Exception:  # pragma: no cover
                logger.exception("snapshot_failed", extra={"user_id": user.id})
        await session.commit()
    logger.info("daily_snapshots_created", extra={"count": count})
    return count


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(snapshot_all_users())
