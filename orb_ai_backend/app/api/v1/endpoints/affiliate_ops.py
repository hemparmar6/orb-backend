"""Commission + Payout REST endpoints + affiliate analytics.

Base paths: /api/v1/commissions, /api/v1/payouts, /api/v1/affiliate-analytics
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.affiliate import (
    Commission,
    CommissionStatus,
    FraudFlag,
    Payout,
    PayoutMethod,
    PayoutStatus,
)
from app.services.affiliate.affiliate_service import AffiliateService
from app.services.affiliate.analytics_service import AffiliateAnalyticsService
from app.services.affiliate.commission_engine import CommissionEngine
from app.services.affiliate.fraud_service import FraudService
from app.services.affiliate.payout_service import (
    BelowMinimumPayout,
    InsufficientBalance,
    PayoutService,
)
from app.services.audit_service import AuditService

router = APIRouter()


# ============================================================ COMMISSIONS
class CommissionOut(BaseModel):
    id: str
    affiliate_id: str
    user_id: str
    order_id: Optional[str] = None
    event_type: str
    base_amount_cents: int
    rate_pct: int
    amount_cents: int
    currency: str
    status: str
    scheduled_release_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    created_at: datetime

    @classmethod
    def from_model(cls, c: Commission) -> "CommissionOut":
        return cls(
            id=c.id, affiliate_id=c.affiliate_id, user_id=c.user_id,
            order_id=c.order_id, event_type=c.event_type.value,
            base_amount_cents=c.base_amount_cents, rate_pct=c.rate_pct,
            amount_cents=c.amount_cents, currency=c.currency,
            status=c.status.value,
            scheduled_release_at=c.scheduled_release_at,
            released_at=c.released_at, created_at=c.created_at,
        )


@router.get(
    "/commissions/me",
    response_model=list[CommissionOut],
    summary="Current affiliate's commissions",
)
async def my_commissions(
    user: CurrentUser, session: DBSession, limit: int = 200,
) -> list[CommissionOut]:
    aff = await AffiliateService(session).by_user(user.id)
    if aff is None:
        raise HTTPException(status_code=403, detail="not an affiliate")
    rows = await session.scalars(
        select(Commission).where(Commission.affiliate_id == aff.id)
        .order_by(Commission.created_at.desc()).limit(limit)
    )
    return [CommissionOut.from_model(c) for c in rows]


@router.get(
    "/commissions/admin",
    response_model=list[CommissionOut],
    summary="[admin] list all commissions",
)
async def admin_list_commissions(
    _: AdminUser, session: DBSession,
    affiliate_id: Optional[str] = None,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = 200,
) -> list[CommissionOut]:
    stmt = select(Commission)
    if affiliate_id:
        stmt = stmt.where(Commission.affiliate_id == affiliate_id)
    if status_filter:
        try:
            stmt = stmt.where(Commission.status == CommissionStatus(status_filter))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid status")
    stmt = stmt.order_by(Commission.created_at.desc()).limit(limit)
    rows = await session.scalars(stmt)
    return [CommissionOut.from_model(c) for c in rows]


@router.post(
    "/commissions/admin/release-matured",
    summary="[admin] release commissions past hold period (idempotent)",
)
async def admin_release(_: AdminUser, session: DBSession) -> dict:
    released = await CommissionEngine(session).release_matured()
    await session.commit()
    return {"released": released}


class ReverseIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


@router.post(
    "/commissions/admin/{commission_id}/reverse",
    response_model=CommissionOut,
    summary="[admin] reverse a commission",
)
async def admin_reverse(
    commission_id: str, payload: ReverseIn,
    admin: AdminUser, session: DBSession,
) -> CommissionOut:
    c = await session.get(Commission, commission_id)
    if c is None:
        raise HTTPException(status_code=404, detail="commission not found")
    await CommissionEngine(session).reverse(c, reason=payload.reason)
    await AuditService(session).record(
        action="commission.reverse", target_type="commission",
        target_id=c.id, actor=admin, details={"reason": payload.reason},
    )
    await session.commit()
    return CommissionOut.from_model(c)


# ============================================================ PAYOUTS
class PayoutOut(BaseModel):
    id: str
    affiliate_id: str
    amount_cents: int
    currency: str
    method: str
    status: str
    requested_at: datetime
    processed_at: Optional[datetime] = None
    transaction_ref: Optional[str] = None
    notes: Optional[str] = None

    @classmethod
    def from_model(cls, p: Payout) -> "PayoutOut":
        return cls(
            id=p.id, affiliate_id=p.affiliate_id,
            amount_cents=p.amount_cents, currency=p.currency,
            method=p.method.value, status=p.status.value,
            requested_at=p.requested_at, processed_at=p.processed_at,
            transaction_ref=p.transaction_ref, notes=p.notes,
        )


class PayoutRequestIn(BaseModel):
    amount_cents: int = Field(..., gt=0)
    method: Optional[str] = None
    method_details: Optional[dict] = None
    notes: Optional[str] = None


@router.post(
    "/payouts/request",
    response_model=PayoutOut,
    status_code=status.HTTP_201_CREATED,
    summary="Request a payout",
)
async def request_payout(
    payload: PayoutRequestIn, user: CurrentUser, session: DBSession,
) -> PayoutOut:
    aff = await AffiliateService(session).by_user(user.id)
    if aff is None:
        raise HTTPException(status_code=403, detail="not an affiliate")
    method = None
    if payload.method:
        try:
            method = PayoutMethod(payload.method)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid payout method")
    try:
        p = await PayoutService(session).request(
            aff, amount_cents=payload.amount_cents,
            method=method, method_details=payload.method_details,
            notes=payload.notes,
        )
    except BelowMinimumPayout as e:
        raise HTTPException(status_code=400, detail=str(e))
    except InsufficientBalance as e:
        raise HTTPException(status_code=400, detail=str(e))
    await session.commit()
    return PayoutOut.from_model(p)


@router.get("/payouts/me", response_model=list[PayoutOut], summary="Own payout history")
async def my_payouts(user: CurrentUser, session: DBSession) -> list[PayoutOut]:
    aff = await AffiliateService(session).by_user(user.id)
    if aff is None:
        return []
    rows = await PayoutService(session).list_for_affiliate(aff.id)
    return [PayoutOut.from_model(p) for p in rows]


@router.get("/payouts/admin", response_model=list[PayoutOut], summary="[admin] list payouts")
async def admin_list_payouts(
    _: AdminUser, session: DBSession,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = 200,
) -> list[PayoutOut]:
    st = None
    if status_filter:
        try:
            st = PayoutStatus(status_filter)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid status")
    rows = await PayoutService(session).list_all(status=st, limit=limit)
    return [PayoutOut.from_model(p) for p in rows]


class ProcessIn(BaseModel):
    transaction_ref: Optional[str] = None
    notes: Optional[str] = None


@router.post("/payouts/admin/{payout_id}/approve", response_model=PayoutOut)
async def admin_approve_payout(
    payout_id: str, admin: AdminUser, session: DBSession,
) -> PayoutOut:
    p = await session.get(Payout, payout_id)
    if p is None:
        raise HTTPException(status_code=404, detail="payout not found")
    await PayoutService(session).approve(p, admin_id=admin.id)
    await AuditService(session).record(
        action="payout.approve", target_type="payout",
        target_id=p.id, actor=admin, details={},
    )
    await session.commit()
    return PayoutOut.from_model(p)


@router.post("/payouts/admin/{payout_id}/process", response_model=PayoutOut)
async def admin_mark_paid(
    payout_id: str, payload: ProcessIn,
    admin: AdminUser, session: DBSession,
) -> PayoutOut:
    p = await session.get(Payout, payout_id)
    if p is None:
        raise HTTPException(status_code=404, detail="payout not found")
    await PayoutService(session).mark_paid(
        p, admin_id=admin.id, transaction_ref=payload.transaction_ref,
    )
    await AuditService(session).record(
        action="payout.paid", target_type="payout",
        target_id=p.id, actor=admin,
        details={"transaction_ref": payload.transaction_ref},
    )
    await session.commit()
    return PayoutOut.from_model(p)


@router.post("/payouts/admin/{payout_id}/reject", response_model=PayoutOut)
async def admin_reject_payout(
    payout_id: str, payload: ReverseIn,
    admin: AdminUser, session: DBSession,
) -> PayoutOut:
    p = await session.get(Payout, payout_id)
    if p is None:
        raise HTTPException(status_code=404, detail="payout not found")
    await PayoutService(session).reject(
        p, admin_id=admin.id, reason=payload.reason,
    )
    await AuditService(session).record(
        action="payout.reject", target_type="payout",
        target_id=p.id, actor=admin, details={"reason": payload.reason},
    )
    await session.commit()
    return PayoutOut.from_model(p)


# ============================================================ ANALYTICS
@router.get(
    "/affiliate-analytics/summary",
    summary="Affiliate performance report (admin can filter by affiliate/campaign/plan/date)",
)
async def analytics_summary(
    user: CurrentUser, session: DBSession,
    period_days: int = Query(30, ge=1, le=365),
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    affiliate_id: Optional[str] = None,
    campaign_id: Optional[str] = None,
    plan_key: Optional[str] = None,
) -> dict:
    is_admin = (user.role.value if hasattr(user.role, "value") else user.role) == "admin"
    # Affiliates can only see their own numbers.
    if not is_admin:
        aff = await AffiliateService(session).by_user(user.id)
        if aff is None:
            raise HTTPException(status_code=403, detail="not an affiliate")
        affiliate_id = aff.id
    end = to_date or datetime.now(timezone.utc)
    start = from_date or (end - timedelta(days=period_days))
    svc = AffiliateAnalyticsService(session)
    report = await svc.report(
        period_start=start, period_end=end,
        affiliate_id=affiliate_id, campaign_id=campaign_id, plan_key=plan_key,
    )
    return report.to_dict()


@router.get(
    "/affiliate-analytics/top",
    summary="[admin] top affiliates by commission earned",
)
async def analytics_top(
    _: AdminUser, session: DBSession,
    limit: int = Query(10, ge=1, le=100),
    period_days: int = Query(30, ge=1, le=365),
) -> dict:
    rows = await AffiliateAnalyticsService(session).top_affiliates(
        limit=limit, period_days=period_days,
    )
    return {"top_affiliates": rows}


# ============================================================ FRAUD
class FraudFlagOut(BaseModel):
    id: str
    subject_type: str
    subject_id: str
    reason: str
    severity: str
    is_active: bool
    details: Optional[dict] = None
    created_at: datetime


@router.get(
    "/affiliate-fraud/admin",
    response_model=list[FraudFlagOut],
    summary="[admin] list active fraud flags",
)
async def admin_list_fraud(
    _: AdminUser, session: DBSession,
    subject_type: Optional[str] = None,
    limit: int = 100,
) -> list[FraudFlagOut]:
    rows = await FraudService(session).list_active(
        subject_type=subject_type, limit=limit,
    )
    return [
        FraudFlagOut(
            id=f.id, subject_type=f.subject_type, subject_id=f.subject_id,
            reason=f.reason, severity=f.severity.value,
            is_active=f.is_active, details=f.details, created_at=f.created_at,
        )
        for f in rows
    ]


@router.post(
    "/affiliate-fraud/admin/{flag_id}/dismiss",
    response_model=FraudFlagOut,
    summary="[admin] dismiss a fraud flag",
)
async def admin_dismiss_fraud(
    flag_id: str, admin: AdminUser, session: DBSession,
) -> FraudFlagOut:
    f = await FraudService(session).dismiss(flag_id)
    if f is None:
        raise HTTPException(status_code=404, detail="flag not found")
    await AuditService(session).record(
        action="fraud.dismiss", target_type="fraud_flag",
        target_id=f.id, actor=admin, details={},
    )
    await session.commit()
    return FraudFlagOut(
        id=f.id, subject_type=f.subject_type, subject_id=f.subject_id,
        reason=f.reason, severity=f.severity.value,
        is_active=f.is_active, details=f.details, created_at=f.created_at,
    )
