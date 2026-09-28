"""Integration: new broker market-data providers plug into the engine cleanly.

We don't spin up a full ``EngineRunner`` here — that would need a DB session,
a strategy, a running task. Instead we verify the *contract* the runner
consumes: any provider obtained via ``get_provider`` behaves exactly like the
:class:`MarketDataProvider` ABC promises — ``start``, ``subscribe``,
``stream``, ``snapshot``, ``stop``.

We also freeze the engine invariant: no file inside ``app/engine`` imports
broker-specific modules (``dhan``, ``kotak_neo``, ``mock_live``) EXCEPT the
dedicated broker-provider files themselves.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.brokers.websocket import reconnect as reconnect_module
from app.engine.market_data import (
    DhanMarketDataProvider,
    KotakNeoMarketDataProvider,
    MarketDataProvider,
    get_provider,
)


class _FakeWS:
    def __init__(self, frames: list[Any], *, close_exc: BaseException | None = None):
        self._frames = list(frames)
        self._close_exc = close_exc
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
            if self._close_exc is not None:
                raise self._close_exc
            await asyncio.sleep(3600)
            raise StopAsyncIteration
        return self._frames.pop(0)


def _factory(sockets: list[_FakeWS]):
    q = list(sockets)

    def _call(url: str, **kwargs: Any) -> _FakeWS:
        return q.pop(0) if q else _FakeWS([])

    return _call


def test_dhan_registered_and_isa_market_data_provider():
    p = get_provider(
        "dhan",
        credentials={"client_id": "C", "access_token": "0123456789abcdef"},
    )
    assert isinstance(p, DhanMarketDataProvider)
    assert isinstance(p, MarketDataProvider)


def test_kotak_neo_registered_and_isa_market_data_provider():
    p = get_provider(
        "kotak_neo",
        credentials={"sid": "S", "session_token": "T"},
    )
    assert isinstance(p, KotakNeoMarketDataProvider)
    assert isinstance(p, MarketDataProvider)


@pytest.mark.asyncio
async def test_provider_satisfies_market_data_provider_contract(monkeypatch):
    """Drive the exact surface the engine consumes: subscribe → stream → snapshot → stop."""
    ws = _FakeWS(
        [
            json.dumps(
                {
                    "securityId": "11536",
                    "exchangeSegment": "NSE_EQ",
                    "LTP": 100.0,
                    "LTQ": 1,
                    "LTT": 1_700_000_000,
                }
            ),
        ],
        close_exc=reconnect_module.ConnectionClosed(None, None),
    )
    monkeypatch.setattr(reconnect_module.websockets, "connect", _factory([ws]))

    provider: MarketDataProvider = get_provider(
        "dhan",
        credentials={"client_id": "C", "access_token": "0123456789abcdef"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
        max_consecutive_failures=1,
    )
    await provider.subscribe(["TCS"], exchange="NSE_EQ")
    await provider.start()

    async for quote in provider.stream():
        assert quote.symbol == "TCS"
        assert quote.price == 100.0
        break

    snap = await provider.snapshot("TCS", exchange="NSE_EQ")
    assert snap is not None
    assert snap.price == 100.0

    await provider.unsubscribe(["TCS"], exchange="NSE_EQ")
    await provider.stop()

    # After unsubscribe + stop, the snapshot cache for that symbol is cleared.
    assert (await provider.snapshot("TCS", exchange="NSE_EQ")) is None


def test_engine_framework_has_no_broker_specific_imports():
    """PRD invariant: engine framework files never import broker-specific
    modules. The market-data providers (dhan.py, kotak_neo.py) are the sole
    location where broker names appear inside ``app/engine``.
    """
    engine_root = Path(__file__).resolve().parents[1] / "app" / "engine"
    banned = ("app.brokers.dhan", "app.brokers.kotak_neo", "app.brokers.mock_live")
    # Files inside the market_data subpackage are ALLOWED to be broker-specific.
    allow_dir = engine_root / "market_data"

    offenders: list[tuple[str, str]] = []
    for py in engine_root.rglob("*.py"):
        # Skip the market-data broker files themselves.
        try:
            py.relative_to(allow_dir)
            continue
        except ValueError:
            pass
        text = py.read_text(encoding="utf-8", errors="ignore")
        for b in banned:
            if b in text:
                offenders.append((str(py.relative_to(engine_root)), b))
    assert offenders == [], (
        f"Engine framework must not import broker-specific modules: {offenders}"
    )
