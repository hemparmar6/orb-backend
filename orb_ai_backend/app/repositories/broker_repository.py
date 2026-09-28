"""BrokerAccount repository."""
from __future__ import annotations

from typing import Sequence

from sqlalchemy import select

from app.models.broker import BrokerAccount, BrokerType
from app.repositories.base import BaseRepository


class BrokerAccountRepository(BaseRepository[BrokerAccount]):
    model = BrokerAccount

    async def list_for_user(self, user_id: str) -> Sequence[BrokerAccount]:
        stmt = (
            select(BrokerAccount)
            .where(BrokerAccount.user_id == user_id)
            .order_by(BrokerAccount.created_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_for_user(self, id_: str, user_id: str) -> BrokerAccount | None:
        stmt = select(BrokerAccount).where(
            BrokerAccount.id == id_, BrokerAccount.user_id == user_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_duplicate(
        self, user_id: str, broker_type: BrokerType, alias: str
    ) -> BrokerAccount | None:
        stmt = select(BrokerAccount).where(
            BrokerAccount.user_id == user_id,
            BrokerAccount.broker_type == broker_type,
            BrokerAccount.alias == alias,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
