"""KotakNeoMarketDataProvider — auth, subscribe, decode wiring tests.

Both credential modes are covered:

- pre-signed (``sid`` + ``session_token``) → skips OAuth+login
- full (``consumer_key/secret`` + ``mobile_number`` + ``mpin``) → drives the
  two-step auth via a fake ``httpx.AsyncClient`` built on ``httpx.MockTransport``.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.brokers.websocket import reconnect as reconnect_module
from app.core.exceptions import EngineError
from app.engine.market_data import KotakNeoMarketDataProvider, get_provider
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


# ------------------------------------------------------------------- basics


def test_missing_credentials_rejected():
    with pytest.raises(ValueError):
        KotakNeoMarketDataProvider(credentials={})
    with pytest.raises(ValueError):
        KotakNeoMarketDataProvider(credentials={"consumer_key": "k"})


def test_registered_via_registry():
    p = get_provider(
        "kotak_neo",
        credentials={"sid": "S", "session_token": "T"},
    )
    assert isinstance(p, KotakNeoMarketDataProvider)
    assert p.name == "kotak_neo"


def test_subscribe_and_unsubscribe_frames_use_seg_pipe_token():
    p = KotakNeoMarketDataProvider(
        credentials={"sid": "S", "session_token": "T"},
        symbol_map={"TCS": ("11536", "nse_cm"), "NIFTY": ("58330", "nse_fo")},
    )
    sub = p._build_subscribe_frame([("11536", "nse_cm"), ("58330", "nse_fo")])
    assert sub == {
        "a": "mws",
        "v": [{"nse_cm|11536": "1"}, {"nse_fo|58330": "1"}],
        "m": "compact_marketdata",
    }
    unsub = p._build_unsubscribe_frame([("11536", "nse_cm")])
    assert unsub["a"] == "muws"


@pytest.mark.asyncio
async def test_presigned_credentials_skip_auth_and_resolve_url():
    p = KotakNeoMarketDataProvider(
        credentials={"sid": "S123", "session_token": "TOK"},
    )
    await p._authenticate()  # no-op — no http_client used
    url = await p._resolve_url()
    assert url == "wss://mlhsm.kotaksecurities.com/realtime?sId=S123"
    headers = p._extra_headers()
    assert headers["Authorization"] == "Bearer TOK"
    assert headers["Sid"] == "S123"


@pytest.mark.asyncio
async def test_full_auth_flow_populates_sid_and_session_token():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(
                200, json={"access_token": "VIEW", "token_type": "Bearer"}
            )
        if request.url.path.endswith("/login/v6/validate"):
            body = json.loads(request.content or b"{}")
            assert body["mobileNumber"] == "+919999999999"
            assert body["mpin"] == "1234"
            return httpx.Response(
                200,
                json={"data": {"token": "SESSIONTOK", "sid": "SID42", "ucc": "U1"}},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    p = KotakNeoMarketDataProvider(
        credentials={
            "consumer_key": "CK",
            "consumer_secret": "CS",
            "mobile_number": "+919999999999",
            "mpin": "1234",
        },
        http_client=client,
    )
    await p._authenticate()

    assert p._view_token == "VIEW"
    assert p._session_token == "SESSIONTOK"
    assert p._sid == "SID42"
    url = await p._resolve_url()
    assert url.endswith("?sId=SID42")
    # oauth then validate — in that order.
    assert calls[0].endswith("/oauth2/token")
    assert calls[1].endswith("/login/v6/validate")


@pytest.mark.asyncio
async def test_auth_failure_raises_engine_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    p = KotakNeoMarketDataProvider(
        credentials={
            "consumer_key": "CK",
            "consumer_secret": "CS",
            "mobile_number": "+91",
            "mpin": "1234",
        },
        http_client=client,
    )
    with pytest.raises(EngineError) as exc:
        await p._authenticate()
    assert exc.value.code == "kotak_oauth_failed"


# ------------------------------------------------------------------ decode


@pytest.mark.asyncio
async def test_decode_quote_frame_normalises():
    p = KotakNeoMarketDataProvider(
        credentials={"sid": "S", "session_token": "T"},
        symbol_map={"TCS": ("11536", "nse_cm")},
    )
    await p.subscribe(["TCS"])
    quotes = p._decode_frame(
        json.dumps(
            {
                "e": "quote",
                "tk": "11536",
                "seg": "nse_cm",
                "ltp": "1523.45",
                "v": "42",
                "ltt": "1700000000",
            }
        )
    )
    assert len(quotes) == 1
    q = quotes[0]
    assert isinstance(q, Quote)
    assert (q.symbol, q.exchange, q.price, q.volume) == ("TCS", "nse_cm", 1523.45, 42.0)
    assert q.ts == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


@pytest.mark.asyncio
async def test_decode_batch_frame():
    p = KotakNeoMarketDataProvider(
        credentials={"sid": "S", "session_token": "T"},
        symbol_map={"TCS": ("11536", "nse_cm"), "INFY": ("2345", "nse_cm")},
    )
    await p.subscribe(["TCS", "INFY"])
    batch = json.dumps(
        [
            {"tk": "11536", "seg": "nse_cm", "ltp": "1", "ltt": "1700000000"},
            {"tk": "2345", "seg": "nse_cm", "ltp": "2", "ltt": "1700000001"},
        ]
    )
    quotes = p._decode_frame(batch)
    assert [q.symbol for q in quotes] == ["TCS", "INFY"]


@pytest.mark.asyncio
async def test_decode_drops_acks_and_unknown():
    p = KotakNeoMarketDataProvider(
        credentials={"sid": "S", "session_token": "T"},
        symbol_map={"TCS": ("11536", "nse_cm")},
    )
    await p.subscribe(["TCS"])
    assert p._decode_frame(json.dumps({"type": "ack", "channel": "quote"})) == []
    assert (
        p._decode_frame(
            json.dumps({"tk": "9999", "seg": "nse_cm", "ltp": "1"})
        )
        == []
    )


# --------------------------------------------------------------- full wire


@pytest.mark.asyncio
async def test_full_wire_run(monkeypatch):
    ws = _FakeWS(
        [
            json.dumps(
                {
                    "e": "quote",
                    "tk": "11536",
                    "seg": "nse_cm",
                    "ltp": "100",
                    "ltt": "1700000000",
                }
            ),
            json.dumps(
                {
                    "e": "quote",
                    "tk": "11536",
                    "seg": "nse_cm",
                    "ltp": "101",
                    "ltt": "1700000001",
                }
            ),
        ],
        close_exc=reconnect_module.ConnectionClosed(None, None),
    )
    monkeypatch.setattr(reconnect_module.websockets, "connect", _factory([ws]))

    p = KotakNeoMarketDataProvider(
        credentials={"sid": "S", "session_token": "T"},
        symbol_map={"TCS": ("11536", "nse_cm")},
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

    assert [q.price for q in got] == [100.0, 101.0]
    # Subscribe frame sent on connect.
    assert ws.sent, "expected subscribe frame on connect"
    sent = json.loads(ws.sent[0])
    assert sent["a"] == "mws"
    assert sent["v"] == [{"nse_cm|11536": "1"}]
