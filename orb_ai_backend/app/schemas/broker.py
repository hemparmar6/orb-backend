"""Broker Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.broker import BrokerType


class BrokerConnectRequest(BaseModel):
    broker_type: BrokerType
    alias: str = Field(default="", max_length=128)
    credentials: dict[str, Any] = Field(
        ...,
        description="Broker-specific credentials — validated against the adapter's "
                    "required_credentials() before encryption.",
    )


class BrokerAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    broker_type: BrokerType
    alias: str
    is_active: bool
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    # NOTE: credentials are never returned.


class BrokerFundsResponse(BaseModel):
    available: float
    used: float
    total: float
    currency: str


class BrokerOrderRead(BaseModel):
    broker_order_id: str
    status: str
    filled_quantity: float
    average_fill_price: Optional[float] = None
    rejection_reason: Optional[str] = None
    client_order_id: Optional[str] = None


class BrokerPositionRead(BaseModel):
    symbol: str
    exchange: str
    product: str
    net_quantity: float
    average_price: float
    realized_pnl: float
    unrealized_pnl: float
    last_price: Optional[float] = None


class BrokerCatalogEntry(BaseModel):
    broker_type: str
    required_credentials: list[str]


class BrokerCatalogResponse(BaseModel):
    brokers: list[BrokerCatalogEntry]
