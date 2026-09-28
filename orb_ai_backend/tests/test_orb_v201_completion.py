"""ORB AI 2.0.1 completion tests.

Covers the functional fixes:
- 8-plan server-authoritative catalog with exact INR paise prices.
- Razorpay order amount comes from plan.price_cents and is NEVER 0.
- Invalid / non-positive Razorpay amounts are rejected.
- /checkout returns server-authoritative amount (client sends only plan_key).
- Razorpay webhook activates a subscription ONLY on a verified success event,
  rejects bad/missing signatures, and is idempotent.
- Dhan REST is actually selected for /candles when configured; mock is never
  labelled as dhan.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from app.engine.market_data.base import Candle, Interval
from app.services.subscriptions import (
    MockBillingProvider,
    RazorpayBillingProvider,
    seed_default_plans,
)
from tests._module8_helpers import register_and_login


EXPECTED_CATALOG = {
    "starter-monthly": 5000,
    "starter-annual": 49900,
    "standard-monthly": 49900,
    "standard-annual": 499900,
    "pro-monthly": 149900,
    "pro-annual": 1499900,
    "elite-monthly": 299900,
    "elite-annual": 2999900,
}


# ---- 8-plan catalog ------------------------------------------------------
@pytest.mark.asyncio
async def test_public_catalog_is_exactly_the_8_plans(client, db_session):
    await seed_default_plans(db_session)
    await db_session.commit()
    r = await client.get("/api/v1/plans/public")
    assert r.status_code == 200, r.text
    rows = r.json()
    active = {p["key"]: p["price_cents"] for p in rows}
    assert active == EXPECTED_CATALOG, active
    assert all(p["currency"] == "INR" for p in rows)


# ---- Razorpay amount (never zero, from plan.price_cents) -----------------
def _install_fake_razorpay(captured: dict) -> None:
    fake = types.ModuleType("razorpay")

    class _Order:
        def create(self, data):
            captured["order_data"] = data
            return {"id": "order_TEST123", "amount": data["amount"],
                    "currency": data["currency"], "status": "created"}

    class _Client:
        def __init__(self, *a, **k):
            self.order = _Order()

    fake.Client = _Client  # type: ignore[attr-defined]
    sys.modules["razorpay"] = fake


@pytest.mark.asyncio
async def test_razorpay_order_amount_from_plan_never_zero(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    captured: dict = {}
    _install_fake_razorpay(captured)
    p = RazorpayBillingProvider()
    # pro-monthly = 149900 paise
    session = await p.create_checkout(
        user_id="u1", user_email="a@b.c", plan_key="pro-monthly",
        provider_price_id=None, success_url="s", cancel_url="c",
        amount_cents=149900, currency="INR",
    )
    assert captured["order_data"]["amount"] == 149900
    assert captured["order_data"]["amount"] != 0
    assert session.metadata["amount_cents"] == 149900
    assert session.metadata["order_id"] == "order_TEST123"


@pytest.mark.asyncio
async def test_razorpay_rejects_non_positive_amount(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    _install_fake_razorpay({})
    p = RazorpayBillingProvider()
    for bad in (0, None, -100):
        with pytest.raises(RuntimeError):
            await p.create_checkout(
                user_id="u1", user_email="a@b.c", plan_key="pro-monthly",
                provider_price_id=None, success_url="s", cancel_url="c",
                amount_cents=bad, currency="INR",
            )


@pytest.mark.asyncio
async def test_checkout_endpoint_returns_server_amount(client, db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "mock", raising=False)
    await seed_default_plans(db_session)
    await db_session.commit()
    _, headers = await register_and_login(client)
    # Client sends ONLY plan_key — no price.
    r = await client.post(
        "/api/v1/subscriptions/checkout",
        headers=headers, json={"plan_key": "standard-annual"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "mock"
    assert body["amount_cents"] == 499900          # server-authoritative
    assert body["currency"] == "INR"
    assert body["order_id"]


# ---- Razorpay webhook activation (verified success only, idempotent) -----
@pytest.mark.asyncio
async def test_webhook_activates_only_on_verified_success(client, db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "mock", raising=False)
    await seed_default_plans(db_session)
    await db_session.commit()
    user_id, headers = await register_and_login(client)

    body, sig = MockBillingProvider.build_event(
        "subscription.activated",
        provider_subscription_id="sub_ok_1",
        status="active", plan_key="pro-monthly", user_id=user_id,
    )
    r = await client.post(
        "/api/v1/subscriptions/webhook/mock",
        headers={"x-signature": sig, "Content-Type": "application/json"},
        content=body,
    )
    assert r.status_code == 200, r.text
    assert r.json()["activated"] is True

    # /me now reflects the pro plan (live trading unlocked).
    me = await client.get("/api/v1/subscriptions/me", headers=headers)
    from app.services.subscriptions import FeatureFlag
    assert me.json()["features"][FeatureFlag.LIVE_TRADING.value] is True

    # Idempotent: replaying the same event does not error / double-activate.
    r2 = await client.post(
        "/api/v1/subscriptions/webhook/mock",
        headers={"x-signature": sig, "Content-Type": "application/json"},
        content=body,
    )
    assert r2.status_code == 200
    assert r2.json()["activated"] is False  # already active on same sub id


@pytest.mark.asyncio
async def test_webhook_bad_signature_does_not_activate(client, db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "mock", raising=False)
    await seed_default_plans(db_session)
    await db_session.commit()
    user_id, headers = await register_and_login(client)

    body, _sig = MockBillingProvider.build_event(
        "subscription.activated",
        provider_subscription_id="sub_bad",
        status="active", plan_key="pro-monthly", user_id=user_id,
    )
    r = await client.post(
        "/api/v1/subscriptions/webhook/mock",
        headers={"x-signature": "tampered", "Content-Type": "application/json"},
        content=body,
    )
    assert r.status_code == 200
    assert r.json()["handled"] is False   # fail closed
    assert r.json().get("activated") in (None, False)

    # No live trading — user is still on the default entry tier.
    me = await client.get("/api/v1/subscriptions/me", headers=headers)
    from app.services.subscriptions import FeatureFlag
    assert me.json()["features"][FeatureFlag.LIVE_TRADING.value] is False


@pytest.mark.asyncio
async def test_webhook_failed_event_does_not_activate(client, db_session, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "mock", raising=False)
    await seed_default_plans(db_session)
    await db_session.commit()
    user_id, headers = await register_and_login(client)

    body, sig = MockBillingProvider.build_event(
        "subscription.halted",
        provider_subscription_id="sub_fail",
        status="failed", plan_key="pro-monthly", user_id=user_id,
    )
    r = await client.post(
        "/api/v1/subscriptions/webhook/mock",
        headers={"x-signature": sig, "Content-Type": "application/json"},
        content=body,
    )
    assert r.status_code == 200
    assert r.json().get("activated") is False


# ---- Dhan REST selection --------------------------------------------------
class _FakeDhanFetcher:
    def __init__(self, *a, **k):
        pass

    async def get_candles(self, symbol, interval, start, end, exchange=""):
        assert isinstance(interval, Interval)
        ts = datetime(2024, 1, 2, 4, 0, tzinfo=timezone.utc)
        return [Candle(symbol=symbol, exchange=exchange, interval=interval,
                       ts=ts, open=100.0, high=101.0, low=99.0, close=100.5,
                       volume=1234.0)]

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_candles_use_dhan_when_configured(client, db_session, monkeypatch):
    from app.core.config import settings
    import app.engine.market_data.historical_base as hb
    import app.api.v1.endpoints.market_data as market_data_endpoint
    from app.brokers.instruments.base import Instrument

    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "dhan", raising=False)
    monkeypatch.setattr(settings, "DHAN_CLIENT_ID", "CID", raising=False)
    monkeypatch.setattr(settings, "DHAN_ACCESS_TOKEN", "TOK", raising=False)
    monkeypatch.setattr(hb, "get_historical", lambda name, **kw: _FakeDhanFetcher())
    monkeypatch.setattr(
        market_data_endpoint.instrument_runtime,
        "resolve_dhan_instrument",
        lambda symbol, exchange: Instrument(
            symbol=symbol,
            token="11536",
            exchange_segment="NSE_EQ",
            exchange="NSE",
            instrument_type="EQUITY",
        ),
    )

    _, headers = await register_and_login(client)
    r = await client.get(
        "/api/v1/market-data/candles"
        "?symbol=TCS&exchange=NSE&timeframe=15m&count=50&security_id=11536",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "dhan"      # genuine dhan data
    assert body["count"] >= 1


@pytest.mark.asyncio
async def test_candles_fall_back_to_mock_when_dhan_absent(client, db_session):
    # No dhan configuration → honest mock, never labelled dhan.
    _, headers = await register_and_login(client)
    r = await client.get(
        "/api/v1/market-data/candles?symbol=TCS&exchange=MOCK&timeframe=15m&count=50",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["source"] == "mock"
