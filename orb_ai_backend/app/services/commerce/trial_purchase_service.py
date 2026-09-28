"""TrialPurchaseService — orchestrates the ₹50 paid trial through the
payment abstraction + coupon engine + wallet, then activates the
underlying TrialService.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import Order, OrderKind, OrderStatus, WalletTxnReason
from app.models.subscription import UserSubscription
from app.models.user import User
from app.services.commerce.order_service import OrderQuote, OrderService
from app.services.commerce.wallet_service import WalletService
from app.services.subscriptions.providers import BillingProvider, get_billing_provider
from app.services.trials import (
    DEFAULT_TRIAL_DURATION_DAYS,
    DEFAULT_TRIAL_PRICE_CENTS,
    DEFAULT_TRIAL_TARGET_PLAN_KEY,
    TrialAlreadyConsumedError,
    EmailNotVerifiedError,
    TrialService,
)


@dataclass
class TrialPurchaseResult:
    order: Order
    quote: OrderQuote
    subscription: Optional[UserSubscription]
    checkout_url: Optional[str]
    activated: bool


class TrialPurchaseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderService(session)
        self.trials = TrialService(session)
        self.wallets = WalletService(session)

    async def purchase(
        self,
        *,
        user: User,
        plan_key: str = DEFAULT_TRIAL_TARGET_PLAN_KEY,
        coupon_codes: Optional[list[str]] = None,
        use_wallet: bool = True,
        provider: Optional[BillingProvider] = None,
    ) -> TrialPurchaseResult:
        """Buy the ₹50 / 3-day trial for the given user.

        Flow
        ----
        1. Check email verified + trial not already consumed.
        2. Create a PENDING Order via OrderService (applies coupons + wallet).
        3. If total == 0 (wallet + coupons covered the ₹50), we
           auto-activate the trial (mock provider assumed for free path).
        4. Otherwise, hand off to the configured payment provider and
           return the checkout URL; the webhook / caller must invoke
           :meth:`confirm_paid` to activate.
        """
        if not user.is_verified:
            raise EmailNotVerifiedError()
        state = await self.trials.get_state(user)
        if state.trial_consumed_at is not None:
            raise TrialAlreadyConsumedError()

        provider = provider or get_billing_provider()
        order, quote = await self.orders.create(
            user=user,
            kind=OrderKind.TRIAL,
            target_ref=plan_key,
            subtotal_cents=DEFAULT_TRIAL_PRICE_CENTS,
            currency="INR",
            provider_name=provider.name,
            coupon_codes=coupon_codes,
            plan_key=plan_key,
            use_wallet=use_wallet,
            metadata={"trial_plan_key": plan_key},
        )

        if quote.total_cents == 0:
            await self.orders.mark_paid(order, payment_reference=f"free-trial-{order.id}")
            sub = await self.trials.start_trial(
                user, plan_key=plan_key,
                duration_days=DEFAULT_TRIAL_DURATION_DAYS,
                price_cents=DEFAULT_TRIAL_PRICE_CENTS,
                payment_reference=order.payment_reference,
            )
            return TrialPurchaseResult(
                order=order, quote=quote, subscription=sub,
                checkout_url=None, activated=True,
            )

        # Non-zero total → create provider checkout.
        session = await provider.create_checkout(
            user_id=user.id, user_email=user.email, plan_key=plan_key,
            provider_price_id=None,
            success_url=(
                (provider.name == "razorpay" and __import__("app.core.config", fromlist=["settings"]).settings.RAZORPAY_SUCCESS_URL)
                or (provider.name == "stripe" and __import__("app.core.config", fromlist=["settings"]).settings.STRIPE_SUCCESS_URL)
                or f"https://orb-ai.local/trial/{order.id}/success"
            ),
            cancel_url=(
                (provider.name == "razorpay" and __import__("app.core.config", fromlist=["settings"]).settings.RAZORPAY_CANCEL_URL)
                or (provider.name == "stripe" and __import__("app.core.config", fromlist=["settings"]).settings.STRIPE_CANCEL_URL)
                or f"https://orb-ai.local/trial/{order.id}/cancel"
            ),
        )
        order.provider_session_id = session.session_id
        await self.session.flush()
        return TrialPurchaseResult(
            order=order, quote=quote, subscription=None,
            checkout_url=session.url, activated=False,
        )

    async def confirm_paid(
        self,
        *,
        order: Order,
        payment_reference: Optional[str] = None,
    ) -> UserSubscription:
        """Called by the webhook (or admin/dev endpoint) after payment
        capture. Activates the trial and marks the order PAID.
        """
        assert order.kind == OrderKind.TRIAL
        if order.status != OrderStatus.PAID:
            await self.orders.mark_paid(order, payment_reference=payment_reference)
        user = await self.session.get(User, order.user_id)
        assert user is not None
        plan_key = (order.target_ref or DEFAULT_TRIAL_TARGET_PLAN_KEY)
        return await self.trials.start_trial(
            user, plan_key=plan_key,
            duration_days=DEFAULT_TRIAL_DURATION_DAYS,
            price_cents=DEFAULT_TRIAL_PRICE_CENTS,
            payment_reference=order.payment_reference or payment_reference,
        )
