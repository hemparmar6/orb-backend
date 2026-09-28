"""Trial Purchase REST endpoint (v1.1.0 Phase 2).

Base path: /api/v1/trials

This module intentionally lives beside :mod:`trials` — the existing
``/trials/*`` router handles free-tier trial state; ``/trials/purchase``
orchestrates the ₹50 paid trial through the payment abstraction with
coupon + wallet support.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DBSession
from app.services.commerce.trial_purchase_service import TrialPurchaseService
from app.services.trials import (
    EmailNotVerifiedError,
    TrialAlreadyConsumedError,
)

router = APIRouter()


class TrialPurchaseIn(BaseModel):
    plan_key: str = Field(default="pro", max_length=64)
    coupon_codes: Optional[list[str]] = None
    use_wallet: bool = True


class TrialPurchaseOut(BaseModel):
    order_id: str
    order_status: str
    subtotal_cents: int
    discount_cents: int
    wallet_debit_cents: int
    total_cents: int
    currency: str
    provider: str
    checkout_url: Optional[str] = None
    activated: bool
    coupons_applied: list[dict]


@router.post(
    "/purchase",
    response_model=TrialPurchaseOut,
    summary="Purchase the ₹50 trial (uses payment abstraction + coupons + wallet)",
)
async def purchase_trial(
    payload: TrialPurchaseIn,
    user: CurrentUser,
    session: DBSession,
) -> TrialPurchaseOut:
    svc = TrialPurchaseService(session)
    try:
        result = await svc.purchase(
            user=user,
            plan_key=payload.plan_key,
            coupon_codes=payload.coupon_codes,
            use_wallet=payload.use_wallet,
        )
    except EmailNotVerifiedError:
        raise HTTPException(status_code=403, detail="email not verified")
    except TrialAlreadyConsumedError:
        raise HTTPException(status_code=409, detail="trial already consumed")
    await session.commit()
    coupons = (result.order.order_metadata or {}).get("coupons") or []
    return TrialPurchaseOut(
        order_id=result.order.id,
        order_status=result.order.status.value,
        subtotal_cents=result.quote.subtotal_cents,
        discount_cents=result.quote.discount_cents,
        wallet_debit_cents=result.quote.wallet_debit_cents,
        total_cents=result.quote.total_cents,
        currency=result.quote.currency,
        provider=result.order.provider,
        checkout_url=result.checkout_url,
        activated=result.activated,
        coupons_applied=coupons,
    )
