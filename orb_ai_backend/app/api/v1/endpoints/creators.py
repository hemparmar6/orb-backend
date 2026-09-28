"""Creator Hub REST endpoints.

Base path: /api/v1/creators

Creator-facing: apply, view own profile, submit strategies, view the
EXISTING coupon assigned by an admin, and view redemption counts derived
from the EXISTING coupon_redemptions table.

Admin: review/approve/reject/suspend creators, approve/reject strategies,
and associate an EXISTING coupon with an approved creator strategy.

No coupon creation, discount calculation, expiry, limit, or redemption
logic lives here — the commerce coupon system remains the single source
of truth. All privileged actions are enforced server-side via AdminUser.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.commerce import Coupon, CouponRedemption
from app.models.creator import (
    Creator,
    CreatorCouponAssignment,
    CreatorStatus,
    CreatorStrategy,
    CreatorStrategyStatus,
)
from app.models.user import User

router = APIRouter()


# --------------------------------------------------------------- schemas
class CreatorApplyIn(BaseModel):
    display_name: str = Field(..., min_length=2, max_length=128)
    bio: Optional[str] = Field(default=None, max_length=2000)
    social_links: Optional[dict[str, str]] = None


class CreatorOut(BaseModel):
    id: str
    user_id: str
    display_name: str
    bio: Optional[str] = None
    social_links: Optional[dict[str, Any]] = None
    status: str
    rejected_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, c: Creator) -> "CreatorOut":
        return cls(
            id=c.id,
            user_id=c.user_id,
            display_name=c.display_name,
            bio=c.bio,
            social_links=c.social_links,
            status=c.status.value,
            rejected_reason=c.rejected_reason,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )


class AdminCreatorOut(CreatorOut):
    user_email: Optional[str] = None
    redemption_count: int = 0


class StrategySubmitIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=128)
    short_description: str = Field(..., min_length=2, max_length=500)
    trading_style: str = Field(..., min_length=1, max_length=64)
    market: str = Field(..., min_length=1, max_length=64)
    timeframe: str = Field(..., min_length=1, max_length=32)
    entry_conditions: str = Field(..., min_length=2, max_length=2000)
    exit_conditions: str = Field(..., min_length=2, max_length=2000)
    risk_management: str = Field(..., min_length=2, max_length=2000)
    reference_link: Optional[str] = Field(default=None, max_length=500)


class CouponBrief(BaseModel):
    code: str
    status: str


class StrategyOut(BaseModel):
    id: str
    creator_id: str
    name: str
    short_description: str
    trading_style: str
    market: str
    timeframe: str
    entry_conditions: str
    exit_conditions: str
    risk_management: str
    reference_link: Optional[str] = None
    status: str
    review_note: Optional[str] = None
    assigned_coupon: Optional[CouponBrief] = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(
        cls, s: CreatorStrategy, coupon: Optional[CouponBrief] = None
    ) -> "StrategyOut":
        return cls(
            id=s.id,
            creator_id=s.creator_id,
            name=s.name,
            short_description=s.short_description,
            trading_style=s.trading_style,
            market=s.market,
            timeframe=s.timeframe,
            entry_conditions=s.entry_conditions,
            exit_conditions=s.exit_conditions,
            risk_management=s.risk_management,
            reference_link=s.reference_link,
            status=s.status.value,
            review_note=s.review_note,
            assigned_coupon=coupon,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )


class AdminStrategyOut(StrategyOut):
    creator_display_name: Optional[str] = None


class ReviewIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class AssignCouponIn(BaseModel):
    coupon_code: Optional[str] = Field(default=None, max_length=64)
    coupon_id: Optional[str] = None


class AssignmentOut(BaseModel):
    id: str
    creator_id: str
    strategy_id: str
    coupon_id: str
    coupon_code: str
    coupon_status: str
    assigned_by: Optional[str] = None
    created_at: datetime


class CreatorCouponView(BaseModel):
    assignment_id: str
    strategy_id: str
    strategy_name: str
    code: str
    status: str
    expires_at: Optional[datetime] = None
    redemption_count: int


class RedemptionSummary(BaseModel):
    redemption_count: int
    discount_cents: int
    coupons: list[dict[str, Any]]


# --------------------------------------------------------------- helpers
async def _my_creator(user_id: str, session) -> Creator:
    c = await session.scalar(select(Creator).where(Creator.user_id == user_id))
    if c is None:
        raise HTTPException(status_code=404, detail="creator profile not found")
    return c


async def _coupon_briefs_by_strategy(session, strategy_ids: list[str]) -> dict[str, CouponBrief]:
    if not strategy_ids:
        return {}
    rows = (
        await session.execute(
            select(CreatorCouponAssignment.strategy_id, Coupon.code, Coupon.status)
            .join(Coupon, Coupon.id == CreatorCouponAssignment.coupon_id)
            .where(CreatorCouponAssignment.strategy_id.in_(strategy_ids))
        )
    ).all()
    return {sid: CouponBrief(code=code, status=st.value) for sid, code, st in rows}


async def _assigned_coupon_rows(session, creator_id: str):
    """Join assignment → existing coupon → strategy for one creator."""
    return (
        await session.execute(
            select(CreatorCouponAssignment, Coupon, CreatorStrategy.name)
            .join(Coupon, Coupon.id == CreatorCouponAssignment.coupon_id)
            .join(CreatorStrategy, CreatorStrategy.id == CreatorCouponAssignment.strategy_id)
            .where(CreatorCouponAssignment.creator_id == creator_id)
            .order_by(CreatorCouponAssignment.created_at.desc())
        )
    ).all()


async def _redemption_count(session, coupon_id: str) -> int:
    """Count redemptions from the EXISTING coupon_redemptions table."""
    n = await session.scalar(
        select(func.count(CouponRedemption.id)).where(
            CouponRedemption.coupon_id == coupon_id
        )
    )
    return int(n or 0)


async def _redemption_summary(session, creator_id: str) -> RedemptionSummary:
    rows = await _assigned_coupon_rows(session, creator_id)
    coupons: list[dict[str, Any]] = []
    total = 0
    discount = 0
    for assignment, coupon, strategy_name in rows:
        count = await _redemption_count(session, coupon.id)
        disc = await session.scalar(
            select(func.coalesce(func.sum(CouponRedemption.discount_applied_cents), 0)).where(
                CouponRedemption.coupon_id == coupon.id
            )
        )
        total += count
        discount += int(disc or 0)
        coupons.append(
            {
                "code": coupon.code,
                "status": coupon.status.value,
                "strategy_name": strategy_name,
                "redemption_count": count,
            }
        )
    return RedemptionSummary(
        redemption_count=total, discount_cents=discount, coupons=coupons
    )


# --------------------------------------------------------------- creator-facing
@router.post("/apply", response_model=CreatorOut, summary="Apply as a creator")
async def apply_creator(
    payload: CreatorApplyIn, user: CurrentUser, session: DBSession
) -> CreatorOut:
    existing = await session.scalar(select(Creator).where(Creator.user_id == user.id))
    if existing is not None:
        if existing.status == CreatorStatus.REJECTED:
            # Re-application resets the review cycle.
            existing.display_name = payload.display_name
            existing.bio = payload.bio
            existing.social_links = payload.social_links
            existing.status = CreatorStatus.PENDING
            existing.rejected_reason = None
            await session.commit()
            await session.refresh(existing)
        return CreatorOut.from_model(existing)
    c = Creator(
        user_id=user.id,
        display_name=payload.display_name,
        bio=payload.bio,
        social_links=payload.social_links,
        status=CreatorStatus.PENDING,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return CreatorOut.from_model(c)


@router.get("/me", response_model=CreatorOut, summary="My creator profile")
async def my_profile(user: CurrentUser, session: DBSession) -> CreatorOut:
    return CreatorOut.from_model(await _my_creator(user.id, session))


@router.get("/me/strategies", response_model=list[StrategyOut], summary="My strategies")
async def my_strategies(user: CurrentUser, session: DBSession) -> list[StrategyOut]:
    creator = await _my_creator(user.id, session)
    rows = await session.scalars(
        select(CreatorStrategy)
        .where(CreatorStrategy.creator_id == creator.id)
        .order_by(CreatorStrategy.created_at.desc())
    )
    strategies = list(rows)
    briefs = await _coupon_briefs_by_strategy(session, [s.id for s in strategies])
    return [StrategyOut.from_model(s, briefs.get(s.id)) for s in strategies]


@router.post(
    "/me/strategies",
    response_model=StrategyOut,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a strategy (approved creators only)",
)
async def submit_strategy(
    payload: StrategySubmitIn, user: CurrentUser, session: DBSession
) -> StrategyOut:
    creator = await _my_creator(user.id, session)
    if creator.status != CreatorStatus.APPROVED:
        raise HTTPException(status_code=403, detail="creator account is not approved")
    s = CreatorStrategy(
        creator_id=creator.id,
        name=payload.name,
        short_description=payload.short_description,
        trading_style=payload.trading_style,
        market=payload.market,
        timeframe=payload.timeframe,
        entry_conditions=payload.entry_conditions,
        exit_conditions=payload.exit_conditions,
        risk_management=payload.risk_management,
        reference_link=payload.reference_link,
        status=CreatorStrategyStatus.PENDING,
    )
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return StrategyOut.from_model(s)


@router.get("/me/coupon", summary="My assigned existing coupon(s)")
async def my_coupon(user: CurrentUser, session: DBSession) -> dict[str, Any]:
    creator = await _my_creator(user.id, session)
    rows = await _assigned_coupon_rows(session, creator.id)
    coupons: list[CreatorCouponView] = []
    for assignment, coupon, strategy_name in rows:
        coupons.append(
            CreatorCouponView(
                assignment_id=assignment.id,
                strategy_id=assignment.strategy_id,
                strategy_name=strategy_name,
                code=coupon.code,
                status=coupon.status.value,
                expires_at=coupon.expires_at,
                redemption_count=await _redemption_count(session, coupon.id),
            )
        )
    return {"coupons": [c.model_dump() for c in coupons]}


@router.get("/me/redemptions", response_model=RedemptionSummary, summary="My redemption count")
async def my_redemptions(user: CurrentUser, session: DBSession) -> RedemptionSummary:
    creator = await _my_creator(user.id, session)
    return await _redemption_summary(session, creator.id)


# --------------------------------------------------------------- admin
@router.get(
    "/admin/applications",
    response_model=list[AdminCreatorOut],
    summary="[admin] list creator applications",
)
async def admin_list_creators(
    _: AdminUser,
    session: DBSession,
    status_filter: Optional[str] = Query(default=None, alias="status"),
) -> list[AdminCreatorOut]:
    q = (
        select(Creator, User.email)
        .join(User, User.id == Creator.user_id)
        .order_by(Creator.created_at.desc())
    )
    if status_filter:
        if status_filter not in {s.value for s in CreatorStatus}:
            raise HTTPException(status_code=400, detail="invalid status filter")
        q = q.where(Creator.status == CreatorStatus(status_filter))
    rows = (await session.execute(q)).all()
    out: list[AdminCreatorOut] = []
    for creator, email in rows:
        summary = await _redemption_summary(session, creator.id)
        out.append(
            AdminCreatorOut(
                **CreatorOut.from_model(creator).model_dump(),
                user_email=email,
                redemption_count=summary.redemption_count,
            )
        )
    return out


@router.get(
    "/admin/strategies",
    response_model=list[AdminStrategyOut],
    summary="[admin] list creator strategies",
)
async def admin_list_strategies(
    _: AdminUser,
    session: DBSession,
    status_filter: Optional[str] = Query(default=None, alias="status"),
) -> list[AdminStrategyOut]:
    q = (
        select(CreatorStrategy, Creator.display_name)
        .join(Creator, Creator.id == CreatorStrategy.creator_id)
        .order_by(CreatorStrategy.created_at.desc())
    )
    if status_filter:
        if status_filter not in {s.value for s in CreatorStrategyStatus}:
            raise HTTPException(status_code=400, detail="invalid status filter")
        q = q.where(CreatorStrategy.status == CreatorStrategyStatus(status_filter))
    rows = (await session.execute(q)).all()
    briefs = await _coupon_briefs_by_strategy(session, [s.id for s, _ in rows])
    return [
        AdminStrategyOut(
            **StrategyOut.from_model(s, briefs.get(s.id)).model_dump(),
            creator_display_name=display_name,
        )
        for s, display_name in rows
    ]


async def _get_creator_or_404(session, creator_id: str) -> Creator:
    c = await session.get(Creator, creator_id)
    if c is None:
        raise HTTPException(status_code=404, detail="creator not found")
    return c


@router.post(
    "/admin/{creator_id}/approve",
    response_model=CreatorOut,
    summary="[admin] approve a creator",
)
async def admin_approve_creator(
    creator_id: str, admin: AdminUser, session: DBSession
) -> CreatorOut:
    c = await _get_creator_or_404(session, creator_id)
    c.status = CreatorStatus.APPROVED
    c.approved_by = admin.id
    c.approved_at = datetime.now(timezone.utc)
    c.rejected_reason = None
    await session.commit()
    await session.refresh(c)
    return CreatorOut.from_model(c)


@router.post(
    "/admin/{creator_id}/reject",
    response_model=CreatorOut,
    summary="[admin] reject a creator",
)
async def admin_reject_creator(
    creator_id: str, payload: ReviewIn, admin: AdminUser, session: DBSession
) -> CreatorOut:
    c = await _get_creator_or_404(session, creator_id)
    c.status = CreatorStatus.REJECTED
    c.rejected_reason = payload.reason
    await session.commit()
    await session.refresh(c)
    return CreatorOut.from_model(c)


@router.post(
    "/admin/{creator_id}/suspend",
    response_model=CreatorOut,
    summary="[admin] suspend a creator",
)
async def admin_suspend_creator(
    creator_id: str, admin: AdminUser, session: DBSession
) -> CreatorOut:
    c = await _get_creator_or_404(session, creator_id)
    c.status = CreatorStatus.SUSPENDED
    await session.commit()
    await session.refresh(c)
    return CreatorOut.from_model(c)


async def _get_strategy_or_404(session, strategy_id: str) -> CreatorStrategy:
    s = await session.get(CreatorStrategy, strategy_id)
    if s is None:
        raise HTTPException(status_code=404, detail="strategy not found")
    return s


@router.post(
    "/admin/strategies/{strategy_id}/approve",
    response_model=StrategyOut,
    summary="[admin] approve a creator strategy",
)
async def admin_approve_strategy(
    strategy_id: str, admin: AdminUser, session: DBSession
) -> StrategyOut:
    s = await _get_strategy_or_404(session, strategy_id)
    s.status = CreatorStrategyStatus.APPROVED
    s.reviewed_by = admin.id
    s.reviewed_at = datetime.now(timezone.utc)
    s.review_note = None
    await session.commit()
    await session.refresh(s)
    return StrategyOut.from_model(s)


@router.post(
    "/admin/strategies/{strategy_id}/reject",
    response_model=StrategyOut,
    summary="[admin] reject a creator strategy",
)
async def admin_reject_strategy(
    strategy_id: str, payload: ReviewIn, admin: AdminUser, session: DBSession
) -> StrategyOut:
    s = await _get_strategy_or_404(session, strategy_id)
    s.status = CreatorStrategyStatus.REJECTED
    s.reviewed_by = admin.id
    s.reviewed_at = datetime.now(timezone.utc)
    s.review_note = payload.reason
    await session.commit()
    await session.refresh(s)
    return StrategyOut.from_model(s)


@router.post(
    "/admin/strategies/{strategy_id}/assign-coupon",
    response_model=AssignmentOut,
    summary="[admin] associate an EXISTING coupon with an approved creator strategy",
)
async def admin_assign_coupon(
    strategy_id: str, payload: AssignCouponIn, admin: AdminUser, session: DBSession
) -> AssignmentOut:
    s = await _get_strategy_or_404(session, strategy_id)
    creator = await session.get(Creator, s.creator_id)
    if creator is None or creator.status != CreatorStatus.APPROVED:
        raise HTTPException(status_code=400, detail="creator is not approved")
    if s.status != CreatorStrategyStatus.APPROVED:
        raise HTTPException(status_code=400, detail="strategy is not approved")

    # Look up the EXISTING coupon — never created or modified here.
    coupon: Optional[Coupon] = None
    if payload.coupon_id:
        coupon = await session.get(Coupon, payload.coupon_id)
    elif payload.coupon_code:
        coupon = await session.scalar(
            select(Coupon).where(func.upper(Coupon.code) == payload.coupon_code.strip().upper())
        )
    if coupon is None:
        raise HTTPException(status_code=404, detail="coupon not found")

    holder = await session.scalar(
        select(CreatorCouponAssignment).where(
            CreatorCouponAssignment.coupon_id == coupon.id
        )
    )
    if holder is not None and holder.strategy_id != s.id:
        raise HTTPException(status_code=409, detail="coupon already assigned to another strategy")

    assignment = await session.scalar(
        select(CreatorCouponAssignment).where(
            CreatorCouponAssignment.strategy_id == s.id
        )
    )
    if assignment is None:
        assignment = CreatorCouponAssignment(
            creator_id=creator.id,
            strategy_id=s.id,
            coupon_id=coupon.id,
            assigned_by=admin.id,
        )
        session.add(assignment)
    else:
        assignment.coupon_id = coupon.id
        assignment.assigned_by = admin.id
    await session.commit()
    await session.refresh(assignment)
    return AssignmentOut(
        id=assignment.id,
        creator_id=assignment.creator_id,
        strategy_id=assignment.strategy_id,
        coupon_id=assignment.coupon_id,
        coupon_code=coupon.code,
        coupon_status=coupon.status.value,
        assigned_by=assignment.assigned_by,
        created_at=assignment.created_at,
    )
