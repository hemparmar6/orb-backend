"""Integration coverage for sharing an existing broker feed with quote WS clients."""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import decode_token
from app.engine.market_data.base import Quote
from app.engine.market_data.broker_ws import BrokerWSMarketDataProvider
from app.main import app as fastapi_app
from app.monitoring.metrics import metrics
from app.ws.quote_broadcaster import QUEUE_MAX, QuoteBroadcaster, quote_broadcaster

quote_broadcaster_module = importlib.import_module("app.ws.quote_broadcaster")


class _SyntheticBrokerProvider(BrokerWSMarketDataProvider):
    """Broker-provider-shaped feed with a counted single start/no network IO."""

    name = "dhan"
    default_exchange = "NSE_EQ"

    def __init__(self) -> None:
        super().__init__(credentials={})
        self.connection_starts = 0

    async def _resolve_url(self) -> str:
        return "wss://test.invalid/feed"

    def _build_subscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        return {"items": items}

    def _build_unsubscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        return {"items": items}

    def _decode_frame(self, raw: Any) -> list[Quote]:
        return []

    async def start(self) -> None:
        self.connection_starts += 1
        self._running = True


async def _bearer_token(client) -> str:
    payload = {"email": "quote-fanout@example.com", "password": "very-secret-1", "full_name": "Fanout"}
    await client.post("/api/v1/auth/register", json=payload)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    return login.json()["access_token"]


@pytest.fixture
def ws_client(client, db_engine):
    with TestClient(fastapi_app) as tc:
        yield tc


@pytest.mark.asyncio
async def test_existing_live_provider_fans_100_identical_second_ticks_to_ws(
    client, ws_client, monkeypatch
):
    token = await _bearer_token(client)
    user_id = decode_token(token, expected_type="access")["sub"]
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "dhan")

    def unexpected_mock(*_args, **_kwargs):
        raise AssertionError("real quote mode must not instantiate a mock provider")

    monkeypatch.setattr(quote_broadcaster_module, "MockMarketDataProvider", unexpected_mock)
    provider = _SyntheticBrokerProvider()
    ws_client.portal.call(provider.start)
    assert quote_broadcaster.attach_provider(provider, user_id)
    before = metrics.snapshot()

    try:
        with ws_client.websocket_connect(f"/api/v1/ws/quotes?token={token}") as ws:
            ws.send_json({"action": "subscribe", "channel": "quotes", "symbols": ["AAA"]})
            assert ws.receive_json()["type"] == "subscription_ack"

            ts = datetime(2026, 9, 28, 12, 34, 56, tzinfo=timezone.utc)

            def inject_batch() -> None:
                for sequence in range(1, 101):
                    provider._handle_quote(
                        Quote(
                            symbol="AAA",
                            exchange="NSE_EQ",
                            price=1000.0 + sequence,
                            volume=float(sequence),
                            ts=ts,
                            instrument_token="11536",
                            sequence=sequence,
                            bid=999.5,
                            ask=1000.5,
                        )
                    )

            ws_client.portal.call(inject_batch)
            received = []
            while len(received) < 100:
                message = ws.receive_json()
                if message.get("type") == "quote" and message.get("data", {}).get("symbol") == "AAA":
                    received.append(message["data"])

        after = metrics.snapshot()
        assert [int(tick["sequence"]) for tick in received] == list(range(1, 101))
        assert {tick["ts"] for tick in received} == {ts.isoformat()}
        assert all(tick["instrument_token"] == "11536" for tick in received)
        assert all(tick["bid"] == 999.5 and tick["ask"] == 1000.5 for tick in received)
        assert provider.connection_starts == 1
        assert provider._queue.qsize() == 100  # runner's queue still receives the same ticks
        assert after["broker_ticks_received"] - before["broker_ticks_received"] == 100
        assert after["quote_ticks_forwarded"] - before["quote_ticks_forwarded"] == 100
        assert after["quote_ticks_sent"] - before["quote_ticks_sent"] == 100
        assert after["quote_ticks_dropped"] - before["quote_ticks_dropped"] == 0
    finally:
        quote_broadcaster.detach_provider(provider)
        provider._running = False


def test_quote_broadcaster_records_per_client_overflow_drop():
    broadcaster = QuoteBroadcaster()
    queue = broadcaster._clients.setdefault(
        7,
        {"queue": asyncio.Queue(maxsize=QUEUE_MAX), "symbols": {"AAA"}, "user_id": "u1"},
    )["queue"]
    for sequence in range(QUEUE_MAX):
        queue.put_nowait(sequence)
    before = metrics.snapshot()["quote_ticks_dropped"]

    broadcaster._dispatch(
        Quote("AAA", "NSE_EQ", 100.0, 1.0, datetime.now(timezone.utc)),
        owner_user_id="u1",
    )

    assert queue.qsize() == QUEUE_MAX
    assert queue.get_nowait() == 1  # the oldest queued item was explicitly evicted
    assert metrics.snapshot()["quote_ticks_dropped"] == before + 1
