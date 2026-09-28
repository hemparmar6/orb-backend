"""Trade Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.trade import TradeSide, TradeStatus


class TradeBase(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=64)
    side: TradeSide
    quantity: Decimal = Field(..., gt=0)
    entry_price: Optional[Decimal] = Field(default=None, ge=0)
    exit_price: Optional[Decimal] = Field(default=None, ge=0)
    stop_loss: Optional[Decimal] = Field(default=None, ge=0)
    take_profit: Optional[Decimal] = Field(default=None, ge=0)
    status: TradeStatus = TradeStatus.PENDING
    broker: Optional[str] = Field(default=None, max_length=64)
    broker_order_id: Optional[str] = Field(default=None, max_length=128)
    notes: Optional[str] = Field(default=None, max_length=4000)


class TradeCreate(TradeBase):
    strategy_id: Optional[str] = None
    opened_at: Optional[datetime] = None


class TradeUpdate(BaseModel):
    exit_price: Optional[Decimal] = Field(default=None, ge=0)
    stop_loss: Optional[Decimal] = Field(default=None, ge=0)
    take_profit: Optional[Decimal] = Field(default=None, ge=0)
    status: Optional[TradeStatus] = None
    pnl: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    broker_order_id: Optional[str] = Field(default=None, max_length=128)
    notes: Optional[str] = Field(default=None, max_length=4000)


class TradeRead(TradeBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    strategy_id: Optional[str] = None
    pnl: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
