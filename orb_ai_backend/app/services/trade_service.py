"""Trade service."""
from __future__ import annotations

from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.trade import Trade, TradeStatus
from app.models.user import User
from app.repositories.strategy_repository import StrategyRepository
from app.repositories.trade_repository import TradeRepository
from app.schemas.trade import TradeCreate, TradeUpdate


class TradeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TradeRepository(session)
        self.strategies = StrategyRepository(session)

    async def create(self, user: User, payload: TradeCreate) -> Trade:
        # If a strategy is referenced, make sure it belongs to this user.
        if payload.strategy_id:
            strat = await self.strategies.get_for_user(payload.strategy_id, user.id)
            if strat is None:
                raise NotFoundError("Referenced strategy not found")

        data = payload.model_dump()
        trade = Trade(user_id=user.id, **data)
        await self.repo.add(trade)
        await self.session.commit()
        await self.session.refresh(trade)
        return trade

    async def list_for_user(
        self,
        user: User,
        *,
        strategy_id: str | None = None,
        status: TradeStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[Sequence[Trade], int]:
        items = await self.repo.list_for_user(
            user.id,
            strategy_id=strategy_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        total = await self.repo.count_for_user(
            user.id, strategy_id=strategy_id, status=status
        )
        return items, total

    async def get_for_user(self, user: User, trade_id: str) -> Trade:
        trade = await self.repo.get_for_user(trade_id, user.id)
        if trade is None:
            raise NotFoundError("Trade not found")
        return trade

    async def update(
        self, user: User, trade_id: str, payload: TradeUpdate
    ) -> Trade:
        trade = await self.get_for_user(user, trade_id)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(trade, key, value)
        await self.session.commit()
        await self.session.refresh(trade)
        return trade

    async def delete(self, user: User, trade_id: str) -> None:
        trade = await self.get_for_user(user, trade_id)
        await self.repo.delete(trade)
        await self.session.commit()
