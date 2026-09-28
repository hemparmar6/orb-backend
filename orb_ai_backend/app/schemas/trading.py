"""Trading engine Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.engine import (
    EngineSessionStatus,
    ExecutionMode,
    OrderProduct,
    OrderSide,
    OrderStatus,
    OrderType,
)


# ---- start / stop -------------------------------------------------------


class RiskConfigInput(BaseModel):
    max_position_size: Optional[float] = Field(default=None, ge=0)
    max_daily_loss: Optional[float] = Field(default=None, ge=0)
    max_risk_per_trade_pct: Optional[float] = Field(default=None, ge=0, le=100)
    trading_session_start: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    trading_session_end: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    trading_session_timezone: Optional[str] = None


class StartTradingRequest(BaseModel):
    strategy_name: str = Field(..., min_length=1, max_length=128)
    strategy_id: Optional[str] = None
    symbols: list[str] = Field(..., min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    risk_config: RiskConfigInput = Field(default_factory=RiskConfigInput)
    initial_capital: float = Field(default=100_000.0, ge=0)
    provider: Optional[str] = Field(default=None, description="Market-data provider name (default 'mock')")
    # Module 3
    execution_mode: str = Field(
        default="paper",
        pattern="^(paper|live)$",
        description="'paper' uses the in-process PaperExecutor; 'live' routes to broker_account_id.",
    )
    broker_account_id: Optional[str] = Field(
        default=None,
        description="Required when execution_mode='live'. Must belong to the caller.",
    )


class StopTradingRequest(BaseModel):
    session_id: Optional[str] = None  # if omitted, stops ALL sessions for user


# ---- manual paper order -------------------------------------------------

class ManualPaperOrderRequest(BaseModel):
    """Authenticated mobile manual-order contract.

    This endpoint is deliberately paper-only. Enum values are normalized from
    the mobile client's conventional uppercase representation (BUY/SELL/MARKET/MIS)
    to the backend's lowercase enum values.
    """

    session_id: str = Field(..., min_length=1, max_length=128)
    symbol: str = Field(..., min_length=1, max_length=64)
    side: OrderSide
    quantity: Decimal = Field(..., gt=0, max_digits=18, decimal_places=4)
    order_type: OrderType = Field(default=OrderType.MARKET)
    exchange: str = Field(default="NSE_INDEX", min_length=1, max_length=16)
    product: OrderProduct = Field(default=OrderProduct.MIS)
    price: Optional[Decimal] = None
    trigger_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    tag: Optional[str] = Field(default="manual_mobile", max_length=64)

    @field_validator("side", "order_type", "product", mode="before")
    @classmethod
    def _normalize_enum_values(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("order_type")
    @classmethod
    def _market_orders_only(cls, value: OrderType) -> OrderType:
        if value != OrderType.MARKET:
            raise ValueError("manual paper orders support MARKET order type only")
        return value

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("quantity", "price", "trigger_price", "stop_loss", "target_price")
    @classmethod
    def _finite_decimal(cls, value):
        if value is not None and not value.is_finite():
            raise ValueError("numeric values must be finite")
        return value


# ---- read models --------------------------------------------------------


class EngineSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    strategy_name: str
    strategy_id: Optional[str] = None
    status: EngineSessionStatus
    execution_mode: ExecutionMode
    broker_account_id: Optional[str] = None
    symbols: list[str]
    params: dict[str, Any]
    risk_config: dict[str, Any]
    initial_capital: Decimal
    realized_pnl: Decimal
    day_pnl: Decimal
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TradingStatusResponse(BaseModel):
    sessions: list[EngineSessionRead]
    registered_strategies: list[str]


class PaperOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    engine_session_id: str
    symbol: str
    exchange: str
    side: OrderSide
    order_type: OrderType
    product: OrderProduct
    quantity: Decimal
    price: Optional[Decimal] = None
    trigger_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    target_price: Optional[Decimal] = None
    status: OrderStatus
    filled_quantity: Decimal
    average_fill_price: Optional[Decimal] = None
    strategy_name: str
    tag: Optional[str] = None
    placed_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime


class PaperPositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    engine_session_id: str
    symbol: str
    exchange: str
    product: OrderProduct
    net_quantity: Decimal
    average_price: Decimal
    realized_pnl: Decimal
    last_price: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    updated_at: datetime


class PaperTradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    engine_session_id: str
    paper_order_id: str
    symbol: str
    exchange: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    realized_pnl_delta: Decimal
    strategy_name: str
    executed_at: datetime


class PnLResponse(BaseModel):
    engine_session_id: str
    realized: float
    unrealized: float
    day_pnl: float
    total: float
    open_positions: int
    winning_trades: int
    losing_trades: int
