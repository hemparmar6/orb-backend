"""Strategy service."""
from __future__ import annotations

from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.strategy import Strategy
from app.models.user import User
from app.repositories.strategy_repository import StrategyRepository
from app.schemas.strategy import StrategyCreate, StrategyUpdate


class StrategyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = StrategyRepository(session)

    async def create(self, user: User, payload: StrategyCreate) -> Strategy:
        strat = Strategy(user_id=user.id, **payload.model_dump())
        await self.repo.add(strat)
        await self.session.commit()
        await self.session.refresh(strat)
        return strat

    async def list_for_user(
        self, user: User, *, offset: int = 0, limit: int = 50
    ) -> tuple[Sequence[Strategy], int]:
        items = await self.repo.list_for_user(user.id, offset=offset, limit=limit)
        total = await self.repo.count_for_user(user.id)
        return items, total

    async def get_for_user(self, user: User, strategy_id: str) -> Strategy:
        strat = await self.repo.get_for_user(strategy_id, user.id)
        if strat is None:
            raise NotFoundError("Strategy not found")
        return strat

    async def update(
        self, user: User, strategy_id: str, payload: StrategyUpdate
    ) -> Strategy:
        strat = await self.get_for_user(user, strategy_id)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(strat, key, value)
        await self.session.commit()
        await self.session.refresh(strat)
        return strat

    async def delete(self, user: User, strategy_id: str) -> None:
        strat = await self.get_for_user(user, strategy_id)
        await self.repo.delete(strat)
        await self.session.commit()
