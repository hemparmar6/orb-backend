"""Phase 2 (v1.1.0) — Commerce integration tests.

Covers:
- Coupon validation, stacking rules, ineligibility reasons.
- Wallet credit/debit/history + admin endpoints.
- Marketplace listing + purchase (including zero-total via wallet+coupon).
- Trial purchase orchestration.
- Revenue KPIs endpoint + Strategy analytics endpoint.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.commerce import Coupon, CouponDiscountType, CouponStatus, Order, OrderKind
from app.models.subscription import UserSubscription
from app.models.user import User
from app.services.commerce.coupon_service import CouponService
from app.services.commerce.wallet_service import WalletService
from app.models.commerce import WalletTxnReason


# =====================================================================
# Helpers
# =====================================================================

async def _seed_coupon(db, **kwargs) -> Coupon:
    """Create a coupon directly via ORM."""
    defaults = dict(
        code="TEST10",
        discount_type=CouponDiscountType.PERCENT,
        discount_value=10,
        status=CouponStatus.ACTIVE,
        one_time_per_user=True,
        allow_stacking=False,
    )
    defaults.update(kwargs)
    c = Coupon(**defaults)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _verify_user(db, user_id: str) -> None:
    u = await db.get(User, user_id)
    u.is_verified = True
    await db.commit()


# =====================================================================
# Coupon service — unit + integration
# =====================================================================


@pytest.mark.asyncio
async def test_coupon_percent_discount_applied(db_session, user_headers, client):
    # Given a 15% coupon and a user
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await _seed_coupon(db_session, code="SAVE15", discount_value=15)

    svc = CouponService(db_session)
    quote = await svc.quote(
        user_id=user_id, codes=["SAVE15"], order_kind=OrderKind.SUBSCRIPTION,
        subtotal_cents=10_000, plan_key="pro",
    )
    assert quote.total_discount_cents == 1_500
    assert len(quote.applied) == 1
    assert quote.applied[0]["code"] == "SAVE15"
    assert quote.stacked is False


@pytest.mark.asyncio
async def test_coupon_flat_with_cap(db_session, user_headers, client):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await _seed_coupon(
        db_session,
        code="OFF100",
        discount_type=CouponDiscountType.FLAT,
        discount_value=100_00,  # 100 rupees flat
        max_discount_cents=50_00,  # cap at 50 rupees
    )
    quote = await CouponService(db_session).quote(
        user_id=user_id, codes=["OFF100"], order_kind=OrderKind.SUBSCRIPTION,
        subtotal_cents=200_00, plan_key="pro",
    )
    assert quote.total_discount_cents == 50_00  # capped


@pytest.mark.asyncio
async def test_coupon_stacking_when_both_allow_stacking(db_session, user_headers, client):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await _seed_coupon(
        db_session, code="STACK10", discount_value=10, allow_stacking=True,
    )
    await _seed_coupon(
        db_session, code="STACK5", discount_value=5, allow_stacking=True,
    )
    quote = await CouponService(db_session).quote(
        user_id=user_id, codes=["STACK10", "STACK5"],
        order_kind=OrderKind.SUBSCRIPTION,
        subtotal_cents=1000_00, plan_key="pro",
    )
    assert quote.stacked is True
    assert len(quote.applied) == 2
    # 10% of 1000 = 100; then 5% of remaining 900 = 45. Total = 145.
    assert quote.total_discount_cents == (100_00 + 45_00)


@pytest.mark.asyncio
async def test_coupon_stacking_rejected_when_one_disallows(
    db_session, user_headers, client
):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await _seed_coupon(db_session, code="A", discount_value=10, allow_stacking=True)
    await _seed_coupon(db_session, code="B", discount_value=5, allow_stacking=False)
    quote = await CouponService(db_session).quote(
        user_id=user_id, codes=["A", "B"],
        order_kind=OrderKind.SUBSCRIPTION,
        subtotal_cents=1000_00, plan_key="pro",
    )
    # Only one applied. The second one is flagged as stacking_not_allowed.
    assert quote.stacked is False
    assert len(quote.applied) == 1
    codes_ineligible = [i["reason"] for i in quote.ineligible]
    assert "stacking_not_allowed" in codes_ineligible


@pytest.mark.asyncio
async def test_coupon_combined_cap(db_session, user_headers, client):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await _seed_coupon(
        db_session, code="C1", discount_value=50,
        allow_stacking=True, max_combined_discount_cents=200_00,
    )
    await _seed_coupon(
        db_session, code="C2", discount_value=50,
        allow_stacking=True, max_combined_discount_cents=200_00,
    )
    quote = await CouponService(db_session).quote(
        user_id=user_id, codes=["C1", "C2"],
        order_kind=OrderKind.SUBSCRIPTION,
        subtotal_cents=1000_00, plan_key="pro",
    )
    # First coupon 50% of 1000 = 500, cap 200 → 200 applied.
    # Second coupon would exceed 200 combined cap → 0 applied.
    assert quote.total_discount_cents == 200_00


@pytest.mark.asyncio
async def test_coupon_trial_only_rejected_for_subscription(
    db_session, user_headers, client
):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await _seed_coupon(
        db_session, code="TRIALX", discount_value=100,
        discount_type=CouponDiscountType.FLAT, trial_only=True,
    )
    quote = await CouponService(db_session).quote(
        user_id=user_id, codes=["TRIALX"], order_kind=OrderKind.SUBSCRIPTION,
        subtotal_cents=10_000, plan_key="pro",
    )
    assert quote.total_discount_cents == 0
    assert quote.ineligible and quote.ineligible[0]["reason"] == "trial_only_coupon"


@pytest.mark.asyncio
async def test_coupon_validate_endpoint(client, user_headers, db_session):
    await _seed_coupon(db_session, code="API10", discount_value=10)
    r = await client.post(
        "/api/v1/coupons/validate",
        headers=user_headers,
        json={
            "codes": ["API10"], "order_kind": "subscription",
            "subtotal_cents": 5000, "plan_key": "pro",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_discount_cents"] == 500
    assert body["net_cents"] == 4500


@pytest.mark.asyncio
async def test_coupon_admin_crud_flow(client, admin_headers):
    # Create
    r = await client.post(
        "/api/v1/coupons/admin", headers=admin_headers,
        json={
            "code": "welcome",
            "description": "Welcome offer",
            "discount_type": "percent", "discount_value": 20,
            "trial_only": False, "allow_stacking": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["code"] == "WELCOME"
    coupon_id = body["id"]

    # List
    r2 = await client.get("/api/v1/coupons/admin", headers=admin_headers)
    assert r2.status_code == 200
    assert any(c["code"] == "WELCOME" for c in r2.json())

    # Patch
    r3 = await client.patch(
        f"/api/v1/coupons/admin/{coupon_id}", headers=admin_headers,
        json={"discount_value": 25, "status": "disabled"},
    )
    assert r3.status_code == 200
    assert r3.json()["discount_value"] == 25
    assert r3.json()["status"] == "disabled"

    # Delete (soft)
    r4 = await client.delete(f"/api/v1/coupons/admin/{coupon_id}", headers=admin_headers)
    assert r4.status_code == 200
    assert r4.json()["status"] == "disabled"


# =====================================================================
# Wallet
# =====================================================================


@pytest.mark.asyncio
async def test_wallet_credit_and_debit(db_session, user_headers, client):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    svc = WalletService(db_session)

    await svc.credit(user_id, 5000, reason=WalletTxnReason.TOPUP, description="top-up")
    snap = await svc.snapshot(user_id)
    assert snap.balance_cents == 5000

    await svc.debit(user_id, 2000, reason=WalletTxnReason.PURCHASE, description="buy")
    snap2 = await svc.snapshot(user_id)
    assert snap2.balance_cents == 3000
    assert snap2.lifetime_earned_cents == 5000
    assert snap2.lifetime_spent_cents == 2000

    hist = await svc.history(user_id)
    assert len(hist) == 2
    await db_session.commit()


@pytest.mark.asyncio
async def test_wallet_debit_insufficient_funds(db_session, user_headers, client):
    from app.services.commerce.wallet_service import InsufficientFundsError
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    with pytest.raises(InsufficientFundsError):
        await WalletService(db_session).debit(
            user_id, 100, reason=WalletTxnReason.PURCHASE,
        )


@pytest.mark.asyncio
async def test_wallet_me_endpoint(client, user_headers):
    r = await client.get("/api/v1/wallet/me", headers=user_headers)
    assert r.status_code == 200, r.text
    assert r.json()["balance_cents"] == 0


@pytest.mark.asyncio
async def test_wallet_admin_credit_endpoint(client, admin_headers, user_headers):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    r = await client.post(
        "/api/v1/wallet/admin/credit", headers=admin_headers,
        json={
            "user_id": user_id, "amount_cents": 10000,
            "reason": "adjustment", "description": "goodwill",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["balance_cents"] == 10000


# =====================================================================
# Marketplace
# =====================================================================


@pytest.mark.asyncio
async def test_marketplace_upsert_and_list(client, admin_headers, user_headers):
    r = await client.post(
        "/api/v1/marketplace/admin/listings", headers=admin_headers,
        json={
            "strategy_key": "basic_orb",
            "price_cents": 49900,
            "tagline": "Classic ORB",
            "is_active": True, "is_featured": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["strategy_key"] == "basic_orb"
    assert body["price_cents"] == 49900

    # Public list
    r2 = await client.get("/api/v1/marketplace/", headers=user_headers)
    assert r2.status_code == 200
    keys = [l["strategy_key"] for l in r2.json()]
    assert "basic_orb" in keys


@pytest.mark.asyncio
async def test_decommissioned_marketplace_purchase_returns_gone_without_order(
    client, admin_headers, user_headers, db_session,
):
    # Set up listing at ₹100
    await client.post(
        "/api/v1/marketplace/admin/listings", headers=admin_headers,
        json={"strategy_key": "vwap_bounce", "price_cents": 10_000},
    )
    # Credit user wallet with ₹100 so wallet fully pays
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await client.post(
        "/api/v1/wallet/admin/credit", headers=admin_headers,
        json={"user_id": user_id, "amount_cents": 10_000, "reason": "topup"},
    )
    # Purchase
    r = await client.post(
        "/api/v1/marketplace/vwap_bounce/purchase",
        headers=user_headers,
        json={"use_wallet": True},
    )
    assert r.status_code == 410, r.text
    assert "marketplace_removed" in r.json()["error"]["message"]
    assert await db_session.scalar(select(Order).where(Order.user_id == user_id)) is None


@pytest.mark.asyncio
async def test_decommissioned_marketplace_purchase_does_not_redeem_coupon(
    client, admin_headers, user_headers, db_session,
):
    await client.post(
        "/api/v1/marketplace/admin/listings", headers=admin_headers,
        json={"strategy_key": "ema_2050", "price_cents": 10_000},
    )
    # 100% off coupon
    await _seed_coupon(
        db_session, code="FULLOFF",
        discount_type=CouponDiscountType.PERCENT, discount_value=100,
    )
    r = await client.post(
        "/api/v1/marketplace/ema_2050/purchase",
        headers=user_headers,
        json={"coupon_codes": ["FULLOFF"], "use_wallet": False},
    )
    assert r.status_code == 410, r.text
    assert await db_session.scalar(select(Order)) is None


# =====================================================================
# Trial purchase
# =====================================================================


@pytest.mark.asyncio
async def test_trial_purchase_rejects_unverified_email(
    client, user_headers,
):
    r = await client.post(
        "/api/v1/trials/purchase", headers=user_headers,
        json={"plan_key": "pro"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_trial_purchase_activates_when_covered_by_wallet(
    client, admin_headers, user_headers, db_session,
):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await _verify_user(db_session, user_id)
    # Wallet has 100 rupees; trial is 50 rupees → total after wallet = 0.
    await client.post(
        "/api/v1/wallet/admin/credit", headers=admin_headers,
        json={"user_id": user_id, "amount_cents": 10_000, "reason": "topup"},
    )
    r = await client.post(
        "/api/v1/trials/purchase", headers=user_headers,
        json={"plan_key": "pro", "use_wallet": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["activated"] is True
    assert body["order_status"] == "paid"
    assert body["wallet_debit_cents"] == 5_000
    assert body["total_cents"] == 0

    # Ensure the underlying trial subscription exists
    sub = await db_session.scalar(
        select(UserSubscription).where(UserSubscription.user_id == user_id)
    )
    await db_session.refresh(sub) if sub else None
    assert sub is not None
    assert sub.is_trial is True


@pytest.mark.asyncio
async def test_trial_purchase_one_time_only(client, admin_headers, user_headers, db_session):
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await _verify_user(db_session, user_id)
    await client.post(
        "/api/v1/wallet/admin/credit", headers=admin_headers,
        json={"user_id": user_id, "amount_cents": 10_000, "reason": "topup"},
    )
    r1 = await client.post(
        "/api/v1/trials/purchase", headers=user_headers,
        json={"plan_key": "pro"},
    )
    assert r1.status_code == 200
    r2 = await client.post(
        "/api/v1/trials/purchase", headers=user_headers,
        json={"plan_key": "pro"},
    )
    assert r2.status_code == 409


# =====================================================================
# Revenue dashboard + strategy analytics
# =====================================================================


@pytest.mark.asyncio
async def test_revenue_summary_is_admin_only(client, user_headers):
    r = await client.get("/api/v1/revenue/summary", headers=user_headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_revenue_summary_returns_all_kpi_groups(client, admin_headers):
    r = await client.get("/api/v1/revenue/summary", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("subscription", "trial", "coupon", "affiliate", "strategy"):
        assert key in body, f"missing KPI group: {key}"
    sub = body["subscription"]
    assert "mrr_cents" in sub and "arr_cents" in sub
    assert "arpu_cents" in sub and "churn_rate_pct" in sub
    trial = body["trial"]
    assert "trial_signups" in trial and "conversion_rate_pct" in trial
    coupon = body["coupon"]
    assert "top_coupons" in coupon
    strat = body["strategy"]
    assert "most_used_strategies" in strat and "plan_distribution" in strat


@pytest.mark.asyncio
async def test_strategy_analytics_endpoint(
    client, admin_headers, user_headers, db_session,
):
    # Strategy purchase is removed in ORB AI 2.0; analytics stays available.
    await client.post(
        "/api/v1/marketplace/admin/listings", headers=admin_headers,
        json={"strategy_key": "supertrend", "price_cents": 5_000},
    )
    me = await client.get("/api/v1/users/me", headers=user_headers)
    user_id = me.json()["id"]
    await client.post(
        "/api/v1/wallet/admin/credit", headers=admin_headers,
        json={"user_id": user_id, "amount_cents": 5_000, "reason": "topup"},
    )
    purchase = await client.post(
        "/api/v1/marketplace/supertrend/purchase",
        headers=user_headers, json={"use_wallet": True},
    )
    assert purchase.status_code == 410

    ana = await client.get(
        "/api/v1/revenue/strategy-analytics", headers=admin_headers,
    )
    assert ana.status_code == 200, ana.text
    body = ana.json()
    assert body["total_purchases"] == 0
    assert body["rows"] == []
