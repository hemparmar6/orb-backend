"""Generic async repository base."""
from __future__ import annotations

from typing import Any, Generic, Sequence, Type, TypeVar

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Common CRUD helpers.

    Subclasses set `model` and add query methods specific to their aggregate.
    Sessions are injected; no repository ever opens its own session.
    """

    model: Type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id_: str) -> ModelT | None:
        return await self.session.get(self.model, id_)

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        filters: dict[str, Any] | None = None,
        order_by: Any | None = None,
    ) -> Sequence[ModelT]:
        stmt = select(self.model)
        if filters:
            for key, value in filters.items():
                if value is None:
                    continue
                stmt = stmt.where(getattr(self.model, key) == value)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        stmt = select(func.count()).select_from(self.model)
        if filters:
            for key, value in filters.items():
                if value is None:
                    continue
                stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def update(self, entity: ModelT, data: dict[str, Any]) -> ModelT:
        for key, value in data.items():
            if value is None and key not in getattr(entity, "__nullable_updatable__", set()):
                # Ignore Nones so PATCH is truly partial.
                continue
            setattr(entity, key, value)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()

    async def delete_by_id(self, id_: str) -> int:
        result = await self.session.execute(
            sa_delete(self.model).where(self.model.id == id_)  # type: ignore[attr-defined]
        )
        return result.rowcount or 0
