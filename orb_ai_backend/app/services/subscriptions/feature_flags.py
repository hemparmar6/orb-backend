"""FeatureFlag enum — the ONLY place features are named.

v1.1.0 additions preserve every v1.0.0 flag (backward compatibility) and
add plan-gated flags for automation, AI features, wallet, coupons,
affiliate, and marketplace access.

Add a flag here → wire it into the plan seed → gate it via
``FeatureGate.is_enabled(user, FeatureFlag.X)`` or the higher-level
``PermissionService``.
"""
from __future__ import annotations

import enum


class FeatureFlag(str, enum.Enum):
    # ---- Reports (v1.0.0) ----
    REPORT_WEEKLY_DIGEST = "report_weekly_digest"
    REPORT_DAILY_DIGEST = "report_daily_digest"
    REPORT_MONTHLY_DIGEST = "report_monthly_digest"

    REPORT_FORMAT_PDF = "report_format_pdf"
    REPORT_FORMAT_CSV = "report_format_csv"
    REPORT_FORMAT_XLSX = "report_format_xlsx"

    REPORT_TYPE_STRATEGY_PERFORMANCE = "report_type_strategy_performance"
    REPORT_TYPE_BROKER_ACTIVITY = "report_type_broker_activity"

    # ---- Trading engine (v1.0.0) ----
    LIVE_TRADING = "live_trading"
    MULTI_BROKER = "multi_broker"

    # ---- Analytics (v1.0.0) ----
    ADVANCED_ANALYTICS = "advanced_analytics"

    # ---- Notifications (v1.0.0) ----
    NOTIFY_TELEGRAM = "notify_telegram"
    NOTIFY_PUSH = "notify_push"

    # ---- v1.1.0: Automation & bots ----
    AUTOMATION = "automation"
    MANUAL_TRADING = "manual_trading"
    PAPER_TRADING = "paper_trading"

    # ---- v1.1.0: AI features ----
    AI_STRATEGY_BUILDER = "ai_strategy_builder"
    AI_STRATEGY_OPTIMIZER = "ai_strategy_optimizer"
    AI_TRADE_REVIEW = "ai_trade_review"
    AI_RECOMMENDATIONS = "ai_recommendations"

    # ---- v1.1.0: Portfolio / Risk / Strategy packs ----
    PORTFOLIO_RISK_MANAGER = "portfolio_risk_manager"
    OPTIONS_STRATEGY_PACK = "options_strategy_pack"
    SWING_STRATEGY_PACK = "swing_strategy_pack"

    # ---- v1.1.0: Commerce ----
    STRATEGY_MARKETPLACE = "strategy_marketplace"
    ORB_WALLET = "orb_wallet"
    COUPONS = "coupons"
    AFFILIATE_PROGRAM = "affiliate_program"


# Features considered "always on" for every user regardless of plan.
BASE_FEATURES: set[FeatureFlag] = {
    FeatureFlag.REPORT_FORMAT_PDF,
    FeatureFlag.REPORT_FORMAT_CSV,
    FeatureFlag.PAPER_TRADING,
    FeatureFlag.MANUAL_TRADING,
}
