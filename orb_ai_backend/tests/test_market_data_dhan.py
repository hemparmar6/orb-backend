"""DhanMarketDataProvider — subscribe/decode/URL wiring tests.

Uses the same monkey-patched ``websockets.connect`` pattern as
``test_broker_ws_reconnect.py`` to drive frames deterministically.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.brokers.websocket import reconnect as reconnect_module
from app.engine.market_data import DhanMarketDataProvider, get_provider
from app.engine.market_data.base import Quote


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
        _call.invocations.append((url, kwargs))
        return q.pop(0) if q else _FakeWS([])

    _call.invocations = []  # type: ignore[attr-defined]
    return _call


def test_missing_credentials_rejected():
    with pytest.raises(ValueError):
        DhanMarketDataProvider(credentials={"client_id": "x"})
    with pytest.raises(ValueError):
        DhanMarketDataProvider(credentials={"access_token": "y"})


def test_registered_via_registry():
    provider = get_provider(
        "dhan",
        credentials={"client_id": "CLI", "access_token": "0123456789abcdef"},
    )
    assert isinstance(provider, DhanMarketDataProvider)
    assert provider.name == "dhan"


def test_build_subscribe_and_unsubscribe_frames():
    p = DhanMarketDataProvider(
        credentials={"client_id": "C1", "access_token": "0123456789abcdef"},
        symbol_map={"TCS": ("11536", "NSE_EQ"), "NIFTY": ("58330", "NSE_FNO")},
    )
    sub = p._build_subscribe_frame([("11536", "NSE_EQ"), ("58330", "NSE_FNO")])
    assert sub == {
        "RequestCode": 15,
        "InstrumentCount": 2,
        "InstrumentList": [
            {"ExchangeSegment": "NSE_EQ", "SecurityId": "11536"},
            {"ExchangeSegment": "NSE_FNO", "SecurityId": "58330"},
        ],
    }
    unsub = p._build_unsubscribe_frame([("11536", "NSE_EQ")])
    assert unsub["RequestCode"] == 16


@pytest.mark.asyncio
async def test_url_carries_credentials_but_logs_strip_query():
    p = DhanMarketDataProvider(
        credentials={"client_id": "CLI", "access_token": "SECRET_TOKEN"},
    )
    url = await p._resolve_url()
    assert url.startswith("wss://api-feed.dhan.co?")
    assert "token=SECRET_TOKEN" in url
    assert "clientId=CLI" in url
    # ReconnectingWSClient strips the query when logging — verify helper too.
    from app.brokers.websocket.reconnect import _strip_query
    assert "SECRET" not in _strip_query(url)


@pytest.mark.asyncio
async def test_decode_ticker_frame_normalises_to_quote():
    p = DhanMarketDataProvider(
        credentials={"client_id": "C1", "access_token": "0123456789abcdef"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
    )
    await p.subscribe(["TCS"])  # populates the reverse map even before start()
    frame = json.dumps(
        {
            "type": "ticker",
            "exchangeSegment": "NSE_EQ",
            "securityId": "11536",
            "LTP": 1523.45,
            "LTQ": 10,
            "LTT": 1_700_000_000,
        }
    )
    quotes = p._decode_frame(frame)
    assert len(quotes) == 1
    q = quotes[0]
    assert isinstance(q, Quote)
    assert (q.symbol, q.exchange, q.price, q.volume) == ("TCS", "NSE_EQ", 1523.45, 10.0)
    assert q.ts == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


@pytest.mark.asyncio
async def test_decode_ignores_control_and_bad_frames():
    p = DhanMarketDataProvider(
        credentials={"client_id": "C1", "access_token": "0123456789abcdef"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
    )
    await p.subscribe(["TCS"])
    assert p._decode_frame("not json") == []
    assert p._decode_frame({"type": "server_disconnect"}) == []
    # Symbol not in map → drop.
    assert p._decode_frame(
        {"securityId": "9999", "exchangeSegment": "NSE_EQ", "LTP": 1}
    ) == []
    # Bad LTP type → drop.
    assert p._decode_frame(
        {"securityId": "11536", "exchangeSegment": "NSE_EQ", "LTP": "not-a-number"}
    ) == []


@pytest.mark.asyncio
async def test_full_wire_run(monkeypatch):
    ws = _FakeWS(
        [
            json.dumps(
                {
                    "securityId": "11536",
                    "exchangeSegment": "NSE_EQ",
                    "LTP": 100.5,
                    "LTQ": 5,
                    "LTT": 1_700_000_000,
                }
            ),
            json.dumps(
                {
                    "securityId": "11536",
                    "exchangeSegment": "NSE_EQ",
                    "LTP": 101.0,
                    "LTQ": 3,
                    "LTT": 1_700_000_001,
                }
            ),
        ],
        close_exc=reconnect_module.ConnectionClosed(None, None),
    )
    monkeypatch.setattr(reconnect_module.websockets, "connect", _factory([ws]))

    p = DhanMarketDataProvider(
        credentials={"client_id": "C1", "access_token": "0123456789abcdef"},
        symbol_map={"TCS": ("11536", "NSE_EQ")},
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
        max_consecutive_failures=1,
    )
    await p.subscribe(["TCS"])
    await p.start()

    got: list[Quote] = []
    async for q in p.stream():
        got.append(q)
        if len(got) == 2:
            break
    await p.stop()

    assert [q.price for q in got] == [100.5, 101.0]
    stats = p.get_stats()
    assert stats["ticks_received"] == 2
    assert stats["decode_errors"] == 0
    # Latency is measured from the frame LTT (fixed at 2023-11-14 22:13:20 UTC),
    # which is in the past by the time we run → non-zero avg + max.
    assert stats["latency_ms_max"] > 0
    assert stats["latency_ms_count"] == 2
    # On-connect subscribe frame is what was sent.
    assert ws.sent, "expected subscribe frame on connect"
    sent = json.loads(ws.sent[0])
    assert sent["RequestCode"] == 15
    assert sent["InstrumentList"][0]["SecurityId"] == "11536"
