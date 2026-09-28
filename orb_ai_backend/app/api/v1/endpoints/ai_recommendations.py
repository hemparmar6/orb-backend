"""Module 9 — AI recommendation endpoints (advisory only)."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from app.ai.service import get_ai_service
from app.analytics.metrics import compute_portfolio_metrics
from app.api.deps import CurrentUser, DBSession
from app.schemas.ai import RecommendationAction, RecommendationOut
from app.services.ai_analytics_service import AIAnalyticsService
from app.services.recommendation_service import RecommendationService

router = APIRouter()


@router.get("", response_model=List[RecommendationOut])
async def list_recs(user: CurrentUser, session: DBSession):
    return await RecommendationService(session).list_pending(user.id)


@router.post("/generate", response_model=List[RecommendationOut])
async def generate(user: CurrentUser, session: DBSession):
    trades = await AIAnalyticsService(session).load_trades(user.id)
    stats = compute_portfolio_metrics(trades)
    svc = RecommendationService(session, ai=get_ai_service())
    return await svc.generate(user.id, stats)


@router.post("/{rec_id}/action", response_model=RecommendationOut)
async def act(rec_id: str, action: RecommendationAction, user: CurrentUser, session: DBSession):
    row = await RecommendationService(session).act(user.id, rec_id, action.status)
    if row is None:
        raise HTTPException(status_code=404, detail="recommendation_not_found")
    return row
