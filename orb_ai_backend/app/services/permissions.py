"""Centralized Permission Engine (v1.1.0).

Every plan-gated behaviour in the platform MUST route through this
service. Callers never hardcode subscription checks::

    perms = PermissionService(session)
    if not await perms.can_use_strategy(user, "orb_pro"):
        raise ForbiddenError("strategy_locked")

    max_bots = await perms.max_running_bots(user)
    max_positions = await perms.max_open_positions(user)

Design principles
-----------------
* Single source of truth — resolves plan, limits, and per-user overrides.
* Composable — every helper returns a plain bool / int / str, no side
  effects. Callers make the enforcement decision.
* Backward-compat — a user without a UserSubscription is treated as the
  ``free`` plan (matches v1.0.0 behaviour of FeatureGate).
* Testable — Redis-independent, cache-free by default. Callers wanting
  hot-path caching can wrap this service.

The trading engine and admin dashboard consult this service before:
* Starting a bot (``can_use_bot``)
* Opening a position (``max_open_positions``)
* Activating a strategy (``can_use_strategy``)
* Enabling AI features (``can_use_ai``)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.strategy_catalog import StrategyCatalog, StrategyStatus as CatalogStatus
from app.models.subscription import (
    PlanTier,
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.user import User
from app.services.subscriptions.feature_flags import FeatureFlag
from app.services.subscriptions.feature_gate import FeatureGate

logger = get_logger(__name__)

# ---- Plan tier ordering (used for min_plan_tier comparisons) ----
# Lower rank = smaller plan. A user on a plan with rank >= min_rank
# can use a strategy that requires min_plan_tier.
_TIER_RANK: dict[str, int] = {
    PlanTier.FREE.value: 0,          # deprecated — still handled for legacy rows
    PlanTier.STANDARD.value: 5,      # ORB AI 2.0 entry paid tier
    PlanTier.STARTER.value: 10,
    PlanTier.PRO.value: 20,
    PlanTier.ELITE.value: 30,
    PlanTier.ENTERPRISE.value: 30,  # ENTERPRISE (v1.0.0 legacy) == ELITE
}


def tier_rank(tier: str | PlanTier) -> int:
    """Return the sort rank for a plan tier string."""
    key = tier.value if isinstance(tier, PlanTier) else str(tier).lower()
    return _TIER_RANK.get(key, 0)


@dataclass(slots=True)
class UserPermissions:
    """Snapshot of every permission decision for one user.

    Returned by :meth:`PermissionService.snapshot` — useful for the
    ``/api/v1/permissions/me`` endpoint and for the admin UI.
    """
    plan_key: str
    plan_name: str
    tier: str
    status: str
    is_trial: bool
    automation_enabled: bool
    paper_trading_only: bool
    ai_features_enabled: bool
    max_running_bots: int          # -1 = unlimited
    max_open_positions: int        # -1 = unlimited
    allowed_strategies: list[str]  # strategy keys the user may run
    features: dict[str, bool]


class PermissionService:
    """Centralized permission resolver.

    Instantiate with an AsyncSession; call the helpers.
    Every method is safe to call concurrently — no shared mutable state.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._gate = FeatureGate(session)

    # ------------------------------------------------------------------ core
    async def _plan_for_user(
        self, user: User
    ) -> tuple[Optional[UserSubscription], Optional[SubscriptionPlan]]:
        sub = (await self.session.execute(
            select(UserSubscription).where(UserSubscription.user_id == user.id)
        )).scalar_one_or_none()
        plan: Optional[SubscriptionPlan] = None
        if sub is not None:
            plan = await self.session.get(SubscriptionPlan, sub.plan_id)
        if plan is None:
            # Default to Standard (ORB AI 2.0 entry paid tier). Falls back
            # to the deprecated Free plan only if Standard hasn't been
            # seeded yet.
            plan = (await self.session.execute(
                select(SubscriptionPlan).where(SubscriptionPlan.key == "standard")
            )).scalar_one_or_none()
            if plan is None:
                plan = (await self.session.execute(
                    select(SubscriptionPlan).where(SubscriptionPlan.key == "free")
                )).scalar_one_or_none()
        return sub, plan

    @staticmethod
    def _plan_active(sub: Optional[UserSubscription]) -> bool:
        if sub is None:
            return True  # default plan (Standard) is active
        return sub.status in (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIALING,
            SubscriptionStatus.GRACE_PERIOD,
        )

    @staticmethod
    def _override_int(sub: Optional[UserSubscription], key: str) -> Optional[int]:
        if sub and sub.limit_overrides:
            v = sub.limit_overrides.get(key)
            if isinstance(v, int):
                return v
        return None

    @staticmethod
    def _override_bool(sub: Optional[UserSubscription], key: str) -> Optional[bool]:
        if sub and sub.limit_overrides:
            v = sub.limit_overrides.get(key)
            if isinstance(v, bool):
                return v
        return None

    # ------------------------------------------------------------------ bots
    async def max_running_bots(self, user: User) -> int:
        """Maximum concurrent running bots. -1 means unlimited."""
        sub, plan = await self._plan_for_user(user)
        ov = self._override_int(sub, "max_running_bots")
        if ov is not None:
            return ov
        if plan is None:
            return 0
        if not self._plan_active(sub):
            return 0
        if plan.unlimited_bots:
            return -1
        return int(plan.max_running_bots or 0)

    async def can_use_bot(self, user: User) -> bool:
        """Automation is available only for plans with automation_enabled=True."""
        sub, plan = await self._plan_for_user(user)
        ov = self._override_bool(sub, "automation_enabled")
        if ov is not None:
            return ov
        if plan is None or not self._plan_active(sub):
            return False
        return bool(plan.automation_enabled)

    # ------------------------------------------------------------------ positions
    async def max_open_positions(self, user: User) -> int:
        """Maximum concurrent open positions. -1 means unlimited."""
        sub, plan = await self._plan_for_user(user)
        ov = self._override_int(sub, "max_open_positions")
        if ov is not None:
            return ov
        if plan is None:
            return 0
        if not self._plan_active(sub):
            return 0
        return int(plan.max_open_positions or 0)

    # ------------------------------------------------------------------ AI
    async def can_use_ai(self, user: User) -> bool:
        sub, plan = await self._plan_for_user(user)
        ov = self._override_bool(sub, "ai_features_enabled")
        if ov is not None:
            return ov
        if plan is None or not self._plan_active(sub):
            return False
        return bool(plan.ai_features_enabled)

    # ------------------------------------------------------------------ paper-only
    async def paper_trading_only(self, user: User) -> bool:
        """True if the user's plan restricts them to paper trading."""
        sub, plan = await self._plan_for_user(user)
        ov = self._override_bool(sub, "paper_trading_only")
        if ov is not None:
            return ov
        if plan is None:
            return True
        if not self._plan_active(sub):
            return True
        return bool(plan.paper_trading_only)

    # ------------------------------------------------------------------ strategies
    async def can_use_strategy(self, user: User, strategy_key: str) -> bool:
        """True iff the user's plan tier >= the strategy's min_plan_tier
        AND the strategy is ACTIVE in the catalog.

        A user cannot run an IMPLEMENTATION_PENDING, DEPRECATED, or
        DISABLED strategy even if their tier allows the category —
        this is a safety guarantee for the trading engine.
        """
        strat = (await self.session.execute(
            select(StrategyCatalog).where(StrategyCatalog.key == strategy_key.lower())
        )).scalar_one_or_none()
        if strat is None:
            return False
        if strat.status != CatalogStatus.ACTIVE.value:
            return False
        sub, plan = await self._plan_for_user(user)
        if plan is None or not self._plan_active(sub):
            return False
        return tier_rank(plan.tier) >= tier_rank(strat.min_plan_tier)

    async def allowed_strategies(self, user: User) -> list[str]:
        """Return the set of strategy keys this user may execute."""
        sub, plan = await self._plan_for_user(user)
        if plan is None or not self._plan_active(sub):
            return []
        user_rank = tier_rank(plan.tier)
        rows = (await self.session.execute(
            select(StrategyCatalog)
            .where(StrategyCatalog.status == CatalogStatus.ACTIVE.value)
            .order_by(StrategyCatalog.display_order.asc(), StrategyCatalog.name.asc())
        )).scalars().all()
        return [r.key for r in rows if user_rank >= tier_rank(r.min_plan_tier)]

    # ------------------------------------------------------------------ snapshot
    async def snapshot(self, user: User) -> UserPermissions:
        """Return the full permission snapshot for a user."""
        sub, plan = await self._plan_for_user(user)
        features = await self._gate.features_for(user)
        allowed = await self.allowed_strategies(user)
        return UserPermissions(
            plan_key=plan.key if plan else "standard",
            plan_name=plan.name if plan else "Standard",
            tier=(plan.tier.value if plan and hasattr(plan.tier, "value") else (plan.tier if plan else "standard")),
            status=(sub.status.value if sub and hasattr(sub.status, "value") else "active"),
            is_trial=bool(sub.is_trial) if sub else False,
            automation_enabled=await self.can_use_bot(user),
            paper_trading_only=await self.paper_trading_only(user),
            ai_features_enabled=await self.can_use_ai(user),
            max_running_bots=await self.max_running_bots(user),
            max_open_positions=await self.max_open_positions(user),
            allowed_strategies=allowed,
            features=features,
        )
