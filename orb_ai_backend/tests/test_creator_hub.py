"""Creator Hub — focused tests.

Covers exactly the requested scope:
1.  creator application submission
2.  admin creator approval
3.  creator strategy submission (incl. unapproved rejection)
4.  admin strategy approval
5.  admin assigning an EXISTING coupon
6.  unauthorized (non-admin) coupon assignment rejection
7.  creator viewing assigned coupon
8.  creator viewing redemption count
9.  existing coupon redemption continues working
10. creator cannot modify coupon discount/limits/status
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.commerce import (
    Coupon,
    CouponDiscountType,
    CouponRedemption,
    CouponStatus,
    OrderKind,
)
from app.models.creator import Creator
from app.services.commerce.coupon_service import CouponService


# =====================================================================
# Helpers
# =====================================================================

async def _me_id(client, headers) -> str:
    r = await client.get("/api/v1/users/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _apply(client, headers, name: str = "Rahul Trading") -> dict:
    r = await client.post(
        "/api/v1/creators/apply",
        headers=headers,
        json={
            "display_name": name,
            "bio": "Full-time momentum trader",
            "social_links": {"youtube": "https://youtube.com/@rahul"},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _admin_creator_id(client, admin_headers, name: str = "Rahul Trading") -> str:
    r = await client.get("/api/v1/creators/admin/applications", headers=admin_headers)
    assert r.status_code == 200, r.text
    match = [c for c in r.json() if c["display_name"] == name]
    assert match, f"creator {name!r} not found in admin list"
    return match[0]["id"]


async def _approve_creator(client, admin_headers, creator_id: str) -> None:
    r = await client.post(
        f"/api/v1/creators/admin/{creator_id}/approve", headers=admin_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


def _strategy_payload() -> dict:
    return {
        "name": "RSI Momentum Strategy",
        "short_description": "Momentum entries on RSI reversals",
        "trading_style": "intraday momentum",
        "market": "NSE equities",
        "timeframe": "15m",
        "entry_conditions": "RSI crosses above 30 with rising volume",
        "exit_conditions": "RSI above 70 or fixed 1% stop",
        "risk_management": "1% capital risk per trade, max 3 positions",
        "reference_link": "https://example.com/rsi-momentum",
    }


async def _submit_strategy(client, headers) -> dict:
    r = await client.post(
        "/api/v1/creators/me/strategies", headers=headers, json=_strategy_payload()
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _approve_strategy(client, admin_headers, strategy_id: str) -> None:
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/approve",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


async def _seed_coupon(db_session, code: str = "RAHUL10") -> Coupon:
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


async def _approved_chain(client, user_headers, admin_headers) -> tuple[str, str]:
    """Apply → approve creator → submit → approve strategy."""
    await _apply(client, user_headers)
    creator_id = await _admin_creator_id(client, admin_headers)
    await _approve_creator(client, admin_headers, creator_id)
    strategy = await _submit_strategy(client, user_headers)
    await _approve_strategy(client, admin_headers, strategy["id"])
    return creator_id, strategy["id"]


# =====================================================================
# 1. Creator submission
# =====================================================================

@pytest.mark.asyncio
async def test_creator_application_submission(client, user_headers):
    created = await _apply(client, user_headers)
    assert created["status"] == "pending"
    assert created["display_name"] == "Rahul Trading"
    assert created["social_links"]["youtube"] == "https://youtube.com/@rahul"

    r = await client.get("/api/v1/creators/me", headers=user_headers)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]
    assert r.json()["status"] == "pending"


# =====================================================================
# 2. Admin creator approval
# =====================================================================

@pytest.mark.asyncio
async def test_admin_creator_approval(client, user_headers, admin_headers):
    await _apply(client, user_headers)

    r = await client.get("/api/v1/creators/admin/applications", headers=admin_headers)
    assert r.status_code == 200
    apps = r.json()
    assert len(apps) == 1
    assert apps[0]["status"] == "pending"
    assert apps[0]["user_email"]

    creator_id = apps[0]["id"]
    r = await client.post(
        f"/api/v1/creators/admin/{creator_id}/approve", headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    me = await client.get("/api/v1/creators/me", headers=user_headers)
    assert me.json()["status"] == "approved"


# =====================================================================
# 3. Creator strategy submission
# =====================================================================

@pytest.mark.asyncio
async def test_creator_strategy_submission(client, user_headers, admin_headers):
    await _apply(client, user_headers)

    # Unapproved creator cannot submit.
    r = await client.post(
        "/api/v1/creators/me/strategies", headers=user_headers, json=_strategy_payload()
    )
    assert r.status_code == 403

    creator_id = await _admin_creator_id(client, admin_headers)
    await _approve_creator(client, admin_headers, creator_id)

    strategy = await _submit_strategy(client, user_headers)
    assert strategy["status"] == "pending"
    assert strategy["name"] == "RSI Momentum Strategy"
    assert strategy["market"] == "NSE equities"
    assert strategy["timeframe"] == "15m"

    mine = await client.get("/api/v1/creators/me/strategies", headers=user_headers)
    assert [s["id"] for s in mine.json()] == [strategy["id"]]


# =====================================================================
# 4. Admin strategy approval
# =====================================================================

@pytest.mark.asyncio
async def test_admin_strategy_approval(client, user_headers, admin_headers):
    await _apply(client, user_headers)
    creator_id = await _admin_creator_id(client, admin_headers)
    await _approve_creator(client, admin_headers, creator_id)
    strategy = await _submit_strategy(client, user_headers)

    r = await client.get("/api/v1/creators/admin/strategies", headers=admin_headers)
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["status"] == "pending"
    assert rows[0]["creator_display_name"] == "Rahul Trading"

    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy['id']}/approve",
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # Creator cannot approve their own strategy (admin-only surface).
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy['id']}/reject",
        headers=user_headers,
        json={"reason": "self approve attempt"},
    )
    assert r.status_code == 403


# =====================================================================
# 5. Admin assigning an existing coupon
# =====================================================================

@pytest.mark.asyncio
async def test_admin_assign_existing_coupon(client, user_headers, admin_headers, db_session):
    _, strategy_id = await _approved_chain(client, user_headers, admin_headers)
    coupon = await _seed_coupon(db_session)

    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "rahul10"},  # case-insensitive existing coupon lookup
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["coupon_code"] == "RAHUL10"
    assert body["coupon_status"] == "active"
    assert body["strategy_id"] == strategy_id
    assert body["coupon_id"] == coupon.id

    # Coupon row itself untouched (same discount/limits).
    await db_session.refresh(coupon)
    assert coupon.discount_value == 10
    assert coupon.status == CouponStatus.ACTIVE


# =====================================================================
# 6. Unauthorized creator coupon assignment rejection
# =====================================================================

@pytest.mark.asyncio
async def test_unauthorized_coupon_assignment_rejected(
    client, user_headers, admin_headers, db_session
):
    _, strategy_id = await _approved_chain(client, user_headers, admin_headers)
    await _seed_coupon(db_session)

    # The creator (non-admin) cannot assign a coupon to their own strategy.
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=user_headers,
        json={"coupon_code": "RAHUL10"},
    )
    assert r.status_code == 403


# =====================================================================
# 7. Creator viewing assigned coupon
# =====================================================================

@pytest.mark.asyncio
async def test_creator_view_assigned_coupon(client, user_headers, admin_headers, db_session):
    _, strategy_id = await _approved_chain(client, user_headers, admin_headers)
    await _seed_coupon(db_session)
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "RAHUL10"},
    )
    assert r.status_code == 200

    r = await client.get("/api/v1/creators/me/coupon", headers=user_headers)
    assert r.status_code == 200
    coupons = r.json()["coupons"]
    assert len(coupons) == 1
    assert coupons[0]["code"] == "RAHUL10"
    assert coupons[0]["status"] == "active"
    assert coupons[0]["strategy_name"] == "RSI Momentum Strategy"
    assert coupons[0]["redemption_count"] == 0

    # Strategy list also carries the assigned coupon brief.
    mine = await client.get("/api/v1/creators/me/strategies", headers=user_headers)
    assert mine.json()[0]["assigned_coupon"]["code"] == "RAHUL10"


# =====================================================================
# 8. Creator viewing redemption count
# =====================================================================

@pytest.mark.asyncio
async def test_creator_view_redemption_count(
    client, user_headers, admin_headers, db_session
):
    _, strategy_id = await _approved_chain(client, user_headers, admin_headers)
    coupon = await _seed_coupon(db_session)
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "RAHUL10"},
    )
    assert r.status_code == 200

    # Existing redemption rows (written by the existing coupon system).
    follower_id = await _me_id(client, user_headers)
    for amount in (1000, 1500):
        db_session.add(
            CouponRedemption(
                coupon_id=coupon.id, user_id=follower_id, discount_applied_cents=amount
            )
        )
    await db_session.commit()

    r = await client.get("/api/v1/creators/me/redemptions", headers=user_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["redemption_count"] == 2
    assert body["discount_cents"] == 2500
    assert body["coupons"][0]["code"] == "RAHUL10"
    assert body["coupons"][0]["redemption_count"] == 2


# =====================================================================
# 9. Existing coupon redemption continues working
# =====================================================================

@pytest.mark.asyncio
async def test_existing_coupon_redemption_continues_working(
    client, user_headers, admin_headers, db_session
):
    _, strategy_id = await _approved_chain(client, user_headers, admin_headers)
    coupon = await _seed_coupon(db_session)
    r = await client.post(
        f"/api/v1/creators/admin/strategies/{strategy_id}/assign-coupon",
        headers=admin_headers,
        json={"coupon_code": "RAHUL10"},
    )
    assert r.status_code == 200

    user_id = await _me_id(client, user_headers)
    svc = CouponService(db_session)
    quote = await svc.quote(
        user_id=user_id,
        codes=["RAHUL10"],
        order_kind=OrderKind.SUBSCRIPTION,
        subtotal_cents=10_000,
        plan_key="pro",
    )
    assert quote.total_discount_cents == 1_000
    redemptions = await svc.redeem(user_id=user_id, applied=quote.applied)
    await db_session.commit()
    assert len(redemptions) == 1

    await db_session.refresh(coupon)
    assert coupon.redemptions_count == 1

    # The existing validate endpoint also still works on the same coupon.
    r = await client.post(
        "/api/v1/coupons/validate",
        headers=user_headers,
        json={
            "codes": ["RAHUL10"],
            "order_kind": "subscription",
            "subtotal_cents": 10_000,
            "plan_key": "pro",
        },
    )
    assert r.status_code == 200
    assert r.json()["applied"][0]["code"] == "RAHUL10"

    # Attribution now reports the redemption through the creator view.
    r = await client.get("/api/v1/creators/me/redemptions", headers=user_headers)
    assert r.json()["redemption_count"] == 1


# =====================================================================
# 10. Creator cannot modify coupon discount/limits/status
# =====================================================================

@pytest.mark.asyncio
async def test_creator_cannot_modify_coupon(
    client, user_headers, admin_headers, db_session
):
    await _approved_chain(client, user_headers, admin_headers)
    coupon = await _seed_coupon(db_session)

    # Even an approved creator hits the existing admin-only coupon CRUD.
    r = await client.patch(
        f"/api/v1/coupons/admin/{coupon.id}",
        headers=user_headers,
        json={"discount_value": 90, "max_redemptions": 1, "status": "disabled"},
    )
    assert r.status_code == 403

    r = await client.post(
        "/api/v1/coupons/admin",
        headers=user_headers,
        json={"code": "HACK100", "discount_type": "percent", "discount_value": 100},
    )
    assert r.status_code == 403

    r = await client.delete(f"/api/v1/coupons/admin/{coupon.id}", headers=user_headers)
    assert r.status_code == 403

    # Coupon unchanged.
    await db_session.refresh(coupon)
    assert coupon.discount_value == 10
    assert coupon.status == CouponStatus.ACTIVE


# =====================================================================
# Guard: suspended creators cannot submit strategies
# =====================================================================

@pytest.mark.asyncio
async def test_suspended_creator_cannot_submit(client, user_headers, admin_headers):
    await _apply(client, user_headers)
    creator_id = await _admin_creator_id(client, admin_headers)
    await _approve_creator(client, admin_headers, creator_id)

    r = await client.post(
        f"/api/v1/creators/admin/{creator_id}/suspend", headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["status"] == "suspended"

    r = await client.post(
        "/api/v1/creators/me/strategies", headers=user_headers, json=_strategy_payload()
    )
    assert r.status_code == 403

    # The creator row is intact.
    row = await db_session_scalar_creator(client, user_headers)
    assert row["status"] == "suspended"


async def db_session_scalar_creator(client, user_headers) -> dict:
    r = await client.get("/api/v1/creators/me", headers=user_headers)
    assert r.status_code == 200
    return r.json()
