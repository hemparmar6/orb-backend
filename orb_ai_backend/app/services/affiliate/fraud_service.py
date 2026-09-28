"""FraudService — foundational abuse detection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.affiliate import (
    Affiliate,
    FraudFlag,
    FraudSeverity,
    ReferralClick,
)
from app.models.commerce import Order, OrderKind, OrderStatus
from app.models.user import User


class FraudService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def flag(
        self,
        *,
        subject_type: str,
        subject_id: str,
        reason: str,
        severity: FraudSeverity = FraudSeverity.MEDIUM,
        details: Optional[dict] = None,
    ) -> FraudFlag:
        f = FraudFlag(
            subject_type=subject_type, subject_id=subject_id,
            reason=reason, severity=severity,
            details=details or {}, is_active=True,
        )
        self.session.add(f)
        await self.session.flush()
        return f

    # ---------------------------------------------------------- checks

    async def check_self_referral(self, *, affiliate: Affiliate, user_id: str) -> Optional[FraudFlag]:
        if affiliate.user_id != user_id:
            return None
        return await self.flag(
            subject_type="user", subject_id=user_id,
            reason="self_referral", severity=FraudSeverity.HIGH,
            details={"affiliate_id": affiliate.id},
        )

    async def check_duplicate_ip(
        self, *, ip: str, affiliate_id: str, window_hours: int = 24, threshold: int = 5,
    ) -> Optional[FraudFlag]:
        """Flag if same IP produced too many clicks for one affiliate recently."""
        if not ip:
            return None
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        count = await self.session.scalar(
            select(func.count(ReferralClick.id)).where(
                ReferralClick.affiliate_id == affiliate_id,
                ReferralClick.ip == ip,
                ReferralClick.created_at >= since,
            )
        )
        if (count or 0) < threshold:
            return None
        return await self.flag(
            subject_type="click", subject_id=affiliate_id,
            reason="duplicate_ip_burst", severity=FraudSeverity.MEDIUM,
            details={"ip": ip, "count": int(count or 0), "window_hours": window_hours},
        )

    async def check_multiple_trials_same_ip(
        self, *, ip: str, window_days: int = 30, threshold: int = 3,
    ) -> Optional[FraudFlag]:
        """Flag when several trial orders originate from the same IP."""
        if not ip:
            return None
        # Trial orders correlate to trial subscriptions. Here we
        # approximate by counting PAID trial orders whose users share
        # a click IP.
        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        rows = await self.session.execute(
            select(func.count(Order.id.distinct()))
            .join(ReferralClick, ReferralClick.ip == ip)
            .where(
                Order.kind == OrderKind.TRIAL,
                Order.status == OrderStatus.PAID,
                Order.created_at >= since,
            )
        )
        count = int(rows.scalar_one() or 0)
        if count < threshold:
            return None
        return await self.flag(
            subject_type="click", subject_id="", reason="multi_trial_same_ip",
            severity=FraudSeverity.HIGH,
            details={"ip": ip, "trial_orders": count, "window_days": window_days},
        )

    async def check_duplicate_account_by_ip(
        self, *, ip: str, threshold: int = 3,
    ) -> Optional[FraudFlag]:
        """Flag if too many accounts registered from the same IP recently."""
        if not ip:
            return None
        # We use ReferralClick.ip as our best per-user IP signal for now.
        rows = await self.session.execute(
            select(func.count(ReferralClick.id.distinct())).where(ReferralClick.ip == ip)
        )
        count = int(rows.scalar_one() or 0)
        if count < threshold:
            return None
        return await self.flag(
            subject_type="user", subject_id="",
            reason="duplicate_account_by_ip", severity=FraudSeverity.MEDIUM,
            details={"ip": ip, "click_count": count},
        )

    async def list_active(
        self, *, subject_type: Optional[str] = None, limit: int = 100,
    ) -> list[FraudFlag]:
        stmt = select(FraudFlag).where(FraudFlag.is_active.is_(True))
        if subject_type:
            stmt = stmt.where(FraudFlag.subject_type == subject_type)
        stmt = stmt.order_by(FraudFlag.created_at.desc()).limit(limit)
        return list(await self.session.scalars(stmt))

    async def dismiss(self, flag_id: str) -> Optional[FraudFlag]:
        f = await self.session.get(FraudFlag, flag_id)
        if f is None:
            return None
        f.is_active = False
        await self.session.flush()
        return f
