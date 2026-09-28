"""User settings Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class UserSettingsUpsert(BaseModel):
    """Full upsert payload for PUT /settings/me. All fields are optional so
    partial updates work like a merge."""

    timezone: Optional[str] = Field(default=None, max_length=64)
    currency: Optional[str] = Field(default=None, max_length=8)
    theme: Optional[str] = Field(default=None, pattern="^(light|dark|system)$")

    notifications_email: Optional[bool] = None
    notifications_push: Optional[bool] = None

    default_broker: Optional[str] = Field(default=None, max_length=64)
    max_daily_loss: Optional[Decimal] = Field(default=None, ge=0)
    max_position_size: Optional[Decimal] = Field(default=None, ge=0)
    risk_per_trade_pct: Optional[Decimal] = Field(default=None, ge=0, le=100)

    preferences: Optional[dict[str, Any]] = None


class UserSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    timezone: str
    currency: str
    theme: str
    notifications_email: bool
    notifications_push: bool
    default_broker: Optional[str] = None
    max_daily_loss: Optional[Decimal] = None
    max_position_size: Optional[Decimal] = None
    risk_per_trade_pct: Optional[Decimal] = None
    preferences: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime
