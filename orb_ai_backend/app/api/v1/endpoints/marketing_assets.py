"""Marketing asset REST endpoints (Phase 3).

Base path: /api/v1/marketing-assets
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.affiliate import AssetType, MarketingAsset
from app.services.affiliate.affiliate_service import AffiliateService

router = APIRouter()


class AssetOut(BaseModel):
    id: str
    affiliate_id: Optional[str] = None
    campaign_id: Optional[str] = None
    name: str
    asset_type: str
    content_url: Optional[str] = None
    body: Optional[str] = None
    mime_type: Optional[str] = None
    dimensions: Optional[str] = None
    tags: Optional[list] = None
    is_active: bool

    @classmethod
    def from_model(cls, a: MarketingAsset) -> "AssetOut":
        return cls(
            id=a.id, affiliate_id=a.affiliate_id, campaign_id=a.campaign_id,
            name=a.name, asset_type=a.asset_type.value,
            content_url=a.content_url, body=a.body,
            mime_type=a.mime_type, dimensions=a.dimensions,
            tags=a.tags, is_active=a.is_active,
        )


class AssetIn(BaseModel):
    campaign_id: Optional[str] = None
    name: str = Field(..., max_length=255)
    asset_type: str = "image"
    content_url: Optional[str] = Field(default=None, max_length=2000)
    body: Optional[str] = Field(default=None, max_length=4000)
    mime_type: Optional[str] = None
    dimensions: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: bool = True


class AssetUpdateIn(BaseModel):
    name: Optional[str] = None
    content_url: Optional[str] = None
    body: Optional[str] = None
    mime_type: Optional[str] = None
    dimensions: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None


def _validate_type(t: str) -> AssetType:
    try:
        return AssetType(t)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid asset_type; must be one of {[t.value for t in AssetType]}")


@router.get("/", response_model=list[AssetOut], summary="Public marketing assets")
async def list_public(session: DBSession) -> list[AssetOut]:
    rows = await session.scalars(
        select(MarketingAsset).where(
            MarketingAsset.is_active.is_(True),
            MarketingAsset.affiliate_id.is_(None),  # global assets only
        )
    )
    return [AssetOut.from_model(a) for a in rows]


@router.get("/me", response_model=list[AssetOut], summary="Affiliate's assets + globals")
async def list_mine(user: CurrentUser, session: DBSession) -> list[AssetOut]:
    aff = await AffiliateService(session).by_user(user.id)
    stmt = select(MarketingAsset).where(MarketingAsset.is_active.is_(True))
    if aff is None:
        stmt = stmt.where(MarketingAsset.affiliate_id.is_(None))
    else:
        stmt = stmt.where(or_(
            MarketingAsset.affiliate_id.is_(None),
            MarketingAsset.affiliate_id == aff.id,
        ))
    rows = await session.scalars(stmt)
    return [AssetOut.from_model(a) for a in rows]


@router.post(
    "/",
    response_model=AssetOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an asset (owned by the calling affiliate)",
)
async def create_own(
    payload: AssetIn, user: CurrentUser, session: DBSession,
) -> AssetOut:
    aff = await AffiliateService(session).by_user(user.id)
    if aff is None:
        raise HTTPException(status_code=403, detail="not an affiliate")
    a = MarketingAsset(
        affiliate_id=aff.id, campaign_id=payload.campaign_id, name=payload.name,
        asset_type=_validate_type(payload.asset_type),
        content_url=payload.content_url, body=payload.body,
        mime_type=payload.mime_type, dimensions=payload.dimensions,
        tags=payload.tags, is_active=payload.is_active,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return AssetOut.from_model(a)


@router.post(
    "/admin",
    response_model=AssetOut,
    status_code=status.HTTP_201_CREATED,
    summary="[admin] Create a global marketing asset",
)
async def admin_create_global(
    payload: AssetIn, _: AdminUser, session: DBSession,
) -> AssetOut:
    a = MarketingAsset(
        affiliate_id=None, campaign_id=payload.campaign_id, name=payload.name,
        asset_type=_validate_type(payload.asset_type),
        content_url=payload.content_url, body=payload.body,
        mime_type=payload.mime_type, dimensions=payload.dimensions,
        tags=payload.tags, is_active=payload.is_active,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return AssetOut.from_model(a)


@router.patch("/{asset_id}", response_model=AssetOut, summary="Update an asset")
async def update_asset(
    asset_id: str, payload: AssetUpdateIn,
    user: CurrentUser, session: DBSession,
) -> AssetOut:
    a = await session.get(MarketingAsset, asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail="asset not found")
    is_admin = (user.role.value if hasattr(user.role, "value") else user.role) == "admin"
    if not is_admin:
        aff = await AffiliateService(session).by_user(user.id)
        if aff is None or a.affiliate_id != aff.id:
            raise HTTPException(status_code=403, detail="not owner")
    data = payload.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(a, k, v)
    await session.commit()
    return AssetOut.from_model(a)


@router.delete("/{asset_id}", response_model=AssetOut, summary="Soft-delete an asset")
async def delete_asset(
    asset_id: str, user: CurrentUser, session: DBSession,
) -> AssetOut:
    a = await session.get(MarketingAsset, asset_id)
    if a is None:
        raise HTTPException(status_code=404, detail="asset not found")
    is_admin = (user.role.value if hasattr(user.role, "value") else user.role) == "admin"
    if not is_admin:
        aff = await AffiliateService(session).by_user(user.id)
        if aff is None or a.affiliate_id != aff.id:
            raise HTTPException(status_code=403, detail="not owner")
    a.is_active = False
    await session.commit()
    return AssetOut.from_model(a)
