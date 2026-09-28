"""Milestone 9 — Automatic audit retention.

Deletes ``execution_safety_events`` older than the admin-configured
``event_retention_days`` and ``risk_breaches`` older than
``RISK_BREACH_RETENTION_DAYS``.

Scheduled by ``app.services.scheduler`` under the job id
``retention_prune`` when ``SCHEDULER_ENABLED=true``. Also callable
directly for tests / one-shot ops.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.services.execution_safety_service import ExecutionSafetyService
from app.services.risk_management_service import RiskManagementService

logger = get_logger(__name__)


async def prune_all() -> dict[str, int]:
    """Run every retention prune in one transaction per store."""
    results = {"execution_safety_events": 0, "risk_breaches": 0}
    async with async_session_factory() as session:
        try:
            svc = ExecutionSafetyService(session)
            results["execution_safety_events"] = await svc.prune_old_events()
            await session.commit()
        except Exception:  # pragma: no cover
            await session.rollback()
            logger.exception("execution_safety_prune_failed")

    async with async_session_factory() as session:
        try:
            svc = RiskManagementService(session)
            retention = getattr(settings, "RISK_BREACH_RETENTION_DAYS", 90)
            results["risk_breaches"] = await svc.prune_old_breaches(retention)
            await session.commit()
        except Exception:  # pragma: no cover
            await session.rollback()
            logger.exception("risk_breach_prune_failed")

    logger.info("retention_prune_complete", extra=results)
    return results
