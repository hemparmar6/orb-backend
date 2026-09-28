"""ORB AI 2.0 — Milestone 9: Risk Management service.

Central risk evaluator for the trading engine + user + admin controls.

Responsibilities
================
1. **Order-time enforcement** — ``check_order(ctx)`` is called from
   ``OrderManager.place()`` *before* ``ExecutionSafetyService.check_order``.
   It evaluates every rule from the M9 spec:
       - Daily Loss Limit
       - Daily Profit Target (optional halt)
       - Maximum Trades Per Day
       - Maximum Consecutive Losses
       - Maximum Capital Allocation
       - Maximum Position Size
       - Maximum Open Positions
       - Maximum Exposure Per Symbol
       - Trading Session Hours
       - live_trading_enabled + force_paper_mode

2. **Breach recording** — every non-ALLOWED decision is written to
   ``risk_breaches`` (one row) and, when severity >= user preference,
   dispatched through ``NotificationService`` (in-app + email + push).

3. **Batch bot controls** — pause_all, resume_all, disable_live_trading,
   return_to_paper_trading.

4. **Portfolio dashboard** — combines existing ``RiskService`` analytics
   with M9 breach history + limits + status flags.

5. **Retention** — ``prune_old_breaches(days)`` called by the scheduler.

Design notes
------------
* One evaluator per request; instantiated with an ``AsyncSession``.
* Never raises; always returns a decision, so callers can decide.
* Persists BREACHES only. ALLOWED decisions are not logged (unlike M8's
  ExecutionSafety events, which are exhaustive for rate observability).
  This keeps risk_breaches focused on the audit-relevant subset.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.bot import Bot, BotStatus
from app.models.engine import (
    EngineSession,
    PaperOrder,
    PaperPosition,
    PaperTrade,
    OrderStatus,
)
from app.models.notification import NotificationEvent, NotificationSeverity
from app.models.risk_management import (
    RiskAction,
    RiskBreach,
    RiskConfigActorType,
    RiskEventType,
    RiskLimit,
    RiskLimitAudit,
    RiskSeverity,
)
from app.models.user import User
from app.services.notification_service import NotificationService
from app.services.risk_service import RiskService

logger = get_logger(__name__)


# ---- DTOs ---------------------------------------------------------------


@dataclass(slots=True)
class RiskOrderContext:
    """Everything needed to evaluate a proposed order against the user's
    risk profile. Populated by ``OrderManager.place()``.
    """

    user_id: str
    bot_id: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    execution_mode: str = "paper"   # "paper" | "live"
    is_exit: bool = False           # Exits are allowed even when daily-loss reached


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    event_type: Optional[RiskEventType] = None
    severity: RiskSeverity = RiskSeverity.INFO
    reason: Optional[str] = None
    triggered_value: Optional[float] = None
    threshold: Optional[float] = None
    action: RiskAction = RiskAction.ALLOWED


SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


# ---- helpers ------------------------------------------------------------


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":", 1)
    return time(hour=int(hh), minute=int(mm))


def _zoneinfo(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or settings.TRADING_SESSION_TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def _dec_to_float(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


# ---- service ------------------------------------------------------------


class RiskManagementService:
    """Central Milestone 9 evaluator + admin surface."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        # Lazy — created on first read/write of a limit row
        self._limit_cache: dict[str, RiskLimit] = {}

    # ==================================================================
    # LIMIT CRUD
    # ==================================================================

    async def get_limit(self, user_id: str) -> Optional[RiskLimit]:
        if user_id in self._limit_cache:
            return self._limit_cache[user_id]
        row = (await self.session.execute(
            select(RiskLimit).where(RiskLimit.user_id == user_id)
        )).scalar_one_or_none()
        if row is not None:
            self._limit_cache[user_id] = row
        return row

    async def ensure_limit(self, user_id: str) -> RiskLimit:
        row = await self.get_limit(user_id)
        if row is not None:
            return row
        row = RiskLimit(user_id=user_id)
        self.session.add(row)
        await self.session.flush()
        self._limit_cache[user_id] = row
        return row

    async def upsert_limit(
        self, user_id: str, *,
        actor_user_id: Optional[str] = None,
        actor_type: RiskConfigActorType = RiskConfigActorType.USER,
        reason: Optional[str] = None,
        **fields,
    ) -> RiskLimit:
        """Update the user's RiskLimit row.

        Also records one :class:`RiskLimitAudit` row per field that
        actually changed value (Milestone 9 follow-up). ``actor_type``
        defaults to USER for the /me/* endpoints, ADMIN for /admin/*,
        SYSTEM for engine-driven flips (e.g. auto disable-live).
        """
        row = await self.ensure_limit(user_id)
        for k, v in fields.items():
            if v is None:
                continue
            if not hasattr(row, k):
                continue
            prev = getattr(row, k)
            # normalise Decimal → float for comparison + storage in the
            # audit's TEXT columns
            def _norm(x):
                if x is None:
                    return None
                try:
                    from decimal import Decimal as _D
                    if isinstance(x, _D):
                        return float(x)
                except Exception:  # pragma: no cover
                    pass
                return x
            prev_n = _norm(prev)
            new_n = _norm(v)
            if prev_n == new_n:
                continue
            setattr(row, k, v)
            self.session.add(RiskLimitAudit(
                target_user_id=user_id,
                actor_user_id=actor_user_id,
                actor_type=actor_type,
                field=k,
                previous_value=(None if prev is None else str(prev_n)),
                new_value=(None if v is None else str(new_n)),
                reason=reason,
            ))
        await self.session.flush()
        self._limit_cache[user_id] = row
        return row

    async def list_audit(
        self,
        *,
        target_user_id: Optional[str] = None,
        actor_user_id: Optional[str] = None,
        actor_type: Optional[RiskConfigActorType] = None,
        since: Optional[datetime] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[RiskLimitAudit]:
        stmt = select(RiskLimitAudit)
        if target_user_id:
            stmt = stmt.where(RiskLimitAudit.target_user_id == target_user_id)
        if actor_user_id:
            stmt = stmt.where(RiskLimitAudit.actor_user_id == actor_user_id)
        if actor_type is not None:
            stmt = stmt.where(RiskLimitAudit.actor_type == actor_type)
        if since is not None:
            stmt = stmt.where(RiskLimitAudit.created_at >= since)
        stmt = stmt.order_by(RiskLimitAudit.created_at.desc()).offset(offset).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    # ==================================================================
    # ORDER-TIME ENFORCEMENT
    # ==================================================================

    async def check_order(self, ctx: RiskOrderContext) -> RiskDecision:
        """Evaluate the proposed order against every M9 rule.

        Returns a :class:`RiskDecision`. Never raises. Persists a breach
        row on any non-ALLOWED decision. Notification dispatch happens
        inside :meth:`_record_breach` so callers stay simple.
        """
        limit = await self.get_limit(ctx.user_id)

        # 1. live_trading_enabled + force_paper_mode
        if limit is not None:
            if ctx.execution_mode == "live" and not limit.live_trading_enabled:
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.LIVE_TRADING_DISABLED,
                    severity=RiskSeverity.CRITICAL,
                    reason="Live trading is disabled on your risk profile",
                    action=RiskAction.LIVE_DISABLED,
                )
            if ctx.execution_mode == "live" and limit.force_paper_mode:
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.PAPER_MODE_FORCED,
                    severity=RiskSeverity.CRITICAL,
                    reason="Force-paper-mode is active on your risk profile",
                    action=RiskAction.PAPER_FORCED,
                )

        # 2. Trading session window (only if configured on limit or global)
        if not self._within_trading_window(limit):
            return await self._deny(
                ctx,
                event_type=RiskEventType.TRADING_SESSION_HOURS,
                severity=RiskSeverity.WARNING,
                reason="Outside configured trading-session hours",
                action=RiskAction.BLOCKED,
            )

        if limit is None:
            # No user-specific limits → allow (system-wide defaults handled
            # by legacy engine.risk.RiskEngine and ExecutionSafety).
            return RiskDecision(allowed=True)

        # 3. Daily Loss Limit (blocks new entries; exits allowed)
        if limit.daily_loss_limit is not None and not ctx.is_exit:
            day_pnl = await self._user_day_pnl(ctx.user_id)
            threshold = -abs(float(limit.daily_loss_limit))
            if day_pnl <= threshold:
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.DAILY_LOSS_LIMIT,
                    severity=RiskSeverity.CRITICAL,
                    reason=f"Daily loss {day_pnl:.2f} reached limit "
                           f"{threshold:.2f}",
                    triggered_value=day_pnl,
                    threshold=threshold,
                    action=RiskAction.BLOCKED,
                )

        # 4. Daily Profit Target (optional halt of NEW entries)
        if limit.daily_profit_target is not None and not ctx.is_exit:
            day_pnl = await self._user_day_pnl(ctx.user_id)
            tgt = float(limit.daily_profit_target)
            if day_pnl >= tgt:
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.DAILY_PROFIT_TARGET,
                    severity=RiskSeverity.WARNING,
                    reason=f"Daily profit target {tgt:.2f} reached "
                           f"(current {day_pnl:.2f})",
                    triggered_value=day_pnl,
                    threshold=tgt,
                    action=RiskAction.BLOCKED,
                )

        # 5. Max Trades Per Day
        if limit.max_trades_per_day is not None:
            n = await self._user_trades_today(ctx.user_id)
            if n >= int(limit.max_trades_per_day):
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.MAX_TRADES_PER_DAY,
                    severity=RiskSeverity.WARNING,
                    reason=f"Trades today {n} >= limit {limit.max_trades_per_day}",
                    triggered_value=float(n),
                    threshold=float(limit.max_trades_per_day),
                    action=RiskAction.BLOCKED,
                )

        # 6. Max Consecutive Losses (evaluated on the user's aggregate)
        if limit.max_consecutive_losses is not None:
            losses = await self._user_consecutive_losses(ctx.user_id)
            if losses >= int(limit.max_consecutive_losses):
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.MAX_CONSECUTIVE_LOSSES,
                    severity=RiskSeverity.WARNING,
                    reason=f"Consecutive losses {losses} >= "
                           f"{limit.max_consecutive_losses}",
                    triggered_value=float(losses),
                    threshold=float(limit.max_consecutive_losses),
                    action=RiskAction.BLOCKED,
                )

        # 7. Max Position Size (absolute qty for this symbol including
        #    the proposed order)
        if (
            limit.max_position_size is not None
            and ctx.symbol
            and ctx.quantity
        ):
            existing = await self._user_symbol_position_qty(ctx.user_id, ctx.symbol)
            projected = abs(existing) + float(ctx.quantity)
            if projected > float(limit.max_position_size):
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.MAX_POSITION_SIZE,
                    severity=RiskSeverity.WARNING,
                    reason=(
                        f"Projected position {projected} on {ctx.symbol} "
                        f"exceeds max {limit.max_position_size}"
                    ),
                    triggered_value=projected,
                    threshold=float(limit.max_position_size),
                    action=RiskAction.BLOCKED,
                )

        # 8. Max Open Positions
        if limit.max_open_positions is not None:
            n_open = await self._user_open_positions_count(ctx.user_id)
            already_in_symbol = False
            if ctx.symbol:
                already_in_symbol = (
                    await self._user_symbol_position_qty(ctx.user_id, ctx.symbol)
                ) != 0
            if not already_in_symbol and n_open >= int(limit.max_open_positions):
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.MAX_OPEN_POSITIONS,
                    severity=RiskSeverity.WARNING,
                    reason=f"Open positions {n_open} >= "
                           f"{limit.max_open_positions}",
                    triggered_value=float(n_open),
                    threshold=float(limit.max_open_positions),
                    action=RiskAction.BLOCKED,
                )

        # 9. Max Exposure Per Symbol (notional)
        if (
            limit.max_exposure_per_symbol is not None
            and ctx.symbol
            and ctx.price
            and ctx.quantity
        ):
            existing_val = await self._user_symbol_exposure_value(ctx.user_id, ctx.symbol)
            projected_val = existing_val + abs(float(ctx.price) * float(ctx.quantity))
            if projected_val > float(limit.max_exposure_per_symbol):
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.MAX_EXPOSURE_PER_SYMBOL,
                    severity=RiskSeverity.WARNING,
                    reason=(
                        f"Exposure {projected_val:.2f} on {ctx.symbol} "
                        f"exceeds max {float(limit.max_exposure_per_symbol):.2f}"
                    ),
                    triggered_value=projected_val,
                    threshold=float(limit.max_exposure_per_symbol),
                    action=RiskAction.BLOCKED,
                )

        # 10. Max Capital Allocation (total gross exposure across all symbols)
        if limit.max_capital_allocation is not None:
            gross = await self._user_gross_exposure(ctx.user_id)
            proposed_notional = 0.0
            if ctx.price and ctx.quantity:
                proposed_notional = abs(float(ctx.price) * float(ctx.quantity))
            projected = gross + proposed_notional
            if projected > float(limit.max_capital_allocation):
                return await self._deny(
                    ctx,
                    event_type=RiskEventType.MAX_CAPITAL_ALLOCATION,
                    severity=RiskSeverity.WARNING,
                    reason=(
                        f"Total exposure {projected:.2f} would exceed capital "
                        f"allocation {float(limit.max_capital_allocation):.2f}"
                    ),
                    triggered_value=projected,
                    threshold=float(limit.max_capital_allocation),
                    action=RiskAction.BLOCKED,
                )

        return RiskDecision(allowed=True)

    # ==================================================================
    # BATCH BOT CONTROLS
    # ==================================================================

    async def pause_all_bots(self, user_id: str, *, reason: str = "risk_pause_all"
                             ) -> tuple[list[str], int]:
        """Move every RUNNING bot for this user to PAUSED. Non-blocking to
        engine sessions here — the bot loop will honour the status flag
        on its next tick and stop soon after.
        """
        stmt = select(Bot).where(
            Bot.user_id == user_id,
            Bot.status == BotStatus.RUNNING,
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        ids: list[str] = []
        for b in rows:
            b.status = BotStatus.PAUSED
            b.stopped_at = datetime.now(timezone.utc)
            b.last_error = reason
            ids.append(b.id)
        await self.session.flush()
        await self._record_breach(
            user_id=user_id, bot_id=None, symbol=None,
            event_type=RiskEventType.BOTS_PAUSED,
            severity=RiskSeverity.WARNING,
            action=RiskAction.BOTS_PAUSED,
            reason=reason, triggered_value=float(len(ids)), threshold=None,
            payload={"bot_ids": ids},
        )
        return ids, len(ids)

    async def resume_all_bots(self, user_id: str) -> tuple[list[str], int]:
        """Return every PAUSED bot to IDLE so the user can start them."""
        stmt = select(Bot).where(
            Bot.user_id == user_id,
            Bot.status == BotStatus.PAUSED,
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        ids: list[str] = []
        for b in rows:
            b.status = BotStatus.IDLE
            b.last_error = None
            ids.append(b.id)
        await self.session.flush()
        await self._record_breach(
            user_id=user_id, bot_id=None, symbol=None,
            event_type=RiskEventType.BOTS_RESUMED,
            severity=RiskSeverity.INFO,
            action=RiskAction.ALLOWED,
            reason="Bots resumed", triggered_value=float(len(ids)),
            payload={"bot_ids": ids},
        )
        return ids, len(ids)

    async def disable_live_trading(self, user_id: str, *,
                                   reason: str = "risk_disable_live",
                                   actor_user_id: Optional[str] = None,
                                   actor_type: RiskConfigActorType = RiskConfigActorType.USER,
                                   ) -> tuple[list[str], int]:
        """Turn off live trading + revert every RUNNING live bot to paper mode."""
        row = await self.ensure_limit(user_id)
        # Audit the flag flip
        if row.live_trading_enabled is True:
            self.session.add(RiskLimitAudit(
                target_user_id=user_id,
                actor_user_id=actor_user_id,
                actor_type=actor_type,
                field="live_trading_enabled",
                previous_value="True", new_value="False",
                reason=reason,
            ))
        row.live_trading_enabled = False
        stmt = select(Bot).where(
            Bot.user_id == user_id,
            Bot.execution_mode == "live",
            Bot.status.in_([BotStatus.RUNNING, BotStatus.STARTING, BotStatus.PAUSED]),
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        ids: list[str] = []
        for b in rows:
            b.status = BotStatus.PAUSED
            b.stopped_at = datetime.now(timezone.utc)
            b.last_error = reason
            ids.append(b.id)
        await self.session.flush()
        await self._record_breach(
            user_id=user_id, bot_id=None, symbol=None,
            event_type=RiskEventType.LIVE_TRADING_DISABLED,
            severity=RiskSeverity.CRITICAL,
            action=RiskAction.LIVE_DISABLED,
            reason=reason, triggered_value=float(len(ids)),
            payload={"bot_ids": ids},
        )
        return ids, len(ids)

    async def return_to_paper_trading(self, user_id: str, *,
                                      reason: str = "risk_paper_mode",
                                      actor_user_id: Optional[str] = None,
                                      actor_type: RiskConfigActorType = RiskConfigActorType.USER,
                                      ) -> tuple[list[str], int]:
        """Set force_paper_mode and switch every live bot to paper execution."""
        row = await self.ensure_limit(user_id)
        if row.force_paper_mode is False:
            self.session.add(RiskLimitAudit(
                target_user_id=user_id,
                actor_user_id=actor_user_id,
                actor_type=actor_type,
                field="force_paper_mode",
                previous_value="False", new_value="True",
                reason=reason,
            ))
        row.force_paper_mode = True
        stmt = select(Bot).where(
            Bot.user_id == user_id,
            Bot.execution_mode == "live",
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        ids: list[str] = []
        for b in rows:
            b.execution_mode = "paper"
            b.last_error = reason
            ids.append(b.id)
        await self.session.flush()
        await self._record_breach(
            user_id=user_id, bot_id=None, symbol=None,
            event_type=RiskEventType.PAPER_MODE_FORCED,
            severity=RiskSeverity.CRITICAL,
            action=RiskAction.PAPER_FORCED,
            reason=reason, triggered_value=float(len(ids)),
            payload={"bot_ids": ids},
        )
        return ids, len(ids)

    # ==================================================================
    # PORTFOLIO DASHBOARD
    # ==================================================================

    async def portfolio_snapshot(self, user_id: str) -> dict[str, Any]:
        """Combine legacy analytics with M9 limits + status."""
        risk = RiskService(self.session)
        limit = await self.get_limit(user_id)
        active = await self.list_breaches(user_id, unresolved_only=True, limit=25)
        n_open = await self._user_open_positions_count(user_id)
        losses = await self._user_consecutive_losses(user_id)
        return {
            "limits": limit,
            "exposure_by_symbol": await risk.exposure_by_symbol(user_id),
            "exposure_by_broker": await risk.exposure_by_broker(user_id),
            "daily": await risk.daily_risk(user_id),
            "drawdown": await risk.max_drawdown(user_id),
            "margin": await risk.margin_utilisation(user_id),
            "position_sizing": await risk.position_sizing(user_id),
            "open_positions": n_open,
            "consecutive_losses": losses,
            "live_trading_enabled": bool(limit.live_trading_enabled) if limit else True,
            "force_paper_mode": bool(limit.force_paper_mode) if limit else False,
            "active_breaches": active,
        }

    # ==================================================================
    # BREACH LOG
    # ==================================================================

    async def list_breaches(
        self,
        user_id: Optional[str] = None,
        *,
        unresolved_only: bool = False,
        event_type: Optional[RiskEventType] = None,
        severity: Optional[RiskSeverity] = None,
        since: Optional[datetime] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[RiskBreach]:
        stmt = select(RiskBreach)
        if user_id:
            stmt = stmt.where(RiskBreach.user_id == user_id)
        if unresolved_only:
            stmt = stmt.where(RiskBreach.resolved_at.is_(None))
        if event_type is not None:
            stmt = stmt.where(RiskBreach.event_type == event_type)
        if severity is not None:
            stmt = stmt.where(RiskBreach.severity == severity)
        if since is not None:
            stmt = stmt.where(RiskBreach.created_at >= since)
        stmt = stmt.order_by(RiskBreach.created_at.desc()).offset(offset).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def resolve_breach(self, breach_id: str, *, admin_user_id: Optional[str]
                             ) -> Optional[RiskBreach]:
        row = await self.session.get(RiskBreach, breach_id)
        if row is None:
            return None
        row.resolved_at = datetime.now(timezone.utc)
        row.resolved_by = admin_user_id
        await self.session.flush()
        return row

    async def prune_old_breaches(self, retention_days: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        from sqlalchemy import delete
        res = await self.session.execute(
            delete(RiskBreach).where(RiskBreach.created_at < cutoff)
        )
        return int(res.rowcount or 0)

    # ==================================================================
    # ADMIN OVERVIEW
    # ==================================================================

    async def admin_overview(self, since_minutes: int = 60 * 24
                             ) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

        total = int((await self.session.execute(
            select(func.count(RiskBreach.id)).where(RiskBreach.created_at >= since)
        )).scalar_one())

        async def _grp(col, top: int = 10) -> list[dict[str, Any]]:
            rows = (await self.session.execute(
                select(col, func.count(RiskBreach.id))
                .where(RiskBreach.created_at >= since)
                .where(col.is_not(None))
                .group_by(col)
                .order_by(func.count(RiskBreach.id).desc())
                .limit(top)
            )).all()
            return [
                {"label": (v.value if hasattr(v, "value") else str(v)), "count": int(n)}
                for v, n in rows if v is not None
            ]

        users_live_off = int((await self.session.execute(
            select(func.count(RiskLimit.id))
            .where(RiskLimit.live_trading_enabled.is_(False))
        )).scalar_one())
        users_forced_paper = int((await self.session.execute(
            select(func.count(RiskLimit.id))
            .where(RiskLimit.force_paper_mode.is_(True))
        )).scalar_one())

        return {
            "since": since,
            "total_breaches": total,
            "by_event_type": await _grp(RiskBreach.event_type),
            "by_severity": await _grp(RiskBreach.severity),
            "top_users": await _grp(RiskBreach.user_id),
            "top_symbols": await _grp(RiskBreach.symbol),
            "users_with_live_disabled": users_live_off,
            "users_forced_to_paper": users_forced_paper,
        }

    # ==================================================================
    # INTERNAL — breach + notification
    # ==================================================================

    async def _deny(
        self,
        ctx: RiskOrderContext,
        *,
        event_type: RiskEventType,
        severity: RiskSeverity,
        reason: str,
        triggered_value: Optional[float] = None,
        threshold: Optional[float] = None,
        action: RiskAction = RiskAction.BLOCKED,
    ) -> RiskDecision:
        await self._record_breach(
            user_id=ctx.user_id, bot_id=ctx.bot_id, symbol=ctx.symbol,
            event_type=event_type, severity=severity, action=action,
            reason=reason, triggered_value=triggered_value, threshold=threshold,
            payload={
                "side": ctx.side, "quantity": ctx.quantity, "price": ctx.price,
                "execution_mode": ctx.execution_mode,
            },
        )
        return RiskDecision(
            allowed=False, event_type=event_type, severity=severity,
            reason=reason, triggered_value=triggered_value,
            threshold=threshold, action=action,
        )

    async def _record_breach(
        self,
        *,
        user_id: str,
        bot_id: Optional[str],
        symbol: Optional[str],
        event_type: RiskEventType,
        severity: RiskSeverity,
        action: RiskAction,
        reason: str,
        triggered_value: Optional[float] = None,
        threshold: Optional[float] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> RiskBreach:
        breach = RiskBreach(
            user_id=user_id, bot_id=bot_id, symbol=symbol,
            event_type=event_type, severity=severity, action_taken=action,
            reason=reason, triggered_value=triggered_value, threshold=threshold,
            payload=payload or {},
        )
        self.session.add(breach)
        await self.session.flush()
        # Best-effort notification (never break the risk decision)
        try:
            await self._dispatch_notification(breach)
        except Exception:  # pragma: no cover
            logger.exception("risk_notification_dispatch_failed")
        # M9 follow-up — live WebSocket push
        try:
            from app.ws.risk_broadcaster import risk_broadcaster
            ws_payload = {
                "event": "risk_breach",
                "breach_id": breach.id,
                "user_id": breach.user_id,
                "bot_id": breach.bot_id,
                "symbol": breach.symbol,
                "event_type": breach.event_type.value,
                "severity": breach.severity.value,
                "action_taken": breach.action_taken.value,
                "reason": breach.reason,
                "triggered_value": (float(breach.triggered_value)
                                    if breach.triggered_value is not None else None),
                "threshold": (float(breach.threshold)
                              if breach.threshold is not None else None),
                "created_at": breach.created_at.isoformat()
                              if breach.created_at else None,
            }
            await risk_broadcaster.publish(user_id, ws_payload, also_admin=True)
        except Exception:  # pragma: no cover
            logger.exception("risk_breach_broadcast_failed")
        return breach

    async def _dispatch_notification(self, breach: RiskBreach) -> None:
        user = await self.session.get(User, breach.user_id)
        if user is None:
            return
        limit = await self.get_limit(breach.user_id)
        min_sev = (limit.notify_severity_min if limit else "warning") or "warning"
        if SEVERITY_ORDER.get(breach.severity.value, 0) < SEVERITY_ORDER.get(min_sev, 0):
            return

        notif_event = _EVENT_TO_NOTIFICATION.get(
            breach.event_type, NotificationEvent.SYSTEM_ALERT,
        )
        notif_severity = _RISK_TO_NOTIFICATION_SEVERITY[breach.severity]
        title = _RISK_TITLES.get(breach.event_type, "Risk alert")
        body = breach.reason

        notif = await NotificationService(self.session).notify(
            user=user, event=notif_event, title=title, body=body,
            severity=notif_severity,
            payload={
                "risk_event": breach.event_type.value,
                "breach_id": breach.id,
                "triggered_value": (float(breach.triggered_value)
                                    if breach.triggered_value is not None else None),
                "threshold": (float(breach.threshold)
                              if breach.threshold is not None else None),
                "symbol": breach.symbol, "bot_id": breach.bot_id,
                "action": breach.action_taken.value,
            },
        )
        breach.notification_id = notif.id
        await self.session.flush()

    # ==================================================================
    # INTERNAL — data helpers
    # ==================================================================

    def _within_trading_window(self, limit: Optional[RiskLimit]) -> bool:
        # If no limit row or no window configured, defer to the global env
        # window (matches legacy engine.risk.RiskEngine behaviour).
        start_s = (
            limit.trading_session_start if limit else None
        ) or settings.TRADING_SESSION_START
        end_s = (
            limit.trading_session_end if limit else None
        ) or settings.TRADING_SESSION_END
        tz_name = (
            limit.trading_session_timezone if limit else None
        ) or settings.TRADING_SESSION_TIMEZONE
        if not start_s or not end_s:
            return True
        try:
            start_t = _parse_hhmm(start_s)
            end_t = _parse_hhmm(end_s)
        except ValueError:
            return True
        now = datetime.now(_zoneinfo(tz_name)).time()
        if start_t <= end_t:
            return start_t <= now <= end_t
        # wraps midnight
        return now >= start_t or now <= end_t

    async def _user_day_pnl(self, user_id: str) -> float:
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        v = (await self.session.execute(
            select(func.coalesce(func.sum(PaperTrade.realized_pnl_delta), 0))
            .where(PaperTrade.user_id == user_id,
                   PaperTrade.executed_at >= start)
        )).scalar_one()
        return float(v or 0)

    async def _user_trades_today(self, user_id: str) -> int:
        start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        v = (await self.session.execute(
            select(func.count())
            .select_from(PaperTrade)
            .where(PaperTrade.user_id == user_id,
                   PaperTrade.executed_at >= start)
        )).scalar_one()
        return int(v or 0)

    async def _user_consecutive_losses(self, user_id: str) -> int:
        """Walk backwards through recent trades until a positive P&L breaks the streak."""
        rows = (await self.session.execute(
            select(PaperTrade.realized_pnl_delta)
            .where(PaperTrade.user_id == user_id)
            .order_by(PaperTrade.executed_at.desc())
            .limit(50)
        )).all()
        losses = 0
        for (pnl,) in rows:
            v = float(pnl or 0)
            if v < 0:
                losses += 1
            elif v > 0:
                break
            # else: 0 → skip
        return losses

    async def _user_symbol_position_qty(self, user_id: str, symbol: str) -> float:
        v = (await self.session.execute(
            select(func.coalesce(func.sum(PaperPosition.net_quantity), 0))
            .where(PaperPosition.user_id == user_id,
                   PaperPosition.symbol == symbol)
        )).scalar_one()
        return float(v or 0)

    async def _user_symbol_exposure_value(self, user_id: str, symbol: str) -> float:
        rows = (await self.session.execute(
            select(PaperPosition)
            .where(PaperPosition.user_id == user_id,
                   PaperPosition.symbol == symbol,
                   PaperPosition.net_quantity != 0)
        )).scalars().all()
        total = 0.0
        for p in rows:
            qty = float(p.net_quantity or 0)
            ltp = float(p.last_price) if p.last_price is not None else float(p.average_price or 0)
            total += abs(qty * ltp)
        return total

    async def _user_open_positions_count(self, user_id: str) -> int:
        v = (await self.session.execute(
            select(func.count())
            .select_from(PaperPosition)
            .where(PaperPosition.user_id == user_id,
                   PaperPosition.net_quantity != 0)
        )).scalar_one()
        return int(v or 0)

    async def _user_gross_exposure(self, user_id: str) -> float:
        rows = (await self.session.execute(
            select(PaperPosition)
            .where(PaperPosition.user_id == user_id,
                   PaperPosition.net_quantity != 0)
        )).scalars().all()
        total = 0.0
        for p in rows:
            qty = float(p.net_quantity or 0)
            ltp = float(p.last_price) if p.last_price is not None else float(p.average_price or 0)
            total += abs(qty * ltp)
        return total


# ---- risk_event → notification_event mapping ---------------------------


_EVENT_TO_NOTIFICATION: dict[RiskEventType, NotificationEvent] = {
    RiskEventType.DAILY_LOSS_LIMIT: NotificationEvent.DAILY_LOSS_REACHED,
    RiskEventType.DAILY_PROFIT_TARGET: NotificationEvent.TARGET_ACHIEVED,
    RiskEventType.MAX_TRADES_PER_DAY: NotificationEvent.MAX_TRADES_REACHED,
    RiskEventType.MAX_CONSECUTIVE_LOSSES: NotificationEvent.CONSECUTIVE_LOSSES,
    RiskEventType.MAX_CAPITAL_ALLOCATION: NotificationEvent.RISK_BREACH,
    RiskEventType.MAX_POSITION_SIZE: NotificationEvent.RISK_BREACH,
    RiskEventType.MAX_OPEN_POSITIONS: NotificationEvent.RISK_BREACH,
    RiskEventType.MAX_EXPOSURE_PER_SYMBOL: NotificationEvent.RISK_BREACH,
    RiskEventType.TRADING_SESSION_HOURS: NotificationEvent.RISK_BREACH,
    RiskEventType.LIVE_TRADING_DISABLED: NotificationEvent.LIVE_TRADING_DISABLED,
    RiskEventType.PAPER_MODE_FORCED: NotificationEvent.PAPER_MODE_FORCED,
    RiskEventType.BOTS_PAUSED: NotificationEvent.BOT_AUTO_PAUSED,
    RiskEventType.BOTS_RESUMED: NotificationEvent.SYSTEM_ALERT,
    RiskEventType.MANUAL_BLOCK: NotificationEvent.SYSTEM_ALERT,
}

_RISK_TO_NOTIFICATION_SEVERITY: dict[RiskSeverity, NotificationSeverity] = {
    RiskSeverity.INFO: NotificationSeverity.INFO,
    RiskSeverity.WARNING: NotificationSeverity.WARNING,
    RiskSeverity.ERROR: NotificationSeverity.ERROR,
    RiskSeverity.CRITICAL: NotificationSeverity.CRITICAL,
}

_RISK_TITLES: dict[RiskEventType, str] = {
    RiskEventType.DAILY_LOSS_LIMIT: "Daily loss limit reached",
    RiskEventType.DAILY_PROFIT_TARGET: "Daily profit target reached",
    RiskEventType.MAX_TRADES_PER_DAY: "Max trades per day reached",
    RiskEventType.MAX_CONSECUTIVE_LOSSES: "Consecutive losses limit hit",
    RiskEventType.MAX_CAPITAL_ALLOCATION: "Capital allocation limit exceeded",
    RiskEventType.MAX_POSITION_SIZE: "Position size limit exceeded",
    RiskEventType.MAX_OPEN_POSITIONS: "Open positions limit reached",
    RiskEventType.MAX_EXPOSURE_PER_SYMBOL: "Symbol exposure limit reached",
    RiskEventType.TRADING_SESSION_HOURS: "Outside trading hours",
    RiskEventType.LIVE_TRADING_DISABLED: "Live trading disabled",
    RiskEventType.PAPER_MODE_FORCED: "Paper-mode enforced",
    RiskEventType.BOTS_PAUSED: "Bots paused",
    RiskEventType.BOTS_RESUMED: "Bots resumed",
    RiskEventType.MANUAL_BLOCK: "Manual block",
}
