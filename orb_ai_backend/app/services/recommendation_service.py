"""Recommendation service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts.registry import LATEST
from app.ai.service import AIRequestContext, AIService
from app.models import AIRecommendation


PROMPT_NAME = "recommendation"
PROMPT_VERSION = LATEST[PROMPT_NAME]


class RecommendationService:
    def __init__(self, session: AsyncSession, ai: AIService | None = None) -> None:
        self.session = session
        self.ai = ai

    async def generate(self, user_id: str, portfolio_stats: Dict[str, Any]) -> List[AIRecommendation]:
        assert self.ai is not None, "AI service required for generate()"
        ctx = AIRequestContext(
            user_id=user_id,
            request_type="recommendation",
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
            payload={"portfolio_stats": portfolio_stats},
        )
        resp = await self.ai.analyse(ctx)
        recs = list((resp.content or {}).get("recommendations") or [])

        created: List[AIRecommendation] = []
        now = datetime.now(timezone.utc)
        for r in recs:
            row = AIRecommendation(
                user_id=user_id,
                type=str(r.get("type", "strategy"))[:32],
                action=str(r.get("action", "")).strip()[:1000],
                rationale=(r.get("rationale") or None),
                confidence=_num(r.get("confidence"), 0.0),
                priority=str(r.get("priority", "medium"))[:8],
                status="pending",
                provider=resp.provider,
                model=resp.model,
                prompt_version=PROMPT_VERSION,
                expires_at=now + timedelta(days=7),
            )
            self.session.add(row)
            created.append(row)
        await self.session.commit()
        for row in created:
            await self.session.refresh(row)
        return created

    async def list_pending(self, user_id: str, limit: int = 50) -> List[AIRecommendation]:
        stmt = (
            select(AIRecommendation)
            .where(AIRecommendation.user_id == user_id, AIRecommendation.status == "pending")
            .order_by(AIRecommendation.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def act(self, user_id: str, rec_id: str, status: str) -> AIRecommendation | None:
        stmt = select(AIRecommendation).where(
            AIRecommendation.id == rec_id, AIRecommendation.user_id == user_id
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        row.status = status
        row.acted_on_at = datetime.now(timezone.utc)
        await self.session.commit()
        await self.session.refresh(row)
        return row


def _num(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
