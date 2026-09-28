"""Billing provider abstraction (Module 8).

Add a new provider by subclassing ``BillingProvider`` and registering it in
``get_billing_provider()``. Everything else in the app talks *only* to the
``BillingProvider`` interface — no direct Stripe / Paddle / Razorpay calls
anywhere.
"""
from __future__ import annotations

import abc
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.core.exceptions import BillingConfigurationError
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CheckoutSession:
    """Provider-agnostic checkout session hand-off."""

    provider: str
    session_id: str
    url: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


@dataclass
class RefundResult:
    """Provider-agnostic refund outcome."""

    provider: str
    refund_id: str
    amount_cents: int
    status: str  # "succeeded" | "pending" | "failed"
    metadata: dict[str, Any] = field(default_factory=dict)


class BillingProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def is_configured(self) -> bool: ...

    @abc.abstractmethod
    async def create_checkout(
        self,
        *,
        user_id: str,
        user_email: str,
        plan_key: str,
        provider_price_id: Optional[str],
        success_url: str,
        cancel_url: str,
        amount_cents: Optional[int] = None,
        currency: str = "INR",
    ) -> CheckoutSession: ...

    @abc.abstractmethod
    async def cancel_subscription(self, *, provider_subscription_id: str) -> bool: ...

    async def refund(
        self,
        *,
        payment_reference: str,
        amount_cents: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> RefundResult:
        """Issue a full or partial refund. Providers may override."""
        raise NotImplementedError(f"refund not supported by {self.name!r}")

    @abc.abstractmethod
    async def handle_webhook(self, *, body: bytes, signature: Optional[str]) -> dict[str, Any]:
        """Return a normalized dict: {event, provider_subscription_id, status, plan_key}."""


class NoopBillingProvider(BillingProvider):
    """Default provider — no external billing. Fine for free tier + tests."""

    name = "noop"

    def is_configured(self) -> bool:
        return True

    async def create_checkout(self, **kwargs: Any) -> CheckoutSession:
        # Noop just "activates" the plan for free.
        return CheckoutSession(
            provider="noop",
            session_id=f"noop_{kwargs.get('plan_key','standard')}",
            url=None,
            metadata={"reason": "noop_provider_activates_immediately"},
        )

    async def cancel_subscription(self, *, provider_subscription_id: str) -> bool:
        return True

    async def handle_webhook(self, *, body: bytes, signature: Optional[str]) -> dict[str, Any]:
        return {"event": "ignored", "provider_subscription_id": None,
                "status": "active", "plan_key": None}


class StripeBillingProvider(BillingProvider):
    """Stripe provider skeleton.

    Kept as an *interface* — we validate credentials + shape here but do
    not import ``stripe`` at module import time so tests do not require
    the SDK. When ``STRIPE_SECRET_KEY`` is set and the ``stripe`` package
    is installed, ``create_checkout`` calls the real API.
    """

    name = "stripe"

    def is_configured(self) -> bool:
        return bool(settings.STRIPE_SECRET_KEY)

    async def create_checkout(
        self,
        *,
        user_id: str,
        user_email: str,
        plan_key: str,
        provider_price_id: Optional[str],
        success_url: str,
        cancel_url: str,
        amount_cents: Optional[int] = None,
        currency: str = "INR",
    ) -> CheckoutSession:
        if not self.is_configured():
            raise RuntimeError("Stripe is not configured (STRIPE_SECRET_KEY missing)")
        if not provider_price_id:
            raise RuntimeError(f"Plan '{plan_key}' has no provider_price_id")

        try:
            import stripe  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("stripe SDK not installed") from e

        stripe.api_key = settings.STRIPE_SECRET_KEY
        session = stripe.checkout.Session.create(
            mode="subscription",
            customer_email=user_email,
            line_items=[{"price": provider_price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": user_id, "plan_key": plan_key},
        )
        return CheckoutSession(
            provider="stripe",
            session_id=session["id"],
            url=session.get("url"),
        )

    async def cancel_subscription(self, *, provider_subscription_id: str) -> bool:
        if not self.is_configured():
            return False
        try:
            import stripe  # type: ignore
        except ImportError:  # pragma: no cover
            return False
        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe.Subscription.modify(provider_subscription_id, cancel_at_period_end=True)
        return True

    async def handle_webhook(self, *, body: bytes, signature: Optional[str]) -> dict[str, Any]:
        if not self.is_configured():
            return {"event": "ignored", "provider_subscription_id": None,
                    "status": "active", "plan_key": None}
        try:
            import stripe  # type: ignore
        except ImportError:  # pragma: no cover
            return {"event": "ignored", "provider_subscription_id": None,
                    "status": "active", "plan_key": None}
        webhook_secret = settings.STRIPE_WEBHOOK_SECRET
        if webhook_secret and signature:
            event = stripe.Webhook.construct_event(body, signature, webhook_secret)
        else:
            event = stripe.Event.construct_from(body, stripe.api_key)  # type: ignore
        return {
            "event": event.get("type") if isinstance(event, dict) else event.type,
            "raw": event if isinstance(event, dict) else event.to_dict(),
        }


def get_billing_provider(settings_obj: Any = None) -> BillingProvider:
    """Return the provider configured via ``BILLING_PROVIDER``.

    Supported values: ``mock`` (dev/tests only), ``razorpay`` (primary),
    ``stripe`` (secondary), ``noop`` (v1.0.0 legacy free-tier).

    Fail-closed contract (Task 5):
    - In **production** a selected real processor (razorpay/stripe) whose
      credentials are missing/invalid raises ``BillingConfigurationError``
      instead of silently degrading to mock billing.
    - ``BILLING_PROVIDER=mock`` is refused in production so mock billing can
      never mark a real payment as successful.
    - In development/staging/test the historical behaviour is preserved: an
      unconfigured real processor gracefully falls back to the mock provider
      so local flows never break.
    """
    s = settings_obj if settings_obj is not None else settings
    kind = (s.BILLING_PROVIDER or "noop").lower()
    prod = bool(getattr(s, "is_production", False))

    if kind == "stripe":
        p: BillingProvider = StripeBillingProvider()
        if not p.is_configured():
            if prod:
                raise BillingConfigurationError(
                    "BILLING_PROVIDER=stripe in production but STRIPE_SECRET_KEY "
                    "is missing/invalid — refusing to fall back to mock billing"
                )
            logger.warning("stripe_not_configured_falling_back_to_mock")
            return MockBillingProvider()
        return p
    if kind == "razorpay":
        p = RazorpayBillingProvider()
        if not p.is_configured():
            if prod:
                raise BillingConfigurationError(
                    "BILLING_PROVIDER=razorpay in production but "
                    "RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET are missing/invalid — "
                    "refusing to fall back to mock billing"
                )
            logger.warning("razorpay_not_configured_falling_back_to_mock")
            return MockBillingProvider()
        return p
    if kind == "mock":
        if prod:
            raise BillingConfigurationError(
                "BILLING_PROVIDER=mock is not permitted in production — "
                "mock billing must never confirm real payments"
            )
        return MockBillingProvider()
    return NoopBillingProvider()


# ---------------------------------------------------------------------------
# Mock provider — full lifecycle simulation for end-to-end tests
# ---------------------------------------------------------------------------


class MockBillingProvider(BillingProvider):
    """Deterministic in-memory provider used for tests and Phase 1 flows.

    Simulates the full lifecycle: create, success, failure, activation,
    renewal, refund, webhook delivery. Uses HMAC-SHA256 to sign the
    synthetic webhook payload with ``MOCK_BILLING_SECRET`` so
    ``handle_webhook`` verification code can be exercised without
    hitting a real provider.
    """

    name = "mock"
    _secret_env = "MOCK_BILLING_SECRET"
    _default_secret = "orb-mock-billing-secret"

    def is_configured(self) -> bool:
        return True

    @classmethod
    def _secret(cls) -> bytes:
        import os
        return os.environ.get(cls._secret_env, cls._default_secret).encode()

    @classmethod
    def sign(cls, body: bytes) -> str:
        """Public helper — tests use this to forge a valid webhook."""
        return hmac.new(cls._secret(), body, hashlib.sha256).hexdigest()

    async def create_checkout(
        self,
        *,
        user_id: str,
        user_email: str,
        plan_key: str,
        provider_price_id: Optional[str],
        success_url: str,
        cancel_url: str,
        amount_cents: Optional[int] = None,
        currency: str = "INR",
    ) -> CheckoutSession:
        session_id = f"mock_sess_{secrets.token_hex(8)}"
        logger.info(
            "mock_checkout_created",
            extra={
                "user_id": user_id, "plan_key": plan_key,
                "session_id": session_id, "amount_cents": amount_cents,
            },
        )
        return CheckoutSession(
            provider="mock",
            session_id=session_id,
            url=f"{success_url}?session_id={session_id}&plan_key={plan_key}",
            metadata={
                "user_id": user_id, "user_email": user_email,
                "plan_key": plan_key,
                "order_id": session_id,
                "amount_cents": int(amount_cents or 0),
                "currency": currency,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def cancel_subscription(self, *, provider_subscription_id: str) -> bool:
        logger.info(
            "mock_subscription_cancelled",
            extra={"provider_subscription_id": provider_subscription_id},
        )
        return True

    async def refund(
        self,
        *,
        payment_reference: str,
        amount_cents: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> RefundResult:
        refund_id = f"mock_rf_{secrets.token_hex(6)}"
        logger.info(
            "mock_refund_issued",
            extra={
                "payment_reference": payment_reference,
                "amount_cents": amount_cents,
                "reason": reason,
                "refund_id": refund_id,
            },
        )
        return RefundResult(
            provider="mock",
            refund_id=refund_id,
            amount_cents=int(amount_cents or 0),
            status="succeeded",
            metadata={
                "payment_reference": payment_reference,
                "reason": reason,
                "issued_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def handle_webhook(
        self, *, body: bytes, signature: Optional[str]
    ) -> dict[str, Any]:
        expected = self.sign(body)
        if signature and not hmac.compare_digest(expected, signature):
            logger.warning("mock_webhook_bad_signature")
            return {
                "event": "invalid_signature",
                "provider_subscription_id": None,
                "status": None,
                "plan_key": None,
            }
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return {
                "event": "invalid_payload",
                "provider_subscription_id": None,
                "status": None,
                "plan_key": None,
            }
        return {
            "event": payload.get("event", "unknown"),
            "provider_subscription_id": payload.get("provider_subscription_id"),
            "status": payload.get("status", "active"),
            "plan_key": payload.get("plan_key"),
            "user_id": payload.get("user_id"),
            "success": (
                str(payload.get("event", "")) in {
                    "subscription.activated", "subscription.charged",
                    "payment.captured", "order.paid",
                }
                and str(payload.get("status", "active")).lower() not in {
                    "failed", "cancelled", "canceled", "refunded",
                }
            ),
            "raw": payload,
        }

    # ------------------------------------------------------------------ test helpers
    @classmethod
    def build_event(
        cls,
        event: str,
        *,
        provider_subscription_id: str,
        status: str = "active",
        plan_key: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> tuple[bytes, str]:
        """Return (body_bytes, hmac_signature) for a synthetic webhook."""
        payload = {
            "event": event,
            "provider_subscription_id": provider_subscription_id,
            "status": status,
            "plan_key": plan_key,
            "user_id": user_id,
            "ts": int(time.time()),
        }
        body = json.dumps(payload, sort_keys=True).encode()
        return body, cls.sign(body)


# ---------------------------------------------------------------------------
# Razorpay provider — primary production processor for INR checkout
# ---------------------------------------------------------------------------


class RazorpayBillingProvider(BillingProvider):
    """Razorpay provider skeleton.

    The SDK is imported lazily so tests do not require ``razorpay`` to be
    installed. Webhook signatures are verified with ``HMAC-SHA256`` per
    Razorpay's public spec, which we can implement without the SDK.
    """

    name = "razorpay"

    def is_configured(self) -> bool:
        import os
        return bool(
            os.environ.get("RAZORPAY_KEY_ID")
            and os.environ.get("RAZORPAY_KEY_SECRET")
        )

    async def create_checkout(
        self,
        *,
        user_id: str,
        user_email: str,
        plan_key: str,
        provider_price_id: Optional[str],
        success_url: str,
        cancel_url: str,
        amount_cents: Optional[int] = None,
        currency: str = "INR",
    ) -> CheckoutSession:
        if not self.is_configured():
            raise RuntimeError("Razorpay is not configured (RAZORPAY_KEY_ID/SECRET)")
        # Amount is SERVER-AUTHORITATIVE: it comes from the selected
        # SubscriptionPlan.price_cents (INR paise). We must never create a
        # ₹0 order — reject any missing / non-positive amount up front so a
        # client can never coerce a free order.
        if amount_cents is None or int(amount_cents) <= 0:
            raise RuntimeError(
                f"Refusing to create a Razorpay order with a non-positive amount "
                f"({amount_cents!r} paise) for plan '{plan_key}'"
            )
        amount = int(amount_cents)
        try:
            import razorpay  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("razorpay SDK not installed") from e
        import os
        client = razorpay.Client(
            auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]),
        )
        order = client.order.create(
            {
                "amount": amount,  # server-authoritative INR paise from plan.price_cents
                "currency": (currency or "INR").upper(),
                "payment_capture": 1,
                "notes": {
                    "user_id": user_id,
                    "user_email": user_email,
                    "plan_key": plan_key,
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                },
            }
        )
        return CheckoutSession(
            provider="razorpay",
            session_id=order["id"],
            url=success_url,
            metadata={
                "order_id": order["id"],
                "amount_cents": int(order.get("amount", amount)),
                "currency": order.get("currency", (currency or "INR").upper()),
                "plan_key": plan_key,
                "order": order,
            },
        )

    async def cancel_subscription(self, *, provider_subscription_id: str) -> bool:
        if not self.is_configured():
            return False
        try:
            import razorpay  # type: ignore
        except ImportError:  # pragma: no cover
            return False
        import os
        client = razorpay.Client(
            auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]),
        )
        client.subscription.cancel(provider_subscription_id, {"cancel_at_cycle_end": 1})
        return True

    async def refund(
        self,
        *,
        payment_reference: str,
        amount_cents: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> RefundResult:
        if not self.is_configured():
            raise RuntimeError("Razorpay is not configured")
        try:
            import razorpay  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("razorpay SDK not installed") from e
        import os
        client = razorpay.Client(
            auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]),
        )
        payload: dict[str, Any] = {"payment_id": payment_reference}
        if amount_cents is not None:
            payload["amount"] = int(amount_cents)
        if reason:
            payload["notes"] = {"reason": reason}
        refund = client.payment.refund(payment_reference, payload)
        return RefundResult(
            provider="razorpay",
            refund_id=refund.get("id", ""),
            amount_cents=int(refund.get("amount", amount_cents or 0)),
            status=refund.get("status", "pending"),
            metadata={"raw": refund},
        )

    async def handle_webhook(
        self, *, body: bytes, signature: Optional[str]
    ) -> dict[str, Any]:
        import os
        secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
        if secret:
            # A webhook secret is configured: require a valid signature.
            # Missing OR mismatched signatures are rejected (no business action)
            # so a caller cannot bypass verification by omitting the header.
            if not signature:
                logger.warning("razorpay_webhook_missing_signature")
                return {
                    "event": "invalid_signature",
                    "provider_subscription_id": None,
                    "status": None,
                    "plan_key": None,
                }
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                logger.warning("razorpay_webhook_bad_signature")
                return {
                    "event": "invalid_signature",
                    "provider_subscription_id": None,
                    "status": None,
                    "plan_key": None,
                }
        try:
            payload = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return {
                "event": "invalid_payload",
                "provider_subscription_id": None,
                "status": None,
                "plan_key": None,
            }
        event = payload.get("event", "unknown")
        container = payload.get("payload") or {}
        # Razorpay success events carry their business object under one of
        # ``subscription`` / ``payment`` / ``order``. We look at all three so
        # payment.captured / order.paid (one-off plan purchases) and
        # subscription.* (recurring) are all normalised the same way.
        entity_body: dict[str, Any] = {}
        for kind in ("subscription", "payment", "order"):
            ent = (container.get(kind) or {}).get("entity")
            if isinstance(ent, dict) and ent:
                entity_body = ent
                break
        notes = entity_body.get("notes") or {}
        # A subscription may become active ONLY on a verified successful event.
        success_events = {
            "payment.captured", "order.paid",
            "subscription.activated", "subscription.charged",
        }
        raw_status = str(entity_body.get("status") or "").lower()
        is_success = event in success_events and raw_status not in {
            "failed", "cancelled", "canceled", "refunded",
        }
        return {
            "event": event,
            "provider_subscription_id": (
                entity_body.get("id")
                or entity_body.get("order_id")
                or entity_body.get("subscription_id")
            ),
            "status": entity_body.get("status", "active" if is_success else None),
            "plan_key": notes.get("plan_key"),
            "user_id": notes.get("user_id"),
            "success": is_success,
            "raw": payload,
        }
