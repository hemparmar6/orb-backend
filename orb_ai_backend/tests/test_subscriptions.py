"""Feature-gate + subscription API tests (Module 8 / subscriptions)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.subscription import PlanTier, SubscriptionPlan, UserSubscription
from app.models.user import User, UserRole
from app.services.subscriptions import (
    FeatureFlag,
    FeatureGate,
    NoopBillingProvider,
    StripeBillingProvider,
    seed_default_plans,
)
from tests._module8_helpers import register_and_login


@pytest.mark.asyncio
async def test_seed_default_plans_idempotent(db_session):
    """conftest already seeds; verify re-running is a no-op and shape is correct."""
    n = await seed_default_plans(db_session)
    assert n == 0  # already seeded by conftest
    await db_session.commit()
    rows = (await db_session.execute(select(SubscriptionPlan))).scalars().all()
    keys = {r.key for r in rows}
    # ORB AI 2.0 replaces Free with paid Standard. Free row is retained
    # (is_active=false) for legacy row compatibility. Pro/pro-annual are
    # active; starter/elite/enterprise remain but inactive.
    assert {"standard", "pro", "pro-annual", "free", "starter", "elite", "enterprise"} <= keys


@pytest.mark.asyncio
async def test_seed_deprecates_free_plan(db_session):
    """Legacy plans are retained but is_active=False; the 8 canonical
    ORB AI 2.0.1 plans are active with server-authoritative paise prices."""
    await seed_default_plans(db_session)
    await db_session.commit()
    rows = (await db_session.execute(select(SubscriptionPlan))).scalars().all()
    by_key = {r.key: r for r in rows}
    # Legacy rows retained but deactivated.
    assert by_key["free"].is_active is False
    assert by_key["standard"].is_active is False       # legacy single-interval
    assert by_key["pro"].is_active is False             # legacy single-interval
    # Canonical 8 active plans with exact INR paise prices.
    assert by_key["starter-monthly"].is_active is True
    assert by_key["starter-monthly"].price_cents == 5000       # ₹50
    assert by_key["starter-annual"].price_cents == 49900       # ₹499
    assert by_key["standard-monthly"].price_cents == 49900     # ₹499
    assert by_key["standard-annual"].price_cents == 499900     # ₹4,999
    assert by_key["pro-monthly"].price_cents == 149900         # ₹1,499
    assert by_key["pro-annual"].price_cents == 1499900         # ₹14,999
    assert by_key["elite-monthly"].price_cents == 299900       # ₹2,999
    assert by_key["elite-annual"].price_cents == 2999900       # ₹29,999


@pytest.mark.asyncio
async def test_feature_gate_defaults_to_standard_plan(db_session):
    """New users with no explicit subscription default to Standard (ORB AI 2.0)."""
    await seed_default_plans(db_session)
    await db_session.commit()
    u = User(email="fg@t.dev", hashed_password="x", full_name="fg", role=UserRole.USER, is_active=True)
    db_session.add(u)
    await db_session.commit()

    gate = FeatureGate(db_session)
    # Standard plan features: paper_trading yes, live_trading no,
    # ai_assistant yes (basic), xlsx report no.
    assert await gate.is_enabled(u, FeatureFlag.PAPER_TRADING) is True
    assert await gate.is_enabled(u, FeatureFlag.LIVE_TRADING) is False
    assert await gate.is_enabled(u, FeatureFlag.REPORT_FORMAT_XLSX) is False
    # WEEKLY_DIGEST is not part of the Standard feature bag any more.
    # (It moved to Pro since it was in the Pro bag already.)
    assert await gate.is_enabled(u, FeatureFlag.REPORT_DAILY_DIGEST) is False
    # And plan lookup returns Standard
    plan = await gate.get_plan(u)
    assert plan is not None and plan.key == "standard"


@pytest.mark.asyncio
async def test_feature_gate_pro_plan_unlocks(db_session):
    await seed_default_plans(db_session)
    await db_session.commit()
    u = User(email="pro@t.dev", hashed_password="x", full_name="pro", role=UserRole.USER, is_active=True)
    db_session.add(u)
    await db_session.commit()

    gate = FeatureGate(db_session)
    await gate.set_plan(u, "pro")
    await db_session.commit()
    assert await gate.is_enabled(u, FeatureFlag.REPORT_DAILY_DIGEST) is True
    assert await gate.is_enabled(u, FeatureFlag.REPORT_FORMAT_XLSX) is True


@pytest.mark.asyncio
async def test_feature_gate_user_override_wins(db_session):
    await seed_default_plans(db_session)
    await db_session.commit()
    u = User(email="ov@t.dev", hashed_password="x", full_name="ov", role=UserRole.USER, is_active=True)
    db_session.add(u)
    await db_session.commit()

    gate = FeatureGate(db_session)
    sub = await gate.set_plan(u, "standard")
    sub.feature_overrides = {FeatureFlag.REPORT_FORMAT_XLSX.value: True}
    await db_session.commit()

    assert await gate.is_enabled(u, FeatureFlag.REPORT_FORMAT_XLSX) is True


@pytest.mark.asyncio
async def test_features_for_returns_all(db_session):
    await seed_default_plans(db_session)
    await db_session.commit()
    u = User(email="all@t.dev", hashed_password="x", full_name="all", role=UserRole.USER, is_active=True)
    db_session.add(u)
    await db_session.commit()
    feats = await FeatureGate(db_session).features_for(u)
    # All FeatureFlag values must appear
    for f in FeatureFlag:
        assert f.value in feats


# ---- Providers ----
@pytest.mark.asyncio
async def test_noop_provider_activates():
    p = NoopBillingProvider()
    assert p.is_configured() is True
    r = await p.create_checkout(
        user_id="u1", user_email="a@b.c", plan_key="pro",
        provider_price_id=None, success_url="s", cancel_url="c",
    )
    assert r.provider == "noop"


@pytest.mark.asyncio
async def test_stripe_provider_not_configured():
    p = StripeBillingProvider()
    # No STRIPE_SECRET_KEY in tests → not configured
    assert p.is_configured() is False


# ---- REST API ----
@pytest.mark.asyncio
async def test_api_list_plans(client, db_session):
    await seed_default_plans(db_session)
    await db_session.commit()
    _, headers = await register_and_login(client)
    r = await client.get("/api/v1/subscriptions/plans", headers=headers)
    assert r.status_code == 200
    plans = r.json()
    # ORB AI 2.0.1: exactly the 8 canonical plans are active. Legacy keys
    # (free / standard / pro / starter / elite / enterprise) are inactive.
    keys = {p["key"] for p in plans}
    expected = {
        "starter-monthly", "starter-annual",
        "standard-monthly", "standard-annual",
        "pro-monthly", "pro-annual",
        "elite-monthly", "elite-annual",
    }
    assert expected <= keys
    assert "free" not in keys and "standard" not in keys and "pro" not in keys


@pytest.mark.asyncio
async def test_api_me_default_standard(client, db_session):
    """A brand-new user with no explicit subscription is on Standard."""
    await seed_default_plans(db_session)
    await db_session.commit()
    _, headers = await register_and_login(client)
    r = await client.get("/api/v1/subscriptions/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    # Standard: paper_trading yes, live_trading no, xlsx no.
    assert body["features"][FeatureFlag.PAPER_TRADING.value] is True
    assert body["features"][FeatureFlag.LIVE_TRADING.value] is False
    assert body["features"][FeatureFlag.REPORT_FORMAT_XLSX.value] is False


@pytest.mark.asyncio
async def test_api_checkout_noop_activates(client, db_session):
    await seed_default_plans(db_session)
    await db_session.commit()
    _, headers = await register_and_login(client)
    r = await client.post(
        "/api/v1/subscriptions/checkout",
        headers=headers,
        json={"plan_key": "pro"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "noop"
    assert body["status"] == "active"

    # Now features_for should reflect the pro plan
    r2 = await client.get("/api/v1/subscriptions/me", headers=headers)
    assert r2.json()["features"][FeatureFlag.REPORT_FORMAT_XLSX.value] is True


@pytest.mark.asyncio
async def test_api_admin_assign_requires_admin(client, db_session):
    await seed_default_plans(db_session)
    await db_session.commit()
    _, headers = await register_and_login(client)
    r = await client.post(
        "/api/v1/subscriptions/admin/assign",
        headers=headers,
        json={"user_id": "any", "plan_key": "pro"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_reports_gated_on_features(client, db_session):
    """Standard-plan user cannot generate the strategy_performance PDF."""
    await seed_default_plans(db_session)
    await db_session.commit()

    user_id, headers = await register_and_login(client)
    # explicitly downgrade to standard (default), just to be safe:
    u = await db_session.get(User, user_id)
    await FeatureGate(db_session).set_plan(u, "standard")
    await db_session.commit()

    r = await client.get(
        "/api/v1/reports/generate/strategy_performance?format=pdf",
        headers=headers,
    )
    assert r.status_code == 402  # payment required
    # Response body format: {"error": {"code": ..., "message": ...}} OR {"detail": ...}
    body = r.json()
    msg = (body.get("detail") or body.get("error", {}).get("message") or "").lower()
    assert "strategy_performance" in msg or "report_type_strategy_performance" in msg

    # PDF for daily is allowed
    r2 = await client.get("/api/v1/reports/generate/daily?format=pdf", headers=headers)
    assert r2.status_code == 200

    # After upgrade to pro, gated type now allowed:
    await FeatureGate(db_session).set_plan(u, "pro")
    await db_session.commit()
    r3 = await client.get(
        "/api/v1/reports/generate/strategy_performance?format=pdf",
        headers=headers,
    )
    assert r3.status_code == 200


@pytest.mark.asyncio
async def test_weekly_digest_gates_on_subscription(client, db_session, db_engine):
    """Users whose plan doesn't include WEEKLY_DIGEST are skipped."""
    from unittest.mock import patch
    from sqlalchemy.ext.asyncio import async_sessionmaker

    await seed_default_plans(db_session)
    await db_session.commit()

    user_id, _ = await register_and_login(client)
    # Set a custom "empty" plan that grants NOTHING.
    empty_plan = SubscriptionPlan(
        key="empty", name="Empty", tier=PlanTier.FREE, features={},
    )
    db_session.add(empty_plan)
    await db_session.flush()
    u = await db_session.get(User, user_id)
    await FeatureGate(db_session).set_plan(u, "empty")
    await db_session.commit()

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    with patch("app.services.tasks.weekly_digest.async_session_factory", factory):
        from app.services.tasks.weekly_digest import run_weekly_digest
        result = await run_weekly_digest()

    assert result["processed"] == 1
    assert result["sent"] == 0
    assert result["skipped"] == 1
