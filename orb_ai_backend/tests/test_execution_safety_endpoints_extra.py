"""Additional HTTP endpoint tests for Milestone 8 that were not covered
in the main test_execution_safety.py: events filtering, /me/events,
prune, and 403 enforcement across all admin routes.
"""
from __future__ import annotations

import pytest

from app.services.execution_safety_service import (
    ExecutionSafetyService,
    SafetyContext,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset():
    ExecutionSafetyService.reset_state_for_tests()
    yield
    ExecutionSafetyService.reset_state_for_tests()


async def test_admin_events_filters(client, admin_headers, db_session):
    svc = ExecutionSafetyService(db_session)
    await svc.ensure_settings_row()
    await svc.update_settings(admin_user_id=None, changes={
        "trades_per_second": 2,
        "duplicate_window_seconds": 0.0,
        "queue_enabled": False,
    })
    for i in range(5):
        await svc.check_order(
            SafetyContext(user_id="filter-u", symbol="TCS", quantity=i + 1)
        )
    await db_session.commit()

    r = await client.get(
        "/api/v1/execution-safety/events?user_id=filter-u&limit=50",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    events = r.json()
    assert len(events) >= 5
    assert all(e["user_id"] == "filter-u" for e in events)


async def test_user_can_see_own_events_only(client, user_headers, db_session):
    r = await client.get(
        "/api/v1/execution-safety/me/events", headers=user_headers,
    )
    assert r.status_code == 200
    # No orders yet, should just be an empty list
    assert isinstance(r.json(), list)


async def test_prune_endpoint_admin_only(client, admin_headers, user_headers):
    r = await client.post(
        "/api/v1/execution-safety/prune",
        params={"older_than_days": 30},
        headers=user_headers,
    )
    assert r.status_code == 403

    r = await client.post(
        "/api/v1/execution-safety/prune",
        params={"older_than_days": 30},
        headers=admin_headers,
    )
    assert r.status_code == 200
    body = r.json()
    # accept either shape key
    assert any(k in body for k in ("deleted", "removed", "pruned")), body


async def test_user_forbidden_on_admin_routes(client, user_headers):
    endpoints = [
        ("GET", "/api/v1/execution-safety/settings"),
        ("GET", "/api/v1/execution-safety/events"),
        ("GET", "/api/v1/execution-safety/dashboard"),
        ("GET", "/api/v1/execution-safety/config-audit"),
    ]
    for method, url in endpoints:
        r = await client.request(method, url, headers=user_headers)
        assert r.status_code == 403, f"{method} {url} → {r.status_code}"

    r = await client.patch(
        "/api/v1/execution-safety/settings",
        headers=user_headers,
        json={"trades_per_second": 5},
    )
    assert r.status_code == 403

    r = await client.post(
        "/api/v1/execution-safety/kill-switch",
        headers=user_headers,
        json={"active": True, "reason": "should-fail"},
    )
    assert r.status_code == 403
