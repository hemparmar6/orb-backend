"""Strategy Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.strategy import StrategyStatus


class StrategyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: StrategyStatus = StrategyStatus.DRAFT
    is_public: bool = False


class StrategyCreate(StrategyBase):
    pass


class StrategyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)
    parameters: Optional[dict[str, Any]] = None
    status: Optional[StrategyStatus] = None
    is_public: Optional[bool] = None


class StrategyRead(StrategyBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
