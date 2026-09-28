"""Campaign REST endpoints (Phase 3).

Base path: /api/v1/campaigns
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.affiliate import Affiliate, Campaign
from app.services.affiliate.affiliate_service import AffiliateService

router = APIRouter()


class CampaignOut(BaseModel):
    id: str
    affiliate_id: Optional[str] = None
    name: str
    slug: str
    description: Optional[str] = None
    landing_url: Optional[str] = None
    is_active: bool
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    commission_rate_pct: Optional[int] = None
    click_count: int
    signup_count: int
    conversion_count: int
    revenue_cents: int

    @classmethod
    def from_model(cls, c: Campaign) -> "CampaignOut":
        return cls(
            id=c.id, affiliate_id=c.affiliate_id, name=c.name,
            slug=c.slug, description=c.description,
            landing_url=c.landing_url, is_active=c.is_active,
            start_at=c.start_at, end_at=c.end_at,
            commission_rate_pct=c.commission_rate_pct,
            click_count=c.click_count, signup_count=c.signup_count,
            conversion_count=c.conversion_count,
            revenue_cents=c.revenue_cents,
        )


class CampaignIn(BaseModel):
    name: str = Field(..., max_length=128)
    slug: str = Field(..., max_length=96, pattern=r"^[a-z0-9][a-z0-9\-_]*$")
    description: Optional[str] = None
    landing_url: Optional[str] = Field(default=None, max_length=500)
    is_active: bool = True
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    commission_rate_pct: Optional[int] = Field(default=None, ge=0, le=100)


class CampaignUpdateIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    landing_url: Optional[str] = None
    is_active: Optional[bool] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    commission_rate_pct: Optional[int] = Field(default=None, ge=0, le=100)


# ---------------------------------------------------------------- self
@router.get("/", response_model=list[CampaignOut], summary="Public campaign listing (active only)")
async def list_public(session: DBSession) -> list[CampaignOut]:
    rows = await session.scalars(
        select(Campaign).where(Campaign.is_active.is_(True))
        .order_by(Campaign.created_at.desc())
    )
    return [CampaignOut.from_model(c) for c in rows]


@router.get("/me", response_model=list[CampaignOut], summary="Current affiliate's campaigns")
async def list_mine(user: CurrentUser, session: DBSession) -> list[CampaignOut]:
    aff = await AffiliateService(session).by_user(user.id)
    if aff is None:
        return []
    rows = await session.scalars(
        select(Campaign).where(Campaign.affiliate_id == aff.id)
        .order_by(Campaign.created_at.desc())
    )
    return [CampaignOut.from_model(c) for c in rows]


@router.post(
    "/", response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a campaign (owned by the calling affiliate)",
)
async def create_own(
    payload: CampaignIn, user: CurrentUser, session: DBSession,
) -> CampaignOut:
    aff = await AffiliateService(session).by_user(user.id)
    if aff is None:
        raise HTTPException(status_code=403, detail="not an affiliate")
    existing = await session.scalar(select(Campaign).where(Campaign.slug == payload.slug))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"slug {payload.slug!r} already exists")
    # Only admins can override commission rate on their own campaigns.
    rate = payload.commission_rate_pct
    if rate is not None and (user.role.value if hasattr(user.role, "value") else user.role) != "admin":
        rate = None
    c = Campaign(
        affiliate_id=aff.id, name=payload.name, slug=payload.slug,
        description=payload.description, landing_url=payload.landing_url,
        is_active=payload.is_active, start_at=payload.start_at, end_at=payload.end_at,
        commission_rate_pct=rate,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return CampaignOut.from_model(c)


@router.patch("/{campaign_id}", response_model=CampaignOut, summary="Update own campaign")
async def update_own(
    campaign_id: str, payload: CampaignUpdateIn,
    user: CurrentUser, session: DBSession,
) -> CampaignOut:
    c = await session.get(Campaign, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    aff = await AffiliateService(session).by_user(user.id)
    is_admin = (user.role.value if hasattr(user.role, "value") else user.role) == "admin"
    if not is_admin and (aff is None or c.affiliate_id != aff.id):
        raise HTTPException(status_code=403, detail="not owner")
    data = payload.model_dump(exclude_none=True)
    if "commission_rate_pct" in data and not is_admin:
        data.pop("commission_rate_pct")
    for k, v in data.items():
        setattr(c, k, v)
    await session.commit()
    return CampaignOut.from_model(c)


# ---------------------------------------------------------------- admin
@router.post(
    "/admin", response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    summary="[admin] Create a global (affiliate-agnostic) campaign",
)
async def admin_create_global(
    payload: CampaignIn, _: AdminUser, session: DBSession,
) -> CampaignOut:
    existing = await session.scalar(select(Campaign).where(Campaign.slug == payload.slug))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"slug {payload.slug!r} already exists")
    c = Campaign(
        affiliate_id=None, name=payload.name, slug=payload.slug,
        description=payload.description, landing_url=payload.landing_url,
        is_active=payload.is_active, start_at=payload.start_at, end_at=payload.end_at,
        commission_rate_pct=payload.commission_rate_pct,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return CampaignOut.from_model(c)


@router.get(
    "/admin", response_model=list[CampaignOut],
    summary="[admin] list all campaigns",
)
async def admin_list_all(
    _: AdminUser, session: DBSession, limit: int = 200,
) -> list[CampaignOut]:
    rows = await session.scalars(
        select(Campaign).order_by(Campaign.created_at.desc()).limit(limit)
    )
    return [CampaignOut.from_model(c) for c in rows]
