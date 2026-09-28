"""Portfolio endpoints (Module 8)."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.schemas.analytics import (
    Allocation,
    DailyPerformance,
    Holding,
    MonthlyPerformance,
    PortfolioSummary,
)
from app.services.portfolio_service import PortfolioService

router = APIRouter()


@router.get("/summary", response_model=PortfolioSummary, summary="Portfolio summary")
async def summary(current_user: CurrentUser, session: DBSession) -> PortfolioSummary:
    data = await PortfolioService(session).summary(current_user.id)
    return PortfolioSummary(**data)


@router.get("/holdings", response_model=list[Holding], summary="Current holdings")
async def holdings(current_user: CurrentUser, session: DBSession) -> list[Holding]:
    data = await PortfolioService(session).holdings(current_user.id)
    return [Holding(**h) for h in data]


@router.get("/allocation", response_model=list[Allocation], summary="Capital allocation by symbol")
async def allocation(current_user: CurrentUser, session: DBSession) -> list[Allocation]:
    data = await PortfolioService(session).allocation(current_user.id)
    return [Allocation(**a) for a in data]


@router.get(
    "/performance/daily",
    response_model=list[DailyPerformance],
    summary="Daily P&L for the last N days",
)
async def daily(
    current_user: CurrentUser,
    session: DBSession,
    days: int = Query(30, ge=1, le=365),
) -> list[DailyPerformance]:
    data = await PortfolioService(session).daily_performance(current_user.id, days)
    return [DailyPerformance(**d) for d in data]


@router.get(
    "/performance/monthly",
    response_model=list[MonthlyPerformance],
    summary="Monthly P&L for the last N months",
)
async def monthly(
    current_user: CurrentUser,
    session: DBSession,
    months: int = Query(12, ge=1, le=60),
) -> list[MonthlyPerformance]:
    data = await PortfolioService(session).monthly_performance(current_user.id, months)
    return [MonthlyPerformance(**d) for d in data]


@router.post("/snapshot", response_model=PortfolioSummary, summary="Force a daily snapshot")
async def snapshot(current_user: CurrentUser, session: DBSession) -> PortfolioSummary:
    await PortfolioService(session).create_daily_snapshot(current_user.id)
    await session.commit()
    data = await PortfolioService(session).summary(current_user.id)
    return PortfolioSummary(**data)
