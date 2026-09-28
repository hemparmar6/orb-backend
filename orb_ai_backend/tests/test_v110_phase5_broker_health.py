"""Phase 5 tests — Broker WebSocket health tracker + circuit-breaker
integration + admin endpoint contract."""
from __future__ import annotations

import asyncio
import pytest
from sqlalchemy import select

import app.db.session as _db_sess
from app.models.bot import (
    BreakerLevel,
    BreakerType,
    CircuitBreakerEvent,
)
from app.services.broker_health import BrokerHealthTracker


pytestmark = pytest.mark.asyncio


def _sf():
    """Late-bound accessor for the session factory (conftest swaps it)."""
    return _db_sess.async_session_factory


async def test_broker_health_connect_and_heartbeat(client):
    t = BrokerHealthTracker()
    await t.on_connect("dhan")
    snap = t.snapshot()
    assert len(snap) == 1
    row = snap[0]
    assert row["broker_type"] == "dhan"
    assert row["connected"] is True
    assert row["last_connect_at"] is not None
    assert row["incident_open"] is False
    assert t.is_healthy("dhan") is True
    await t.on_heartbeat("dhan")
    assert t.get("dhan")["last_heartbeat_at"] is not None


async def test_broker_health_disconnect_creates_incident_and_dedups(client):
    t = BrokerHealthTracker()
    t.bind_session_factory(_sf())
    await t.on_connect("dhan")
    await t.on_disconnect("dhan", reason="ws closed 1006", unexpected=True)
    row = t.get("dhan")
    assert row["incident_open"] is True
    assert row["retry_count"] == 1
    assert row["total_disconnects"] == 1
    assert row["last_incident_id"] is not None

    await t.on_disconnect("dhan", reason="still down", unexpected=True)
    row = t.get("dhan")
    assert row["retry_count"] == 2
    assert row["total_disconnects"] == 1  # dedup

    async with _sf()() as db:
        events = (await db.execute(
            select(CircuitBreakerEvent).where(
                CircuitBreakerEvent.level == BreakerLevel.BROKER,
                CircuitBreakerEvent.broker_type == "dhan",
                CircuitBreakerEvent.breaker_type == BreakerType.DISCONNECT_DETECTION,
            )
        )).scalars().all()
        assert len(events) == 1
        assert events[0].resolved_at is None


async def test_broker_health_reconnect_resolves_incident(client):
    t = BrokerHealthTracker()
    t.bind_session_factory(_sf())
    await t.on_connect("kotak_neo")
    await t.on_disconnect("kotak_neo", reason="test", unexpected=True)
    await asyncio.sleep(0.01)
    await t.on_connect("kotak_neo")

    row = t.get("kotak_neo")
    assert row["connected"] is True
    assert row["incident_open"] is False
    assert row["total_reconnects"] == 1
    assert row["retry_count"] == 0
    assert row["downtime_seconds"] >= 0.0

    async with _sf()() as db:
        events = (await db.execute(
            select(CircuitBreakerEvent).where(
                CircuitBreakerEvent.broker_type == "kotak_neo",
                CircuitBreakerEvent.breaker_type == BreakerType.DISCONNECT_DETECTION,
            )
        )).scalars().all()
        assert len(events) == 1
        assert events[0].resolved_at is not None


async def test_broker_health_snapshot_endpoint(client, admin_headers):
    from app.services.broker_health import tracker as global_tracker
    global_tracker.bind_session_factory(_sf())
    await global_tracker.on_connect("mock_live")

    r = await client.get("/api/v1/monitoring/broker-health", headers=admin_headers)
    assert r.status_code == 200
    data = r.json()
    assert "brokers" in data
    assert any(b["broker_type"] == "mock_live" for b in data["brokers"])

    r = await client.get("/api/v1/monitoring/broker-health/mock_live",
                         headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["broker_type"] == "mock_live"

    r = await client.get("/api/v1/monitoring/broker-health/does_not_exist",
                         headers=admin_headers)
    assert r.status_code == 404


async def test_graceful_close_is_not_unexpected(client):
    t = BrokerHealthTracker()
    t.bind_session_factory(_sf())
    await t.on_connect("mock_live_graceful")
    await t.on_disconnect("mock_live_graceful",
                          reason="graceful_close", unexpected=False)
    async with _sf()() as db:
        events = (await db.execute(
            select(CircuitBreakerEvent).where(
                CircuitBreakerEvent.broker_type == "mock_live_graceful",
            )
        )).scalars().all()
        assert len(events) == 0
