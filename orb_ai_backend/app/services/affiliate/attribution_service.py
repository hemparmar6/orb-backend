"""AttributionService — track clicks, attribute users, log funnel events.

Attribution rules (all admin-configurable via AffiliateProgram):
* Windows: click / trial / paid attribution days.
* Model: first_touch (never overwrite) vs last_touch (overwrite within window).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.affiliate import (
    Affiliate,
    AffiliateStatus,
    AttributionModel,
    Campaign,
    ReferralAttribution,
    ReferralClick,
    ReferralEvent,
    ReferralEventType,
)
from app.services.affiliate.program_service import ProgramService


def _naive_to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class AttributionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------- clicks

    async def track_click(
        self,
        *,
        affiliate_code: str,
        campaign_slug: Optional[str] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        device_fingerprint: Optional[str] = None,
        referrer: Optional[str] = None,
        landing_url: Optional[str] = None,
        cookie_id: Optional[str] = None,
        utm_source: Optional[str] = None,
        utm_medium: Optional[str] = None,
        utm_campaign: Optional[str] = None,
    ) -> tuple[Optional[ReferralClick], Optional[Affiliate], Optional[Campaign]]:
        affiliate = await self.session.scalar(
            select(Affiliate).where(Affiliate.code == affiliate_code.upper())
        )
        if affiliate is None or affiliate.status != AffiliateStatus.APPROVED:
            return None, None, None

        campaign = None
        if campaign_slug:
            campaign = await self.session.scalar(
                select(Campaign).where(Campaign.slug == campaign_slug)
            )

        click = ReferralClick(
            affiliate_id=affiliate.id,
            campaign_id=(campaign.id if campaign else None),
            ip=ip, user_agent=user_agent,
            device_fingerprint=device_fingerprint,
            referrer=referrer, landing_url=landing_url,
            cookie_id=cookie_id,
            utm_source=utm_source, utm_medium=utm_medium, utm_campaign=utm_campaign,
        )
        self.session.add(click)
        affiliate.total_clicks = (affiliate.total_clicks or 0) + 1
        if campaign is not None:
            campaign.click_count = (campaign.click_count or 0) + 1

        await self.session.flush()

        # Log a "click" ReferralEvent (no user yet).
        self.session.add(ReferralEvent(
            affiliate_id=affiliate.id,
            campaign_id=(campaign.id if campaign else None),
            attribution_id=None,
            user_id=None,
            event_type=ReferralEventType.CLICK,
            event_metadata={"click_id": click.id, "cookie_id": cookie_id},
        ))
        await self.session.flush()
        return click, affiliate, campaign

    # ---------------------------------------------------------- attribute users

    async def attribute_user(
        self,
        *,
        user_id: str,
        affiliate_code: Optional[str] = None,
        cookie_id: Optional[str] = None,
        click: Optional[ReferralClick] = None,
        now: Optional[datetime] = None,
    ) -> Optional[ReferralAttribution]:
        """Attach a user to an affiliate.

        Resolution order for the referring click:
        1. Explicit click if provided.
        2. Explicit affiliate_code (find affiliate).
        3. cookie_id -> latest un-expired ReferralClick.

        Respects the configured attribution model + window.
        """
        program = await ProgramService(self.session).get()
        now = _naive_to_utc(now) or datetime.now(timezone.utc)

        # Resolve the referring click / affiliate.
        affiliate: Optional[Affiliate] = None
        if click is not None:
            affiliate = await self.session.get(Affiliate, click.affiliate_id)
        elif affiliate_code:
            affiliate = await self.session.scalar(
                select(Affiliate).where(Affiliate.code == affiliate_code.upper())
            )
        elif cookie_id:
            recent = await self.session.scalar(
                select(ReferralClick)
                .where(ReferralClick.cookie_id == cookie_id)
                .order_by(ReferralClick.created_at.desc())
                .limit(1)
            )
            if recent is not None:
                click = recent
                affiliate = await self.session.get(Affiliate, recent.affiliate_id)

        if affiliate is None or affiliate.status != AffiliateStatus.APPROVED:
            return None

        # Self-referral guard.
        if program.block_self_referral and affiliate.user_id == user_id:
            return None

        # Window check.
        if click is not None:
            click_at = _naive_to_utc(click.created_at)
            if click_at:
                cutoff = click_at + timedelta(days=program.click_attribution_days)
                if now > cutoff:
                    return None

        existing = await self.session.scalar(
            select(ReferralAttribution).where(ReferralAttribution.user_id == user_id)
        )

        if existing is not None:
            if program.attribution_model == AttributionModel.FIRST_TOUCH:
                return existing  # never overwrite
            # Last touch: overwrite if new click/affiliate is later.
            existing.affiliate_id = affiliate.id
            existing.campaign_id = (click.campaign_id if click else existing.campaign_id)
            existing.click_id = (click.id if click else existing.click_id)
            existing.attributed_at = now
            existing.expires_at = now + timedelta(days=program.paid_attribution_days)
            existing.model = AttributionModel.LAST_TOUCH
            await self.session.flush()
            return existing

        attribution = ReferralAttribution(
            user_id=user_id,
            affiliate_id=affiliate.id,
            campaign_id=(click.campaign_id if click else None),
            click_id=(click.id if click else None),
            model=program.attribution_model,
            attributed_at=now,
            expires_at=now + timedelta(days=program.paid_attribution_days),
        )
        self.session.add(attribution)
        affiliate.total_signups = (affiliate.total_signups or 0) + 1
        if click is not None and click.campaign_id:
            camp = await self.session.get(Campaign, click.campaign_id)
            if camp is not None:
                camp.signup_count = (camp.signup_count or 0) + 1
        await self.session.flush()
        return attribution

    # ---------------------------------------------------------- funnel events

    async def log_event(
        self,
        *,
        user_id: Optional[str],
        event_type: ReferralEventType,
        revenue_cents: int = 0,
        metadata: Optional[dict] = None,
    ) -> Optional[ReferralEvent]:
        """Record a funnel event, associating it with the user's attribution."""
        attribution = None
        if user_id:
            attribution = await self.session.scalar(
                select(ReferralAttribution).where(ReferralAttribution.user_id == user_id)
            )
        if attribution is None and user_id and event_type not in {
            ReferralEventType.CLICK, ReferralEventType.LANDING,
        }:
            # Nothing to attribute to.
            return None
        ev = ReferralEvent(
            affiliate_id=(attribution.affiliate_id if attribution else None),
            campaign_id=(attribution.campaign_id if attribution else None),
            attribution_id=(attribution.id if attribution else None),
            user_id=user_id,
            event_type=event_type,
            revenue_cents=revenue_cents,
            event_metadata=metadata,
        )
        self.session.add(ev)
        await self.session.flush()
        return ev

    async def get_attribution(self, user_id: str) -> Optional[ReferralAttribution]:
        return await self.session.scalar(
            select(ReferralAttribution).where(ReferralAttribution.user_id == user_id)
        )
