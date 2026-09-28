"""Subscription & feature-gate REST endpoints (Module 8)."""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.config import settings
from app.models.subscription import (
    PlanTier,
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.subscriptions import (
    FeatureGate,
    MockBillingProvider,
    get_billing_provider,
)

router = APIRouter()


# ---- Schemas ----
class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    key: str
    name: str
    tier: PlanTier
    price_cents: int
    currency: str
    interval: str
    provider: str
    provider_price_id: Optional[str] = None
    features: Optional[dict[str, Any]] = None


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    plan_key: str
    plan_name: str
    tier: PlanTier
    status: SubscriptionStatus
    provider: str
    cancel_at_period_end: bool
    current_period_end: Optional[str] = None


class MeSubscription(BaseModel):
    subscription: Optional[SubscriptionRead]
    features: dict[str, bool]


class CheckoutRequest(BaseModel):
    plan_key: str = Field(..., description="Target plan key, e.g. 'pro'")
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutResponse(BaseModel):
    provider: str
    session_id: str
    url: Optional[str] = None
    plan_key: str
    status: str
    # ORB AI 2.0.1 — server-authoritative order details for mobile checkout.
    # The mobile client uses these verbatim and NEVER recomputes the price.
    order_id: Optional[str] = None
    amount_cents: Optional[int] = None
    currency: Optional[str] = None
    key_id: Optional[str] = None


class AdminAssignPlan(BaseModel):
    user_id: str
    plan_key: str
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE


class AdminRefundRequest(BaseModel):
    payment_reference: str = Field(..., description="Provider payment id to refund")
    amount_cents: Optional[int] = Field(
        default=None, description="Partial-refund amount; omit for full refund",
    )
    reason: Optional[str] = Field(default=None, max_length=500)


class RefundResponse(BaseModel):
    provider: str
    refund_id: str
    amount_cents: int
    status: str


# ---- Public endpoints ----
@router.get("/plans", response_model=list[PlanRead], summary="List available plans")
async def list_plans(session: DBSession) -> list[PlanRead]:
    rows = (await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.price_cents.asc())
    )).scalars().all()
    return [PlanRead.model_validate(r) for r in rows]


@router.get("/me", response_model=MeSubscription, summary="Current user's plan + features")
async def me(current_user: CurrentUser, session: DBSession) -> MeSubscription:
    gate = FeatureGate(session)
    sub = await gate.get_subscription(current_user)
    plan = await gate.get_plan(current_user)
    features = await gate.features_for(current_user)
    subscription = None
    if plan is not None:
        subscription = SubscriptionRead(
            plan_key=plan.key,
            plan_name=plan.name,
            tier=plan.tier,
            status=sub.status if sub else SubscriptionStatus.ACTIVE,
            provider=sub.provider if sub else "noop",
            cancel_at_period_end=bool(sub.cancel_at_period_end) if sub else False,
            current_period_end=(sub.current_period_end.isoformat()
                                 if sub and sub.current_period_end else None),
        )
    return MeSubscription(subscription=subscription, features=features)


@router.post("/checkout", response_model=CheckoutResponse, summary="Start a checkout session")
async def checkout(
    payload: CheckoutRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> CheckoutResponse:
    plan = (await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.key == payload.plan_key)
    )).scalar_one_or_none()
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown plan: {payload.plan_key}",
        )

    provider = get_billing_provider()

    # Noop provider — activate the plan immediately (free tier / dev).
    if provider.name == "noop":
        gate = FeatureGate(session)
        await gate.set_plan(
            current_user, payload.plan_key, provider="noop",
            status=SubscriptionStatus.ACTIVE,
        )
        await AuditService(session).record(
            action="subscription.activated", target_type="user_subscription",
            target_id=current_user.id, actor=current_user,
            details={"plan_key": payload.plan_key, "provider": "noop"},
        )
        await session.commit()
        return CheckoutResponse(
            provider="noop",
            session_id=f"noop_{payload.plan_key}",
            url=None,
            plan_key=payload.plan_key,
            status="active",
        )

    # External provider (Razorpay, Stripe, etc.) — kick off checkout session.
    # The amount is SERVER-AUTHORITATIVE: it is read from the selected plan's
    # price_cents (INR paise) and passed to the provider. The client only ever
    # sends `plan_key`; it never supplies or recomputes a price.
    amount_cents = int(plan.price_cents or 0)
    if amount_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Plan '{plan.key}' has a non-positive price ({amount_cents} paise); "
                "cannot start a paid checkout"
            ),
        )
    try:
        checkout_session = await provider.create_checkout(
            user_id=current_user.id,
            user_email=current_user.email,
            plan_key=payload.plan_key,
            provider_price_id=plan.provider_price_id,
            success_url=payload.success_url or settings.RAZORPAY_SUCCESS_URL,
            cancel_url=payload.cancel_url or settings.RAZORPAY_CANCEL_URL,
            amount_cents=amount_cents,
            currency=plan.currency or "INR",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    meta = checkout_session.metadata or {}
    key_id = os.environ.get("RAZORPAY_KEY_ID") if provider.name == "razorpay" else None

    await AuditService(session).record(
        action="subscription.checkout_started", target_type="user",
        target_id=current_user.id, actor=current_user,
        details={
            "plan_key": payload.plan_key, "provider": provider.name,
            "amount_cents": int(meta.get("amount_cents", amount_cents)),
        },
    )
    await session.commit()
    return CheckoutResponse(
        provider=provider.name,
        session_id=checkout_session.session_id,
        url=checkout_session.url,
        plan_key=payload.plan_key,
        status="pending_payment",
        order_id=meta.get("order_id", checkout_session.session_id),
        amount_cents=int(meta.get("amount_cents", amount_cents)),
        currency=meta.get("currency", plan.currency or "INR"),
        key_id=key_id,
    )


@router.post("/cancel", summary="Cancel current subscription at period end")
async def cancel(current_user: CurrentUser, session: DBSession) -> dict[str, Any]:
    gate = FeatureGate(session)
    sub = await gate.get_subscription(current_user)
    if sub is None or sub.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
        raise HTTPException(status_code=400, detail="No active subscription to cancel")

    provider = get_billing_provider()
    if sub.provider_subscription_id and provider.name != "noop":
        await provider.cancel_subscription(
            provider_subscription_id=sub.provider_subscription_id
        )
    sub.cancel_at_period_end = True

    await AuditService(session).record(
        action="subscription.cancelled", target_type="user_subscription",
        target_id=sub.id, actor=current_user,
        details={"provider": sub.provider},
    )
    await session.commit()
    return {"cancelled": True, "cancel_at_period_end": True}


@router.post("/webhook/{provider_name}", summary="Billing webhook")
async def webhook(
    provider_name: str,
    request: Request,
    session: DBSession,
) -> dict[str, Any]:
    body = await request.body()
    signature = (
        request.headers.get("stripe-signature")
        or request.headers.get("x-razorpay-signature")
        or request.headers.get("x-signature")
    )
    provider = get_billing_provider()
    if provider.name != provider_name:
        # Accept & discard — allows multiple providers to be safely
        # pointed at the same webhook without breaking.
        return {"received": True, "handled": False, "reason": "provider_mismatch"}
    try:
        payload = await provider.handle_webhook(body=body, signature=signature)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid webhook: {e}")

    event = payload.get("event")
    # Fail closed: an unverifiable / malformed event never triggers a
    # business action. A subscription can NEVER be activated off it.
    if event in ("invalid_signature", "invalid_payload", "ignored", None):
        return {"received": True, "handled": False, "event": event}

    activated = False
    # A subscription becomes active ONLY after a verified successful payment
    # event that carries the originating user + plan. Failed / cancelled
    # events fall through and never activate. Processing is idempotent —
    # re-delivering the same event just re-attaches the same plan/status.
    is_success = bool(payload.get("success"))
    user_id = payload.get("user_id")
    plan_key = payload.get("plan_key")
    if is_success and user_id and plan_key:
        user = await session.get(User, user_id)
        if user is not None:
            gate = FeatureGate(session)
            existing = await gate.get_subscription(user)
            psid = payload.get("provider_subscription_id")
            already = (
                existing is not None
                and existing.status == SubscriptionStatus.ACTIVE
                and existing.provider_subscription_id == psid
                and psid is not None
            )
            if not already:
                try:
                    await gate.set_plan(
                        user, plan_key,
                        provider=provider.name,
                        provider_subscription_id=psid,
                        status=SubscriptionStatus.ACTIVE,
                    )
                    activated = True
                except ValueError:
                    activated = False

    await AuditService(session).record(
        action="subscription.webhook", target_type="billing_event",
        target_id=(event or "unknown")[:36],
        details={
            "provider": provider.name, "event": event,
            "activated": activated,
        },
    )
    await session.commit()
    return {
        "received": True, "handled": True,
        "event": event, "activated": activated,
    }


# ---- Admin endpoints ----
@router.post(
    "/admin/refund",
    response_model=RefundResponse,
    summary="[admin] Issue a refund via the configured billing provider",
)
async def admin_refund(
    payload: AdminRefundRequest,
    _admin: AdminUser,
    session: DBSession,
) -> RefundResponse:
    provider = get_billing_provider()
    try:
        result = await provider.refund(
            payment_reference=payload.payment_reference,
            amount_cents=payload.amount_cents,
            reason=payload.reason,
        )
    except NotImplementedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await AuditService(session).record(
        action="subscription.refund_issued",
        target_type="payment",
        target_id=payload.payment_reference[:36],
        actor=_admin,
        details={
            "provider": provider.name,
            "amount_cents": result.amount_cents,
            "refund_id": result.refund_id,
            "reason": payload.reason,
            "status": result.status,
        },
    )
    await session.commit()
    return RefundResponse(
        provider=result.provider,
        refund_id=result.refund_id,
        amount_cents=result.amount_cents,
        status=result.status,
    )


class MockWebhookRequest(BaseModel):
    event: str = Field(..., description="Event name, e.g. 'subscription.activated'")
    provider_subscription_id: str
    status: str = "active"
    plan_key: Optional[str] = None
    user_id: Optional[str] = None


@router.post(
    "/admin/mock-webhook",
    summary="[admin] Emit a signed synthetic webhook (mock provider only)",
)
async def admin_mock_webhook(
    payload: MockWebhookRequest,
    _admin: AdminUser,
    request: Request,
    session: DBSession,
) -> dict[str, Any]:
    """Test helper — forge a valid HMAC-signed mock webhook and dispatch
    it through the real webhook path. Only usable when the active
    billing provider is ``mock``.
    """
    provider = get_billing_provider()
    if provider.name != "mock":
        raise HTTPException(
            status_code=400,
            detail="mock-webhook is only available when BILLING_PROVIDER=mock",
        )
    body, sig = MockBillingProvider.build_event(
        payload.event,
        provider_subscription_id=payload.provider_subscription_id,
        status=payload.status,
        plan_key=payload.plan_key,
        user_id=payload.user_id,
    )
    result = await provider.handle_webhook(body=body, signature=sig)
    await AuditService(session).record(
        action="subscription.webhook",
        target_type="billing_event",
        target_id=payload.event[:36],
        actor=_admin,
        details={"provider": "mock", "event": payload.event, "synthetic": True},
    )
    await session.commit()
    return {"received": True, "handled": True, "result": result}


@router.post("/admin/assign", summary="[admin] Assign a plan to a user")
async def admin_assign(
    payload: AdminAssignPlan,
    _admin: AdminUser,
    session: DBSession,
) -> SubscriptionRead:
    user = await session.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    gate = FeatureGate(session)
    try:
        sub = await gate.set_plan(user, payload.plan_key, status=payload.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    plan = await session.get(SubscriptionPlan, sub.plan_id)
    await AuditService(session).record(
        action="subscription.admin_assigned",
        target_type="user_subscription", target_id=sub.id, actor=_admin,
        details={"user_id": user.id, "plan_key": plan.key},
    )
    await session.commit()
    return SubscriptionRead(
        plan_key=plan.key, plan_name=plan.name, tier=plan.tier,
        status=sub.status, provider=sub.provider,
        cancel_at_period_end=bool(sub.cancel_at_period_end),
        current_period_end=(sub.current_period_end.isoformat()
                             if sub.current_period_end else None),
    )
