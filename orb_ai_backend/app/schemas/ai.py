"""Module 9 — Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class TradeReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    trade_id: str
    trade_quality_score: Optional[float] = None
    entry_quality: Optional[float] = None
    exit_quality: Optional[float] = None
    risk_management_score: Optional[float] = None
    rule_compliance: Optional[float] = None
    emotional_flags: List[str] = Field(default_factory=list)
    improvements: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    provider: str
    model: str
    prompt_version: str
    source: str
    created_at: datetime


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    action: str
    rationale: Optional[str] = None
    confidence: float
    priority: Literal["low", "medium", "high"]
    status: Literal["pending", "accepted", "dismissed", "expired"]
    created_at: datetime
    expires_at: Optional[datetime] = None


class RecommendationAction(BaseModel):
    status: Literal["accepted", "dismissed"]


class AnalyticsMetricsOut(BaseModel):
    win_rate: float
    profit_factor: float
    expected_value: float
    sharpe: float
    sortino: float
    max_drawdown_pct: float
    total_trades: int
    total_pnl: float


class AnalyticsSnapshotOut(BaseModel):
    scope: str
    scope_ref_id: Optional[str] = None
    metrics: AnalyticsMetricsOut
    equity_curve: List[Dict[str, Any]]
    generated_at: datetime


class AIOverviewOut(BaseModel):
    highlights: List[str]
    daily_headline: Optional[str] = None
    weekly_headline: Optional[str] = None
    alerts: List[Dict[str, Any]] = Field(default_factory=list)
    performance: AnalyticsMetricsOut
    market_intelligence: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    generated_at: datetime


class OptimJobCreate(BaseModel):
    strategy_id: str
    kind: Literal["parameter", "walk_forward", "multi_symbol", "multi_tf", "batch"]
    params_space: Dict[str, List[Any]]
    config: Dict[str, Any] = Field(default_factory=dict)


class OptimJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    status: str
    strategy_id: str
    params_space: Dict[str, Any]
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    created_at: datetime


class OptimResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_id: str
    rank: int
    params: Dict[str, Any]
    metrics: Dict[str, Any]
    is_best: bool


class MarketIntelligenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    symbol: str
    timeframe: str
    regime: str
    trend_strength: float
    volatility_regime: str
    liquidity: str
    gap_behaviour: Optional[str] = None
    session_stats: Dict[str, Any]
    generated_at: datetime
