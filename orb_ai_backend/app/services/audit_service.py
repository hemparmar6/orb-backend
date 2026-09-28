"""Audit service — thin wrapper that records privileged actions."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.user import User
from app.repositories.audit_repository import AuditRepository


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AuditRepository(session)

    async def record(
        self,
        *,
        action: str,
        target_type: str,
        target_id: Optional[str] = None,
        actor: Optional[User] = None,
        details: Optional[dict[str, Any]] = None,
        ip_address: Optional[str] = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_user_id=actor.id if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or None,
            ip_address=ip_address,
        )
        return await self.repo.add(entry)
