"""WebSocket /ws/v1/quotes — live market-data fail-closed safety.

Reconstructs the behaviour of commit ``a44fe81``:

* The quote broadcaster is backed only by the deterministic
  ``MockMarketDataProvider``. When a REAL provider is selected
  (``MARKET_DATA_PROVIDER != "mock"``) the socket MUST NOT fan those mock
  ticks out as if they were live. It must fail closed with error code
  ``market_data_provider_unavailable`` and application close code ``4503``.
* When ``MARKET_DATA_PROVIDER == "mock"`` the existing mock streaming behaviour
  remains available for tests / dev preview.

Uses FastAPI's synchronous ``TestClient`` (the only reliable WS client with the
in-memory sqlite test DB), mirroring ``test_ws_quotes.py``.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.main import app as fastapi_app
from app.ws.quote_broadcaster import quote_broadcaster  # noqa: F401  (re-exported for clarity)
from app.api.v1.ws.quotes import CLOSE_PROVIDER_UNAVAILABLE


async def _bearer_token(client) -> str:
    payload = {"email": "wsfc@example.com", "password": "very-secret-1", "full_name": "WSFC"}
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
@pytest.mark.parametrize("provider", ["upstox", "dhan", "kotak_neo", "UPSTOX"])
async def test_quotes_ws_fails_closed_for_real_provider(
    client, ws_client, monkeypatch, provider
):
    """Any non-mock provider → error frame + close 4503, never a mock tick."""
    token = await _bearer_token(client)
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", provider)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with ws_client.websocket_connect(f"/api/v1/ws/quotes?token={token}") as ws:
            first = ws.receive_json()
            assert first["type"] == "error"
            assert first["data"]["code"] == "market_data_provider_unavailable"
            # No mock "quote" frame may be emitted — the next receive must be
            # the server-initiated close.
            ws.receive_text()

    assert exc_info.value.code == CLOSE_PROVIDER_UNAVAILABLE == 4503


@pytest.mark.asyncio
async def test_quotes_ws_mock_provider_still_streams(client, ws_client, monkeypatch):
    """MARKET_DATA_PROVIDER=mock keeps the deterministic mock stream working."""
    from app.ws.quote_broadcaster import quote_broadcaster

    token = await _bearer_token(client)
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "mock")

    with ws_client.websocket_connect(f"/api/v1/ws/quotes?token={token}") as ws:
        ws.send_json({"action": "subscribe", "channel": "quotes", "symbols": ["A"]})
        ack = ws.receive_json()
        assert ack["type"] == "subscription_ack"
        assert "A" in ack["data"]["symbols"]

        assert quote_broadcaster._provider is not None
        quote_broadcaster._provider.next_tick("A")

        tick = ws.receive_json()
        assert tick["type"] == "quote"
        assert tick["data"]["symbol"] == "A"
        assert tick["data"]["price"] > 0
