"""StrategyManager — process-scoped registry of live engine sessions.

Owns:
- The mapping `engine_session_id -> (EngineRunner, asyncio.Task)`.
- Starting, stopping, pausing, and resuming sessions.

DB state is authoritative (``EngineSession`` rows). This manager only holds
the in-process runner reference. When the API process restarts, in-flight
sessions are marked STOPPED at startup — restart-then-resume is a future
module (needs a leader-elected worker; not in scope for M2).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import (
    SessionAlreadyRunningError,
    SessionNotFoundError,
    StrategyNotFoundError,
)
from app.core.logging import get_logger
from app.db.session import async_session_factory as default_session_factory
from app.engine.market_data.base import MarketDataProvider
from app.engine.market_data.registry import resolve_provider
from app.engine.risk.engine import RiskConfig
from app.engine.runner import EngineRunner
from app.engine.strategy.registry import get_strategy_class
from app.models.engine import EngineSession, EngineSessionStatus

logger = get_logger(__name__)


@dataclass(slots=True)
class RunnerHandle:
    runner: EngineRunner
    task: asyncio.Task
    provider: MarketDataProvider
    broker_stream: Any = None      # BrokerOrderStream | None
    broker_adapter: Any = None     # BrokerAdapter | None


class StrategyManager:
    """Process-scoped singleton (import ``manager`` from this module).

    NOT multi-process-safe. Fine for a single Uvicorn worker; horizontal
    scaling is a future module.
    """

    def __init__(
        self, session_factory: async_sessionmaker | None = None
    ) -> None:
        self._session_factory = session_factory or default_session_factory
        self._runners: dict[str, RunnerHandle] = {}
        self._lock = asyncio.Lock()

    # ---- public API ------------------------------------------------------

    async def start(
        self,
        *,
        user_id: str,
        strategy_name: str,
        symbols: list[str],
        params: dict[str, Any] | None = None,
        risk_config: dict[str, Any] | None = None,
        initial_capital: float = 0.0,
        strategy_id: Optional[str] = None,
        provider_name: Optional[str] = None,
        provider_kwargs: dict[str, Any] | None = None,
        # Module 3 additions
        execution_mode: str = "paper",         # "paper" | "live"
        broker_account_id: Optional[str] = None,
    ) -> EngineSession:
        """Create + start a new session. Idempotency: raises if the user
        already has a running session with the same strategy + symbols."""
        from app.core.exceptions import BrokerRequiredForLiveModeError
        from app.models.engine import ExecutionMode

        strategy_cls = get_strategy_class(strategy_name)  # validates registration
        mode = execution_mode.lower()
        if mode == "live":
            # Global operator gate. This runs before broker/provider setup so
            # PAPER cannot accidentally open a real-money connection.
            async with self._session_factory() as gate_db:
                from app.services.trading_mode_service import TradingModeService

                await TradingModeService(gate_db).assert_live_enabled()
        if mode == "live" and not broker_account_id:
            raise BrokerRequiredForLiveModeError()

        async with self._lock:
            async with self._session_factory() as db:
                if await self._user_has_running_session(
                    db, user_id, strategy_name, symbols
                ):
                    raise SessionAlreadyRunningError(
                        f"A session for '{strategy_name}' on {symbols} is already running"
                    )

                # In live mode, resolve a REAL market-data provider and the
                # broker adapter early so we fail closed if anything is missing
                # / invalid before we persist a RUNNING session.
                broker_adapter = None
                live_provider = None
                if mode == "live":
                    from app.core.crypto import decrypt_json
                    from app.services.broker_service import BrokerService

                    svc = BrokerService(db)
                    account = await svc.get_for_user(user_id, broker_account_id)  # raises if not found

                    # FAIL CLOSED: a live session must never silently fall back
                    # to the mock market-data provider. Resolve the real,
                    # authenticated provider BEFORE starting the broker adapter
                    # or committing the session row.
                    live_provider = resolve_provider(
                        execution_mode=mode,
                        provider_name=provider_name,
                        broker_type=account.broker_type.value,
                        credentials=decrypt_json(account.credentials_ciphertext),
                        provider_kwargs=provider_kwargs,
                    )

                    broker_adapter = await svc.build_adapter(account)
                    await broker_adapter.start()

                session = EngineSession(
                    user_id=user_id,
                    strategy_name=strategy_name,
                    strategy_id=strategy_id,
                    status=EngineSessionStatus.RUNNING,
                    symbols=list(symbols),
                    params=dict(params or {}),
                    risk_config=dict(risk_config or {}),
                    initial_capital=float(initial_capital),
                    execution_mode=ExecutionMode(mode),
                    broker_account_id=broker_account_id,
                    started_at=datetime.now(timezone.utc),
                    last_heartbeat_at=datetime.now(timezone.utc),
                )
                db.add(session)
                await db.flush()
                await db.commit()
                await db.refresh(session)

            # LIVE sessions use the real provider resolved above (fail closed,
            # never mock). PAPER/DEMO/testing keep using the mock provider.
            if mode == "live":
                provider = live_provider
            else:
                provider = resolve_provider(
                    execution_mode=mode,
                    provider_name=provider_name,
                    provider_kwargs=provider_kwargs,
                )
            await provider.start()

            runner = EngineRunner(
                engine_session_id=session.id,
                user_id=user_id,
                strategy_cls=strategy_cls,
                provider=provider,
                risk_config=RiskConfig.from_dict(session.risk_config or {}),
                params=session.params or {},
                symbols=list(session.symbols or []),
                session_factory=self._session_factory,
                execution_mode=mode,
                broker_adapter=broker_adapter,
                broker_account_id=broker_account_id,
            )
            await runner.start()
            task = asyncio.create_task(
                self._run_and_track(session.id, runner),
                name=f"engine-session-{session.id}",
            )

            # In live mode, spin up a broker order stream that reconciles fills
            # back into our paper_orders / paper_positions tables.
            broker_stream = None
            if mode == "live" and broker_adapter is not None:
                broker_stream = await self._start_broker_stream(
                    session.id, user_id, broker_adapter, broker_account_id
                )

            self._runners[session.id] = RunnerHandle(
                runner=runner,
                task=task,
                provider=provider,
                broker_stream=broker_stream,
                broker_adapter=broker_adapter,
            )

        logger.info(
            "engine_session_started",
            extra={
                "engine_session_id": session.id,
                "strategy": strategy_name,
                "symbols": symbols,
                "user_id": user_id,
                "execution_mode": mode,
                "broker_account_id": broker_account_id,
            },
        )
        return session

    # ---- restart recovery: resume an EXISTING live session ------------

    async def resume_session(
        self,
        session: EngineSession,
        *,
        provider: MarketDataProvider,
        broker_adapter: Any = None,
    ) -> None:
        """Re-attach an in-process runner to an EXISTING (already-RUNNING) live
        session after a backend/process restart.

        Unlike :meth:`start`, this NEVER creates a new ``EngineSession`` row and
        NEVER submits an order — it only rebuilds the ``EngineRunner`` + broker
        order stream so live-fill reconciliation and the kill switch work again.
        The caller (``LiveRecoveryService``) is responsible for having verified
        that the broker and ORB states are safely consistent BEFORE calling.

        Idempotent: a no-op if a runner for this session already exists, so
        running recovery multiple times never duplicates runners or orders.
        """
        async with self._lock:
            if session.id in self._runners:
                return

            strategy_cls = get_strategy_class(session.strategy_name)
            mode = (
                session.execution_mode.value
                if hasattr(session.execution_mode, "value")
                else str(session.execution_mode)
            )

            await provider.start()

            runner = EngineRunner(
                engine_session_id=session.id,
                user_id=session.user_id,
                strategy_cls=strategy_cls,
                provider=provider,
                risk_config=RiskConfig.from_dict(session.risk_config or {}),
                params=session.params or {},
                symbols=list(session.symbols or []),
                session_factory=self._session_factory,
                execution_mode=mode,
                broker_adapter=broker_adapter,
                broker_account_id=session.broker_account_id,
            )
            await runner.start()
            task = asyncio.create_task(
                self._run_and_track(session.id, runner),
                name=f"engine-session-{session.id}",
            )

            broker_stream = None
            if mode == "live" and broker_adapter is not None:
                broker_stream = await self._start_broker_stream(
                    session.id, session.user_id, broker_adapter, session.broker_account_id
                )

            self._runners[session.id] = RunnerHandle(
                runner=runner,
                task=task,
                provider=provider,
                broker_stream=broker_stream,
                broker_adapter=broker_adapter,
            )

        logger.info(
            "engine_session_resumed",
            extra={
                "engine_session_id": session.id,
                "strategy": session.strategy_name,
                "execution_mode": mode,
                "broker_account_id": session.broker_account_id,
            },
        )

    # ---- broker-stream lifecycle (live mode) --------------------------

    async def _start_broker_stream(self, engine_session_id: str, user_id: str, broker_adapter, broker_account_id=None):
        """Spin up a BrokerOrderStream that reconciles broker updates into
        the engine's `paper_orders` / `paper_positions` tables."""
        from app.brokers.websocket.stream import BrokerOrderStream
        from app.engine.logger import TradeLogger
        from app.engine.orders.live_executor import LiveBrokerExecutor
        from app.engine.orders.manager import OrderManager
        from app.engine.portfolio.manager import PortfolioManager

        session_factory = self._session_factory

        def _live_executor():
            # Bind a LIVE executor so OCO reconciliation triggered by a fill
            # (cancel/resize the sibling exit) actually reaches the broker
            # instead of being a paper no-op. Task 4 safety.
            return LiveBrokerExecutor(
                broker_adapter, broker_account_id=broker_account_id or ""
            )

        async def _on_update(update):
            # Open a fresh DB session for each incoming update.
            async with session_factory() as db:
                portfolio = PortfolioManager(db)
                trade_logger = TradeLogger(db)
                mgr = OrderManager(
                    db,
                    engine_session_id=engine_session_id,
                    user_id=user_id,
                    portfolio=portfolio,
                    trade_logger=trade_logger,
                    executor=_live_executor(),
                )
                await mgr.apply_broker_update(update)
                await db.commit()

        async def _open_ids():
            async with session_factory() as db:
                portfolio = PortfolioManager(db)
                trade_logger = TradeLogger(db)
                mgr = OrderManager(
                    db,
                    engine_session_id=engine_session_id,
                    user_id=user_id,
                    portfolio=portfolio,
                    trade_logger=trade_logger,
                )
                return await mgr.open_broker_order_ids()

        stream = BrokerOrderStream(
            adapter=broker_adapter,
            engine_session_id=engine_session_id,
            on_update=_on_update,
            open_order_ids=_open_ids,
        )
        await stream.start()
        return stream

    async def stop(self, session_id: str, *, user_id: Optional[str] = None) -> EngineSession:
        async with self._lock:
            handle = self._runners.pop(session_id, None)
            if handle is not None:
                # Stop broker stream first so it doesn't try to apply updates
                # after the runner has released its DB session factory.
                if handle.broker_stream is not None:
                    try:
                        await handle.broker_stream.stop()
                    except Exception:  # pragma: no cover
                        pass
                await handle.runner.stop()
                if not handle.task.done():
                    handle.task.cancel()
                    try:
                        await handle.task
                    except (asyncio.CancelledError, Exception):
                        pass
                try:
                    await handle.provider.stop()
                except Exception:  # pragma: no cover
                    pass
                if handle.broker_adapter is not None:
                    try:
                        await handle.broker_adapter.stop()
                    except Exception:  # pragma: no cover
                        pass

            async with self._session_factory() as db:
                sess = await db.get(EngineSession, session_id)
                if sess is None or (user_id is not None and sess.user_id != user_id):
                    raise SessionNotFoundError()
                sess.status = EngineSessionStatus.STOPPED
                sess.stopped_at = datetime.now(timezone.utc)
                await db.commit()
                await db.refresh(sess)

        logger.info("engine_session_stopped", extra={"engine_session_id": session_id})
        return sess

    async def stop_all_for_user(self, user_id: str) -> list[str]:
        stopped: list[str] = []
        for sid in list(self._runners.keys()):
            async with self._session_factory() as db:
                sess = await db.get(EngineSession, sid)
                if sess and sess.user_id == user_id:
                    await self.stop(sid, user_id=user_id)
                    stopped.append(sid)
        return stopped

    async def status(self, user_id: str) -> list[EngineSession]:
        async with self._session_factory() as db:
            stmt = (
                select(EngineSession)
                .where(EngineSession.user_id == user_id)
                .order_by(EngineSession.created_at.desc())
                .limit(50)
            )
            return list((await db.execute(stmt)).scalars().all())

    def is_running(self, session_id: str) -> bool:
        return session_id in self._runners

    # ---- internals -------------------------------------------------------

    async def _run_and_track(self, session_id: str, runner: EngineRunner) -> None:
        try:
            await runner.run_forever()
        except Exception as exc:  # pragma: no cover
            logger.exception("engine_session_crashed", extra={"engine_session_id": session_id})
            async with self._session_factory() as db:
                sess = await db.get(EngineSession, session_id)
                if sess is not None:
                    sess.status = EngineSessionStatus.ERROR
                    sess.error_message = str(exc)
                    sess.stopped_at = datetime.now(timezone.utc)
                    await db.commit()

    async def _user_has_running_session(
        self,
        db: AsyncSession,
        user_id: str,
        strategy_name: str,
        symbols: list[str],
    ) -> bool:
        stmt = select(EngineSession).where(
            EngineSession.user_id == user_id,
            EngineSession.status == EngineSessionStatus.RUNNING,
            EngineSession.strategy_name == strategy_name,
        )
        rows = (await db.execute(stmt)).scalars().all()
        wanted = sorted(symbols)
        for r in rows:
            if sorted(r.symbols or []) == wanted:
                return True
        return False

    async def shutdown(self) -> None:
        """Stop every runner in this process — called on app shutdown."""
        for sid in list(self._runners.keys()):
            try:
                await self.stop(sid)
            except Exception:  # pragma: no cover
                pass

    # ---- kill switch: emergency flatten of LIVE sessions -----------------

    async def emergency_flatten_all(
        self, *, reason: str = "kill_switch"
    ) -> list[dict]:
        """Genuine emergency stop for every RUNNING **live** session.

        For each live session this:
        1. Stops the strategy from issuing new orders.
        2. Cancels outstanding broker orders and flattens the actual broker
           positions via :class:`EmergencyFlattenService` (broker = source of
           truth).

        Paper / demo sessions are ignored — they carry no real exposure.
        Returns one result dict per live session. Never raises.
        """
        from app.services.emergency_flatten_service import EmergencyFlattenService

        results: list[dict] = []
        for sid, handle in list(self._runners.items()):
            runner = handle.runner
            if getattr(runner, "execution_mode", "paper") != "live":
                continue
            if handle.broker_adapter is None:
                continue

            # 1. Stop new strategy-generated orders immediately.
            try:
                runner._stop_event.set()
            except Exception:  # pragma: no cover
                pass

            # 2. Cancel + flatten via the broker.
            try:
                async with self._session_factory() as db:
                    svc = EmergencyFlattenService(db)
                    res = await svc.flatten(
                        adapter=handle.broker_adapter,
                        engine_session_id=sid,
                        user_id=runner.user_id,
                        broker_account_id=getattr(runner, "_broker_account_id", None),
                        reason=reason,
                    )
                    await db.commit()
                results.append(res.to_dict())
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("emergency_flatten_failed",
                                 extra={"engine_session_id": sid})
                results.append({
                    "engine_session_id": sid,
                    "status": "failed",
                    "error": f"flatten_dispatch_failed: {exc}",
                })
        return results


# ---- process-scoped singleton --------------------------------------------

manager = StrategyManager()
