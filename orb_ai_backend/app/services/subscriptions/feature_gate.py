"""FeatureGate — central authoritative service for feature access.

Callers never inspect plans / subscriptions / Stripe / anything else.
They ask exactly one question::

    if await FeatureGate(session).is_enabled(user, FeatureFlag.X):
        ...

Resolution order (first match wins):
    1. Per-user override on the user's ``UserSubscription.feature_overrides``.
    2. Feature bag on the user's ``SubscriptionPlan.features``.
    3. Global ``BASE_FEATURES`` set.
    4. Default: False.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.subscription import (
    PlanTier,
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.user import User
from app.services.subscriptions.feature_flags import BASE_FEATURES, FeatureFlag

logger = get_logger(__name__)


class FeatureGate:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _load(self, user: User) -> tuple[Optional[UserSubscription], Optional[SubscriptionPlan]]:
        stmt = (
            select(UserSubscription)
            .where(UserSubscription.user_id == user.id)
        )
        sub = (await self.session.execute(stmt)).scalar_one_or_none()
        plan: Optional[SubscriptionPlan] = None
        if sub is not None:
            plan = await self.session.get(SubscriptionPlan, sub.plan_id)
        else:
            # No explicit subscription → treat as Standard (the new entry
            # tier in ORB AI 2.0, replacing the deprecated Free plan).
            plan = await self._plan_by_key("standard")
            if plan is None:
                # Fall back to the deprecated free plan only if Standard
                # hasn't been seeded yet (rare — first migration).
                plan = await self._plan_by_key("free")
        return sub, plan

    async def _plan_by_key(self, key: str) -> Optional[SubscriptionPlan]:
        stmt = select(SubscriptionPlan).where(SubscriptionPlan.key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def is_enabled(self, user: User, feature: FeatureFlag) -> bool:
        # Base features first (cheap short-circuit).
        if feature in BASE_FEATURES:
            return True

        sub, plan = await self._load(user)

        # Only active/trialing subs grant plan features.
        plan_active = sub is None or sub.status in (
            SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING
        )

        # Per-user override wins (even against an inactive subscription).
        if sub and sub.feature_overrides:
            v = sub.feature_overrides.get(feature.value)
            if isinstance(v, bool):
                return v

        if plan and plan_active and plan.features:
            v = plan.features.get(feature.value)
            if isinstance(v, bool):
                return v
        return False

    async def features_for(self, user: User) -> dict[str, bool]:
        """Return {feature_value: bool} for every known feature flag."""
        out: dict[str, bool] = {}
        for f in FeatureFlag:
            out[f.value] = await self.is_enabled(user, f)
        return out

    async def get_plan(self, user: User) -> Optional[SubscriptionPlan]:
        _, plan = await self._load(user)
        return plan

    async def get_subscription(self, user: User) -> Optional[UserSubscription]:
        sub, _ = await self._load(user)
        return sub

    async def set_plan(
        self,
        user: User,
        plan_key: str,
        *,
        provider: str = "noop",
        provider_customer_id: Optional[str] = None,
        provider_subscription_id: Optional[str] = None,
        status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    ) -> UserSubscription:
        """Attach the user to a plan (idempotent)."""
        plan = await self._plan_by_key(plan_key)
        if plan is None:
            raise ValueError(f"Unknown plan key: {plan_key}")
        sub = (await self.session.execute(
            select(UserSubscription).where(UserSubscription.user_id == user.id)
        )).scalar_one_or_none()
        if sub is None:
            sub = UserSubscription(user_id=user.id, plan_id=plan.id)
            self.session.add(sub)
        sub.plan_id = plan.id
        sub.provider = provider
        sub.status = status
        if provider_customer_id is not None:
            sub.provider_customer_id = provider_customer_id
        if provider_subscription_id is not None:
            sub.provider_subscription_id = provider_subscription_id
        await self.session.flush()
        return sub
