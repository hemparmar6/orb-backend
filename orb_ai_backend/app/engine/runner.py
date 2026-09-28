"""EngineRunner — one instance per RUNNING EngineSession.

Wires: market data provider ─▶ strategy ─▶ order manager ─▶ portfolio.

Two consumption modes:
- ``run_forever()`` — long-running background task; iterates ``provider.stream()``
- ``run_once(quote)`` — synchronous single-tick step (used by tests & backtest)

The runner opens a fresh AsyncSession per tick batch so it doesn't hold a
transaction across the whole session lifetime.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.logging import get_logger
from app.db.session import async_session_factory as default_session_factory
from app.engine.logger import TradeLogger
from app.engine.market_data.base import MarketDataProvider, Quote
from app.engine.orders.manager import OrderIntentRequest, OrderManager
from app.engine.portfolio.manager import PortfolioManager
from app.engine.risk.engine import (
    OrderIntent,
    RiskConfig,
    RiskDecision,
    RiskEngine,
    SessionSnapshot,
)
from app.engine.strategy.base import BaseStrategy, StrategyContext
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    OrderSide,
    OrderType,
    PaperOrder,
)

logger = get_logger(__name__)


class EngineRunner:
    def __init__(
        self,
        *,
        engine_session_id: str,
        user_id: str,
        strategy_cls: type[BaseStrategy],
        provider: MarketDataProvider,
        risk_config: RiskConfig,
        params: dict[str, Any],
        symbols: list[str],
        session_factory: async_sessionmaker | None = None,
        # Module 3 additions
        execution_mode: str = "paper",       # "paper" | "live"
        broker_adapter=None,                  # BrokerAdapter | None
        broker_account_id: str | None = None,
    ) -> None:
        self.engine_session_id = engine_session_id
        self.user_id = user_id
        self.strategy_cls = strategy_cls
        self.provider = provider
        self.risk_config = risk_config
        self.params = params
        self.symbols = symbols
        self._session_factory = session_factory or default_session_factory
        self.execution_mode = execution_mode
        self._broker_adapter = broker_adapter
        self._broker_account_id = broker_account_id

        self._strategy: BaseStrategy | None = None
        self._state: dict[str, Any] = {}
        self._risk_engine = RiskEngine(risk_config)
        self._stop_event = asyncio.Event()
        self._last_prices: dict[str, float] = {}

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        # Instantiate strategy with a context that closes over `self`.
        ctx = StrategyContext(
            engine_session_id=self.engine_session_id,
            user_id=self.user_id,
            strategy_name=self.strategy_cls.name,
            params=self.params,
            symbols=list(self.symbols),
            place_order=self._ctx_place_order,
            modify_order=self._ctx_modify_order,
            cancel_order=self._ctx_cancel_order,
            get_positions=self._ctx_positions,
            get_last_price=self._ctx_last_price,
            state=self._state,
        )
        self._strategy = self.strategy_cls(ctx)
        await self._strategy.on_start()

        await self.provider.subscribe(self.symbols)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._strategy is not None:
            try:
                await self._strategy.on_stop()
            except Exception:  # pragma: no cover
                logger.exception("strategy_on_stop_failed")
        try:
            await self.provider.unsubscribe(self.symbols)
        except Exception:  # pragma: no cover
            pass

    async def run_forever(self) -> None:
        """Long-running loop. Cancelled on stop()."""
        assert self._strategy is not None, "call start() first"
        try:
            async for quote in self.provider.stream():
                if self._stop_event.is_set():
                    break
                await self._process_quote(quote)
        except asyncio.CancelledError:
            return

    # ---- test / step-mode ------------------------------------------------

    async def run_once(self, quote: Quote) -> None:
        """Deterministic single-tick step used by tests and backtests."""
        if self._strategy is None:
            await self.start()
        await self._process_quote(quote)

    # ---- core tick handler ----------------------------------------------

    async def _process_quote(self, quote: Quote) -> None:
        self._last_prices[quote.symbol] = float(quote.price)

        async with self._session_factory() as db:
            portfolio = PortfolioManager(db)
            trade_logger = TradeLogger(db)
            executor = self._build_executor()
            order_mgr = OrderManager(
                db,
                engine_session_id=self.engine_session_id,
                user_id=self.user_id,
                portfolio=portfolio,
                trade_logger=trade_logger,
                executor=executor,
            )
            # Bind the tick to the strategy first so it may issue orders,
            # then let existing orders try to fill against this same quote.
            self._bind_managers(order_mgr, portfolio)
            try:
                await self._strategy.on_tick(quote)  # type: ignore[union-attr]
            except Exception:  # pragma: no cover
                logger.exception(
                    "strategy_tick_failed",
                    extra={"symbol": quote.symbol, "price": float(quote.price)},
                )

            reports = await order_mgr.on_quote(quote)

            # Mark-to-market so unrealized P&L reads well from the API.
            await portfolio.mark_prices(self.engine_session_id, [(quote.symbol, quote.price)])

            # Update session heartbeat + P&L.
            engine_session = await db.get(EngineSession, self.engine_session_id)
            if engine_session is not None:
                engine_session.last_heartbeat_at = datetime.now(timezone.utc)
                engine_session.realized_pnl = float(engine_session.realized_pnl) + sum(
                    r.realized_pnl_delta for r in reports
                )
                engine_session.day_pnl = float(engine_session.realized_pnl)
            await db.commit()

            # Notify strategy of any order-state transitions from fills.
            for r in reports:
                try:
                    await self._strategy.on_order_update(r.order)  # type: ignore[union-attr]
                except Exception:  # pragma: no cover
                    logger.exception("strategy_on_order_update_failed")

    # ---- context-injected callables (rebound per tick) ------------------

    def _build_executor(self):
        """Return the right ``Executor`` implementation for this session."""
        if self.execution_mode == "live" and self._broker_adapter is not None and self._broker_account_id:
            from app.engine.orders.live_executor import LiveBrokerExecutor

            return LiveBrokerExecutor(
                self._broker_adapter, broker_account_id=self._broker_account_id
            )
        # Default: paper.
        from app.engine.orders.paper_executor_adapter import PaperExecutorAdapter

        return PaperExecutorAdapter()

    def _bind_managers(self, order_mgr: OrderManager, portfolio: PortfolioManager) -> None:
        self._current_order_mgr = order_mgr
        self._current_portfolio = portfolio

    async def _ctx_place_order(self, intent: OrderIntentRequest) -> PaperOrder:
        # RISK CHECK
        price_hint = intent.price
        if price_hint is None:
            snap = await self._current_portfolio.pnl_snapshot(
                self.engine_session_id, last_prices=self._last_prices
            )
            price_hint = self._last_prices.get(intent.symbol) or 0.0
        else:
            price_hint = float(price_hint)

        engine_session = await self._current_order_mgr.session.get(
            EngineSession, self.engine_session_id
        )
        initial_capital = float(engine_session.initial_capital) if engine_session else 0.0
        day_pnl = float(engine_session.day_pnl) if engine_session else 0.0
        current_capital = initial_capital + day_pnl
        positions = await self._current_portfolio.positions_snapshot(self.engine_session_id)

        # An intent is an "exit" if it reduces existing position on that symbol.
        existing = positions.get(intent.symbol, 0.0)
        is_exit = (existing > 0 and intent.side == OrderSide.SELL) or (
            existing < 0 and intent.side == OrderSide.BUY
        )

        decision: RiskDecision = self._risk_engine.check(
            OrderIntent(
                symbol=intent.symbol,
                side=intent.side.value,
                quantity=intent.quantity,
                price=price_hint,
                is_exit=is_exit,
            ),
            SessionSnapshot(
                initial_capital=initial_capital,
                current_capital=current_capital,
                day_pnl=day_pnl,
                positions=positions,
            ),
        )
        if not decision.allowed:
            # Persist a REJECTED order for auditability.
            from app.models.engine import OrderStatus  # local import to avoid cycle

            order = PaperOrder(
                user_id=self.user_id,
                engine_session_id=self.engine_session_id,
                symbol=intent.symbol,
                exchange=intent.exchange,
                side=intent.side,
                order_type=intent.order_type,
                product=intent.product,
                quantity=intent.quantity,
                price=intent.price,
                trigger_price=intent.trigger_price,
                stop_loss=intent.stop_loss,
                target_price=intent.target_price,
                status=OrderStatus.REJECTED,
                strategy_name=intent.strategy_name,
                tag=intent.tag,
                rejection_reason=decision.reason,
                placed_at=datetime.now(timezone.utc),
            )
            self._current_order_mgr.session.add(order)
            await self._current_order_mgr.session.flush()
            logger.info(
                "paper_order_rejected_by_risk",
                extra={"symbol": intent.symbol, "reason": decision.reason},
            )
            return order

        if decision.adjusted_quantity is not None:
            intent.quantity = decision.adjusted_quantity
        return await self._current_order_mgr.place(intent)

    async def _ctx_modify_order(self, order_id: str, **changes) -> PaperOrder:
        return await self._current_order_mgr.modify(order_id, **changes)

    async def _ctx_cancel_order(self, order_id: str) -> PaperOrder:
        return await self._current_order_mgr.cancel(order_id)

    async def _ctx_positions(self) -> dict[str, float]:
        return await self._current_portfolio.positions_snapshot(self.engine_session_id)

    async def _ctx_last_price(self, symbol: str) -> Optional[float]:
        return self._last_prices.get(symbol)


# ---- utility: hydrate open sessions on boot -------------------------------


async def list_sessions_by_status(
    session_factory: async_sessionmaker,
    status: EngineSessionStatus,
) -> list[str]:
    async with session_factory() as db:
        stmt = select(EngineSession.id).where(EngineSession.status == status)
        rows = (await db.execute(stmt)).scalars().all()
        return list(rows)
