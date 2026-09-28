"""Coupon REST endpoints (v1.1.0 Phase 2).

Base path: /api/v1/coupons

Public: validate coupon codes (compute the discount without redeeming).
Admin: full CRUD.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.commerce import (
    Coupon,
    CouponDiscountType,
    CouponStatus,
    OrderKind,
)
from app.services.commerce.coupon_service import CouponService

router = APIRouter()


# --------------------------------------------------------------- schemas
class CouponOut(BaseModel):
    id: str
    code: str
    description: Optional[str] = None
    discount_type: str
    discount_value: int
    max_discount_cents: Optional[int] = None
    min_purchase_cents: int
    eligible_plans: Optional[list] = None
    eligible_order_kinds: Optional[list] = None
    trial_only: bool
    first_purchase_only: bool
    one_time_per_user: bool
    allow_stacking: bool
    max_combined_discount_cents: Optional[int] = None
    max_redemptions: Optional[int] = None
    redemptions_count: int
    valid_from: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    status: str

    @classmethod
    def from_model(cls, c: Coupon) -> "CouponOut":
        return cls(
            id=c.id,
            code=c.code,
            description=c.description,
            discount_type=c.discount_type.value,
            discount_value=c.discount_value,
            max_discount_cents=c.max_discount_cents,
            min_purchase_cents=c.min_purchase_cents,
            eligible_plans=c.eligible_plans,
            eligible_order_kinds=c.eligible_order_kinds,
            trial_only=c.trial_only,
            first_purchase_only=c.first_purchase_only,
            one_time_per_user=c.one_time_per_user,
            allow_stacking=c.allow_stacking,
            max_combined_discount_cents=c.max_combined_discount_cents,
            max_redemptions=c.max_redemptions,
            redemptions_count=c.redemptions_count,
            valid_from=c.valid_from,
            expires_at=c.expires_at,
            status=c.status.value,
        )


class CouponCreateIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    description: Optional[str] = None
    discount_type: str = Field(..., description="'percent' or 'flat'")
    discount_value: int = Field(..., ge=0)
    max_discount_cents: Optional[int] = Field(default=None, ge=0)
    min_purchase_cents: int = Field(default=0, ge=0)
    eligible_plans: Optional[list[str]] = None
    eligible_order_kinds: Optional[list[str]] = None
    trial_only: bool = False
    first_purchase_only: bool = False
    one_time_per_user: bool = True
    allow_stacking: bool = False
    max_combined_discount_cents: Optional[int] = Field(default=None, ge=0)
    max_redemptions: Optional[int] = Field(default=None, ge=0)
    valid_from: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class CouponUpdateIn(BaseModel):
    description: Optional[str] = None
    discount_value: Optional[int] = Field(default=None, ge=0)
    max_discount_cents: Optional[int] = None
    min_purchase_cents: Optional[int] = None
    eligible_plans: Optional[list[str]] = None
    eligible_order_kinds: Optional[list[str]] = None
    trial_only: Optional[bool] = None
    first_purchase_only: Optional[bool] = None
    one_time_per_user: Optional[bool] = None
    allow_stacking: Optional[bool] = None
    max_combined_discount_cents: Optional[int] = None
    max_redemptions: Optional[int] = None
    valid_from: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    status: Optional[str] = None


class ValidateIn(BaseModel):
    codes: list[str] = Field(..., min_length=1)
    order_kind: str = Field(..., description="'subscription' | 'trial' | 'strategy' | 'wallet_topup'")
    subtotal_cents: int = Field(..., ge=0)
    plan_key: Optional[str] = None


class ValidateOut(BaseModel):
    subtotal_cents: int
    total_discount_cents: int
    net_cents: int
    applied: list[dict[str, Any]]
    ineligible: list[dict[str, Any]]
    stacked: bool


# --------------------------------------------------------------- validate
@router.post("/validate", response_model=ValidateOut, summary="Validate a coupon (no redemption)")
async def validate_coupon(
    payload: ValidateIn,
    user: CurrentUser,
    session: DBSession,
) -> ValidateOut:
    kinds = {k.value for k in OrderKind}
    if payload.order_kind not in kinds:
        raise HTTPException(status_code=400, detail=f"order_kind must be one of {sorted(kinds)}")
    svc = CouponService(session)
    quote = await svc.quote(
        user_id=user.id,
        codes=payload.codes,
        order_kind=OrderKind(payload.order_kind),
        subtotal_cents=payload.subtotal_cents,
        plan_key=payload.plan_key,
    )
    net = max(0, payload.subtotal_cents - quote.total_discount_cents)
    return ValidateOut(
        subtotal_cents=payload.subtotal_cents,
        total_discount_cents=quote.total_discount_cents,
        net_cents=net,
        applied=quote.applied,
        ineligible=quote.ineligible,
        stacked=quote.stacked,
    )


# --------------------------------------------------------------- admin CRUD
@router.get("/admin", response_model=list[CouponOut], summary="[admin] list all coupons")
async def admin_list(_: AdminUser, session: DBSession) -> list[CouponOut]:
    rows = await session.scalars(select(Coupon).order_by(Coupon.created_at.desc()))
    return [CouponOut.from_model(c) for c in rows]


@router.post(
    "/admin",
    response_model=CouponOut,
    status_code=status.HTTP_201_CREATED,
    summary="[admin] create a coupon",
)
async def admin_create(
    payload: CouponCreateIn, _: AdminUser, session: DBSession,
) -> CouponOut:
    if payload.discount_type not in {t.value for t in CouponDiscountType}:
        raise HTTPException(status_code=400, detail="discount_type must be 'percent' or 'flat'")
    existing = await session.scalar(select(Coupon).where(Coupon.code == payload.code.upper()))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"coupon {payload.code!r} already exists")
    c = Coupon(
        code=payload.code.upper(),
        description=payload.description,
        discount_type=CouponDiscountType(payload.discount_type),
        discount_value=payload.discount_value,
        max_discount_cents=payload.max_discount_cents,
        min_purchase_cents=payload.min_purchase_cents,
        eligible_plans=payload.eligible_plans,
        eligible_order_kinds=payload.eligible_order_kinds,
        trial_only=payload.trial_only,
        first_purchase_only=payload.first_purchase_only,
        one_time_per_user=payload.one_time_per_user,
        allow_stacking=payload.allow_stacking,
        max_combined_discount_cents=payload.max_combined_discount_cents,
        max_redemptions=payload.max_redemptions,
        valid_from=payload.valid_from,
        expires_at=payload.expires_at,
        status=CouponStatus.ACTIVE,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return CouponOut.from_model(c)


@router.patch(
    "/admin/{coupon_id}",
    response_model=CouponOut,
    summary="[admin] update a coupon",
)
async def admin_update(
    coupon_id: str,
    payload: CouponUpdateIn,
    _: AdminUser,
    session: DBSession,
) -> CouponOut:
    c = await session.get(Coupon, coupon_id)
    if c is None:
        raise HTTPException(status_code=404, detail="coupon not found")
    data = payload.model_dump(exclude_none=True)
    if "status" in data:
        if data["status"] not in {s.value for s in CouponStatus}:
            raise HTTPException(status_code=400, detail="invalid status")
        data["status"] = CouponStatus(data["status"])
    for k, v in data.items():
        setattr(c, k, v)
    await session.commit()
    await session.refresh(c)
    return CouponOut.from_model(c)


@router.delete(
    "/admin/{coupon_id}",
    response_model=CouponOut,
    summary="[admin] disable a coupon (soft)",
)
async def admin_disable(coupon_id: str, _: AdminUser, session: DBSession) -> CouponOut:
    c = await session.get(Coupon, coupon_id)
    if c is None:
        raise HTTPException(status_code=404, detail="coupon not found")
    c.status = CouponStatus.DISABLED
    await session.commit()
    await session.refresh(c)
    return CouponOut.from_model(c)
