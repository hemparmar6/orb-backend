"""Broker instrument masters — DTO, loaders, cache, refresh scheduler.

Covers:
- ``Instrument`` + ``InstrumentMaster`` lookup + ``to_ws_symbol_map`` export.
- ``DhanInstrumentLoader`` parses the public scrip-master CSV.
- ``KotakInstrumentLoader`` parses the JSON scrip-master payload (both
  ``sid+session_token`` and pre-fetched-payload modes).
- ``CachedInstrumentLoader`` writes + reads its JSON cache with TTL.
- ``RefreshScheduler`` initial fetch + on_update callback + failure counting
  + ``refresh_now()``.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.brokers.instruments import (
    CachedInstrumentLoader,
    DhanInstrumentLoader,
    Instrument,
    InstrumentKind,
    InstrumentLoader,
    InstrumentMaster,
    KotakInstrumentLoader,
    RefreshScheduler,
    get_loader,
    list_loaders,
)
from app.core.exceptions import EngineError


# ============================================================ InstrumentMaster


def test_registry_has_both_brokers():
    assert set(list_loaders()) == {"dhan", "kotak_neo", "upstox"}


def test_master_lookup_by_symbol_and_token():
    master = InstrumentMaster(
        broker="dhan",
        instruments=[
            Instrument(symbol="TCS", token="11536", exchange_segment="NSE_EQ"),
            Instrument(symbol="INFY", token="1594", exchange_segment="NSE_EQ"),
            Instrument(
                symbol="TCS",
                token="99999",
                exchange_segment="BSE_EQ",
                exchange="BSE",
            ),
        ],
    )
    # By symbol, first match wins across exchanges.
    inst = master.lookup("TCS")
    assert inst is not None and inst.token == "11536"
    # Scoped to BSE.
    inst_bse = master.lookup("TCS", exchange="BSE")
    assert inst_bse is not None and inst_bse.token == "99999"
    # Missing.
    assert master.lookup("ZZZ") is None
    # By token+segment.
    by_tok = master.lookup_by_token("1594", "NSE_EQ")
    assert by_tok is not None and by_tok.symbol == "INFY"


def test_master_to_ws_symbol_map_filters_and_dedupes():
    master = InstrumentMaster(
        broker="dhan",
        instruments=[
            Instrument(symbol="TCS", token="11536", exchange_segment="NSE_EQ"),
            Instrument(symbol="TCS", token="88888", exchange_segment="NSE_FNO"),
            Instrument(symbol="INFY", token="1594", exchange_segment="NSE_EQ"),
        ],
    )
    # All symbols.
    full = master.to_ws_symbol_map()
    assert full == {"TCS": ("11536", "NSE_EQ"), "INFY": ("1594", "NSE_EQ")}
    # Filtered.
    filt = master.to_ws_symbol_map(symbols=["INFY"])
    assert filt == {"INFY": ("1594", "NSE_EQ")}


def test_master_stats_counts_by_kind_and_segment():
    master = InstrumentMaster(
        broker="dhan",
        instruments=[
            Instrument(symbol="A", token="1", exchange_segment="NSE_EQ"),
            Instrument(symbol="B", token="2", exchange_segment="NSE_EQ"),
            Instrument(
                symbol="C",
                token="3",
                exchange_segment="NSE_FNO",
                kind=InstrumentKind.FUTURE,
            ),
        ],
    )
    s = master.stats()
    assert s["total"] == 3
    assert s["by_kind:EQUITY"] == 2
    assert s["by_kind:FUTURE"] == 1
    assert s["by_segment:NSE_EQ"] == 2


# ============================================================= Dhan loader


DHAN_CSV_SAMPLE = (
    "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,"
    "SEM_EXCH_INSTRUMENT_TYPE,SEM_LOT_UNITS,SEM_TICK_SIZE,SEM_ISIN,"
    "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_CUSTOM_SYMBOL\n"
    "11536,TCS,NSE,E,EQUITY,1,0.05,INE467B01029,,,,\n"
    "1594,INFY,NSE,E,EQUITY,1,0.05,INE009A01021,,,,\n"
    "58330,NIFTY 26DEC 21000 CE,NSE,D,OPTIDX,25,0.05,,26/12/2024,21000,CE,NIFTY\n"
    ",BADROW,NSE,E,EQUITY,1,0.05,,,,,\n"  # missing token — should be skipped
)


@pytest.mark.asyncio
async def test_dhan_loader_parses_injected_csv():
    loader = DhanInstrumentLoader(csv_text=DHAN_CSV_SAMPLE)
    master = await loader.fetch()
    assert master.broker == "dhan"
    assert len(master.instruments) == 3  # the row without token is skipped
    tcs = master.lookup("TCS")
    assert tcs is not None
    assert tcs.token == "11536"
    assert tcs.exchange_segment == "NSE_EQ"
    assert tcs.isin == "INE467B01029"
    opt = master.lookup("NIFTY 26DEC 21000 CE")
    assert opt is not None
    assert opt.kind == InstrumentKind.OPTION
    assert opt.exchange_segment == "NSE_FNO"
    assert opt.expiry == date(2024, 12, 26)
    assert opt.strike == 21000.0
    assert opt.option_type == "CE"
    assert opt.underlying == "NIFTY"
    assert opt.lot_size == 25


@pytest.mark.asyncio
async def test_dhan_loader_downloads_from_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api-scrip-master.csv" in str(request.url)
        return httpx.Response(200, text=DHAN_CSV_SAMPLE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    loader = DhanInstrumentLoader(http_client=client)
    master = await loader.fetch()
    await loader.close()
    assert len(master.instruments) == 3


@pytest.mark.asyncio
async def test_dhan_loader_error_raises_engine_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    loader = DhanInstrumentLoader(http_client=client)
    with pytest.raises(EngineError):
        await loader.fetch()
    await loader.close()


# ================================================== Kotak Neo loader


KOTAK_PAYLOAD_SAMPLE = {
    "data": [
        {
            "pSymbol": "11536",
            "pTrdSymbol": "TCS",
            "pExchSeg": "nse_cm",
            "pExch": "NSE",
            "pInstType": "EQUITY",
            "pLotSize": "1",
            "pTickSize": "0.05",
            "pISIN": "INE467B01029",
        },
        {
            "pSymbol": "58330",
            "pTrdSymbol": "NIFTY 26DEC 21000 CE",
            "pExchSeg": "nse_fo",
            "pExch": "NSE",
            "pInstType": "OPTIDX",
            "pLotSize": "25",
            "pTickSize": "0.05",
            "pExpiryDate": "26DEC2024",
            "pStrikePrice": "21000",
            "pOptType": "CE",
            "pAssetName": "NIFTY",
        },
        {
            # missing token → skipped
            "pTrdSymbol": "ORPHAN",
            "pExchSeg": "nse_cm",
        },
    ]
}


@pytest.mark.asyncio
async def test_kotak_loader_parses_injected_payload():
    loader = KotakInstrumentLoader(
        credentials={"sid": "S", "session_token": "T"},
        json_payload=KOTAK_PAYLOAD_SAMPLE,
    )
    master = await loader.fetch()
    assert master.broker == "kotak_neo"
    assert len(master.instruments) == 2
    tcs = master.lookup("TCS")
    assert tcs is not None and tcs.token == "11536"
    assert tcs.exchange_segment == "nse_cm"
    opt = master.lookup("NIFTY 26DEC 21000 CE")
    assert opt is not None and opt.kind == InstrumentKind.OPTION
    assert opt.strike == 21000.0
    assert opt.expiry == date(2024, 12, 26)


@pytest.mark.asyncio
async def test_kotak_loader_pre_signed_downloads_masters():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=KOTAK_PAYLOAD_SAMPLE)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    loader = KotakInstrumentLoader(
        credentials={"sid": "SID", "session_token": "TOK", "view_token": "VIEW"},
        segments=("nse_cm", "nse_fo"),
        http_client=client,
    )
    master = await loader.fetch()
    await loader.close()
    assert len(master.instruments) == 4  # 2 rows * 2 segments (skipped ones)
    assert len(seen) == 2
    assert "/scripmaster/nse_cm" in seen[0]
    assert "/scripmaster/nse_fo" in seen[1]


@pytest.mark.asyncio
async def test_kotak_loader_full_auth_then_master():
    order: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            order.append("oauth")
            return httpx.Response(200, json={"access_token": "VIEW"})
        if request.url.path.endswith("/login/v6/validate"):
            order.append("login")
            return httpx.Response(
                200, json={"data": {"token": "S", "sid": "SID42"}}
            )
        if "/scripmaster/" in request.url.path:
            order.append("master")
            return httpx.Response(200, json=KOTAK_PAYLOAD_SAMPLE)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    loader = KotakInstrumentLoader(
        credentials={
            "consumer_key": "CK",
            "consumer_secret": "CS",
            "mobile_number": "+91",
            "mpin": "1234",
        },
        segments=("nse_cm",),
        http_client=client,
    )
    master = await loader.fetch()
    await loader.close()
    assert order == ["oauth", "login", "master"]
    assert len(master.instruments) == 2


def test_kotak_loader_credentials_validation():
    with pytest.raises(ValueError):
        KotakInstrumentLoader(credentials={})


# ==================================================== CachedInstrumentLoader


class _StubLoader(InstrumentLoader):
    name = "stub"

    def __init__(self, instruments: list[Instrument]) -> None:
        self._instruments = instruments
        self.calls = 0

    async def fetch(self) -> InstrumentMaster:
        self.calls += 1
        return InstrumentMaster(broker="stub", instruments=list(self._instruments))


@pytest.mark.asyncio
async def test_cached_loader_persists_and_reuses(tmp_path: Path):
    stub = _StubLoader(
        [
            Instrument(symbol="TCS", token="11536", exchange_segment="NSE_EQ"),
            Instrument(symbol="INFY", token="1594", exchange_segment="NSE_EQ"),
        ]
    )
    cached = CachedInstrumentLoader(stub, cache_dir=str(tmp_path), ttl_s=3600)
    a = await cached.fetch()
    b = await cached.fetch()  # second call must hit the cache
    assert len(a.instruments) == 2
    assert len(b.instruments) == 2
    assert stub.calls == 1  # cache prevented a second fetch
    # File was written.
    assert (tmp_path / "stub.json").exists()


@pytest.mark.asyncio
async def test_cached_loader_expires_when_ttl_elapsed(tmp_path: Path):
    stub = _StubLoader(
        [Instrument(symbol="A", token="1", exchange_segment="NSE_EQ")]
    )
    cached = CachedInstrumentLoader(stub, cache_dir=str(tmp_path), ttl_s=0.0)
    await cached.fetch()
    # ttl=0 → any subsequent fetch bypasses the cache.
    await cached.fetch()
    assert stub.calls == 2


@pytest.mark.asyncio
async def test_cached_loader_force_refresh(tmp_path: Path):
    stub = _StubLoader(
        [Instrument(symbol="A", token="1", exchange_segment="NSE_EQ")]
    )
    cached = CachedInstrumentLoader(stub, cache_dir=str(tmp_path), ttl_s=3600)
    await cached.fetch()
    cached.force_refresh()
    await cached.fetch()
    assert stub.calls == 2


# ======================================================== RefreshScheduler


@pytest.mark.asyncio
async def test_refresh_scheduler_initial_fetch_and_on_update():
    stub = _StubLoader(
        [Instrument(symbol="TCS", token="11536", exchange_segment="NSE_EQ")]
    )
    received: list[InstrumentMaster] = []
    sched = RefreshScheduler(
        stub,
        interval_s=3600,  # long — we won't wait for the loop's tick
        on_update=lambda m: received.append(m),
    )
    await sched.start()
    await sched.stop()

    assert stub.calls == 1
    assert sched.current() is not None
    assert len(received) == 1
    stats = sched.get_stats()
    assert stats["refresh_count"] == 1
    assert stats["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_refresh_scheduler_refresh_now_forces_reload():
    stub = _StubLoader(
        [Instrument(symbol="A", token="1", exchange_segment="NSE_EQ")]
    )
    sched = RefreshScheduler(stub, interval_s=3600)
    await sched.start()
    await sched.refresh_now()
    await sched.stop()
    assert stub.calls == 2


@pytest.mark.asyncio
async def test_refresh_scheduler_failures_do_not_crash_scheduler():
    """When the loader raises, the scheduler catches, counts the failure,
    keeps the old master, and continues serving.
    """

    class _FailAfterOne(InstrumentLoader):
        name = "fail_after_one"

        def __init__(self) -> None:
            self.calls = 0

        async def fetch(self) -> InstrumentMaster:
            self.calls += 1
            if self.calls == 1:
                return InstrumentMaster(
                    broker="stub",
                    instruments=[
                        Instrument(symbol="X", token="1", exchange_segment="NSE_EQ")
                    ],
                )
            raise RuntimeError("boom")

    loader = _FailAfterOne()
    sched = RefreshScheduler(loader, interval_s=3600)
    await sched.start()
    good = sched.current()
    await sched.refresh_now()  # this will raise inside the scheduler and be swallowed
    still_current = sched.current()
    await sched.stop()

    assert loader.calls == 2
    assert sched.get_stats()["consecutive_failures"] == 1
    # Old master preserved — no partial state.
    assert still_current is good
