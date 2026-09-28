"""Default subscription plans + strategy catalog seed (ORB AI 2.0).

Idempotent. Preserves all legacy plan keys so existing v1.0.0 / v1.1.0 rows
load unchanged.

ORB AI 2.0.1 canonical catalog — EXACTLY 8 active, server-authoritative plans
(prices are INR paise, ``price_cents``):

  • starter-monthly  ₹50/month        →    5000
  • starter-annual   ₹499/year        →   49900
  • standard-monthly ₹499/month       →   49900
  • standard-annual  ₹4,999/year      →  499900
  • pro-monthly      ₹1,499/month     →  149900
  • pro-annual       ₹14,999/year     → 1499900
  • elite-monthly    ₹2,999/month     →  299900
  • elite-annual     ₹29,999/year     → 2999900

Legacy plans (``free``, the old single-interval ``standard`` / ``pro`` keys,
``starter`` / ``elite`` / ``enterprise``) are RETAINED in the DB with
``is_active = False`` so existing ``user_subscriptions`` rows never orphan.
They are deactivated (not deleted) and never appear in the public catalog.

Prices are server authoritative — the mobile app must never hardcode or
recompute them; Razorpay orders are created from ``plan.price_cents``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.strategy_catalog import (
    StrategyCatalog,
    StrategyDifficulty,
    StrategyRiskLevel,
    StrategyStatus as CatalogStatus,
)
from app.models.subscription import PlanTier, SubscriptionPlan
from app.services.subscriptions.feature_flags import FeatureFlag as F

logger = get_logger(__name__)


def _feats(*flags: F, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {f.value: True for f in flags}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# Per-tier feature bags (shared by the monthly + annual variant of each tier).
# ---------------------------------------------------------------------------
STARTER_FEATURES = _feats(
    F.PAPER_TRADING, F.MANUAL_TRADING,
    F.REPORT_FORMAT_PDF, F.REPORT_FORMAT_CSV,
    max_saved_strategies=1,
    max_backtests_per_month=15,
    live_trading=False,
    ai_assistant=False,
    no_code_builder=True,
    live_charts=True,
    offline_charts=True,
    historical_data_download=False,
    basic_analytics=True,
    community_blueprints_import=True,
    portfolio_analytics=False,
    cloud_sync=False,
    priority_support=False,
    email_support=True,
    advanced_risk_management=False,
    blueprint_sharing=False,
    multi_device_sync=False,
)

STANDARD_FEATURES = _feats(
    F.PAPER_TRADING, F.MANUAL_TRADING,
    F.REPORT_FORMAT_PDF, F.REPORT_FORMAT_CSV,
    max_saved_strategies=2,
    max_backtests_per_month=30,
    live_trading=False,
    ai_assistant=True,          # basic
    ai_assistant_tier="basic",
    no_code_builder=True,
    live_charts=True,
    offline_charts=True,
    historical_data_download=True,
    basic_analytics=True,
    community_blueprints_import=True,
    portfolio_analytics=False,
    cloud_sync=False,
    priority_support=False,
    email_support=True,
    advanced_risk_management=False,
    blueprint_sharing=False,
    multi_device_sync=False,
)

PRO_FEATURES = _feats(
    F.PAPER_TRADING, F.MANUAL_TRADING, F.LIVE_TRADING, F.MULTI_BROKER,
    F.AUTOMATION, F.ADVANCED_ANALYTICS,
    F.AI_TRADE_REVIEW, F.AI_RECOMMENDATIONS,
    F.NOTIFY_TELEGRAM, F.NOTIFY_PUSH,
    F.REPORT_FORMAT_PDF, F.REPORT_FORMAT_CSV, F.REPORT_FORMAT_XLSX,
    F.REPORT_WEEKLY_DIGEST, F.REPORT_DAILY_DIGEST, F.REPORT_MONTHLY_DIGEST,
    F.REPORT_TYPE_STRATEGY_PERFORMANCE, F.REPORT_TYPE_BROKER_ACTIVITY,
    F.PORTFOLIO_RISK_MANAGER,
    max_saved_strategies=-1,      # unlimited
    max_backtests_per_month=-1,   # unlimited
    live_trading=True,
    ai_assistant=True,
    ai_assistant_tier="advanced",
    no_code_builder=True,
    live_charts=True,
    offline_charts=True,
    historical_data_download=True,
    basic_analytics=True,
    community_blueprints_import=True,
    portfolio_analytics=True,
    cloud_sync=True,
    priority_support=True,
    email_support=True,
    advanced_risk_management=True,
    blueprint_sharing=True,
    multi_device_sync=True,
)

ELITE_FEATURES = _feats(
    F.PAPER_TRADING, F.MANUAL_TRADING, F.LIVE_TRADING, F.MULTI_BROKER,
    F.AUTOMATION, F.ADVANCED_ANALYTICS,
    F.AI_STRATEGY_BUILDER, F.AI_STRATEGY_OPTIMIZER,
    F.AI_TRADE_REVIEW, F.AI_RECOMMENDATIONS,
    F.NOTIFY_TELEGRAM, F.NOTIFY_PUSH,
    F.REPORT_FORMAT_PDF, F.REPORT_FORMAT_CSV, F.REPORT_FORMAT_XLSX,
    F.REPORT_WEEKLY_DIGEST, F.REPORT_DAILY_DIGEST, F.REPORT_MONTHLY_DIGEST,
    F.REPORT_TYPE_STRATEGY_PERFORMANCE, F.REPORT_TYPE_BROKER_ACTIVITY,
    F.PORTFOLIO_RISK_MANAGER, F.OPTIONS_STRATEGY_PACK, F.SWING_STRATEGY_PACK,
    F.STRATEGY_MARKETPLACE, F.ORB_WALLET, F.COUPONS, F.AFFILIATE_PROGRAM,
    max_saved_strategies=-1,
    max_backtests_per_month=-1,
    live_trading=True,
    ai_assistant=True,
    ai_assistant_tier="advanced",
    no_code_builder=True,
    live_charts=True,
    offline_charts=True,
    historical_data_download=True,
    basic_analytics=True,
    portfolio_analytics=True,
    community_blueprints_import=True,
    cloud_sync=True,
    priority_support=True,
    email_support=True,
    advanced_risk_management=True,
    blueprint_sharing=True,
    multi_device_sync=True,
)


def _plan(
    key: str, name: str, tier: PlanTier, price_cents: int, interval: str,
    *, display_order: int, description: str, features: dict[str, Any],
    max_running_bots: int, max_open_positions: int,
    automation_enabled: bool, paper_trading_only: bool,
    ai_features_enabled: bool, unlimited_bots: bool = False,
    is_active: bool = True,
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "tier": tier,
        "price_cents": price_cents,
        "currency": "INR",
        "interval": interval,
        "description": description,
        "display_order": display_order,
        "max_running_bots": max_running_bots,
        "max_open_positions": max_open_positions,
        "automation_enabled": automation_enabled,
        "paper_trading_only": paper_trading_only,
        "ai_features_enabled": ai_features_enabled,
        "unlimited_bots": unlimited_bots,
        "is_active": is_active,
        "features": features,
    }


# ---- ORB AI 2.0.1 — the 8 canonical, active, server-authoritative plans ----
DEFAULT_PLANS: list[dict[str, Any]] = [
    # ---- Starter (₹50/mo, ₹499/yr) ----
    _plan("starter-monthly", "Starter", PlanTier.STARTER, 5000, "monthly",
          display_order=1,
          description="Entry tier — paper trading, 1 saved strategy, 15 backtests / month.",
          features=STARTER_FEATURES,
          max_running_bots=1, max_open_positions=3,
          automation_enabled=False, paper_trading_only=True,
          ai_features_enabled=False),
    _plan("starter-annual", "Starter (Annual)", PlanTier.STARTER, 49900, "annual",
          display_order=2,
          description="Starter billed annually — save vs monthly. All Starter features.",
          features=STARTER_FEATURES,
          max_running_bots=1, max_open_positions=3,
          automation_enabled=False, paper_trading_only=True,
          ai_features_enabled=False),

    # ---- Standard (₹499/mo, ₹4,999/yr) ----
    _plan("standard-monthly", "Standard", PlanTier.STANDARD, 49900, "monthly",
          display_order=3,
          description=(
              "1 active bot, 2 saved strategies, 30 backtests / month, paper trading, "
              "no-code builder, charts, historical data, basic AI assistant, basic "
              "analytics, community blueprints, email support."
          ),
          features=STANDARD_FEATURES,
          max_running_bots=1, max_open_positions=5,
          automation_enabled=False, paper_trading_only=True,
          ai_features_enabled=True),
    _plan("standard-annual", "Standard (Annual)", PlanTier.STANDARD, 499900, "annual",
          display_order=4,
          description="Standard billed annually — save vs monthly. All Standard features.",
          features=STANDARD_FEATURES,
          max_running_bots=1, max_open_positions=5,
          automation_enabled=False, paper_trading_only=True,
          ai_features_enabled=True),

    # ---- Pro (₹1,499/mo, ₹14,999/yr) ----
    _plan("pro-monthly", "Pro", PlanTier.PRO, 149900, "monthly",
          display_order=5,
          description=(
              "10 active bots, unlimited strategies & backtests, advanced AI assistant, "
              "live trading, portfolio analytics, cloud sync, priority support, advanced "
              "risk management, blueprint sharing, multi-device sync."
          ),
          features=PRO_FEATURES,
          max_running_bots=10, max_open_positions=50,
          automation_enabled=True, paper_trading_only=False,
          ai_features_enabled=True),
    _plan("pro-annual", "Pro (Annual)", PlanTier.PRO, 1499900, "annual",
          display_order=6,
          description="Pro billed annually — save vs monthly. All Pro features included.",
          features=PRO_FEATURES,
          max_running_bots=10, max_open_positions=50,
          automation_enabled=True, paper_trading_only=False,
          ai_features_enabled=True),

    # ---- Elite (₹2,999/mo, ₹29,999/yr) ----
    _plan("elite-monthly", "Elite", PlanTier.ELITE, 299900, "monthly",
          display_order=7,
          description=(
              "Everything in Pro plus unlimited bots, options & swing strategy packs, "
              "AI strategy builder & optimizer, wallet, coupons and affiliate program."
          ),
          features=ELITE_FEATURES,
          max_running_bots=-1, max_open_positions=100,
          automation_enabled=True, paper_trading_only=False,
          ai_features_enabled=True, unlimited_bots=True),
    _plan("elite-annual", "Elite (Annual)", PlanTier.ELITE, 2999900, "annual",
          display_order=8,
          description="Elite billed annually — save vs monthly. All Elite features included.",
          features=ELITE_FEATURES,
          max_running_bots=-1, max_open_positions=100,
          automation_enabled=True, paper_trading_only=False,
          ai_features_enabled=True, unlimited_bots=True),

    # ======================================================================
    # Legacy plans — RETAINED but DEACTIVATED (is_active=False). Never
    # deleted so existing user_subscriptions rows do not orphan. These keys
    # keep their historical feature bags so a user still attached to them
    # continues to resolve the correct capabilities. FeatureGate also uses
    # the legacy ``standard`` row as the default-tier fallback for brand-new
    # users with no explicit subscription.
    # ======================================================================
    _plan("standard", "Standard (Legacy)", PlanTier.STANDARD, 49900, "monthly",
          display_order=90,
          description="Legacy single-interval Standard — superseded by standard-monthly.",
          features=STANDARD_FEATURES,
          max_running_bots=1, max_open_positions=5,
          automation_enabled=False, paper_trading_only=True,
          ai_features_enabled=True, is_active=False),
    _plan("pro", "Pro (Legacy)", PlanTier.PRO, 99900, "monthly",
          display_order=91,
          description="Legacy single-interval Pro — superseded by pro-monthly.",
          features=PRO_FEATURES,
          max_running_bots=10, max_open_positions=50,
          automation_enabled=True, paper_trading_only=False,
          ai_features_enabled=True, is_active=False),
    _plan("free", "Free (Deprecated)", PlanTier.FREE, 0, "monthly",
          display_order=99,
          description=(
              "Legacy free tier — deprecated in ORB AI 2.0. Existing users are "
              "auto-migrated to a paid tier on their next login."
          ),
          features=_feats(
              F.PAPER_TRADING, F.MANUAL_TRADING,
              F.REPORT_FORMAT_PDF, F.REPORT_FORMAT_CSV,
              max_saved_strategies=2, max_backtests_per_month=30,
              live_trading=False, ai_assistant=False,
              portfolio_analytics=False, cloud_sync=False, priority_support=False,
          ),
          max_running_bots=1, max_open_positions=5,
          automation_enabled=False, paper_trading_only=True,
          ai_features_enabled=False, is_active=False),
    _plan("starter", "Starter (Legacy v1.1)", PlanTier.STARTER, 49900, "monthly",
          display_order=92,
          description="Legacy v1.1.0 Starter tier — superseded by starter-monthly.",
          features={},
          max_running_bots=0, max_open_positions=3,
          automation_enabled=False, paper_trading_only=False,
          ai_features_enabled=False, is_active=False),
    _plan("elite", "Elite (Legacy v1.1)", PlanTier.ELITE, 399900, "monthly",
          display_order=93,
          description="Legacy v1.1.0 Elite tier — superseded by elite-monthly.",
          features=ELITE_FEATURES,
          max_running_bots=-1, max_open_positions=20,
          automation_enabled=True, paper_trading_only=False,
          ai_features_enabled=True, unlimited_bots=True, is_active=False),
    _plan("enterprise", "Enterprise (Legacy)", PlanTier.ENTERPRISE, 999900, "monthly",
          display_order=94,
          description="Legacy tier — contact sales for enterprise SLAs.",
          features=ELITE_FEATURES,
          max_running_bots=-1, max_open_positions=50,
          automation_enabled=True, paper_trading_only=False,
          ai_features_enabled=True, unlimited_bots=True, is_active=False),
]


def _strat(
    key: str, name: str, min_tier: str, *,
    description: str = "",
    category: str = "orb",
    difficulty: StrategyDifficulty = StrategyDifficulty.INTERMEDIATE,
    risk: StrategyRiskLevel = StrategyRiskLevel.MEDIUM,
    markets: list[str] | None = None,
    timeframes: list[str] | None = None,
    version: str = "1.0.0",
    status: CatalogStatus = CatalogStatus.ACTIVE,
    automation: bool = True,
    live: bool = True,
    is_featured: bool = False,
    display_order: int = 0,
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "description": description or f"{name} strategy.",
        "category": category,
        "difficulty": difficulty.value,
        "risk_level": risk.value,
        "supported_markets": markets or ["NSE", "BSE"],
        "supported_timeframes": timeframes or ["5m", "15m"],
        "version": version,
        # ORB AI 2.0: minimum tier is either "standard" or "pro".
        "min_plan_tier": min_tier,
        "automation_supported": automation,
        "ai_compatible": True,
        "paper_trading_supported": True,
        "live_trading_supported": live,
        "status": status.value,
        "is_featured": is_featured,
        "display_order": display_order,
        "performance_stats": None,
        "default_params": None,
    }


# ORB AI 2.0: the built-in catalog is a *template library*. Users OWN their
# own strategies (via /api/v1/my-strategies) and can clone from these
# templates. min_plan_tier is either "standard" (paper trading only) or "pro".
DEFAULT_STRATEGIES: list[dict[str, Any]] = [
    _strat("demo_orb", "Demo ORB", "standard",
           description="Introductory Opening Range Breakout — paper trading only.",
           difficulty=StrategyDifficulty.BEGINNER, risk=StrategyRiskLevel.LOW,
           automation=False, live=False, display_order=1, is_featured=True),
    _strat("basic_orb", "Basic ORB", "standard",
           description="Classic Opening Range Breakout on the 15-minute range.",
           difficulty=StrategyDifficulty.BEGINNER, risk=StrategyRiskLevel.MEDIUM,
           timeframes=["15m"], automation=False, live=False, display_order=2),
    _strat("orb_pro", "ORB Pro", "pro", category="orb",
           description="Flagship ORB with adaptive stop, trailing, and re-entry.",
           difficulty=StrategyDifficulty.ADVANCED, risk=StrategyRiskLevel.MEDIUM,
           is_featured=True, display_order=10),
    _strat("orb_5m", "5-Minute ORB", "pro",
           description="Fast intraday ORB using a 5-minute opening range.",
           timeframes=["5m"], display_order=11),
    _strat("vwap_breakout", "VWAP Breakout", "pro", category="breakout",
           description="Momentum breakouts above/below VWAP with volume confirmation.",
           display_order=12),
    _strat("ema_20_50", "EMA 20/50", "pro", category="trend",
           description="Trend-following EMA 20/50 with pullback entries.",
           display_order=13),
    _strat("supertrend", "Supertrend", "pro", category="trend",
           description="ATR-based Supertrend with dynamic stops.",
           display_order=14),
    _strat("rsi_pullback", "RSI Pullback", "pro", category="mean_reversion",
           description="RSI-based pullback entries in a strong trend.",
           display_order=15),
    _strat("gap_trading", "Gap Trading", "pro", category="gap",
           description="Gap-and-go / gap-fill strategies at open.",
           display_order=16),
    _strat("opening_momentum", "Opening Momentum", "pro", category="momentum",
           description="First-hour momentum riding using volume + price action.",
           display_order=17),
]


async def seed_default_plans(session: AsyncSession) -> int:
    """Insert missing plans and refresh price/features/limits on redeploy.

    Idempotent. Returns the number of plans created (existing plans are
    updated in place — legacy rows are preserved and only deactivated).
    """
    created = 0
    for spec in DEFAULT_PLANS:
        existing = (await session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.key == spec["key"])
        )).scalar_one_or_none()
        if existing is None:
            session.add(SubscriptionPlan(**spec))
            created += 1
        else:
            for k, v in spec.items():
                setattr(existing, k, v)
    await session.flush()
    if created:
        logger.info("subscription_plans_seeded", extra={"created_count": created})
    return created


async def seed_default_strategies(session: AsyncSession) -> int:
    """Insert missing strategy catalog entries; refresh metadata on redeploy.

    Idempotent. Never *removes* strategies (admins can toggle status).
    """
    created = 0
    for spec in DEFAULT_STRATEGIES:
        existing = (await session.execute(
            select(StrategyCatalog).where(StrategyCatalog.key == spec["key"])
        )).scalar_one_or_none()
        if existing is None:
            session.add(StrategyCatalog(**spec))
            created += 1
        else:
            for k in (
                "name", "description", "category", "difficulty", "risk_level",
                "supported_markets", "supported_timeframes", "version",
                "min_plan_tier", "automation_supported", "ai_compatible",
                "paper_trading_supported", "live_trading_supported",
                "default_params",
            ):
                if k in spec:
                    setattr(existing, k, spec[k])
    await session.flush()
    if created:
        logger.info("strategy_catalog_seeded", extra={"created_count": created})
    return created
