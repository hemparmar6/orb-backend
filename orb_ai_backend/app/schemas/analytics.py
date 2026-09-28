"""Module 8 Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class PortfolioSummary(BaseModel):
    initial_capital: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    exposure: float
    open_positions: int
    num_sessions: int


class Holding(BaseModel):
    symbol: str
    exchange: str
    product: str
    quantity: float
    average_price: float
    last_price: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    side: str


class Allocation(BaseModel):
    symbol: str
    market_value: float
    weight: float


class DailyPerformance(BaseModel):
    date: str
    pnl: float
    trades: int


class MonthlyPerformance(BaseModel):
    month: str
    pnl: float
    trades: int


class AnalyticsSummaryOut(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    loss_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    recovery_factor: float
    expectancy: float
    average_win: float
    average_loss: float
    average_rr: float
    largest_win: float
    largest_loss: float
    consecutive_wins: int
    consecutive_losses: int
    max_drawdown: float
    max_drawdown_pct: float
    active_bots: int
    active_strategies: int


class EquityPoint(BaseModel):
    ts: Optional[str]
    equity: float
    trade_pnl: float


class DrawdownPoint(BaseModel):
    ts: Optional[str]
    equity: float
    peak: float
    drawdown: float


class MonthlyPerfDetail(BaseModel):
    month: str
    pnl: float
    trades: int
    wins: int
    losses: int
    win_rate: float


class JournalItem(BaseModel):
    id: str
    symbol: str
    side: str
    quantity: float
    price: float
    realized_pnl_delta: float
    strategy_name: str
    executed_at: Optional[str]


# ---- Notifications ----

class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    event: str
    severity: str
    title: str
    body: str
    payload: Optional[dict[str, Any]] = None
    read_at: Optional[datetime] = None
    channel_status: Optional[dict[str, Any]] = None
    delivery_attempts: int
    created_at: datetime


class NotificationPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email_enabled: bool
    telegram_enabled: bool
    push_enabled: bool
    in_app_enabled: bool
    telegram_chat_id: Optional[str] = None
    push_token: Optional[str] = None
    event_overrides: Optional[dict[str, bool]] = None


class NotificationPreferenceUpdate(BaseModel):
    email_enabled: Optional[bool] = None
    telegram_enabled: Optional[bool] = None
    push_enabled: Optional[bool] = None
    in_app_enabled: Optional[bool] = None
    telegram_chat_id: Optional[str] = None
    push_token: Optional[str] = None
    event_overrides: Optional[dict[str, bool]] = None


class NotificationTestRequest(BaseModel):
    title: str = Field(default="Test Notification")
    body: str = Field(default="This is a test notification from ORB AI.")
    event: str = Field(default="system_alert")
    severity: str = Field(default="info")


# ---- Reports ----
class ReportRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    report_type: str
    report_format: str
    status: str
    row_count: int
    byte_size: int
    filename: Optional[str] = None
    generated_at: Optional[datetime] = None
    created_at: datetime


class ReportPreview(BaseModel):
    report_type: str
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    portfolio_summary: dict[str, Any]
    analytics_summary: dict[str, Any]
    trade_count: int
