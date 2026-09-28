"""Historical candle fetchers — Dhan, Kotak Neo, and the synthetic fallback.

Uses ``httpx.MockTransport`` to drive the two REST-backed fetchers. Also
verifies ``BacktestService`` still emits candles when the fetcher is left
unset (synthetic fallback) and when a fetcher is supplied.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.core.exceptions import EngineError
from app.engine.market_data import (
    DhanHistoricalFetcher,
    HistoricalCandleFetcher,
    KotakNeoHistoricalFetcher,
    SyntheticHistoricalFetcher,
    get_historical,
    list_historical,
)
from app.engine.market_data.base import Candle, Interval


# ------------------------------------------------------------------ registry


def test_registry_lists_all_fetchers():
    names = list_historical()
    assert "synthetic" in names
    assert "dhan" in names
    assert "kotak_neo" in names


def test_registry_unknown_raises():
    with pytest.raises(EngineError):
        get_historical("does_not_exist")


# -------------------------------------------------------- synthetic fallback


@pytest.mark.asyncio
async def test_synthetic_fetcher_returns_candles():
    fetcher = SyntheticHistoricalFetcher(base_price=100.0, seed=1)
    candles = await fetcher.get_candles(
        symbol="TCS",
        interval=Interval.ONE_MIN,
        start=datetime(2024, 1, 1, 9, 15, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, 15, 15, tzinfo=timezone.utc),
    )
    assert len(candles) > 0
    assert all(isinstance(c, Candle) for c in candles)
    assert all(c.symbol == "TCS" for c in candles)


# ----------------------------------------------------------- Dhan historical


def test_dhan_fetcher_rejects_missing_credentials():
    with pytest.raises(ValueError):
        DhanHistoricalFetcher(credentials={"client_id": "x"})
    with pytest.raises(ValueError):
        DhanHistoricalFetcher(credentials={"access_token": "y"})


@pytest.mark.asyncio
async def test_dhan_fetcher_intraday_happy_path():
    seen_requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        seen_requests.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        assert body["securityId"] == "11536"
        assert body["exchangeSegment"] == "NSE_EQ"
        assert body["interval"] == "1"
        return httpx.Response(
            200,
            json={
                "open": [100.0, 101.0, 102.0],
                "high": [102.0, 103.0, 104.0],
                "low": [99.5, 100.5, 101.5],
                "close": [101.0, 102.0, 103.0],
                "volume": [1000, 1500, 1200],
                "timestamp": [1_700_000_000, 1_700_000_060, 1_700_000_120],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = DhanHistoricalFetcher(
        credentials={"client_id": "C1", "access_token": "TOKEN"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
        http_client=client,
    )

    candles = await fetcher.get_candles(
        symbol="TCS",
        interval=Interval.ONE_MIN,
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    await fetcher.close()

    assert len(candles) == 3
    assert candles[0].symbol == "TCS"
    assert candles[0].exchange == "NSE_EQ"
    assert candles[0].interval == Interval.ONE_MIN
    assert candles[0].open == 100.0
    assert candles[0].close == 101.0
    assert candles[0].ts == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    # Headers carry auth without hitting the URL.
    assert seen_requests[0]["headers"].get("access-token") == "TOKEN"
    assert seen_requests[0]["headers"].get("client-id") == "C1"


@pytest.mark.asyncio
async def test_dhan_fetcher_uses_historical_url_for_daily():
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "open": [10.0],
                "high": [11.0],
                "low": [9.0],
                "close": [10.5],
                "volume": [100],
                "timestamp": [1_700_000_000],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = DhanHistoricalFetcher(
        credentials={"client_id": "C1", "access_token": "T"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
        http_client=client,
    )
    candles = await fetcher.get_candles(
        symbol="TCS",
        interval=Interval.ONE_DAY,
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 10, tzinfo=timezone.utc),
    )
    await fetcher.close()

    assert len(candles) == 1
    assert candles[0].interval == Interval.ONE_DAY
    # Daily → the /historical URL (not /intraday).
    assert "charts/historical" in seen_urls[0]


@pytest.mark.asyncio
async def test_dhan_fetcher_unknown_symbol_raises():
    fetcher = DhanHistoricalFetcher(
        credentials={"client_id": "C1", "access_token": "T"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
    )
    with pytest.raises(EngineError):
        await fetcher.get_candles(
            "UNKNOWN",
            Interval.ONE_MIN,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_dhan_fetcher_error_response_raises_engine_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = DhanHistoricalFetcher(
        credentials={"client_id": "C1", "access_token": "T"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
        http_client=client,
    )
    with pytest.raises(EngineError) as exc:
        await fetcher.get_candles(
            "TCS",
            Interval.ONE_MIN,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert exc.value.code == "dhan_historical_failed"
    await fetcher.close()


@pytest.mark.asyncio
async def test_dhan_fetcher_skips_malformed_rows():
    """Malformed rows should be skipped, not crash the batch."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "open": [100.0, "nan", 102.0],
                "high": [101.0, 101.0, 103.0],
                "low": [99.0, 99.0, 101.0],
                "close": [100.5, 100.5, 102.5],
                "volume": [1000, 1000, 1000],
                "timestamp": [1_700_000_000, "bad", 1_700_000_120],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = DhanHistoricalFetcher(
        credentials={"client_id": "C1", "access_token": "T"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
        http_client=client,
    )
    candles = await fetcher.get_candles(
        "TCS",
        Interval.ONE_MIN,
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    await fetcher.close()
    assert len(candles) == 2  # rows 0 and 2, row 1 skipped


# --------------------------------------------------- Kotak Neo historical


def test_kotak_neo_fetcher_rejects_missing_credentials():
    with pytest.raises(ValueError):
        KotakNeoHistoricalFetcher(credentials={})
    with pytest.raises(ValueError):
        KotakNeoHistoricalFetcher(credentials={"consumer_key": "k"})


@pytest.mark.asyncio
async def test_kotak_neo_fetcher_presigned_happy_path():
    seen_urls: list[str] = []
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        seen_headers.append(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "data": {
                    "candles": [
                        [1_700_000_000, 100.0, 102.0, 99.0, 101.0, 1000],
                        [1_700_000_060, 101.0, 103.0, 100.0, 102.5, 1500],
                    ]
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = KotakNeoHistoricalFetcher(
        credentials={"sid": "SID", "session_token": "TOK", "view_token": "VIEW"},
        symbol_map={"TCS": ("11536", "nse_cm")},
        http_client=client,
    )
    candles = await fetcher.get_candles(
        symbol="TCS",
        interval=Interval.ONE_MIN,
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    await fetcher.close()

    assert len(candles) == 2
    assert candles[0].open == 100.0
    assert candles[0].close == 101.0
    # URL is /apim/historical/v1/nse_cm/11536/1?from=...&to=...
    assert "/historical/v1/nse_cm/11536/1" in seen_urls[0]
    assert "from=" in seen_urls[0] and "to=" in seen_urls[0]
    # Auth headers wired.
    h = seen_headers[0]
    assert h.get("sid") == "SID"
    assert h.get("auth") == "TOK"
    assert h.get("neo-fin-key") == "neotradeapi"


@pytest.mark.asyncio
async def test_kotak_neo_fetcher_full_auth_flow_then_candles():
    """OAuth → login → historical, all in one flow."""
    call_order: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            call_order.append("oauth")
            return httpx.Response(200, json={"access_token": "VIEW"})
        if request.url.path.endswith("/login/v6/validate"):
            call_order.append("login")
            return httpx.Response(
                200, json={"data": {"token": "SESSION", "sid": "SID42", "ucc": "U"}}
            )
        if "/historical/v1/" in request.url.path:
            call_order.append("historical")
            return httpx.Response(
                200,
                json={
                    "data": {
                        "candles": [[1_700_000_000, 1, 2, 0.5, 1.5, 10]]
                    }
                },
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = KotakNeoHistoricalFetcher(
        credentials={
            "consumer_key": "CK",
            "consumer_secret": "CS",
            "mobile_number": "+919999999999",
            "mpin": "1234",
        },
        symbol_map={"TCS": ("11536", "nse_cm")},
        http_client=client,
    )
    candles = await fetcher.get_candles(
        "TCS",
        Interval.ONE_MIN,
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    await fetcher.close()

    assert len(candles) == 1
    assert call_order == ["oauth", "login", "historical"]


@pytest.mark.asyncio
async def test_kotak_neo_fetcher_reauth_on_401():
    """When historical returns 401, fetcher should re-auth and retry once."""
    state = {"hist_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "VIEW"})
        if request.url.path.endswith("/login/v6/validate"):
            return httpx.Response(
                200, json={"data": {"token": "SESSION", "sid": "SID42"}}
            )
        if "/historical/v1/" in request.url.path:
            state["hist_calls"] += 1
            if state["hist_calls"] == 1:
                return httpx.Response(401, text="expired")
            return httpx.Response(
                200,
                json={"data": {"candles": [[1_700_000_000, 1, 2, 0.5, 1.5, 10]]}},
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = KotakNeoHistoricalFetcher(
        credentials={
            "consumer_key": "CK",
            "consumer_secret": "CS",
            "mobile_number": "+91",
            "mpin": "1234",
        },
        symbol_map={"TCS": ("11536", "nse_cm")},
        http_client=client,
    )
    candles = await fetcher.get_candles(
        "TCS",
        Interval.ONE_MIN,
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    await fetcher.close()
    assert len(candles) == 1
    assert state["hist_calls"] == 2  # 401 then success


@pytest.mark.asyncio
async def test_kotak_neo_fetcher_error_response_raises_engine_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = KotakNeoHistoricalFetcher(
        credentials={"sid": "S", "session_token": "T"},
        symbol_map={"TCS": ("11536", "nse_cm")},
        http_client=client,
    )
    with pytest.raises(EngineError) as exc:
        await fetcher.get_candles(
            "TCS",
            Interval.ONE_MIN,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert exc.value.code == "kotak_historical_failed"
    await fetcher.close()


# --------------------------------------------------- BacktestService wiring


@pytest.mark.asyncio
async def test_backtest_service_default_uses_synthetic_when_no_fetcher():
    """Without a fetcher, ``_load_candles`` falls back to the synthetic
    generator — exactly the pre-existing (Module 5) behaviour.
    """
    from app.services.backtest_service import _load_candles
    from app.engine.strategy.orb_params import OrbParams

    params = OrbParams.from_dict({"symbols": ["TCS"]})
    candles = await _load_candles(
        symbols=["TCS"],
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 3, tzinfo=timezone.utc),
        params=params,
        fetcher=None,
    )
    assert len(candles) > 0
    assert candles[0].symbol == "TCS"


@pytest.mark.asyncio
async def test_backtest_service_uses_supplied_fetcher():
    """When a fetcher IS supplied, ``_load_candles`` uses it instead of the
    synthetic generator.
    """
    from app.services.backtest_service import _load_candles
    from app.engine.strategy.orb_params import OrbParams

    class _CountingFetcher(HistoricalCandleFetcher):
        name = "counting"

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_candles(self, symbol, interval, start, end, exchange=""):
            self.calls.append(symbol)
            return [
                Candle(
                    symbol=symbol,
                    exchange=exchange or "NSE",
                    interval=interval,
                    ts=start,
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1000.0,
                )
            ]

    fetcher = _CountingFetcher()
    params = OrbParams.from_dict({"symbols": ["A", "B"]})
    candles = await _load_candles(
        symbols=["A", "B"],
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 2, tzinfo=timezone.utc),
        params=params,
        fetcher=fetcher,
        interval=Interval.ONE_MIN,
    )
    assert fetcher.calls == ["A", "B"]
    assert len(candles) == 2
