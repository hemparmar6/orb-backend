"""Revenue Dashboard REST endpoints (v1.1.0 Phase 2).

Base path: /api/v1/revenue
All endpoints require admin.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DBSession
from app.services.commerce.revenue_service import RevenueDashboardService
from app.services.commerce.strategy_analytics_service import StrategyAnalyticsService

router = APIRouter()


@router.get(
    "/summary",
    summary="[admin] Revenue KPIs summary (subscription, trial, coupon, strategy)",
)
async def revenue_summary(
    _: AdminUser,
    session: DBSession,
    period_days: int = Query(30, ge=1, le=365),
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
) -> dict:
    end = to_date or datetime.now(timezone.utc)
    start = from_date or (end - timedelta(days=period_days))
    svc = RevenueDashboardService(session)
    summary = await svc.summary(period_start=start, period_end=end)
    return summary.to_dict()


@router.get(
    "/strategy-analytics",
    summary="[admin] Per-strategy purchase & revenue breakdown",
)
async def strategy_analytics(
    _: AdminUser,
    session: DBSession,
    period_days: int = Query(30, ge=1, le=365),
    limit: int = Query(25, ge=1, le=200),
) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=period_days)
    svc = StrategyAnalyticsService(session)
    report = await svc.report(period_start=start, period_end=end, limit=limit)
    return report.to_dict()
