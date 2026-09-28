"""Focused tests for Dhan instrument-master market-data wiring."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.brokers.instruments import DhanInstrumentLoader, InstrumentMaster
from app.brokers.instruments.base import Instrument, InstrumentKind
from app.core.exceptions import EngineError
from app.core.config import settings
from app.engine.market_data import instrument_runtime
from app.engine.market_data.base import Interval
from app.engine.market_data.dhan_historical import DhanHistoricalFetcher


DHAN_CSV = (
    "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,"
    "SEM_EXCH_INSTRUMENT_TYPE,SEM_LOT_UNITS,SEM_TICK_SIZE,SEM_ISIN,"
    "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_CUSTOM_SYMBOL\n"
    "11536,TCS,NSE,E,EQUITY,1,0.05,INE467B01029,,,,\n"
    "99999,NIFTY,NSE,I,INDEX,1,0.05,,,,,,\n"
    "88888,NIFTY-FUT,NSE,D,FUTIDX,50,0.05,,,,,,NIFTY\n"
    "77777,CRUDE-FUT,MCX,M,FUTCOM,100,0.05,,,,,,CRUDE\n"
)


def _master() -> InstrumentMaster:
    return __import__("asyncio").run(DhanInstrumentLoader(csv_text=DHAN_CSV).fetch())


def test_loader_preserves_raw_dhan_instrument_type_and_lookup():
    master = _master()
    tcs = master.lookup("TCS", exchange="NSE")
    future = master.lookup("NIFTY-FUT", exchange="NSE")
    assert tcs is not None
    assert (tcs.token, tcs.exchange_segment, tcs.instrument_type) == (
        "11536", "NSE_EQ", "EQUITY"
    )
    assert future is not None
    assert (future.token, future.exchange_segment, future.instrument_type) == (
        "88888", "NSE_FNO", "FUTIDX"
    )


def test_runtime_resolves_server_side_metadata(monkeypatch):
    master = _master()
    runtime = instrument_runtime.dhan_instrument_runtime
    monkeypatch.setattr(runtime, "_scheduler", SimpleNamespace(current=lambda: master))
    resolved = instrument_runtime.resolve_dhan_instrument("TCS", "NSE")
    assert resolved is not None
    assert resolved.token == "11536"
    assert resolved.exchange_segment == "NSE_EQ"


@pytest.mark.asyncio
async def test_fetcher_uses_per_symbol_instrument_type_and_segment():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content or b"{}"))
        return httpx.Response(
            200,
            json={
                "open": [100.0], "high": [101.0], "low": [99.0],
                "close": [100.5], "volume": [10], "timestamp": [1_700_000_000],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = DhanHistoricalFetcher(
        credentials={"client_id": "CID", "access_token": "TOKEN"},
        symbol_map={"NIFTY-FUT": ("88888", "NSE_FNO")},
        instrument_map={"NIFTY-FUT": "FUTIDX"},
        http_client=client,
    )
    candles = await fetcher.get_candles(
        "NIFTY-FUT", Interval.FIFTEEN_MIN,
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    await fetcher.close()
    assert len(candles) == 1
    assert captured["securityId"] == "88888"
    assert captured["exchangeSegment"] == "NSE_FNO"
    assert captured["instrument"] == "FUTIDX"


@pytest.mark.asyncio
async def test_unsupported_dhan_interval_is_rejected():
    fetcher = DhanHistoricalFetcher(
        credentials={"client_id": "CID", "access_token": "TOKEN"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
    )
    with pytest.raises(EngineError) as exc:
        await fetcher.get_candles(
            "TCS", Interval.THREE_MIN,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
    assert exc.value.code == "unsupported_dhan_interval"
    await fetcher.close()


@pytest.mark.asyncio
async def test_endpoint_uses_resolved_master_for_candles_and_tick(monkeypatch):
    from app.api.v1.endpoints import market_data as endpoint
    import app.engine.market_data.historical_base as historical_base

    resolved = Instrument(
        symbol="TCS", token="SERVER_TOKEN", exchange_segment="NSE_EQ",
        exchange="NSE", kind=InstrumentKind.EQUITY, instrument_type="EQUITY",
    )
    captured: dict[str, Any] = {}

    class FakeFetcher:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def get_candles(self, symbol, interval, start, end, exchange=""):
            return [
                type("CandleLike", (), {
                    "ts": start, "open": 100.0, "high": 101.0,
                    "low": 99.0, "close": 100.5, "volume": 10.0,
                })()
            ]

        async def close(self):
            pass

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "dhan", raising=False)
    monkeypatch.setattr(settings, "DHAN_CLIENT_ID", "CID", raising=False)
    monkeypatch.setattr(settings, "DHAN_ACCESS_TOKEN", "TOKEN", raising=False)
    monkeypatch.setattr(endpoint.instrument_runtime, "resolve_dhan_instrument", lambda *_: resolved)
    monkeypatch.setattr(historical_base, "get_historical", lambda name, **kwargs: FakeFetcher(**kwargs))

    result = await endpoint._dhan_candles(
        symbol="TCS", exchange="NSE", timeframe="1m", count=1,
        end_dt=datetime.now(timezone.utc), security_id="CALLER_VALUE",
    )
    assert result is not None
    assert captured["symbol_map"] == {"TCS": ("SERVER_TOKEN", "NSE_EQ")}
    assert captured["instrument_map"] == {"TCS": "EQUITY"}

    captured.clear()
    tick = await endpoint.get_tick(
        user=object(), symbol="TCS", exchange="NSE", security_id="CALLER_VALUE"
    )
    assert tick.source == "dhan"
    assert captured["symbol_map"] == {"TCS": ("SERVER_TOKEN", "NSE_EQ")}


@pytest.mark.asyncio
async def test_master_failure_does_not_fabricate_or_call_dhan(monkeypatch):
    from app.api.v1.endpoints import market_data as endpoint
    import app.engine.market_data.historical_base as historical_base

    called = False

    def should_not_call(*args: Any, **kwargs: Any):
        nonlocal called
        called = True
        raise AssertionError("Dhan must not be called without a resolved master instrument")

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "dhan", raising=False)
    monkeypatch.setattr(settings, "DHAN_CLIENT_ID", "CID", raising=False)
    monkeypatch.setattr(settings, "DHAN_ACCESS_TOKEN", "TOKEN", raising=False)
    monkeypatch.setattr(endpoint.instrument_runtime, "resolve_dhan_instrument", lambda *_: None)
    monkeypatch.setattr(historical_base, "get_historical", should_not_call)
    result = await endpoint._dhan_candles(
        symbol="UNKNOWN", exchange="NSE", timeframe="1m", count=1,
        end_dt=datetime.now(timezone.utc), security_id=None,
    )
    assert result is None
    assert called is False