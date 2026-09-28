"""WebSocket tests — /ws/v1/orders.

Redis is disabled in the test suite, so the orders WS should degrade cleanly
with an error frame + close code 4501. This is the important contract:
the WS never hangs when Redis is unavailable.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app as fastapi_app


async def _bearer_token(client) -> str:
    payload = {"email": "wsorders@example.com", "password": "very-secret-1", "full_name": "WSO"}
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
async def test_orders_ws_rejects_without_token(ws_client):
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/api/v1/ws/orders") as ws:
            err = ws.receive_json()
            assert err["type"] == "error"
            ws.receive_text()  # WebSocketDisconnect


@pytest.mark.asyncio
async def test_orders_ws_returns_503_when_redis_disabled(client, ws_client):
    """With REDIS_ENABLED=false (test default), the orders socket sends an
    error frame + closes with 4501 rather than hanging."""
    token = await _bearer_token(client)
    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(f"/api/v1/ws/orders?token={token}") as ws:
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["data"]["code"] == "redis_unavailable"
            ws.receive_text()  # closes with 4501
