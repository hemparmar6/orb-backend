"""Referral tracking + campaigns + marketing assets REST endpoints.

Base path: /api/v1/referrals   /api/v1/campaigns   /api/v1/marketing-assets
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.models.affiliate import (
    Affiliate,
    AssetType,
    Campaign,
    MarketingAsset,
    ReferralEventType,
    FraudFlag,
)
from app.services.affiliate.affiliate_service import AffiliateService
from app.services.affiliate.attribution_service import AttributionService
from app.services.affiliate.fraud_service import FraudService

router = APIRouter()


# ============================================================ TRACK
class TrackClickIn(BaseModel):
    code: str = Field(..., max_length=32)
    campaign_slug: Optional[str] = None
    landing_url: Optional[str] = None
    cookie_id: Optional[str] = None
    device_fingerprint: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    referrer: Optional[str] = None


class TrackClickOut(BaseModel):
    tracked: bool
    click_id: Optional[str] = None
    affiliate_code: Optional[str] = None
    campaign_slug: Optional[str] = None
    landing_redirect_url: Optional[str] = None


@router.post("/track/click", response_model=TrackClickOut, summary="Track a referral click (public)")
async def track_click(
    payload: TrackClickIn,
    request: Request,
    session: DBSession,
) -> TrackClickOut:
    """Called by the landing page after the user clicks a referral URL."""
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    svc = AttributionService(session)
    click, affiliate, campaign = await svc.track_click(
        affiliate_code=payload.code,
        campaign_slug=payload.campaign_slug,
        ip=ip, user_agent=ua,
        device_fingerprint=payload.device_fingerprint,
        referrer=payload.referrer,
        landing_url=payload.landing_url,
        cookie_id=payload.cookie_id,
        utm_source=payload.utm_source, utm_medium=payload.utm_medium,
        utm_campaign=payload.utm_campaign,
    )
    if click is None or affiliate is None:
        await session.commit()
        return TrackClickOut(tracked=False)

    # Foundational fraud checks (side effects only — never block the click).
    fraud = FraudService(session)
    if ip:
        await fraud.check_duplicate_ip(ip=ip, affiliate_id=affiliate.id)

    landing = (campaign.landing_url if campaign and campaign.landing_url else payload.landing_url) or "/"
    await session.commit()
    return TrackClickOut(
        tracked=True,
        click_id=click.id,
        affiliate_code=affiliate.code,
        campaign_slug=(campaign.slug if campaign else None),
        landing_redirect_url=landing,
    )


class AttributeIn(BaseModel):
    affiliate_code: Optional[str] = None
    cookie_id: Optional[str] = None


class AttributionOut(BaseModel):
    attributed: bool
    affiliate_id: Optional[str] = None
    campaign_id: Optional[str] = None
    model: Optional[str] = None
    attributed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


@router.post(
    "/attribute",
    response_model=AttributionOut,
    summary="Attach the current user to an affiliate/campaign (called at signup/login)",
)
async def attribute_current_user(
    payload: AttributeIn,
    user: CurrentUser,
    session: DBSession,
) -> AttributionOut:
    svc = AttributionService(session)
    attribution = await svc.attribute_user(
        user_id=user.id,
        affiliate_code=payload.affiliate_code,
        cookie_id=payload.cookie_id,
    )
    if attribution is None:
        await session.commit()
        return AttributionOut(attributed=False)
    # Self-referral fraud flag (in addition to being blocked in AttributionService).
    fraud = FraudService(session)
    affiliate = await session.get(Affiliate, attribution.affiliate_id)
    if affiliate is not None:
        await fraud.check_self_referral(affiliate=affiliate, user_id=user.id)

    # Log an implicit REGISTRATION event.
    await svc.log_event(
        user_id=user.id, event_type=ReferralEventType.REGISTRATION,
    )
    await session.commit()
    return AttributionOut(
        attributed=True,
        affiliate_id=attribution.affiliate_id,
        campaign_id=attribution.campaign_id,
        model=attribution.model.value,
        attributed_at=attribution.attributed_at,
        expires_at=attribution.expires_at,
    )


class EventIn(BaseModel):
    event_type: str
    revenue_cents: int = 0
    metadata: Optional[dict] = None


@router.post(
    "/events",
    summary="Log a funnel event for the current user",
)
async def log_event(
    payload: EventIn, user: CurrentUser, session: DBSession,
) -> dict:
    try:
        et = ReferralEventType(payload.event_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid event_type")
    ev = await AttributionService(session).log_event(
        user_id=user.id, event_type=et,
        revenue_cents=payload.revenue_cents,
        metadata=payload.metadata,
    )
    await session.commit()
    return {"logged": ev is not None, "event_id": (ev.id if ev else None)}


@router.get(
    "/attribution/me",
    response_model=AttributionOut,
    summary="Get the current user's attribution (if any)",
)
async def my_attribution(user: CurrentUser, session: DBSession) -> AttributionOut:
    a = await AttributionService(session).get_attribution(user.id)
    if a is None:
        return AttributionOut(attributed=False)
    return AttributionOut(
        attributed=True,
        affiliate_id=a.affiliate_id, campaign_id=a.campaign_id,
        model=a.model.value, attributed_at=a.attributed_at,
        expires_at=a.expires_at,
    )
