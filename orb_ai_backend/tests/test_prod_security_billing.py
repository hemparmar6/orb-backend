"""Task 5 — Production security hardening + billing fail-closed.

Verifies:
- No default production admin credentials are usable; dev/test defaults preserved.
- Production billing fails closed (never silently instantiates MockBillingProvider).
- Production config fail-fast for JWT / encryption / CORS / database / billing.
- Existing webhook signature verification still rejects bad/missing signatures.

Notes:
- ``Settings`` is constructed explicitly per test (its ``@model_validator``
  runs the production fail-fast checks). JWT_SECRET_KEY / ENCRYPTION_KEY /
  DATABASE_URL come from the test env (conftest), so a production Settings is
  valid unless we deliberately break one field.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.exceptions import BillingConfigurationError
from app.services.subscriptions import (
    MockBillingProvider,
    RazorpayBillingProvider,
    get_billing_provider,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _prod_settings(**overrides) -> Settings:
    base = dict(
        APP_ENV="production",
        DEBUG=False,
        CORS_ORIGINS=["https://orb-ai.co.in"],
        BILLING_PROVIDER="noop",
    )
    base.update(overrides)
    return Settings(**base)


def _load_seed_admin():
    for candidate in (
        Path("/app/orb_ai_backend/scripts/seed_admin.py"),
        Path(__file__).resolve().parents[1] / "scripts" / "seed_admin.py",
    ):
        if candidate.exists():
            spec = importlib.util.spec_from_file_location("seed_admin_t5", str(candidate))
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
    raise FileNotFoundError("seed_admin.py not found")


SEED = _load_seed_admin()


# ==========================================================================
# 1. Admin credentials — no usable production defaults.
# ==========================================================================


def test_admin_seed_production_requires_explicit_credentials(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ADMIN_SEED_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_SEED_PASSWORD", raising=False)
    with pytest.raises(SEED.AdminSeedConfigError):
        SEED._resolve_admin_credentials()


def test_admin_seed_production_rejects_known_default_email(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_SEED_EMAIL", "admin@orb.com")
    monkeypatch.setenv("ADMIN_SEED_PASSWORD", "AStrongPassword123")
    with pytest.raises(SEED.AdminSeedConfigError):
        SEED._resolve_admin_credentials()


def test_admin_seed_production_rejects_known_default_password(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_SEED_EMAIL", "ceo@orb-ai.co.in")
    monkeypatch.setenv("ADMIN_SEED_PASSWORD", "Admin@123")
    with pytest.raises(SEED.AdminSeedConfigError):
        SEED._resolve_admin_credentials()


def test_admin_seed_production_rejects_weak_password(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_SEED_EMAIL", "ceo@orb-ai.co.in")
    monkeypatch.setenv("ADMIN_SEED_PASSWORD", "short")
    with pytest.raises(SEED.AdminSeedConfigError):
        SEED._resolve_admin_credentials()


def test_admin_seed_production_valid_explicit_credentials(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_SEED_EMAIL", "Ceo@Orb-AI.co.in")
    monkeypatch.setenv("ADMIN_SEED_PASSWORD", "Sup3rStr0ng!Pass")
    email, password, full_name = SEED._resolve_admin_credentials()
    assert email == "ceo@orb-ai.co.in"           # normalized
    assert password == "Sup3rStr0ng!Pass"
    assert password not in SEED._BLOCKED_ADMIN_PASSWORDS


def test_admin_seed_dev_defaults_preserved(monkeypatch):
    """Existing development/test seeding behaviour is unchanged."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("ADMIN_SEED_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_SEED_PASSWORD", raising=False)
    email, password, _ = SEED._resolve_admin_credentials()
    assert email == "admin@orb.com"
    assert password == "Admin@123"


# ==========================================================================
# 2. Billing fail-closed in production.
# ==========================================================================


def test_prod_razorpay_missing_credentials_is_startup_failure():
    with pytest.raises(ValueError) as exc:
        _prod_settings(BILLING_PROVIDER="razorpay")
    assert "RAZORPAY" in str(exc.value)


def test_prod_razorpay_invalid_config_missing_webhook_fails():
    with pytest.raises(ValueError) as exc:
        _prod_settings(
            BILLING_PROVIDER="razorpay",
            RAZORPAY_KEY_ID="rzp_live_abc",
            RAZORPAY_KEY_SECRET="secret_value",
            # RAZORPAY_WEBHOOK_SECRET intentionally omitted → fail closed
        )
    assert "RAZORPAY_WEBHOOK_SECRET" in str(exc.value)


def test_prod_razorpay_valid_config_initializes(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret_value")
    s = _prod_settings(
        BILLING_PROVIDER="razorpay",
        RAZORPAY_KEY_ID="rzp_live_abc",
        RAZORPAY_KEY_SECRET="secret_value",
        RAZORPAY_WEBHOOK_SECRET="whsec_value",
    )
    provider = get_billing_provider(s)
    assert isinstance(provider, RazorpayBillingProvider)
    assert provider.name == "razorpay"


def test_prod_billing_factory_never_returns_mock_when_unconfigured(monkeypatch):
    """Even when Settings is valid, if the provider is unconfigured at runtime
    the factory MUST raise in production — never a MockBillingProvider."""
    # Valid Settings (creds present as settings fields) so construction passes...
    s = _prod_settings(
        BILLING_PROVIDER="razorpay",
        RAZORPAY_KEY_ID="rzp_live_abc",
        RAZORPAY_KEY_SECRET="secret_value",
        RAZORPAY_WEBHOOK_SECRET="whsec_value",
    )
    # ...but the runtime provider reads os.environ, which we clear.
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(BillingConfigurationError):
        get_billing_provider(s)


def test_prod_mock_billing_is_rejected():
    with pytest.raises(ValueError) as exc:
        _prod_settings(BILLING_PROVIDER="mock")
    assert "mock" in str(exc.value).lower()


def test_prod_mock_billing_factory_also_fails_closed():
    """Defence in depth: the factory refuses mock in production directly."""
    # Build a dev settings then flip to production-like via a lightweight stub
    # so we exercise the factory branch without the Settings validator.
    class _S:
        BILLING_PROVIDER = "mock"
        is_production = True

    with pytest.raises(BillingConfigurationError):
        get_billing_provider(_S())


# ==========================================================================
# 3. Development/test billing still works (mock allowed).
# ==========================================================================


def test_dev_mock_billing_works():
    s = Settings(APP_ENV="test", BILLING_PROVIDER="mock")
    provider = get_billing_provider(s)
    assert isinstance(provider, MockBillingProvider)


def test_dev_razorpay_unconfigured_falls_back_to_mock(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    s = Settings(APP_ENV="development", BILLING_PROVIDER="razorpay")
    provider = get_billing_provider(s)
    assert isinstance(provider, MockBillingProvider)  # dev convenience preserved


# ==========================================================================
# 4. Production security config fail-fast (do not weaken existing guards).
# ==========================================================================


def test_prod_wildcard_cors_rejected():
    with pytest.raises(ValueError):
        _prod_settings(CORS_ORIGINS=["*"])


def test_prod_default_jwt_secret_rejected():
    with pytest.raises(ValueError) as exc:
        _prod_settings(JWT_SECRET_KEY="dev-insecure-secret-change-me")
    assert "JWT_SECRET_KEY" in str(exc.value)


def test_prod_dev_default_database_url_rejected():
    with pytest.raises(ValueError) as exc:
        _prod_settings(DATABASE_URL="postgresql+asyncpg://orb:orb@localhost:5432/orb_ai")
    assert "DATABASE_URL" in str(exc.value)


def test_dev_defaults_do_not_fail_fast():
    """Non-production boot must be unaffected by the production guards."""
    s = Settings(APP_ENV="development")
    assert s.is_production is False


# ==========================================================================
# 5. Webhook signature verification still enforced.
# ==========================================================================


@pytest.mark.asyncio
async def test_mock_webhook_rejects_bad_signature():
    p = MockBillingProvider()
    body, _sig = MockBillingProvider.build_event(
        "subscription.activated", provider_subscription_id="sub_1"
    )
    out = await p.handle_webhook(body=body, signature="tampered")
    assert out["event"] == "invalid_signature"


@pytest.mark.asyncio
async def test_razorpay_webhook_missing_signature_rejected(monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "whsec_value")
    p = RazorpayBillingProvider()
    out = await p.handle_webhook(body=b'{"event":"x"}', signature=None)
    assert out["event"] == "invalid_signature"
