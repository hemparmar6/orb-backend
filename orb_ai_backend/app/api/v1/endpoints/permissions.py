"""Permission Engine REST endpoints (v1.1.0).

Public::
    GET /api/v1/permissions/me   → user's current permission snapshot.

The centralised permission engine (:class:`app.services.permissions.PermissionService`)
is the ONLY place plan-gating decisions are made — clients should call
this endpoint to render plan-locked UI.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser, DBSession
from app.services.permissions import PermissionService

router = APIRouter()


class PermissionSnapshot(BaseModel):
    plan_key: str
    plan_name: str
    tier: str
    status: str
    is_trial: bool
    automation_enabled: bool
    paper_trading_only: bool
    ai_features_enabled: bool
    max_running_bots: int
    max_open_positions: int
    allowed_strategies: list[str]
    features: dict[str, bool]


class StrategyPermissionQuery(BaseModel):
    strategy_key: str


class StrategyPermissionResult(BaseModel):
    strategy_key: str
    allowed: bool
    reason: Optional[str] = None


@router.get(
    "/me",
    response_model=PermissionSnapshot,
    summary="Current user's permission snapshot",
)
async def me(current_user: CurrentUser, session: DBSession) -> PermissionSnapshot:
    snap = await PermissionService(session).snapshot(current_user)
    return PermissionSnapshot(**asdict(snap))


@router.get(
    "/strategies/{strategy_key}",
    response_model=StrategyPermissionResult,
    summary="Can the current user run this strategy?",
)
async def can_use_strategy(
    strategy_key: str, current_user: CurrentUser, session: DBSession
) -> StrategyPermissionResult:
    svc = PermissionService(session)
    allowed = await svc.can_use_strategy(current_user, strategy_key)
    reason = None if allowed else "plan_upgrade_required_or_strategy_inactive"
    return StrategyPermissionResult(
        strategy_key=strategy_key, allowed=allowed, reason=reason
    )
