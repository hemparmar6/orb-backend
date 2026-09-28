"""Pydantic schemas — Milestone 9 Risk Management."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.risk_management import RiskAction, RiskConfigActorType, RiskEventType, RiskSeverity


# ---- risk_limits ---------------------------------------------------------


class RiskLimitBase(BaseModel):
    daily_loss_limit: Optional[float] = None
    daily_profit_target: Optional[float] = None
    max_capital_allocation: Optional[float] = None
    max_position_size: Optional[float] = None
    max_exposure_per_symbol: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    max_consecutive_losses: Optional[int] = None
    max_open_positions: Optional[int] = None
    trading_session_start: Optional[str] = None  # "HH:MM"
    trading_session_end: Optional[str] = None    # "HH:MM"
    trading_session_timezone: Optional[str] = None
    live_trading_enabled: Optional[bool] = None
    force_paper_mode: Optional[bool] = None
    notify_channels: Optional[dict[str, bool]] = None
    notify_severity_min: Optional[str] = Field(default=None, pattern="^(info|warning|error|critical)$")


class RiskLimitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    daily_loss_limit: Optional[float] = None
    daily_profit_target: Optional[float] = None
    max_capital_allocation: Optional[float] = None
    max_position_size: Optional[float] = None
    max_exposure_per_symbol: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    max_consecutive_losses: Optional[int] = None
    max_open_positions: Optional[int] = None
    trading_session_start: Optional[str] = None
    trading_session_end: Optional[str] = None
    trading_session_timezone: Optional[str] = None
    live_trading_enabled: bool
    force_paper_mode: bool
    notify_channels: dict[str, Any]
    notify_severity_min: str
    extra: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# ---- risk_breaches -------------------------------------------------------


class RiskBreachRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    bot_id: Optional[str] = None
    symbol: Optional[str] = None
    event_type: RiskEventType
    severity: RiskSeverity
    action_taken: RiskAction
    reason: str
    triggered_value: Optional[float] = None
    threshold: Optional[float] = None
    payload: dict[str, Any]
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    notification_id: Optional[str] = None
    created_at: datetime


# ---- portfolio risk dashboard --------------------------------------------


class PortfolioRiskSnapshot(BaseModel):
    limits: Optional[RiskLimitRead]
    exposure_by_symbol: list[dict[str, Any]]
    exposure_by_broker: list[dict[str, Any]]
    daily: dict[str, Any]
    drawdown: dict[str, Any]
    margin: dict[str, Any]
    position_sizing: list[dict[str, Any]]
    open_positions: int
    consecutive_losses: int
    live_trading_enabled: bool
    force_paper_mode: bool
    active_breaches: list[RiskBreachRead]


# ---- batch bot actions ---------------------------------------------------


class BotBatchActionResponse(BaseModel):
    action: str
    count: int
    bot_ids: list[str]
    live_trading_enabled: Optional[bool] = None
    force_paper_mode: Optional[bool] = None


class BatchActionRequest(BaseModel):
    reason: Optional[str] = None


# ---- admin overview ------------------------------------------------------


class AdminRiskOverview(BaseModel):
    since: datetime
    total_breaches: int
    by_event_type: list[dict[str, Any]]
    by_severity: list[dict[str, Any]]
    top_users: list[dict[str, Any]]
    top_symbols: list[dict[str, Any]]
    users_with_live_disabled: int
    users_forced_to_paper: int


class RiskLimitAuditRead(BaseModel):
    """Milestone 9 follow-up — one row per risk-limit field change."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    target_user_id: str
    actor_user_id: Optional[str] = None
    actor_type: RiskConfigActorType
    field: str
    previous_value: Optional[str] = None
    new_value: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime
