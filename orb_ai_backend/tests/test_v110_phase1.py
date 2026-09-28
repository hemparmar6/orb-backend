"""v1.1.0 Phase 1 — Permission Engine + Trial + Plan Limits + Strategy Catalog.

These tests exercise the *centralised* permission API and confirm every
plan gate resolves correctly. They must ALWAYS run against the shipped
plan/strategy seed data — no ad-hoc plan creation inside the tests
(that would defeat the "single source of truth" contract).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.strategy_catalog import StrategyCatalog, StrategyStatus as CatStatus
from app.models.subscription import (
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.user import User, UserRole
from app.services.permissions import PermissionService, tier_rank
from app.services.subscriptions.feature_gate import FeatureGate
from app.services.trials import (
    DEFAULT_TRIAL_DURATION_DAYS,
    DEFAULT_TRIAL_PRICE_CENTS,
    EmailNotVerifiedError,
    TrialAlreadyConsumedError,
    TrialService,
)
from tests._module8_helpers import register_and_login


# ---------------------------------------------------------------- helpers
async def _mk_user(db, email: str, *, verified: bool = True) -> User:
    u = User(
        email=email, hashed_password="x", full_name="t",
        role=UserRole.USER, is_active=True, is_verified=verified,
    )
    db.add(u)
    await db.commit()
    return u


async def _set_plan(db, user: User, plan_key: str) -> UserSubscription:
    gate = FeatureGate(db)
    sub = await gate.set_plan(user, plan_key)
    await db.commit()
    return sub


# ---------------------------------------------------------------- tier_rank
def test_tier_rank_ordering():
    assert tier_rank("free") < tier_rank("starter")
    assert tier_rank("starter") < tier_rank("pro")
    assert tier_rank("pro") < tier_rank("elite")
    # ENTERPRISE (legacy) aliases to ELITE rank
    assert tier_rank("enterprise") == tier_rank("elite")


# ---------------------------------------------------------------- plans seeded
@pytest.mark.asyncio
async def test_v110_plans_seeded(db_session):
    rows = (await db_session.execute(select(SubscriptionPlan))).scalars().all()
    active = {r.key for r in rows if r.is_active}
    assert active == {
        "starter-monthly", "starter-annual", "standard-monthly", "standard-annual",
        "pro-monthly", "pro-annual", "elite-monthly", "elite-annual",
    }
    # Legacy rows are retained for existing subscriptions but are not sold.
    assert {"free", "starter", "standard", "pro", "elite"} <= {r.key for r in rows}


@pytest.mark.asyncio
async def test_v110_strategy_catalog_seeded(db_session):
    rows = (await db_session.execute(select(StrategyCatalog))).scalars().all()
    assert len(rows) == 10  # ORB AI 2.0 template catalog, per plan_seed.py
    keys = {r.key for r in rows}
    # Sample checks — all tiers represented
    assert "demo_orb" in keys           # standard
    assert "basic_orb" in keys          # standard
    assert "orb_pro" in keys            # pro
    assert "premium_strategy_1" not in keys  # removed from the current template catalog


# ---------------------------------------------------------------- permission engine
@pytest.mark.asyncio
async def test_new_user_permissions_use_standard_default(db_session):
    u = await _mk_user(db_session, "standard-default@t.dev")
    perms = PermissionService(db_session)
    assert await perms.max_running_bots(u) == 1
    assert await perms.max_open_positions(u) == 5
    assert await perms.can_use_bot(u) is False
    assert await perms.can_use_ai(u) is True
    assert await perms.paper_trading_only(u) is True
    assert await perms.can_use_strategy(u, "demo_orb") is True
    assert await perms.can_use_strategy(u, "orb_pro") is False


@pytest.mark.asyncio
async def test_starter_user_permissions(db_session):
    u = await _mk_user(db_session, "starter@t.dev")
    await _set_plan(db_session, u, "starter")
    perms = PermissionService(db_session)
    assert await perms.can_use_bot(u) is False   # manual only
    assert await perms.can_use_strategy(u, "basic_orb") is True
    assert await perms.can_use_strategy(u, "orb_pro") is False


@pytest.mark.asyncio
async def test_pro_user_permissions(db_session):
    u = await _mk_user(db_session, "pro@t.dev")
    await _set_plan(db_session, u, "pro")
    perms = PermissionService(db_session)
    assert await perms.can_use_bot(u) is True
    assert await perms.max_running_bots(u) == 10
    assert await perms.max_open_positions(u) == 50
    assert await perms.can_use_ai(u) is True
    assert await perms.can_use_strategy(u, "orb_pro") is True
    assert await perms.can_use_strategy(u, "premium_strategy_1") is False


@pytest.mark.asyncio
async def test_elite_user_permissions(db_session):
    u = await _mk_user(db_session, "elite@t.dev")
    await _set_plan(db_session, u, "elite")
    perms = PermissionService(db_session)
    assert await perms.can_use_bot(u) is True
    assert await perms.max_running_bots(u) == -1  # unlimited
    assert await perms.max_open_positions(u) == 20
    # premium_strategy_1 is IMPLEMENTATION_PENDING → still cannot use.
    assert await perms.can_use_strategy(u, "premium_strategy_1") is False
    # But orb_pro (ACTIVE) is allowed.
    assert await perms.can_use_strategy(u, "orb_pro") is True


@pytest.mark.asyncio
async def test_pending_strategy_never_runnable(db_session):
    u = await _mk_user(db_session, "any@t.dev")
    await _set_plan(db_session, u, "elite")
    perms = PermissionService(db_session)
    # ema_9_21 is IMPLEMENTATION_PENDING in the seed.
    assert await perms.can_use_strategy(u, "ema_9_21") is False


@pytest.mark.asyncio
async def test_limit_overrides_take_precedence(db_session):
    u = await _mk_user(db_session, "ov@t.dev")
    sub = await _set_plan(db_session, u, "pro")
    sub.limit_overrides = {"max_running_bots": 10, "ai_features_enabled": False}
    await db_session.commit()

    perms = PermissionService(db_session)
    assert await perms.max_running_bots(u) == 10   # override wins
    assert await perms.can_use_ai(u) is False       # override wins


@pytest.mark.asyncio
async def test_inactive_sub_denies_everything(db_session):
    u = await _mk_user(db_session, "cancelled@t.dev")
    sub = await _set_plan(db_session, u, "pro")
    sub.status = SubscriptionStatus.EXPIRED
    await db_session.commit()
    perms = PermissionService(db_session)
    assert await perms.can_use_bot(u) is False
    assert await perms.max_running_bots(u) == 0
    assert await perms.can_use_strategy(u, "orb_pro") is False


@pytest.mark.asyncio
async def test_snapshot_returns_all_fields(db_session):
    u = await _mk_user(db_session, "snap@t.dev")
    await _set_plan(db_session, u, "pro")
    snap = await PermissionService(db_session).snapshot(u)
    assert snap.plan_key == "pro"
    assert snap.max_running_bots == 10
    assert snap.max_open_positions == 50
    assert snap.automation_enabled is True
    assert "orb_pro" in snap.allowed_strategies
    assert "demo_orb" in snap.allowed_strategies


# ---------------------------------------------------------------- trials
@pytest.mark.asyncio
async def test_trial_requires_verified_email(db_session):
    u = await _mk_user(db_session, "unverified@t.dev", verified=False)
    svc = TrialService(db_session)
    with pytest.raises(EmailNotVerifiedError):
        await svc.start_trial(u)


@pytest.mark.asyncio
async def test_trial_start_success(db_session):
    u = await _mk_user(db_session, "trial@t.dev")
    svc = TrialService(db_session)
    sub = await svc.start_trial(u, payment_reference="rzp_test_123")
    await db_session.commit()

    assert sub.is_trial is True
    assert sub.status == SubscriptionStatus.TRIALING
    assert sub.trial_credit_cents == DEFAULT_TRIAL_PRICE_CENTS
    assert sub.provider_subscription_id == "rzp_test_123"
    assert sub.trial_ends_at is not None
    # Duration
    delta = sub.trial_ends_at - sub.trial_started_at
    assert delta.days == DEFAULT_TRIAL_DURATION_DAYS


@pytest.mark.asyncio
async def test_trial_is_one_time_only(db_session):
    u = await _mk_user(db_session, "once@t.dev")
    svc = TrialService(db_session)
    await svc.start_trial(u)
    await db_session.commit()
    with pytest.raises(TrialAlreadyConsumedError):
        await svc.start_trial(u)


@pytest.mark.asyncio
async def test_expire_stale_trials(db_session):
    u = await _mk_user(db_session, "expiring@t.dev")
    svc = TrialService(db_session)
    sub = await svc.start_trial(u)
    # Force the trial into the past.
    sub.trial_ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.commit()

    n = await svc.expire_stale_trials()
    await db_session.commit()
    assert n == 1
    await db_session.refresh(sub)
    assert sub.is_trial is False
    # Dropped to Standard plan (ORB AI 2.0 entry paid tier).
    standard_plan = (await db_session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.key == "standard")
    )).scalar_one()
    assert sub.plan_id == standard_plan.id


# ---------------------------------------------------------------- API
@pytest.mark.asyncio
async def test_api_permissions_me(client):
    _, headers = await register_and_login(client)
    r = await client.get("/api/v1/permissions/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["plan_key"] == "standard"
    assert body["automation_enabled"] is False
    assert body["max_running_bots"] == 1
    assert "demo_orb" in body["allowed_strategies"]


@pytest.mark.asyncio
async def test_api_permissions_strategy_check(client):
    _, headers = await register_and_login(client)
    r = await client.get("/api/v1/permissions/strategies/orb_pro", headers=headers)
    assert r.status_code == 200
    assert r.json()["allowed"] is False

    r2 = await client.get("/api/v1/permissions/strategies/demo_orb", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["allowed"] is True


@pytest.mark.asyncio
async def test_api_plans_public(client):
    r = await client.get("/api/v1/plans/public")
    assert r.status_code == 200
    plans = r.json()
    keys = {p["key"] for p in plans}
    assert keys == {
        "starter-monthly", "starter-annual", "standard-monthly", "standard-annual",
        "pro-monthly", "pro-annual", "elite-monthly", "elite-annual",
    }


@pytest.mark.asyncio
async def test_api_plans_admin_crud(client, admin_headers):
    # LIST
    r = await client.get("/api/v1/plans/", headers=admin_headers)
    assert r.status_code == 200
    assert len(r.json()) >= 14  # active catalog plus retained legacy rows

    # GET one
    r = await client.get("/api/v1/plans/pro", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["automation_enabled"] is True

    # UPDATE limits
    r = await client.put(
        "/api/v1/plans/pro/limits",
        headers=admin_headers,
        json={"max_running_bots": 3, "max_open_positions": 7},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["max_running_bots"] == 3
    assert body["max_open_positions"] == 7


@pytest.mark.asyncio
async def test_api_plans_public_no_auth_required(client):
    """Anonymous users can list public plans (for pricing page)."""
    r = await client.get("/api/v1/plans/public")
    assert r.status_code == 200
    assert len(r.json()) == 8


@pytest.mark.asyncio
async def test_api_strategy_catalog_public(client):
    r = await client.get("/api/v1/strategy-catalog/")
    assert r.status_code == 200
    catalog = r.json()
    assert len(catalog) == 10
    # Filter by min_plan_tier
    r2 = await client.get("/api/v1/strategy-catalog/?tier=pro")
    assert r2.status_code == 200
    keys = {c["key"] for c in r2.json()}
    assert "orb_pro" in keys


@pytest.mark.asyncio
async def test_api_strategy_catalog_admin_update(client, admin_headers):
    r = await client.put(
        "/api/v1/strategy-catalog/supertrend/status",
        headers=admin_headers,
        json={"status": "active"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_api_trial_flow(client, admin_headers):
    """Admin fixture yields verified account → trial can start."""
    # Get trial state (none yet)
    r = await client.get("/api/v1/trials/me", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["is_trial"] is False

    # Start trial
    r = await client.post(
        "/api/v1/trials/start",
        headers=admin_headers,
        json={"plan_key": "pro", "payment_reference": "rzp_x"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_trial"] is True
    assert body["trial_credit_cents"] == DEFAULT_TRIAL_PRICE_CENTS

    # Second start fails
    r = await client.post(
        "/api/v1/trials/start",
        headers=admin_headers,
        json={"plan_key": "pro"},
    )
    assert r.status_code == 409
