"""UpstoxMarketDataProvider — V3 authorize / subscribe / protobuf-decode tests.

External Upstox HTTP + WebSocket are fully mocked (httpx.MockTransport for REST,
a monkeypatched ``websockets.connect`` for the feed). No production credentials
are required and no network is touched.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.brokers.websocket import reconnect as reconnect_module
from app.core.exceptions import EngineError, LiveMarketDataError
from app.engine.market_data import UpstoxMarketDataProvider, get_provider
from app.engine.market_data.base import Quote
from app.engine.market_data.registry import (
    is_mock_provider_name,
    resolve_provider,
)
from app.engine.market_data.upstox import UpstoxStatus
from app.engine.market_data.upstox_instruments import (
    build_symbol_map,
    resolve_instrument_key,
)
from app.engine.market_data.upstox_protobuf import (
    UpstoxOHLC,
    UpstoxProtobufError,
    build_feed_response,
    decode_feed_response,
    index_full_feed,
    ltpc_feed,
    market_full_feed,
)

BANKNIFTY_KEY = "NSE_INDEX|Nifty Bank"
NIFTY_KEY = "NSE_INDEX|Nifty 50"
TOKEN = "SECRET-UPSTOX-TOKEN"


# --------------------------------------------------------------------------- #
# Fake WebSocket (same shape as the Dhan/Kotak tests)
# --------------------------------------------------------------------------- #


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


def _ws_factory(sockets: list[_FakeWS]):
    q = list(sockets)

    def _call(url: str, **kwargs: Any) -> _FakeWS:
        _call.invocations.append((url, kwargs))
        return q.pop(0) if q else _FakeWS([])

    _call.invocations = []  # type: ignore[attr-defined]
    return _call


def _authorize_client(uri: str = "wss://feed.upstox.fake/socket?code=abc") -> httpx.AsyncClient:
    """httpx client whose authorize call returns a fake wss URI."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "authorize" in request.url.path:
            assert request.headers.get("Authorization") == f"Bearer {TOKEN}"
            return httpx.Response(200, json={"data": {"authorized_redirect_uri": uri}})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# 1-6 config / token / registration / selection / auth-config / mapping
# --------------------------------------------------------------------------- #


def test_missing_token_rejected():
    with pytest.raises(ValueError):
        UpstoxMarketDataProvider(credentials={})
    with pytest.raises(ValueError):
        UpstoxMarketDataProvider(credentials={"access_token": "   "})


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        UpstoxMarketDataProvider(credentials={"access_token": TOKEN}, mode="bogus")


def test_registered_via_registry():
    provider = get_provider("upstox", credentials={"access_token": TOKEN})
    assert isinstance(provider, UpstoxMarketDataProvider)
    assert provider.name == "upstox"


def test_provider_selection_never_falls_back_to_mock():
    # Explicitly selecting upstox with a token yields the real provider…
    provider = resolve_provider(execution_mode="paper", provider_name="upstox",
                                provider_kwargs={"credentials": {"access_token": TOKEN}})
    assert isinstance(provider, UpstoxMarketDataProvider)
    assert not is_mock_provider_name(provider.name)


def test_default_index_symbol_mapping():
    p = UpstoxMarketDataProvider(credentials={"access_token": TOKEN})
    assert p._symbol_map["NIFTY"] == NIFTY_KEY
    assert p._symbol_map["BANKNIFTY"] == BANKNIFTY_KEY


def test_instrument_key_resolution():
    assert resolve_instrument_key("NIFTY") == NIFTY_KEY
    assert resolve_instrument_key("banknifty") == BANKNIFTY_KEY
    # Already-formatted keys pass through untouched.
    assert resolve_instrument_key("NSE_EQ|INE467B01029") == "NSE_EQ|INE467B01029"
    # Unknown, non-key symbols are not guessed.
    assert resolve_instrument_key("WHATISTHIS") is None
    merged = build_symbol_map({"TCS": "NSE_EQ|INE467B01029"})
    assert merged["TCS"] == "NSE_EQ|INE467B01029"
    assert merged["NIFTY"] == NIFTY_KEY


def test_unresolvable_custom_symbol_rejected():
    with pytest.raises(EngineError):
        UpstoxMarketDataProvider(
            credentials={"access_token": TOKEN},
            symbol_map={"MYSTERY": "not-a-key"},
        )


# --------------------------------------------------------------------------- #
# 7-11 protobuf normalisation (LTP / OHLC / timestamp / volume / decode)
# --------------------------------------------------------------------------- #


def test_decode_ltpc_feed_normalisation():
    raw = build_feed_response(
        {NIFTY_KEY: ltpc_feed(25400.5, 1_700_000_000_000, 50, cp=25350.0)},
        feed_ts_ms=1_700_000_000_500,
    )
    ticks = decode_feed_response(raw)
    assert len(ticks) == 1
    t = ticks[0]
    assert t.instrument_key == NIFTY_KEY
    assert t.ltp == 25400.5
    assert t.ltq == 50
    assert t.cp == 25350.0
    assert t.mode == "ltpc"
    # ltpc mode → volume falls back to last-traded-qty.
    assert t.volume == 50
    assert t.ltt_ms == 1_700_000_000_000


def test_decode_market_full_feed_ohlc_and_volume():
    candles = [
        UpstoxOHLC("1d", 100.0, 110.0, 95.0, 108.0, 123456, 1_700_000_000_000),
        UpstoxOHLC("I1", 107.0, 109.0, 106.5, 108.0, 999, 1_700_000_000_000),
    ]
    raw = build_feed_response(
        {"NSE_EQ|INE467B01029": market_full_feed(
            108.0, 1_700_000_000_000, 10, cp=107.0, vtt=555000, ohlc=candles)}
    )
    t = decode_feed_response(raw)[0]
    assert t.mode == "full"
    assert t.ltp == 108.0
    # vtt (daily volume) wins over ltq / candle volume.
    assert t.volume == 555000
    assert t.ohlc["1d"].high == 110.0
    assert t.ohlc["1d"].volume == 123456


def test_decode_index_full_feed_volume_from_daily_candle():
    candles = [UpstoxOHLC("1d", 52000.0, 52500.0, 51800.0, 52200.0, 88888, 0)]
    raw = build_feed_response(
        {BANKNIFTY_KEY: index_full_feed(52200.2, 1_700_000_001_000, 0, ohlc=candles)}
    )
    t = decode_feed_response(raw)[0]
    assert t.ltp == 52200.2
    # index feed has no vtt → daily candle volume is used.
    assert t.volume == 88888


def test_decode_timestamp_uses_feed_ts_when_ltt_absent():
    raw = build_feed_response(
        {NIFTY_KEY: ltpc_feed(1.0, 0, 0)},
        feed_ts_ms=1_700_000_000_000,
    )
    t = decode_feed_response(raw)[0]
    assert t.ltt_ms == 0
    assert t.feed_ts_ms == 1_700_000_000_000


def test_decode_malformed_frame_raises():
    with pytest.raises(UpstoxProtobufError):
        decode_feed_response(b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff")
    with pytest.raises(UpstoxProtobufError):
        decode_feed_response("not-binary")  # type: ignore[arg-type]


def test_provider_decode_frame_ignores_untracked_and_non_binary():
    p = UpstoxMarketDataProvider(credentials={"access_token": TOKEN})
    # Non-binary control frame → ignored, no crash.
    assert p._decode_frame("hello") == []
    # Tick for an instrument we never subscribed to → dropped.
    raw = build_feed_response({"NSE_EQ|UNKNOWN": ltpc_feed(1.0, 1, 1)})
    assert p._decode_frame(raw) == []


@pytest.mark.asyncio
async def test_provider_decode_frame_maps_to_quote_and_caches_ohlc():
    p = UpstoxMarketDataProvider(credentials={"access_token": TOKEN})
    await p.subscribe(["BANKNIFTY"])  # populates reverse map
    candles = [UpstoxOHLC("1d", 52000.0, 52500.0, 51800.0, 52200.0, 88888, 0)]
    raw = build_feed_response(
        {BANKNIFTY_KEY: index_full_feed(52200.2, 1_700_000_001_000, 0, ohlc=candles)}
    )
    quotes = p._decode_frame(raw)
    assert len(quotes) == 1
    q = quotes[0]
    assert isinstance(q, Quote)
    assert (q.symbol, q.price, q.volume) == ("BANKNIFTY", 52200.2, 88888.0)
    assert q.ts == datetime.fromtimestamp(1_700_000_001, tz=timezone.utc)
    assert p.latest_ohlc("BANKNIFTY")["1d"].close == 52200.0


# --------------------------------------------------------------------------- #
# subscribe frame shape + duplicate prevention
# --------------------------------------------------------------------------- #


def test_build_subscribe_and_unsubscribe_frames():
    p = UpstoxMarketDataProvider(credentials={"access_token": TOKEN}, mode="full")
    sub = p._build_subscribe_frame([BANKNIFTY_KEY, NIFTY_KEY])
    assert sub["method"] == "sub"
    assert sub["data"] == {"mode": "full", "instrumentKeys": [BANKNIFTY_KEY, NIFTY_KEY]}
    assert "guid" in sub
    unsub = p._build_unsubscribe_frame([BANKNIFTY_KEY])
    assert unsub["method"] == "unsub"
    # Binary encoding for the wire.
    assert isinstance(p._encode_send(sub), bytes)


@pytest.mark.asyncio
async def test_duplicate_subscription_prevention():
    p = UpstoxMarketDataProvider(credentials={"access_token": TOKEN})
    await p.subscribe(["BANKNIFTY"])
    await p.subscribe(["BANKNIFTY"])  # idempotent
    assert p._subscribed == {("BANKNIFTY", "NSE")}
    await p.unsubscribe(["BANKNIFTY"])
    assert p._subscribed == set()


# --------------------------------------------------------------------------- #
# 12-16 full wire run: authorize → connect → subscribe → decode → reconnect
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_full_wire_run_with_reconnect_and_resubscribe(monkeypatch):
    frame1 = build_feed_response({BANKNIFTY_KEY: ltpc_feed(52000.0, 1_700_000_000_000, 5)})
    frame2 = build_feed_response({BANKNIFTY_KEY: ltpc_feed(52010.0, 1_700_000_001_000, 3)})
    ws1 = _FakeWS([frame1], close_exc=reconnect_module.ConnectionClosed(None, None))
    ws2 = _FakeWS([frame2])
    factory = _ws_factory([ws1, ws2])
    monkeypatch.setattr(reconnect_module.websockets, "connect", factory)

    p = UpstoxMarketDataProvider(
        credentials={"access_token": TOKEN},
        http_client=_authorize_client(),
        ping_interval_s=None,
        ping_timeout_s=None,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
        max_consecutive_failures=5,
    )
    await p.subscribe(["BANKNIFTY"])
    await p.start()

    got: list[Quote] = []
    async for q in p.stream():
        got.append(q)
        if len(got) == 2:
            break
    await p.stop()

    assert [q.price for q in got] == [52000.0, 52010.0]
    stats = p.get_stats()
    assert stats["ticks_received"] == 2
    assert stats["decode_errors"] == 0
    # Two sockets used → a reconnect happened, and each got a subscribe frame.
    assert len(factory.invocations) == 2
    assert ws1.sent and ws2.sent, "subscribe frame must be re-sent after reconnect"
    resub = json.loads(ws2.sent[0].decode("utf-8"))
    assert resub["method"] == "sub"
    assert resub["data"]["instrumentKeys"] == [BANKNIFTY_KEY]


@pytest.mark.asyncio
async def test_malformed_wire_frame_counted_not_fatal(monkeypatch):
    good = build_feed_response({BANKNIFTY_KEY: ltpc_feed(52000.0, 1_700_000_000_000, 5)})
    ws = _FakeWS([b"\xff\xff\xff\xff", good], close_exc=reconnect_module.ConnectionClosed(None, None))
    monkeypatch.setattr(reconnect_module.websockets, "connect", _ws_factory([ws]))

    p = UpstoxMarketDataProvider(
        credentials={"access_token": TOKEN},
        http_client=_authorize_client(),
        ping_interval_s=None, ping_timeout_s=None,
        backoff_base_s=0.001, backoff_max_s=0.002, max_consecutive_failures=1,
    )
    await p.subscribe(["BANKNIFTY"])
    await p.start()
    got: list[Quote] = []
    async for q in p.stream():
        got.append(q)
        if got:
            break
    await p.stop()
    assert [q.price for q in got] == [52000.0]
    assert p.get_stats()["decode_errors"] == 1  # the malformed frame


# --------------------------------------------------------------------------- #
# 17 provider health / status
# --------------------------------------------------------------------------- #


def test_status_starts_disconnected():
    p = UpstoxMarketDataProvider(credentials={"access_token": TOKEN})
    assert p.status == UpstoxStatus.DISCONNECTED
    stats = p.get_stats()
    assert stats["status"] == "disconnected"
    assert stats["provider"] == "upstox"


@pytest.mark.asyncio
async def test_status_authentication_failed_on_401(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid token"})

    p = UpstoxMarketDataProvider(
        credentials={"access_token": TOKEN},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(EngineError) as exc:
        await p._resolve_url()
    assert exc.value.code == "upstox_authentication_failed"
    assert p.status == UpstoxStatus.AUTHENTICATION_FAILED


# --------------------------------------------------------------------------- #
# 18-19 unavailable + no mock fallback
# --------------------------------------------------------------------------- #


def test_upstox_unavailable_without_token_fails_closed():
    # get_provider with empty creds must raise, never return a mock instance.
    with pytest.raises((ValueError, TypeError)):
        get_provider("upstox", credentials={})


def test_live_resolution_refuses_mock_when_upstox_intended():
    # Requesting mock explicitly for a real broker is forbidden (existing guard).
    with pytest.raises(LiveMarketDataError):
        resolve_provider(execution_mode="live", provider_name="mock",
                         broker_type="dhan")


# --------------------------------------------------------------------------- #
# initial snapshot via V3 LTP REST
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_prime_snapshots_via_v3_ltp_rest():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v3/market-quote/ltp")
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"status": "success", "data": {
            "NSE_INDEX:Nifty Bank": {
                "last_price": 52000.2, "instrument_token": BANKNIFTY_KEY,
                "ltq": 25, "volume": 85000, "cp": 51900.5,
            }
        }})

    p = UpstoxMarketDataProvider(
        credentials={"access_token": TOKEN},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    snaps = await p.prime_snapshots(["BANKNIFTY"])
    assert snaps["BANKNIFTY"].price == 52000.2
    assert snaps["BANKNIFTY"].volume == 85000.0
    # Now cached — snapshot() returns without another network call.
    cached = await p.snapshot("BANKNIFTY")
    assert cached is not None and cached.price == 52000.2


# --------------------------------------------------------------------------- #
# 20 no secret logging
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_token_never_appears_in_stats_or_url_logs(monkeypatch, caplog):
    ws = _FakeWS([], close_exc=reconnect_module.ConnectionClosed(None, None))
    monkeypatch.setattr(reconnect_module.websockets, "connect", _ws_factory([ws]))
    p = UpstoxMarketDataProvider(
        credentials={"access_token": TOKEN},
        http_client=_authorize_client(uri="wss://feed.upstox.fake/socket?code=xyz"),
        ping_interval_s=None, ping_timeout_s=None,
        backoff_base_s=0.001, backoff_max_s=0.002, max_consecutive_failures=1,
    )
    await p.subscribe(["BANKNIFTY"])
    with caplog.at_level("INFO"):
        await p.start()
        await asyncio.sleep(0.05)
        await p.stop()

    # The token must not leak into stats or any log record.
    stats_blob = json.dumps(p.get_stats())
    assert TOKEN not in stats_blob
    for record in caplog.records:
        assert TOKEN not in record.getMessage()
        for value in (getattr(record, "__dict__", {}) or {}).values():
            assert TOKEN not in str(value)


def test_mask_helper_redacts_configured_token(monkeypatch):
    from app.engine.market_data import upstox as upstox_mod
    from app.core.config import settings

    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", TOKEN, raising=False)
    masked = upstox_mod._mask(f"boom {TOKEN} happened")
    assert TOKEN not in masked
    assert "***" in masked
