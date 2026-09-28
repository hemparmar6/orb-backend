"""Module 9 — Market Intelligence endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, DBSession
from app.schemas.ai import MarketIntelligenceOut
from app.services.market_intelligence_service import MarketIntelligenceService

router = APIRouter()


@router.get("/{symbol}", response_model=MarketIntelligenceOut)
async def latest(symbol: str, user: CurrentUser, session: DBSession):
    row = await MarketIntelligenceService(session).latest(symbol)
    if row is None:
        raise HTTPException(status_code=404, detail="no_snapshot")
    return row


@router.post("/{symbol}/snapshot", response_model=MarketIntelligenceOut)
async def snapshot(
    symbol: str, user: CurrentUser, session: DBSession, timeframe: str = "D1",
):
    # Bars are provided by whichever market_data provider is configured
    # in the running project. For safety we return an error if we can't
    # obtain them — a real integration should call the project's market
    # data registry here.
    bars = await _fetch_bars(symbol, timeframe)
    if not bars:
        raise HTTPException(status_code=422, detail="no_bars_available")
    return await MarketIntelligenceService(session).snapshot(symbol, timeframe, bars)


async def _fetch_bars(symbol: str, timeframe: str):
    """Best-effort bar fetch via the project's historical data registry.

    We import lazily so this endpoint stays deployable even if the
    market-data providers are not yet wired for a given environment.
    """
    try:
        from app.engine.market_data.registry import get_historical_provider  # type: ignore
        provider = get_historical_provider()
        return await provider.recent_bars(symbol, timeframe)
    except Exception:
        return []
