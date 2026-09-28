"""Affiliate REST endpoints (Phase 3).

Base path: /api/v1/affiliates
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.config import settings
from app.models.affiliate import Affiliate, AffiliateStatus, PayoutMethod
from app.services.affiliate.affiliate_service import (
    AffiliateService,
    AlreadyRegistered,
    NotEligible,
)
from app.services.affiliate.program_service import (
    ProgramService,
    render_qr_data_url,
)
from app.services.audit_service import AuditService

router = APIRouter()


# -------------------------------------------------------------- schemas
class AffiliateOut(BaseModel):
    id: str
    user_id: str
    code: str
    display_name: Optional[str] = None
    status: str
    application_notes: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejected_reason: Optional[str] = None
    commission_rate_pct: Optional[int] = None
    payout_method: str
    payout_details: Optional[dict] = None
    total_clicks: int
    total_signups: int
    total_conversions: int
    total_commission_earned_cents: int
    total_commission_paid_cents: int
    created_at: datetime

    @classmethod
    def from_model(cls, a: Affiliate) -> "AffiliateOut":
        return cls(
            id=a.id, user_id=a.user_id, code=a.code,
            display_name=a.display_name, status=a.status.value,
            application_notes=a.application_notes,
            approved_at=a.approved_at, rejected_reason=a.rejected_reason,
            commission_rate_pct=a.commission_rate_pct,
            payout_method=a.payout_method.value,
            payout_details=a.payout_details,
            total_clicks=a.total_clicks, total_signups=a.total_signups,
            total_conversions=a.total_conversions,
            total_commission_earned_cents=a.total_commission_earned_cents,
            total_commission_paid_cents=a.total_commission_paid_cents,
            created_at=a.created_at,
        )


class RegisterIn(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=128)
    application_notes: Optional[str] = Field(default=None, max_length=2000)
    payout_method: str = "wallet_only"
    payout_details: Optional[dict] = None


class ApproveIn(BaseModel):
    commission_rate_pct: Optional[int] = Field(default=None, ge=0, le=100)


class RejectIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class UpdateAffiliateIn(BaseModel):
    display_name: Optional[str] = None
    commission_rate_pct: Optional[int] = Field(default=None, ge=0, le=100)
    payout_method: Optional[str] = None
    payout_details: Optional[dict] = None


class ReferralLinkOut(BaseModel):
    code: str
    referral_url: str
    qr_data_url: str


class ProgramConfigOut(BaseModel):
    id: str
    default_commission_rate_pct: int
    click_attribution_days: int
    trial_attribution_days: int
    paid_attribution_days: int
    attribution_model: str
    block_self_referral: bool
    hold_period_days: int
    min_payout_cents: int
    is_enabled: bool
    currency: str


class ProgramConfigIn(BaseModel):
    default_commission_rate_pct: Optional[int] = Field(default=None, ge=0, le=100)
    click_attribution_days: Optional[int] = Field(default=None, ge=1, le=365)
    trial_attribution_days: Optional[int] = Field(default=None, ge=1, le=365)
    paid_attribution_days: Optional[int] = Field(default=None, ge=1, le=365)
    attribution_model: Optional[str] = Field(default=None, description="'first_touch' or 'last_touch'")
    block_self_referral: Optional[bool] = None
    hold_period_days: Optional[int] = Field(default=None, ge=0, le=180)
    min_payout_cents: Optional[int] = Field(default=None, ge=0)
    is_enabled: Optional[bool] = None
    currency: Optional[str] = None


# -------------------------------------------------------------- self
@router.post(
    "/register",
    response_model=AffiliateOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register the current user as an affiliate",
)
async def register(
    payload: RegisterIn,
    user: CurrentUser,
    session: DBSession,
) -> AffiliateOut:
    svc = AffiliateService(session)
    try:
        a = await svc.register(
            user,
            display_name=payload.display_name,
            application_notes=payload.application_notes,
            payout_method=payload.payout_method,
            payout_details=payload.payout_details,
        )
    except AlreadyRegistered as e:
        raise HTTPException(status_code=409, detail=str(e))
    except NotEligible as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await session.commit()
    return AffiliateOut.from_model(a)


@router.get("/me", response_model=AffiliateOut, summary="Get current affiliate profile")
async def get_me(user: CurrentUser, session: DBSession) -> AffiliateOut:
    a = await AffiliateService(session).by_user(user.id)
    if a is None:
        raise HTTPException(status_code=404, detail="not an affiliate")
    return AffiliateOut.from_model(a)


@router.patch("/me", response_model=AffiliateOut, summary="Update own affiliate profile")
async def update_me(
    payload: UpdateAffiliateIn, user: CurrentUser, session: DBSession,
) -> AffiliateOut:
    a = await AffiliateService(session).by_user(user.id)
    if a is None:
        raise HTTPException(status_code=404, detail="not an affiliate")
    data = payload.model_dump(exclude_none=True)
    if "payout_method" in data:
        try:
            data["payout_method"] = PayoutMethod(data["payout_method"])
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid payout_method")
    # commission_rate_pct only changeable by admin. Silently ignore here.
    data.pop("commission_rate_pct", None)
    for k, v in data.items():
        setattr(a, k, v)
    await session.commit()
    return AffiliateOut.from_model(a)


@router.get("/me/referral-link", response_model=ReferralLinkOut, summary="Own referral link + QR")
async def my_referral_link(
    user: CurrentUser, session: DBSession,
    base_url: Optional[str] = None,
) -> ReferralLinkOut:
    a = await AffiliateService(session).by_user(user.id)
    if a is None:
        raise HTTPException(status_code=404, detail="not an affiliate")
    if a.status != AffiliateStatus.APPROVED:
        raise HTTPException(status_code=403, detail="affiliate not approved")
    referral_base = base_url or settings.RAZORPAY_SUCCESS_URL.rsplit("/", 2)[0]
    referral_url = f"{referral_base.rstrip('/')}/r/{a.code}"
    return ReferralLinkOut(
        code=a.code,
        referral_url=referral_url,
        qr_data_url=render_qr_data_url(referral_url),
    )


# -------------------------------------------------------------- admin
@router.get(
    "/admin", response_model=list[AffiliateOut],
    summary="[admin] list affiliates",
)
async def admin_list(
    _: AdminUser, session: DBSession,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = 100,
) -> list[AffiliateOut]:
    st = None
    if status_filter:
        try:
            st = AffiliateStatus(status_filter)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid status")
    rows = await AffiliateService(session).list_all(status=st, limit=limit)
    return [AffiliateOut.from_model(a) for a in rows]


@router.post(
    "/admin/{affiliate_id}/approve",
    response_model=AffiliateOut,
    summary="[admin] approve an affiliate application",
)
async def admin_approve(
    affiliate_id: str, payload: ApproveIn,
    admin: AdminUser, session: DBSession,
) -> AffiliateOut:
    a = await session.get(Affiliate, affiliate_id)
    if a is None:
        raise HTTPException(status_code=404, detail="affiliate not found")
    if payload.commission_rate_pct is not None:
        a.commission_rate_pct = payload.commission_rate_pct
    await AffiliateService(session).approve(a, admin_id=admin.id)
    await AuditService(session).record(
        action="affiliate.approve", target_type="affiliate",
        target_id=a.id, actor=admin,
        details={"commission_rate_pct": a.commission_rate_pct},
    )
    await session.commit()
    return AffiliateOut.from_model(a)


@router.post(
    "/admin/{affiliate_id}/reject",
    response_model=AffiliateOut,
    summary="[admin] reject an affiliate application",
)
async def admin_reject(
    affiliate_id: str, payload: RejectIn,
    admin: AdminUser, session: DBSession,
) -> AffiliateOut:
    a = await session.get(Affiliate, affiliate_id)
    if a is None:
        raise HTTPException(status_code=404, detail="affiliate not found")
    await AffiliateService(session).reject(a, reason=payload.reason, admin_id=admin.id)
    await AuditService(session).record(
        action="affiliate.reject", target_type="affiliate",
        target_id=a.id, actor=admin, details={"reason": payload.reason},
    )
    await session.commit()
    return AffiliateOut.from_model(a)


@router.post(
    "/admin/{affiliate_id}/suspend",
    response_model=AffiliateOut,
    summary="[admin] suspend an affiliate",
)
async def admin_suspend(
    affiliate_id: str, payload: RejectIn,
    admin: AdminUser, session: DBSession,
) -> AffiliateOut:
    a = await session.get(Affiliate, affiliate_id)
    if a is None:
        raise HTTPException(status_code=404, detail="affiliate not found")
    await AffiliateService(session).suspend(a, reason=payload.reason, admin_id=admin.id)
    await AuditService(session).record(
        action="affiliate.suspend", target_type="affiliate",
        target_id=a.id, actor=admin, details={"reason": payload.reason},
    )
    await session.commit()
    return AffiliateOut.from_model(a)


# -------------------------------------------------------------- program config
@router.get(
    "/admin/program",
    response_model=ProgramConfigOut,
    summary="[admin] get global affiliate program config",
)
async def admin_get_program(_: AdminUser, session: DBSession) -> ProgramConfigOut:
    p = await ProgramService(session).get()
    await session.commit()
    return ProgramConfigOut(
        id=p.id,
        default_commission_rate_pct=p.default_commission_rate_pct,
        click_attribution_days=p.click_attribution_days,
        trial_attribution_days=p.trial_attribution_days,
        paid_attribution_days=p.paid_attribution_days,
        attribution_model=p.attribution_model.value,
        block_self_referral=p.block_self_referral,
        hold_period_days=p.hold_period_days,
        min_payout_cents=p.min_payout_cents,
        is_enabled=p.is_enabled,
        currency=p.currency,
    )


@router.patch(
    "/admin/program",
    response_model=ProgramConfigOut,
    summary="[admin] update global affiliate program config",
)
async def admin_update_program(
    payload: ProgramConfigIn, admin: AdminUser, session: DBSession,
) -> ProgramConfigOut:
    data = payload.model_dump(exclude_none=True)
    if "attribution_model" in data and data["attribution_model"] not in {"first_touch", "last_touch"}:
        raise HTTPException(status_code=400, detail="attribution_model must be 'first_touch' or 'last_touch'")
    p = await ProgramService(session).update(**data)
    await AuditService(session).record(
        action="affiliate.program.update", target_type="affiliate_program",
        target_id=p.id, actor=admin, details=data,
    )
    await session.commit()
    return ProgramConfigOut(
        id=p.id,
        default_commission_rate_pct=p.default_commission_rate_pct,
        click_attribution_days=p.click_attribution_days,
        trial_attribution_days=p.trial_attribution_days,
        paid_attribution_days=p.paid_attribution_days,
        attribution_model=p.attribution_model.value,
        block_self_referral=p.block_self_referral,
        hold_period_days=p.hold_period_days,
        min_payout_cents=p.min_payout_cents,
        is_enabled=p.is_enabled,
        currency=p.currency,
    )
