"""CommissionEngine — idempotent commission calculation + release.

Business-service integration hook:
    await CommissionEngine(session).on_paid_order(order)
        → creates a Commission for the referring affiliate (if any).

Rate resolution priority (first non-null wins):
    campaign.commission_rate_pct
    affiliate.commission_rate_pct
    program.default_commission_rate_pct
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.affiliate import (
    Affiliate,
    AffiliateStatus,
    Campaign,
    Commission,
    CommissionStatus,
    ReferralAttribution,
    ReferralEventType,
)
from app.models.commerce import (
    Order,
    OrderKind,
    OrderStatus,
    WalletTxnReason,
)
from app.services.affiliate.program_service import ProgramService
from app.services.commerce.wallet_service import WalletService


_ORDER_KIND_TO_EVENT = {
    OrderKind.SUBSCRIPTION: ReferralEventType.SUBSCRIPTION_PURCHASE,
    OrderKind.TRIAL: ReferralEventType.TRIAL_ACTIVATION,
    OrderKind.STRATEGY: ReferralEventType.SUBSCRIPTION_PURCHASE,
    OrderKind.WALLET_TOPUP: ReferralEventType.RENEWAL,
}


def _naive_to_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CommissionEngine:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def on_paid_order(self, order: Order) -> Optional[Commission]:
        """Create a commission record for a PAID order. Idempotent per order."""
        if order.status != OrderStatus.PAID:
            return None

        idem_key = f"order:{order.id}"
        existing = await self.session.scalar(
            select(Commission).where(Commission.idempotency_key == idem_key)
        )
        if existing is not None:
            return existing

        attribution = await self.session.scalar(
            select(ReferralAttribution).where(ReferralAttribution.user_id == order.user_id)
        )
        if attribution is None:
            return None

        # Attribution-window check.
        program = await ProgramService(self.session).get()
        now = datetime.now(timezone.utc)
        attributed_at = _naive_to_utc(attribution.attributed_at) or now
        if order.kind == OrderKind.TRIAL:
            deadline = attributed_at + timedelta(days=program.trial_attribution_days)
        else:
            deadline = attributed_at + timedelta(days=program.paid_attribution_days)
        if now > deadline:
            return None

        affiliate = await self.session.get(Affiliate, attribution.affiliate_id)
        if affiliate is None or affiliate.status != AffiliateStatus.APPROVED:
            return None

        # Rate resolution.
        rate = None
        if attribution.campaign_id:
            campaign = await self.session.get(Campaign, attribution.campaign_id)
            if campaign and campaign.commission_rate_pct is not None:
                rate = campaign.commission_rate_pct
        if rate is None:
            rate = affiliate.commission_rate_pct
        if rate is None:
            rate = program.default_commission_rate_pct

        base_amount = max(0, order.subtotal_cents - order.discount_cents)
        commission_amount = base_amount * int(rate) // 100

        event_type = _ORDER_KIND_TO_EVENT.get(order.kind, ReferralEventType.SUBSCRIPTION_PURCHASE)
        release_at = now + timedelta(days=program.hold_period_days)

        if commission_amount <= 0:
            return None

        c = Commission(
            affiliate_id=affiliate.id,
            attribution_id=attribution.id,
            user_id=order.user_id,
            order_id=order.id,
            event_type=event_type,
            base_amount_cents=base_amount,
            rate_pct=int(rate),
            amount_cents=int(commission_amount),
            currency=order.currency,
            status=CommissionStatus.PENDING,
            scheduled_release_at=release_at,
            idempotency_key=idem_key,
        )
        self.session.add(c)

        affiliate.total_commission_earned_cents = (
            affiliate.total_commission_earned_cents or 0
        ) + commission_amount
        affiliate.total_conversions = (affiliate.total_conversions or 0) + 1

        if attribution.campaign_id:
            campaign = await self.session.get(Campaign, attribution.campaign_id)
            if campaign is not None:
                campaign.conversion_count = (campaign.conversion_count or 0) + 1
                campaign.revenue_cents = (campaign.revenue_cents or 0) + base_amount

        await self.session.flush()
        return c

    # ------------------------------------------------------------------ release/reverse

    async def release_matured(self, *, now: Optional[datetime] = None) -> int:
        """Move PENDING commissions past hold period → APPROVED + credit wallet.

        Idempotent — safe to call every hour from the scheduler.
        Returns the number of commissions released.
        """
        now = now or datetime.now(timezone.utc)
        rows = list(await self.session.scalars(
            select(Commission).where(
                Commission.status == CommissionStatus.PENDING,
                Commission.scheduled_release_at.is_not(None),
            )
        ))
        wallets = WalletService(self.session)
        released = 0
        for c in rows:
            sched = _naive_to_utc(c.scheduled_release_at)
            if sched is None or sched > now:
                continue
            affiliate = await self.session.get(Affiliate, c.affiliate_id)
            if affiliate is None:
                continue
            await wallets.credit(
                affiliate.user_id, c.amount_cents,
                reason=WalletTxnReason.AFFILIATE_COMMISSION,
                description=f"Commission for order {c.order_id}",
                reference_type="commission",
                reference_id=c.id,
            )
            c.status = CommissionStatus.APPROVED
            c.released_at = now
            released += 1
        if released:
            await self.session.flush()
        return released

    async def reverse(
        self, commission: Commission, *, reason: str,
    ) -> Commission:
        """Reverse a commission — debit affiliate wallet if already released.

        Idempotent when called on an already-reversed commission.
        """
        if commission.status == CommissionStatus.REVERSED:
            return commission
        was_released = commission.status == CommissionStatus.APPROVED
        commission.status = CommissionStatus.REVERSED
        commission.reversed_reason = reason
        if was_released:
            affiliate = await self.session.get(Affiliate, commission.affiliate_id)
            if affiliate is not None:
                await WalletService(self.session).debit(
                    affiliate.user_id, commission.amount_cents,
                    reason=WalletTxnReason.ADJUSTMENT,
                    description=f"Commission reversal: {reason}",
                    reference_type="commission",
                    reference_id=commission.id,
                    allow_partial=True,
                )
                affiliate.total_commission_earned_cents = max(
                    0, (affiliate.total_commission_earned_cents or 0) - commission.amount_cents
                )
        await self.session.flush()
        return commission
