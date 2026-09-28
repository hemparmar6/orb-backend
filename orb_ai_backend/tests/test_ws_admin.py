"""WebSocket admin dashboard live-updates (Module 7).

Uses FastAPI's TestClient (starlette) with WebSocket connect. The
``/ws/admin`` endpoint:
- Requires a valid access-token JWT via ``?token=``.
- Requires ``role=admin`` — non-admins get a ``forbidden`` error frame
  + close code 4403.
- On accept, sends an initial ``snapshot`` frame with the full dashboard
  payload, then streams a fresh snapshot every ``interval`` seconds.
- Responds ``pong`` to a ``{"action": "ping"}`` client frame.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db.session import get_db
from app.main import app as fastapi_app
from app.models.user import User, UserRole


ADMIN_REG = {
    "email": "wsadmin@example.com",
    "password": "adminpass1234",
    "full_name": "WS Admin",
}
USER_REG = {
    "email": "wsuser@example.com",
    "password": "userpass1234",
    "full_name": "WS User",
}


async def _register_and_login(async_client, payload: dict[str, str]) -> str:
    r = await async_client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 201, r.text
    r = await async_client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _promote(db_session, email: str) -> None:
    await db_session.execute(
        update(User).where(User.email == email).values(role=UserRole.ADMIN)
    )
    await db_session.commit()


@pytest.fixture
async def admin_and_user_tokens(client, db_session) -> dict[str, str]:
    admin_tok = await _register_and_login(client, ADMIN_REG)
    user_tok = await _register_and_login(client, USER_REG)
    await _promote(db_session, ADMIN_REG["email"])
    return {"admin_token": admin_tok, "user_token": user_tok}


def _make_sync_client(db_engine) -> TestClient:
    """Build a TestClient that shares the async engine used by the async fixtures."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db():
        async with factory() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    return TestClient(fastapi_app)


@pytest.mark.asyncio
async def test_ws_admin_streams_snapshot(admin_and_user_tokens: dict[str, str], db_engine: Any) -> None:
    sync_client = _make_sync_client(db_engine)
    tok = admin_and_user_tokens["admin_token"]
    try:
        with sync_client.websocket_connect(f"/api/v1/ws/admin?token={tok}&interval=1") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "snapshot"
            data = msg["data"]
            assert "health" in data
            assert "running_sessions" in data
            assert "recent_orders" in data
            assert "order_counters" in data
            assert "daily_pnl" in data
            assert "generated_at" in data

            # Second push must arrive within a couple of intervals.
            msg2 = ws.receive_json()
            assert msg2["type"] == "snapshot"
            assert msg2["data"]["generated_at"] != data["generated_at"]

            # Ping/pong round-trip
            ws.send_json({"action": "ping"})
            # Ignore any snapshot that landed in between.
            got_pong = False
            for _ in range(5):
                m = ws.receive_json()
                if m["type"] == "pong":
                    got_pong = True
                    break
            assert got_pong, "expected pong within a few frames"
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ws_admin_rejects_non_admin(admin_and_user_tokens: dict[str, str], db_engine: Any) -> None:
    sync_client = _make_sync_client(db_engine)
    tok = admin_and_user_tokens["user_token"]
    try:
        from starlette.websockets import WebSocketDisconnect

        with sync_client.websocket_connect(f"/api/v1/ws/admin?token={tok}") as ws:
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["data"]["code"] == "forbidden"
            # Server closes the socket after the error frame.
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4403
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ws_admin_rejects_missing_token(db_engine: Any) -> None:
    sync_client = _make_sync_client(db_engine)
    try:
        from starlette.websockets import WebSocketDisconnect

        with sync_client.websocket_connect("/api/v1/ws/admin") as ws:
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["data"]["code"] in ("unauthorized", "invalid_token")
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ws_admin_rejects_bad_token(db_engine: Any) -> None:
    sync_client = _make_sync_client(db_engine)
    try:
        from starlette.websockets import WebSocketDisconnect

        with sync_client.websocket_connect("/api/v1/ws/admin?token=not-a-real-jwt") as ws:
            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["data"]["code"] == "invalid_token"
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 4401
    finally:
        fastapi_app.dependency_overrides.clear()
