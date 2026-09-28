"""Reports endpoints (Module 8)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.models.report import ReportFormat, ReportRun, ReportStatus, ReportType
from app.schemas.analytics import ReportPreview, ReportRunRead
from app.schemas.common import PaginatedResponse
from app.services.audit_service import AuditService
from app.services.reports.csv_export import CSVExporter
from app.services.reports.data_layer import ReportDataLayer
from app.services.reports.pdf import PDFReportBuilder
from app.services.subscriptions import FeatureFlag, FeatureGate

router = APIRouter()

_ALLOWED_TYPES = {t.value for t in ReportType}

# Report type -> optional feature flag that must be enabled to run it.
# Types without a mapping are considered "always available".
REPORT_TYPE_FEATURE: dict[str, FeatureFlag] = {
    "strategy_performance": FeatureFlag.REPORT_TYPE_STRATEGY_PERFORMANCE,
    "broker_activity": FeatureFlag.REPORT_TYPE_BROKER_ACTIVITY,
}
FORMAT_FEATURE: dict[str, FeatureFlag] = {
    "pdf": FeatureFlag.REPORT_FORMAT_PDF,
    "csv": FeatureFlag.REPORT_FORMAT_CSV,
    "xlsx": FeatureFlag.REPORT_FORMAT_XLSX,
}


async def _require_feature(
    session, user, feature: FeatureFlag, *, subject: str
) -> None:
    if not await FeatureGate(session).is_enabled(user, feature):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"{subject} requires the '{feature.value}' feature — please upgrade your plan.",
        )


def _parse_type(report_type: str) -> ReportType:
    if report_type not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown report type. Allowed: {sorted(_ALLOWED_TYPES)}",
        )
    return ReportType(report_type)


@router.get("/preview/{report_type}", response_model=ReportPreview, summary="Preview report data")
async def preview(
    report_type: str,
    current_user: CurrentUser,
    session: DBSession,
    period_start: Optional[datetime] = Query(None),
    period_end: Optional[datetime] = Query(None),
) -> ReportPreview:
    _parse_type(report_type)
    if period_start is None or period_end is None:
        period_start, period_end = ReportDataLayer.period_for(report_type)
    ctx = await ReportDataLayer(session).build_context(
        user_id=current_user.id,
        user_email=current_user.email,
        report_type=report_type,
        period_start=period_start,
        period_end=period_end,
    )
    return ReportPreview(
        report_type=report_type,
        period_start=period_start.isoformat() if period_start else None,
        period_end=period_end.isoformat() if period_end else None,
        portfolio_summary=ctx.portfolio_summary,
        analytics_summary=ctx.analytics_summary,
        trade_count=len(ctx.trades),
    )


@router.get(
    "/generate/{report_type}",
    summary="Generate a report (PDF or CSV, streamed)",
    response_class=Response,
)
async def generate(
    report_type: str,
    current_user: CurrentUser,
    session: DBSession,
    format: Literal["pdf", "csv"] = Query("pdf"),
    period_start: Optional[datetime] = Query(None),
    period_end: Optional[datetime] = Query(None),
    backtest_id: Optional[str] = Query(None),
) -> Response:
    rtype = _parse_type(report_type)
    fmt = ReportFormat.PDF if format == "pdf" else ReportFormat.CSV
    if period_start is None or period_end is None:
        period_start, period_end = ReportDataLayer.period_for(report_type)

    # Feature-gate: report type + format.
    tflag = REPORT_TYPE_FEATURE.get(report_type)
    if tflag is not None:
        await _require_feature(session, current_user, tflag, subject=f"Report '{report_type}'")
    ffl = FORMAT_FEATURE.get(format)
    if ffl is not None:
        await _require_feature(session, current_user, ffl,
                               subject=f"Format '{format}'")

    run = ReportRun(
        user_id=current_user.id,
        report_type=rtype,
        report_format=fmt,
        status=ReportStatus.PENDING,
        params={
            "period_start": period_start.isoformat() if period_start else None,
            "period_end": period_end.isoformat() if period_end else None,
            "backtest_id": backtest_id,
        },
    )
    session.add(run)
    await session.flush()

    try:
        ctx = await ReportDataLayer(session).build_context(
            user_id=current_user.id,
            user_email=current_user.email,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            backtest_id=backtest_id,
        )
        if format == "pdf":
            body = PDFReportBuilder().build(ctx)
            media = "application/pdf"
            ext = "pdf"
        else:
            body = CSVExporter().build(ctx)
            media = "text/csv"
            ext = "csv"
        filename = f"orb_ai_{report_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}"
        run.status = ReportStatus.GENERATED
        run.byte_size = len(body)
        run.row_count = len(ctx.trades)
        run.filename = filename
        run.generated_at = datetime.utcnow()
    except Exception as e:  # noqa: BLE001
        run.status = ReportStatus.FAILED
        run.error_message = str(e)[:500]
        await AuditService(session).record(
            action="report.failed", target_type="report_run",
            target_id=run.id, actor=current_user, details={"error": str(e)[:200]},
        )
        await session.commit()
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    await AuditService(session).record(
        action="report.generated", target_type="report_run",
        target_id=run.id, actor=current_user,
        details={"type": report_type, "format": format, "bytes": run.byte_size},
    )
    await session.commit()

    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/history",
    response_model=PaginatedResponse[ReportRunRead],
    summary="List report generation history",
)
async def history(
    current_user: CurrentUser,
    session: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse[ReportRunRead]:
    offset = (page - 1) * page_size
    stmt = (
        select(ReportRun)
        .where(ReportRun.user_id == current_user.id)
        .order_by(ReportRun.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = (await session.execute(stmt)).scalars().all()
    total_stmt = select(ReportRun).where(ReportRun.user_id == current_user.id)
    total = len((await session.execute(total_stmt)).scalars().all())
    return PaginatedResponse[ReportRunRead](
        items=[ReportRunRead.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size,
    )
