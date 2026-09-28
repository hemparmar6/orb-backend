"""BrokerWSMarketDataProvider — shared broker WS behaviour tests.

We build a minimal concrete subclass whose ``_decode_frame`` echoes each raw
dict as a :class:`Quote`, and drive it via a fake ``websockets.connect`` that
we control frame-by-frame. This exercises the whole base contract without
touching any real broker code.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.brokers.websocket import reconnect as reconnect_module
from app.engine.market_data.base import Quote
from app.engine.market_data.broker_ws import BrokerWSMarketDataProvider


# ------------------------------------------------------------------ fake WS

class _FakeWS:
    """Async-iterable fake WebSocket connection with programmable frames."""

    def __init__(self, frames: list[Any], *, close_exc: BaseException | None = None):
        self._frames = list(frames)
        self._close_exc = close_exc
        self.sent: list[Any] = []
        self.closed = False

    async def __aenter__(self) -> "_FakeWS":
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.closed = True

    async def send(self, data: Any) -> None:
        self.sent.append(data)

    def __aiter__(self) -> "_FakeWS":
        return self

    async def __anext__(self) -> Any:
        if not self._frames:
            if self._close_exc is not None:
                raise self._close_exc
            # Hang forever so the caller has to close() us — mimics a live socket
            # that keeps the connection open after all planned frames are drained.
            await asyncio.sleep(3600)
            raise StopAsyncIteration
        return self._frames.pop(0)


class _FakeConnectFactory:
    def __init__(self, sockets: list[_FakeWS]):
        self._queue = list(sockets)
        self.invocations: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> _FakeWS:
        self.invocations.append((url, kwargs))
        if not self._queue:
            return _FakeWS([])
        return self._queue.pop(0)


# --------------------------------------------------------- concrete probe

class _ProbeProvider(BrokerWSMarketDataProvider):
    """Minimal provider: token map, JSON tick decoding, deterministic URL."""

    name = "probe"
    default_exchange = "T"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.subscribe_calls: list[list[Any]] = []
        self.unsubscribe_calls: list[list[Any]] = []

    async def _resolve_url(self) -> str:
        return "wss://probe/feed?token=SECRET"

    def _build_subscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        self.subscribe_calls.append(list(items))
        return {"a": "sub", "items": items}

    def _build_unsubscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        self.unsubscribe_calls.append(list(items))
        return {"a": "unsub", "items": items}

    def _decode_frame(self, raw: Any) -> list[Quote]:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            return []
        if "boom" in raw:
            raise RuntimeError("decode boom")
        if "ltp" not in raw:
            return []
        token = raw["tk"]
        mapped = self._reverse_map.get(token)
        if not mapped:
            return []
        symbol, exch = mapped
        ts_val = raw.get("ltt")
        ts = (
            datetime.fromtimestamp(float(ts_val), tz=timezone.utc)
            if ts_val is not None
            else datetime.now(timezone.utc)
        )
        return [
            Quote(
                symbol=symbol,
                exchange=exch,
                price=float(raw["ltp"]),
                volume=float(raw.get("v", 0)),
                ts=ts,
            )
        ]


# ---------------------------------------------------------------- tests

@pytest.mark.asyncio
async def test_subscribe_resolves_and_streams_ticks(monkeypatch):
    ws = _FakeWS(
        [
            json.dumps({"tk": "A1", "ltp": "100.5", "v": 12, "ltt": 1_700_000_000}),
            json.dumps({"tk": "A1", "ltp": "101.0", "v": 5,  "ltt": 1_700_000_001}),
        ],
        close_exc=reconnect_module.ConnectionClosed(None, None),
    )
    factory = _FakeConnectFactory([ws])
    monkeypatch.setattr(reconnect_module.websockets, "connect", factory)

    provider = _ProbeProvider(
        credentials={"any": "thing"},
        symbol_map={"AAA": "A1"},
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
        max_consecutive_failures=1,
    )
    await provider.subscribe(["AAA"])
    await provider.start()

    got: list[Quote] = []
    async for q in provider.stream():
        got.append(q)
        if len(got) == 2:
            break
    await provider.stop()

    assert [q.symbol for q in got] == ["AAA", "AAA"]
    assert [q.price for q in got] == [100.5, 101.0]
    # Subscribe frame sent on connect via _on_connect (not via subscribe() because
    # the socket wasn't connected yet when we called subscribe).
    assert ws.sent, "expected subscribe frame on connect"
    sent = json.loads(ws.sent[0])
    assert sent == {"a": "sub", "items": ["A1"]}
    # Snapshot cache reflects last tick.
    snap = await provider.snapshot("AAA", exchange="T")
    assert snap is not None and snap.price == 101.0


@pytest.mark.asyncio
async def test_subscribe_when_connected_sends_incremental_frame(monkeypatch):
    ws = _FakeWS(
        [json.dumps({"tk": "A1", "ltp": "10", "ltt": 1_700_000_000})]
    )
    factory = _FakeConnectFactory([ws])
    monkeypatch.setattr(reconnect_module.websockets, "connect", factory)

    provider = _ProbeProvider(
        credentials={"x": "y"},
        symbol_map={"AAA": "A1", "BBB": "B1"},
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
        max_consecutive_failures=1,
    )
    await provider.subscribe(["AAA"])
    await provider.start()

    # Wait until the first tick arrives — the socket is now connected.
    async for _q in provider.stream():
        break
    # Now subscribe to a NEW symbol: should send a separate frame on the wire.
    await provider.subscribe(["BBB"])
    # give the send() a beat
    for _ in range(5):
        if len(ws.sent) >= 2:
            break
        await asyncio.sleep(0.01)
    await provider.stop()

    # First frame was the on-connect subscribe for AAA, second is the
    # incremental subscribe for BBB.
    assert len(ws.sent) >= 2
    frames = [json.loads(f) for f in ws.sent]
    assert frames[0]["items"] == ["A1"]
    assert frames[1] == {"a": "sub", "items": ["B1"]}


@pytest.mark.asyncio
async def test_unknown_ticks_are_counted_and_dropped(monkeypatch):
    ws = _FakeWS(
        [
            json.dumps({"tk": "A1", "ltp": "100", "ltt": 1_700_000_000}),
            json.dumps({"tk": "UNKNOWN", "ltp": "999", "ltt": 1_700_000_001}),
        ],
        close_exc=reconnect_module.ConnectionClosed(None, None),
    )
    monkeypatch.setattr(
        reconnect_module.websockets, "connect", _FakeConnectFactory([ws])
    )

    provider = _ProbeProvider(
        credentials={"x": "y"},
        symbol_map={"AAA": "A1"},
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
        max_consecutive_failures=1,
    )
    await provider.subscribe(["AAA"])
    await provider.start()

    got: list[Quote] = []
    async for q in provider.stream():
        got.append(q)
        if len(got) == 1:
            break
    await provider.stop()

    assert [q.symbol for q in got] == ["AAA"]
    stats = provider.get_stats()
    assert stats["ticks_received"] == 1
    # The UNKNOWN frame decoded to zero Quotes (reverse-map miss) — decode
    # errors stay at 0.
    assert stats["decode_errors"] == 0


@pytest.mark.asyncio
async def test_decode_errors_are_counted_and_do_not_kill_stream(monkeypatch):
    ws = _FakeWS(
        [
            json.dumps({"boom": True}),
            json.dumps({"tk": "A1", "ltp": "77", "ltt": 1_700_000_000}),
        ],
        close_exc=reconnect_module.ConnectionClosed(None, None),
    )
    monkeypatch.setattr(
        reconnect_module.websockets, "connect", _FakeConnectFactory([ws])
    )

    provider = _ProbeProvider(
        credentials={"x": "y"},
        symbol_map={"AAA": "A1"},
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
        max_consecutive_failures=1,
    )
    await provider.subscribe(["AAA"])
    await provider.start()

    got: list[Quote] = []
    async for q in provider.stream():
        got.append(q)
        break
    await provider.stop()

    assert len(got) == 1 and got[0].price == 77.0
    assert provider.get_stats()["decode_errors"] == 1


@pytest.mark.asyncio
async def test_reconnect_resubscribes(monkeypatch):
    first = _FakeWS(
        [json.dumps({"tk": "A1", "ltp": "1", "ltt": 1_700_000_000})],
        close_exc=reconnect_module.ConnectionClosed(None, None),
    )
    second = _FakeWS(
        [json.dumps({"tk": "A1", "ltp": "2", "ltt": 1_700_000_001})],
        close_exc=reconnect_module.ConnectionClosed(None, None),
    )
    factory = _FakeConnectFactory([first, second])
    monkeypatch.setattr(reconnect_module.websockets, "connect", factory)

    provider = _ProbeProvider(
        credentials={"x": "y"},
        symbol_map={"AAA": "A1"},
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
        max_consecutive_failures=3,
    )
    await provider.subscribe(["AAA"])
    await provider.start()

    got: list[Quote] = []
    async for q in provider.stream():
        got.append(q)
        if len(got) == 2:
            break
    await provider.stop()

    assert [q.price for q in got] == [1.0, 2.0]
    # Both sockets received a subscribe frame on connect.
    assert first.sent and second.sent
    assert json.loads(first.sent[0])["items"] == ["A1"]
    assert json.loads(second.sent[0])["items"] == ["A1"]


@pytest.mark.asyncio
async def test_unsubscribe_stops_snapshot_and_sends_frame(monkeypatch):
    ws = _FakeWS(
        [json.dumps({"tk": "A1", "ltp": "11.11", "ltt": 1_700_000_000})]
    )
    factory = _FakeConnectFactory([ws])
    monkeypatch.setattr(reconnect_module.websockets, "connect", factory)

    provider = _ProbeProvider(
        credentials={"x": "y"},
        symbol_map={"AAA": "A1"},
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
        max_consecutive_failures=1,
    )
    await provider.subscribe(["AAA"])
    await provider.start()

    # Wait for first tick to prime the snapshot cache.
    async for _q in provider.stream():
        break

    assert (await provider.snapshot("AAA", exchange="T")) is not None
    await provider.unsubscribe(["AAA"])
    # unsubscribe frame goes on the wire
    for _ in range(5):
        if any(json.loads(s).get("a") == "unsub" for s in ws.sent):
            break
        await asyncio.sleep(0.01)
    await provider.stop()
    assert (await provider.snapshot("AAA", exchange="T")) is None
    assert any(json.loads(s).get("a") == "unsub" for s in ws.sent)


@pytest.mark.asyncio
async def test_unknown_symbol_raises_on_subscribe():
    provider = _ProbeProvider(
        credentials={"x": "y"},
        symbol_map={"AAA": "A1"},
        ping_interval_s=None,
        ping_timeout_s=None,
    )
    from app.core.exceptions import EngineError

    with pytest.raises(EngineError):
        await provider.subscribe(["ZZZ"])
