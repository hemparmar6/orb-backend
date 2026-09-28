"""ORB AI 2.0 — Market-data & chart endpoints (Milestone 5).

Base path: ``/api/v1/market-data``

Charts, watchlists, favorites, layouts, indicator presets and drawings all
live here. All endpoints are user-scoped. Chart-layout cloud-sync is a
Pro feature (Milestones 2 & 4) — free users can still save layouts, but
they're capped at 2 and are NOT synced across devices.

Data-plane rule (per your instruction):
  * Dev preview: deterministic mock candles (no broker credentials needed).
  * Production: automatically switches to the existing Dhan historical /
    live providers via `app.engine.market_data.registry`. This module
    contains no broker-specific code.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.brokers.instruments.base import Instrument, InstrumentKind
from app.core.config import settings
from app.core.logging import get_logger
from app.engine.market_data import instrument_runtime
from app.engine.market_data.base import Interval
from app.models.chart import (
    ChartDrawingObject,
    ChartFavorite,
    ChartIndicatorPreset,
    ChartLayout,
    ChartOfflineData,
    ChartWatchlist,
)
from app.services.subscriptions.feature_gate import FeatureGate

router = APIRouter()

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Dhan REST wiring (Task 9)
# ---------------------------------------------------------------------------
# Endpoint timeframe string -> engine Interval, restricted to the values the
# Dhan v2 historical/intraday API actually supports. Timeframes outside this
# map cannot come from Dhan, so we honestly fall back to the mock provider.
_DHAN_TF_TO_INTERVAL: dict[str, Interval] = {
    "1m": Interval.ONE_MIN,
    "5m": Interval.FIVE_MIN,
    "15m": Interval.FIFTEEN_MIN,
    "1H": Interval.ONE_HOUR,
    "1D": Interval.ONE_DAY,
}

_DHAN_INSTRUMENT_TYPES = frozenset({
    "EQUITY", "INDEX", "FUTIDX", "FUTSTK", "FUTCUR", "FUTCOM",
    "OPTIDX", "OPTSTK", "OPTCUR", "OPTCOM", "COM", "CUR",
})


def _dhan_configured() -> bool:
    return bool(
        (settings.MARKET_DATA_PROVIDER or "").strip().lower() == "dhan"
        and (settings.DHAN_CLIENT_ID or "").strip()
        and (settings.DHAN_ACCESS_TOKEN or "").strip()
    )


def _dhan_instrument_type(instrument: Instrument) -> Optional[str]:
    raw = (instrument.instrument_type or "").strip().upper()
    if raw in _DHAN_INSTRUMENT_TYPES:
        return raw
    if instrument.kind == InstrumentKind.EQUITY:
        return "EQUITY"
    if instrument.kind == InstrumentKind.INDEX:
        return "INDEX"
    if instrument.kind == InstrumentKind.COMMODITY:
        return "COM"
    if instrument.kind == InstrumentKind.CURRENCY:
        return "CUR"
    # FUTURE/OPTION cannot be safely inferred as index vs stock without the
    # raw Dhan instrument type. Fail closed rather than sending EQUITY.
    return None


async def _dhan_candles(
    *, symbol: str, exchange: str, timeframe: str,
    count: int, end_dt: datetime, security_id: Optional[str],
) -> Optional[list[list[float]]]:
    """Fetch genuine Dhan historical candles, or ``None`` if unavailable.

    Returns ``None`` (never fake data) when Dhan is not configured, the
    timeframe/instrument cannot be resolved, or the provider errors / times
    out — the caller then applies the honest mock fallback. Only a non-None
    result may be labelled ``source="dhan"``.
    """
    if not _dhan_configured():
        return None
    interval = _DHAN_TF_TO_INTERVAL.get(timeframe)
    if interval is None:
        return None
    # ``security_id`` remains in the API for backwards compatibility but is
    # intentionally ignored. The instrument master is authoritative.
    resolved = instrument_runtime.resolve_dhan_instrument(symbol, exchange)
    if resolved is None:
        logger.info(
            "dhan_instrument_unresolved",
            extra={"symbol": symbol, "exchange": exchange},
        )
        return None
    resolved_instrument_type = _dhan_instrument_type(resolved)
    if resolved_instrument_type is None:
        logger.warning(
            "dhan_instrument_type_unresolved",
            extra={"symbol": symbol, "exchange": exchange, "kind": resolved.kind.value},
        )
        return None
    segment = resolved.exchange_segment
    # Estimate a lookback window big enough to cover `count` bars.
    span_seconds = _TF_SECONDS.get(timeframe, 900) * max(count, 1)
    start_dt = end_dt - timedelta(seconds=span_seconds)
    fetcher = None
    try:
        from app.engine.market_data.historical_base import get_historical
        fetcher = get_historical(
            "dhan",
            credentials={
                "client_id": settings.DHAN_CLIENT_ID,
                "access_token": settings.DHAN_ACCESS_TOKEN,
            },
            symbol_map={symbol: (str(resolved.token), segment)},
            instrument_map={symbol: resolved_instrument_type},
        )
        candles = await fetcher.get_candles(
            symbol, interval, start_dt, end_dt, exchange=segment
        )
    except Exception as exc:  # noqa: BLE001 — honest fallback, never fake "dhan"
        logger.warning(
            "dhan_rest_candles_unavailable",
            extra={"symbol": symbol, "exchange": exchange, "err": str(exc)},
        )
        return None
    finally:
        if fetcher is not None:
            try:
                await fetcher.close()
            except Exception:  # pragma: no cover
                pass
    if not candles:
        return None
    return [
        [int(c.ts.timestamp() * 1000),
         round(c.open, 4), round(c.high, 4), round(c.low, 4),
         round(c.close, 4), round(c.volume, 2)]
        for c in candles[-count:]
    ]

# ---------------------------------------------------------------------------
# Upstox V3 REST wiring (Task: staging live candles) — fail-closed
# ---------------------------------------------------------------------------
# Endpoint timeframe string -> Upstox native (unit, interval). Every ORB chart
# timeframe is natively supported by Upstox V3 (minutes 1-300, hours 1-5,
# days/weeks/months = 1), so each is served by a real Upstox endpoint/interval —
# no aggregation, no mock. Sourced from the fetcher module (single source of truth).
from app.engine.market_data.upstox_historical import (  # noqa: E402
    _UPSTOX_TF_UNIT_INTERVAL as _UPSTOX_TF,
)


from app.engine.market_data import upstox_credentials  # noqa: E402


def _upstox_selected() -> bool:
    return (settings.MARKET_DATA_PROVIDER or "").strip().lower() == "upstox"


def _upstox_configured() -> bool:
    # A read-only market-data credential is enough: UPSTOX_ACCESS_TOKEN first,
    # else the read-only UPSTOX_ANALYTICS_TOKEN fallback.
    return bool(_upstox_selected() and upstox_credentials.has_market_data_token())


def _resolve_upstox_key(symbol: str, exchange: str) -> Optional[str]:
    """Resolve an ORB symbol to an Upstox instrument key (never guessed)."""
    from app.engine.market_data.upstox_instruments import resolve_instrument_key

    return resolve_instrument_key(symbol)


async def _upstox_candles(
    *, symbol: str, exchange: str, timeframe: str,
    count: int, end_dt: datetime,
) -> Optional[list[list[float]]]:
    """Fetch genuine Upstox V3 candles, or ``None`` when unavailable.

    Returns ``None`` (never fake data) when Upstox is not configured, the
    timeframe/instrument cannot be resolved, or the provider errors / times out.
    Only a non-None result may be labelled ``source="upstox"``. Callers decide
    whether to fail closed (they must, when Upstox is the selected provider).
    """
    if not _upstox_configured():
        return None
    unit_interval = _UPSTOX_TF.get(timeframe)
    if unit_interval is None:
        return None
    unit, mult = unit_interval
    instrument_key = _resolve_upstox_key(symbol, exchange)
    if instrument_key is None:
        logger.info(
            "upstox_instrument_unresolved",
            extra={"symbol": symbol, "exchange": exchange},
        )
        return None
    span_seconds = _TF_SECONDS.get(timeframe, 900) * max(count, 1)
    start_dt = end_dt - timedelta(seconds=span_seconds)
    fetcher = None
    try:
        from app.engine.market_data.historical_base import get_historical
        fetcher = get_historical(
            "upstox",
            credentials=upstox_credentials.market_data_credentials(),
        )
        rows = await fetcher.fetch_ohlcv(
            instrument_key, unit, mult, start_dt, end_dt,
        )
    except Exception as exc:  # noqa: BLE001 — honest failure, never fake "upstox"
        logger.warning(
            "upstox_rest_candles_unavailable",
            extra={"symbol": symbol, "exchange": exchange, "err": str(exc)},
        )
        return None
    finally:
        if fetcher is not None:
            try:
                await fetcher.close()
            except Exception:  # pragma: no cover
                pass
    if not rows:
        return None
    return [
        [int(r[0]), round(r[1], 4), round(r[2], 4), round(r[3], 4),
         round(r[4], 4), round(r[5], 2)]
        for r in rows[-count:]
    ]


# ---------------------------------------------------------------------------
TIMEFRAMES: list[str] = [
    "1m", "2m", "3m", "5m", "10m", "15m", "30m", "45m",
    "1H", "2H", "4H", "1D", "1W", "1M",
]

_TF_SECONDS: dict[str, int] = {
    "1m": 60, "2m": 120, "3m": 180, "5m": 300, "10m": 600,
    "15m": 900, "30m": 1800, "45m": 2700,
    "1H": 3600, "2H": 7200, "4H": 14400,
    "1D": 86400, "1W": 604800,
    # For 1M we approximate to 30d for pagination; real broker feeds use exact months.
    "1M": 2592000,
}


class TimeframesOut(BaseModel):
    timeframes: list[str] = Field(default_factory=lambda: list(TIMEFRAMES))


@router.get("/timeframes", response_model=TimeframesOut,
            summary="List the 14 canonical chart timeframes")
async def list_timeframes() -> TimeframesOut:
    return TimeframesOut()


# ---------------------------------------------------------------------------
# Deterministic mock candles for dev preview (per your instruction)
# ---------------------------------------------------------------------------
def _seed_price(symbol: str) -> float:
    """Base price derived from the symbol so it stays stable across calls."""
    h = sum(ord(c) for c in symbol.upper()) or 100
    return round(50.0 + (h % 500) + (h % 37) * 3.7, 2)


def _mock_candles(symbol: str, timeframe: str, count: int,
                  end_ts: Optional[datetime] = None) -> list[list[float]]:
    """Generate `count` deterministic candles ending at `end_ts` (UTC).

    Uses per-symbol seeded RNG so the chart is stable across page reloads
    while still varying between symbols. Returned as compact
    [ts_ms, o, h, l, c, v] arrays for lightweight transport.
    """
    end_ts = end_ts or datetime.now(timezone.utc)
    step = _TF_SECONDS[timeframe]
    seed = hash((symbol.upper(), timeframe)) & 0xFFFFFFFF
    rng = random.Random(seed)

    price = _seed_price(symbol)
    drift = rng.uniform(-0.0004, 0.0004)   # subtle up/down bias per symbol
    vol_base = rng.uniform(0.0008, 0.004)  # tick-level volatility

    out: list[list[float]] = []
    for i in range(count):
        ts = end_ts - timedelta(seconds=step * (count - 1 - i))
        # Multiplicative random walk with mean reversion to seed price.
        shock = rng.gauss(0, 1) * price * vol_base
        mean_pull = (_seed_price(symbol) - price) * 0.02
        price = max(0.01, price + shock + mean_pull + drift * price)
        span = abs(rng.gauss(0, 1)) * price * vol_base * 1.4
        o = price + rng.uniform(-1, 1) * span * 0.35
        c = price + rng.uniform(-1, 1) * span * 0.35
        h = max(o, c, price) + rng.uniform(0, span * 0.5)
        l = min(o, c, price) - rng.uniform(0, span * 0.5)
        v = max(1.0, rng.gauss(1000, 250) + abs(shock) * 800)
        out.append([
            int(ts.timestamp() * 1000),
            round(o, 4), round(h, 4), round(l, 4), round(c, 4),
            round(v, 2),
        ])
    return out


# ---------------------------------------------------------------------------
# GET /candles  — historical bars
# ---------------------------------------------------------------------------
class CandlesOut(BaseModel):
    symbol: str
    exchange: str
    timeframe: str
    count: int
    source: Literal["mock", "dhan", "upstox", "offline"] = "mock"
    candles: list[list[float]]  # [[ts_ms, o, h, l, c, v], …]


@router.get("/candles", response_model=CandlesOut,
            summary="Historical candles (Dhan when configured, else deterministic mock)")
async def get_candles(
    user: CurrentUser, session: DBSession,
    symbol: str = Query(..., min_length=1, max_length=64),
    timeframe: str = Query("15m"),
    exchange: str = Query("MOCK"),
    count: int = Query(300, ge=1, le=5000),
    end_ts: Optional[int] = Query(None, description="Unix ms, exclusive; defaults to now"),
    prefer_offline: bool = Query(False),
    security_id: Optional[str] = Query(
        None, description="Deprecated; server-side Dhan instrument resolution is authoritative"
    ),
) -> CandlesOut:
    if timeframe not in _TF_SECONDS:
        raise HTTPException(status_code=422, detail=f"Unknown timeframe {timeframe!r}")

    end_dt = (
        datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)
        if end_ts is not None else datetime.now(timezone.utc)
    )

    # 1) Try offline cache when requested / available.
    if prefer_offline:
        row = (await session.execute(
            select(ChartOfflineData).where(
                ChartOfflineData.user_id == user.id,
                ChartOfflineData.symbol == symbol,
                ChartOfflineData.exchange == exchange,
                ChartOfflineData.timeframe == timeframe,
            )
        )).scalar_one_or_none()
        if row and row.candles:
            candles = row.candles[-count:]
            return CandlesOut(
                symbol=symbol, exchange=exchange, timeframe=timeframe,
                count=len(candles), source="offline", candles=candles,
            )

    # 2) When MARKET_DATA_PROVIDER=upstox, serve genuine Upstox V3 candles and
    #    FAIL CLOSED — never substitute mock prices for a real, explicitly
    #    selected provider. A failure returns a clear 503 instead of fake data.
    if _upstox_selected():
        if not _upstox_configured():
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "market_data_provider_unavailable",
                    "provider": "upstox",
                    "reason": "no Upstox market-data credential configured (UPSTOX_ACCESS_TOKEN or read-only UPSTOX_ANALYTICS_TOKEN)",
                },
            )
        if timeframe not in _UPSTOX_TF:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "market_data_timeframe_unsupported",
                    "provider": "upstox",
                    "timeframe": timeframe,
                },
            )
        upstox = await _upstox_candles(
            symbol=symbol, exchange=exchange, timeframe=timeframe,
            count=count, end_dt=end_dt,
        )
        if upstox is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "market_data_provider_unavailable",
                    "provider": "upstox",
                    "reason": "upstox candle retrieval failed or instrument unresolved",
                },
            )
        return CandlesOut(
            symbol=symbol, exchange=exchange, timeframe=timeframe,
            count=len(upstox), source="upstox", candles=upstox,
        )

    # 3) When MARKET_DATA_PROVIDER=dhan and Dhan is configured, use the
    #    existing Dhan historical REST fetcher. Only label source="dhan"
    #    when the data genuinely came from Dhan; otherwise fall through to
    #    the deterministic mock provider (never label mock as dhan/LIVE).
    dhan = await _dhan_candles(
        symbol=symbol, exchange=exchange, timeframe=timeframe,
        count=count, end_dt=end_dt, security_id=security_id,
    )
    if dhan is not None:
        return CandlesOut(
            symbol=symbol, exchange=exchange, timeframe=timeframe,
            count=len(dhan), source="dhan", candles=dhan,
        )

    # 3) Honest fallback — deterministic mock provider.
    candles = _mock_candles(symbol, timeframe, count, end_dt)
    return CandlesOut(
        symbol=symbol, exchange=exchange, timeframe=timeframe,
        count=len(candles), source="mock", candles=candles,
    )


class LiveTickOut(BaseModel):
    symbol: str
    exchange: str
    ts: int
    price: float
    volume: float
    source: Literal["mock", "dhan", "upstox", "offline"] = "mock"


@router.get("/tick", response_model=LiveTickOut,
            summary="Latest tick (Dhan when configured, else deterministic mock)")
async def get_tick(
    user: CurrentUser,
    symbol: str = Query(..., min_length=1, max_length=64),
    exchange: str = Query("MOCK"),
    security_id: Optional[str] = Query(
        None, description="Deprecated; server-side Dhan instrument resolution is authoritative"
    ),
) -> LiveTickOut:
    now = datetime.now(timezone.utc)

    # Upstox selected → fail closed: derive the latest tick from the newest
    # 1-minute Upstox candle. Never fall back to mock for a real provider.
    if _upstox_selected():
        if not _upstox_configured():
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "market_data_provider_unavailable",
                    "provider": "upstox",
                    "reason": "no Upstox market-data credential configured (UPSTOX_ACCESS_TOKEN or read-only UPSTOX_ANALYTICS_TOKEN)",
                },
            )
        upstox = await _upstox_candles(
            symbol=symbol, exchange=exchange, timeframe="1m",
            count=1, end_dt=now,
        )
        if not upstox:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "market_data_provider_unavailable",
                    "provider": "upstox",
                    "reason": "upstox tick retrieval failed or instrument unresolved",
                },
            )
        last = upstox[-1]
        return LiveTickOut(
            symbol=symbol, exchange=exchange,
            ts=int(last[0]), price=float(last[4]), volume=float(last[5]),
            source="upstox",
        )

    # Real Dhan tick — derive from the latest 1-minute candle. Only labelled
    # source="dhan" when the data genuinely came from Dhan.
    if _dhan_configured():
        dhan = await _dhan_candles(
            symbol=symbol, exchange=exchange, timeframe="1m",
            count=1, end_dt=now, security_id=security_id,
        )
        if dhan:
            last = dhan[-1]
            return LiveTickOut(
                symbol=symbol, exchange=exchange,
                ts=int(last[0]), price=float(last[4]), volume=float(last[5]),
                source="dhan",
            )

    # Honest fallback — same deterministic RNG so consecutive polls produce a
    # coherent random walk around the last candle's close.
    seed = int(now.timestamp() // 60) ^ hash(symbol.upper())
    rng = random.Random(seed & 0xFFFFFFFF)
    base = _seed_price(symbol)
    shock = rng.gauss(0, 1) * base * 0.001
    price = max(0.01, base + shock + math.sin(now.timestamp() / 90) * base * 0.002)
    return LiveTickOut(
        symbol=symbol, exchange=exchange,
        ts=int(now.timestamp() * 1000),
        price=round(price, 4), volume=round(rng.uniform(50, 500), 2),
        source="mock",
    )


# ---------------------------------------------------------------------------
# POST /download — pre-fetch a symbol/timeframe range for offline viewing
# ---------------------------------------------------------------------------
class DownloadIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=64)
    exchange: str = "MOCK"
    timeframe: str = "15m"
    range_label: Literal["1D", "1W", "1M", "3M", "6M", "1Y"] = "1M"


class DownloadOut(BaseModel):
    stored: int
    range_label: str


_RANGE_CANDLES: dict[str, int] = {
    "1D": 96, "1W": 500, "1M": 720, "3M": 1440, "6M": 2400, "1Y": 4000,
}


@router.post("/download", response_model=DownloadOut,
             summary="Download and cache candles locally for offline mode")
async def download_offline(
    payload: DownloadIn, user: CurrentUser, session: DBSession,
) -> DownloadOut:
    if payload.timeframe not in _TF_SECONDS:
        raise HTTPException(status_code=422, detail="Unknown timeframe")
    count = _RANGE_CANDLES[payload.range_label]
    candles = _mock_candles(payload.symbol, payload.timeframe, count)

    row = (await session.execute(
        select(ChartOfflineData).where(
            ChartOfflineData.user_id == user.id,
            ChartOfflineData.symbol == payload.symbol,
            ChartOfflineData.exchange == payload.exchange,
            ChartOfflineData.timeframe == payload.timeframe,
        )
    )).scalar_one_or_none()

    from_ts = datetime.fromtimestamp(candles[0][0] / 1000, tz=timezone.utc) if candles else None
    to_ts = datetime.fromtimestamp(candles[-1][0] / 1000, tz=timezone.utc) if candles else None

    if row is None:
        session.add(ChartOfflineData(
            user_id=user.id, symbol=payload.symbol, exchange=payload.exchange,
            timeframe=payload.timeframe, range_label=payload.range_label,
            candles=candles, from_ts=from_ts, to_ts=to_ts,
        ))
    else:
        row.candles = candles
        row.range_label = payload.range_label
        row.from_ts = from_ts
        row.to_ts = to_ts
    await session.commit()
    return DownloadOut(stored=len(candles), range_label=payload.range_label)


# ===========================================================================
# WATCHLISTS
# ===========================================================================
class SymbolEntry(BaseModel):
    symbol: str
    exchange: str = "MOCK"
    label: Optional[str] = None


class WatchlistIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    symbols: list[SymbolEntry] = Field(default_factory=list)


class WatchlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    symbols: list[SymbolEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


def _wl_out(row: ChartWatchlist) -> WatchlistOut:
    return WatchlistOut(
        id=row.id, name=row.name,
        symbols=[SymbolEntry(**s) if isinstance(s, dict) else s for s in (row.symbols or [])],
        created_at=row.created_at, updated_at=row.updated_at,
    )


@router.get("/watchlists", response_model=list[WatchlistOut],
            summary="List my watchlists")
async def list_watchlists(user: CurrentUser, session: DBSession) -> list[WatchlistOut]:
    rows = (await session.execute(
        select(ChartWatchlist).where(ChartWatchlist.user_id == user.id)
        .order_by(ChartWatchlist.created_at.desc())
    )).scalars().all()
    return [_wl_out(r) for r in rows]


@router.post("/watchlists", response_model=WatchlistOut,
             status_code=status.HTTP_201_CREATED)
async def create_watchlist(payload: WatchlistIn, user: CurrentUser,
                           session: DBSession) -> WatchlistOut:
    row = ChartWatchlist(
        user_id=user.id, name=payload.name,
        symbols=[s.model_dump() for s in payload.symbols],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _wl_out(row)


@router.patch("/watchlists/{watchlist_id}", response_model=WatchlistOut)
async def update_watchlist(watchlist_id: str, payload: WatchlistIn,
                           user: CurrentUser, session: DBSession) -> WatchlistOut:
    row = (await session.execute(
        select(ChartWatchlist).where(
            ChartWatchlist.id == watchlist_id, ChartWatchlist.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    row.name = payload.name
    row.symbols = [s.model_dump() for s in payload.symbols]
    await session.commit()
    await session.refresh(row)
    return _wl_out(row)


@router.delete("/watchlists/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_watchlist(watchlist_id: str, user: CurrentUser,
                           session: DBSession) -> Response:
    row = (await session.execute(
        select(ChartWatchlist).where(
            ChartWatchlist.id == watchlist_id, ChartWatchlist.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# FAVORITES
# ===========================================================================
class FavoriteIn(BaseModel):
    symbol: str
    exchange: str = "MOCK"


class FavoriteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    symbol: str
    exchange: str
    created_at: datetime


@router.get("/favorites", response_model=list[FavoriteOut])
async def list_favorites(user: CurrentUser, session: DBSession) -> list[FavoriteOut]:
    rows = (await session.execute(
        select(ChartFavorite).where(ChartFavorite.user_id == user.id)
        .order_by(ChartFavorite.created_at.desc())
    )).scalars().all()
    return [FavoriteOut.model_validate(r) for r in rows]


@router.post("/favorites", response_model=FavoriteOut,
             status_code=status.HTTP_201_CREATED)
async def add_favorite(payload: FavoriteIn, user: CurrentUser,
                       session: DBSession) -> FavoriteOut:
    exists = (await session.execute(
        select(ChartFavorite).where(
            ChartFavorite.user_id == user.id,
            ChartFavorite.symbol == payload.symbol,
            ChartFavorite.exchange == payload.exchange,
        )
    )).scalar_one_or_none()
    if exists:
        return FavoriteOut.model_validate(exists)
    row = ChartFavorite(user_id=user.id, symbol=payload.symbol, exchange=payload.exchange)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return FavoriteOut.model_validate(row)


@router.delete("/favorites/{fav_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def remove_favorite(fav_id: str, user: CurrentUser, session: DBSession) -> Response:
    row = (await session.execute(
        select(ChartFavorite).where(
            ChartFavorite.id == fav_id, ChartFavorite.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Favorite not found")
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# CHART LAYOUTS (1 / 2 / 4 panes; cross-device sync is Pro)
class LayoutIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    grid: Literal["1", "2h", "2v", "4"] = "1"
    data: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class LayoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    grid: str
    data: dict[str, Any]
    is_default: int
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


@router.get("/layouts", response_model=list[LayoutOut])
async def list_layouts(user: CurrentUser, session: DBSession) -> list[LayoutOut]:
    rows = (await session.execute(
        select(ChartLayout).where(ChartLayout.user_id == user.id)
        .order_by(ChartLayout.updated_at.desc())
    )).scalars().all()
    return [LayoutOut.model_validate(r) for r in rows]


@router.post("/layouts", response_model=LayoutOut,
             status_code=status.HTTP_201_CREATED)
async def create_layout(payload: LayoutIn, user: CurrentUser,
                        session: DBSession) -> LayoutOut:
    # Free plan cap: 2 saved layouts (mirrors Milestone 2 saved-strategies cap).
    try:
        plan = await FeatureGate(session).get_plan(user)
    except Exception:
        plan = None
    is_pro = bool(plan and plan.ai_features_enabled)
    if not is_pro:
        existing = (await session.execute(
            select(ChartLayout).where(ChartLayout.user_id == user.id)
        )).scalars().all()
        if len(existing) >= 2:
            raise HTTPException(status_code=402, detail={
                "code": "layout_quota_exceeded",
                "message": "Free plan allows 2 saved chart layouts. Upgrade to Pro for unlimited + cloud sync.",
                "upgrade_url": "/plans",
            })

    row = ChartLayout(
        user_id=user.id, name=payload.name, grid=payload.grid,
        data=payload.data, is_default=1 if payload.is_default else 0,
        last_synced_at=datetime.now(timezone.utc) if is_pro else None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return LayoutOut.model_validate(row)


@router.patch("/layouts/{layout_id}", response_model=LayoutOut)
async def update_layout(layout_id: str, payload: LayoutIn,
                        user: CurrentUser, session: DBSession) -> LayoutOut:
    row = (await session.execute(
        select(ChartLayout).where(
            ChartLayout.id == layout_id, ChartLayout.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Layout not found")
    row.name = payload.name
    row.grid = payload.grid
    row.data = payload.data
    row.is_default = 1 if payload.is_default else 0

    try:
        plan = await FeatureGate(session).get_plan(user)
    except Exception:
        plan = None
    if plan and plan.ai_features_enabled:
        row.last_synced_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(row)
    return LayoutOut.model_validate(row)


@router.delete("/layouts/{layout_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_layout(layout_id: str, user: CurrentUser, session: DBSession) -> Response:
    row = (await session.execute(
        select(ChartLayout).where(
            ChartLayout.id == layout_id, ChartLayout.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Layout not found")
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# INDICATOR PRESETS
# ===========================================================================
class IndicatorPresetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    indicators: list[dict[str, Any]] = Field(default_factory=list)


class IndicatorPresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    indicators: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


@router.get("/indicator-presets", response_model=list[IndicatorPresetOut])
async def list_indicator_presets(user: CurrentUser,
                                 session: DBSession) -> list[IndicatorPresetOut]:
    rows = (await session.execute(
        select(ChartIndicatorPreset).where(ChartIndicatorPreset.user_id == user.id)
        .order_by(ChartIndicatorPreset.updated_at.desc())
    )).scalars().all()
    return [IndicatorPresetOut.model_validate(r) for r in rows]


@router.post("/indicator-presets", response_model=IndicatorPresetOut,
             status_code=status.HTTP_201_CREATED)
async def create_indicator_preset(payload: IndicatorPresetIn, user: CurrentUser,
                                  session: DBSession) -> IndicatorPresetOut:
    row = ChartIndicatorPreset(
        user_id=user.id, name=payload.name, indicators=payload.indicators,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return IndicatorPresetOut.model_validate(row)


@router.delete("/indicator-presets/{preset_id}",
               status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_indicator_preset(preset_id: str, user: CurrentUser,
                                  session: DBSession) -> Response:
    row = (await session.execute(
        select(ChartIndicatorPreset).where(
            ChartIndicatorPreset.id == preset_id,
            ChartIndicatorPreset.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Preset not found")
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# DRAWING OBJECTS
# ===========================================================================
class DrawingIn(BaseModel):
    symbol: str
    exchange: str = "MOCK"
    timeframe: str = "15m"
    tool: str
    data: dict[str, Any] = Field(default_factory=dict)


class DrawingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    symbol: str
    exchange: str
    timeframe: str
    tool: str
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@router.get("/drawings", response_model=list[DrawingOut])
async def list_drawings(
    user: CurrentUser, session: DBSession,
    symbol: str = Query(...),
    exchange: str = Query("MOCK"),
    timeframe: str = Query("15m"),
) -> list[DrawingOut]:
    rows = (await session.execute(
        select(ChartDrawingObject).where(
            ChartDrawingObject.user_id == user.id,
            ChartDrawingObject.symbol == symbol,
            ChartDrawingObject.exchange == exchange,
            ChartDrawingObject.timeframe == timeframe,
        )
    )).scalars().all()
    return [DrawingOut.model_validate(r) for r in rows]


@router.post("/drawings", response_model=DrawingOut,
             status_code=status.HTTP_201_CREATED)
async def create_drawing(payload: DrawingIn, user: CurrentUser,
                         session: DBSession) -> DrawingOut:
    row = ChartDrawingObject(
        user_id=user.id, symbol=payload.symbol, exchange=payload.exchange,
        timeframe=payload.timeframe, tool=payload.tool, data=payload.data,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return DrawingOut.model_validate(row)


@router.delete("/drawings/{drawing_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_drawing(drawing_id: str, user: CurrentUser,
                         session: DBSession) -> Response:
    row = (await session.execute(
        select(ChartDrawingObject).where(
            ChartDrawingObject.id == drawing_id,
            ChartDrawingObject.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Drawing not found")
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
