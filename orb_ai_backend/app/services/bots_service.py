"""Phase 4 — Bot / CircuitBreaker / KillSwitch service layer.

All services are thin, provider-agnostic, and reuse existing infrastructure:
- ``PermissionService`` for plan-based limits (max_running_bots, max_open_positions).
- ``strategy_manager`` for start/stop of underlying engine sessions.
- ``NotificationService`` for user + admin alerts.
- ``AuditService`` for privileged action logging.

Never bypasses the existing subscription / permission gates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.bot import (
    Bot,
    BotAuditLog,
    BotStatus,
    BreakerLevel,
    BreakerType,
    CircuitBreakerConfig,
    CircuitBreakerEvent,
    KillSwitchEvent,
    KillSwitchScope,
)
from app.models.engine import EngineSession, EngineSessionStatus
from app.models.notification import NotificationEvent, NotificationSeverity
from app.models.user import User
from app.services.audit_service import AuditService
from app.services.notification_service import NotificationService
from app.services.permissions import PermissionService

logger = get_logger(__name__)


# ==========================================================================
# BotService
# ==========================================================================


@dataclass(slots=True)
class BotHealth:
    bot_id: str
    status: str
    day_pnl_cents: int
    total_pnl_cents: int
    trades_today: int
    consecutive_losses: int
    open_positions: int
    active_orders: int
    last_heartbeat_at: Optional[str]
    broker_connected: bool
    breaker_active: bool
    is_killed: bool


class BotService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)
        self.perms = PermissionService(session)

    # ---- audit helper ---------------------------------------------------
    async def _log(self, bot: Bot, action: str, actor: Optional[User] = None,
                   previous: Optional[str] = None, new: Optional[str] = None,
                   details: Optional[dict[str, Any]] = None) -> None:
        row = BotAuditLog(
            bot_id=bot.id, user_id=bot.user_id,
            actor_user_id=actor.id if actor else None,
            action=action, previous_status=previous, new_status=new,
            details=details,
        )
        self.session.add(row)
        await self.audit.record(
            action=f"bot.{action}", target_type="bot", target_id=bot.id,
            actor=actor, details=details,
        )

    # ---- CRUD -----------------------------------------------------------
    async def create(self, *, user: User, name: str, strategy_key: str,
                     symbols: list[str], params: dict | None = None,
                     risk_config: dict | None = None, description: str | None = None,
                     execution_mode: str = "paper", broker_account_id: str | None = None,
                     initial_capital: float = 0, tags: list[str] | None = None) -> Bot:
        # Uniqueness by (user, name)
        existing = (await self.session.execute(
            select(Bot).where(Bot.user_id == user.id, Bot.name == name)
        )).scalar_one_or_none()
        if existing is not None:
            from app.core.exceptions import ConflictError
            raise ConflictError(f"Bot '{name}' already exists")

        bot = Bot(
            user_id=user.id, name=name, description=description,
            strategy_key=strategy_key, symbols=list(symbols),
            params=params or {}, risk_config=risk_config or {},
            execution_mode=execution_mode, broker_account_id=broker_account_id,
            initial_capital=float(initial_capital), tags=tags or [],
            status=BotStatus.IDLE,
        )
        self.session.add(bot)
        await self.session.flush()
        await self._log(bot, "create", actor=user, new=BotStatus.IDLE.value,
                        details={"strategy_key": strategy_key, "symbols": symbols})
        return bot

    async def list_for_user(self, user_id: str, *, status: Optional[str] = None,
                            limit: int = 100, offset: int = 0) -> list[Bot]:
        stmt = select(Bot).where(Bot.user_id == user_id)
        if status:
            stmt = stmt.where(Bot.status == status)
        stmt = stmt.order_by(Bot.updated_at.desc()).offset(offset).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get(self, bot_id: str, *, user_id: Optional[str] = None) -> Optional[Bot]:
        b = await self.session.get(Bot, bot_id)
        if b is None:
            return None
        if user_id and b.user_id != user_id:
            return None
        return b

    async def delete(self, bot: Bot, *, actor: User) -> None:
        if bot.status in (BotStatus.RUNNING, BotStatus.STARTING):
            from app.core.exceptions import ConflictError
            raise ConflictError("Stop the bot before deleting it")
        await self._log(bot, "delete", actor=actor, previous=bot.status.value)
        await self.session.delete(bot)

    # ---- lifecycle ------------------------------------------------------
    async def start(self, bot: Bot, *, actor: User) -> Bot:
        from app.core.exceptions import ConflictError, ForbiddenError

        # 1. Kill-switch check
        if bot.is_killed:
            raise ForbiddenError("Bot is killed — clear kill switch first")
        if await KillSwitchService(self.session).is_global_active():
            raise ForbiddenError("Global kill switch is active")
        if await KillSwitchService(self.session).is_user_active(bot.user_id):
            raise ForbiddenError("User kill switch is active")

        # 2. Permission checks (subscription + plan)
        user = await self.session.get(User, bot.user_id)
        if user is None:
            raise ForbiddenError("User not found")
        if not await self.perms.can_use_bot(user):
            raise ForbiddenError("Automation not enabled for your plan")
        if not await self.perms.can_use_strategy(user, bot.strategy_key):
            raise ForbiddenError(f"Strategy '{bot.strategy_key}' not allowed on your plan")

        # 3. Max running bots
        max_bots = await self.perms.max_running_bots(user)
        if max_bots >= 0:
            running = int((await self.session.execute(
                select(func.count()).select_from(Bot).where(
                    Bot.user_id == bot.user_id,
                    Bot.status.in_([BotStatus.RUNNING, BotStatus.STARTING]),
                    Bot.id != bot.id,
                )
            )).scalar_one())
            if running >= max_bots:
                raise ConflictError(f"Max running bots reached ({max_bots})")

        # 4. Global circuit-breakers
        breaker_svc = CircuitBreakerService(self.session)
        blocked = await breaker_svc.check_pre_start(bot=bot, user=user)
        if blocked is not None:
            raise ForbiddenError(f"Circuit breaker: {blocked}")

        if bot.status == BotStatus.RUNNING:
            return bot

        prev = bot.status.value
        bot.status = BotStatus.STARTING
        await self.session.flush()

        # Actually start the underlying engine session via strategy_manager.
        from app.engine.strategy.manager import manager as strategy_manager
        session = await strategy_manager.start(
            user_id=bot.user_id, strategy_name=bot.strategy_key,
            symbols=list(bot.symbols or []), params=dict(bot.params or {}),
            risk_config=dict(bot.risk_config or {}),
            initial_capital=float(bot.initial_capital),
            execution_mode=bot.execution_mode,
            broker_account_id=bot.broker_account_id,
        )
        bot.engine_session_id = session.id
        bot.status = BotStatus.RUNNING
        bot.started_at = datetime.now(timezone.utc)
        bot.last_heartbeat_at = bot.started_at
        bot.last_error = None
        await self._log(bot, "start", actor=actor, previous=prev,
                        new=BotStatus.RUNNING.value,
                        details={"engine_session_id": session.id})
        return bot

    async def stop(self, bot: Bot, *, actor: User, reason: str = "user_stop") -> Bot:
        prev = bot.status.value
        if bot.engine_session_id:
            try:
                from app.engine.strategy.manager import manager as strategy_manager
                await strategy_manager.stop(bot.engine_session_id, user_id=bot.user_id)
            except Exception:  # pragma: no cover
                logger.exception("bot_engine_stop_failed", extra={"bot_id": bot.id})
        bot.status = BotStatus.STOPPED
        bot.stopped_at = datetime.now(timezone.utc)
        bot.engine_session_id = None
        await self._log(bot, "stop", actor=actor, previous=prev,
                        new=BotStatus.STOPPED.value, details={"reason": reason})
        return bot

    async def pause(self, bot: Bot, *, actor: User, reason: str = "user_pause") -> Bot:
        # Pause = stop engine session, mark PAUSED so it can be resumed.
        if bot.status != BotStatus.RUNNING:
            return bot
        prev = bot.status.value
        if bot.engine_session_id:
            try:
                from app.engine.strategy.manager import manager as strategy_manager
                await strategy_manager.stop(bot.engine_session_id, user_id=bot.user_id)
            except Exception:  # pragma: no cover
                logger.exception("bot_engine_pause_failed", extra={"bot_id": bot.id})
        bot.status = BotStatus.PAUSED
        bot.engine_session_id = None
        await self._log(bot, "pause", actor=actor, previous=prev,
                        new=BotStatus.PAUSED.value, details={"reason": reason})
        return bot

    async def resume(self, bot: Bot, *, actor: User) -> Bot:
        if bot.status not in (BotStatus.PAUSED, BotStatus.BREAKER_TRIPPED):
            from app.core.exceptions import ConflictError
            raise ConflictError("Only paused or breaker-tripped bots can be resumed")
        return await self.start(bot, actor=actor)

    async def kill(self, bot: Bot, *, actor: User, reason: str = "kill_switch") -> Bot:
        prev = bot.status.value
        if bot.engine_session_id:
            try:
                from app.engine.strategy.manager import manager as strategy_manager
                await strategy_manager.stop(bot.engine_session_id, user_id=bot.user_id)
            except Exception:  # pragma: no cover
                logger.exception("bot_kill_engine_stop_failed", extra={"bot_id": bot.id})
        bot.status = BotStatus.KILLED
        bot.is_killed = True
        bot.stopped_at = datetime.now(timezone.utc)
        bot.engine_session_id = None
        await self._log(bot, "kill", actor=actor, previous=prev,
                        new=BotStatus.KILLED.value, details={"reason": reason})
        return bot

    async def clear_kill(self, bot: Bot, *, actor: User) -> Bot:
        bot.is_killed = False
        if bot.status == BotStatus.KILLED:
            bot.status = BotStatus.STOPPED
        await self._log(bot, "clear_kill", actor=actor,
                        new=bot.status.value)
        return bot

    async def trip_breaker(self, bot: Bot, *, action: str, reason: str) -> Bot:
        prev = bot.status.value
        if action in ("stop", "kill"):
            if bot.engine_session_id:
                try:
                    from app.engine.strategy.manager import manager as strategy_manager
                    await strategy_manager.stop(bot.engine_session_id, user_id=bot.user_id)
                except Exception:  # pragma: no cover
                    pass
            bot.engine_session_id = None
            bot.status = BotStatus.KILLED if action == "kill" else BotStatus.STOPPED
            if action == "kill":
                bot.is_killed = True
        else:
            bot.status = BotStatus.BREAKER_TRIPPED
            if bot.engine_session_id:
                try:
                    from app.engine.strategy.manager import manager as strategy_manager
                    await strategy_manager.stop(bot.engine_session_id, user_id=bot.user_id)
                except Exception:  # pragma: no cover
                    pass
                bot.engine_session_id = None
        bot.stopped_at = datetime.now(timezone.utc)
        await self._log(bot, "breaker_trip", previous=prev, new=bot.status.value,
                        details={"action": action, "reason": reason})
        return bot

    # ---- health / monitoring -------------------------------------------
    async def health(self, bot: Bot) -> BotHealth:
        from app.models.engine import PaperOrder, PaperPosition, OrderStatus
        open_positions = 0
        active_orders = 0
        if bot.engine_session_id:
            open_positions = int((await self.session.execute(
                select(func.count()).select_from(PaperPosition).where(
                    PaperPosition.engine_session_id == bot.engine_session_id,
                    PaperPosition.net_quantity != 0,
                )
            )).scalar_one())
            active_orders = int((await self.session.execute(
                select(func.count()).select_from(PaperOrder).where(
                    PaperOrder.engine_session_id == bot.engine_session_id,
                    PaperOrder.status.in_([OrderStatus.PENDING, OrderStatus.OPEN,
                                           OrderStatus.PARTIALLY_FILLED]),
                )
            )).scalar_one())
        broker_connected = True
        if bot.execution_mode == "live" and bot.broker_account_id:
            broker_connected = await CircuitBreakerService(self.session).is_broker_healthy(
                bot.broker_account_id
            )
        return BotHealth(
            bot_id=bot.id, status=bot.status.value,
            day_pnl_cents=int(bot.day_pnl_cents or 0),
            total_pnl_cents=int(bot.total_pnl_cents or 0),
            trades_today=int(bot.trades_today or 0),
            consecutive_losses=int(bot.consecutive_losses or 0),
            open_positions=open_positions, active_orders=active_orders,
            last_heartbeat_at=bot.last_heartbeat_at.isoformat() if bot.last_heartbeat_at else None,
            broker_connected=broker_connected,
            breaker_active=bot.status == BotStatus.BREAKER_TRIPPED,
            is_killed=bot.is_killed,
        )

    async def refresh_pnl_from_engine(self, bot: Bot) -> Bot:
        """Roll aggregate KPIs up from the underlying engine session."""
        if not bot.engine_session_id:
            return bot
        sess = await self.session.get(EngineSession, bot.engine_session_id)
        if sess is None:
            return bot
        day = int(float(sess.day_pnl or 0) * 100)
        bot.day_pnl_cents = day
        bot.total_pnl_cents = int(float(sess.realized_pnl or 0) * 100)
        if day > bot.peak_pnl_cents:
            bot.peak_pnl_cents = day
        bot.last_heartbeat_at = sess.last_heartbeat_at
        return bot


# ==========================================================================
# KillSwitchService
# ==========================================================================


class KillSwitchService:
    """Emergency kill switch — three scopes: bot, user, global."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    async def _active_event(self, scope: KillSwitchScope, *,
                            target_user_id: Optional[str] = None,
                            target_bot_id: Optional[str] = None) -> Optional[KillSwitchEvent]:
        stmt = select(KillSwitchEvent).where(
            KillSwitchEvent.scope == scope,
            KillSwitchEvent.resolved_at.is_(None),
        )
        if target_user_id is not None:
            stmt = stmt.where(KillSwitchEvent.target_user_id == target_user_id)
        if target_bot_id is not None:
            stmt = stmt.where(KillSwitchEvent.target_bot_id == target_bot_id)
        stmt = stmt.order_by(KillSwitchEvent.created_at.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def is_global_active(self) -> bool:
        return (await self._active_event(KillSwitchScope.GLOBAL)) is not None

    async def is_user_active(self, user_id: str) -> bool:
        return (await self._active_event(KillSwitchScope.USER, target_user_id=user_id)) is not None

    async def trigger(self, *, scope: KillSwitchScope, actor: User,
                      target_user_id: Optional[str] = None,
                      target_bot_id: Optional[str] = None,
                      reason: str = "", close_positions: bool = False) -> KillSwitchEvent:
        # Stop matching bots.
        stopped = 0
        bots_stmt = select(Bot).where(
            Bot.status.in_([BotStatus.RUNNING, BotStatus.STARTING, BotStatus.PAUSED])
        )
        if scope == KillSwitchScope.BOT and target_bot_id:
            bots_stmt = bots_stmt.where(Bot.id == target_bot_id)
        elif scope == KillSwitchScope.USER and target_user_id:
            bots_stmt = bots_stmt.where(Bot.user_id == target_user_id)
        # scope == GLOBAL → all bots
        bots = list((await self.session.execute(bots_stmt)).scalars().all())
        bsvc = BotService(self.session)
        for b in bots:
            await bsvc.kill(b, actor=actor, reason=f"kill_switch:{scope.value}")
            stopped += 1

        ev = KillSwitchEvent(
            scope=scope,
            actor_user_id=actor.id,
            target_user_id=target_user_id,
            target_bot_id=target_bot_id,
            reason=reason,
            close_positions=close_positions,
            bots_stopped=stopped,
            positions_closed=0,  # not implementing live position-close here
        )
        self.session.add(ev)
        await self.session.flush()
        await self.audit.record(
            action="kill_switch.trigger", target_type="kill_switch",
            target_id=ev.id, actor=actor,
            details={"scope": scope.value, "bots_stopped": stopped,
                     "target_user_id": target_user_id, "target_bot_id": target_bot_id,
                     "reason": reason},
        )

        # Notify affected users.
        try:
            noti = NotificationService(self.session)
            affected_user_ids = {b.user_id for b in bots}
            for uid in affected_user_ids:
                u = await self.session.get(User, uid)
                if u:
                    await noti.notify(
                        user=u, event=NotificationEvent.SYSTEM_ALERT,
                        title="Kill switch activated",
                        body=f"Scope={scope.value}. Bots stopped: {stopped}",
                        severity=NotificationSeverity.CRITICAL,
                        payload={"scope": scope.value, "reason": reason},
                    )
        except Exception:  # pragma: no cover
            logger.exception("kill_switch_notify_failed")
        return ev

    async def resolve(self, event_id: str, *, actor: User) -> KillSwitchEvent:
        ev = await self.session.get(KillSwitchEvent, event_id)
        if ev is None:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("KillSwitchEvent not found")
        ev.resolved_at = datetime.now(timezone.utc)
        await self.audit.record(
            action="kill_switch.resolve", target_type="kill_switch",
            target_id=ev.id, actor=actor,
        )
        return ev

    async def list_events(self, *, active_only: bool = False,
                          limit: int = 100) -> list[KillSwitchEvent]:
        stmt = select(KillSwitchEvent)
        if active_only:
            stmt = stmt.where(KillSwitchEvent.resolved_at.is_(None))
        stmt = stmt.order_by(KillSwitchEvent.created_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())


# ==========================================================================
# CircuitBreakerService
# ==========================================================================


class CircuitBreakerService:
    """Configurable circuit breakers. Evaluated at bot start + pre-order.

    Enforcement points (integration hooks are separate — this service
    reports "should trip" decisions; callers act):
    1. ``check_pre_start(bot, user)`` → called by BotService.start.
    2. ``evaluate_bot_pnl(bot)`` → called by automation monitor.
    3. ``on_broker_failure(broker_type, kind)`` → increments counters.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit = AuditService(session)

    # ---- config CRUD ----------------------------------------------------
    async def upsert(self, *, level: BreakerLevel, breaker_type: BreakerType,
                     value_num: Optional[float] = None,
                     value_json: Optional[dict] = None,
                     enabled: bool = True, action: str = "pause",
                     unit: Optional[str] = None, cooldown_seconds: int = 0,
                     notify: bool = True, user_id: Optional[str] = None,
                     strategy_key: Optional[str] = None,
                     broker_type: Optional[str] = None,
                     bot_id: Optional[str] = None, notes: Optional[str] = None,
                     actor: Optional[User] = None) -> CircuitBreakerConfig:
        # Find existing config matching identity
        stmt = select(CircuitBreakerConfig).where(
            CircuitBreakerConfig.level == level,
            CircuitBreakerConfig.breaker_type == breaker_type,
            CircuitBreakerConfig.user_id.is_(None) if user_id is None else CircuitBreakerConfig.user_id == user_id,
            CircuitBreakerConfig.strategy_key.is_(None) if strategy_key is None else CircuitBreakerConfig.strategy_key == strategy_key,
            CircuitBreakerConfig.broker_type.is_(None) if broker_type is None else CircuitBreakerConfig.broker_type == broker_type,
            CircuitBreakerConfig.bot_id.is_(None) if bot_id is None else CircuitBreakerConfig.bot_id == bot_id,
        )
        cfg = (await self.session.execute(stmt)).scalar_one_or_none()
        if cfg is None:
            cfg = CircuitBreakerConfig(
                level=level, breaker_type=breaker_type,
                user_id=user_id, strategy_key=strategy_key,
                broker_type=broker_type, bot_id=bot_id,
                created_by_user_id=actor.id if actor else None,
            )
            self.session.add(cfg)
        cfg.value_num = value_num
        cfg.value_json = value_json
        cfg.enabled = enabled
        cfg.action = action
        cfg.unit = unit
        cfg.cooldown_seconds = cooldown_seconds
        cfg.notify = notify
        cfg.notes = notes
        await self.session.flush()
        await self.audit.record(
            action="circuit_breaker.upsert", target_type="circuit_breaker",
            target_id=cfg.id, actor=actor,
            details={"level": level.value, "breaker_type": breaker_type.value,
                     "enabled": enabled, "action": action,
                     "value_num": value_num, "value_json": value_json},
        )
        return cfg

    async def list_configs(self, *, level: Optional[BreakerLevel] = None,
                           user_id: Optional[str] = None) -> list[CircuitBreakerConfig]:
        stmt = select(CircuitBreakerConfig)
        if level is not None:
            stmt = stmt.where(CircuitBreakerConfig.level == level)
        if user_id is not None:
            stmt = stmt.where(or_(CircuitBreakerConfig.user_id == user_id,
                                  CircuitBreakerConfig.user_id.is_(None)))
        return list((await self.session.execute(
            stmt.order_by(CircuitBreakerConfig.level, CircuitBreakerConfig.breaker_type)
        )).scalars().all())

    async def delete(self, cfg_id: str, *, actor: User) -> None:
        cfg = await self.session.get(CircuitBreakerConfig, cfg_id)
        if cfg is None:
            from app.core.exceptions import NotFoundError
            raise NotFoundError("Config not found")
        await self.audit.record(
            action="circuit_breaker.delete", target_type="circuit_breaker",
            target_id=cfg_id, actor=actor,
        )
        await self.session.delete(cfg)

    # ---- event log ------------------------------------------------------
    async def _record_event(self, *, cfg: Optional[CircuitBreakerConfig],
                            level: BreakerLevel, breaker_type: BreakerType,
                            user_id: Optional[str], bot_id: Optional[str],
                            triggered_value: Optional[float],
                            threshold: Optional[float], action_taken: str,
                            reason: str,
                            strategy_key: Optional[str] = None,
                            broker_type: Optional[str] = None) -> CircuitBreakerEvent:
        ev = CircuitBreakerEvent(
            config_id=cfg.id if cfg else None,
            level=level, breaker_type=breaker_type,
            user_id=user_id, bot_id=bot_id,
            strategy_key=strategy_key, broker_type=broker_type,
            triggered_value=triggered_value, threshold=threshold,
            action_taken=action_taken, reason=reason,
        )
        self.session.add(ev)
        await self.session.flush()
        # Notify affected user if any
        if user_id and (cfg is None or cfg.notify):
            try:
                u = await self.session.get(User, user_id)
                if u:
                    await NotificationService(self.session).notify(
                        user=u, event=NotificationEvent.SYSTEM_ALERT,
                        title=f"Circuit breaker: {breaker_type.value}",
                        body=reason,
                        severity=NotificationSeverity.WARNING,
                        payload={"level": level.value, "type": breaker_type.value,
                                 "action": action_taken, "threshold": threshold,
                                 "value": triggered_value},
                    )
            except Exception:  # pragma: no cover
                logger.exception("cb_notify_failed")
        return ev

    async def list_events(self, *, user_id: Optional[str] = None,
                          bot_id: Optional[str] = None,
                          limit: int = 100) -> list[CircuitBreakerEvent]:
        stmt = select(CircuitBreakerEvent)
        if user_id:
            stmt = stmt.where(CircuitBreakerEvent.user_id == user_id)
        if bot_id:
            stmt = stmt.where(CircuitBreakerEvent.bot_id == bot_id)
        return list((await self.session.execute(
            stmt.order_by(CircuitBreakerEvent.created_at.desc()).limit(limit)
        )).scalars().all())

    # ---- lookup helpers -------------------------------------------------
    async def _matching_config(self, level: BreakerLevel, breaker_type: BreakerType,
                               *, user_id: Optional[str] = None,
                               strategy_key: Optional[str] = None,
                               broker_type: Optional[str] = None,
                               bot_id: Optional[str] = None
                               ) -> Optional[CircuitBreakerConfig]:
        stmt = select(CircuitBreakerConfig).where(
            CircuitBreakerConfig.level == level,
            CircuitBreakerConfig.breaker_type == breaker_type,
            CircuitBreakerConfig.enabled == True,  # noqa: E712
        )
        if bot_id:
            stmt = stmt.where(or_(CircuitBreakerConfig.bot_id == bot_id,
                                  CircuitBreakerConfig.bot_id.is_(None)))
        if user_id:
            stmt = stmt.where(or_(CircuitBreakerConfig.user_id == user_id,
                                  CircuitBreakerConfig.user_id.is_(None)))
        if strategy_key:
            stmt = stmt.where(or_(CircuitBreakerConfig.strategy_key == strategy_key,
                                  CircuitBreakerConfig.strategy_key.is_(None)))
        if broker_type:
            stmt = stmt.where(or_(CircuitBreakerConfig.broker_type == broker_type,
                                  CircuitBreakerConfig.broker_type.is_(None)))
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none()

    # ---- checks ---------------------------------------------------------
    async def check_pre_start(self, *, bot: Bot, user: User) -> Optional[str]:
        """Returns human-readable reason if the bot should NOT start; else None."""
        # Global: MAINTENANCE_MODE / EMERGENCY_STOP / HOLIDAY_LOCK
        for bt in (BreakerType.MAINTENANCE_MODE, BreakerType.EMERGENCY_STOP,
                   BreakerType.HOLIDAY_LOCK):
            cfg = await self._matching_config(BreakerLevel.GLOBAL, bt)
            if cfg and cfg.enabled:
                await self._record_event(
                    cfg=cfg, level=BreakerLevel.GLOBAL, breaker_type=bt,
                    user_id=user.id, bot_id=bot.id,
                    triggered_value=None, threshold=None,
                    action_taken="block_new", reason=f"{bt.value} active",
                )
                return f"{bt.value} is active"

        # User: MAX_DAILY_LOSS on today's aggregate
        cfg = await self._matching_config(BreakerLevel.USER,
                                          BreakerType.MAX_DAILY_LOSS,
                                          user_id=user.id)
        if cfg and cfg.value_num is not None:
            today_loss = await self._user_day_pnl_cents(user.id)
            if today_loss <= -abs(int(float(cfg.value_num))):
                await self._record_event(
                    cfg=cfg, level=BreakerLevel.USER,
                    breaker_type=BreakerType.MAX_DAILY_LOSS,
                    user_id=user.id, bot_id=bot.id,
                    triggered_value=today_loss, threshold=float(cfg.value_num),
                    action_taken="block_new",
                    reason=f"Max daily loss {today_loss/100:.2f} <= -{cfg.value_num/100:.2f}",
                )
                return "max daily loss reached"

        # User: MAX_CONSECUTIVE_LOSSES on this bot
        cfg = await self._matching_config(BreakerLevel.USER,
                                          BreakerType.MAX_CONSECUTIVE_LOSSES,
                                          user_id=user.id, bot_id=bot.id)
        if cfg and cfg.value_num is not None and bot.consecutive_losses >= int(cfg.value_num):
            await self._record_event(
                cfg=cfg, level=BreakerLevel.USER,
                breaker_type=BreakerType.MAX_CONSECUTIVE_LOSSES,
                user_id=user.id, bot_id=bot.id,
                triggered_value=bot.consecutive_losses, threshold=float(cfg.value_num),
                action_taken="block_new", reason="consecutive losses limit",
            )
            return "max consecutive losses reached"

        # Strategy cooldown
        cfg = await self._matching_config(BreakerLevel.STRATEGY,
                                          BreakerType.STRATEGY_COOLDOWN,
                                          strategy_key=bot.strategy_key)
        if cfg and cfg.value_num is not None and bot.stopped_at is not None:
            elapsed = (datetime.now(timezone.utc) - bot.stopped_at).total_seconds()
            if elapsed < float(cfg.value_num):
                return f"strategy cooldown ({int(float(cfg.value_num) - elapsed)}s left)"

        return None

    async def _user_day_pnl_cents(self, user_id: str) -> int:
        stmt = select(func.coalesce(func.sum(Bot.day_pnl_cents), 0)).where(Bot.user_id == user_id)
        return int((await self.session.execute(stmt)).scalar_one() or 0)

    async def evaluate_bot_pnl(self, bot: Bot) -> Optional[CircuitBreakerEvent]:
        """After a fresh P&L update, check strategy / user daily-profit breakers."""
        user_id = bot.user_id
        user = await self.session.get(User, user_id)

        # MAX_DAILY_PROFIT — halt further trades once target is reached
        cfg = await self._matching_config(BreakerLevel.USER,
                                          BreakerType.MAX_DAILY_PROFIT,
                                          user_id=user_id)
        if cfg and cfg.value_num is not None:
            day = await self._user_day_pnl_cents(user_id)
            if day >= int(float(cfg.value_num)):
                ev = await self._record_event(
                    cfg=cfg, level=BreakerLevel.USER,
                    breaker_type=BreakerType.MAX_DAILY_PROFIT,
                    user_id=user_id, bot_id=bot.id,
                    triggered_value=day, threshold=float(cfg.value_num),
                    action_taken=cfg.action, reason="daily profit target reached",
                )
                if user and cfg.action in ("pause", "stop", "kill"):
                    await BotService(self.session).trip_breaker(
                        bot, action=cfg.action,
                        reason="daily profit target reached",
                    )
                return ev

        # MAX_DRAWDOWN on strategy (percent from peak_pnl)
        cfg = await self._matching_config(BreakerLevel.STRATEGY,
                                          BreakerType.MAX_DRAWDOWN,
                                          strategy_key=bot.strategy_key)
        if cfg and cfg.value_num is not None and bot.peak_pnl_cents > 0:
            drawdown_pct = (bot.peak_pnl_cents - bot.day_pnl_cents) * 100.0 / bot.peak_pnl_cents
            if drawdown_pct >= float(cfg.value_num):
                ev = await self._record_event(
                    cfg=cfg, level=BreakerLevel.STRATEGY,
                    breaker_type=BreakerType.MAX_DRAWDOWN,
                    user_id=user_id, bot_id=bot.id,
                    strategy_key=bot.strategy_key,
                    triggered_value=drawdown_pct, threshold=float(cfg.value_num),
                    action_taken=cfg.action, reason="strategy drawdown limit",
                )
                await BotService(self.session).trip_breaker(
                    bot, action=cfg.action, reason="strategy drawdown limit"
                )
                return ev
        return None

    async def is_broker_healthy(self, broker_account_id: str) -> bool:
        """Look up recent DISCONNECT_DETECTION / API_FAILURE events."""
        from app.models.broker import BrokerAccount
        acc = await self.session.get(BrokerAccount, broker_account_id)
        if acc is None:
            return False
        cfg = await self._matching_config(BreakerLevel.BROKER,
                                          BreakerType.DISCONNECT_DETECTION,
                                          broker_type=str(acc.broker_type))
        if not cfg:
            return True
        # Consider unhealthy if we have any unresolved event in the last N seconds.
        window = int(float(cfg.value_num or 60))
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window)
        stmt = select(CircuitBreakerEvent).where(
            CircuitBreakerEvent.level == BreakerLevel.BROKER,
            CircuitBreakerEvent.broker_type == str(acc.broker_type),
            CircuitBreakerEvent.created_at >= cutoff,
            CircuitBreakerEvent.resolved_at.is_(None),
        ).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none() is None

    async def report_broker_incident(self, *, broker_type: str,
                                     kind: BreakerType, reason: str = "",
                                     count: int = 1) -> CircuitBreakerEvent:
        cfg = await self._matching_config(BreakerLevel.BROKER, kind,
                                          broker_type=broker_type)
        return await self._record_event(
            cfg=cfg, level=BreakerLevel.BROKER, breaker_type=kind,
            user_id=None, bot_id=None, broker_type=broker_type,
            triggered_value=float(count), threshold=float(cfg.value_num) if cfg and cfg.value_num else None,
            action_taken=(cfg.action if cfg else "warn"),
            reason=reason or f"{kind.value} count={count}",
        )


# ==========================================================================
# BotAnalyticsService (light — for Phase 4 mobile summaries)
# ==========================================================================


class BotAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def summary_for_user(self, user_id: str) -> dict[str, Any]:
        running = int((await self.session.execute(
            select(func.count()).select_from(Bot).where(
                Bot.user_id == user_id, Bot.status == BotStatus.RUNNING
            )
        )).scalar_one())
        paused = int((await self.session.execute(
            select(func.count()).select_from(Bot).where(
                Bot.user_id == user_id, Bot.status == BotStatus.PAUSED
            )
        )).scalar_one())
        stopped = int((await self.session.execute(
            select(func.count()).select_from(Bot).where(
                Bot.user_id == user_id,
                Bot.status.in_([BotStatus.STOPPED, BotStatus.IDLE, BotStatus.KILLED])
            )
        )).scalar_one())
        day_pnl = int((await self.session.execute(
            select(func.coalesce(func.sum(Bot.day_pnl_cents), 0))
            .where(Bot.user_id == user_id)
        )).scalar_one() or 0)
        trades_today = int((await self.session.execute(
            select(func.coalesce(func.sum(Bot.trades_today), 0))
            .where(Bot.user_id == user_id)
        )).scalar_one() or 0)
        return {
            "running_bots": running,
            "paused_bots": paused,
            "stopped_bots": stopped,
            "day_pnl_cents": day_pnl,
            "trades_today": trades_today,
        }

    async def per_bot(self, bot: Bot) -> dict[str, Any]:
        events = await CircuitBreakerService(self.session).list_events(
            bot_id=bot.id, limit=20
        )
        return {
            "bot_id": bot.id,
            "name": bot.name,
            "day_pnl_cents": int(bot.day_pnl_cents or 0),
            "total_pnl_cents": int(bot.total_pnl_cents or 0),
            "trades_today": int(bot.trades_today or 0),
            "total_trades": int(bot.total_trades or 0),
            "consecutive_losses": int(bot.consecutive_losses or 0),
            "peak_pnl_cents": int(bot.peak_pnl_cents or 0),
            "breaker_events": [
                {"id": e.id, "type": e.breaker_type.value if hasattr(e.breaker_type, "value") else str(e.breaker_type),
                 "action": e.action_taken, "reason": e.reason,
                 "created_at": e.created_at.isoformat() if e.created_at else None}
                for e in events
            ],
        }


# ==========================================================================
# Automation monitor (call periodically from scheduler)
# ==========================================================================


async def automation_monitor_tick(session_factory) -> dict[str, int]:
    """Periodic health-check across every running bot.

    - Rolls P&L up from underlying engine sessions.
    - Evaluates user / strategy circuit breakers.
    - Flags bots whose ``last_heartbeat_at`` is older than 60s while
      status=RUNNING.

    Returns counters for logging.
    """
    stale_cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    stats = {"checked": 0, "breakers_tripped": 0, "stale": 0}
    async with session_factory() as db:
        bots = list((await db.execute(
            select(Bot).where(Bot.status == BotStatus.RUNNING)
        )).scalars().all())
        bsvc = BotService(db)
        cbsvc = CircuitBreakerService(db)
        for b in bots:
            stats["checked"] += 1
            await bsvc.refresh_pnl_from_engine(b)
            ev = await cbsvc.evaluate_bot_pnl(b)
            if ev is not None:
                stats["breakers_tripped"] += 1
            if b.last_heartbeat_at is not None and b.last_heartbeat_at < stale_cutoff:
                stats["stale"] += 1
                b.last_error = "stale_heartbeat"
        await db.commit()
    return stats
