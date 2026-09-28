"""AuditLog repository."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, entry: AuditLog) -> AuditLog:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list(
        self,
        *,
        actor_user_id: Optional[str] = None,
        action: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[AuditLog], int]:
        base = select(AuditLog)
        if actor_user_id:
            base = base.where(AuditLog.actor_user_id == actor_user_id)
        if action:
            base = base.where(AuditLog.action == action)
        if target_type:
            base = base.where(AuditLog.target_type == target_type)
        if target_id:
            base = base.where(AuditLog.target_id == target_id)

        total = (await self.session.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar_one()

        rows = (
            await self.session.execute(
                base.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
            )
        ).scalars().all()
        return list(rows), int(total)
