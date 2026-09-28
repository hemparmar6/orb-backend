"""StrategyAnalyticsService — per-strategy purchase/revenue analytics.

Complements RevenueDashboardService with drill-down data. Used by the
admin dashboard's Strategy Analytics screen.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.commerce import (
    MarketplaceListing,
    StrategyPurchase,
)
from app.models.strategy_catalog import StrategyCatalog


@dataclass
class StrategyAnalyticsRow:
    strategy_key: str
    strategy_name: Optional[str]
    category: Optional[str]
    status: Optional[str]
    total_purchases: int
    revenue_cents: int
    is_marketplace_listed: bool
    is_featured: bool
    price_cents: int


@dataclass
class StrategyAnalyticsReport:
    period_start: datetime
    period_end: datetime
    total_purchases: int
    total_revenue_cents: int
    rows: list[StrategyAnalyticsRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_purchases": self.total_purchases,
            "total_revenue_cents": self.total_revenue_cents,
            "rows": [asdict(r) for r in self.rows],
        }


class StrategyAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def report(
        self,
        *,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
        limit: int = 25,
    ) -> StrategyAnalyticsReport:
        now = datetime.now(timezone.utc)
        end = period_end or now
        start = period_start or (end - timedelta(days=30))

        # 1. Purchase aggregates (by strategy_key) over the period.
        agg_rows = (
            await self.session.execute(
                select(
                    StrategyPurchase.strategy_key,
                    func.count(StrategyPurchase.id),
                    func.coalesce(func.sum(StrategyPurchase.price_cents), 0),
                )
                .where(
                    StrategyPurchase.granted_at >= start,
                    StrategyPurchase.granted_at <= end,
                )
                .group_by(StrategyPurchase.strategy_key)
                .order_by(func.count(StrategyPurchase.id).desc())
                .limit(limit)
            )
        ).all()
        agg_map = {k: (int(c), int(r)) for k, c, r in agg_rows}

        # 2. Merge with catalog + listing metadata.
        strategies = list(
            await self.session.scalars(
                select(StrategyCatalog).where(
                    StrategyCatalog.key.in_(list(agg_map.keys())) if agg_map else False
                )
            )
        ) if agg_map else []
        listings = list(
            await self.session.scalars(
                select(MarketplaceListing).where(
                    MarketplaceListing.strategy_key.in_(list(agg_map.keys())) if agg_map else False
                )
            )
        ) if agg_map else []
        strat_by_key = {s.key: s for s in strategies}
        list_by_key = {l.strategy_key: l for l in listings}

        rows: list[StrategyAnalyticsRow] = []
        for key, (purchases, revenue) in agg_map.items():
            s = strat_by_key.get(key)
            l = list_by_key.get(key)
            rows.append(
                StrategyAnalyticsRow(
                    strategy_key=key,
                    strategy_name=(s.name if s else None),
                    category=(s.category if s else None),
                    status=(s.status if s else None),
                    total_purchases=purchases,
                    revenue_cents=revenue,
                    is_marketplace_listed=(l is not None and bool(l.is_active)),
                    is_featured=bool(l.is_featured) if l else False,
                    price_cents=int(l.price_cents) if l else 0,
                )
            )

        total_purchases = sum(r.total_purchases for r in rows)
        total_revenue = sum(r.revenue_cents for r in rows)

        return StrategyAnalyticsReport(
            period_start=start,
            period_end=end,
            total_purchases=total_purchases,
            total_revenue_cents=total_revenue,
            rows=rows,
        )
