"""Trade repository."""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import select

from app.models.trade import Trade, TradeStatus
from app.repositories.base import BaseRepository


class TradeRepository(BaseRepository[Trade]):
    model = Trade

    async def list_for_user(
        self,
        user_id: str,
        *,
        strategy_id: str | None = None,
        status: TradeStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> Sequence[Trade]:
        stmt = select(Trade).where(Trade.user_id == user_id)
        if strategy_id is not None:
            stmt = stmt.where(Trade.strategy_id == strategy_id)
        if status is not None:
            stmt = stmt.where(Trade.status == status)
        stmt = stmt.order_by(Trade.created_at.desc()).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count_for_user(
        self,
        user_id: str,
        *,
        strategy_id: str | None = None,
        status: TradeStatus | None = None,
    ) -> int:
        from sqlalchemy import func

        stmt = select(func.count()).select_from(Trade).where(Trade.user_id == user_id)
        if strategy_id is not None:
            stmt = stmt.where(Trade.strategy_id == strategy_id)
        if status is not None:
            stmt = stmt.where(Trade.status == status)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def get_for_user(self, id_: str, user_id: str) -> Trade | None:
        stmt = select(Trade).where(Trade.id == id_, Trade.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
