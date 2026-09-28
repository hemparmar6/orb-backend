"""Weekly P&L Digest scheduled task (Module 8).

Modular, feature-flag aware weekly report:
- Uses the existing ReportDataLayer + PDFReportBuilder for the PDF body.
- Uses NotificationService to deliver the digest via *any* configured
  channel (in-app is always available; email / telegram / push are
  used if the user has enabled them and their provider is configured).
- Records every generation in the audit log.
- Skips users the ``WeeklyDigestFeatureGate`` returns False for — this is
  a thin adapter over the generic ``FeatureGate`` service, so gating
  logic is defined ONCE in the subscription system, not scattered here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.models.notification import NotificationEvent, NotificationSeverity
from app.models.report import ReportFormat, ReportRun, ReportStatus, ReportType
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService
from app.services.reports.data_layer import ReportDataLayer
from app.services.reports.pdf import PDFReportBuilder
from app.services.subscriptions import FeatureFlag, FeatureGate

logger = get_logger(__name__)


class WeeklyDigestFeatureGate:
    """Thin adapter over the generic ``FeatureGate`` service.

    All eligibility rules for the weekly digest live in the plan
    definitions (see ``services/subscriptions/plan_seed.py``). Swap
    a user's plan and this gate follows automatically — no code changes.
    """

    def __init__(self, session) -> None:
        self._gate = FeatureGate(session)

    async def is_eligible(self, user: User) -> bool:
        if not user.is_active:
            return False
        return await self._gate.is_enabled(user, FeatureFlag.REPORT_WEEKLY_DIGEST)


async def _process_user(user: User) -> bool:
    """Generate + deliver the weekly digest for one user. Returns success."""
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=7)

    async with async_session_factory() as session:
        gate = WeeklyDigestFeatureGate(session)
        if not await gate.is_eligible(user):
            return False
        run = ReportRun(
            user_id=user.id,
            report_type=ReportType.WEEKLY,
            report_format=ReportFormat.PDF,
            status=ReportStatus.PENDING,
            params={
                "source": "scheduled_weekly_digest",
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            },
        )
        session.add(run)
        await session.flush()

        try:
            ctx = await ReportDataLayer(session).build_context(
                user_id=user.id,
                user_email=user.email,
                report_type="weekly",
                period_start=period_start,
                period_end=period_end,
            )
            pdf_bytes = PDFReportBuilder().build(ctx)
            filename = f"orb_ai_weekly_digest_{period_end.strftime('%Y%m%d')}.pdf"
            run.status = ReportStatus.GENERATED
            run.byte_size = len(pdf_bytes)
            run.row_count = len(ctx.trades)
            run.filename = filename
            run.generated_at = datetime.now(timezone.utc)
        except Exception as e:  # noqa: BLE001
            run.status = ReportStatus.FAILED
            run.error_message = str(e)[:500]
            await AuditService(session).record(
                action="report.scheduled.failed",
                target_type="report_run",
                target_id=run.id,
                actor=user,
                details={"job": "weekly_digest", "error": str(e)[:200]},
            )
            await session.commit()
            logger.exception("weekly_digest_failed", extra={"user_id": user.id})
            return False

        # Deliver via NotificationService (in-app + any enabled channel)
        try:
            summary = ctx.analytics_summary or {}
            portfolio = ctx.portfolio_summary or {}
            body = (
                f"Your weekly P&L digest is ready.\n\n"
                f"Net P&L: {summary.get('net_pnl', 0)}\n"
                f"Trades: {summary.get('total_trades', 0)}\n"
                f"Win Rate: {(summary.get('win_rate', 0) or 0) * 100:.1f}%\n"
                f"Equity: {portfolio.get('equity', 0)}"
            )
            await NotificationService(session).notify(
                user=user,
                event=NotificationEvent.SYSTEM_ALERT,
                title="ORB AI — Weekly P&L Digest",
                body=body,
                severity=NotificationSeverity.INFO,
                payload={
                    "report_run_id": run.id,
                    "report_type": "weekly",
                    "filename": run.filename,
                    "byte_size": run.byte_size,
                    "trades": len(ctx.trades),
                    "net_pnl": summary.get("net_pnl", 0),
                },
            )
        except Exception:  # pragma: no cover
            logger.exception("weekly_digest_notify_failed", extra={"user_id": user.id})

        await AuditService(session).record(
            action="report.scheduled.generated",
            target_type="report_run",
            target_id=run.id,
            actor=user,
            details={
                "job": "weekly_digest",
                "bytes": run.byte_size,
                "trades": run.row_count,
            },
        )
        await session.commit()
        return True


async def run_weekly_digest(users: Iterable[User] | None = None) -> dict[str, int]:
    """Entry point invoked by the scheduler (or on-demand from admin API).

    Returns a summary dict {"processed": N, "sent": M, "skipped": K}.
    """
    processed = 0
    sent = 0
    skipped = 0

    if users is None:
        async with async_session_factory() as session:
            users = list((await session.execute(
                select(User).where(User.is_active.is_(True))
            )).scalars().all())

    for user in users:
        processed += 1
        ok = await _process_user(user)
        if ok:
            sent += 1
        else:
            skipped += 1

    logger.info("weekly_digest_completed",
                extra={"processed": processed, "sent": sent, "skipped": skipped})
    return {"processed": processed, "sent": sent, "skipped": skipped}


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run_weekly_digest())
