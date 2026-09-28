"""Trial state machine (v1.1.0).

Rules
-----
* One trial per verified user (email must be verified).
* ₹50 trial price, 3-day duration (configurable per plan).
* When the trial ends, the subscription flips to ``TRIAL_EXPIRED``
  and the user is dropped back to the ``free`` plan by the scheduler.
* If the user upgrades during the trial, the ₹50 is credited toward
  the paid plan and ``trial_credit_cents`` is recorded on the sub.
* Trial history is permanent — once ``trial_consumed_at`` is set it
  never resets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
)
from app.core.logging import get_logger
from app.models.subscription import (
    PlanTier,
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.user import User

logger = get_logger(__name__)

DEFAULT_TRIAL_DURATION_DAYS = 3
DEFAULT_TRIAL_PRICE_CENTS = 5000  # ₹50.00 → 5000 paise
DEFAULT_TRIAL_TARGET_PLAN_KEY = "pro"


class TrialAlreadyConsumedError(ConflictError):
    code = "trial_already_consumed"
    message = "This user has already used their one-time trial."


class EmailNotVerifiedError(ForbiddenError):
    code = "email_not_verified"
    message = "Email verification is required before starting a trial."


@dataclass(slots=True)
class TrialState:
    is_trial: bool
    trial_started_at: Optional[datetime]
    trial_ends_at: Optional[datetime]
    trial_consumed_at: Optional[datetime]
    trial_credit_cents: int
    days_remaining: Optional[int]

    @classmethod
    def from_sub(cls, sub: Optional[UserSubscription]) -> "TrialState":
        if sub is None:
            return cls(False, None, None, None, 0, None)
        days_remaining = None
        if sub.is_trial and sub.trial_ends_at:
            ends = sub.trial_ends_at
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            delta = ends - datetime.now(timezone.utc)
            days_remaining = max(0, delta.days)
        return cls(
            is_trial=bool(sub.is_trial),
            trial_started_at=sub.trial_started_at,
            trial_ends_at=sub.trial_ends_at,
            trial_consumed_at=sub.trial_consumed_at,
            trial_credit_cents=int(sub.trial_credit_cents or 0),
            days_remaining=days_remaining,
        )


class TrialService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get_sub(self, user: User) -> Optional[UserSubscription]:
        return (await self.session.execute(
            select(UserSubscription).where(UserSubscription.user_id == user.id)
        )).scalar_one_or_none()

    async def _plan_by_key(self, key: str) -> Optional[SubscriptionPlan]:
        return (await self.session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.key == key)
        )).scalar_one_or_none()

    async def get_state(self, user: User) -> TrialState:
        return TrialState.from_sub(await self._get_sub(user))

    async def start_trial(
        self,
        user: User,
        *,
        plan_key: str = DEFAULT_TRIAL_TARGET_PLAN_KEY,
        duration_days: int = DEFAULT_TRIAL_DURATION_DAYS,
        price_cents: int = DEFAULT_TRIAL_PRICE_CENTS,
        payment_reference: Optional[str] = None,
    ) -> UserSubscription:
        """Start a paid trial for the user (idempotent per user).

        Preconditions:
            * user.is_verified must be True.
            * user has not previously consumed a trial.

        On success: creates/updates the UserSubscription with
        status=TRIALING, is_trial=True, trial_started_at=now,
        trial_ends_at=now+duration, trial_credit_cents=price_cents.
        """
        if not user.is_verified:
            raise EmailNotVerifiedError()

        sub = await self._get_sub(user)
        if sub and sub.trial_consumed_at is not None:
            raise TrialAlreadyConsumedError()

        plan = await self._plan_by_key(plan_key)
        if plan is None:
            raise BadRequestError(f"Unknown plan: {plan_key}", code="unknown_plan")

        now = datetime.now(timezone.utc)
        ends = now + timedelta(days=max(1, int(duration_days)))

        if sub is None:
            sub = UserSubscription(user_id=user.id, plan_id=plan.id)
            self.session.add(sub)
        sub.plan_id = plan.id
        sub.status = SubscriptionStatus.TRIALING
        sub.is_trial = True
        sub.trial_started_at = now
        sub.trial_ends_at = ends
        sub.trial_consumed_at = now
        sub.trial_credit_cents = int(price_cents)
        sub.current_period_start = now
        sub.current_period_end = ends
        sub.provider = "razorpay" if payment_reference else "manual"
        if payment_reference:
            sub.provider_subscription_id = payment_reference

        await self.session.flush()
        logger.info(
            "trial_started",
            extra={
                "user_id": user.id, "plan_key": plan_key,
                "duration_days": duration_days,
                "price_cents": price_cents,
                "payment_ref": payment_reference,
            },
        )
        return sub

    async def expire_stale_trials(self, *, now: Optional[datetime] = None) -> int:
        """Batch job: flip any TRIALING sub whose trial_ends_at is past.

        Returns the number of subs updated. Idempotent — safe to call
        every minute from the scheduler.
        """
        now = now or datetime.now(timezone.utc)
        stmt = select(UserSubscription).where(
            UserSubscription.status == SubscriptionStatus.TRIALING,
            UserSubscription.trial_ends_at.is_not(None),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        # Drop expired trials to the ORB AI 2.0 entry paid tier (Standard);
        # if Standard hasn't been seeded yet fall back to the deprecated
        # free plan.
        dropdown_plan = await self._plan_by_key("standard") or await self._plan_by_key("free")
        expired = 0
        for sub in rows:
            if sub.trial_ends_at is None:
                continue
            ends = sub.trial_ends_at
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            if ends > now:
                continue
            sub.status = SubscriptionStatus.TRIAL_EXPIRED
            sub.is_trial = False
            if dropdown_plan is not None:
                sub.plan_id = dropdown_plan.id
                sub.status = SubscriptionStatus.ACTIVE  # drop to entry paid tier
            expired += 1
        if expired:
            await self.session.flush()
            logger.info("trials_expired", extra={"count": expired})
        return expired

    async def apply_trial_credit_on_upgrade(
        self, user: User, target_plan_key: str
    ) -> int:
        """When a trialing user upgrades, apply their trial payment as
        credit toward the target plan. Returns cents credited (0 if
        nothing was credited).
        """
        sub = await self._get_sub(user)
        if sub is None or not sub.is_trial:
            return 0
        credit = int(sub.trial_credit_cents or 0)
        if credit <= 0:
            return 0
        plan = await self._plan_by_key(target_plan_key)
        if plan is None:
            return 0
        sub.plan_id = plan.id
        sub.status = SubscriptionStatus.ACTIVE
        sub.is_trial = False
        sub.trial_ends_at = None
        # We don't zero trial_credit_cents so the credit remains auditable.
        logger.info(
            "trial_credit_applied",
            extra={"user_id": user.id, "cents": credit, "target_plan": target_plan_key},
        )
        await self.session.flush()
        return credit
