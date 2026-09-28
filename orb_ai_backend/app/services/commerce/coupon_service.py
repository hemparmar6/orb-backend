"""CouponService — validation, redemption, stacking rules.

The service is provider-agnostic: it only computes the discount to
apply. Actual money movement is orchestrated by the calling service
(marketplace, subscription, trial).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import (
    Coupon,
    CouponDiscountType,
    CouponRedemption,
    CouponStatus,
    Order,
    OrderKind,
    OrderStatus,
)


class CouponError(Exception):
    pass


class CouponNotFound(CouponError):
    pass


class CouponIneligible(CouponError):
    pass


@dataclass
class CouponQuote:
    """Result of validating one or more coupons against a purchase."""

    applied: list[dict]           # [{coupon_id, code, discount_cents}, ...]
    total_discount_cents: int
    ineligible: list[dict]        # [{code, reason}, ...]
    stacked: bool


class CouponService:
    """Coupon validation + redemption engine."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ lookup

    async def get_by_code(self, code: str) -> Coupon:
        code = (code or "").strip().upper()
        c = await self.session.scalar(select(Coupon).where(func.upper(Coupon.code) == code))
        if c is None:
            raise CouponNotFound(f"coupon {code!r} not found")
        return c

    # ------------------------------------------------------------------ core

    async def quote(
        self,
        *,
        user_id: str,
        codes: list[str],
        order_kind: OrderKind,
        subtotal_cents: int,
        plan_key: Optional[str] = None,
        is_first_purchase: Optional[bool] = None,
    ) -> CouponQuote:
        """Compute the effective discount for a set of coupon codes.

        Stacking rules are enforced here:
        * Only coupons that individually pass eligibility are considered.
        * If more than one is provided, ALL of them must have
          ``allow_stacking=True``, else stacking is silently
          rejected and only the first eligible coupon is applied.
        * The combined discount cap = min of all
          ``max_combined_discount_cents`` values that are set.
        """
        applied: list[dict] = []
        ineligible: list[dict] = []
        eligible: list[Coupon] = []

        seen_codes: set[str] = set()
        for code in codes:
            up = (code or "").strip().upper()
            if not up or up in seen_codes:
                continue
            seen_codes.add(up)
            try:
                c = await self.get_by_code(up)
            except CouponNotFound:
                ineligible.append({"code": up, "reason": "not_found"})
                continue
            reason = await self._check_eligibility(
                c, user_id=user_id, order_kind=order_kind,
                subtotal_cents=subtotal_cents, plan_key=plan_key,
                is_first_purchase=is_first_purchase,
            )
            if reason:
                ineligible.append({"code": c.code, "reason": reason})
                continue
            eligible.append(c)

        stacked = False
        if len(eligible) > 1 and all(c.allow_stacking for c in eligible):
            stacked = True
            selected = eligible
        elif len(eligible) >= 1:
            # If stacking not allowed, keep only the first eligible coupon.
            selected = eligible[:1]
            for extra in eligible[1:]:
                ineligible.append({"code": extra.code, "reason": "stacking_not_allowed"})
        else:
            selected = []

        running_subtotal = subtotal_cents
        total_discount = 0
        combined_caps = [c.max_combined_discount_cents for c in selected if c.max_combined_discount_cents]
        combined_cap = min(combined_caps) if combined_caps else None

        for c in selected:
            d = self._compute_discount(c, running_subtotal)
            if combined_cap is not None and (total_discount + d) > combined_cap:
                d = max(0, combined_cap - total_discount)
                if d == 0:
                    ineligible.append({"code": c.code, "reason": "combined_cap_reached"})
                    continue
            applied.append({
                "coupon_id": c.id,
                "code": c.code,
                "discount_cents": d,
                "discount_type": c.discount_type.value,
            })
            total_discount += d
            running_subtotal -= d
            if running_subtotal <= 0:
                break

        total_discount = min(total_discount, subtotal_cents)
        return CouponQuote(
            applied=applied,
            total_discount_cents=total_discount,
            ineligible=ineligible,
            stacked=stacked,
        )

    async def redeem(
        self,
        *,
        user_id: str,
        applied: list[dict],
        order_id: Optional[str] = None,
    ) -> list[CouponRedemption]:
        """Persist redemptions and increment ``redemptions_count``.

        ``applied`` must be the output of :meth:`quote` — the caller is
        responsible for ensuring the coupons are still valid at commit
        time (small race window is acceptable for this iteration).
        """
        redemptions: list[CouponRedemption] = []
        for row in applied:
            coupon_id = row["coupon_id"]
            discount = int(row["discount_cents"])
            c = await self.session.get(Coupon, coupon_id)
            if c is None:
                continue
            c.redemptions_count = (c.redemptions_count or 0) + 1
            if c.max_redemptions is not None and c.redemptions_count >= c.max_redemptions:
                c.status = CouponStatus.EXHAUSTED
            r = CouponRedemption(
                coupon_id=c.id,
                user_id=user_id,
                order_id=order_id,
                discount_applied_cents=discount,
            )
            self.session.add(r)
            redemptions.append(r)
        await self.session.flush()
        return redemptions

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _compute_discount(c: Coupon, subtotal_cents: int) -> int:
        if c.discount_type == CouponDiscountType.PERCENT:
            raw = subtotal_cents * int(c.discount_value) // 100
        else:  # FLAT
            raw = int(c.discount_value)
        if c.max_discount_cents is not None:
            raw = min(raw, int(c.max_discount_cents))
        return max(0, min(raw, subtotal_cents))

    async def _check_eligibility(
        self,
        c: Coupon,
        *,
        user_id: str,
        order_kind: OrderKind,
        subtotal_cents: int,
        plan_key: Optional[str],
        is_first_purchase: Optional[bool],
    ) -> Optional[str]:
        # Status
        if c.status != CouponStatus.ACTIVE:
            return f"status_{c.status.value}"
        now = datetime.now(timezone.utc)
        if c.valid_from and c.valid_from > now:
            return "not_yet_valid"
        if c.expires_at and c.expires_at < now:
            return "expired"
        # Global limits
        if c.max_redemptions is not None and c.redemptions_count >= c.max_redemptions:
            return "exhausted"
        # Minimum purchase
        if subtotal_cents < (c.min_purchase_cents or 0):
            return "below_minimum_purchase"
        # Kind restriction
        if c.eligible_order_kinds:
            allowed = {str(k).lower() for k in c.eligible_order_kinds}
            if order_kind.value not in allowed:
                return "order_kind_not_eligible"
        # Trial-only
        if c.trial_only and order_kind != OrderKind.TRIAL:
            return "trial_only_coupon"
        # Plan restriction
        if c.eligible_plans and plan_key:
            if plan_key.lower() not in {str(p).lower() for p in c.eligible_plans}:
                return "plan_not_eligible"
        # First purchase restriction
        if c.first_purchase_only:
            if is_first_purchase is None:
                is_first_purchase = await self._user_has_no_paid_orders(user_id)
            if not is_first_purchase:
                return "not_first_purchase"
        # One-time per user
        if c.one_time_per_user:
            existing = await self.session.scalar(
                select(func.count(CouponRedemption.id)).where(
                    CouponRedemption.coupon_id == c.id,
                    CouponRedemption.user_id == user_id,
                )
            )
            if int(existing or 0) > 0:
                return "already_redeemed_by_user"
        return None

    async def _user_has_no_paid_orders(self, user_id: str) -> bool:
        n = await self.session.scalar(
            select(func.count(Order.id)).where(
                Order.user_id == user_id,
                Order.status == OrderStatus.PAID,
            )
        )
        return int(n or 0) == 0
