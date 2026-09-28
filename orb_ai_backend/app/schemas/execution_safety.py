"""Pydantic schemas for Milestone 8 — Execution Safety."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.execution_safety import ExecutionLimitType, ExecutionSafetyAction


class ExecutionSafetySettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trades_per_second: int
    orders_per_minute: int
    orders_per_hour: int
    global_orders_per_second: int
    global_orders_per_minute: int
    duplicate_window_seconds: float
    duplicate_action: str
    queue_enabled: bool
    queue_max_size: int
    queue_timeout_seconds: float
    auto_pause_enabled: bool
    auto_pause_violations: int
    auto_pause_window_seconds: int
    kill_switch_active: bool
    kill_switch_reason: Optional[str] = None
    kill_switch_activated_by: Optional[str] = None
    kill_switch_activated_at: Optional[datetime] = None
    event_retention_days: int
    extra: dict[str, Any] = Field(default_factory=dict)


class ExecutionSafetySettingsUpdate(BaseModel):
    """All fields optional — partial update semantics."""

    trades_per_second: Optional[int] = Field(default=None, ge=1, le=1000)
    orders_per_minute: Optional[int] = Field(default=None, ge=1, le=100_000)
    orders_per_hour: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    global_orders_per_second: Optional[int] = Field(default=None, ge=1, le=100_000)
    global_orders_per_minute: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    duplicate_window_seconds: Optional[float] = Field(default=None, ge=0.0, le=600.0)
    duplicate_action: Optional[str] = Field(default=None, pattern="^(reject|queue)$")
    queue_enabled: Optional[bool] = None
    queue_max_size: Optional[int] = Field(default=None, ge=0, le=100_000)
    queue_timeout_seconds: Optional[float] = Field(default=None, ge=0.0, le=3600.0)
    auto_pause_enabled: Optional[bool] = None
    auto_pause_violations: Optional[int] = Field(default=None, ge=1, le=1000)
    auto_pause_window_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    event_retention_days: Optional[int] = Field(default=None, ge=1, le=3650)
    reason: Optional[str] = Field(default=None, max_length=512)


class KillSwitchIn(BaseModel):
    active: bool
    reason: str = Field(min_length=1, max_length=512)


class ExecutionSafetyEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    user_id: Optional[str] = None
    bot_id: Optional[str] = None
    strategy_id: Optional[str] = None
    engine_session_id: Optional[str] = None
    broker: Optional[str] = None
    symbol: Optional[str] = None
    order_type: Optional[str] = None
    endpoint: Optional[str] = None
    limit_type: ExecutionLimitType
    action: ExecutionSafetyAction
    current_counter: Optional[int] = None
    configured_limit: Optional[int] = None
    retry_after_seconds: Optional[float] = None
    fingerprint: Optional[str] = None
    reason: Optional[str] = None


class ExecutionSafetyDashboardCounter(BaseModel):
    label: str
    count: int


class ExecutionSafetyDashboard(BaseModel):
    since: datetime
    total_events: int
    by_action: list[ExecutionSafetyDashboardCounter]
    by_limit_type: list[ExecutionSafetyDashboardCounter]
    top_users: list[ExecutionSafetyDashboardCounter]
    top_bots: list[ExecutionSafetyDashboardCounter]
    top_symbols: list[ExecutionSafetyDashboardCounter]
    top_brokers: list[ExecutionSafetyDashboardCounter]
    queue_size: int


class ExecutionSafetyConfigAuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    admin_user_id: Optional[str] = None
    field: str
    previous_value: Optional[str] = None
    new_value: Optional[str] = None
    reason: Optional[str] = None
