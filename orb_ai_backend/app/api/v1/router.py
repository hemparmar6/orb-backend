"""v1 router — aggregates all endpoint routers.

Note: In Module 2 the paper-engine paths ``/orders``, ``/positions``,
``/trades`` and ``/pnl`` are top-level (per spec). Module 1's manual trade
CRUD has been remounted at ``/manual-trades`` to avoid collision.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    affiliate_ops,
    affiliates,
    ai_analytics,
    ai_assistant,
    ai_recommendations,
    ai_trade_review,
    analytics,
    auth,
    backtest,
    bots,
    brokers,
    campaigns,
    coupons,
    creators,
    execution_safety,
    health,
    market_data,
    market_intelligence,
    marketing_assets,
    marketplace,
    monitoring,
    my_strategies,
    notifications,
    optimisation,
    permissions,
    plans,
    portfolio,
    referrals,
    reports,
    revenue,
    risk,
    risk_management,
    scheduler,
    settings,
    strategies,
    strategy_catalog,
    subscriptions,
    trades,
    trading,
    trading_mode,
    trial_purchase,
    trials,
    users,
    wallet,
)

api_router = APIRouter()

# ---- Module 1 ---------------------------------------------------------
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(strategies.router, prefix="/strategies", tags=["strategies"])
api_router.include_router(trades.router, prefix="/manual-trades", tags=["manual-trades"])

# ---- Module 2 (trading engine) ----------------------------------------
api_router.include_router(trading.trading_router, prefix="/trading", tags=["trading"])
api_router.include_router(trading.top_level_router, tags=["trading"])
api_router.include_router(trading_mode.router, prefix="/trading/mode", tags=["trading-mode"])

# ---- Module 3 (brokers) -----------------------------------------------
api_router.include_router(brokers.router, prefix="/brokers", tags=["brokers"])

# ---- Module 5 (backtest) ----------------------------------------------
api_router.include_router(backtest.router, prefix="/backtest", tags=["backtest"])

# ---- Module 7 (admin dashboard) ---------------------------------------
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])

# ---- Module 8 (portfolio, analytics, risk, notifications, reports) ----
api_router.include_router(portfolio.router, prefix="/portfolio", tags=["portfolio"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(risk.router, prefix="/risk", tags=["risk"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(scheduler.router, prefix="/scheduler", tags=["scheduler"])
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])

# ---- Module 9 (AI Trading Intelligence) --------------------------------
api_router.include_router(ai_analytics.router, prefix="/ai/analytics", tags=["ai-analytics"])
api_router.include_router(ai_trade_review.router, prefix="/ai/trade-review", tags=["ai-trade-review"])
api_router.include_router(ai_recommendations.router, prefix="/ai/recommendations", tags=["ai-recommendations"])
api_router.include_router(optimisation.router, prefix="/optimisation", tags=["optimisation"])
api_router.include_router(market_intelligence.router, prefix="/market-intelligence", tags=["market-intelligence"])

# ---- Module 10 (Enterprise Monitoring, Security, Backups) --------------
api_router.include_router(monitoring.router, prefix="/monitoring", tags=["monitoring"])

# ---- v1.1.0 (Commercialization Foundation - Phase 1) --------------------
api_router.include_router(permissions.router, prefix="/permissions", tags=["permissions"])
api_router.include_router(trials.router, prefix="/trials", tags=["trials"])
api_router.include_router(plans.router, prefix="/plans", tags=["plans"])
api_router.include_router(strategy_catalog.router, prefix="/strategy-catalog", tags=["strategy-catalog"])

# ---- v1.1.0 (Commercialization — Phase 2) --------------------------------
api_router.include_router(marketplace.router, prefix="/marketplace", tags=["marketplace"])
api_router.include_router(coupons.router, prefix="/coupons", tags=["coupons"])

# ---- Creator Hub (creators → strategies → existing coupon attribution) ----
api_router.include_router(creators.router, prefix="/creators", tags=["creators"])
api_router.include_router(wallet.router, prefix="/wallet", tags=["wallet"])
api_router.include_router(revenue.router, prefix="/revenue", tags=["revenue"])
api_router.include_router(trial_purchase.router, prefix="/trials", tags=["trials"])

# ---- ORB AI 2.0 (Milestones 1-3) — "My Strategies" replaces the marketplace-
# as-a-store paradigm. Users create and own their own strategies.
api_router.include_router(my_strategies.router, prefix="/my-strategies", tags=["my-strategies"])

# ---- ORB AI 2.0 (Milestone 4) — AI Strategy Assistant (Pro feature).
api_router.include_router(ai_assistant.router, prefix="/ai-assistant", tags=["ai-assistant"])

# ---- ORB AI 2.0 (Milestone 5) — Charts & market data (candles, watchlists,
# favorites, layouts, indicator presets, drawing objects, offline cache).
api_router.include_router(market_data.router, prefix="/market-data", tags=["market-data"])

# ---- ORB AI 2.0 (Milestone 8) — Execution Safety (rate limits, duplicate
# detection, queue manager, kill switch, admin dashboard, audit events).
api_router.include_router(
    execution_safety.router, prefix="/execution-safety", tags=["execution-safety"],
)

# ---- ORB AI 2.0 (Milestone 9) — Risk Management (per-user limits,
# breach log, batch bot controls, portfolio dashboard, admin overview).
api_router.include_router(
    risk_management.router, prefix="/risk-management", tags=["risk-management"],
)

# ---- v1.1.0 (Commercialization — Phase 3, Affiliate) ---------------------
api_router.include_router(affiliates.router, prefix="/affiliates", tags=["affiliates"])
api_router.include_router(referrals.router, prefix="/referrals", tags=["referrals"])
api_router.include_router(campaigns.router, prefix="/campaigns", tags=["campaigns"])
api_router.include_router(
    marketing_assets.router, prefix="/marketing-assets", tags=["marketing-assets"],
)
# affiliate_ops exposes several sub-paths (commissions, payouts, analytics, fraud)
api_router.include_router(affiliate_ops.router, prefix="", tags=["affiliate-ops"])

# ---- v1.1.0 (Phase 4 — Bot Management + Circuit Breakers) ---------------
api_router.include_router(bots.router, prefix="", tags=["bots"])
