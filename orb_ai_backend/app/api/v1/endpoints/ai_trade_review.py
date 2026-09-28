"""Module 9 — AI trade review endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.ai.service import get_ai_service
from app.api.deps import CurrentUser, DBSession
from app.models import AITradeReview, TradeStatus
from app.schemas.ai import TradeReviewOut
from app.services.trade_journal_service import TradeJournalService

router = APIRouter()


@router.get("", response_model=List[TradeReviewOut])
async def list_reviews(user: CurrentUser, session: DBSession, limit: int = 50):
    stmt = (
        select(AITradeReview)
        .where(AITradeReview.user_id == user.id)
        .order_by(AITradeReview.created_at.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return rows


@router.get("/{trade_id}", response_model=TradeReviewOut)
async def get_review(trade_id: str, user: CurrentUser, session: DBSession):
    svc = TradeJournalService(session, ai=get_ai_service())
    row = await svc.get_existing(trade_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="review_not_found")
    return row


@router.post("/{trade_id}/generate", response_model=TradeReviewOut)
async def generate_review(trade_id: str, user: CurrentUser, session: DBSession):
    svc = TradeJournalService(session, ai=get_ai_service())
    trade = await svc.get_trade(trade_id, user.id)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade_not_found")
    if trade.status != TradeStatus.CLOSED:
        raise HTTPException(status_code=400, detail="trade_not_closed")
    return await svc.review_trade(user.id, trade)
