"""ORB AI 2.0 — Milestone 8 REST endpoints for Execution Safety.

Routes:

Admin (all require ``AdminUser``):
    GET    /execution-safety/settings
    PATCH  /execution-safety/settings
    POST   /execution-safety/kill-switch
    GET    /execution-safety/events         (filters + pagination)
    GET    /execution-safety/dashboard
    GET    /execution-safety/config-audit
    POST   /execution-safety/prune

User (self-scoped, read-only):
    GET    /execution-safety/me/events
    GET    /execution-safety/me/limits
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.exceptions import BadRequestError
from app.models.execution_safety import (
    ExecutionLimitType,
    ExecutionSafetyAction,
    ExecutionSafetyConfigAudit,
    ExecutionSafetyEvent,
)
from app.schemas.execution_safety import (
    ExecutionSafetyConfigAuditRead,
    ExecutionSafetyDashboard,
    ExecutionSafetyDashboardCounter,
    ExecutionSafetyEventRead,
    ExecutionSafetySettingsRead,
    ExecutionSafetySettingsUpdate,
    KillSwitchIn,
)
from app.services.execution_safety_service import (
    ExecutionSafetyService,
    GLOBAL_SETTINGS_ID,
)

router = APIRouter()


def _settings_to_read(row) -> ExecutionSafetySettingsRead:
    return ExecutionSafetySettingsRead.model_validate(row)


# =============== admin: settings ===================================

@router.get(
    "/settings",
    response_model=ExecutionSafetySettingsRead,
    summary="Read the execution-safety settings (admin)",
)
async def get_settings(_admin: AdminUser, session: DBSession) -> ExecutionSafetySettingsRead:
    svc = ExecutionSafetyService(session)
    row = await svc.ensure_settings_row()
    await session.commit()
    return _settings_to_read(row)


@router.patch(
    "/settings",
    response_model=ExecutionSafetySettingsRead,
    summary="Update execution-safety settings (admin)",
)
async def update_settings(
    payload: ExecutionSafetySettingsUpdate,
    admin: AdminUser,
    session: DBSession,
) -> ExecutionSafetySettingsRead:
    changes = payload.model_dump(exclude={"reason"}, exclude_none=True)
    if not changes:
        raise BadRequestError("No changes provided", code="no_changes")
    svc = ExecutionSafetyService(session)
    row = await svc.update_settings(
        admin_user_id=admin.id, changes=changes, reason=payload.reason,
    )
    await session.commit()
    return _settings_to_read(row)


@router.post(
    "/kill-switch",
    response_model=ExecutionSafetySettingsRead,
    summary="Activate or deactivate the global kill switch (admin)",
)
async def kill_switch(
    payload: KillSwitchIn,
    admin: AdminUser,
    session: DBSession,
) -> ExecutionSafetySettingsRead:
    svc = ExecutionSafetyService(session)
    if payload.active:
        row = await svc.activate_kill_switch(admin_user_id=admin.id, reason=payload.reason)
        await session.commit()
        # Genuine emergency stop: cancel open live orders + flatten every
        # running live session's actual broker positions.
        from app.engine.strategy.manager import manager as strategy_manager

        try:
            flatten_results = await strategy_manager.emergency_flatten_all(
                reason=payload.reason or "kill_switch"
            )
        except Exception:  # pragma: no cover - never break activation
            flatten_results = [{"status": "failed", "error": "flatten_dispatch_failed"}]
        await svc.record_emergency_flatten(
            results=flatten_results, reason=payload.reason,
        )
        await session.commit()
        row = await svc.ensure_settings_row()
    else:
        row = await svc.deactivate_kill_switch(admin_user_id=admin.id, reason=payload.reason)
        await session.commit()
    return _settings_to_read(row)


# =============== admin: events ====================================

@router.get(
    "/events",
    response_model=list[ExecutionSafetyEventRead],
    summary="List execution-safety events (admin, filterable)",
)
async def list_events(
    _admin: AdminUser,
    session: DBSession,
    user_id: Optional[str] = None,
    bot_id: Optional[str] = None,
    symbol: Optional[str] = None,
    limit_type: Optional[ExecutionLimitType] = None,
    action: Optional[ExecutionSafetyAction] = None,
    since_minutes: Optional[int] = Query(default=None, ge=1, le=60 * 24 * 90),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[ExecutionSafetyEventRead]:
    svc = ExecutionSafetyService(session)
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        if since_minutes else None
    )
    events = await svc.list_events(
        limit=limit,
        offset=offset,
        user_id=user_id,
        bot_id=bot_id,
        symbol=symbol,
        limit_type=limit_type,
        action=action,
        since=since,
    )
    return [ExecutionSafetyEventRead.model_validate(e) for e in events]


# =============== admin: dashboard =================================

@router.get(
    "/dashboard",
    response_model=ExecutionSafetyDashboard,
    summary="Aggregated execution-safety dashboard (admin)",
)
async def dashboard(
    _admin: AdminUser,
    session: DBSession,
    since_minutes: int = Query(default=60, ge=1, le=60 * 24 * 30),
) -> ExecutionSafetyDashboard:
    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    total = (await session.execute(
        select(func.count(ExecutionSafetyEvent.id))
        .where(ExecutionSafetyEvent.created_at >= since)
    )).scalar_one()

    async def _group(col) -> list[ExecutionSafetyDashboardCounter]:
        rows = (await session.execute(
            select(col, func.count(ExecutionSafetyEvent.id))
            .where(ExecutionSafetyEvent.created_at >= since)
            .where(col.is_not(None))
            .group_by(col)
            .order_by(func.count(ExecutionSafetyEvent.id).desc())
            .limit(10)
        )).all()
        out: list[ExecutionSafetyDashboardCounter] = []
        for label, count in rows:
            if label is None:
                continue
            lbl = label.value if hasattr(label, "value") else str(label)
            out.append(ExecutionSafetyDashboardCounter(label=lbl, count=int(count)))
        return out

    return ExecutionSafetyDashboard(
        since=since,
        total_events=int(total),
        by_action=await _group(ExecutionSafetyEvent.action),
        by_limit_type=await _group(ExecutionSafetyEvent.limit_type),
        top_users=await _group(ExecutionSafetyEvent.user_id),
        top_bots=await _group(ExecutionSafetyEvent.bot_id),
        top_symbols=await _group(ExecutionSafetyEvent.symbol),
        top_brokers=await _group(ExecutionSafetyEvent.broker),
        queue_size=ExecutionSafetyService.queue_size(),
    )


# =============== admin: config audit ==============================

@router.get(
    "/config-audit",
    response_model=list[ExecutionSafetyConfigAuditRead],
    summary="List admin-triggered configuration changes (admin)",
)
async def config_audit(
    _admin: AdminUser,
    session: DBSession,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[ExecutionSafetyConfigAuditRead]:
    rows = (await session.execute(
        select(ExecutionSafetyConfigAudit)
        .order_by(ExecutionSafetyConfigAudit.created_at.desc())
        .offset(offset)
        .limit(limit)
    )).scalars().all()
    return [ExecutionSafetyConfigAuditRead.model_validate(r) for r in rows]


@router.post(
    "/prune",
    status_code=status.HTTP_200_OK,
    summary="Prune expired execution-safety events (admin)",
)
async def prune_events(_admin: AdminUser, session: DBSession) -> dict:
    svc = ExecutionSafetyService(session)
    removed = await svc.prune_old_events()
    await session.commit()
    return {"removed": removed}


# =============== user: self-scoped ================================

@router.get(
    "/me/events",
    response_model=list[ExecutionSafetyEventRead],
    summary="List MY execution-safety events",
)
async def my_events(
    user: CurrentUser,
    session: DBSession,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ExecutionSafetyEventRead]:
    svc = ExecutionSafetyService(session)
    events = await svc.list_events(limit=limit, offset=offset, user_id=user.id)
    return [ExecutionSafetyEventRead.model_validate(e) for e in events]


@router.get(
    "/me/limits",
    summary="Show the current effective execution-safety limits",
)
async def my_limits(_user: CurrentUser, session: DBSession) -> dict:
    """Non-admin variant: expose the numeric limits, not the kill switch."""
    svc = ExecutionSafetyService(session)
    cfg = await svc.get_config()
    return {
        "trades_per_second": cfg.trades_per_second,
        "orders_per_minute": cfg.orders_per_minute,
        "orders_per_hour": cfg.orders_per_hour,
        "duplicate_window_seconds": cfg.duplicate_window_seconds,
        "queue_enabled": cfg.queue_enabled,
        "queue_max_size": cfg.queue_max_size,
        "queue_timeout_seconds": cfg.queue_timeout_seconds,
        "kill_switch_active": cfg.kill_switch_active,
    }
