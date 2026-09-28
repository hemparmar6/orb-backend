"""Repository for BacktestRun."""
from __future__ import annotations

from sqlalchemy import desc, select

from app.models.backtest import BacktestRun
from app.repositories.base import BaseRepository


class BacktestRepository(BaseRepository[BacktestRun]):
    model = BacktestRun

    async def list_for_user(
        self, user_id: str, *, offset: int = 0, limit: int = 50
    ) -> list[BacktestRun]:
        stmt = (
            select(BacktestRun)
            .where(BacktestRun.user_id == user_id)
            .order_by(desc(BacktestRun.created_at))
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_for_user(self, user_id: str) -> int:
        return await self.count({"user_id": user_id})
