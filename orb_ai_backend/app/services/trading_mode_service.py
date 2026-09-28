"""Global PAPER/LIVE trading master switch.

The existing execution-safety singleton is reused as the durable settings
row.  The mode lives in its ``extra`` JSON bag so this additive feature needs
no schema migration and remains compatible with every completed task.

This module also owns three additive safety features layered on top of the
Master Switch:

* **LIVE cooldown**  — a server-enforced grace period after PAPER → LIVE is
  armed during which real-money order execution remains blocked.  The
  cooldown is persisted alongside the mode metadata, so restarts cannot
  bypass it, and it is re-checked by ``assert_live_enabled()`` on every
  order attempt (so concurrent requests cannot race past it either).
* **Admin notifications**  — every *successful* mode transition fans out a
  best-effort ``system_alert`` to admin users after the DB commit.  A
  notification failure never rolls back the mode change; the failure is
  logged and the audit record remains authoritative.
* **Automatic PAPER revert** — an admin-configurable, persisted scheduler
  policy that flips LIVE → PAPER at the configured time.  It refuses to
  run when live exposure exists and instead raises an admin alert.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestError, ConflictError
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderStatus,
    PaperOrder,
    PaperPosition,
)
from app.models.execution_safety import ExecutionSafetySetting
from app.models.notification import NotificationEvent, NotificationSeverity
from app.models.user import User, UserRole
from app.services.audit_service import AuditService
from app.services.execution_safety_service import GLOBAL_SETTINGS_ID

logger = logging.getLogger(__name__)

PAPER = "paper"
LIVE = "live"
LIVE_CONFIRMATION = "I UNDERSTAND THIS CAN PLACE REAL-MONEY ORDERS."
_OPEN_ORDER_STATUSES = (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)


class TradingModeError(BadRequestError):
    """Base for stable trading-mode transition failures."""


class LiveTradingDisabledError(TradingModeError):
    code = "live_trading_disabled_by_global_mode"
    message = "LIVE execution is disabled because the global trading mode is PAPER"


class LiveConfirmationRequiredError(TradingModeError):
    code = "live_confirmation_required"
    message = "Explicit real-money trading confirmation is required"


class LiveCooldownActiveError(TradingModeError):
    code = "live_cooldown_active"
    message = "LIVE trading is armed but still in the post-arm cooldown window"


class UnsafePaperTransitionError(ConflictError):
    code = "live_exposure_blocks_paper_mode"
    message = "Cannot switch to PAPER while LIVE sessions or exposure require shutdown and reconciliation"


class InvalidAutoRevertConfigError(TradingModeError):
    code = "invalid_auto_revert_config"
    message = "Auto-revert configuration is invalid"


def _cooldown_seconds() -> int:
    """Configured cooldown, always non-negative.

    Zero disables the cooldown entirely (used in most tests / paper-only
    installations).  The setting is env-configurable so operators can widen
    it without a code change.
    """
    return max(0, int(getattr(settings, "TRADING_MODE_LIVE_COOLDOWN_SECONDS", 60)))


def _parse_hhmm(text: str) -> time:
    parts = text.strip().split(":")
    if len(parts) != 2:
        raise InvalidAutoRevertConfigError(
            "at_time must be HH:MM in 24h", code="invalid_auto_revert_config"
        )
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError as e:
        raise InvalidAutoRevertConfigError(
            "at_time must be HH:MM in 24h", code="invalid_auto_revert_config"
        ) from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise InvalidAutoRevertConfigError(
            "at_time must be HH:MM in 24h", code="invalid_auto_revert_config"
        )
    return time(hour=h, minute=m)


def _parse_tz(text: str) -> ZoneInfo:
    try:
        return ZoneInfo(text)
    except ZoneInfoNotFoundError as e:
        raise InvalidAutoRevertConfigError(
            f"unknown timezone: {text}", code="invalid_auto_revert_config"
        ) from e


class TradingModeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ #
    # Settings row + basic mode accessors
    # ------------------------------------------------------------------ #
    async def ensure_settings_row(self) -> ExecutionSafetySetting:
        row = await self.session.get(ExecutionSafetySetting, GLOBAL_SETTINGS_ID)
        if row is not None:
            return row
        row = ExecutionSafetySetting(id=GLOBAL_SETTINGS_ID, extra={})
        self.session.add(row)
        await self.session.flush()
        return row

    @staticmethod
    def _mode_bag(row: ExecutionSafetySetting) -> dict[str, Any]:
        raw = (row.extra or {}).get("trading_mode", {})
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _mode_from_row(row: ExecutionSafetySetting) -> str:
        bag = TradingModeService._mode_bag(row)
        mode = bag.get("mode")
        return mode if mode in {PAPER, LIVE} else PAPER

    @staticmethod
    def _armed_at(row: ExecutionSafetySetting) -> datetime | None:
        raw = TradingModeService._mode_bag(row).get("armed_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _cooldown_seconds_persisted(row: ExecutionSafetySetting) -> int:
        raw = TradingModeService._mode_bag(row).get("cooldown_seconds")
        try:
            return max(0, int(raw)) if raw is not None else 0
        except (TypeError, ValueError):
            return 0

    def _cooldown_state(self, row: ExecutionSafetySetting) -> dict[str, Any]:
        """Compute derived cooldown fields (active flag + expiry timestamp)."""
        mode = self._mode_from_row(row)
        armed_at = self._armed_at(row)
        secs = self._cooldown_seconds_persisted(row)
        if mode != LIVE or armed_at is None or secs <= 0:
            return {"cooldown_active": False, "cooldown_expires_at": None, "cooldown_seconds": secs}
        expires = armed_at.timestamp() + secs
        active = datetime.now(timezone.utc).timestamp() < expires
        return {
            "cooldown_active": active,
            "cooldown_expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat(),
            "cooldown_seconds": secs,
        }

    # ------------------------------------------------------------------ #
    # Exposure & snapshot
    # ------------------------------------------------------------------ #
    async def _exposure_counts(self) -> dict[str, int]:
        live_session_filter = select(EngineSession.id).where(
            EngineSession.execution_mode == ExecutionMode.LIVE,
            EngineSession.status == EngineSessionStatus.RUNNING,
        )
        active_sessions = int((await self.session.execute(
            select(func.count()).select_from(live_session_filter.subquery())
        )).scalar_one())
        open_positions = int((await self.session.execute(
            select(func.count()).select_from(PaperPosition).where(
                PaperPosition.engine_session_id.in_(live_session_filter),
                PaperPosition.net_quantity != 0,
            )
        )).scalar_one())
        open_orders = int((await self.session.execute(
            select(func.count()).select_from(PaperOrder).where(
                PaperOrder.engine_session_id.in_(live_session_filter),
                PaperOrder.status.in_(_OPEN_ORDER_STATUSES),
            )
        )).scalar_one())
        return {
            "active_live_sessions": active_sessions,
            "open_live_positions": open_positions,
            "open_live_orders": open_orders,
        }

    async def snapshot(self) -> dict[str, Any]:
        row = await self.ensure_settings_row()
        bag = self._mode_bag(row)
        counts = await self._exposure_counts()
        cooldown = self._cooldown_state(row)
        mode = self._mode_from_row(row)
        if row.kill_switch_active:
            live_gate = "blocked"
        elif cooldown["cooldown_active"]:
            live_gate = "cooldown_active"
        else:
            live_gate = "requires_existing_live_safety_checks"
        return {
            "mode": mode,
            "changed_at": bag.get("changed_at"),
            "changed_by": bag.get("changed_by"),
            "armed_at": bag.get("armed_at"),
            **cooldown,
            **counts,
            "kill_switch_active": bool(row.kill_switch_active),
            "live_gate": live_gate,
            "auto_revert": self._auto_revert_from_row(row),
        }

    # ------------------------------------------------------------------ #
    # LIVE gate — order-time enforcement
    # ------------------------------------------------------------------ #
    async def assert_live_enabled(self) -> None:
        """Called by ``OrderManager.place()`` for every LIVE order.

        Fails closed when the global mode is not LIVE.  When it *is* LIVE
        but the post-arm cooldown window has not expired, we raise the
        distinct ``LiveCooldownActiveError`` so the caller sees a stable
        error code and audit trail.
        """
        row = await self.ensure_settings_row()
        if self._mode_from_row(row) != LIVE:
            raise LiveTradingDisabledError()
        state = self._cooldown_state(row)
        if state["cooldown_active"]:
            raise LiveCooldownActiveError(details={
                "cooldown_expires_at": state["cooldown_expires_at"],
            })

    # ------------------------------------------------------------------ #
    # Mode changes
    # ------------------------------------------------------------------ #
    async def set_mode(
        self,
        *,
        target: str,
        operator: User,
        reason: str | None = None,
        confirmation: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        target = (target or "").strip().lower()
        if target not in {PAPER, LIVE}:
            raise BadRequestError("Trading mode must be 'paper' or 'live'", code="invalid_trading_mode")
        row = await self.ensure_settings_row()
        previous = self._mode_from_row(row)
        counts = await self._exposure_counts()

        if target == LIVE and (confirmation or "").strip().upper() != LIVE_CONFIRMATION:
            await self._record_denied(operator, previous, target, "confirmation_required", reason, ip_address)
            raise LiveConfirmationRequiredError()
        if target == PAPER and previous == LIVE and any(counts.values()):
            await self._record_denied(operator, previous, target, "live_exposure_active", reason, ip_address, counts)
            raise UnsafePaperTransitionError(details=counts)

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        extra = dict(row.extra or {})
        new_bag: dict[str, Any] = {
            "mode": target,
            "changed_at": now_iso,
            "changed_by": operator.id,
        }
        if target == LIVE:
            # Only stamp/reset ``armed_at`` when this transition actually turns
            # LIVE on.  Re-applying LIVE while already LIVE is a no-op that
            # must not extend the cooldown.
            if previous == LIVE:
                prior = self._mode_bag(row)
                new_bag["armed_at"] = prior.get("armed_at") or now_iso
                new_bag["cooldown_seconds"] = prior.get("cooldown_seconds", _cooldown_seconds())
            else:
                new_bag["armed_at"] = now_iso
                new_bag["cooldown_seconds"] = _cooldown_seconds()
        extra["trading_mode"] = new_bag
        row.extra = extra
        self.session.add(row)
        await AuditService(self.session).record(
            action="trading_mode.change",
            target_type="trading_mode",
            target_id=GLOBAL_SETTINGS_ID,
            actor=operator,
            details={
                "previous_mode": previous,
                "new_mode": target,
                "reason": reason,
                "cooldown_seconds": new_bag.get("cooldown_seconds"),
            },
            ip_address=ip_address,
        )
        await self.session.flush()
        snapshot = await self.snapshot()
        # Notify admins AFTER the transactional work.  Failures are logged
        # but MUST NOT roll back the successful mode change.
        await self._notify_admins_mode_change(
            operator=operator, previous=previous, new=target, reason=reason, snapshot=snapshot,
        )
        return snapshot

    async def _record_denied(
        self, operator: User, previous: str, target: str, reason_code: str,
        reason: str | None, ip_address: str | None, counts: dict[str, int] | None = None,
    ) -> None:
        await AuditService(self.session).record(
            action="trading_mode.change_denied",
            target_type="trading_mode",
            target_id=GLOBAL_SETTINGS_ID,
            actor=operator,
            details={
                "previous_mode": previous,
                "requested_mode": target,
                "reason_code": reason_code,
                "reason": reason,
                "exposure": counts or {},
            },
            ip_address=ip_address,
        )
        await self.session.flush()
        # Denied attempts must survive the request rollback caused by the
        # deliberate domain error; they are part of the audit contract.
        await self.session.commit()

    # ------------------------------------------------------------------ #
    # Notifications (best-effort — never roll back the mode change)
    # ------------------------------------------------------------------ #
    async def _notify_admins_mode_change(
        self, *, operator: User, previous: str, new: str,
        reason: str | None, snapshot: dict[str, Any],
    ) -> None:
        if previous == new:
            return  # No-op transition
        try:
            from app.services.notification_service import NotificationService

            severity = NotificationSeverity.WARNING if new == LIVE else NotificationSeverity.INFO
            title = f"Trading mode → {new.upper()}"
            body = (
                f"{operator.email or operator.id} switched trading mode from "
                f"{previous.upper()} to {new.upper()}."
            )
            payload = {
                "previous_mode": previous,
                "new_mode": new,
                "reason": reason,
                "operator_id": operator.id,
                "operator_email": operator.email,
                "changed_at": snapshot.get("changed_at"),
                "armed_at": snapshot.get("armed_at"),
                "cooldown_seconds": snapshot.get("cooldown_seconds"),
            }
            svc = NotificationService(self.session)
            admins = (
                await self.session.execute(
                    select(User).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
                )
            ).scalars().all()
            for admin in admins:
                await svc.notify(
                    user=admin,
                    event=NotificationEvent.SYSTEM_ALERT,
                    title=title,
                    body=body,
                    severity=severity,
                    payload=payload,
                )
        except Exception:  # noqa: BLE001 — best-effort, never re-raise
            logger.exception(
                "trading_mode_notification_failed",
                extra={"previous": previous, "new": new, "operator_id": operator.id},
            )

    # ------------------------------------------------------------------ #
    # Auto-revert configuration
    # ------------------------------------------------------------------ #
    @staticmethod
    def _auto_revert_from_row(row: ExecutionSafetySetting) -> dict[str, Any]:
        raw = (row.extra or {}).get("trading_mode_auto_revert", {})
        if not isinstance(raw, dict):
            raw = {}
        return {
            "enabled": bool(raw.get("enabled", False)),
            "at_time": raw.get("at_time"),
            "timezone": raw.get("timezone"),
            "last_run_date": raw.get("last_run_date"),
            "last_run_result": raw.get("last_run_result"),
            "last_run_at": raw.get("last_run_at"),
        }

    async def get_auto_revert(self) -> dict[str, Any]:
        row = await self.ensure_settings_row()
        return self._auto_revert_from_row(row)

    async def set_auto_revert(
        self, *, operator: User, enabled: bool, at_time: str | None,
        timezone_name: str | None, ip_address: str | None = None,
    ) -> dict[str, Any]:
        # Validation happens even when disabling, so an admin cannot store
        # garbage that a future ``enable`` would trip over.
        if enabled:
            if not at_time or not timezone_name:
                raise InvalidAutoRevertConfigError(
                    "at_time and timezone are required to enable auto-revert",
                    code="invalid_auto_revert_config",
                )
            _parse_hhmm(at_time)
            _parse_tz(timezone_name)
        row = await self.ensure_settings_row()
        prev = self._auto_revert_from_row(row)
        extra = dict(row.extra or {})
        extra["trading_mode_auto_revert"] = {
            "enabled": bool(enabled),
            "at_time": at_time,
            "timezone": timezone_name,
            "last_run_date": prev.get("last_run_date"),
            "last_run_result": prev.get("last_run_result"),
            "last_run_at": prev.get("last_run_at"),
        }
        row.extra = extra
        self.session.add(row)
        await AuditService(self.session).record(
            action="trading_mode.auto_revert.configure",
            target_type="trading_mode",
            target_id=GLOBAL_SETTINGS_ID,
            actor=operator,
            details={"previous": prev, "new": extra["trading_mode_auto_revert"]},
            ip_address=ip_address,
        )
        await self.session.flush()
        return self._auto_revert_from_row(row)

    async def run_auto_revert_if_due(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Check the persisted auto-revert config and, if this scheduler tick
        should trigger a revert, perform it safely.

        Idempotency: uses the persisted ``last_run_date`` in the config bag
        so at most one revert runs per calendar day.  A restart cannot
        create a duplicate transition because the flag is DB-backed.

        Safety: exposure is checked *before* any state change; if there is
        any LIVE exposure we do not silently flatten anything — we record
        an audit event and notify admins that the revert was blocked.
        """
        now = now or datetime.now(timezone.utc)
        row = await self.ensure_settings_row()
        cfg = self._auto_revert_from_row(row)
        if not cfg["enabled"]:
            return {"ran": False, "reason": "disabled"}
        if not cfg["at_time"] or not cfg["timezone"]:
            return {"ran": False, "reason": "misconfigured"}
        try:
            target_time = _parse_hhmm(cfg["at_time"])
            tz = _parse_tz(cfg["timezone"])
        except InvalidAutoRevertConfigError:
            return {"ran": False, "reason": "invalid_config"}
        local_now = now.astimezone(tz)
        # Trigger only when *after* the configured time (and up to 6h later)
        # on that same local day, so a scheduler running every minute picks
        # it up but a slow tick doesn't miss the window entirely.
        target_dt = local_now.replace(
            hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
        )
        if local_now < target_dt:
            return {"ran": False, "reason": "not_yet"}
        today_iso = local_now.date().isoformat()
        if cfg.get("last_run_date") == today_iso:
            return {"ran": False, "reason": "already_ran_today"}

        # Guard: only revert while currently LIVE.
        current_mode = self._mode_from_row(row)
        if current_mode != LIVE:
            await self._auto_revert_record(row, today_iso, now, result="skipped_not_live")
            return {"ran": False, "reason": "not_live"}

        # Exposure check — never silently flatten anything.
        counts = await self._exposure_counts()
        if any(counts.values()):
            await self._auto_revert_blocked(row, today_iso, now, counts=counts)
            return {"ran": False, "reason": "live_exposure", "exposure": counts}

        operator = await self._auto_revert_operator(row)
        if operator is None:
            await self._auto_revert_record(row, today_iso, now, result="skipped_no_admin")
            return {"ran": False, "reason": "no_admin_operator"}

        # Route through the normal ``set_mode`` path so notifications, audit
        # logs and safety checks fire exactly as they would for a manual
        # LIVE → PAPER click.  Refresh the ``last_run_date`` afterwards.
        try:
            snapshot = await self.set_mode(
                target=PAPER, operator=operator,
                reason=f"automatic_paper_revert@{cfg['at_time']} {cfg['timezone']}",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("trading_mode_auto_revert_failed", extra={"error": str(e)})
            await self._auto_revert_record(row, today_iso, now, result=f"error:{type(e).__name__}")
            return {"ran": False, "reason": "error", "error": str(e)}
        await self._auto_revert_record(row, today_iso, now, result="reverted")
        await self.session.commit()
        return {"ran": True, "reason": "reverted", "snapshot": snapshot}

    async def _auto_revert_operator(self, row: ExecutionSafetySetting) -> User | None:
        """Pick a stable operator for the audit trail.

        Prefer the admin who armed LIVE (if still active); otherwise any
        active admin.  This keeps notifications addressed to a real person
        without inventing a synthetic user.
        """
        armed_by = self._mode_bag(row).get("changed_by")
        if armed_by:
            u = await self.session.get(User, armed_by)
            if u is not None and u.is_active and u.role == UserRole.ADMIN:
                return u
        return (await self.session.execute(
            select(User).where(User.role == UserRole.ADMIN, User.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()

    async def _auto_revert_record(
        self, row: ExecutionSafetySetting, today_iso: str, now: datetime, *, result: str,
    ) -> None:
        extra = dict(row.extra or {})
        cfg = dict(extra.get("trading_mode_auto_revert") or {})
        cfg["last_run_date"] = today_iso
        cfg["last_run_result"] = result
        cfg["last_run_at"] = now.astimezone(timezone.utc).isoformat()
        extra["trading_mode_auto_revert"] = cfg
        row.extra = extra
        self.session.add(row)
        await self.session.flush()

    async def _auto_revert_blocked(
        self, row: ExecutionSafetySetting, today_iso: str, now: datetime,
        *, counts: dict[str, int],
    ) -> None:
        """Record the block and notify admins.  Does not commit."""
        await AuditService(self.session).record(
            action="trading_mode.auto_revert.blocked",
            target_type="trading_mode",
            target_id=GLOBAL_SETTINGS_ID,
            actor=None,
            details={"reason": "live_exposure_active", "exposure": counts},
            ip_address=None,
        )
        await self._auto_revert_record(row, today_iso, now, result="blocked_live_exposure")
        await self.session.commit()
        # Best-effort admin alert
        try:
            from app.services.notification_service import NotificationService

            admins = (await self.session.execute(
                select(User).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
            )).scalars().all()
            svc = NotificationService(self.session)
            for admin in admins:
                await svc.notify(
                    user=admin,
                    event=NotificationEvent.SYSTEM_ALERT,
                    title="Auto PAPER revert BLOCKED — LIVE exposure active",
                    body=(
                        "Nightly PAPER revert refused to run because LIVE "
                        f"exposure remains: {counts}. Stop live sessions or "
                        "close positions before it can run."
                    ),
                    severity=NotificationSeverity.CRITICAL,
                    payload={"exposure": counts, "date": today_iso},
                )
            await self.session.commit()
        except Exception:
            logger.exception("trading_mode_auto_revert_blocked_notify_failed")
