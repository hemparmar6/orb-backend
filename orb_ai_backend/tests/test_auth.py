"""End-to-end auth flow tests."""
from __future__ import annotations

import pytest


REG_PAYLOAD = {
    "email": "alice@example.com",
    "password": "s3cret-password!",
    "full_name": "Alice Example",
}


@pytest.mark.asyncio
async def test_register_and_login(client):
    # Register
    resp = await client.post("/api/v1/auth/register", json=REG_PAYLOAD)
    assert resp.status_code == 201, resp.text
    user = resp.json()
    assert user["email"] == REG_PAYLOAD["email"]
    assert "id" in user
    assert user["is_active"] is True

    # Duplicate registration
    dup = await client.post("/api/v1/auth/register", json=REG_PAYLOAD)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "email_already_registered"

    # Login
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": REG_PAYLOAD["email"], "password": REG_PAYLOAD["password"]},
    )
    assert login.status_code == 200, login.text
    tokens = login.json()
    assert set(tokens) >= {"access_token", "refresh_token", "token_type", "expires_in"}
    assert tokens["token_type"] == "bearer"

    # Access /users/me with the token
    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == REG_PAYLOAD["email"]


@pytest.mark.asyncio
async def test_login_invalid_credentials(client):
    await client.post("/api/v1/auth/register", json=REG_PAYLOAD)
    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": REG_PAYLOAD["email"], "password": "wrong"},
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_refresh_and_logout(client):
    await client.post("/api/v1/auth/register", json=REG_PAYLOAD)
    tokens = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": REG_PAYLOAD["email"], "password": REG_PAYLOAD["password"]},
        )
    ).json()

    # Refresh
    refreshed = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # Old refresh token must now be rejected (rotation)
    stale = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert stale.status_code == 401

    # Logout with the current access token
    logout = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
    )
    assert logout.status_code == 200

    # After logout, the current refresh token is revoked
    after = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": new_tokens["refresh_token"]},
    )
    assert after.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_without_token(client):
    resp = await client.get("/api/v1/users/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] in {"unauthorized", "invalid_token"}
