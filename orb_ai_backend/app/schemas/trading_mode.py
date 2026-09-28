from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AutoRevertRead(BaseModel):
    enabled: bool
    at_time: Optional[str] = None
    timezone: Optional[str] = None
    last_run_date: Optional[str] = None
    last_run_result: Optional[str] = None
    last_run_at: Optional[str] = None


class AutoRevertUpdate(BaseModel):
    enabled: bool
    at_time: Optional[str] = Field(default=None, max_length=8)
    timezone: Optional[str] = Field(default=None, max_length=64)


class TradingModeUpdate(BaseModel):
    mode: str = Field(pattern="^(paper|live)$")
    confirmation: Optional[str] = Field(default=None, max_length=128)
    reason: Optional[str] = Field(default=None, max_length=512)


class TradingModeRead(BaseModel):
    mode: str
    changed_at: Optional[str] = None
    changed_by: Optional[str] = None
    armed_at: Optional[str] = None
    cooldown_active: bool = False
    cooldown_expires_at: Optional[str] = None
    cooldown_seconds: int = 0
    active_live_sessions: int
    open_live_positions: int
    open_live_orders: int
    kill_switch_active: bool
    live_gate: str
    auto_revert: AutoRevertRead
