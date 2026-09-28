"""Creator Hub — supplementary security & edge-case probes (T1 testing agent).

Covers gaps not asserted by tests/test_creator_hub.py:
1.  Non-admin 403 sweep across ALL /creators/admin/* endpoints
2.  Non-admin 403 on existing /coupons/admin list endpoint
3.  Assign coupon to a PENDING (unapproved) strategy -> 400
4.  Assign coupon to a REJECTED strategy -> 400
5.  Assign coupon to strategy whose creator is SUSPENDED -> 400
6.  Assign a coupon already assigned to another strategy -> 409
7.  Assign a nonexistent coupon code -> 404
8.  Invalid admin status filter -> 400
9.  Unauthenticated requests -> 401
10. Re-application after rejection resets creator to pending
"""
from __future__ import annotations

import pytest

from app.models.commerce import Coupon, CouponDiscountType, CouponStatus


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def _strategy_payload(name: str = "Probe Strategy") -> dict:
    return {
        "name": name,
        "short_description": "probe",
        "trading_style": "swing",
        "market": "NSE",
        "timeframe": "1h",
        "entry_conditions": "probe entry",
        "exit_conditions": "probe exit",
        "risk_management": "probe risk",
    }


async def _apply(client, headers, name: str = "Probe Creator") -> dict:
    r = await client.post(
        "/api/v1/creators/apply", headers=headers, json={"display_name": name}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _seed_coupon(db_session, code: str = "PROBE10") -> Coupon:
    c = Coupon(
        code=code,
        discount_type=CouponDiscountType.PERCENT,
        discount_value=10,
        status=CouponStatus.ACTIVE,
        one_time_per_user=False,
        allow_stacking=False,
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


async def _register_user(client, email: str) -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "ProbePass123!", "full_name": "Probe"},
    )
    r = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "ProbePass123!"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _approved_creator_with_strategy(
    client, user_headers, admin_headers, name: str = "Probe Creator"
):
    creator = await _apply(client, user_headers, name)
    r = await client.post(
        f"/api/v1/creators/admin/{creator['id']}/approve", headers=admin_headers
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        "/api/v1/creators/me/strategies", headers=user_headers, json=_strategy_payload()
    )
    assert r.status_code == 201, r.text
    return creator["id"], r.json()["id"]


# ---------------------------------------------------------------------
# 1. Non-admin 403 sweep across all /creators/admin/* endpoints
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_admin_forbidden_on_all_creator_admin_endpoints(
    client, user_headers, admin_headers
):
    creator_id, strategy_id = await _approved_creator_with_strategy(
        client, user_headers, admin_headers
    )

    probes = [
        ("GET", "/api/v1/creators/admin/applications", None),
        ("GET", "/api/v1/creators/admin/strategies", None),
        ("POST", f"/api/v1/creators/admin/{creator_id}/approve", None),
        ("POST", f"/api/v1/creators/admin/{creator_id}/reject", {"reason": "x"}),
        ("POST", f"/api/v1/creators/admin/{creator_id}/suspend", None),
        ("POST", f"/api/v1/creators/admin/strategies/{strategy_id}/approve", None),
        ("POST", f"/api/v1/creators/admin/strategies/{strategy_id}/reject", {"reason": "x"}),
        (
            "POST",
            f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
            {"coupon_code": "ANY"},
        ),
    ]
    for method, url, body in probes:
        r = await client.request(method, url, headers=user_headers, json=body)
        assert r.status_code == 403, f"{method} {url} -> {r.status_code} (expected 403)"


# ---------------------------------------------------------------------
# 2. Non-admin 403 on existing coupon admin list
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_admin_forbidden_on_coupon_admin_list(client, user_headers):
    r = await client.get("/api/v1/coupons/admin", headers=user_headers)
    assert r.status_code == 403


# ---------------------------------------------------------------------
# 3. Assign coupon to a PENDING strategy -> 400
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_coupon_to_pending_strategy_rejected(
    client, user_headers, admin_headers, db_session
):
    _, strategy_id = await _approved_creator_with_strategy(
        client, user_headers, admin_headers
    )  # strategy still pending
    await _seed_coupon(db_session)

    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "PROBE10"},
    )
    assert r.status_code == 400
    assert "not approved" in r.json()["error"]["message"]


# ---------------------------------------------------------------------
# 4. Assign coupon to a REJECTED strategy -> 400
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_coupon_to_rejected_strategy_rejected(
    client, user_headers, admin_headers, db_session
):
    _, strategy_id = await _approved_creator_with_strategy(
        client, user_headers, admin_headers
    )
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/reject",
        headers=admin_headers,
        json={"reason": "incomplete risk rules"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    await _seed_coupon(db_session)
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "PROBE10"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------
# 5. Assign coupon to strategy of SUSPENDED creator -> 400
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_coupon_suspended_creator_rejected(
    client, user_headers, admin_headers, db_session
):
    creator_id, strategy_id = await _approved_creator_with_strategy(
        client, user_headers, admin_headers
    )
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/approve",
        headers=admin_headers,
    )
    assert r.status_code == 200

    # Suspend the creator AFTER the strategy was approved.
    r = await client.post(
        f"/api/v1/creators/admin/{creator_id}/suspend", headers=admin_headers
    )
    assert r.status_code == 200

    await _seed_coupon(db_session)
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "PROBE10"},
    )
    assert r.status_code == 400
    assert "creator is not approved" in r.json()["error"]["message"]


# ---------------------------------------------------------------------
# 6. Coupon already assigned to another strategy -> 409
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_already_assigned_coupon_conflict(
    client, user_headers, admin_headers, db_session
):
    _, strategy_a = await _approved_creator_with_strategy(
        client, user_headers, admin_headers, name="Creator A"
    )
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_a}/approve",
        headers=admin_headers,
    )
    assert r.status_code == 200

    await _seed_coupon(db_session)
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_a}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "PROBE10"},
    )
    assert r.status_code == 200

    # Second creator with their own approved strategy.
    other_headers = await _register_user(client, "probe2@example.com")
    _, strategy_b = await _approved_creator_with_strategy(
        client, other_headers, admin_headers, name="Creator B"
    )
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_b}/approve",
        headers=admin_headers,
    )
    assert r.status_code == 200

    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_b}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "PROBE10"},
    )
    assert r.status_code == 409
    assert "already assigned" in r.json()["error"]["message"]

    # Re-assigning the SAME coupon to the SAME strategy is idempotent (200).
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_a}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "PROBE10"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------
# 7. Assign nonexistent coupon -> 404
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_nonexistent_coupon_404(
    client, user_headers, admin_headers
):
    _, strategy_id = await _approved_creator_with_strategy(
        client, user_headers, admin_headers
    )
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/approve",
        headers=admin_headers,
    )
    assert r.status_code == 200

    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "NO_SUCH_CODE"},
    )
    assert r.status_code == 404

    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------
# 8. Invalid admin status filter -> 400
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_status_filter_400(client, admin_headers):
    r = await client.get(
        "/api/v1/creators/admin/applications?status=bogus", headers=admin_headers
    )
    assert r.status_code == 400
    r = await client.get(
        "/api/v1/creators/admin/strategies?status=bogus", headers=admin_headers
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------
# 9. Unauthenticated requests rejected
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthenticated_requests_rejected(client):
    for url in (
        "/api/v1/creators/me",
        "/api/v1/creators/me/strategies",
        "/api/v1/creators/me/coupon",
        "/api/v1/creators/me/redemptions",
        "/api/v1/creators/admin/applications",
    ):
        r = await client.get(url)
        assert r.status_code in (401, 403), f"{url} -> {r.status_code}"
    r = await client.post("/api/v1/creators/apply", json={"display_name": "Anon"})
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------
# 10. Re-application after rejection resets to pending
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reapply_after_rejection_resets_pending(
    client, user_headers, admin_headers
):
    created = await _apply(client, user_headers)
    r = await client.post(
        f"/api/v1/creators/admin/{created['id']}/reject",
        headers=admin_headers,
        json={"reason": "insufficient track record"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["rejected_reason"] == "insufficient track record"

    r = await client.post(
        "/api/v1/creators/apply",
        headers=user_headers,
        json={"display_name": "Probe Creator v2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["display_name"] == "Probe Creator v2"
    assert body["rejected_reason"] is None
    assert body["id"] == created["id"]  # same creator row reused
