"""Trading endpoints.

Matches the Module 2 spec exactly:

- POST /api/v1/trading/start
- POST /api/v1/trading/stop
- POST /api/v1/trading/manual-order
- GET  /api/v1/trading/status
- GET  /api/v1/positions
- GET  /api/v1/orders
- GET  /api/v1/pnl
- GET  /api/v1/trades

All are scoped to the authenticated user.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession
from app.core.exceptions import ForbiddenError, InvalidOrderError, SessionNotFoundError
from app.engine.logger import TradeLogger
from app.engine.portfolio.manager import PortfolioManager
from app.engine.orders.manager import OrderIntentRequest, OrderManager
from app.engine.orders.paper_executor_adapter import PaperExecutorAdapter
from app.engine.market_data.upstox_instruments import resolve_instrument_key
from app.engine.strategy.manager import manager as strategy_manager
from app.engine.strategy.registry import list_strategies
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderProduct,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperOrder,
    PaperPosition,
)
from app.schemas.common import Message, PaginatedResponse
from app.schemas.trading import (
    EngineSessionRead,
    ManualPaperOrderRequest,
    PaperOrderRead,
    PaperPositionRead,
    PaperTradeRead,
    PnLResponse,
    StartTradingRequest,
    StopTradingRequest,
    TradingStatusResponse,
)
from sqlalchemy import select

# ---- Sub-routers so we can share this file across two prefixes ----------

trading_router = APIRouter()
top_level_router = APIRouter()


# ---- /trading/start ------------------------------------------------------


@trading_router.post(
    "/start",
    response_model=EngineSessionRead,
    summary="Start a trading engine session for the current user",
)
async def start_trading(
    payload: StartTradingRequest,
    current_user: CurrentUser,
) -> EngineSessionRead:
    session = await strategy_manager.start(
        user_id=current_user.id,
        strategy_name=payload.strategy_name,
        strategy_id=payload.strategy_id,
        symbols=payload.symbols,
        params=payload.params,
        risk_config=payload.risk_config.model_dump(exclude_none=True),
        initial_capital=payload.initial_capital,
        provider_name=payload.provider,
        execution_mode=payload.execution_mode,
        broker_account_id=payload.broker_account_id,
    )
    return EngineSessionRead.model_validate(session)


# ---- /trading/manual-order -----------------------------------------------


def _normalize_symbol_for_match(symbol: str) -> str:
    return " ".join(symbol.strip().upper().split())


def _session_symbol_match(requested: str, configured: str) -> bool:
    """Match user-facing aliases without inventing instrument mappings."""
    if _normalize_symbol_for_match(requested) == _normalize_symbol_for_match(configured):
        return True
    requested_key = resolve_instrument_key(requested)
    configured_key = resolve_instrument_key(configured)
    return requested_key is not None and configured_key is not None and requested_key == configured_key


@trading_router.post(
    "/manual-order",
    response_model=PaperOrderRead,
    status_code=201,
    summary="Place an authenticated manual paper market order",
)
async def manual_paper_order(
    payload: ManualPaperOrderRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> PaperOrderRead:
    """Create a mobile manual order through the existing paper order engine.

    This route is intentionally incapable of live execution: it requires a
    running PAPER EngineSession and constructs a PaperExecutorAdapter directly.
    The order remains pending until the normal quote-driven runner fills it.
    """
    engine_session = await session.get(EngineSession, payload.session_id)
    if engine_session is None or engine_session.user_id != current_user.id:
        # Do not reveal whether another user's session exists.
        raise SessionNotFoundError()

    if engine_session.execution_mode != ExecutionMode.PAPER:
        raise ForbiddenError(
            "Manual orders require a paper trading session",
            code="manual_paper_order_requires_paper_session",
        )

    if engine_session.status != EngineSessionStatus.RUNNING:
        raise InvalidOrderError(
            "Manual paper orders require a running trading session",
            code="manual_paper_order_session_not_running",
            status_code=409,
        )

    # The process-local runner is the component that consumes quotes and fills
    # pending paper orders. Refuse to create orders that would otherwise sit
    # indefinitely after a restart or in a process that does not own the runner.
    if not strategy_manager.is_running(engine_session.id):
        raise InvalidOrderError(
            "The paper trading engine is not active for this session",
            code="manual_paper_order_engine_not_active",
            status_code=409,
        )

    configured_symbols = list(engine_session.symbols or [])
    matched_symbol = next(
        (configured for configured in configured_symbols if _session_symbol_match(payload.symbol, configured)),
        None,
    )
    if matched_symbol is None:
        raise InvalidOrderError(
            "Symbol is not configured for this trading session",
            code="manual_paper_order_symbol_not_in_session",
            details={"symbol": payload.symbol},
            status_code=422,
        )

    if payload.order_type != OrderType.MARKET:
        raise InvalidOrderError(
            "Manual mobile orders support MARKET orders only",
            code="manual_paper_order_market_only",
            status_code=422,
        )

    # The mobile contract is deliberately limited to MIS paper orders.
    # Keeping this explicit prevents a future product enum expansion from
    # silently widening the endpoint's execution contract.
    if payload.product != OrderProduct.MIS:
        raise InvalidOrderError(
            "Manual mobile orders support MIS product only",
            code="manual_paper_order_mis_only",
            status_code=422,
        )

    # Defensive validation in addition to Pydantic so future schema changes
    # cannot accidentally make this endpoint live-capable.
    if payload.side not in (OrderSide.BUY, OrderSide.SELL):
        raise InvalidOrderError(
            "Side must be BUY or SELL",
            code="manual_paper_order_invalid_side",
            status_code=422,
        )
    if payload.quantity <= 0:
        raise InvalidOrderError(
            "Quantity must be positive",
            code="manual_paper_order_invalid_quantity",
            status_code=422,
        )

    # Deliberately instantiate ONLY the paper executor. No broker adapter,
    # broker account, credential lookup, or LiveExecutor is reachable here.
    order_manager = OrderManager(
        session,
        engine_session_id=engine_session.id,
        user_id=current_user.id,
        portfolio=PortfolioManager(session),
        trade_logger=TradeLogger(session),
        executor=PaperExecutorAdapter(),
    )

    order = await order_manager.place(
        OrderIntentRequest(
            symbol=matched_symbol,
            side=payload.side,
            quantity=float(payload.quantity),
            order_type=OrderType.MARKET,
            product=payload.product,
            price=None,
            trigger_price=payload.trigger_price,
            stop_loss=float(payload.stop_loss) if payload.stop_loss is not None else None,
            target_price=float(payload.target_price) if payload.target_price is not None else None,
            exchange=payload.exchange,
            tag=payload.tag,
            strategy_name=engine_session.strategy_name,
        )
    )
    await session.commit()
    await session.refresh(order)
    return PaperOrderRead.model_validate(order)


# ---- /trading/stop -------------------------------------------------------


@trading_router.post(
    "/stop",
    response_model=Message,
    summary="Stop one session (by id) or all of the user's running sessions",
)
async def stop_trading(
    payload: StopTradingRequest,
    current_user: CurrentUser,
) -> Message:
    if payload.session_id is not None:
        await strategy_manager.stop(payload.session_id, user_id=current_user.id)
        return Message(message=f"Session {payload.session_id} stopped")
    stopped = await strategy_manager.stop_all_for_user(current_user.id)
    return Message(message=f"Stopped {len(stopped)} session(s)")


# ---- /trading/status -----------------------------------------------------


@trading_router.get(
    "/status",
    response_model=TradingStatusResponse,
    summary="List the user's engine sessions and available strategies",
)
async def status(current_user: CurrentUser) -> TradingStatusResponse:
    sessions = await strategy_manager.status(current_user.id)
    return TradingStatusResponse(
        sessions=[EngineSessionRead.model_validate(s) for s in sessions],
        registered_strategies=list_strategies(),
    )


# ---- Helper: resolve session_id (default = the user's latest session) ----


async def _resolve_session_id(
    db, user_id: str, session_id: str | None
) -> str:
    if session_id:
        sess = await db.get(EngineSession, session_id)
        if sess is None or sess.user_id != user_id:
            raise SessionNotFoundError()
        return session_id
    stmt = (
        select(EngineSession.id)
        .where(EngineSession.user_id == user_id)
        .order_by(EngineSession.created_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise SessionNotFoundError("No engine sessions exist yet")
    return row


# ---- /positions ----------------------------------------------------------


@top_level_router.get(
    "/positions",
    response_model=PaginatedResponse[PaperPositionRead],
    summary="List paper positions for a session (defaults to the latest)",
)
async def list_positions(
    current_user: CurrentUser,
    session: DBSession,
    session_id: str | None = Query(None),
    only_open: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
) -> PaginatedResponse[PaperPositionRead]:
    sid = await _resolve_session_id(session, current_user.id, session_id)
    stmt = select(PaperPosition).where(PaperPosition.engine_session_id == sid)
    if only_open:
        stmt = stmt.where(PaperPosition.net_quantity != 0)
    total = len((await session.execute(stmt)).scalars().all())
    offset = (page - 1) * page_size
    stmt2 = stmt.order_by(PaperPosition.updated_at.desc()).offset(offset).limit(page_size)
    items = (await session.execute(stmt2)).scalars().all()
    return PaginatedResponse[PaperPositionRead](
        items=[PaperPositionRead.model_validate(x) for x in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---- /orders -------------------------------------------------------------


@top_level_router.get(
    "/orders",
    response_model=PaginatedResponse[PaperOrderRead],
    summary="List paper orders",
)
async def list_orders(
    current_user: CurrentUser,
    session: DBSession,
    session_id: str | None = Query(None),
    status_filter: OrderStatus | None = Query(None, alias="status"),
    symbol: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
) -> PaginatedResponse[PaperOrderRead]:
    sid = await _resolve_session_id(session, current_user.id, session_id)
    stmt = select(PaperOrder).where(PaperOrder.engine_session_id == sid)
    if status_filter is not None:
        stmt = stmt.where(PaperOrder.status == status_filter)
    if symbol is not None:
        stmt = stmt.where(PaperOrder.symbol == symbol)
    total = len((await session.execute(stmt)).scalars().all())
    offset = (page - 1) * page_size
    stmt2 = stmt.order_by(PaperOrder.created_at.desc()).offset(offset).limit(page_size)
    items = (await session.execute(stmt2)).scalars().all()
    return PaginatedResponse[PaperOrderRead](
        items=[PaperOrderRead.model_validate(x) for x in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---- /pnl ----------------------------------------------------------------


@top_level_router.get(
    "/pnl",
    response_model=PnLResponse,
    summary="P&L snapshot for a session (realized + unrealized + counts)",
)
async def pnl(
    current_user: CurrentUser,
    session: DBSession,
    session_id: str | None = Query(None),
) -> PnLResponse:
    sid = await _resolve_session_id(session, current_user.id, session_id)
    snap = await PortfolioManager(session).pnl_snapshot(sid)
    return PnLResponse(
        engine_session_id=sid,
        realized=snap.realized,
        unrealized=snap.unrealized,
        day_pnl=snap.day_pnl,
        total=snap.total,
        open_positions=snap.open_positions,
        winning_trades=snap.winning_trades,
        losing_trades=snap.losing_trades,
    )


# ---- /trades -------------------------------------------------------------


@top_level_router.get(
    "/trades",
    response_model=PaginatedResponse[PaperTradeRead],
    summary="Paper trade execution log for a session",
)
async def list_trades(
    current_user: CurrentUser,
    session: DBSession,
    session_id: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
) -> PaginatedResponse[PaperTradeRead]:
    sid = await _resolve_session_id(session, current_user.id, session_id)
    offset = (page - 1) * page_size
    items = await TradeLogger(session).list_trades(sid, offset=offset, limit=page_size)
    from app.models.engine import PaperTrade  # local

    total = len((await session.execute(select(PaperTrade).where(PaperTrade.engine_session_id == sid))).scalars().all())
    return PaginatedResponse[PaperTradeRead](
        items=[PaperTradeRead.model_validate(x) for x in items],
        total=total,
        page=page,
        page_size=page_size,
    )
