"""SQLAlchemy models package."""

from app.db.base import Base
from app.models.audit import AuditLog
from app.models.backtest import BacktestRun, BacktestStatus
from app.models.broker import BrokerAccount, BrokerType
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderProduct,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    PaperPosition,
    PaperTrade,
)
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationEvent,
    NotificationPreference,
    NotificationSeverity,
    NotificationStatus,
)
from app.models.portfolio import PortfolioSnapshot
from app.models.report import ReportFormat, ReportRun, ReportStatus, ReportType
from app.models.settings import UserSettings
from app.models.strategy import Strategy, StrategyStatus
from app.models.strategy_catalog import (
    StrategyCatalog,
    StrategyDifficulty,
    StrategyRiskLevel,
    StrategyStatus as CatalogStrategyStatus,
)
from app.models.subscription import (
    PlanTier,
    SubscriptionPlan,
    SubscriptionStatus,
    UserSubscription,
)
from app.models.commerce import (
    Coupon,
    CouponDiscountType,
    CouponRedemption,
    CouponStatus,
    MarketplaceListing,
    Order,
    OrderKind,
    OrderStatus as CommerceOrderStatus,
    RevenueSnapshot,
    StrategyPurchase,
    Wallet,
    WalletTransaction,
    WalletTxnDirection,
    WalletTxnReason,
)
from app.models.affiliate import (
    Affiliate,
    AffiliateProgram,
    AffiliateStatus,
    AssetType,
    AttributionModel,
    Campaign,
    Commission,
    CommissionStatus,
    FraudFlag,
    FraudSeverity,
    MarketingAsset,
    Payout,
    PayoutMethod,
    PayoutStatus,
    ReferralAttribution,
    ReferralClick,
    ReferralEvent,
    ReferralEventType,
)
from app.models.creator import (
    Creator,
    CreatorCouponAssignment,
    CreatorStatus,
    CreatorStrategy,
    CreatorStrategyStatus,
)
from app.models.bot import (
    Bot,
    BotAuditLog,
    BotStatus,
    BreakerLevel,
    BreakerType,
    CircuitBreakerConfig,
    CircuitBreakerEvent,
    KillSwitchEvent,
    KillSwitchScope,
)
from app.models.trade import Trade, TradeSide, TradeStatus
from app.models.user import User, UserRole
from app.models.ai import (
    AIAnalyticsSnapshot,
    AIAuditLog,
    AIRecommendation,
    AITradeReview,
    MarketIntelligence,
    OptimisationJob,
    OptimisationResult,
)
from app.models.monitoring import (
    BackupRecord,
    LoginActivity,
    RateLimitEvent,
    RestoreRecord,
)

# ORB AI 2.0 — Milestone 5 (Charts)
from app.models.chart import (
    ChartDrawingObject,
    ChartFavorite,
    ChartIndicatorPreset,
    ChartLayout,
    ChartOfflineData,
    ChartWatchlist,
)

# ORB AI 2.0 — Milestone 8 (Execution Safety)
from app.models.execution_safety import (
    ExecutionLimitType,
    ExecutionSafetyAction,
    ExecutionSafetyConfigAudit,
    ExecutionSafetyEvent,
    ExecutionSafetySetting,
)

# ORB AI 2.0 — Milestone 9 (Risk Management)
from app.models.risk_management import (
    RiskAction,
    RiskBreach,
    RiskConfigActorType,
    RiskEventType,
    RiskLimit,
    RiskLimitAudit,
    RiskSeverity,
)

__all__ = [
    "Base",
    "User", "UserRole",
    "Trade", "TradeSide", "TradeStatus",
    "Strategy", "StrategyStatus",
    "UserSettings",
    "EngineSession", "EngineSessionStatus", "ExecutionMode",
    "PaperOrder", "PaperPosition", "PaperTrade",
    "OrderSide", "OrderType", "OrderProduct", "OrderStatus",
    "BrokerAccount", "BrokerType",
    "BacktestRun", "BacktestStatus",
    "AuditLog",
    # Module 8
    "Notification", "NotificationChannel", "NotificationEvent",
    "NotificationPreference", "NotificationSeverity", "NotificationStatus",
    "PortfolioSnapshot",
    "ReportRun", "ReportType", "ReportFormat", "ReportStatus",
    "SubscriptionPlan", "UserSubscription", "PlanTier", "SubscriptionStatus",
    # Module 9
    "AITradeReview", "AIRecommendation", "AIAuditLog",
    "OptimisationJob", "OptimisationResult",
    "MarketIntelligence", "AIAnalyticsSnapshot",
    # Module 10
    "BackupRecord", "RestoreRecord", "LoginActivity", "RateLimitEvent",
    # v1.1.0 Phase 2 — Commerce
    "Order", "OrderKind", "CommerceOrderStatus",
    "Coupon", "CouponRedemption", "CouponDiscountType", "CouponStatus",
    "Wallet", "WalletTransaction", "WalletTxnDirection", "WalletTxnReason",
    "MarketplaceListing", "StrategyPurchase", "RevenueSnapshot",
    # v1.1.0 Phase 3 — Affiliate
    "AffiliateProgram", "Affiliate", "AffiliateStatus",
    "Campaign", "MarketingAsset", "AssetType",
    "ReferralClick", "ReferralAttribution", "ReferralEvent",
    "ReferralEventType", "AttributionModel",
    "Commission", "CommissionStatus",
    "Payout", "PayoutStatus", "PayoutMethod",
    "FraudFlag", "FraudSeverity",
    # Creator Hub
    "Creator", "CreatorStatus",
    "CreatorStrategy", "CreatorStrategyStatus",
    "CreatorCouponAssignment",
    # v1.1.0 Phase 4 — Bot Management + Circuit Breakers
    "Bot", "BotStatus", "BotAuditLog",
    "CircuitBreakerConfig", "CircuitBreakerEvent",
    "BreakerLevel", "BreakerType",
    "KillSwitchEvent", "KillSwitchScope",
    # ORB AI 2.0 — Milestone 5 (Charts)
    "ChartWatchlist", "ChartFavorite", "ChartLayout",
    "ChartIndicatorPreset", "ChartDrawingObject", "ChartOfflineData",
    # ORB AI 2.0 — Milestone 8 (Execution Safety)
    "ExecutionSafetySetting", "ExecutionSafetyEvent", "ExecutionSafetyConfigAudit",
    "ExecutionLimitType", "ExecutionSafetyAction",
    # ORB AI 2.0 — Milestone 9 (Risk Management)
    "RiskLimit", "RiskBreach", "RiskLimitAudit",
    "RiskEventType", "RiskSeverity", "RiskAction", "RiskConfigActorType",
]
