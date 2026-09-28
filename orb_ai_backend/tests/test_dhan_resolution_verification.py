"""Independent verification of Dhan server-side symbol resolution.

READ-ONLY: no application source is modified. Boundary calls to Dhan HTTP
are mocked via httpx.MockTransport. No real broker credentials or trades.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.brokers.instruments import DhanInstrumentLoader
from app.brokers.instruments.base import Instrument, InstrumentKind
from app.core.config import settings
from app.core.exceptions import EngineError
from app.engine.market_data import instrument_runtime
from app.engine.market_data.base import Interval
from app.engine.market_data.dhan_historical import (
    DhanHistoricalFetcher,
    _DHAN_SUPPORTED_INTERVALS,
)


DHAN_CSV = (
    "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,"
    "SEM_EXCH_INSTRUMENT_TYPE,SEM_LOT_UNITS,SEM_TICK_SIZE,SEM_ISIN,"
    "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_CUSTOM_SYMBOL\n"
    "11536,TCS,NSE,E,EQUITY,1,0.05,INE467B01029,,,,\n"
    "99999,NIFTY,NSE,I,INDEX,1,0.05,,,,,,\n"
    "88888,NIFTY-FUT,NSE,D,FUTIDX,50,0.05,,,,,,NIFTY\n"
    "77777,CRUDE-FUT,MCX,M,FUTCOM,100,0.05,,,,,,CRUDE\n"
)


def _master():
    return asyncio.run(DhanInstrumentLoader(csv_text=DHAN_CSV).fetch())


# ---------------------------------------------------------------------------
# 1) Loader parses raw Dhan instrument types and segments
# ---------------------------------------------------------------------------
class TestLoaderParsing:
    def test_tcs_equity(self):
        m = _master()
        r = m.lookup("TCS", exchange="NSE")
        assert r is not None
        assert r.token == "11536"
        assert r.exchange_segment == "NSE_EQ"
        assert r.instrument_type == "EQUITY"

    def test_nifty_fut_futidx(self):
        m = _master()
        r = m.lookup("NIFTY-FUT", exchange="NSE")
        assert r is not None
        assert r.token == "88888"
        assert r.exchange_segment == "NSE_FNO"
        assert r.instrument_type == "FUTIDX"

    def test_crude_fut_mcx_comm(self):
        m = _master()
        r = m.lookup("CRUDE-FUT", exchange="MCX")
        assert r is not None
        assert r.exchange_segment == "MCX_COMM"
        assert r.instrument_type == "FUTCOM"


# ---------------------------------------------------------------------------
# 2) instrument_runtime.resolve_dhan_instrument
# ---------------------------------------------------------------------------
class TestRuntimeResolve:
    def test_resolve_tcs(self, monkeypatch):
        master = _master()
        runtime = instrument_runtime.dhan_instrument_runtime
        monkeypatch.setattr(runtime, "_scheduler",
                            SimpleNamespace(current=lambda: master))
        r = instrument_runtime.resolve_dhan_instrument("TCS", "NSE")
        assert r is not None and r.token == "11536"

    def test_resolve_nifty_fut_via_nfo_alias(self, monkeypatch):
        master = _master()
        runtime = instrument_runtime.dhan_instrument_runtime
        monkeypatch.setattr(runtime, "_scheduler",
                            SimpleNamespace(current=lambda: master))
        r = instrument_runtime.resolve_dhan_instrument("NIFTY-FUT", "NFO")
        assert r is not None
        assert r.instrument_type == "FUTIDX"
        assert r.exchange == "NSE"
        assert "FNO" in r.exchange_segment


# ---------------------------------------------------------------------------
# 3) DhanHistoricalFetcher body includes resolved per-symbol values
# ---------------------------------------------------------------------------
class TestFetcherBody:
    @pytest.mark.asyncio
    async def test_body_uses_instrument_map_futidx(self):
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured.update(json.loads(req.content or b"{}"))
            return httpx.Response(200, json={
                "open": [1.0], "high": [1.0], "low": [1.0],
                "close": [1.0], "volume": [1], "timestamp": [1_700_000_000],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        f = DhanHistoricalFetcher(
            credentials={"client_id": "CID", "access_token": "TOKEN"},
            symbol_map={"NIFTY-FUT": ("88888", "NSE_FNO")},
            instrument_map={"NIFTY-FUT": "FUTIDX"},
            http_client=client,
        )
        await f.get_candles(
            "NIFTY-FUT", Interval.FIFTEEN_MIN,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        await f.close()
        assert captured["securityId"] == "88888"
        assert captured["exchangeSegment"] == "NSE_FNO"
        assert captured["instrument"] == "FUTIDX"
        assert captured["interval"] == "15"

    @pytest.mark.asyncio
    async def test_body_defaults_to_equity_when_no_instrument_map(self):
        captured: dict[str, Any] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured.update(json.loads(req.content or b"{}"))
            return httpx.Response(200, json={
                "open": [1.0], "high": [1.0], "low": [1.0],
                "close": [1.0], "volume": [1], "timestamp": [1_700_000_000],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        f = DhanHistoricalFetcher(
            credentials={"client_id": "CID", "access_token": "TOKEN"},
            symbol_map={"TCS": ("11536", "NSE_EQ")},
            http_client=client,
        )
        await f.get_candles(
            "TCS", Interval.ONE_MIN,
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        await f.close()
        assert captured["instrument"] == "EQUITY"

    @pytest.mark.asyncio
    async def test_three_min_unsupported(self):
        f = DhanHistoricalFetcher(
            credentials={"client_id": "CID", "access_token": "TOKEN"},
            symbol_map={"TCS": ("11536", "NSE_EQ")},
        )
        with pytest.raises(EngineError) as exc:
            await f.get_candles(
                "TCS", Interval.THREE_MIN,
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
        assert exc.value.code == "unsupported_dhan_interval"
        await f.close()

    def test_supported_intervals_set(self):
        # Only these five intervals are supported by Dhan
        assert _DHAN_SUPPORTED_INTERVALS == {
            Interval.ONE_MIN, Interval.FIVE_MIN, Interval.FIFTEEN_MIN,
            Interval.ONE_HOUR, Interval.ONE_DAY,
        }
        # 30m / 2H / 45m aren't representable in the Interval enum at all
        # (which itself proves they can't reach the fetcher):
        interval_names = {e.name for e in Interval}
        assert "THIRTY_MIN" not in interval_names
        assert "TWO_HOUR" not in interval_names
        assert "FORTY_FIVE_MIN" not in interval_names


# ---------------------------------------------------------------------------
# 4) Endpoint _dhan_candles resolves via master, ignoring caller security_id
# ---------------------------------------------------------------------------
def _patch_dhan_configured(monkeypatch):
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "dhan", raising=False)
    monkeypatch.setattr(settings, "DHAN_CLIENT_ID", "CID", raising=False)
    monkeypatch.setattr(settings, "DHAN_ACCESS_TOKEN", "TOKEN", raising=False)


def _resolved_tcs():
    return Instrument(
        symbol="TCS", token="SERVER_TOKEN", exchange_segment="NSE_EQ",
        exchange="NSE", kind=InstrumentKind.EQUITY, instrument_type="EQUITY",
    )


class _FakeCandle:
    def __init__(self, ts):
        self.ts = ts
        self.open = 100.0
        self.high = 101.0
        self.low = 99.0
        self.close = 100.5
        self.volume = 10.0


class TestEndpointDhanCandles:
    @pytest.mark.asyncio
    async def test_no_caller_security_id(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint
        import app.engine.market_data.historical_base as historical_base

        captured: dict[str, Any] = {}

        class FakeFetcher:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def get_candles(self, symbol, interval, start, end, exchange=""):
                return [_FakeCandle(end)]

            async def close(self): pass

        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint.instrument_runtime,
                            "resolve_dhan_instrument",
                            lambda *_: _resolved_tcs())
        monkeypatch.setattr(historical_base, "get_historical",
                            lambda name, **kw: FakeFetcher(**kw))

        result = await endpoint._dhan_candles(
            symbol="TCS", exchange="NSE", timeframe="1m", count=1,
            end_dt=datetime.now(timezone.utc), security_id=None,
        )
        assert result is not None
        assert captured["symbol_map"] == {"TCS": ("SERVER_TOKEN", "NSE_EQ")}
        assert captured["instrument_map"] == {"TCS": "EQUITY"}

    @pytest.mark.asyncio
    async def test_caller_security_id_is_ignored(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint
        import app.engine.market_data.historical_base as historical_base

        captured: dict[str, Any] = {}

        class FakeFetcher:
            def __init__(self, **kwargs): captured.update(kwargs)
            async def get_candles(self, *a, **kw): return [_FakeCandle(datetime.now(timezone.utc))]
            async def close(self): pass

        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint.instrument_runtime,
                            "resolve_dhan_instrument",
                            lambda *_: _resolved_tcs())
        monkeypatch.setattr(historical_base, "get_historical",
                            lambda name, **kw: FakeFetcher(**kw))

        result = await endpoint._dhan_candles(
            symbol="TCS", exchange="NSE", timeframe="1m", count=1,
            end_dt=datetime.now(timezone.utc), security_id="CALLER_JUNK",
        )
        assert result is not None
        assert captured["symbol_map"] == {"TCS": ("SERVER_TOKEN", "NSE_EQ")}


# ---------------------------------------------------------------------------
# 5) get_tick works without caller security_id + proves REST-1m-derived
# ---------------------------------------------------------------------------
class TestEndpointGetTick:
    @pytest.mark.asyncio
    async def test_tick_from_dhan(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint
        import app.engine.market_data.historical_base as historical_base

        captured: dict[str, Any] = {}

        class FakeFetcher:
            def __init__(self, **kwargs): captured.update(kwargs)
            async def get_candles(self, symbol, interval, start, end, exchange=""):
                c = _FakeCandle(end)
                return [c]
            async def close(self): pass

        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint.instrument_runtime,
                            "resolve_dhan_instrument",
                            lambda *_: _resolved_tcs())
        monkeypatch.setattr(historical_base, "get_historical",
                            lambda name, **kw: FakeFetcher(**kw))

        tick = await endpoint.get_tick(user=object(), symbol="TCS", exchange="NSE")
        assert tick.source == "dhan"
        assert tick.price == 100.5
        assert captured["symbol_map"] == {"TCS": ("SERVER_TOKEN", "NSE_EQ")}

    @pytest.mark.asyncio
    async def test_tick_calls_dhan_candles_with_1m_count1(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint

        captured_kwargs: dict[str, Any] = {}

        async def fake_dhan_candles(**kwargs):
            captured_kwargs.update(kwargs)
            return [[1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 10.0]]

        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint, "_dhan_candles", fake_dhan_candles)

        tick = await endpoint.get_tick(user=object(), symbol="TCS", exchange="NSE")
        assert tick.source == "dhan"
        assert captured_kwargs["timeframe"] == "1m"
        assert captured_kwargs["count"] == 1


# ---------------------------------------------------------------------------
# 6) Source honesty: none of these must ever return source="dhan"
# ---------------------------------------------------------------------------
class TestSourceHonesty:
    @pytest.mark.asyncio
    async def test_a_dhan_not_configured(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint
        monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "mock", raising=False)
        result = await endpoint._dhan_candles(
            symbol="TCS", exchange="NSE", timeframe="1m", count=1,
            end_dt=datetime.now(timezone.utc), security_id=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_b_resolver_returns_none(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint
        import app.engine.market_data.historical_base as historical_base

        called = {"v": False}

        def should_not_call(*a, **kw):
            called["v"] = True
            raise AssertionError("must not call get_historical")

        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint.instrument_runtime,
                            "resolve_dhan_instrument", lambda *_: None)
        monkeypatch.setattr(historical_base, "get_historical", should_not_call)
        result = await endpoint._dhan_candles(
            symbol="UNKNOWN", exchange="NSE", timeframe="1m", count=1,
            end_dt=datetime.now(timezone.utc), security_id=None,
        )
        assert result is None
        assert called["v"] is False

    @pytest.mark.asyncio
    async def test_c_option_kind_without_instrument_type(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint
        import app.engine.market_data.historical_base as historical_base

        opt = Instrument(
            symbol="X", token="1", exchange_segment="NSE_FNO",
            exchange="NSE", kind=InstrumentKind.OPTION, instrument_type=None,
        )
        called = {"v": False}

        def should_not_call(*a, **kw):
            called["v"] = True
            raise AssertionError("must not call get_historical")

        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint.instrument_runtime,
                            "resolve_dhan_instrument", lambda *_: opt)
        monkeypatch.setattr(historical_base, "get_historical", should_not_call)

        # Also verify _dhan_instrument_type returns None
        assert endpoint._dhan_instrument_type(opt) is None

        result = await endpoint._dhan_candles(
            symbol="X", exchange="NSE", timeframe="1m", count=1,
            end_dt=datetime.now(timezone.utc), security_id=None,
        )
        assert result is None
        assert called["v"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tf", ["3m", "30m", "45m", "2H", "4H", "1W", "1M"])
    async def test_d_unsupported_timeframe(self, monkeypatch, tf):
        from app.api.v1.endpoints import market_data as endpoint
        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint.instrument_runtime,
                            "resolve_dhan_instrument",
                            lambda *_: _resolved_tcs())
        result = await endpoint._dhan_candles(
            symbol="TCS", exchange="NSE", timeframe=tf, count=1,
            end_dt=datetime.now(timezone.utc), security_id=None,
        )
        assert result is None
        # And confirm the tf is still in _TF_SECONDS (endpoint returns 200 mock)
        assert tf in endpoint._TF_SECONDS

    @pytest.mark.asyncio
    async def test_e_fetcher_raises(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint
        import app.engine.market_data.historical_base as historical_base

        class BoomFetcher:
            def __init__(self, **kw): pass
            async def get_candles(self, *a, **kw): raise RuntimeError("boom")
            async def close(self): pass

        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint.instrument_runtime,
                            "resolve_dhan_instrument",
                            lambda *_: _resolved_tcs())
        monkeypatch.setattr(historical_base, "get_historical",
                            lambda n, **kw: BoomFetcher(**kw))
        result = await endpoint._dhan_candles(
            symbol="TCS", exchange="NSE", timeframe="1m", count=1,
            end_dt=datetime.now(timezone.utc), security_id=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_f_empty_candles(self, monkeypatch):
        from app.api.v1.endpoints import market_data as endpoint
        import app.engine.market_data.historical_base as historical_base

        class EmptyFetcher:
            def __init__(self, **kw): pass
            async def get_candles(self, *a, **kw): return []
            async def close(self): pass

        _patch_dhan_configured(monkeypatch)
        monkeypatch.setattr(endpoint.instrument_runtime,
                            "resolve_dhan_instrument",
                            lambda *_: _resolved_tcs())
        monkeypatch.setattr(historical_base, "get_historical",
                            lambda n, **kw: EmptyFetcher(**kw))
        result = await endpoint._dhan_candles(
            symbol="TCS", exchange="NSE", timeframe="1m", count=1,
            end_dt=datetime.now(timezone.utc), security_id=None,
        )
        assert result is None


# ---------------------------------------------------------------------------
# 7) DHAN_SYMBOL_MAP independence — checked by an external grep in the runner
# ---------------------------------------------------------------------------
def test_no_dhan_symbol_map_in_repo():
    # Search this extracted backend repository with pathlib so the check is
    # independent of GNU grep and the runner's checkout location.
    root = Path(__file__).resolve().parents[1]
    excluded = {".venv", "node_modules", "__pycache__", ".git", "test_reports"}
    matches = []
    for path in root.rglob("*.py"):
        if path.name == Path(__file__).name or excluded.intersection(path.parts):
            continue
        if "DHAN_SYMBOL_MAP" in path.read_text(encoding="utf-8", errors="ignore"):
            matches.append(str(path.relative_to(root)))
    assert matches == [], f"unexpected DHAN_SYMBOL_MAP references: {matches}"
