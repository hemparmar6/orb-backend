"""Trial subscription endpoints (v1.1.0).

Public::
    GET  /api/v1/trials/me         → current trial state
    POST /api/v1/trials/start      → start a paid ₹50 / 3-day trial

Admin::
    POST /api/v1/trials/admin/expire-stale
        → sweep expired trials (also runs from the scheduler).

The trial state machine is defined in :mod:`app.services.trials`.
Payment integration is added in Phase 2 (Razorpay); Phase 1 accepts
a caller-supplied ``payment_reference`` so the endpoint contract is
already stable.
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.services.trials import (
    DEFAULT_TRIAL_DURATION_DAYS,
    DEFAULT_TRIAL_PRICE_CENTS,
    DEFAULT_TRIAL_TARGET_PLAN_KEY,
    TrialService,
    TrialState,
)

router = APIRouter()


class TrialStateOut(BaseModel):
    is_trial: bool
    trial_started_at: Optional[datetime]
    trial_ends_at: Optional[datetime]
    trial_consumed_at: Optional[datetime]
    trial_credit_cents: int
    days_remaining: Optional[int]

    @classmethod
    def from_state(cls, s: TrialState) -> "TrialStateOut":
        return cls(**asdict(s))


class StartTrialIn(BaseModel):
    plan_key: str = Field(default=DEFAULT_TRIAL_TARGET_PLAN_KEY, max_length=64)
    payment_reference: Optional[str] = Field(default=None, max_length=255)


class ExpireStaleResult(BaseModel):
    expired: int


@router.get("/me", response_model=TrialStateOut, summary="Current trial state")
async def get_trial(current_user: CurrentUser, session: DBSession) -> TrialStateOut:
    state = await TrialService(session).get_state(current_user)
    return TrialStateOut.from_state(state)


@router.post("/start", response_model=TrialStateOut, summary="Start ₹50 / 3-day trial")
async def start_trial(
    payload: StartTrialIn, current_user: CurrentUser, session: DBSession
) -> TrialStateOut:
    svc = TrialService(session)
    sub = await svc.start_trial(
        current_user,
        plan_key=payload.plan_key,
        duration_days=DEFAULT_TRIAL_DURATION_DAYS,
        price_cents=DEFAULT_TRIAL_PRICE_CENTS,
        payment_reference=payload.payment_reference,
    )
    await session.commit()
    return TrialStateOut(
        is_trial=sub.is_trial,
        trial_started_at=sub.trial_started_at,
        trial_ends_at=sub.trial_ends_at,
        trial_consumed_at=sub.trial_consumed_at,
        trial_credit_cents=sub.trial_credit_cents,
        days_remaining=DEFAULT_TRIAL_DURATION_DAYS,
    )


@router.post(
    "/admin/expire-stale",
    response_model=ExpireStaleResult,
    summary="[Admin] Sweep expired trials",
)
async def expire_stale(_: AdminUser, session: DBSession) -> ExpireStaleResult:
    n = await TrialService(session).expire_stale_trials()
    await session.commit()
    return ExpireStaleResult(expired=n)
