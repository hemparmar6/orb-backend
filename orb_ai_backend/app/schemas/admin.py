"""Admin-only Pydantic schemas.

Kept small and dedicated so the mobile-app schemas aren't polluted with
admin fields.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


# ------------------------------------------------------------ users


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: Optional[str] = None
    phone: Optional[str] = None
    role: UserRole
    is_active: bool
    is_verified: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class AdminUserUpdate(BaseModel):
    """Fields an admin can change on any user."""

    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    full_name: Optional[str] = Field(default=None, max_length=255)


# ---------------------------------------------------- broker accounts


class AdminBrokerAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    broker_type: str
    alias: str
    is_active: bool
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------- engine sessions


class AdminSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    strategy_name: str
    strategy_id: Optional[str] = None
    status: str
    execution_mode: str
    broker_account_id: Optional[str] = None
    symbols: list[str]
    initial_capital: float
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime


# ------------------------------------------------------- strategies


class AdminStrategyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    status: str
    is_public: bool = False
    parameters: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AdminStrategyRegistryEntry(BaseModel):
    """A strategy class registered in the process."""

    name: str
    class_name: str
    module: str


# --------------------------------------------------------- backtests


class AdminBacktestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    strategy_name: str
    strategy_id: Optional[str] = None
    symbols: list[str]
    start_date: datetime
    end_date: datetime
    initial_capital: float
    status: str
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime


# ------------------------------------------------------- orders / trades


class AdminOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    engine_session_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    limit_price: Optional[float] = None
    trigger_price: Optional[float] = None
    status: str
    filled_quantity: float
    avg_fill_price: Optional[float] = None
    broker_order_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AdminTradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    engine_session_id: str
    order_id: Optional[str] = None
    symbol: str
    side: str
    quantity: float
    price: float
    fees: float
    created_at: datetime


# ------------------------------------------------------ audit log


class AdminAuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_user_id: Optional[str] = None
    action: str
    target_type: str
    target_id: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    ip_address: Optional[str] = None
    created_at: datetime


# --------------------------------------------------- system health


class AdminSystemHealth(BaseModel):
    status: str
    database: str
    redis: str
    engine_sessions_running: int
    engine_sessions_total: int
    users_total: int
    users_active: int
    users_admins: int
    broker_accounts_total: int
    broker_accounts_active: int
    backtests_total: int
    backtests_completed: int
    market_data_providers: list[str]
    historical_providers: list[str]
    registered_strategies: list[str]
    api_version: str
    now: datetime


# --------------------------------------------------- risk defaults


class AdminRiskDefaults(BaseModel):
    """Global risk defaults applied when a session doesn't override."""

    max_daily_loss: Optional[float] = Field(default=None, ge=0)
    max_position_size: Optional[float] = Field(default=None, ge=0)
    max_risk_per_trade_pct: Optional[float] = Field(default=None, ge=0, le=100)
    trading_session_start: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    trading_session_end: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    timezone: Optional[str] = None
