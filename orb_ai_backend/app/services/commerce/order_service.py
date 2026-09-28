"""OrderService — creates Orders + orchestrates PaymentProvider + wallet + coupons.

Business services (marketplace, subscriptions, trials) call this rather
than talking to Razorpay/Stripe/Mock directly. Zero gateway coupling.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import (
    Order,
    OrderKind,
    OrderStatus,
    WalletTxnReason,
)
from app.models.user import User
from app.services.commerce.coupon_service import CouponQuote, CouponService
from app.services.commerce.wallet_service import WalletService


@dataclass
class OrderQuote:
    subtotal_cents: int
    discount_cents: int
    wallet_debit_cents: int
    total_cents: int
    currency: str
    coupons: list[dict]
    stacked: bool


class OrderService:
    """High-level order orchestration.

    * Computes coupons discount.
    * Applies wallet credit (bounded).
    * Persists an :class:`Order`.
    * Delegates final capture to :class:`~app.services.subscriptions.providers.BillingProvider`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.coupons = CouponService(session)
        self.wallets = WalletService(session)

    async def quote(
        self,
        *,
        user_id: str,
        kind: OrderKind,
        subtotal_cents: int,
        currency: str = "INR",
        coupon_codes: Optional[list[str]] = None,
        plan_key: Optional[str] = None,
        use_wallet: bool = True,
        wallet_cap_cents: Optional[int] = None,
    ) -> OrderQuote:
        codes = coupon_codes or []
        quote: CouponQuote = await self.coupons.quote(
            user_id=user_id,
            codes=codes,
            order_kind=kind,
            subtotal_cents=subtotal_cents,
            plan_key=plan_key,
        )
        discount = quote.total_discount_cents
        running = max(0, subtotal_cents - discount)

        wallet_debit = 0
        if use_wallet and running > 0:
            snap = await self.wallets.snapshot(user_id)
            available = snap.balance_cents
            desired = running
            if wallet_cap_cents is not None:
                desired = min(desired, wallet_cap_cents)
            wallet_debit = min(available, desired)

        total = max(0, running - wallet_debit)

        return OrderQuote(
            subtotal_cents=subtotal_cents,
            discount_cents=discount,
            wallet_debit_cents=wallet_debit,
            total_cents=total,
            currency=currency,
            coupons=quote.applied,
            stacked=quote.stacked,
        )

    async def create(
        self,
        *,
        user: User,
        kind: OrderKind,
        target_ref: Optional[str],
        subtotal_cents: int,
        currency: str = "INR",
        provider_name: str = "mock",
        coupon_codes: Optional[list[str]] = None,
        plan_key: Optional[str] = None,
        use_wallet: bool = True,
        wallet_cap_cents: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> tuple[Order, OrderQuote]:
        """Create an Order row in ``pending`` state."""
        quote = await self.quote(
            user_id=user.id, kind=kind,
            subtotal_cents=subtotal_cents, currency=currency,
            coupon_codes=coupon_codes, plan_key=plan_key,
            use_wallet=use_wallet, wallet_cap_cents=wallet_cap_cents,
        )
        coupon_code = quote.coupons[0]["code"] if quote.coupons else None
        coupon_id = quote.coupons[0]["coupon_id"] if quote.coupons else None
        order = Order(
            user_id=user.id,
            kind=kind,
            status=OrderStatus.PENDING,
            subtotal_cents=quote.subtotal_cents,
            discount_cents=quote.discount_cents,
            wallet_debit_cents=quote.wallet_debit_cents,
            total_cents=quote.total_cents,
            currency=currency,
            target_ref=target_ref,
            provider=provider_name,
            coupon_code=coupon_code,
            coupon_id=coupon_id,
            order_metadata=(metadata or {}) | {
                "coupons": quote.coupons,
                "stacked": quote.stacked,
            },
        )
        self.session.add(order)
        await self.session.flush()
        return order, quote

    async def mark_paid(
        self,
        order: Order,
        *,
        payment_reference: Optional[str] = None,
        provider_session_id: Optional[str] = None,
    ) -> Order:
        """Finalise an order: debit wallet, redeem coupons, flip status."""
        if order.status == OrderStatus.PAID:
            return order
        # Debit wallet (idempotent — we only debit up to what's declared).
        if order.wallet_debit_cents > 0:
            await self.wallets.debit(
                order.user_id,
                order.wallet_debit_cents,
                reason=WalletTxnReason.PURCHASE,
                description=f"Order {order.id} ({order.kind.value})",
                reference_type="order",
                reference_id=order.id,
                allow_partial=False,
            )
        # Redeem coupons.
        coupons = (order.order_metadata or {}).get("coupons") or []
        if coupons:
            await self.coupons.redeem(
                user_id=order.user_id, applied=coupons, order_id=order.id,
            )
        order.status = OrderStatus.PAID
        order.paid_at = datetime.now(timezone.utc)
        if payment_reference:
            order.payment_reference = payment_reference
        if provider_session_id:
            order.provider_session_id = provider_session_id
        await self.session.flush()

        # Phase 3 — create affiliate commission if the payer was attributed.
        # Import here to avoid circular deps at module load.
        try:
            from app.services.affiliate.commission_engine import CommissionEngine
            await CommissionEngine(self.session).on_paid_order(order)
        except Exception:  # noqa: BLE001 - commission failure must never break checkout
            import logging
            logging.getLogger(__name__).exception("commission_creation_failed")
        return order

    async def mark_failed(self, order: Order, *, reason: Optional[str] = None) -> Order:
        order.status = OrderStatus.FAILED
        meta = order.order_metadata or {}
        meta["failure_reason"] = reason or "unknown"
        order.order_metadata = meta
        await self.session.flush()
        return order

    async def history(
        self, user_id: str, *, limit: int = 50, kind: Optional[OrderKind] = None,
    ) -> list[Order]:
        stmt = select(Order).where(Order.user_id == user_id)
        if kind is not None:
            stmt = stmt.where(Order.kind == kind)
        stmt = stmt.order_by(Order.created_at.desc()).limit(limit)
        rows = await self.session.scalars(stmt)
        return list(rows)
