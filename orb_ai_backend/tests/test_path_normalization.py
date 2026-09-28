"""Regression tests for PathNormalizationMiddleware.

Guards against a class of bug where FastAPI's default 307 slash redirect
drops the ``Authorization`` header on the client-side (React Native fetch,
httpx with ``follow_redirects=False``, browsers on cross-origin). We prefer
in-process path normalization so both trailing-slash variants land at the
exact same handler with headers preserved.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_no_307_slash_redirect(client):
    """A trailing-slash mismatch must NEVER produce a 307 wire redirect."""
    # /health has no trailing slash registered — hitting /health/ used to 307.
    r = await client.get("/health/", follow_redirects=False)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_slash_variants_reach_same_handler(client):
    """Both variants of an authenticated endpoint must be reachable and
    return equivalent status codes without dropping the auth header."""
    reg = {"email": "slash@example.com", "password": "SlashPass123!",
           "full_name": "Slash Tester"}
    await client.post("/api/v1/auth/register", json=reg)
    login = await client.post("/api/v1/auth/login",
                              json={"email": reg["email"],
                                    "password": reg["password"]})
    token = login.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}

    # Endpoint registered WITHOUT trailing slash — /notifications
    r_no = await client.get("/api/v1/notifications?limit=5",
                             headers=hdr, follow_redirects=False)
    r_yes = await client.get("/api/v1/notifications/?limit=5",
                              headers=hdr, follow_redirects=False)
    assert r_no.status_code == 200, r_no.text
    assert r_yes.status_code == 200, r_yes.text  # no auth drop
    assert isinstance(r_no.json(), (list, dict))
    assert isinstance(r_yes.json(), (list, dict))

    # Endpoint registered WITH trailing slash — /strategy-catalog/
    r_yes2 = await client.get("/api/v1/strategy-catalog/", headers=hdr,
                               follow_redirects=False)
    r_no2 = await client.get("/api/v1/strategy-catalog", headers=hdr,
                              follow_redirects=False)
    assert r_yes2.status_code == 200, r_yes2.text
    assert r_no2.status_code == 200, r_no2.text


@pytest.mark.asyncio
async def test_unknown_path_still_404(client):
    """Path normalization must not mask legitimate 404s."""
    r = await client.get("/api/v1/does-not-exist", follow_redirects=False)
    assert r.status_code == 404
    r2 = await client.get("/api/v1/does-not-exist/", follow_redirects=False)
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_parameterized_route_slash_variants(client):
    """Parameterized routes (``/bots/{bot_id}``) must also normalize."""
    reg = {"email": "param@example.com", "password": "ParamPass123!",
           "full_name": "Param Tester"}
    await client.post("/api/v1/auth/register", json=reg)
    login = await client.post("/api/v1/auth/login",
                              json={"email": reg["email"],
                                    "password": reg["password"]})
    hdr = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # Non-existent bot id — endpoint returns 404 for the bot, but the ROUTE
    # itself must match under both slash variants (i.e. not 404 from the
    # router or 307 from redirect).
    r_no = await client.get("/api/v1/bots/nonexistent-id", headers=hdr,
                             follow_redirects=False)
    r_yes = await client.get("/api/v1/bots/nonexistent-id/", headers=hdr,
                              follow_redirects=False)
    # Both should produce the SAME status (bot 404 from the handler, or 200
    # if the id happens to exist — never a wire 307).
    assert r_no.status_code == r_yes.status_code
    assert r_no.status_code in (200, 404)
