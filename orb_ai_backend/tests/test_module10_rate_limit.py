"""Module 10 — rate limit + security headers tests."""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.rate_limit import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_up_to_burst_then_rejects():
    lim = RateLimiter(default_rate=10, per_seconds=60, burst=5)
    allowed = 0
    rejected = 0
    for _ in range(10):
        ok, _retry = await lim.acquire("test-key-1")
        if ok:
            allowed += 1
        else:
            rejected += 1
    assert allowed == 5, "expected 5 requests to pass (burst=5)"
    assert rejected == 5


@pytest.mark.asyncio
async def test_rate_limiter_isolates_keys():
    lim = RateLimiter(default_rate=1, per_seconds=60, burst=2)
    # Exhaust key A
    assert (await lim.acquire("a"))[0] is True
    assert (await lim.acquire("a"))[0] is True
    assert (await lim.acquire("a"))[0] is False
    # Key B still fine
    assert (await lim.acquire("b"))[0] is True


@pytest.mark.asyncio
async def test_security_headers_are_set(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    for header in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
    ):
        assert header in r.headers, header

    if settings.SECURITY_CSP_ENABLED:
        assert "Content-Security-Policy" in r.headers


@pytest.mark.asyncio
async def test_rate_limit_returns_429_when_enabled(monkeypatch, client):
    """When RATE_LIMIT_ENABLED=true with a tiny burst, hammering the API
    trips the limiter."""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_BURST", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 1)  # extremely slow refill

    # Reset the process-scoped limiter so it picks up new settings
    from app.core import rate_limit
    rate_limit.default_limiter = None

    # /api/v1/manual-trades is not in exempt paths and requires auth (401 fast path)
    statuses: list[int] = []
    for _ in range(6):
        r = await client.get("/api/v1/manual-trades")
        statuses.append(r.status_code)

    assert 429 in statuses, f"expected at least one 429, got {statuses}"


@pytest.mark.asyncio
async def test_health_endpoint_exempt_from_rate_limit(monkeypatch, client):
    """/health must never be rate-limited (used by k8s liveness probes)."""
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 1)

    from app.core import rate_limit
    rate_limit.default_limiter = None

    for _ in range(20):
        r = await client.get("/api/v1/health")
        assert r.status_code == 200, "health probe must always succeed"
