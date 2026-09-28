"""AffiliateService — registration, approval workflow, code management."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.affiliate import Affiliate, AffiliateStatus, PayoutMethod
from app.models.user import User
from app.services.affiliate.program_service import ProgramService, generate_code


class AffiliateError(Exception):
    pass


class AlreadyRegistered(AffiliateError):
    pass


class NotEligible(AffiliateError):
    pass


class AffiliateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_user(self, user_id: str) -> Optional[Affiliate]:
        return await self.session.scalar(
            select(Affiliate).where(Affiliate.user_id == user_id)
        )

    async def by_code(self, code: str) -> Optional[Affiliate]:
        return await self.session.scalar(
            select(Affiliate).where(Affiliate.code == code.upper())
        )

    async def register(
        self,
        user: User,
        *,
        display_name: Optional[str] = None,
        application_notes: Optional[str] = None,
        payout_method: str = "wallet_only",
        payout_details: Optional[dict] = None,
    ) -> Affiliate:
        program = await ProgramService(self.session).get()
        if not program.is_enabled:
            raise NotEligible("Affiliate program is disabled")
        existing = await self.by_user(user.id)
        if existing is not None:
            raise AlreadyRegistered("User already has an affiliate profile")
        # Reserve a unique code (retry-if-collision).
        for _ in range(6):
            code = generate_code()
            if (await self.by_code(code)) is None:
                break
        else:
            code = generate_code(10)
        a = Affiliate(
            user_id=user.id, code=code,
            display_name=display_name or (user.full_name or user.email),
            application_notes=application_notes,
            payout_method=PayoutMethod(payout_method),
            payout_details=payout_details,
            status=AffiliateStatus.PENDING,
        )
        self.session.add(a)
        await self.session.flush()
        return a

    async def approve(self, affiliate: Affiliate, *, admin_id: str) -> Affiliate:
        affiliate.status = AffiliateStatus.APPROVED
        affiliate.approved_at = datetime.now(timezone.utc)
        affiliate.approved_by = admin_id
        affiliate.rejected_reason = None
        await self.session.flush()
        return affiliate

    async def reject(self, affiliate: Affiliate, *, reason: str, admin_id: str) -> Affiliate:
        affiliate.status = AffiliateStatus.REJECTED
        affiliate.rejected_reason = reason
        affiliate.approved_by = admin_id
        await self.session.flush()
        return affiliate

    async def suspend(self, affiliate: Affiliate, *, reason: str, admin_id: str) -> Affiliate:
        affiliate.status = AffiliateStatus.SUSPENDED
        affiliate.rejected_reason = reason
        affiliate.approved_by = admin_id
        await self.session.flush()
        return affiliate

    async def list_all(
        self, *, status: Optional[AffiliateStatus] = None, limit: int = 100,
    ) -> list[Affiliate]:
        stmt = select(Affiliate)
        if status is not None:
            stmt = stmt.where(Affiliate.status == status)
        stmt = stmt.order_by(Affiliate.created_at.desc()).limit(limit)
        return list(await self.session.scalars(stmt))
