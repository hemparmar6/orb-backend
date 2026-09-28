"""LiveRecoveryService — safe startup recovery + broker reconciliation (Task 3).

Live strategy runners are process-scoped. If the backend restarts (e.g. a
Railway redeploy) the in-memory ``EngineRunner`` disappears while a real broker
position/order may still exist. A database ``EngineSession`` row that merely
says ``RUNNING`` is NOT sufficient evidence that it is safe to resume live
trading.

On startup this service, for every ``RUNNING`` + ``execution_mode=live``
session:

1. Reconnects to the session's broker via the existing ``BrokerAdapter``
   abstraction (never a mock on the live path).
2. Fetches the broker's *actual* positions and open/pending orders.
3. Reconciles the broker state against ORB's persisted state
   (``paper_positions`` / ``paper_orders``).
4. Resumes the runner ONLY when the two states are safely consistent.
   Otherwise it marks the session ``NEEDS_RECONCILE`` and leaves live trading
   stopped.

Safety guarantees (fail-closed):
- Never creates a new EngineSession because the process restarted.
- Never submits or duplicates a broker order during recovery — recovery is
  read-then-reconcile only; it places nothing at the broker.
- Existing broker order ids / client-order ids are used for correlation.
- A broker position with no matching ORB position is NOT auto-closed; it is
  flagged for manual reconciliation.
- An ORB position that the broker reports flat is reconciled DOWN to the
  broker (source of truth) rather than recreated.
- Unknown pending broker orders are never duplicated — they block resume.
- Idempotent: running it repeatedly yields the same safe state (a session
  already running in-process is skipped; a NEEDS_RECONCILE session is no longer
  RUNNING so it is not picked up again).
- Resumed sessions are re-registered with the ``StrategyManager`` so the
  existing kill switch keeps working after recovery.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.brokers.base import BrokerAdapter, BrokerOrderStatus
from app.brokers.registry import get_broker_adapter
from app.core.crypto import decrypt_json
from app.core.exceptions import BrokerError
from app.core.logging import get_logger
from app.db.session import async_session_factory as default_session_factory
from app.engine.market_data.base import MarketDataProvider
from app.engine.market_data.registry import resolve_provider
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderStatus,
    PaperOrder,
    PaperPosition,
)
from app.repositories.broker_repository import BrokerAccountRepository

logger = get_logger(__name__)

_EPS = 1e-9

_ORB_OPEN_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.OPEN,
    OrderStatus.PARTIALLY_FILLED,
)
_BROKER_OPEN_STATUSES = {
    BrokerOrderStatus.PENDING,
    BrokerOrderStatus.OPEN,
    BrokerOrderStatus.PARTIALLY_FILLED,
}


# ---- results / decisions --------------------------------------------------


@dataclass
class ReconcileDecision:
    """Outcome of comparing broker state with ORB's persisted state."""

    safe: bool = False
    reason: Optional[str] = None
    # symbols the broker holds that ORB does not know about (BLOCK resume).
    position_mismatches: list[dict[str, Any]] = field(default_factory=list)
    # broker open orders that map to no known ORB order (BLOCK resume).
    unknown_broker_orders: list[str] = field(default_factory=list)
    # ORB open orders whose broker order id is missing / never reached the
    # broker (uncertain → BLOCK resume, fail-closed).
    missing_orb_orders: list[str] = field(default_factory=list)
    # symbols ORB thinks it holds but the broker reports flat — safe to
    # reconcile DOWN to the broker (does NOT block resume).
    orb_only_symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecoveryResult:
    engine_session_id: str
    action: str = "skipped"  # resumed|needs_reconcile|already_running|skipped|not_found
    resumed: bool = False
    reason: Optional[str] = None
    decision: Optional[dict[str, Any]] = None
    reconciled_positions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LiveRecoveryService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker | None = None,
        manager: Any = None,
    ) -> None:
        self._session_factory = session_factory or default_session_factory
        if manager is None:
            from app.engine.strategy.manager import manager as default_manager

            manager = default_manager
        self._manager = manager

    # ---- public API -----------------------------------------------------

    async def recover_all(self) -> list[RecoveryResult]:
        """Recover every RUNNING live session. Never raises."""
        async with self._session_factory() as db:
            stmt = select(EngineSession.id).where(
                EngineSession.status == EngineSessionStatus.RUNNING,
                EngineSession.execution_mode == ExecutionMode.LIVE,
            )
            ids = list((await db.execute(stmt)).scalars().all())

        results: list[RecoveryResult] = []
        for sid in ids:
            try:
                results.append(await self.recover_session(sid))
            except Exception as exc:  # pragma: no cover - defensive, fail closed
                logger.exception(
                    "live_recovery_session_failed",
                    extra={"engine_session_id": sid},
                )
                await self._mark_needs_reconcile(sid, f"recovery_error: {exc}")
                results.append(
                    RecoveryResult(
                        engine_session_id=sid,
                        action="needs_reconcile",
                        reason=f"recovery_error: {exc}",
                    )
                )
        logger.info("live_recovery_complete", extra={"count": len(results)})
        return results

    async def recover_session(
        self,
        session_id: str,
        *,
        adapter: BrokerAdapter | None = None,
        provider: MarketDataProvider | None = None,
    ) -> RecoveryResult:
        """Recover a single session.

        ``adapter`` / ``provider`` may be injected (tests); otherwise they are
        built from the session's persisted broker account (production path).
        """
        # Idempotency: never touch a session already running in-process.
        if self._manager.is_running(session_id):
            return RecoveryResult(session_id, action="already_running", resumed=True)

        # ---- 1. Load session + snapshot ORB state -----------------------
        async with self._session_factory() as db:
            sess = await db.get(EngineSession, session_id)
            if sess is None:
                return RecoveryResult(session_id, action="not_found")
            if (
                sess.status != EngineSessionStatus.RUNNING
                or sess.execution_mode != ExecutionMode.LIVE
            ):
                return RecoveryResult(
                    session_id,
                    action="skipped",
                    reason="not a running live session",
                )
            user_id = sess.user_id
            broker_account_id = sess.broker_account_id
            orb_net = await self._orb_position_net(db, session_id)
            orb_open_orders = await self._orb_open_orders(db, session_id)
            known_broker_ids = (
                await self._known_broker_order_ids(db, broker_account_id)
                if broker_account_id
                else set()
            )

        if not broker_account_id:
            await self._mark_needs_reconcile(
                session_id, "live session has no broker_account_id"
            )
            return RecoveryResult(
                session_id,
                action="needs_reconcile",
                reason="live session has no broker_account_id",
            )

        # ---- 2. Acquire the broker account (production build path) ------
        creds: dict | None = None
        broker_type: str | None = None
        alias: str = ""
        if adapter is None or provider is None:
            async with self._session_factory() as db:
                account = await BrokerAccountRepository(db).get_for_user(
                    broker_account_id, user_id
                )
            if account is None:
                await self._mark_needs_reconcile(
                    session_id, "broker account not found for live session"
                )
                return RecoveryResult(
                    session_id,
                    action="needs_reconcile",
                    reason="broker account not found",
                )
            creds = decrypt_json(account.credentials_ciphertext)
            broker_type = account.broker_type.value
            alias = account.alias

        # ---- 3. Reconnect + fetch broker state (fail closed) ------------
        built_adapter = False
        try:
            if provider is None:
                provider = resolve_provider(
                    execution_mode="live",
                    broker_type=broker_type,
                    credentials=creds,
                )
            if adapter is None:
                adapter = get_broker_adapter(broker_type, creds, alias=alias)  # type: ignore[arg-type]
                built_adapter = True
            await adapter.start()
            if not await adapter.health_check():
                raise BrokerError("broker health_check returned False")
            broker_positions = await adapter.list_positions()
            broker_orders = await adapter.list_orders()
        except Exception as exc:
            if built_adapter and adapter is not None:
                try:
                    await adapter.stop()
                except Exception:  # pragma: no cover
                    pass
            await self._mark_needs_reconcile(
                session_id, f"broker_connection_failed: {exc}"
            )
            return RecoveryResult(
                session_id,
                action="needs_reconcile",
                reason=f"broker_connection_failed: {exc}",
            )

        # ---- 4. Reconcile broker vs ORB ---------------------------------
        decision = self.reconcile(
            orb_net=orb_net,
            orb_open_orders=orb_open_orders,
            broker_positions=broker_positions,
            broker_orders=broker_orders,
            known_broker_ids=known_broker_ids,
        )

        if not decision.safe:
            # Fail closed: do NOT resume live trading. Leave any broker orders
            # / positions untouched (never auto-close, never duplicate).
            if built_adapter:
                try:
                    await adapter.stop()
                except Exception:  # pragma: no cover
                    pass
            await self._mark_needs_reconcile(
                session_id, decision.reason, decision=decision
            )
            return RecoveryResult(
                session_id,
                action="needs_reconcile",
                reason=decision.reason,
                decision=decision.to_dict(),
            )

        # ---- 5. Safe: apply DB-only reconciliation then resume ----------
        if decision.orb_only_symbols:
            await self._flatten_orb_positions(session_id, decision.orb_only_symbols)

        async with self._session_factory() as db:
            fresh = await db.get(EngineSession, session_id)

        await self._manager.resume_session(
            fresh, provider=provider, broker_adapter=adapter
        )
        logger.info(
            "live_session_recovered",
            extra={
                "engine_session_id": session_id,
                "reconciled_positions": decision.orb_only_symbols,
            },
        )
        return RecoveryResult(
            session_id,
            action="resumed",
            resumed=True,
            reason=decision.reason,
            decision=decision.to_dict(),
            reconciled_positions=list(decision.orb_only_symbols),
        )

    # ---- reconciliation logic (pure — unit-testable) --------------------

    def reconcile(
        self,
        *,
        orb_net: dict[str, float],
        orb_open_orders: list[PaperOrder],
        broker_positions: list[Any],
        broker_orders: list[Any],
        known_broker_ids: set[str],
    ) -> ReconcileDecision:
        """Compare broker state (source of truth) with ORB's persisted state.

        Returns a :class:`ReconcileDecision`. ``safe`` is True only when the
        two states are consistent enough to resume live trading without any
        risk of hidden exposure or duplicate orders.
        """
        decision = ReconcileDecision()

        # ---- positions ----
        broker_net: dict[str, float] = {}
        for p in broker_positions:
            broker_net[p.symbol] = broker_net.get(p.symbol, 0.0) + float(p.net_quantity)

        symbols = {s for s, q in orb_net.items() if abs(q) > _EPS} | {
            s for s, q in broker_net.items() if abs(q) > _EPS
        }
        for sym in sorted(symbols):
            o = float(orb_net.get(sym, 0.0))
            b = float(broker_net.get(sym, 0.0))
            if abs(o - b) <= _EPS:
                continue
            if abs(o) <= _EPS and abs(b) > _EPS:
                # Broker holds a position ORB doesn't know about — NEVER assume
                # it should be closed. Block resume, flag for manual review.
                decision.position_mismatches.append(
                    {"symbol": sym, "type": "broker_position_unknown_to_orb",
                     "orb_net": o, "broker_net": b}
                )
            elif abs(o) > _EPS and abs(b) <= _EPS:
                # ORB thinks it has a position but the broker is flat — reconcile
                # ORB DOWN to the broker (source of truth). Safe.
                decision.orb_only_symbols.append(sym)
            else:
                # Both hold, quantities differ — genuine conflict. Block resume.
                decision.position_mismatches.append(
                    {"symbol": sym, "type": "quantity_mismatch",
                     "orb_net": o, "broker_net": b}
                )

        # ---- orders ----
        broker_ids_present: set[str] = set()
        for bo in broker_orders:
            broker_ids_present.add(bo.broker_order_id)
            if (
                bo.status in _BROKER_OPEN_STATUSES
                and bo.broker_order_id not in known_broker_ids
            ):
                # A pending broker order ORB does not recognise — never
                # duplicate it; block resume until reconciled.
                decision.unknown_broker_orders.append(bo.broker_order_id)

        for o in orb_open_orders:
            if not o.broker_order_id:
                # ORB believes this order is live but it never got a broker id
                # (likely crashed mid-submit) — uncertain, fail closed.
                decision.missing_orb_orders.append(o.id)
            elif o.broker_order_id not in broker_ids_present:
                # ORB thinks this order is live but the broker has no record —
                # conflicting; block resume.
                decision.missing_orb_orders.append(o.id)

        decision.safe = not (
            decision.position_mismatches
            or decision.unknown_broker_orders
            or decision.missing_orb_orders
        )
        decision.reason = self._build_reason(decision)
        return decision

    @staticmethod
    def _build_reason(d: ReconcileDecision) -> str:
        if d.safe:
            parts = ["broker and ORB state consistent"]
            if d.orb_only_symbols:
                parts.append(
                    "reconciled ORB positions to flat for "
                    + ",".join(d.orb_only_symbols)
                )
            return "; ".join(parts)
        parts = []
        if d.position_mismatches:
            parts.append(f"position mismatch: {d.position_mismatches}")
        if d.unknown_broker_orders:
            parts.append(f"unknown broker orders: {d.unknown_broker_orders}")
        if d.missing_orb_orders:
            parts.append(f"orb orders missing at broker: {d.missing_orb_orders}")
        return "manual reconciliation required — " + "; ".join(parts)

    # ---- ORB-state readers ----------------------------------------------

    async def _orb_position_net(self, db, session_id: str) -> dict[str, float]:
        stmt = select(PaperPosition).where(
            PaperPosition.engine_session_id == session_id
        )
        rows = (await db.execute(stmt)).scalars().all()
        net: dict[str, float] = {}
        for p in rows:
            q = float(p.net_quantity)
            if abs(q) > _EPS:
                net[p.symbol] = net.get(p.symbol, 0.0) + q
        return net

    async def _orb_open_orders(self, db, session_id: str) -> list[PaperOrder]:
        stmt = select(PaperOrder).where(
            PaperOrder.engine_session_id == session_id,
            PaperOrder.status.in_(_ORB_OPEN_STATUSES),
        )
        return list((await db.execute(stmt)).scalars().all())

    async def _known_broker_order_ids(
        self, db, broker_account_id: str
    ) -> set[str]:
        """Broker order ids ORB already owns for this account (across ALL
        sessions) — used so an order belonging to another ORB session is not
        mistaken for an 'unknown' broker order."""
        stmt = select(PaperOrder.broker_order_id).where(
            PaperOrder.broker_account_id == broker_account_id,
            PaperOrder.broker_order_id.is_not(None),
        )
        rows = (await db.execute(stmt)).scalars().all()
        return {r for r in rows if r}

    # ---- state mutations (idempotent) -----------------------------------

    async def _flatten_orb_positions(self, session_id: str, symbols: list[str]) -> None:
        """Reconcile ORB positions DOWN to the broker (broker reports flat).

        Idempotent: a position already at zero is left unchanged.
        """
        async with self._session_factory() as db:
            stmt = select(PaperPosition).where(
                PaperPosition.engine_session_id == session_id,
                PaperPosition.symbol.in_(list(symbols)),
            )
            rows = (await db.execute(stmt)).scalars().all()
            now = datetime.now(timezone.utc)
            for p in rows:
                if abs(float(p.net_quantity)) > _EPS:
                    p.net_quantity = 0
                    if p.closed_at is None:
                        p.closed_at = now
            await db.commit()

    async def _mark_needs_reconcile(
        self,
        session_id: str,
        reason: Optional[str],
        *,
        decision: ReconcileDecision | None = None,
    ) -> None:
        async with self._session_factory() as db:
            sess = await db.get(EngineSession, session_id)
            if sess is None:
                return
            # Idempotent: only meaningful for a still-RUNNING live session.
            sess.status = EngineSessionStatus.NEEDS_RECONCILE
            sess.error_message = (reason or "manual reconciliation required")[:2000]
            await db.commit()
        logger.warning(
            "live_session_needs_reconcile",
            extra={
                "engine_session_id": session_id,
                "reason": reason,
                "decision": decision.to_dict() if decision else None,
            },
        )
