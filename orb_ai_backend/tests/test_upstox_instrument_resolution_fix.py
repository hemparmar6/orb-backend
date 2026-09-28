"""Regression tests for the ``upstox_instrument_unresolved`` production defect.

Root cause: the mobile app / feed sends the Upstox *display* names for the core
indices (``"Nifty 50"`` / ``"Nifty Bank"``, exactly as they appear in the Upstox
instrument master) rather than the compact ORB aliases (``NIFTY`` / ``BANKNIFTY``).
Case/spacing variants of those display names must resolve to the SAME verified
instrument key — never to ``None`` (which triggered ``upstox_instrument_unresolved``
and, downstream, a fail-closed 503).

These tests assert:
  * NIFTY 50   -> ``NSE_INDEX|Nifty 50`` for every realistic casing/spacing
  * NIFTY BANK -> ``NSE_INDEX|Nifty Bank`` for every realistic casing/spacing
  * unknown symbols are still NOT guessed (return ``None``)
  * a genuinely unresolved instrument fails closed with a safe 503 (no mock)
  * Upstox is selected -> never a silent mock fallback
  * the resolved instrument_key is what the historical fetcher is actually called with
"""
from __future__ import annotations

import pytest

from app.engine.market_data import upstox_historical as uh
from app.engine.market_data.upstox_instruments import resolve_instrument_key

NIFTY_KEY = "NSE_INDEX|Nifty 50"
BANKNIFTY_KEY = "NSE_INDEX|Nifty Bank"
TOKEN = "SECRET-UPSTOX-TOKEN"


@pytest.mark.parametrize(
    "symbol",
    ["Nifty 50", "NIFTY 50", "nifty 50", "  Nifty   50 ", "NIFTY50", "nifty50", "NIFTY"],
)
def test_nifty50_resolves_for_every_casing(symbol):
    assert resolve_instrument_key(symbol) == NIFTY_KEY


@pytest.mark.parametrize(
    "symbol",
    ["Nifty Bank", "NIFTY BANK", "nifty bank", " Nifty  Bank ", "NIFTYBANK", "BANKNIFTY"],
)
def test_niftybank_resolves_for_every_casing(symbol):
    assert resolve_instrument_key(symbol) == BANKNIFTY_KEY


def test_authoritative_display_names_resolve():
    # Display names exactly as published in the Upstox instrument master.
    assert resolve_instrument_key("Nifty Fin Service") == "NSE_INDEX|Nifty Fin Service"
    assert resolve_instrument_key("India VIX") == "NSE_INDEX|India VIX"


def test_preformatted_key_passthrough():
    assert resolve_instrument_key(NIFTY_KEY) == NIFTY_KEY
    assert resolve_instrument_key("NSE_EQ|INE467B01029") == "NSE_EQ|INE467B01029"


def test_unknown_symbol_is_not_guessed():
    assert resolve_instrument_key("WHATISTHIS") is None
    assert resolve_instrument_key("") is None
    assert resolve_instrument_key("   ") is None


@pytest.mark.asyncio
async def test_upstox_candles_uses_resolved_instrument_key(monkeypatch):
    """`_upstox_candles` must resolve 'Nifty 50' and call the fetcher with the
    verified instrument key — proving the unresolved-instrument path is gone."""
    from app.api.v1.endpoints import market_data as md
    from app.core.config import settings

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", TOKEN, raising=False)

    captured: dict[str, str] = {}

    class _FakeFetcher:
        async def fetch_ohlcv(self, instrument_key, unit, mult, start, end):
            captured["instrument_key"] = instrument_key
            return [[1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 1234.0]]

        async def close(self):
            return None

    def _fake_get_historical(name, **kwargs):
        assert name == "upstox"
        return _FakeFetcher()

    monkeypatch.setattr(uh, "get_historical", _fake_get_historical, raising=False)
    # `_upstox_candles` imports get_historical from historical_base at call time.
    from app.engine.market_data import historical_base as hb
    monkeypatch.setattr(hb, "get_historical", _fake_get_historical, raising=False)

    from datetime import datetime, timezone
    rows = await md._upstox_candles(
        symbol="Nifty 50", exchange="NSE_INDEX", timeframe="15m",
        count=10, end_dt=datetime.now(timezone.utc),
    )
    assert rows is not None and len(rows) == 1
    assert captured["instrument_key"] == NIFTY_KEY


@pytest.mark.asyncio
async def test_endpoint_fails_closed_on_unresolved_instrument(monkeypatch):
    """Upstox selected + a genuinely unresolvable symbol -> safe 503, never mock."""
    from app.api.v1.endpoints import market_data as md
    from app.core.config import settings
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", TOKEN, raising=False)

    with pytest.raises(HTTPException) as exc:
        await md.get_candles(
            user=object(), session=None, symbol="TOTALLY_UNKNOWN_XYZ",
            timeframe="15m", exchange="NSE_INDEX", count=10,
            end_ts=None, prefer_offline=False, security_id=None,
        )
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "market_data_provider_unavailable"
