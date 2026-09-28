"""Broker WebSocket helpers — ReconnectingWSClient behaviour tests.

We stub the ``websockets.connect`` async context manager with a fake that
yields controllable frames + close events, so we can exercise the reconnect
loop deterministically without opening a real socket.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.brokers.websocket import reconnect as reconnect_module
from app.brokers.websocket.reconnect import ReconnectingWSClient


class _FakeWS:
    """Async-iterable fake WebSocket connection."""

    def __init__(self, frames: list[Any], *, close_exc: BaseException | None = None):
        self._frames = frames
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
            raise StopAsyncIteration
        return self._frames.pop(0)


class _FakeConnectFactory:
    """Callable that returns a new _FakeWS on each invocation."""

    def __init__(self, sockets: list[_FakeWS]):
        self._queue = list(sockets)
        self.invocations: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs: Any) -> _FakeWS:
        self.invocations.append((url, kwargs))
        if not self._queue:
            # After the queue is drained, subsequent connections yield an empty
            # socket so the client can shut down cleanly on close().
            return _FakeWS([])
        return self._queue.pop(0)


@pytest.mark.asyncio
async def test_reconnect_client_yields_frames_and_calls_on_connect(monkeypatch):
    ws = _FakeWS(["frame-1", "frame-2"])
    factory = _FakeConnectFactory([ws])
    monkeypatch.setattr(reconnect_module.websockets, "connect", factory)

    seen: list[Any] = []

    async def on_connect(w):
        seen.append(("connected",))
        await w.send("hello")

    client = ReconnectingWSClient(
        url_provider=lambda: "wss://example/feed?token=SECRET",
        broker="test",
        on_connect=on_connect,
        decode=lambda raw: {"payload": raw},
        max_consecutive_failures=1,  # after the fake stream ends, give up quickly
        backoff_base_s=0.01,
        backoff_max_s=0.02,
        ping_interval_s=None,
        ping_timeout_s=None,
    )

    frames: list[Any] = []
    async for f in client:
        frames.append(f)
        if len(frames) == 2:
            await client.close()

    assert frames == [{"payload": "frame-1"}, {"payload": "frame-2"}]
    assert seen == [("connected",)]
    assert ws.sent == ["hello"]
    # URL was passed intact (token in query, not headers).
    assert factory.invocations[0][0].startswith("wss://example/feed?token=")


@pytest.mark.asyncio
async def test_reconnect_client_reconnects_after_connection_closed(monkeypatch):
    """When the first connection closes cleanly, the client must reconnect and
    resume yielding frames from the new socket."""
    first = _FakeWS(["A"], close_exc=reconnect_module.ConnectionClosed(None, None))
    second = _FakeWS(["B"])
    factory = _FakeConnectFactory([first, second])
    monkeypatch.setattr(reconnect_module.websockets, "connect", factory)

    client = ReconnectingWSClient(
        url_provider=lambda: "wss://example/feed",
        broker="test",
        decode=lambda raw: raw,
        max_consecutive_failures=3,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
        ping_interval_s=None,
        ping_timeout_s=None,
    )

    got: list[Any] = []
    async for f in client:
        got.append(f)
        if len(got) == 2:
            await client.close()
    assert got == ["A", "B"]
    assert len(factory.invocations) >= 2


@pytest.mark.asyncio
async def test_reconnect_client_gives_up_after_max_failures(monkeypatch):
    """If every reconnect fails immediately, iteration ends after the limit."""
    calls = {"n": 0}

    def raising_factory(url: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        raise RuntimeError("dns unreachable")

    monkeypatch.setattr(reconnect_module.websockets, "connect", raising_factory)

    client = ReconnectingWSClient(
        url_provider=lambda: "wss://example/feed",
        broker="test",
        max_consecutive_failures=2,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
        ping_interval_s=None,
        ping_timeout_s=None,
    )
    got: list[Any] = []
    async for f in client:  # pragma: no cover - would only hit if a frame arrived
        got.append(f)
    assert got == []
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_reconnect_client_close_stops_iteration(monkeypatch):
    async def long_stream():
        for i in range(3):
            await asyncio.sleep(0.01)
            yield f"f{i}"

    class _StreamingWS(_FakeWS):
        def __aiter__(self):
            self._it = long_stream()
            return self

        async def __anext__(self):
            return await self._it.__anext__()

    ws = _StreamingWS([])
    monkeypatch.setattr(
        reconnect_module.websockets,
        "connect",
        _FakeConnectFactory([ws, _FakeWS([])]),
    )

    client = ReconnectingWSClient(
        url_provider=lambda: "wss://example/feed",
        broker="test",
        decode=lambda raw: raw,
        max_consecutive_failures=2,
        backoff_base_s=0.01,
        backoff_max_s=0.02,
        ping_interval_s=None,
        ping_timeout_s=None,
    )
    got: list[Any] = []
    async for f in client:
        got.append(f)
        if len(got) == 1:
            await client.close()
    assert got == ["f0"]
