"""Subscription & feature-gate services (Module 8).

Public API:
    - FeatureFlag enum
    - PlanTier / SubscriptionStatus (re-exported from models)
    - FeatureGate — the *only* thing app code should call.
    - BillingProvider ABC + noop/stripe implementations.
    - get_billing_provider() — pick provider by env.
"""

from app.models.subscription import (
    PlanTier,
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)
from app.services.subscriptions.feature_flags import FeatureFlag
from app.services.subscriptions.feature_gate import FeatureGate
from app.services.subscriptions.plan_seed import (
    DEFAULT_PLANS,
    DEFAULT_STRATEGIES,
    seed_default_plans,
    seed_default_strategies,
)
from app.services.subscriptions.providers import (
    BillingProvider,
    CheckoutSession,
    MockBillingProvider,
    NoopBillingProvider,
    RazorpayBillingProvider,
    RefundResult,
    StripeBillingProvider,
    get_billing_provider,
)

__all__ = [
    "PlanTier",
    "SubscriptionPlan",
    "SubscriptionStatus",
    "UserSubscription",
    "FeatureFlag",
    "FeatureGate",
    "DEFAULT_PLANS",
    "DEFAULT_STRATEGIES",
    "seed_default_plans",
    "seed_default_strategies",
    "BillingProvider",
    "CheckoutSession",
    "RefundResult",
    "NoopBillingProvider",
    "MockBillingProvider",
    "RazorpayBillingProvider",
    "StripeBillingProvider",
    "get_billing_provider",
]
