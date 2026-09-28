"""AffiliateAnalyticsService — clicks/signups/conversions/commissions KPIs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.affiliate import (
    Affiliate,
    Campaign,
    Commission,
    CommissionStatus,
    ReferralAttribution,
    ReferralClick,
    ReferralEvent,
    ReferralEventType,
)
from app.models.commerce import Order, OrderKind, OrderStatus
from app.models.subscription import (
    SubscriptionPlan,
    UserSubscription,
    SubscriptionStatus,
)


@dataclass
class AffiliateAnalyticsReport:
    period_start: datetime
    period_end: datetime
    clicks: int = 0
    signups: int = 0
    verified_users: int = 0
    trial_activations: int = 0
    trial_to_paid_conversions: int = 0
    paid_subscribers: int = 0
    renewals: int = 0
    revenue_cents: int = 0
    commission_earned_cents: int = 0
    commission_paid_cents: int = 0
    conversion_rate_pct: float = 0.0
    epc_cents: float = 0.0        # earnings per click
    roi_pct: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["period_start"] = self.period_start.isoformat()
        d["period_end"] = self.period_end.isoformat()
        return d


class AffiliateAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def report(
        self,
        *,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        affiliate_id: Optional[str] = None,
        campaign_id: Optional[str] = None,
        plan_key: Optional[str] = None,
    ) -> AffiliateAnalyticsReport:
        now = datetime.now(timezone.utc)
        end = period_end or now
        start = period_start or (end - timedelta(days=30))

        report = AffiliateAnalyticsReport(period_start=start, period_end=end)

        # ---------- clicks ----------
        click_stmt = select(func.count(ReferralClick.id)).where(
            ReferralClick.created_at >= start,
            ReferralClick.created_at <= end,
        )
        if affiliate_id:
            click_stmt = click_stmt.where(ReferralClick.affiliate_id == affiliate_id)
        if campaign_id:
            click_stmt = click_stmt.where(ReferralClick.campaign_id == campaign_id)
        report.clicks = int(await self.session.scalar(click_stmt) or 0)

        # ---------- signups (attributions) ----------
        sig_stmt = select(func.count(ReferralAttribution.id)).where(
            ReferralAttribution.created_at >= start,
            ReferralAttribution.created_at <= end,
        )
        if affiliate_id:
            sig_stmt = sig_stmt.where(ReferralAttribution.affiliate_id == affiliate_id)
        if campaign_id:
            sig_stmt = sig_stmt.where(ReferralAttribution.campaign_id == campaign_id)
        report.signups = int(await self.session.scalar(sig_stmt) or 0)

        # ---------- funnel events ----------
        async def _event_count(evt: ReferralEventType) -> int:
            stmt = select(func.count(ReferralEvent.id)).where(
                ReferralEvent.event_type == evt,
                ReferralEvent.created_at >= start,
                ReferralEvent.created_at <= end,
            )
            if affiliate_id:
                stmt = stmt.where(ReferralEvent.affiliate_id == affiliate_id)
            if campaign_id:
                stmt = stmt.where(ReferralEvent.campaign_id == campaign_id)
            return int(await self.session.scalar(stmt) or 0)

        report.verified_users = await _event_count(ReferralEventType.EMAIL_VERIFICATION)
        report.trial_activations = await _event_count(ReferralEventType.TRIAL_ACTIVATION)
        report.renewals = await _event_count(ReferralEventType.RENEWAL)

        # ---------- paid subs + revenue (from commissions) ----------
        com_stmt = select(
            func.count(Commission.id),
            func.coalesce(func.sum(Commission.base_amount_cents), 0),
            func.coalesce(func.sum(Commission.amount_cents), 0),
        ).where(
            Commission.created_at >= start,
            Commission.created_at <= end,
        )
        if affiliate_id:
            com_stmt = com_stmt.where(Commission.affiliate_id == affiliate_id)
        row = (await self.session.execute(com_stmt)).one()
        report.paid_subscribers = int(row[0] or 0)
        report.revenue_cents = int(row[1] or 0)
        report.commission_earned_cents = int(row[2] or 0)

        paid_stmt = select(
            func.coalesce(func.sum(Commission.amount_cents), 0)
        ).where(
            Commission.status == CommissionStatus.PAID,
            Commission.created_at >= start,
            Commission.created_at <= end,
        )
        if affiliate_id:
            paid_stmt = paid_stmt.where(Commission.affiliate_id == affiliate_id)
        report.commission_paid_cents = int(await self.session.scalar(paid_stmt) or 0)

        # ---------- trial→paid ----------
        trial_paid_stmt = (
            select(func.count(UserSubscription.id))
            .join(ReferralAttribution, ReferralAttribution.user_id == UserSubscription.user_id)
            .where(
                UserSubscription.trial_consumed_at.is_not(None),
                UserSubscription.status == SubscriptionStatus.ACTIVE,
                UserSubscription.is_trial.is_(False),
                UserSubscription.trial_consumed_at >= start,
                UserSubscription.trial_consumed_at <= end,
            )
        )
        if affiliate_id:
            trial_paid_stmt = trial_paid_stmt.where(ReferralAttribution.affiliate_id == affiliate_id)
        report.trial_to_paid_conversions = int(
            await self.session.scalar(trial_paid_stmt) or 0
        )

        # ---------- filter by plan_key (post-hoc on revenue) ----------
        if plan_key:
            plan_rev_stmt = (
                select(func.coalesce(func.sum(Order.total_cents), 0))
                .join(ReferralAttribution, ReferralAttribution.user_id == Order.user_id)
                .where(
                    Order.status == OrderStatus.PAID,
                    Order.kind == OrderKind.SUBSCRIPTION,
                    Order.target_ref == plan_key,
                    Order.paid_at.is_not(None),
                    Order.paid_at >= start, Order.paid_at <= end,
                )
            )
            if affiliate_id:
                plan_rev_stmt = plan_rev_stmt.where(ReferralAttribution.affiliate_id == affiliate_id)
            report.revenue_cents = int(await self.session.scalar(plan_rev_stmt) or 0)

        # ---------- derived rates ----------
        if report.clicks:
            report.conversion_rate_pct = round(
                (report.paid_subscribers / report.clicks) * 100.0, 2
            )
            report.epc_cents = round(report.commission_earned_cents / report.clicks, 2)
        if report.revenue_cents:
            report.roi_pct = round(
                (report.commission_earned_cents / report.revenue_cents) * 100.0, 2
            )

        return report

    async def top_affiliates(
        self, *, limit: int = 10,
        period_days: int = 30,
    ) -> list[dict]:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=period_days)
        rows = (await self.session.execute(
            select(
                Affiliate.id, Affiliate.code, Affiliate.display_name,
                func.coalesce(func.sum(Commission.amount_cents), 0),
                func.count(Commission.id),
            )
            .join(Commission, Commission.affiliate_id == Affiliate.id)
            .where(Commission.created_at >= start)
            .group_by(Affiliate.id, Affiliate.code, Affiliate.display_name)
            .order_by(func.sum(Commission.amount_cents).desc())
            .limit(limit)
        )).all()
        return [
            {
                "affiliate_id": aid, "code": code, "display_name": name,
                "commission_earned_cents": int(earned),
                "conversions": int(conv),
            }
            for aid, code, name, earned, conv in rows
        ]
