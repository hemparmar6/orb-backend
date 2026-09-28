"""Strategy repository."""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import select

from app.models.strategy import Strategy
from app.repositories.base import BaseRepository


class StrategyRepository(BaseRepository[Strategy]):
    model = Strategy

    async def list_for_user(
        self,
        user_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> Sequence[Strategy]:
        stmt = (
            select(Strategy)
            .where(Strategy.user_id == user_id)
            .order_by(Strategy.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_for_user(self, user_id: str) -> int:
        return await self.count(filters={"user_id": user_id})

    async def get_for_user(self, id_: str, user_id: str) -> Strategy | None:
        stmt = select(Strategy).where(Strategy.id == id_, Strategy.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
