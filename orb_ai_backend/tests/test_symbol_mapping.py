"""Automatic symbol mapping — instrument master → provider symbol_map.

Verifies the full end-to-end pipeline: an :class:`InstrumentMaster` produced
by a loader can be plugged directly into a real-time market-data provider
and a historical fetcher without any manual translation.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.brokers.instruments import (
    DhanInstrumentLoader,
    InstrumentMaster,
    KotakInstrumentLoader,
)
from app.brokers.websocket import reconnect as reconnect_module
from app.engine.market_data import (
    DhanMarketDataProvider,
    KotakNeoMarketDataProvider,
)


DHAN_CSV_SAMPLE = (
    "SEM_SMST_SECURITY_ID,SEM_TRADING_SYMBOL,SEM_EXM_EXCH_ID,SEM_SEGMENT,"
    "SEM_EXCH_INSTRUMENT_TYPE,SEM_LOT_UNITS,SEM_TICK_SIZE,SEM_ISIN,"
    "SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_CUSTOM_SYMBOL\n"
    "11536,TCS,NSE,E,EQUITY,1,0.05,INE467B01029,,,,\n"
    "1594,INFY,NSE,E,EQUITY,1,0.05,INE009A01021,,,,\n"
)

KOTAK_PAYLOAD = {
    "data": [
        {
            "pSymbol": "11536",
            "pTrdSymbol": "TCS",
            "pExchSeg": "nse_cm",
            "pExch": "NSE",
            "pInstType": "EQUITY",
            "pLotSize": "1",
            "pTickSize": "0.05",
        },
        {
            "pSymbol": "1594",
            "pTrdSymbol": "INFY",
            "pExchSeg": "nse_cm",
            "pExch": "NSE",
            "pInstType": "EQUITY",
            "pLotSize": "1",
            "pTickSize": "0.05",
        },
    ]
}


class _FakeWS:
    def __init__(self, frames: list[Any]) -> None:
        self._frames = list(frames)
        self.sent: list[Any] = []

    async def __aenter__(self) -> "_FakeWS":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    def __aiter__(self) -> "_FakeWS":
        return self

    async def __anext__(self) -> Any:
        if not self._frames:
            raise reconnect_module.ConnectionClosed(None, None)
        return self._frames.pop(0)


@pytest.mark.asyncio
async def test_dhan_master_symbol_map_plugs_into_ws_provider(monkeypatch):
    """Full pipeline: DhanInstrumentLoader → InstrumentMaster.to_ws_symbol_map
    → DhanMarketDataProvider — the provider must be able to subscribe to a
    symbol using the auto-generated map."""
    loader = DhanInstrumentLoader(csv_text=DHAN_CSV_SAMPLE)
    master = await loader.fetch()
    smap = master.to_ws_symbol_map(symbols=["TCS", "INFY"])
    assert smap == {"TCS": ("11536", "NSE_EQ"), "INFY": ("1594", "NSE_EQ")}

    # Simulate one tick for TCS on the wire.
    ws = _FakeWS(
        [
            json.dumps(
                {
                    "securityId": "11536",
                    "exchangeSegment": "NSE_EQ",
                    "LTP": 3100.5,
                    "LTQ": 10,
                    "LTT": 1_700_000_000,
                }
            )
        ]
    )
    monkeypatch.setattr(
        reconnect_module.websockets, "connect", lambda *a, **k: ws
    )

    provider = DhanMarketDataProvider(
        credentials={"client_id": "C1", "access_token": "0123456789abcdef"},
        symbol_map=smap,  # ← auto-mapped
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
        max_consecutive_failures=1,
    )
    await provider.subscribe(["TCS"])
    await provider.start()
    async for q in provider.stream():
        assert q.symbol == "TCS"
        assert q.exchange == "NSE_EQ"
        assert q.price == 3100.5
        break
    await provider.stop()


@pytest.mark.asyncio
async def test_kotak_master_symbol_map_plugs_into_ws_provider(monkeypatch):
    loader = KotakInstrumentLoader(
        credentials={"sid": "S", "session_token": "T"},
        json_payload=KOTAK_PAYLOAD,
    )
    master = await loader.fetch()
    smap = master.to_ws_symbol_map()
    assert smap == {"TCS": ("11536", "nse_cm"), "INFY": ("1594", "nse_cm")}

    ws = _FakeWS(
        [
            json.dumps(
                {
                    "e": "quote",
                    "tk": "1594",
                    "seg": "nse_cm",
                    "ltp": "1520.5",
                    "ltt": "1700000000",
                }
            )
        ]
    )
    monkeypatch.setattr(
        reconnect_module.websockets, "connect", lambda *a, **k: ws
    )

    provider = KotakNeoMarketDataProvider(
        credentials={"sid": "S", "session_token": "T"},
        symbol_map=smap,
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
        max_consecutive_failures=1,
    )
    await provider.subscribe(["INFY"])
    await provider.start()
    async for q in provider.stream():
        assert q.symbol == "INFY"
        assert q.exchange == "nse_cm"
        assert q.price == 1520.5
        break
    await provider.stop()


@pytest.mark.asyncio
async def test_scheduler_populated_master_can_drive_provider_add_symbols():
    """Simulate the full production wiring: a RefreshScheduler owns the
    master, its ``on_update`` callback pushes the fresh symbol map into a
    provider's ``add_symbols``. After a scheduler refresh, the provider knows
    about the new symbol.
    """
    from app.brokers.instruments import RefreshScheduler

    class _StubLoader:
        name = "dhan"

        def __init__(self, master: InstrumentMaster) -> None:
            self._master = master

        async def fetch(self) -> InstrumentMaster:
            return self._master

        async def close(self) -> None:
            pass

    provider = DhanMarketDataProvider(
        credentials={"client_id": "C1", "access_token": "TOK"},
    )
    assert "TCS" not in provider._symbol_map  # noqa: SLF001

    loader = DhanInstrumentLoader(csv_text=DHAN_CSV_SAMPLE)
    master = await loader.fetch()

    sched = RefreshScheduler(
        _StubLoader(master),
        interval_s=3600,
        on_update=lambda m: provider.add_symbols(m.to_ws_symbol_map()),
    )
    await sched.start()
    await sched.stop()

    # After the scheduler ran its initial refresh, the provider knows both
    # symbols from the master.
    assert provider._symbol_map["TCS"] == ("11536", "NSE_EQ")  # noqa: SLF001
    assert provider._symbol_map["INFY"] == ("1594", "NSE_EQ")  # noqa: SLF001
