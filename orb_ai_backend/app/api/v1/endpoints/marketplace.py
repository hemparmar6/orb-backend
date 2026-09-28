"""Marketplace REST endpoints (v1.1.0 Phase 2).

Base path: /api/v1/marketplace
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.services.commerce.marketplace_service import (
    AlreadyPurchased,
    ListingInactive,
    ListingNotFound,
    MarketplaceService,
)

router = APIRouter()


# ---------------------------------------------------------------- schemas
class ListingOut(BaseModel):
    id: str
    strategy_key: str
    strategy_name: Optional[str] = None
    strategy_category: Optional[str] = None
    strategy_status: Optional[str] = None
    tagline: Optional[str] = None
    description_md: Optional[str] = None
    price_cents: int
    currency: str
    is_active: bool
    is_featured: bool
    display_order: int
    allow_trial: bool
    trial_days: int
    preview_image_url: Optional[str] = None
    features: Optional[list] = None
    total_purchases: int
    total_revenue_cents: int


class ListingUpsertIn(BaseModel):
    strategy_key: str = Field(..., max_length=64)
    price_cents: int = Field(ge=0)
    currency: str = "INR"
    tagline: Optional[str] = None
    description_md: Optional[str] = None
    is_active: bool = True
    is_featured: bool = False
    display_order: int = 0
    allow_trial: bool = False
    trial_days: int = Field(ge=0, default=0)
    preview_image_url: Optional[str] = None
    features: Optional[list] = None


class PurchaseIn(BaseModel):
    coupon_codes: Optional[list[str]] = None
    use_wallet: bool = True


class PurchaseOut(BaseModel):
    order_id: str
    order_status: str
    subtotal_cents: int
    discount_cents: int
    wallet_debit_cents: int
    total_cents: int
    currency: str
    provider: str
    checkout_url: Optional[str] = None
    activated: bool
    strategy_key: str


class StrategyPurchaseOut(BaseModel):
    id: str
    strategy_key: str
    price_cents: int
    is_lifetime: bool
    expires_at: Optional[str] = None
    granted_at: str


def _view_to_out(v) -> ListingOut:
    l = v.listing
    s = v.strategy
    return ListingOut(
        id=l.id,
        strategy_key=l.strategy_key,
        strategy_name=(s.name if s else None),
        strategy_category=(s.category if s else None),
        strategy_status=(s.status if s else None),
        tagline=l.tagline,
        description_md=l.description_md,
        price_cents=l.price_cents,
        currency=l.currency,
        is_active=l.is_active,
        is_featured=l.is_featured,
        display_order=l.display_order,
        allow_trial=l.allow_trial,
        trial_days=l.trial_days,
        preview_image_url=l.preview_image_url,
        features=l.features,
        total_purchases=l.total_purchases,
        total_revenue_cents=l.total_revenue_cents,
    )


# ---------------------------------------------------------------- public reads
@router.get("/", response_model=list[ListingOut], summary="List active marketplace listings")
async def list_listings(
    session: DBSession,
    featured_only: bool = False,
) -> list[ListingOut]:
    svc = MarketplaceService(session)
    views = await svc.list_active(featured_only=featured_only)
    return [_view_to_out(v) for v in views]


@router.get("/{key}", response_model=ListingOut, summary="Get a listing by strategy key")
async def get_listing(key: str, session: DBSession) -> ListingOut:
    svc = MarketplaceService(session)
    try:
        view = await svc.get_by_key(key)
    except ListingNotFound:
        raise HTTPException(status_code=404, detail=f"listing {key!r} not found")
    return _view_to_out(view)


@router.get(
    "/me/purchases",
    response_model=list[StrategyPurchaseOut],
    summary="Current user's strategy purchases",
)
async def my_purchases(user: CurrentUser, session: DBSession) -> list[StrategyPurchaseOut]:
    svc = MarketplaceService(session)
    rows = await svc.user_purchases(user.id)
    return [
        StrategyPurchaseOut(
            id=p.id,
            strategy_key=p.strategy_key,
            price_cents=p.price_cents,
            is_lifetime=p.is_lifetime,
            expires_at=(p.expires_at.isoformat() if p.expires_at else None),
            granted_at=p.granted_at.isoformat(),
        )
        for p in rows
    ]


# ---------------------------------------------------------------- purchase
# ORB AI 2.0 — Milestone 1: Strategy purchase is REMOVED. Users create and
# own their own strategies via /api/v1/my-strategies. The route is kept but
# returns HTTP 410 Gone so existing mobile clients get a clear signal.
@router.post(
    "/{key}/purchase",
    response_model=PurchaseOut,
    summary="[DECOMMISSIONED] Strategy purchase — removed in ORB AI 2.0",
    deprecated=True,
    responses={410: {"description": "Strategy marketplace decommissioned in ORB AI 2.0"}},
)
async def purchase(
    key: str,
    payload: PurchaseIn,
    user: CurrentUser,
    session: DBSession,
) -> PurchaseOut:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "marketplace_removed",
            "message": (
                "Strategy purchases have been removed in ORB AI 2.0. "
                "Users now create and own their own strategies. "
                "Use POST /api/v1/my-strategies to create one, or "
                "GET /api/v1/my-strategies/templates for starter templates."
            ),
            "replaced_by": "/api/v1/my-strategies",
        },
    )


# ---------------------------------------------------------------- admin
@router.post(
    "/admin/listings",
    response_model=ListingOut,
    status_code=status.HTTP_201_CREATED,
    summary="[admin] Create or update a marketplace listing",
)
async def upsert_listing(
    payload: ListingUpsertIn,
    _: AdminUser,
    session: DBSession,
) -> ListingOut:
    svc = MarketplaceService(session)
    listing = await svc.upsert_listing(**payload.model_dump())
    await session.commit()
    view = await svc.get_by_key(listing.strategy_key)
    return _view_to_out(view)


@router.get(
    "/admin/listings",
    response_model=list[ListingOut],
    summary="[admin] List all listings (incl. inactive)",
)
async def admin_list_all(
    _: AdminUser, session: DBSession,
) -> list[ListingOut]:
    svc = MarketplaceService(session)
    views = await svc.list_active(include_inactive=True)
    return [_view_to_out(v) for v in views]
