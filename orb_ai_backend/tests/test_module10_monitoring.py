"""Module 10 — monitoring endpoints smoke tests.

These tests spin up an isolated FastAPI app + SQLite database, register
an admin user, and hit every new /monitoring/* route. They avoid pg_dump
and boto3 (Backup service integration is exercised in
test_module10_backup.py via a mock).
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_endpoint_returns_all_components(client, admin_headers):
    r = await client.get("/api/v1/monitoring/health", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overall"] in {"ok", "degraded"}
    for comp in ("api", "database", "redis", "ai_service", "brokers", "websockets", "scheduler"):
        assert comp in body["components"], comp


@pytest.mark.asyncio
async def test_metrics_endpoint(client, admin_headers):
    # Make a few requests first so the counter is non-zero.
    for _ in range(3):
        await client.get("/api/v1/health")

    r = await client.get("/api/v1/monitoring/metrics", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_requests"] >= 1
    assert "endpoints" in body
    assert "status_buckets" in body


@pytest.mark.asyncio
async def test_metrics_prometheus(client):
    """Prometheus scrape endpoint is unauthenticated and exempt from rate limits."""
    r = await client.get("/api/v1/monitoring/metrics/prometheus")
    assert r.status_code == 200
    text = r.text
    assert "orb_ai_requests_total" in text
    assert "orb_ai_uptime_seconds" in text


@pytest.mark.asyncio
async def test_logs_endpoint_returns_stats_and_entries(client, admin_headers):
    r = await client.get("/api/v1/monitoring/logs?category=application&limit=50", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "application"
    assert isinstance(body["entries"], list)
    assert "stats" in body


@pytest.mark.asyncio
async def test_performance_endpoint(client, admin_headers):
    r = await client.get("/api/v1/monitoring/performance", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "endpoints" in body
    assert "slow_request_threshold_ms" in body


@pytest.mark.asyncio
async def test_security_summary(client, admin_headers):
    r = await client.get("/api/v1/monitoring/security/summary", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert "login_success" in body
    assert "config" in body


@pytest.mark.asyncio
async def test_login_activity_records_success_and_failure(client, admin_headers, admin_email):
    """Login activity table is populated for both good and bad credentials.

    The admin_headers fixture already registered the admin (which
    produced 1 successful login row). We only need to add a failed
    attempt and re-check the endpoint.
    """
    # Bad password
    r = await client.post(
        "/api/v1/auth/login", json={"email": admin_email, "password": "wrong"}
    )
    assert r.status_code in (400, 401)

    # Verify persistence (admin_headers already contains a valid token)
    r = await client.get(
        "/api/v1/monitoring/security/login-activity?limit=20",
        headers=admin_headers,
    )
    assert r.status_code == 200
    entries = r.json()
    assert any(e["success"] is True and e["email"] == admin_email for e in entries)
    assert any(e["success"] is False and e["email"] == admin_email for e in entries)


@pytest.mark.asyncio
async def test_non_admin_cannot_access_monitoring(client, user_headers):
    r = await client.get("/api/v1/monitoring/metrics", headers=user_headers)
    assert r.status_code in (401, 403)
