"""Pydantic DTOs for the backtest module."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---- Request --------------------------------------------------------------


class OrbParamsIn(BaseModel):
    """Loose input schema for ORB params — all fields optional, defaults live
    in ``OrbParams``. Extra keys are accepted (forward-compatible) and passed
    through unchanged."""

    model_config = ConfigDict(extra="allow")

    symbols: Optional[list[str]] = None
    exchange: Optional[str] = None
    opening_range_minutes: Optional[int] = Field(None, ge=1, le=240)
    session_start: Optional[str] = None      # "09:15"
    session_end: Optional[str] = None        # "15:15"
    timezone: Optional[str] = None

    enable_long: Optional[bool] = None
    enable_short: Optional[bool] = None

    stop_loss_pct: Optional[float] = Field(None, gt=0, le=50)
    target_pct: Optional[float] = Field(None, gt=0, le=200)
    trailing_stop_pct: Optional[float] = Field(None, gt=0, le=50)

    re_entry_enabled: Optional[bool] = None
    max_re_entries_per_day: Optional[int] = Field(None, ge=0, le=20)
    max_trades_per_day: Optional[int] = Field(None, ge=1, le=100)
    daily_loss_limit: Optional[float] = Field(None, ge=0)
    risk_per_trade_pct: Optional[float] = Field(None, gt=0, le=100)

    quantity: Optional[float] = Field(None, gt=0)
    use_risk_based_sizing: Optional[bool] = None

    fee_per_trade: Optional[float] = Field(None, ge=0)
    slippage_pct: Optional[float] = Field(None, ge=0, le=10)


class BacktestRunRequest(BaseModel):
    """POST /api/v1/backtest/run — everything the runner needs."""

    strategy_name: str = Field(default="orb", description="Registered strategy key")
    strategy_id: Optional[str] = Field(None, description="Optional Strategy row id")
    symbols: list[str] = Field(..., min_length=1)
    start_date: datetime
    end_date: datetime
    initial_capital: float = Field(default=100_000, gt=0)
    params: OrbParamsIn = Field(default_factory=OrbParamsIn)

    @field_validator("end_date")
    @classmethod
    def _end_after_start(cls, v, info):
        start = info.data.get("start_date")
        if start is not None and v <= start:
            raise ValueError("end_date must be strictly after start_date")
        return v


# ---- Response -------------------------------------------------------------


class BacktestRunSummary(BaseModel):
    """Compact snapshot — history listing / status polling."""

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
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Include just the top-line KPIs so a list view has enough to sort by.
    summary: dict[str, Any] = Field(default_factory=dict)


class BacktestRunFull(BacktestRunSummary):
    """Full payload — includes trades + equity curve."""

    params: dict[str, Any] = Field(default_factory=dict)
    trades: list[dict[str, Any]] = Field(default_factory=list)
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)


class BacktestResultsResponse(BaseModel):
    """GET /api/v1/backtest/{id}/results — hoists metrics to top level."""

    id: str
    status: str
    summary: dict[str, Any]
    trades: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
