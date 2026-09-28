"""MarketplaceService — strategy listings + purchase orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import (
    MarketplaceListing,
    Order,
    OrderKind,
    OrderStatus,
    StrategyPurchase,
)
from app.models.strategy_catalog import StrategyCatalog
from app.models.user import User
from app.services.commerce.order_service import OrderQuote, OrderService


class MarketplaceError(Exception):
    pass


class ListingNotFound(MarketplaceError):
    pass


class ListingInactive(MarketplaceError):
    pass


class AlreadyPurchased(MarketplaceError):
    pass


@dataclass
class MarketplaceView:
    """Combined listing + strategy metadata view."""
    listing: MarketplaceListing
    strategy: Optional[StrategyCatalog]


class MarketplaceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderService(session)

    # ------------------------------------------------------------ read

    async def list_active(
        self, *, include_inactive: bool = False, featured_only: bool = False,
    ) -> list[MarketplaceView]:
        stmt = select(MarketplaceListing)
        if not include_inactive:
            stmt = stmt.where(MarketplaceListing.is_active.is_(True))
        if featured_only:
            stmt = stmt.where(MarketplaceListing.is_featured.is_(True))
        stmt = stmt.order_by(
            MarketplaceListing.display_order.asc(),
            MarketplaceListing.created_at.asc(),
        )
        listings = list(await self.session.scalars(stmt))
        keys = [l.strategy_key for l in listings]
        strategies: dict[str, StrategyCatalog] = {}
        if keys:
            rows = await self.session.scalars(
                select(StrategyCatalog).where(StrategyCatalog.key.in_(keys))
            )
            for s in rows:
                strategies[s.key] = s
        return [MarketplaceView(listing=l, strategy=strategies.get(l.strategy_key)) for l in listings]

    async def get_by_key(self, key: str) -> MarketplaceView:
        listing = await self.session.scalar(
            select(MarketplaceListing).where(MarketplaceListing.strategy_key == key)
        )
        if listing is None:
            raise ListingNotFound(key)
        strategy = await self.session.scalar(
            select(StrategyCatalog).where(StrategyCatalog.key == key)
        )
        return MarketplaceView(listing=listing, strategy=strategy)

    async def user_owns(self, user_id: str, strategy_key: str) -> bool:
        now = datetime.now(timezone.utc)
        stmt = select(StrategyPurchase).where(
            StrategyPurchase.user_id == user_id,
            StrategyPurchase.strategy_key == strategy_key,
        )
        rows = await self.session.scalars(stmt)
        for p in rows:
            if p.is_lifetime:
                return True
            if p.expires_at is None or p.expires_at > now:
                return True
        return False

    async def user_purchases(self, user_id: str) -> list[StrategyPurchase]:
        rows = await self.session.scalars(
            select(StrategyPurchase)
            .where(StrategyPurchase.user_id == user_id)
            .order_by(StrategyPurchase.granted_at.desc())
        )
        return list(rows)

    # ------------------------------------------------------------ write

    async def upsert_listing(
        self,
        *,
        strategy_key: str,
        price_cents: int,
        currency: str = "INR",
        tagline: Optional[str] = None,
        description_md: Optional[str] = None,
        is_active: bool = True,
        is_featured: bool = False,
        display_order: int = 0,
        allow_trial: bool = False,
        trial_days: int = 0,
        preview_image_url: Optional[str] = None,
        features: Optional[list] = None,
    ) -> MarketplaceListing:
        existing = await self.session.scalar(
            select(MarketplaceListing).where(MarketplaceListing.strategy_key == strategy_key)
        )
        if existing is None:
            existing = MarketplaceListing(strategy_key=strategy_key)
            self.session.add(existing)
        existing.price_cents = price_cents
        existing.currency = currency
        existing.tagline = tagline
        existing.description_md = description_md
        existing.is_active = is_active
        existing.is_featured = is_featured
        existing.display_order = display_order
        existing.allow_trial = allow_trial
        existing.trial_days = trial_days
        existing.preview_image_url = preview_image_url
        existing.features = features
        await self.session.flush()
        return existing

    async def purchase(
        self,
        *,
        user: User,
        strategy_key: str,
        coupon_codes: Optional[list[str]] = None,
        use_wallet: bool = True,
        provider_name: str = "mock",
    ) -> tuple[Order, OrderQuote, MarketplaceListing]:
        """Purchase a marketplace strategy.

        If the total after coupons + wallet is 0, the order is
        auto-marked PAID (no gateway hop needed). Otherwise it
        stays PENDING and the caller drives the payment provider.
        """
        view = await self.get_by_key(strategy_key)
        if not view.listing.is_active:
            raise ListingInactive(strategy_key)
        if await self.user_owns(user.id, strategy_key):
            raise AlreadyPurchased(strategy_key)

        order, quote = await self.orders.create(
            user=user,
            kind=OrderKind.STRATEGY,
            target_ref=strategy_key,
            subtotal_cents=view.listing.price_cents,
            currency=view.listing.currency,
            provider_name=provider_name,
            coupon_codes=coupon_codes,
            use_wallet=use_wallet,
            metadata={"strategy_key": strategy_key},
        )
        # Zero-total shortcut: no gateway needed.
        if quote.total_cents == 0:
            await self.orders.mark_paid(order)
            await self._grant_ownership(user_id=user.id, listing=view.listing, order=order)
        return order, quote, view.listing

    async def confirm_gateway_paid(
        self,
        order: Order,
        *,
        payment_reference: Optional[str] = None,
    ) -> StrategyPurchase:
        """Called after external payment success to grant ownership."""
        assert order.kind == OrderKind.STRATEGY
        assert order.target_ref
        listing = await self.session.scalar(
            select(MarketplaceListing).where(
                MarketplaceListing.strategy_key == order.target_ref
            )
        )
        assert listing is not None
        await self.orders.mark_paid(order, payment_reference=payment_reference)
        return await self._grant_ownership(
            user_id=order.user_id, listing=listing, order=order,
        )

    async def _grant_ownership(
        self,
        *,
        user_id: str,
        listing: MarketplaceListing,
        order: Order,
    ) -> StrategyPurchase:
        purchase = StrategyPurchase(
            user_id=user_id,
            strategy_key=listing.strategy_key,
            order_id=order.id,
            price_cents=order.total_cents + order.discount_cents + order.wallet_debit_cents,
            is_lifetime=True,
            expires_at=None,
            granted_at=datetime.now(timezone.utc),
        )
        self.session.add(purchase)
        listing.total_purchases = (listing.total_purchases or 0) + 1
        listing.total_revenue_cents = (listing.total_revenue_cents or 0) + order.total_cents
        await self.session.flush()
        return purchase
