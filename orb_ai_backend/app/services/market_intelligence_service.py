"""Market Intelligence service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.market_intel import gap, liquidity, regime, session_stats, trend_strength, volatility
from app.models import MarketIntelligence


class MarketIntelligenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def snapshot(
        self, symbol: str, timeframe: str, bars: List[Dict[str, Any]],
    ) -> MarketIntelligence:
        row = MarketIntelligence(
            symbol=symbol,
            timeframe=timeframe,
            regime=regime.detect(bars),
            trend_strength=trend_strength.score(bars),
            volatility_regime=volatility.regime(bars),
            liquidity=liquidity.classify(bars),
            gap_behaviour=gap.behaviour(bars),
            session_stats=session_stats.summarise(bars),
            generated_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return row

    async def latest(self, symbol: str) -> MarketIntelligence | None:
        stmt = (
            select(MarketIntelligence)
            .where(MarketIntelligence.symbol == symbol)
            .order_by(MarketIntelligence.generated_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
