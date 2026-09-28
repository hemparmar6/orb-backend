"""UpstoxInstrumentLoader — parse the instrument master JSON (no network)."""
from __future__ import annotations

import json

import pytest

from app.brokers.instruments import get_loader, upstox_symbol_map
from app.brokers.instruments.base import InstrumentKind
from app.brokers.instruments.upstox import UpstoxInstrumentLoader

_SAMPLE = json.dumps([
    {
        "segment": "NSE_INDEX", "name": "Nifty Bank", "exchange": "NSE",
        "instrument_key": "NSE_INDEX|Nifty Bank", "instrument_type": "INDEX",
        "trading_symbol": "Nifty Bank",
    },
    {
        "segment": "NSE_EQ", "name": "Tata Consultancy Services Ltd",
        "exchange": "NSE", "isin": "INE467B01029", "instrument_type": "EQ",
        "instrument_key": "NSE_EQ|INE467B01029", "lot_size": 1,
        "tick_size": 5.0, "trading_symbol": "TCS",
    },
    {
        "segment": "NSE_FO", "name": "BANKNIFTY", "exchange": "NSE",
        "instrument_key": "NSE_FO|41000", "instrument_type": "PE",
        "trading_symbol": "BANKNIFTY 52000 PE", "lot_size": 15,
        "strike_price": 52000, "underlying_symbol": "BANKNIFTY",
        "expiry": "1735669800000",
    },
    {"garbage": "row without keys"},  # tolerated, skipped
])


@pytest.mark.asyncio
async def test_loader_registered_and_parses_master():
    loader = get_loader("upstox", json_text=_SAMPLE)
    assert isinstance(loader, UpstoxInstrumentLoader)
    master = await loader.fetch()
    assert master.broker == "upstox"
    assert len(master.instruments) == 3  # garbage row dropped

    tcs = master.lookup("TCS", exchange="NSE")
    assert tcs is not None
    assert tcs.token == "NSE_EQ|INE467B01029"      # token IS the instrument key
    assert tcs.exchange_segment == "NSE_EQ"
    assert tcs.kind == InstrumentKind.EQUITY

    opt = master.lookup("BANKNIFTY 52000 PE", exchange="NSE")
    assert opt.kind == InstrumentKind.OPTION
    assert opt.option_type == "PE"
    assert opt.strike == 52000.0
    assert opt.expiry is not None  # epoch-ms expiry parsed


@pytest.mark.asyncio
async def test_upstox_symbol_map_emits_instrument_keys():
    loader = get_loader("upstox", json_text=_SAMPLE)
    master = await loader.fetch()
    mapping = upstox_symbol_map(master, ["TCS", "Nifty Bank"])
    assert mapping == {
        "TCS": "NSE_EQ|INE467B01029",
        "Nifty Bank": "NSE_INDEX|Nifty Bank",
    }
