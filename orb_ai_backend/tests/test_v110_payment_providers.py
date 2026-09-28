"""Phase 1 v1.1.0 delta tests: Mock + Razorpay providers, refund API.

These tests exercise the new billing provider layer added in Phase 1
without touching real Stripe / Razorpay endpoints. The mock provider
performs the full lifecycle (checkout -> signed webhook -> refund) so
downstream subscription flows can be validated end-to-end.
"""
from __future__ import annotations

import json
import os

import pytest

from app.services.subscriptions import (
    MockBillingProvider,
    RazorpayBillingProvider,
    get_billing_provider,
)
from tests._module8_helpers import register_and_login


# ---- Mock provider unit tests -------------------------------------------


@pytest.mark.asyncio
async def test_mock_provider_creates_checkout_session():
    p = MockBillingProvider()
    assert p.name == "mock"
    assert p.is_configured() is True
    session = await p.create_checkout(
        user_id="u1", user_email="a@b.c", plan_key="pro",
        provider_price_id=None,
        success_url="https://ok", cancel_url="https://cancel",
    )
    assert session.provider == "mock"
    assert session.session_id.startswith("mock_sess_")
    assert "plan_key=pro" in session.url


@pytest.mark.asyncio
async def test_mock_provider_signs_webhook_and_verifies():
    p = MockBillingProvider()
    body, sig = MockBillingProvider.build_event(
        "subscription.activated",
        provider_subscription_id="sub_123",
        status="active",
        plan_key="pro",
        user_id="u42",
    )
    payload = await p.handle_webhook(body=body, signature=sig)
    assert payload["event"] == "subscription.activated"
    assert payload["provider_subscription_id"] == "sub_123"
    assert payload["plan_key"] == "pro"
    assert payload["status"] == "active"


@pytest.mark.asyncio
async def test_mock_provider_rejects_bad_signature():
    p = MockBillingProvider()
    body = json.dumps({"event": "fake"}).encode()
    payload = await p.handle_webhook(body=body, signature="not-the-real-hmac")
    assert payload["event"] == "invalid_signature"


@pytest.mark.asyncio
async def test_mock_provider_refund_succeeds():
    p = MockBillingProvider()
    r = await p.refund(payment_reference="pay_abc", amount_cents=5000, reason="test")
    assert r.provider == "mock"
    assert r.status == "succeeded"
    assert r.amount_cents == 5000
    assert r.refund_id.startswith("mock_rf_")


# ---- Razorpay provider (skeleton without SDK) ---------------------------


@pytest.mark.asyncio
async def test_razorpay_provider_not_configured_by_default():
    # Clear env for isolation
    for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"):
        os.environ.pop(k, None)
    p = RazorpayBillingProvider()
    assert p.is_configured() is False


@pytest.mark.asyncio
async def test_razorpay_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "top-secret")
    p = RazorpayBillingProvider()
    body = json.dumps({"event": "subscription.activated"}).encode()
    payload = await p.handle_webhook(body=body, signature="bad-signature")
    assert payload["event"] == "invalid_signature"


# ---- Factory selection --------------------------------------------------


@pytest.mark.asyncio
async def test_factory_selects_mock_when_configured(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "mock", raising=False)
    provider = get_billing_provider()
    assert provider.name == "mock"


@pytest.mark.asyncio
async def test_factory_falls_back_to_mock_when_razorpay_unconfigured(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "razorpay", raising=False)
    for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"):
        monkeypatch.delenv(k, raising=False)
    provider = get_billing_provider()
    assert provider.name == "mock", (
        "Razorpay must fall back to mock when credentials are missing"
    )


# ---- Refund admin endpoint ---------------------------------------------


@pytest.mark.asyncio
async def test_admin_refund_endpoint_with_mock_provider(client, admin_headers, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "mock", raising=False)

    r = await client.post(
        "/api/v1/subscriptions/admin/refund",
        headers=admin_headers,
        json={
            "payment_reference": "pay_test_123",
            "amount_cents": 5000,
            "reason": "customer_request",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "mock"
    assert body["status"] == "succeeded"
    assert body["amount_cents"] == 5000


@pytest.mark.asyncio
async def test_admin_refund_requires_admin(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "mock", raising=False)
    _, headers = await register_and_login(client)
    r = await client.post(
        "/api/v1/subscriptions/admin/refund",
        headers=headers,
        json={"payment_reference": "pay_x"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_mock_webhook_only_when_mock_active(
    client, admin_headers, monkeypatch
):
    from app.core.config import settings

    # 1. With mock provider active -> succeeds
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "mock", raising=False)
    r = await client.post(
        "/api/v1/subscriptions/admin/mock-webhook",
        headers=admin_headers,
        json={
            "event": "subscription.activated",
            "provider_subscription_id": "sub_1",
            "status": "active",
            "plan_key": "pro",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["handled"] is True
    assert body["result"]["event"] == "subscription.activated"

    # 2. With noop active -> 400
    monkeypatch.setattr(settings, "BILLING_PROVIDER", "noop", raising=False)
    r2 = await client.post(
        "/api/v1/subscriptions/admin/mock-webhook",
        headers=admin_headers,
        json={
            "event": "subscription.activated",
            "provider_subscription_id": "sub_2",
        },
    )
    assert r2.status_code == 400
