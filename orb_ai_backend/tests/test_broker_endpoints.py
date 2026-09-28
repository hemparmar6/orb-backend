"""Broker API endpoints — connect, list, snapshots."""
from __future__ import annotations

import pytest


REG = {
    "email": "brokertester@example.com",
    "password": "very-secret-1",
    "full_name": "Broker Tester",
}


async def _login(client) -> str:
    await client.post("/api/v1/auth/register", json=REG)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": REG["email"], "password": REG["password"]},
    )
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_catalog_lists_all_brokers(client):
    r = await client.get("/api/v1/brokers/catalog")
    assert r.status_code == 200
    types = {b["broker_type"] for b in r.json()["brokers"]}
    assert types >= {"mock_live", "dhan", "kotak_neo"}
    dhan_entry = next(b for b in r.json()["brokers"] if b["broker_type"] == "dhan")
    assert dhan_entry["required_credentials"] == ["client_id", "access_token"]


@pytest.mark.asyncio
async def test_connect_list_get_delete(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    # Connect a mock_live account.
    r = await client.post(
        "/api/v1/brokers/connect",
        headers=hdr,
        json={
            "broker_type": "mock_live",
            "alias": "paper-sandbox",
            "credentials": {"initial_funds": 250000},
        },
    )
    assert r.status_code == 201, r.text
    account = r.json()
    assert account["broker_type"] == "mock_live"
    assert account["alias"] == "paper-sandbox"
    # Credentials must never leak.
    assert "credentials" not in account
    assert "credentials_ciphertext" not in account
    account_id = account["id"]

    # List.
    lst = await client.get("/api/v1/brokers", headers=hdr)
    assert lst.status_code == 200
    assert len(lst.json()) == 1

    # Get single.
    one = await client.get(f"/api/v1/brokers/{account_id}", headers=hdr)
    assert one.status_code == 200
    assert one.json()["id"] == account_id

    # Duplicate connect for the same (broker, alias) — 409.
    dup = await client.post(
        "/api/v1/brokers/connect",
        headers=hdr,
        json={
            "broker_type": "mock_live",
            "alias": "paper-sandbox",
            "credentials": {},
        },
    )
    assert dup.status_code == 409

    # Delete.
    d = await client.delete(f"/api/v1/brokers/{account_id}", headers=hdr)
    assert d.status_code == 200

    empty = await client.get("/api/v1/brokers", headers=hdr)
    assert empty.json() == []


@pytest.mark.asyncio
async def test_connect_dhan_rejects_missing_credentials(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/api/v1/brokers/connect",
        headers=hdr,
        json={
            "broker_type": "dhan",
            "alias": "primary",
            "credentials": {"client_id": "abc"},  # missing access_token
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "broker_credentials_invalid"


@pytest.mark.asyncio
async def test_mock_live_snapshots_via_api(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    r = await client.post(
        "/api/v1/brokers/connect",
        headers=hdr,
        json={
            "broker_type": "mock_live",
            "alias": "snap",
            "credentials": {"initial_funds": 500000},
        },
    )
    account_id = r.json()["id"]

    # Funds
    f = await client.get(f"/api/v1/brokers/{account_id}/funds", headers=hdr)
    assert f.status_code == 200
    assert f.json()["available"] == 500000
    assert f.json()["currency"] == "INR"

    # Positions (empty initially)
    p = await client.get(f"/api/v1/brokers/{account_id}/positions", headers=hdr)
    assert p.status_code == 200
    assert p.json() == []

    # Orders (empty)
    o = await client.get(f"/api/v1/brokers/{account_id}/orders", headers=hdr)
    assert o.status_code == 200
    assert o.json() == []


@pytest.mark.asyncio
async def test_cannot_access_other_users_broker(client):
    # Register two users, then try to read the other's account.
    tok_a = await _login(client)
    r = await client.post(
        "/api/v1/brokers/connect",
        headers={"Authorization": f"Bearer {tok_a}"},
        json={"broker_type": "mock_live", "alias": "a", "credentials": {}},
    )
    account_id = r.json()["id"]

    # Register user B.
    await client.post(
        "/api/v1/auth/register",
        json={"email": "userb@example.com", "password": "very-secret-1", "full_name": "B"},
    )
    tok_b = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": "userb@example.com", "password": "very-secret-1"},
        )
    ).json()["access_token"]

    r = await client.get(
        f"/api/v1/brokers/{account_id}",
        headers={"Authorization": f"Bearer {tok_b}"},
    )
    assert r.status_code == 404
