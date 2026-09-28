"""Phase 4 API endpoints — Bots, Kill Switch, Circuit Breakers, Analytics."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.models.bot import (
    BotStatus,
    BreakerLevel,
    BreakerType,
    KillSwitchScope,
)
from app.services.bots_service import (
    BotAnalyticsService,
    BotService,
    CircuitBreakerService,
    KillSwitchService,
    automation_monitor_tick,
)

router = APIRouter()


# ---- Pydantic DTOs -------------------------------------------------------

class BotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    strategy_key: str
    symbols: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    risk_config: dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    execution_mode: str = "paper"
    broker_account_id: Optional[str] = None
    initial_capital: float = 0
    tags: list[str] = Field(default_factory=list)


class BotUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    risk_config: Optional[dict[str, Any]] = None
    symbols: Optional[list[str]] = None
    tags: Optional[list[str]] = None


class BotRead(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    strategy_key: str
    symbols: list[str] = []
    params: dict[str, Any] = {}
    risk_config: dict[str, Any] = {}
    execution_mode: str
    broker_account_id: Optional[str] = None
    initial_capital: float = 0
    status: str
    engine_session_id: Optional[str] = None
    day_pnl_cents: int = 0
    total_pnl_cents: int = 0
    trades_today: int = 0
    total_trades: int = 0
    consecutive_losses: int = 0
    peak_pnl_cents: int = 0
    tags: list[str] = []
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    last_heartbeat_at: Optional[str] = None
    last_error: Optional[str] = None
    is_killed: bool = False

    @classmethod
    def from_orm_(cls, bot) -> "BotRead":
        return cls(
            id=bot.id, user_id=bot.user_id, name=bot.name,
            description=bot.description, strategy_key=bot.strategy_key,
            symbols=list(bot.symbols or []), params=dict(bot.params or {}),
            risk_config=dict(bot.risk_config or {}),
            execution_mode=bot.execution_mode,
            broker_account_id=bot.broker_account_id,
            initial_capital=float(bot.initial_capital or 0),
            status=bot.status.value if hasattr(bot.status, "value") else str(bot.status),
            engine_session_id=bot.engine_session_id,
            day_pnl_cents=int(bot.day_pnl_cents or 0),
            total_pnl_cents=int(bot.total_pnl_cents or 0),
            trades_today=int(bot.trades_today or 0),
            total_trades=int(bot.total_trades or 0),
            consecutive_losses=int(bot.consecutive_losses or 0),
            peak_pnl_cents=int(bot.peak_pnl_cents or 0),
            tags=list(bot.tags or []),
            started_at=bot.started_at.isoformat() if bot.started_at else None,
            stopped_at=bot.stopped_at.isoformat() if bot.stopped_at else None,
            last_heartbeat_at=bot.last_heartbeat_at.isoformat() if bot.last_heartbeat_at else None,
            last_error=bot.last_error, is_killed=bool(bot.is_killed),
        )


class BreakerConfigUpsert(BaseModel):
    level: BreakerLevel
    breaker_type: BreakerType
    value_num: Optional[float] = None
    value_json: Optional[dict[str, Any]] = None
    enabled: bool = True
    action: str = "pause"  # pause|stop|kill|block_new|warn
    unit: Optional[str] = None
    cooldown_seconds: int = 0
    notify: bool = True
    user_id: Optional[str] = None
    strategy_key: Optional[str] = None
    broker_type: Optional[str] = None
    bot_id: Optional[str] = None
    notes: Optional[str] = None


class KillSwitchTrigger(BaseModel):
    scope: KillSwitchScope
    target_user_id: Optional[str] = None
    target_bot_id: Optional[str] = None
    reason: str = ""
    close_positions: bool = False


# =========================================================================
# /api/v1/bots  (user)
# =========================================================================

@router.get("/bots", summary="List my bots")
async def list_bots(current_user: CurrentUser, session: DBSession,
                    status: Optional[str] = Query(None),
                    limit: int = Query(100, ge=1, le=500),
                    offset: int = Query(0, ge=0)) -> list[BotRead]:
    bots = await BotService(session).list_for_user(
        current_user.id, status=status, limit=limit, offset=offset
    )
    return [BotRead.from_orm_(b) for b in bots]


@router.post("/bots", summary="Create a bot", status_code=201)
async def create_bot(payload: BotCreate, current_user: CurrentUser,
                     session: DBSession) -> BotRead:
    try:
        bot = await BotService(session).create(
            user=current_user, name=payload.name,
            strategy_key=payload.strategy_key, symbols=payload.symbols,
            params=payload.params, risk_config=payload.risk_config,
            description=payload.description,
            execution_mode=payload.execution_mode,
            broker_account_id=payload.broker_account_id,
            initial_capital=payload.initial_capital, tags=payload.tags,
        )
        await session.commit()
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return BotRead.from_orm_(bot)


@router.get("/bots/{bot_id}", summary="Get bot by id")
async def get_bot(bot_id: str, current_user: CurrentUser,
                  session: DBSession) -> BotRead:
    bot = await BotService(session).get(bot_id, user_id=current_user.id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    return BotRead.from_orm_(bot)


@router.patch("/bots/{bot_id}", summary="Update bot config")
async def update_bot(bot_id: str, payload: BotUpdate, current_user: CurrentUser,
                     session: DBSession) -> BotRead:
    svc = BotService(session)
    bot = await svc.get(bot_id, user_id=current_user.id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    if payload.name is not None:
        bot.name = payload.name
    if payload.description is not None:
        bot.description = payload.description
    if payload.params is not None:
        bot.params = payload.params
    if payload.risk_config is not None:
        bot.risk_config = payload.risk_config
    if payload.symbols is not None:
        bot.symbols = payload.symbols
    if payload.tags is not None:
        bot.tags = payload.tags
    await svc._log(bot, "update_config", actor=current_user,
                   details=payload.model_dump(exclude_none=True))
    await session.commit()
    return BotRead.from_orm_(bot)


@router.delete("/bots/{bot_id}", summary="Delete a bot", status_code=204,
               response_class=Response)
async def delete_bot(bot_id: str, current_user: CurrentUser,
                     session: DBSession) -> Response:
    svc = BotService(session)
    bot = await svc.get(bot_id, user_id=current_user.id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    try:
        await svc.delete(bot, actor=current_user)
        await session.commit()
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return Response(status_code=204)


# ---- lifecycle ----------------------------------------------------------

async def _dispatch(action: str, bot_id: str, current_user, session):
    svc = BotService(session)
    bot = await svc.get(bot_id, user_id=current_user.id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    try:
        if action == "start":
            bot = await svc.start(bot, actor=current_user)
        elif action == "stop":
            bot = await svc.stop(bot, actor=current_user)
        elif action == "pause":
            bot = await svc.pause(bot, actor=current_user)
        elif action == "resume":
            bot = await svc.resume(bot, actor=current_user)
        elif action == "kill":
            bot = await svc.kill(bot, actor=current_user)
        elif action == "clear-kill":
            bot = await svc.clear_kill(bot, actor=current_user)
        await session.commit()
    except ForbiddenError as e:
        # Preserve any audit/breaker-event rows written before the guard rejected.
        try:
            await session.commit()
        except Exception:  # pragma: no cover
            await session.rollback()
        raise HTTPException(status_code=403, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return BotRead.from_orm_(bot)


@router.post("/bots/{bot_id}/start")
async def start_bot(bot_id: str, current_user: CurrentUser, session: DBSession):
    return await _dispatch("start", bot_id, current_user, session)


@router.post("/bots/{bot_id}/stop")
async def stop_bot(bot_id: str, current_user: CurrentUser, session: DBSession):
    return await _dispatch("stop", bot_id, current_user, session)


@router.post("/bots/{bot_id}/pause")
async def pause_bot(bot_id: str, current_user: CurrentUser, session: DBSession):
    return await _dispatch("pause", bot_id, current_user, session)


@router.post("/bots/{bot_id}/resume")
async def resume_bot(bot_id: str, current_user: CurrentUser, session: DBSession):
    return await _dispatch("resume", bot_id, current_user, session)


@router.post("/bots/{bot_id}/kill")
async def kill_bot(bot_id: str, current_user: CurrentUser, session: DBSession):
    return await _dispatch("kill", bot_id, current_user, session)


@router.post("/bots/{bot_id}/clear-kill")
async def clear_kill(bot_id: str, current_user: CurrentUser, session: DBSession):
    return await _dispatch("clear-kill", bot_id, current_user, session)


# ---- health / monitoring ------------------------------------------------

@router.get("/bots/{bot_id}/health", summary="Live health snapshot for a bot")
async def bot_health(bot_id: str, current_user: CurrentUser, session: DBSession):
    svc = BotService(session)
    bot = await svc.get(bot_id, user_id=current_user.id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    await svc.refresh_pnl_from_engine(bot)
    h = await svc.health(bot)
    await session.commit()
    from dataclasses import asdict
    return asdict(h)


@router.get("/bots/live/monitoring", summary="Aggregate live monitoring for current user")
async def live_monitoring(current_user: CurrentUser, session: DBSession):
    """One-shot mobile snapshot: today's totals, breaker states, health."""
    svc = BotService(session)
    bots = await svc.list_for_user(current_user.id)
    from dataclasses import asdict
    healths = []
    for b in bots:
        await svc.refresh_pnl_from_engine(b)
        h = await svc.health(b)
        healths.append({"bot": BotRead.from_orm_(b).model_dump(), "health": asdict(h)})
    summary = await BotAnalyticsService(session).summary_for_user(current_user.id)
    active_kill = await KillSwitchService(session).is_user_active(current_user.id)
    global_kill = await KillSwitchService(session).is_global_active()
    await session.commit()
    return {
        "summary": summary,
        "bots": healths,
        "kill_switch": {
            "user_active": active_kill,
            "global_active": global_kill,
        },
    }


# =========================================================================
# /api/v1/kill-switch  (user + admin)
# =========================================================================

@router.post("/kill-switch/trigger",
             summary="Trigger kill switch (bot/user/global)")
async def trigger_kill_switch(payload: KillSwitchTrigger,
                              current_user: CurrentUser, session: DBSession):
    # scope==GLOBAL requires admin
    if payload.scope == KillSwitchScope.GLOBAL:
        if current_user.role.value != "admin":
            raise HTTPException(status_code=403, detail="Admin required for global kill")
        target_user_id = None
        target_bot_id = None
    elif payload.scope == KillSwitchScope.USER:
        target_user_id = payload.target_user_id or current_user.id
        target_bot_id = None
        if target_user_id != current_user.id and current_user.role.value != "admin":
            raise HTTPException(status_code=403, detail="Admin required for other users")
    else:  # BOT
        if not payload.target_bot_id:
            raise HTTPException(status_code=400, detail="target_bot_id required")
        # verify ownership
        b = await BotService(session).get(payload.target_bot_id,
                                          user_id=current_user.id if current_user.role.value != "admin" else None)
        if b is None:
            raise HTTPException(status_code=404, detail="Bot not found")
        target_user_id = b.user_id
        target_bot_id = b.id

    ev = await KillSwitchService(session).trigger(
        scope=payload.scope, actor=current_user,
        target_user_id=target_user_id, target_bot_id=target_bot_id,
        reason=payload.reason, close_positions=payload.close_positions,
    )
    await session.commit()
    return {
        "id": ev.id, "scope": ev.scope.value, "bots_stopped": ev.bots_stopped,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
        "reason": ev.reason,
    }


@router.get("/kill-switch/events", summary="List kill switch events")
async def list_kill_events(current_user: CurrentUser, session: DBSession,
                           active_only: bool = Query(False),
                           limit: int = Query(100, ge=1, le=500)):
    events = await KillSwitchService(session).list_events(
        active_only=active_only, limit=limit
    )
    # Non-admins see only events involving themselves.
    if current_user.role.value != "admin":
        events = [e for e in events if e.target_user_id in (None, current_user.id)
                  or e.actor_user_id == current_user.id]
    return [
        {"id": e.id, "scope": e.scope.value,
         "target_user_id": e.target_user_id, "target_bot_id": e.target_bot_id,
         "reason": e.reason, "bots_stopped": e.bots_stopped,
         "created_at": e.created_at.isoformat() if e.created_at else None,
         "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
         "actor_user_id": e.actor_user_id}
        for e in events
    ]


@router.post("/kill-switch/{event_id}/resolve",
             summary="Resolve a kill switch event (admin)")
async def resolve_kill(event_id: str, admin: AdminUser, session: DBSession):
    ev = await KillSwitchService(session).resolve(event_id, actor=admin)
    await session.commit()
    return {"id": ev.id, "resolved_at": ev.resolved_at.isoformat() if ev.resolved_at else None}


# =========================================================================
# /api/v1/circuit-breakers  (admin CRUD + user read)
# =========================================================================

@router.get("/circuit-breakers/configs",
            summary="List circuit breaker configs")
async def list_cb_configs(current_user: CurrentUser, session: DBSession,
                          level: Optional[BreakerLevel] = Query(None)):
    # Non-admins get their own configs + global.
    user_id = None if current_user.role.value == "admin" else current_user.id
    cfgs = await CircuitBreakerService(session).list_configs(
        level=level, user_id=user_id
    )
    return [
        {"id": c.id, "level": c.level.value, "breaker_type": c.breaker_type.value,
         "enabled": c.enabled, "action": c.action, "value_num": float(c.value_num) if c.value_num is not None else None,
         "value_json": c.value_json, "unit": c.unit,
         "user_id": c.user_id, "strategy_key": c.strategy_key,
         "broker_type": c.broker_type, "bot_id": c.bot_id,
         "cooldown_seconds": c.cooldown_seconds, "notify": c.notify,
         "notes": c.notes}
        for c in cfgs
    ]


@router.post("/circuit-breakers/configs",
             summary="Create/update circuit breaker config (admin)")
async def upsert_cb_config(payload: BreakerConfigUpsert,
                           admin: AdminUser, session: DBSession):
    cfg = await CircuitBreakerService(session).upsert(
        level=payload.level, breaker_type=payload.breaker_type,
        value_num=payload.value_num, value_json=payload.value_json,
        enabled=payload.enabled, action=payload.action, unit=payload.unit,
        cooldown_seconds=payload.cooldown_seconds, notify=payload.notify,
        user_id=payload.user_id, strategy_key=payload.strategy_key,
        broker_type=payload.broker_type, bot_id=payload.bot_id,
        notes=payload.notes, actor=admin,
    )
    await session.commit()
    return {"id": cfg.id, "level": cfg.level.value,
            "breaker_type": cfg.breaker_type.value, "enabled": cfg.enabled}


@router.delete("/circuit-breakers/configs/{cfg_id}",
               summary="Delete a circuit breaker config (admin)",
               status_code=204, response_class=Response)
async def delete_cb_config(cfg_id: str, admin: AdminUser,
                           session: DBSession) -> Response:
    try:
        await CircuitBreakerService(session).delete(cfg_id, actor=admin)
        await session.commit()
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Config not found")
    return Response(status_code=204)


@router.get("/circuit-breakers/events",
            summary="List circuit breaker events")
async def list_cb_events(current_user: CurrentUser, session: DBSession,
                         bot_id: Optional[str] = Query(None),
                         limit: int = Query(100, ge=1, le=500)):
    user_filter = None if current_user.role.value == "admin" else current_user.id
    events = await CircuitBreakerService(session).list_events(
        user_id=user_filter, bot_id=bot_id, limit=limit
    )
    return [
        {"id": e.id, "level": e.level.value, "breaker_type": e.breaker_type.value,
         "user_id": e.user_id, "bot_id": e.bot_id, "strategy_key": e.strategy_key,
         "broker_type": e.broker_type,
         "triggered_value": float(e.triggered_value) if e.triggered_value is not None else None,
         "threshold": float(e.threshold) if e.threshold is not None else None,
         "action_taken": e.action_taken, "reason": e.reason,
         "created_at": e.created_at.isoformat() if e.created_at else None,
         "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None}
        for e in events
    ]


# =========================================================================
# /api/v1/bot-analytics
# =========================================================================

@router.get("/bot-analytics/summary")
async def analytics_summary(current_user: CurrentUser, session: DBSession):
    return await BotAnalyticsService(session).summary_for_user(current_user.id)


@router.get("/bot-analytics/{bot_id}")
async def analytics_per_bot(bot_id: str, current_user: CurrentUser,
                            session: DBSession):
    bot = await BotService(session).get(bot_id, user_id=current_user.id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")
    return await BotAnalyticsService(session).per_bot(bot)


# =========================================================================
# /api/v1/automation-monitor  (admin trigger + status)
# =========================================================================

@router.post("/automation-monitor/tick",
             summary="Run automation monitor sweep (admin — normally scheduled)")
async def automation_tick(admin: AdminUser, session: DBSession):
    from app.db.session import async_session_factory
    stats = await automation_monitor_tick(async_session_factory)
    return stats


# =========================================================================
# /api/v1/monitoring/broker-health
# =========================================================================

@router.get("/monitoring/broker-health",
            summary="Live broker WebSocket health snapshot")
async def broker_health(current_user: CurrentUser):
    """Returns per-broker connection state.

    Rows include: broker_type, connected, last_connect_at, last_disconnect_at,
    last_reconnect_at, last_heartbeat_at, downtime_seconds,
    total_downtime_seconds, retry_count, total_reconnects, total_disconnects,
    incident_open, last_incident_id, last_error.

    Available to all authenticated users so the Bot Dashboard can show the
    banner; admin gets the same payload.
    """
    from app.services.broker_health import tracker as _bh
    return {"brokers": _bh.snapshot()}


@router.get("/monitoring/broker-health/{broker_type}",
            summary="Health for a single broker")
async def broker_health_one(broker_type: str, current_user: CurrentUser):
    from app.services.broker_health import tracker as _bh
    row = _bh.get(broker_type)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No health data for '{broker_type}'")
    return row
