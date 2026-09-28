"""Module 9 — AI analytics endpoints (portfolio metrics + snapshot)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.analytics.equity_curve import equity_curve
from app.analytics.metrics import compute_portfolio_metrics, expected_value, win_probability
from app.api.deps import CurrentUser, DBSession
from app.schemas.ai import AnalyticsMetricsOut, AnalyticsSnapshotOut
from app.services.ai_analytics_service import AIAnalyticsService

router = APIRouter()


@router.get("/portfolio", response_model=AnalyticsSnapshotOut)
async def portfolio_snapshot(user: CurrentUser, session: DBSession) -> AnalyticsSnapshotOut:
    svc = AIAnalyticsService(session)
    trades = await svc.load_trades(user.id)
    return AnalyticsSnapshotOut(
        scope="portfolio",
        scope_ref_id=None,
        metrics=AnalyticsMetricsOut(**compute_portfolio_metrics(trades)),
        equity_curve=equity_curve(trades),
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/win-probability")
async def win_prob(user: CurrentUser, session: DBSession, last_n: int = 100) -> dict:
    svc = AIAnalyticsService(session)
    trades = await svc.load_trades(user.id)
    return {"win_probability": win_probability(trades, last_n=last_n), "last_n": last_n}


@router.get("/expected-value")
async def ev(user: CurrentUser, session: DBSession) -> dict:
    svc = AIAnalyticsService(session)
    trades = await svc.load_trades(user.id)
    return {"expected_value": expected_value(trades)}


@router.post("/snapshot", response_model=AnalyticsSnapshotOut)
async def create_snapshot(user: CurrentUser, session: DBSession) -> AnalyticsSnapshotOut:
    svc = AIAnalyticsService(session)
    snap = await svc.snapshot_portfolio(user.id)
    return AnalyticsSnapshotOut(
        scope=snap.scope,
        scope_ref_id=snap.scope_ref_id,
        metrics=AnalyticsMetricsOut(**snap.metrics),
        equity_curve=snap.equity_curve,
        generated_at=snap.generated_at,
    )
