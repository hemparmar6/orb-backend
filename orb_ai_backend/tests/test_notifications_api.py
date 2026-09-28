"""Notification API tests (Module 8)."""
from __future__ import annotations

import pytest

from tests._module8_helpers import register_and_login


@pytest.mark.asyncio
async def test_notification_preferences_defaults(client):
    _, headers = await register_and_login(client)
    r = await client.get("/api/v1/notifications/preferences", headers=headers)
    assert r.status_code == 200
    prefs = r.json()
    assert prefs["in_app_enabled"] is True
    assert prefs["email_enabled"] is True
    assert prefs["telegram_enabled"] is False
    assert prefs["push_enabled"] is False


@pytest.mark.asyncio
async def test_update_preferences(client):
    _, headers = await register_and_login(client)
    r = await client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={
            "telegram_enabled": True,
            "telegram_chat_id": "12345",
            "event_overrides": {"trade_executed": False},
        },
    )
    assert r.status_code == 200
    prefs = r.json()
    assert prefs["telegram_enabled"] is True
    assert prefs["telegram_chat_id"] == "12345"
    assert prefs["event_overrides"]["trade_executed"] is False


@pytest.mark.asyncio
async def test_send_test_notification_and_list(client):
    _, headers = await register_and_login(client)
    r = await client.post(
        "/api/v1/notifications/test",
        headers=headers,
        json={"title": "Hi", "body": "There", "event": "system_alert", "severity": "info"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Hi"
    assert body["event"] == "system_alert"
    # Providers not configured — channel_status should reflect that
    assert body["channel_status"]["in_app"] == "delivered"

    # List
    r2 = await client.get("/api/v1/notifications", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["total"] >= 1

    # Mark read
    r3 = await client.post(f"/api/v1/notifications/{body['id']}/read", headers=headers)
    assert r3.status_code == 200
    assert r3.json()["read_at"] is not None

    # Mark all read
    r4 = await client.post("/api/v1/notifications/read-all", headers=headers)
    assert r4.status_code == 200


@pytest.mark.asyncio
async def test_notifications_requires_auth(client):
    r = await client.get("/api/v1/notifications")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_event_override_skips_dispatch(client):
    _, headers = await register_and_login(client)
    # Disable system_alert entirely
    await client.put(
        "/api/v1/notifications/preferences",
        headers=headers,
        json={"event_overrides": {"system_alert": False}},
    )
    r = await client.post(
        "/api/v1/notifications/test",
        headers=headers,
        json={"title": "X", "body": "Y", "event": "system_alert"},
    )
    assert r.status_code == 200
    assert r.json()["channel_status"]["in_app"] == "skipped_by_pref"
