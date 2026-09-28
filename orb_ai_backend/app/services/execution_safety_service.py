"""ORB AI 2.0 — Milestone 8: Execution Safety service.

Two-layer defence-in-depth for order flow:

1. **API gateway layer** — the global HTTP `RateLimitMiddleware` remains in
   place. This service handles the *trading* layer.
2. **Trading engine layer** — called from `OrderManager.place()` (and any
   other order-submission path) *just before* an order is handed to the
   broker. This is the last line of defence.

Enforcement (in order):
    1. Global kill-switch check          → KILL_SWITCH_ACTIVATED
    2. Duplicate detection               → DUPLICATE_BLOCKED (or queue)
    3. Per-user trades-per-second        → REJECTED / QUEUED
    4. Per-user orders-per-minute        → REJECTED / QUEUED
    5. Per-user orders-per-hour          → REJECTED / QUEUED
    6. Global orders-per-second          → REJECTED
    7. Global orders-per-minute          → REJECTED
    8. Bot auto-pause on repeat breaches → BOT_PAUSED (side-effect)

Counters are held in-memory (per-process). For multi-worker deployments,
Redis is used transparently if `REDIS_ENABLED=true`.

Every decision (including ALLOWED) is optionally logged to
`execution_safety_events` — the admin monitoring dashboard reads from
there. Configuration is stored in `execution_safety_settings` (singleton
row keyed by ``id='global'``) and hot-reloaded on every check.
"""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Deque, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.execution_safety import (
    ExecutionLimitType,
    ExecutionSafetyAction,
    ExecutionSafetyConfigAudit,
    ExecutionSafetyEvent,
    ExecutionSafetySetting,
)
from app.services.execution_safety_backend import (
    GLOBAL_OPS,
    GLOBAL_OPM,
    USER_BREACH,
    USER_OPH,
    USER_OPM,
    USER_TPS,
    BOT_BREACH,
    get_redis_counters,
)

logger = get_logger(__name__)

GLOBAL_SETTINGS_ID = "global"


class ExecutionRejected(Exception):
    """Raised when the safety layer rejects an order.

    The `code` attribute is a stable identifier for API consumers.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        limit_type: ExecutionLimitType,
        retry_after: Optional[float] = None,
        current: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.limit_type = limit_type
        self.retry_after = retry_after
        self.current = current
        self.limit = limit


# ---- data-classes for decisions ----------------------------------------

@dataclass(slots=True)
class SafetyContext:
    user_id: str
    bot_id: Optional[str] = None
    strategy_id: Optional[str] = None
    engine_session_id: Optional[str] = None
    broker: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    order_type: Optional[str] = None
    endpoint: Optional[str] = None

    def fingerprint(self) -> str:
        """Deterministic duplicate-order fingerprint."""
        raw = (
            f"{self.user_id}|{self.bot_id or ''}|{self.broker or ''}|"
            f"{self.symbol or ''}|{self.side or ''}|{self.order_type or ''}|"
            f"{self.quantity or ''}|{self.price or ''}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass(slots=True)
class SafetyDecision:
    action: ExecutionSafetyAction
    limit_type: Optional[ExecutionLimitType] = None
    reason: Optional[str] = None
    retry_after: Optional[float] = None
    current_counter: Optional[int] = None
    configured_limit: Optional[int] = None


@dataclass
class _EffectiveConfig:
    """Snapshot of the current settings — safe defaults → env → DB row."""

    trades_per_second: int
    orders_per_minute: int
    orders_per_hour: int
    global_orders_per_second: int
    global_orders_per_minute: int
    duplicate_window_seconds: float
    duplicate_action: str
    queue_enabled: bool
    queue_max_size: int
    queue_timeout_seconds: float
    auto_pause_enabled: bool
    auto_pause_violations: int
    auto_pause_window_seconds: int
    kill_switch_active: bool
    kill_switch_reason: Optional[str]
    kill_switch_activated_by: Optional[str]
    kill_switch_activated_at: Optional[datetime]
    event_retention_days: int

    @classmethod
    def from_env(cls) -> "_EffectiveConfig":
        return cls(
            trades_per_second=settings.EXEC_TRADES_PER_SECOND,
            orders_per_minute=settings.EXEC_ORDERS_PER_MINUTE,
            orders_per_hour=settings.EXEC_ORDERS_PER_HOUR,
            global_orders_per_second=settings.EXEC_GLOBAL_ORDERS_PER_SECOND,
            global_orders_per_minute=settings.EXEC_GLOBAL_ORDERS_PER_MINUTE,
            duplicate_window_seconds=settings.EXEC_DUPLICATE_WINDOW_SECONDS,
            duplicate_action=settings.EXEC_DUPLICATE_ACTION,
            queue_enabled=settings.EXEC_QUEUE_ENABLED,
            queue_max_size=settings.EXEC_QUEUE_MAX_SIZE,
            queue_timeout_seconds=settings.EXEC_QUEUE_TIMEOUT_SECONDS,
            auto_pause_enabled=settings.EXEC_AUTO_PAUSE_ENABLED,
            auto_pause_violations=settings.EXEC_AUTO_PAUSE_VIOLATIONS,
            auto_pause_window_seconds=settings.EXEC_AUTO_PAUSE_WINDOW_SECONDS,
            kill_switch_active=False,
            kill_switch_reason=None,
            kill_switch_activated_by=None,
            kill_switch_activated_at=None,
            event_retention_days=settings.EXEC_EVENT_RETENTION_DAYS,
        )

    @classmethod
    def from_row(cls, row: ExecutionSafetySetting) -> "_EffectiveConfig":
        return cls(
            trades_per_second=int(row.trades_per_second),
            orders_per_minute=int(row.orders_per_minute),
            orders_per_hour=int(row.orders_per_hour),
            global_orders_per_second=int(row.global_orders_per_second),
            global_orders_per_minute=int(row.global_orders_per_minute),
            duplicate_window_seconds=float(row.duplicate_window_seconds),
            duplicate_action=str(row.duplicate_action),
            queue_enabled=bool(row.queue_enabled),
            queue_max_size=int(row.queue_max_size),
            queue_timeout_seconds=float(row.queue_timeout_seconds),
            auto_pause_enabled=bool(row.auto_pause_enabled),
            auto_pause_violations=int(row.auto_pause_violations),
            auto_pause_window_seconds=int(row.auto_pause_window_seconds),
            kill_switch_active=bool(row.kill_switch_active),
            kill_switch_reason=row.kill_switch_reason,
            kill_switch_activated_by=row.kill_switch_activated_by,
            kill_switch_activated_at=row.kill_switch_activated_at,
            event_retention_days=int(row.event_retention_days),
        )


# ---- process-scoped counter store --------------------------------------

@dataclass
class _CounterStore:
    """Sliding-window counters and duplicate fingerprints.

    All timestamps are monotonic seconds. Trimmed on read so memory
    stays bounded in the presence of low-volume users.
    """

    per_user_trade_ts: dict[str, Deque[float]] = field(default_factory=lambda: defaultdict(deque))
    per_user_minute_ts: dict[str, Deque[float]] = field(default_factory=lambda: defaultdict(deque))
    per_user_hour_ts: dict[str, Deque[float]] = field(default_factory=lambda: defaultdict(deque))
    global_second_ts: Deque[float] = field(default_factory=deque)
    global_minute_ts: Deque[float] = field(default_factory=deque)
    # fingerprint → last_seen_monotonic
    duplicate_seen: dict[str, float] = field(default_factory=dict)
    # per-user rolling breach timestamps for auto-pause
    per_user_breach_ts: dict[str, Deque[float]] = field(default_factory=lambda: defaultdict(deque))
    # per-bot rolling breach timestamps for auto-pause
    per_bot_breach_ts: dict[str, Deque[float]] = field(default_factory=lambda: defaultdict(deque))
    lock: RLock = field(default_factory=RLock)

    @staticmethod
    def _trim(dq: Deque[float], older_than: float) -> None:
        while dq and dq[0] < older_than:
            dq.popleft()


_STORE = _CounterStore()


# ---- in-memory queue ---------------------------------------------------

@dataclass(slots=True)
class QueuedOrder:
    enqueued_at: float
    expires_at: float
    context: SafetyContext
    payload: dict[str, Any]


class _OrderQueue:
    """Bounded FIFO queue for orders that exceeded a soft limit.

    The Trading Engine polls this via ``pop_ready()`` when its next tick
    fires. Orders that exceed ``queue_timeout_seconds`` are dropped and
    logged as REJECTED with reason=queue_expired.
    """

    def __init__(self) -> None:
        self._dq: Deque[QueuedOrder] = deque()
        self._lock = RLock()

    def size(self) -> int:
        with self._lock:
            return len(self._dq)

    def enqueue(self, item: QueuedOrder, *, max_size: int) -> bool:
        with self._lock:
            if len(self._dq) >= max_size:
                return False
            self._dq.append(item)
            return True

    def expire_stale(self, now: float) -> list[QueuedOrder]:
        with self._lock:
            expired: list[QueuedOrder] = []
            while self._dq and self._dq[0].expires_at <= now:
                expired.append(self._dq.popleft())
            return expired

    def peek_all(self) -> list[QueuedOrder]:
        with self._lock:
            return list(self._dq)


_QUEUE = _OrderQueue()


# ---- the service --------------------------------------------------------

class ExecutionSafetyService:
    """Central execution-safety enforcer.

    Instantiated per request / per engine tick with an ``AsyncSession``.
    Reads config from the singleton row every call so admin edits take
    effect immediately.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._cfg: Optional[_EffectiveConfig] = None

    # ---- config ------------------------------------------------------

    async def get_config(self) -> _EffectiveConfig:
        if self._cfg is not None:
            return self._cfg
        row = await self.session.get(ExecutionSafetySetting, GLOBAL_SETTINGS_ID)
        if row is None:
            self._cfg = _EffectiveConfig.from_env()
        else:
            self._cfg = _EffectiveConfig.from_row(row)
        return self._cfg

    async def ensure_settings_row(self) -> ExecutionSafetySetting:
        """Read-or-create the singleton settings row. Idempotent."""
        row = await self.session.get(ExecutionSafetySetting, GLOBAL_SETTINGS_ID)
        if row is not None:
            return row
        env_cfg = _EffectiveConfig.from_env()
        row = ExecutionSafetySetting(
            id=GLOBAL_SETTINGS_ID,
            trades_per_second=env_cfg.trades_per_second,
            orders_per_minute=env_cfg.orders_per_minute,
            orders_per_hour=env_cfg.orders_per_hour,
            global_orders_per_second=env_cfg.global_orders_per_second,
            global_orders_per_minute=env_cfg.global_orders_per_minute,
            duplicate_window_seconds=env_cfg.duplicate_window_seconds,
            duplicate_action=env_cfg.duplicate_action,
            queue_enabled=env_cfg.queue_enabled,
            queue_max_size=env_cfg.queue_max_size,
            queue_timeout_seconds=env_cfg.queue_timeout_seconds,
            auto_pause_enabled=env_cfg.auto_pause_enabled,
            auto_pause_violations=env_cfg.auto_pause_violations,
            auto_pause_window_seconds=env_cfg.auto_pause_window_seconds,
            event_retention_days=env_cfg.event_retention_days,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def update_settings(
        self,
        *,
        admin_user_id: Optional[str],
        changes: dict[str, Any],
        reason: Optional[str] = None,
    ) -> ExecutionSafetySetting:
        """Update the singleton row and record an audit trail per field."""
        row = await self.ensure_settings_row()
        for field_name, new_value in changes.items():
            if not hasattr(row, field_name):
                continue
            prev = getattr(row, field_name)
            if prev == new_value:
                continue
            setattr(row, field_name, new_value)
            self.session.add(ExecutionSafetyConfigAudit(
                admin_user_id=admin_user_id,
                field=field_name,
                previous_value=str(prev),
                new_value=str(new_value),
                reason=reason,
            ))
        await self.session.flush()
        # Bust the per-instance cache so subsequent checks use new values
        self._cfg = None
        return row

    # ---- kill switch -------------------------------------------------

    async def activate_kill_switch(
        self, *, admin_user_id: Optional[str], reason: str,
    ) -> ExecutionSafetySetting:
        row = await self.ensure_settings_row()
        row.kill_switch_active = True
        row.kill_switch_reason = reason
        row.kill_switch_activated_by = admin_user_id
        row.kill_switch_activated_at = datetime.now(timezone.utc)
        self.session.add(ExecutionSafetyConfigAudit(
            admin_user_id=admin_user_id,
            field="kill_switch_active",
            previous_value="false",
            new_value="true",
            reason=reason,
        ))
        await self.session.flush()
        self._cfg = None
        # M9 follow-up: live push to every admin dashboard
        try:
            from app.ws.risk_broadcaster import risk_broadcaster
            await risk_broadcaster.publish_admin({
                "event": "kill_switch",
                "active": True,
                "reason": reason,
                "activated_by": admin_user_id,
            })
        except Exception:  # pragma: no cover
            logger.exception("kill_switch_broadcast_failed")
        return row

    async def record_emergency_flatten(
        self, *, results: list[dict], reason: Optional[str] = None,
    ) -> str:
        """Persist the kill-switch emergency-flatten outcome on the singleton
        settings row (existing ``extra`` JSON column — no schema change).

        Aggregate status:
        - ``no_live_sessions`` — nothing was running to flatten (we do NOT
          claim the account is flat, since we cannot know broker state here).
        - ``failed``  — at least one session failed to flatten.
        - ``partial`` — some sessions flattened, others partial/failed.
        - ``flat``    — every live session was verified flat.
        """
        row = await self.ensure_settings_row()
        statuses = [str(r.get("status")) for r in results]
        if not results:
            agg = "no_live_sessions"
        elif all(s == "flat" for s in statuses):
            agg = "flat"
        elif any(s == "failed" for s in statuses):
            agg = "failed"
        else:
            agg = "partial"
        row.extra = {
            **(dict(row.extra) if row.extra else {}),
            "kill_switch_flatten": {
                "status": agg,
                "reason": reason,
                "at": datetime.now(timezone.utc).isoformat(),
                "sessions": results,
            },
        }
        await self.session.flush()
        self._cfg = None
        return agg

    async def deactivate_kill_switch(
        self, *, admin_user_id: Optional[str], reason: Optional[str] = None,
    ) -> ExecutionSafetySetting:
        row = await self.ensure_settings_row()
        if not row.kill_switch_active:
            return row
        row.kill_switch_active = False
        row.kill_switch_reason = None
        row.kill_switch_activated_by = None
        row.kill_switch_activated_at = None
        self.session.add(ExecutionSafetyConfigAudit(
            admin_user_id=admin_user_id,
            field="kill_switch_active",
            previous_value="true",
            new_value="false",
            reason=reason,
        ))
        await self.session.flush()
        self._cfg = None
        try:
            from app.ws.risk_broadcaster import risk_broadcaster
            await risk_broadcaster.publish_admin({
                "event": "kill_switch",
                "active": False,
                "reason": reason,
                "deactivated_by": admin_user_id,
            })
        except Exception:  # pragma: no cover
            logger.exception("kill_switch_broadcast_failed")
        return row

    # ---- core check --------------------------------------------------

    async def check_order(
        self,
        ctx: SafetyContext,
        *,
        persist_event: bool = True,
    ) -> SafetyDecision:
        """Run the full safety gauntlet.

        Returns a :class:`SafetyDecision`. Never raises. Callers should:
          - proceed if ``decision.action == ALLOWED`` or ``QUEUED``
          - block if ``decision.action in {REJECTED, DUPLICATE_BLOCKED,
                        KILL_SWITCH_ACTIVATED}``

        When ``settings.REDIS_ENABLED=true`` the counters + duplicate
        fingerprints are backed by Redis so multiple uvicorn workers
        share state. On any Redis error the method falls through to the
        legacy in-process store — behaviour is unchanged.
        """
        cfg = await self.get_config()
        now = time.monotonic()

        # 1. Kill switch — hard stop for every non-admin flow
        if cfg.kill_switch_active:
            decision = SafetyDecision(
                action=ExecutionSafetyAction.KILL_SWITCH_ACTIVATED,
                limit_type=ExecutionLimitType.KILL_SWITCH,
                reason=cfg.kill_switch_reason or "Global kill-switch is active",
            )
            if persist_event:
                await self._log_event(ctx, decision)
            # M9 follow-up: real-time push
            await self._broadcast_safety(ctx, decision)
            return decision

        # 2. Duplicate detection (Redis first, in-memory fallback)
        fp = ctx.fingerprint()
        rc = get_redis_counters()
        dup: Optional[bool] = None
        if rc.enabled:
            dup = await rc.duplicate_seen(fp, cfg.duplicate_window_seconds)
        if dup is None:
            with _STORE.lock:
                last = _STORE.duplicate_seen.get(fp)
                if last is not None and (now - last) < cfg.duplicate_window_seconds:
                    dup = True
                else:
                    dup = False
                    _STORE.duplicate_seen[fp] = now
                    stale = [k for k, t in _STORE.duplicate_seen.items()
                             if (now - t) > (cfg.duplicate_window_seconds * 10.0)]
                    for k in stale:
                        del _STORE.duplicate_seen[k]
        if dup:
            if cfg.duplicate_action == "queue" and cfg.queue_enabled:
                if self._enqueue(now, ctx, cfg):
                    decision = SafetyDecision(
                        action=ExecutionSafetyAction.QUEUED,
                        limit_type=ExecutionLimitType.DUPLICATE_ORDER,
                        reason="Duplicate order queued",
                    )
                    if persist_event:
                        await self._log_event(ctx, decision, fingerprint=fp)
                    await self._broadcast_safety(ctx, decision)
                    return decision
            decision = SafetyDecision(
                action=ExecutionSafetyAction.DUPLICATE_BLOCKED,
                limit_type=ExecutionLimitType.DUPLICATE_ORDER,
                reason="Duplicate order within {}s window".format(cfg.duplicate_window_seconds),
            )
            await self._record_breach(ctx, now, cfg)
            if persist_event:
                await self._log_event(ctx, decision, fingerprint=fp)
            await self._broadcast_safety(ctx, decision)
            return decision

        # 3-7. Rate windows (Redis first with per-key trim, else in-memory)
        checks: list[tuple[str, str, float, int, ExecutionLimitType, str, bool]] = [
            # (key, in-memory bucket, window_seconds, limit, limit_type, reason, per_user)
            (USER_TPS.format(uid=ctx.user_id), "per_user_trade_ts", 1.0,
             cfg.trades_per_second, ExecutionLimitType.TRADES_PER_SECOND,
             f"Trades/sec limit {cfg.trades_per_second} exceeded", True),
            (USER_OPM.format(uid=ctx.user_id), "per_user_minute_ts", 60.0,
             cfg.orders_per_minute, ExecutionLimitType.ORDERS_PER_MINUTE,
             f"Orders/min limit {cfg.orders_per_minute} exceeded", True),
            (USER_OPH.format(uid=ctx.user_id), "per_user_hour_ts", 3600.0,
             cfg.orders_per_hour, ExecutionLimitType.ORDERS_PER_HOUR,
             f"Orders/hour limit {cfg.orders_per_hour} exceeded", True),
            (GLOBAL_OPS, "global_second_ts", 1.0,
             cfg.global_orders_per_second, ExecutionLimitType.GLOBAL_PLATFORM_LIMIT,
             "Global orders/sec limit exceeded", False),
            (GLOBAL_OPM, "global_minute_ts", 60.0,
             cfg.global_orders_per_minute, ExecutionLimitType.GLOBAL_PLATFORM_LIMIT,
             "Global orders/min limit exceeded", False),
        ]

        for redis_key, mem_bucket, window_s, limit_n, limit_type, reason, per_user in checks:
            count = -1
            retry_after: Optional[float] = None
            if rc.enabled:
                count = await rc.trim_and_count(redis_key, window_s)
                if count >= 0 and count >= limit_n:
                    oldest = await rc.oldest_score(redis_key)
                    if oldest is not None:
                        retry_after = max(0.0, window_s - (time.time() - oldest))
            if count < 0:
                # Fallback: in-memory count
                with _STORE.lock:
                    if per_user:
                        dq = getattr(_STORE, mem_bucket)[ctx.user_id]
                    else:
                        dq = getattr(_STORE, mem_bucket)
                    _CounterStore._trim(dq, now - window_s)
                    count = len(dq)
                    if count >= limit_n:
                        retry_after = max(0.0, window_s - (now - dq[0]))
            if count >= limit_n:
                d = SafetyDecision(
                    action=ExecutionSafetyAction.REJECTED,
                    limit_type=limit_type,
                    reason=reason,
                    retry_after=(round(retry_after, 3) if retry_after is not None else None),
                    current_counter=count,
                    configured_limit=limit_n,
                )
                return await self._reject_or_queue(ctx, d, cfg, now, persist_event)

        # All checks passed — commit counters (Redis first + always mirror
        # to in-memory so tests using the in-memory path still see counts).
        if rc.enabled:
            for redis_key, _, window_s, _, _, _, _ in checks:
                await rc.add(redis_key, window_s)
        with _STORE.lock:
            _STORE.per_user_trade_ts[ctx.user_id].append(now)
            _STORE.per_user_minute_ts[ctx.user_id].append(now)
            _STORE.per_user_hour_ts[ctx.user_id].append(now)
            _STORE.global_second_ts.append(now)
            _STORE.global_minute_ts.append(now)

        decision = SafetyDecision(action=ExecutionSafetyAction.ALLOWED)
        if persist_event:
            await self._log_event(ctx, decision, fingerprint=fp)
        return decision

    # ---- reject-or-queue helper -------------------------------------

    async def _reject_or_queue(
        self,
        ctx: SafetyContext,
        decision: SafetyDecision,
        cfg: _EffectiveConfig,
        now: float,
        persist: bool,
    ) -> SafetyDecision:
        # Queue if enabled AND soft limit (i.e. not a global hard cap that
        # would just get requeued forever). We only queue per-user limits.
        queueable = cfg.queue_enabled and decision.limit_type in {
            ExecutionLimitType.TRADES_PER_SECOND,
            ExecutionLimitType.ORDERS_PER_MINUTE,
            ExecutionLimitType.ORDERS_PER_HOUR,
        }
        if queueable and self._enqueue(now, ctx, cfg):
            decision.action = ExecutionSafetyAction.QUEUED
        # Track breach for auto-pause even if we queued
        await self._record_breach(ctx, now, cfg)
        if persist:
            await self._log_event(ctx, decision)
        # M9 follow-up — live broadcast to admin + user WS clients
        await self._broadcast_safety(ctx, decision)
        return decision

    async def _broadcast_safety(
        self, ctx: SafetyContext, decision: SafetyDecision,
    ) -> None:
        """Best-effort real-time push to WS clients.

        Uses the risk_broadcaster (M9 follow-up). Never blocks — a broken
        broadcaster is logged and ignored.
        """
        try:
            from app.ws.risk_broadcaster import risk_broadcaster
            payload = {
                "event": "execution_safety",
                "user_id": ctx.user_id,
                "bot_id": ctx.bot_id,
                "symbol": ctx.symbol,
                "action": decision.action.value if decision.action else None,
                "limit_type": (decision.limit_type.value
                               if decision.limit_type else None),
                "reason": decision.reason,
                "retry_after": decision.retry_after,
                "current_counter": decision.current_counter,
                "configured_limit": decision.configured_limit,
            }
            await risk_broadcaster.publish_user(ctx.user_id, payload)
            await risk_broadcaster.publish_admin(payload)
        except Exception:  # pragma: no cover
            logger.exception("execution_safety_broadcast_failed")

    def _enqueue(self, now: float, ctx: SafetyContext, cfg: _EffectiveConfig) -> bool:
        return _QUEUE.enqueue(
            QueuedOrder(
                enqueued_at=now,
                expires_at=now + cfg.queue_timeout_seconds,
                context=ctx,
                payload={},
            ),
            max_size=cfg.queue_max_size,
        )

    # ---- auto-pause tracking ----------------------------------------

    async def _record_breach(
        self, ctx: SafetyContext, now: float, cfg: _EffectiveConfig,
    ) -> None:
        if not cfg.auto_pause_enabled:
            return
        with _STORE.lock:
            udq = _STORE.per_user_breach_ts[ctx.user_id]
            _CounterStore._trim(udq, now - cfg.auto_pause_window_seconds)
            udq.append(now)
            triggered_bot = False
            if ctx.bot_id:
                bdq = _STORE.per_bot_breach_ts[ctx.bot_id]
                _CounterStore._trim(bdq, now - cfg.auto_pause_window_seconds)
                bdq.append(now)
                triggered_bot = len(bdq) >= cfg.auto_pause_violations
        if triggered_bot and ctx.bot_id:
            await self._auto_pause_bot(ctx)

    async def _auto_pause_bot(self, ctx: SafetyContext) -> None:
        """Best-effort bot pause. Import lazily to avoid a hard cycle."""
        try:
            from app.models.bot import Bot, BotStatus
            bot = await self.session.get(Bot, ctx.bot_id)
            if bot and bot.status not in (BotStatus.PAUSED, BotStatus.STOPPED, BotStatus.KILLED):
                bot.status = BotStatus.PAUSED
                bot.last_error = "Auto-paused: repeated execution-safety breaches"
                await self.session.flush()
                logger.warning(
                    "execution_safety_auto_paused_bot",
                    extra={"bot_id": ctx.bot_id, "user_id": ctx.user_id},
                )
                await self._log_event(
                    ctx,
                    SafetyDecision(
                        action=ExecutionSafetyAction.BOT_PAUSED,
                        limit_type=ExecutionLimitType.EMERGENCY_STOP,
                        reason="Bot auto-paused after repeated breaches",
                    ),
                )
                # M9 follow-up: live push
                try:
                    from app.ws.risk_broadcaster import risk_broadcaster
                    await risk_broadcaster.publish(ctx.user_id, {
                        "event": "bot_auto_paused",
                        "user_id": ctx.user_id,
                        "bot_id": ctx.bot_id,
                        "reason": "Auto-paused: repeated execution-safety breaches",
                    }, also_admin=True)
                except Exception:  # pragma: no cover
                    logger.exception("bot_auto_pause_broadcast_failed")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("auto_pause_bot_failed", extra={"err": str(exc)})

    # ---- event logging ----------------------------------------------

    async def _log_event(
        self,
        ctx: SafetyContext,
        decision: SafetyDecision,
        *,
        fingerprint: Optional[str] = None,
    ) -> None:
        try:
            evt = ExecutionSafetyEvent(
                user_id=ctx.user_id,
                bot_id=ctx.bot_id,
                strategy_id=ctx.strategy_id,
                engine_session_id=ctx.engine_session_id,
                broker=ctx.broker,
                symbol=ctx.symbol,
                order_type=ctx.order_type,
                endpoint=ctx.endpoint,
                limit_type=decision.limit_type or ExecutionLimitType.GLOBAL_PLATFORM_LIMIT,
                action=decision.action,
                current_counter=decision.current_counter,
                configured_limit=decision.configured_limit,
                retry_after_seconds=decision.retry_after,
                fingerprint=fingerprint,
                reason=decision.reason,
            )
            self.session.add(evt)
            await self.session.flush()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("execution_safety_log_failed", extra={"err": str(exc)})

    # ---- admin dashboard queries -------------------------------------

    async def list_events(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        symbol: Optional[str] = None,
        limit_type: Optional[ExecutionLimitType] = None,
        action: Optional[ExecutionSafetyAction] = None,
        since: Optional[datetime] = None,
    ) -> list[ExecutionSafetyEvent]:
        stmt = select(ExecutionSafetyEvent)
        if user_id:
            stmt = stmt.where(ExecutionSafetyEvent.user_id == user_id)
        if bot_id:
            stmt = stmt.where(ExecutionSafetyEvent.bot_id == bot_id)
        if symbol:
            stmt = stmt.where(ExecutionSafetyEvent.symbol == symbol)
        if limit_type is not None:
            stmt = stmt.where(ExecutionSafetyEvent.limit_type == limit_type)
        if action is not None:
            stmt = stmt.where(ExecutionSafetyEvent.action == action)
        if since is not None:
            stmt = stmt.where(ExecutionSafetyEvent.created_at >= since)
        stmt = stmt.order_by(ExecutionSafetyEvent.created_at.desc()).offset(offset).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def prune_old_events(self) -> int:
        cfg = await self.get_config()
        cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.event_retention_days)
        from sqlalchemy import delete
        res = await self.session.execute(
            delete(ExecutionSafetyEvent).where(ExecutionSafetyEvent.created_at < cutoff)
        )
        return int(res.rowcount or 0)

    # ---- process-level introspection --------------------------------

    @staticmethod
    def queue_size() -> int:
        return _QUEUE.size()

    @staticmethod
    def reset_state_for_tests() -> None:
        """Clear all in-memory counters. Intended for tests only.

        Also flushes the Redis ``exec_safety:*`` namespace when
        ``REDIS_ENABLED=true``, so tests see a clean slate on both
        backends.
        """
        with _STORE.lock:
            _STORE.per_user_trade_ts.clear()
            _STORE.per_user_minute_ts.clear()
            _STORE.per_user_hour_ts.clear()
            _STORE.global_second_ts.clear()
            _STORE.global_minute_ts.clear()
            _STORE.duplicate_seen.clear()
            _STORE.per_user_breach_ts.clear()
            _STORE.per_bot_breach_ts.clear()
        with _QUEUE._lock:
            _QUEUE._dq.clear()
        # Best-effort Redis flush (only touched when the shim is active).
        if settings.REDIS_ENABLED:
            try:  # pragma: no cover - side effect only
                import asyncio
                rc = get_redis_counters()
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(rc.flush_namespace())
                    else:
                        loop.run_until_complete(rc.flush_namespace())
                except RuntimeError:
                    # No running loop — nothing to do
                    pass
            except Exception:
                pass
