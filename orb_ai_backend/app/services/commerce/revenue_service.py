"""RevenueDashboardService — computes all Phase 2 KPIs.

All queries are read-only aggregations against Orders,
UserSubscriptions, Coupons, StrategyPurchases, and Users.
Affiliate KPIs are stubbed (return zeros) — the affiliate module is
Phase 3.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import (
    Coupon,
    CouponRedemption,
    MarketplaceListing,
    Order,
    OrderKind,
    OrderStatus,
    StrategyPurchase,
)
from app.models.subscription import (
    PlanTier,
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)


@dataclass
class SubscriptionKPIs:
    mrr_cents: int = 0
    arr_cents: int = 0
    active_subscribers: int = 0
    new_subscribers: int = 0
    churned_subscribers: int = 0
    churn_rate_pct: float = 0.0
    arpu_cents: int = 0


@dataclass
class TrialKPIs:
    trial_signups: int = 0
    trial_activations: int = 0
    trial_expirations: int = 0
    trial_to_paid_conversions: int = 0
    conversion_rate_pct: float = 0.0
    avg_time_to_upgrade_hours: float = 0.0
    trial_conversion_revenue_cents: int = 0


@dataclass
class CouponKPIs:
    total_redemptions: int = 0
    total_discount_cents: int = 0
    conversions_from_coupon: int = 0
    top_coupons: list[dict] = field(default_factory=list)


@dataclass
class AffiliateKPIs:
    """Placeholder — full affiliate module is Phase 3."""
    referral_signups: int = 0
    referral_paid_conversions: int = 0
    commission_paid_cents: int = 0
    top_affiliates: list[dict] = field(default_factory=list)


@dataclass
class StrategyKPIs:
    total_purchases: int = 0
    total_revenue_cents: int = 0
    most_used_strategies: list[dict] = field(default_factory=list)
    plan_distribution: dict = field(default_factory=dict)


@dataclass
class RevenueSummary:
    period_start: datetime
    period_end: datetime
    subscription: SubscriptionKPIs
    trial: TrialKPIs
    coupon: CouponKPIs
    affiliate: AffiliateKPIs
    strategy: StrategyKPIs

    def to_dict(self) -> dict:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "subscription": asdict(self.subscription),
            "trial": asdict(self.trial),
            "coupon": asdict(self.coupon),
            "affiliate": asdict(self.affiliate),
            "strategy": asdict(self.strategy),
        }


class RevenueDashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def summary(
        self,
        *,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> RevenueSummary:
        now = datetime.now(timezone.utc)
        end = period_end or now
        start = period_start or (end - timedelta(days=30))
        return RevenueSummary(
            period_start=start,
            period_end=end,
            subscription=await self._subscription_kpis(start, end),
            trial=await self._trial_kpis(start, end),
            coupon=await self._coupon_kpis(start, end),
            affiliate=AffiliateKPIs(),  # Phase 3 stub
            strategy=await self._strategy_kpis(start, end),
        )

    # ------------------------------------------------------------ subscriptions

    async def _subscription_kpis(self, start: datetime, end: datetime) -> SubscriptionKPIs:
        # MRR: sum of monthly plan prices for currently-active subs
        # (excludes trialing users).
        active_stmt = (
            select(SubscriptionPlan.price_cents, SubscriptionPlan.interval, func.count(UserSubscription.id))
            .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
            .where(
                UserSubscription.status == SubscriptionStatus.ACTIVE,
                UserSubscription.is_trial.is_(False),
                SubscriptionPlan.price_cents > 0,
            )
            .group_by(SubscriptionPlan.id, SubscriptionPlan.price_cents, SubscriptionPlan.interval)
        )
        mrr = 0
        active_paid = 0
        for price_cents, interval, count in (await self.session.execute(active_stmt)).all():
            monthly = price_cents if (interval or "monthly").lower() == "monthly" else price_cents // 12
            mrr += int(monthly) * int(count)
            active_paid += int(count)

        active_total = (
            await self.session.scalar(
                select(func.count(UserSubscription.id)).where(
                    UserSubscription.status == SubscriptionStatus.ACTIVE
                )
            )
        ) or 0

        new_subs = (
            await self.session.scalar(
                select(func.count(UserSubscription.id)).where(
                    UserSubscription.status == SubscriptionStatus.ACTIVE,
                    UserSubscription.created_at >= start,
                    UserSubscription.created_at <= end,
                )
            )
        ) or 0

        churned = (
            await self.session.scalar(
                select(func.count(UserSubscription.id)).where(
                    UserSubscription.status.in_(
                        [SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED]
                    ),
                    UserSubscription.updated_at >= start,
                    UserSubscription.updated_at <= end,
                )
            )
        ) or 0

        base = max(active_total, 1)
        churn_rate = round((churned / base) * 100.0, 2)

        arpu = int(mrr // active_paid) if active_paid > 0 else 0

        return SubscriptionKPIs(
            mrr_cents=mrr,
            arr_cents=mrr * 12,
            active_subscribers=int(active_total),
            new_subscribers=int(new_subs),
            churned_subscribers=int(churned),
            churn_rate_pct=churn_rate,
            arpu_cents=arpu,
        )

    # ------------------------------------------------------------ trial

    async def _trial_kpis(self, start: datetime, end: datetime) -> TrialKPIs:
        # Trial orders in period.
        trial_orders = list(
            await self.session.scalars(
                select(Order).where(
                    Order.kind == OrderKind.TRIAL,
                    Order.created_at >= start,
                    Order.created_at <= end,
                )
            )
        )
        signups = len(trial_orders)
        activations = sum(1 for o in trial_orders if o.status == OrderStatus.PAID)

        # Trial expirations: subs whose trial_ends_at fell in period and are
        # no longer trialing.
        expirations = (
            await self.session.scalar(
                select(func.count(UserSubscription.id)).where(
                    UserSubscription.trial_ends_at.is_not(None),
                    UserSubscription.trial_ends_at >= start,
                    UserSubscription.trial_ends_at <= end,
                    UserSubscription.is_trial.is_(False),
                )
            )
        ) or 0

        # Trial-to-paid conversions: subs that have trial_consumed_at set
        # AND are currently ACTIVE (non-trial) with trial_credit_cents > 0.
        conv_rows = list(
            await self.session.scalars(
                select(UserSubscription).where(
                    UserSubscription.trial_consumed_at.is_not(None),
                    UserSubscription.trial_consumed_at >= start,
                    UserSubscription.trial_consumed_at <= end,
                    UserSubscription.status == SubscriptionStatus.ACTIVE,
                    UserSubscription.is_trial.is_(False),
                    UserSubscription.trial_credit_cents > 0,
                )
            )
        )
        conversions = len(conv_rows)

        avg_hours = 0.0
        if conv_rows:
            deltas = []
            for s in conv_rows:
                if s.trial_started_at and s.updated_at:
                    d = (s.updated_at - s.trial_started_at).total_seconds() / 3600.0
                    deltas.append(max(0.0, d))
            if deltas:
                avg_hours = round(sum(deltas) / len(deltas), 2)

        # Conversion revenue = sum of PAID subscription orders whose user had
        # a trial_consumed_at in the period.
        rev = (
            await self.session.scalar(
                select(func.coalesce(func.sum(Order.total_cents), 0))
                .join(UserSubscription, UserSubscription.user_id == Order.user_id)
                .where(
                    Order.kind == OrderKind.SUBSCRIPTION,
                    Order.status == OrderStatus.PAID,
                    UserSubscription.trial_consumed_at.is_not(None),
                    UserSubscription.trial_consumed_at >= start,
                    UserSubscription.trial_consumed_at <= end,
                )
            )
        ) or 0

        rate = round((conversions / signups) * 100.0, 2) if signups else 0.0
        return TrialKPIs(
            trial_signups=signups,
            trial_activations=activations,
            trial_expirations=int(expirations),
            trial_to_paid_conversions=conversions,
            conversion_rate_pct=rate,
            avg_time_to_upgrade_hours=avg_hours,
            trial_conversion_revenue_cents=int(rev),
        )

    # ------------------------------------------------------------ coupons

    async def _coupon_kpis(self, start: datetime, end: datetime) -> CouponKPIs:
        rows = (
            await self.session.execute(
                select(
                    func.count(CouponRedemption.id),
                    func.coalesce(func.sum(CouponRedemption.discount_applied_cents), 0),
                ).where(
                    CouponRedemption.created_at >= start,
                    CouponRedemption.created_at <= end,
                )
            )
        ).one()
        total_redemptions, total_discount = int(rows[0] or 0), int(rows[1] or 0)

        conversions = (
            await self.session.scalar(
                select(func.count(Order.id)).where(
                    Order.coupon_id.is_not(None),
                    Order.status == OrderStatus.PAID,
                    Order.paid_at.is_not(None),
                    Order.paid_at >= start,
                    Order.paid_at <= end,
                )
            )
        ) or 0

        top_rows = (
            await self.session.execute(
                select(
                    Coupon.code,
                    Coupon.id,
                    func.count(CouponRedemption.id),
                    func.coalesce(func.sum(CouponRedemption.discount_applied_cents), 0),
                )
                .join(CouponRedemption, CouponRedemption.coupon_id == Coupon.id)
                .where(
                    CouponRedemption.created_at >= start,
                    CouponRedemption.created_at <= end,
                )
                .group_by(Coupon.id, Coupon.code)
                .order_by(func.count(CouponRedemption.id).desc())
                .limit(5)
            )
        ).all()
        top = [
            {
                "code": code,
                "coupon_id": cid,
                "redemptions": int(cnt),
                "total_discount_cents": int(disc),
            }
            for code, cid, cnt, disc in top_rows
        ]

        return CouponKPIs(
            total_redemptions=total_redemptions,
            total_discount_cents=total_discount,
            conversions_from_coupon=int(conversions),
            top_coupons=top,
        )

    # ------------------------------------------------------------ strategies

    async def _strategy_kpis(self, start: datetime, end: datetime) -> StrategyKPIs:
        rows = (
            await self.session.execute(
                select(
                    func.count(StrategyPurchase.id),
                    func.coalesce(func.sum(StrategyPurchase.price_cents), 0),
                ).where(
                    StrategyPurchase.granted_at >= start,
                    StrategyPurchase.granted_at <= end,
                )
            )
        ).one()
        purchases, revenue = int(rows[0] or 0), int(rows[1] or 0)

        top_rows = (
            await self.session.execute(
                select(
                    StrategyPurchase.strategy_key,
                    func.count(StrategyPurchase.id),
                    func.coalesce(func.sum(StrategyPurchase.price_cents), 0),
                )
                .where(
                    StrategyPurchase.granted_at >= start,
                    StrategyPurchase.granted_at <= end,
                )
                .group_by(StrategyPurchase.strategy_key)
                .order_by(func.count(StrategyPurchase.id).desc())
                .limit(5)
            )
        ).all()
        most_used = [
            {
                "strategy_key": key,
                "purchases": int(cnt),
                "revenue_cents": int(rev),
            }
            for key, cnt, rev in top_rows
        ]

        # Plan distribution — how many active subs per plan tier.
        plan_dist_rows = (
            await self.session.execute(
                select(SubscriptionPlan.tier, func.count(UserSubscription.id))
                .join(UserSubscription, UserSubscription.plan_id == SubscriptionPlan.id)
                .where(UserSubscription.status == SubscriptionStatus.ACTIVE)
                .group_by(SubscriptionPlan.tier)
            )
        ).all()
        plan_distribution = {
            (tier.value if hasattr(tier, "value") else str(tier)): int(cnt)
            for tier, cnt in plan_dist_rows
        }

        return StrategyKPIs(
            total_purchases=purchases,
            total_revenue_cents=revenue,
            most_used_strategies=most_used,
            plan_distribution=plan_distribution,
        )
