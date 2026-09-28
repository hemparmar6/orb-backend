"""ORB AI 2.0 — Milestone 9 REST endpoints for Risk Management.

Mounted at ``/api/v1/risk-management`` (user + admin).

User routes:
    GET    /me/limits                     — read my risk limits
    PUT    /me/limits                     — upsert my risk limits
    GET    /me/portfolio                  — combined portfolio risk snapshot
    GET    /me/breaches                   — my breach history
    POST   /me/pause-all-bots             — batch pause
    POST   /me/resume-all-bots            — batch resume
    POST   /me/disable-live-trading       — kill live mode
    POST   /me/return-to-paper-trading    — force paper mode
    POST   /me/enable-live-trading        — re-enable (does NOT clear paper mode)
    POST   /me/clear-paper-mode-force     — clear paper-mode force flag

Admin routes:
    GET    /admin/overview                — aggregate breach dashboard
    GET    /admin/limits/{user_id}        — read any user's limits
    PUT    /admin/limits/{user_id}        — set any user's limits
    GET    /admin/breaches                — filterable breach log
    POST   /admin/breaches/{id}/resolve   — mark a breach resolved
    POST   /admin/prune                   — force retention prune
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, status

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.config import settings
from app.models.risk_management import RiskConfigActorType, RiskEventType, RiskSeverity
from app.schemas.risk_management import (
    AdminRiskOverview,
    BatchActionRequest,
    BotBatchActionResponse,
    PortfolioRiskSnapshot,
    RiskBreachRead,
    RiskLimitAuditRead,
    RiskLimitBase,
    RiskLimitRead,
)
from app.services.risk_management_service import RiskManagementService

router = APIRouter()


# =============== USER ==================================================


@router.get(
    "/me/limits",
    response_model=RiskLimitRead,
    summary="Get my risk limits",
)
async def my_limits(user: CurrentUser, session: DBSession) -> RiskLimitRead:
    svc = RiskManagementService(session)
    row = await svc.ensure_limit(user.id)
    await session.commit()
    return RiskLimitRead.model_validate(row)


@router.put(
    "/me/limits",
    response_model=RiskLimitRead,
    summary="Update my risk limits (partial)",
)
async def update_my_limits(
    payload: RiskLimitBase, user: CurrentUser, session: DBSession,
) -> RiskLimitRead:
    changes = payload.model_dump(exclude_none=True)
    svc = RiskManagementService(session)
    row = await svc.upsert_limit(
        user.id,
        actor_user_id=user.id,
        actor_type=RiskConfigActorType.USER,
        reason="self_service",
        **changes,
    )
    await session.commit()
    return RiskLimitRead.model_validate(row)


@router.get(
    "/me/portfolio",
    summary="Combined portfolio risk dashboard (user)",
)
async def my_portfolio(user: CurrentUser, session: DBSession) -> dict[str, Any]:
    svc = RiskManagementService(session)
    snap = await svc.portfolio_snapshot(user.id)
    await session.commit()
    # Serialise the ORM RiskLimit → dict (or None)
    if snap.get("limits") is not None:
        snap["limits"] = RiskLimitRead.model_validate(snap["limits"]).model_dump(
            mode="json",
        )
    snap["active_breaches"] = [
        RiskBreachRead.model_validate(b).model_dump(mode="json")
        for b in snap["active_breaches"]
    ]
    # datetimes → isoformat
    if isinstance(snap.get("since"), datetime):
        snap["since"] = snap["since"].isoformat()
    return snap


@router.get(
    "/me/breaches",
    response_model=list[RiskBreachRead],
    summary="My risk breach history",
)
async def my_breaches(
    user: CurrentUser, session: DBSession,
    event_type: Optional[RiskEventType] = None,
    severity: Optional[RiskSeverity] = None,
    unresolved_only: bool = False,
    since_minutes: Optional[int] = Query(default=None, ge=1, le=60 * 24 * 90),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RiskBreachRead]:
    svc = RiskManagementService(session)
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        if since_minutes else None
    )
    rows = await svc.list_breaches(
        user.id, unresolved_only=unresolved_only,
        event_type=event_type, severity=severity, since=since,
        offset=offset, limit=limit,
    )
    return [RiskBreachRead.model_validate(r) for r in rows]


@router.post(
    "/me/pause-all-bots",
    response_model=BotBatchActionResponse,
    summary="Pause every running bot",
)
async def pause_all_my_bots(
    user: CurrentUser, session: DBSession, payload: BatchActionRequest = Body(default=None),
) -> BotBatchActionResponse:
    svc = RiskManagementService(session)
    reason = (payload.reason if payload else None) or "user_pause_all"
    ids, n = await svc.pause_all_bots(user.id, reason=reason)
    await session.commit()
    return BotBatchActionResponse(action="pause_all", count=n, bot_ids=ids)


@router.post(
    "/me/resume-all-bots",
    response_model=BotBatchActionResponse,
    summary="Resume every paused bot (to IDLE — user must Start each one)",
)
async def resume_all_my_bots(
    user: CurrentUser, session: DBSession,
) -> BotBatchActionResponse:
    svc = RiskManagementService(session)
    ids, n = await svc.resume_all_bots(user.id)
    await session.commit()
    return BotBatchActionResponse(action="resume_all", count=n, bot_ids=ids)


@router.post(
    "/me/disable-live-trading",
    response_model=BotBatchActionResponse,
    summary="Disable live trading + pause every live bot",
)
async def disable_live_trading(
    user: CurrentUser, session: DBSession, payload: BatchActionRequest = Body(default=None),
) -> BotBatchActionResponse:
    svc = RiskManagementService(session)
    reason = (payload.reason if payload else None) or "user_disable_live"
    ids, n = await svc.disable_live_trading(
        user.id, reason=reason,
        actor_user_id=user.id, actor_type=RiskConfigActorType.USER,
    )
    await session.commit()
    return BotBatchActionResponse(
        action="disable_live_trading", count=n, bot_ids=ids,
        live_trading_enabled=False,
    )


@router.post(
    "/me/return-to-paper-trading",
    response_model=BotBatchActionResponse,
    summary="Force paper mode + switch every live bot to paper",
)
async def return_to_paper(
    user: CurrentUser, session: DBSession, payload: BatchActionRequest = Body(default=None),
) -> BotBatchActionResponse:
    svc = RiskManagementService(session)
    reason = (payload.reason if payload else None) or "user_return_paper"
    ids, n = await svc.return_to_paper_trading(
        user.id, reason=reason,
        actor_user_id=user.id, actor_type=RiskConfigActorType.USER,
    )
    await session.commit()
    return BotBatchActionResponse(
        action="return_to_paper", count=n, bot_ids=ids, force_paper_mode=True,
    )


@router.post(
    "/me/enable-live-trading",
    response_model=RiskLimitRead,
    summary="Re-enable live trading on my profile",
)
async def enable_live_trading(user: CurrentUser, session: DBSession) -> RiskLimitRead:
    svc = RiskManagementService(session)
    row = await svc.upsert_limit(
        user.id, live_trading_enabled=True,
        actor_user_id=user.id, actor_type=RiskConfigActorType.USER,
        reason="user_enable_live",
    )
    await session.commit()
    return RiskLimitRead.model_validate(row)


@router.post(
    "/me/clear-paper-mode-force",
    response_model=RiskLimitRead,
    summary="Clear force-paper-mode on my profile",
)
async def clear_paper_force(user: CurrentUser, session: DBSession) -> RiskLimitRead:
    svc = RiskManagementService(session)
    row = await svc.upsert_limit(
        user.id, force_paper_mode=False,
        actor_user_id=user.id, actor_type=RiskConfigActorType.USER,
        reason="user_clear_paper_force",
    )
    await session.commit()
    return RiskLimitRead.model_validate(row)


@router.get(
    "/me/audit",
    response_model=list[RiskLimitAuditRead],
    summary="Audit log of my risk-limit changes",
)
async def my_audit(
    user: CurrentUser, session: DBSession,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RiskLimitAuditRead]:
    svc = RiskManagementService(session)
    rows = await svc.list_audit(
        target_user_id=user.id, limit=limit, offset=offset,
    )
    return [RiskLimitAuditRead.model_validate(r) for r in rows]


# =============== ADMIN =================================================


@router.get(
    "/admin/overview",
    response_model=AdminRiskOverview,
    summary="Aggregate risk-breach dashboard (admin)",
)
async def admin_overview(
    _admin: AdminUser, session: DBSession,
    since_minutes: int = Query(default=60 * 24, ge=1, le=60 * 24 * 90),
) -> AdminRiskOverview:
    svc = RiskManagementService(session)
    data = await svc.admin_overview(since_minutes=since_minutes)
    return AdminRiskOverview.model_validate(data)


@router.get(
    "/admin/limits/{user_id}",
    response_model=RiskLimitRead,
    summary="Read a user's risk limits (admin)",
)
async def admin_get_limits(
    user_id: str, _admin: AdminUser, session: DBSession,
) -> RiskLimitRead:
    svc = RiskManagementService(session)
    row = await svc.ensure_limit(user_id)
    await session.commit()
    return RiskLimitRead.model_validate(row)


@router.put(
    "/admin/limits/{user_id}",
    response_model=RiskLimitRead,
    summary="Update a user's risk limits (admin)",
)
async def admin_update_limits(
    user_id: str, payload: RiskLimitBase,
    admin: AdminUser, session: DBSession,
) -> RiskLimitRead:
    changes = payload.model_dump(exclude_none=True)
    svc = RiskManagementService(session)
    row = await svc.upsert_limit(
        user_id, actor_user_id=admin.id, actor_type=RiskConfigActorType.ADMIN,
        reason="admin_edit", **changes,
    )
    await session.commit()
    return RiskLimitRead.model_validate(row)


@router.get(
    "/admin/audit",
    response_model=list[RiskLimitAuditRead],
    summary="Risk-limit configuration audit log (admin, filterable)",
)
async def admin_list_audit(
    _admin: AdminUser, session: DBSession,
    target_user_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_type: Optional[RiskConfigActorType] = None,
    since_minutes: Optional[int] = Query(default=None, ge=1, le=60 * 24 * 365),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[RiskLimitAuditRead]:
    svc = RiskManagementService(session)
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        if since_minutes else None
    )
    rows = await svc.list_audit(
        target_user_id=target_user_id, actor_user_id=actor_user_id,
        actor_type=actor_type, since=since, offset=offset, limit=limit,
    )
    return [RiskLimitAuditRead.model_validate(r) for r in rows]


@router.get(
    "/admin/breaches",
    response_model=list[RiskBreachRead],
    summary="List risk breaches (admin, filterable)",
)
async def admin_list_breaches(
    _admin: AdminUser, session: DBSession,
    user_id: Optional[str] = None,
    event_type: Optional[RiskEventType] = None,
    severity: Optional[RiskSeverity] = None,
    unresolved_only: bool = False,
    since_minutes: Optional[int] = Query(default=None, ge=1, le=60 * 24 * 90),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[RiskBreachRead]:
    svc = RiskManagementService(session)
    since = (
        datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        if since_minutes else None
    )
    rows = await svc.list_breaches(
        user_id=user_id, unresolved_only=unresolved_only,
        event_type=event_type, severity=severity, since=since,
        offset=offset, limit=limit,
    )
    return [RiskBreachRead.model_validate(r) for r in rows]


@router.post(
    "/admin/breaches/{breach_id}/resolve",
    response_model=RiskBreachRead,
    summary="Resolve a risk breach (admin)",
)
async def admin_resolve_breach(
    breach_id: str, admin: AdminUser, session: DBSession,
) -> RiskBreachRead:
    svc = RiskManagementService(session)
    row = await svc.resolve_breach(breach_id, admin_user_id=admin.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Breach not found")
    await session.commit()
    return RiskBreachRead.model_validate(row)


@router.post(
    "/admin/prune",
    status_code=status.HTTP_200_OK,
    summary="Force-run risk-breach retention prune (admin)",
)
async def admin_prune(
    _admin: AdminUser, session: DBSession,
    retention_days: int = Query(
        default=getattr(settings, "RISK_BREACH_RETENTION_DAYS", 90),
        ge=1, le=3650,
    ),
) -> dict[str, int]:
    svc = RiskManagementService(session)
    removed = await svc.prune_old_breaches(retention_days)
    await session.commit()
    return {"removed": removed, "retention_days": retention_days}
