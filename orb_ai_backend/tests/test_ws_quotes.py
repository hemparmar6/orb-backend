"""WebSocket tests — /ws/v1/quotes.

Uses FastAPI's synchronous TestClient (the only reliable WS client with our
in-memory sqlite test DB). Runs inside pytest-asyncio via ``anyio_backend``
because TestClient itself is sync.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app as fastapi_app
from app.ws.quote_broadcaster import quote_broadcaster


async def _bearer_token(client) -> str:
    """Reuse the async httpx client to register + login, return access token."""
    payload = {"email": "ws@example.com", "password": "very-secret-1", "full_name": "WS"}
    await client.post("/api/v1/auth/register", json=payload)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    return login.json()["access_token"]


@pytest.fixture
def ws_client(client, db_engine):
    """Build a fresh TestClient bound to the SAME test DB session factory."""
    # The `client` fixture already overrode get_db + set strategy_manager to the
    # test engine's session_factory. Reuse those overrides for the sync client.
    with TestClient(fastapi_app) as tc:
        yield tc


@pytest.mark.asyncio
async def test_quotes_ws_rejects_without_token(client, ws_client):
    with pytest.raises(Exception):  # starlette raises WebSocketDisconnect on close
        with ws_client.websocket_connect("/api/v1/ws/quotes") as ws:
            # Server sends error frame then closes.
            first = ws.receive_json()
            assert first["type"] == "error"
            assert first["data"]["code"] == "unauthorized"
            ws.receive_text()  # should raise WebSocketDisconnect


@pytest.mark.asyncio
async def test_quotes_ws_subscribe_and_receive(client, ws_client):
    token = await _bearer_token(client)

    # Seed a deterministic price on the shared broadcaster's provider — we do
    # this after connect so the singleton exists.
    with ws_client.websocket_connect(f"/api/v1/ws/quotes?token={token}") as ws:
        ws.send_json({"action": "subscribe", "channel": "quotes", "symbols": ["A"]})
        ack = ws.receive_json()
        assert ack["type"] == "subscription_ack"
        assert "A" in ack["data"]["symbols"]

        # Force one deterministic tick so we don't have to wait for the loop.
        assert quote_broadcaster._provider is not None
        quote_broadcaster._provider.next_tick("A")

        tick = ws.receive_json()
        assert tick["type"] == "quote"
        assert tick["data"]["symbol"] == "A"
        assert tick["data"]["price"] > 0

        # Ping/pong.
        ws.send_json({"action": "ping"})
        # Server may still deliver another quote first — accept up to 5 frames
        for _ in range(5):
            frame = ws.receive_json()
            if frame["type"] == "pong":
                break
        else:
            pytest.fail("Expected pong within 5 frames")


@pytest.mark.asyncio
async def test_quotes_ws_unsubscribe_stops_delivery(client, ws_client):
    token = await _bearer_token(client)
    with ws_client.websocket_connect(f"/api/v1/ws/quotes?token={token}") as ws:
        ws.send_json({"action": "subscribe", "channel": "quotes", "symbols": ["A"]})
        ws.receive_json()  # ack

        # Unsubscribe.
        ws.send_json({"action": "unsubscribe", "channel": "quotes", "symbols": ["A"]})
        # Drain until we see the unsubscribe ack (allow a stray tick in-between).
        seen_ack = False
        for _ in range(5):
            f = ws.receive_json()
            if f["type"] == "subscription_ack" and f["data"]["action"] == "unsubscribe":
                seen_ack = True
                break
        assert seen_ack

        # After unsubscribe, forcing a tick on symbol A should NOT be delivered.
        assert quote_broadcaster._provider is not None
        # Give the broadcaster a moment to reconcile.
        quote_broadcaster._provider.next_tick("A")


@pytest.mark.asyncio
async def test_quotes_ws_wrong_channel_returns_error(client, ws_client):
    token = await _bearer_token(client)
    with ws_client.websocket_connect(f"/api/v1/ws/quotes?token={token}") as ws:
        ws.send_json({"action": "subscribe", "channel": "orders", "symbols": []})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["data"]["code"] == "wrong_channel"
