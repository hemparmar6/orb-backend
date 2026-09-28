"""UpstoxHistoricalFetcher + /candles fail-closed wiring (Upstox selected).

External Upstox REST is mocked with httpx.MockTransport — no network, no creds.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.core.exceptions import EngineError
from app.engine.market_data import UpstoxHistoricalFetcher, get_historical
from app.engine.market_data.base import Interval

TOKEN = "SECRET-UPSTOX-TOKEN"
NIFTY_KEY = "NSE_INDEX|Nifty 50"


def _client(historical: list, intraday: list, *, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        if status >= 400:
            return httpx.Response(status, json={"status": "error"})
        rows = intraday if "/intraday/" in request.url.path else historical
        return httpx.Response(200, json={"status": "success", "data": {"candles": rows}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_registered_via_historical_registry():
    f = get_historical("upstox", credentials={"access_token": TOKEN})
    assert isinstance(f, UpstoxHistoricalFetcher)
    assert f.name == "upstox"


def test_missing_token_rejected():
    with pytest.raises(ValueError):
        UpstoxHistoricalFetcher(credentials={})


@pytest.mark.asyncio
async def test_candles_normalised_sorted_and_deduped():
    # Upstox returns newest-first; include a duplicate ts across hist+intraday.
    historical = [
        ["2025-01-02T09:16:00+05:30", 101.0, 103.0, 100.0, 102.0, 2000, 0],
        ["2025-01-02T09:15:00+05:30", 100.0, 102.0, 99.0, 101.0, 1500, 0],
    ]
    intraday = [
        ["2025-01-02T09:17:00+05:30", 102.0, 104.0, 101.0, 103.5, 2500, 0],
        # duplicate of a historical bar (must be de-duped, intraday wins)
        ["2025-01-02T09:16:00+05:30", 101.0, 103.0, 100.0, 102.5, 9999, 0],
    ]
    f = UpstoxHistoricalFetcher(
        credentials={"access_token": TOKEN}, symbol_map={"NIFTY": NIFTY_KEY},
        http_client=_client(historical, intraday),
    )
    start = datetime.now(timezone.utc).replace(hour=0, minute=0)
    end = datetime.now(timezone.utc)
    candles = await f.get_candles("NIFTY", Interval.ONE_MIN, start, end)

    # 3 unique timestamps, oldest-first.
    assert [c.ts.isoformat() for c in candles] == sorted(
        c.ts.isoformat() for c in candles
    )
    assert len(candles) == 3
    # Timestamps normalised to UTC (09:15 IST == 03:45 UTC).
    assert candles[0].ts == datetime(2025, 1, 2, 3, 45, tzinfo=timezone.utc)
    # OHLC + volume mapped correctly.
    assert (candles[0].open, candles[0].high, candles[0].low, candles[0].close) == (
        100.0, 102.0, 99.0, 101.0,
    )
    assert candles[0].volume == 1500.0
    # Dedup: the 09:16 bar takes the intraday value (close 102.5, vol 9999).
    bar_0916 = candles[1]
    assert bar_0916.close == 102.5 and bar_0916.volume == 9999.0


@pytest.mark.asyncio
async def test_unknown_symbol_raises():
    f = UpstoxHistoricalFetcher(
        credentials={"access_token": TOKEN}, http_client=_client([], []),
    )
    with pytest.raises(EngineError):
        await f.get_candles("MYSTERY", Interval.ONE_MIN,
                            datetime.now(timezone.utc), datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_auth_failure_raises_engine_error():
    f = UpstoxHistoricalFetcher(
        credentials={"access_token": TOKEN}, symbol_map={"NIFTY": NIFTY_KEY},
        http_client=_client([], [], status=401), include_intraday=False,
    )
    with pytest.raises(EngineError) as exc:
        await f.get_candles("NIFTY", Interval.ONE_DAY,
                            datetime.now(timezone.utc), datetime.now(timezone.utc))
    assert exc.value.code == "upstox_authentication_failed"


# --------------------------------------------------------------------------- #
# Native timeframe coverage — every ORB chart timeframe is native to Upstox V3.
# --------------------------------------------------------------------------- #

_ORB_CHART_TIMEFRAMES = [
    "1m", "2m", "3m", "5m", "10m", "15m", "30m", "45m",
    "1H", "2H", "4H", "1D", "1W", "1M",
]


def test_all_orb_chart_timeframes_natively_mapped():
    from app.engine.market_data.upstox_historical import _UPSTOX_TF_UNIT_INTERVAL
    for tf in _ORB_CHART_TIMEFRAMES:
        assert tf in _UPSTOX_TF_UNIT_INTERVAL, f"{tf} not mapped"
        unit, mult = _UPSTOX_TF_UNIT_INTERVAL[tf]
        assert unit in {"minutes", "hours", "days", "weeks", "months"}
        # Upstox native limits: minutes 1-300, hours 1-5, days/weeks/months = 1.
        n = int(mult)
        if unit == "minutes":
            assert 1 <= n <= 300
        elif unit == "hours":
            assert 1 <= n <= 5
        else:
            assert n == 1


@pytest.mark.asyncio
async def test_fetch_ohlcv_non_enum_timeframe_30m():
    # 30m has no ORB Interval enum value, but Upstox serves it natively.
    historical = [
        ["2025-01-02T10:00:00+05:30", 200.0, 205.0, 199.0, 204.0, 5000, 0],
        ["2025-01-02T09:30:00+05:30", 198.0, 201.0, 197.0, 200.0, 4000, 0],
    ]
    f = UpstoxHistoricalFetcher(
        credentials={"access_token": TOKEN}, http_client=_client(historical, []),
    )
    rows = await f.fetch_ohlcv("NSE_INDEX|Nifty Bank", "minutes", "30",
                               datetime.now(timezone.utc).replace(hour=0),
                               datetime.now(timezone.utc))
    assert len(rows) == 2
    # oldest-first, shape [ts_ms, o, h, l, c, v]
    assert rows[0][1:] == [198.0, 201.0, 197.0, 200.0, 4000.0]
    assert rows[1][0] > rows[0][0]


@pytest.mark.asyncio
async def test_fetch_ohlcv_weekly_skips_intraday():
    # Weeks unit must NOT hit the intraday endpoint.
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        rows = [["2025-01-06T00:00:00+05:30", 100.0, 110.0, 95.0, 108.0, 9, 0]]
        return httpx.Response(200, json={"status": "success", "data": {"candles": rows}})

    f = UpstoxHistoricalFetcher(
        credentials={"access_token": TOKEN},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    rows = await f.fetch_ohlcv("NSE_INDEX|Nifty 50", "weeks", "1",
                               datetime.now(timezone.utc), datetime.now(timezone.utc))
    assert len(rows) == 1
    assert not any("/intraday/" in p for p in calls)


# --------------------------------------------------------------------------- #
# Endpoint fail-closed wiring — call the handler directly (no DB needed when
# prefer_offline=False). Verifies NO mock fallback under MARKET_DATA_PROVIDER=upstox.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_candles_endpoint_fails_closed_without_token(monkeypatch):
    from app.api.v1.endpoints import market_data as md
    from app.core.config import settings
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", None, raising=False)

    with pytest.raises(HTTPException) as exc:
        await md.get_candles(user=object(), session=None, symbol="NIFTY",
                             timeframe="15m", exchange="NSE", count=10,
                             end_ts=None, prefer_offline=False, security_id=None)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "market_data_provider_unavailable"


@pytest.mark.asyncio
async def test_candles_endpoint_fails_closed_on_provider_error(monkeypatch):
    from app.api.v1.endpoints import market_data as md
    from app.core.config import settings
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", TOKEN, raising=False)

    async def _fail(**kwargs):
        return None  # simulates upstox unavailable

    monkeypatch.setattr(md, "_upstox_candles", _fail)
    with pytest.raises(HTTPException) as exc:
        await md.get_candles(user=object(), session=None, symbol="NIFTY",
                             timeframe="15m", exchange="NSE", count=10,
                             end_ts=None, prefer_offline=False, security_id=None)
    assert exc.value.status_code == 503
    # Critically: it did NOT return mock candles.


@pytest.mark.asyncio
async def test_candles_endpoint_returns_upstox_source_on_success(monkeypatch):
    from app.api.v1.endpoints import market_data as md
    from app.core.config import settings

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", TOKEN, raising=False)

    async def _ok(**kwargs):
        return [[1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 1234.0]]

    monkeypatch.setattr(md, "_upstox_candles", _ok)
    out = await md.get_candles(user=object(), session=None, symbol="BANKNIFTY",
                               timeframe="15m", exchange="NSE", count=10,
                               end_ts=None, prefer_offline=False, security_id=None)
    assert out.source == "upstox"
    assert out.count == 1
    assert out.candles[0][4] == 100.5


@pytest.mark.asyncio
async def test_mock_provider_endpoint_behaviour_unchanged(monkeypatch):
    from app.api.v1.endpoints import market_data as md
    from app.core.config import settings

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "mock", raising=False)
    out = await md.get_candles(user=object(), session=None, symbol="NIFTY",
                               timeframe="15m", exchange="MOCK", count=5,
                               end_ts=None, prefer_offline=False, security_id=None)
    assert out.source == "mock"
    assert out.count == 5


@pytest.mark.asyncio
async def test_all_14_chart_timeframes_route_to_upstox_no_503(monkeypatch):
    """Every ORB chart timeframe must be served by Upstox (source=upstox),
    never 503-unsupported and never mock, when the provider succeeds."""
    from app.api.v1.endpoints import market_data as md
    from app.core.config import settings

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", TOKEN, raising=False)

    async def _ok(**kwargs):
        return [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]

    monkeypatch.setattr(md, "_upstox_candles", _ok)
    for tf in _ORB_CHART_TIMEFRAMES:
        out = await md.get_candles(user=object(), session=None, symbol="NIFTY",
                                   timeframe=tf, exchange="NSE", count=10,
                                   end_ts=None, prefer_offline=False, security_id=None)
        assert out.source == "upstox", f"{tf} did not route to upstox"
        assert out.count == 1
