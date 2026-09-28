"""Analytics endpoints (Module 8)."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.schemas.analytics import (
    AnalyticsSummaryOut,
    DrawdownPoint,
    EquityPoint,
    JournalItem,
    MonthlyPerfDetail,
)
from app.schemas.common import PaginatedResponse
from app.services.analytics_service import AnalyticsService

router = APIRouter()


@router.get("/summary", response_model=AnalyticsSummaryOut, summary="Full analytics summary")
async def summary(current_user: CurrentUser, session: DBSession) -> AnalyticsSummaryOut:
    data = await AnalyticsService(session).summary(current_user.id)
    return AnalyticsSummaryOut(**asdict(data))


@router.get("/equity-curve", response_model=list[EquityPoint], summary="Equity curve")
async def equity_curve(current_user: CurrentUser, session: DBSession) -> list[EquityPoint]:
    data = await AnalyticsService(session).equity_curve(current_user.id)
    return [EquityPoint(**p) for p in data]


@router.get("/drawdown", response_model=list[DrawdownPoint], summary="Drawdown series")
async def drawdown(current_user: CurrentUser, session: DBSession) -> list[DrawdownPoint]:
    data = await AnalyticsService(session).drawdown_series(current_user.id)
    return [DrawdownPoint(**p) for p in data]


@router.get(
    "/monthly",
    response_model=list[MonthlyPerfDetail],
    summary="Monthly performance breakdown",
)
async def monthly(current_user: CurrentUser, session: DBSession) -> list[MonthlyPerfDetail]:
    data = await AnalyticsService(session).monthly_performance(current_user.id)
    return [MonthlyPerfDetail(**m) for m in data]


@router.get(
    "/journal",
    response_model=PaginatedResponse[JournalItem],
    summary="Trade journal",
)
async def journal(
    current_user: CurrentUser,
    session: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    symbol: str | None = Query(None),
) -> PaginatedResponse[JournalItem]:
    offset = (page - 1) * page_size
    items, total = await AnalyticsService(session).journal(
        current_user.id, offset=offset, limit=page_size, symbol=symbol
    )
    return PaginatedResponse[JournalItem](
        items=[JournalItem(**i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )
