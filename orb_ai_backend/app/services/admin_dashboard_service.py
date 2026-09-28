"""Admin dashboard snapshot service.

Computes the same payload as ``GET /api/v1/admin/system/health`` plus a
compact list of running engine sessions, recent orders, and today's paper
P&L. Used by both the REST ``/system/health`` endpoint (via the router
composing this) and the ``WS /admin`` live-stream (Module 7).

Kept as a pure function of an ``AsyncSession`` so it stays trivial to test
and so the WebSocket handler can call it in a tight loop without holding a
long-lived DB session.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.core.redis import get_redis
from app.engine.market_data.historical_base import list_historical
from app.engine.market_data.registry import _registry as _mdp_registry  # noqa: SLF001
from app.engine.strategy.manager import manager as strategy_manager
from app.engine.strategy.registry import _registry as _strategy_registry  # noqa: SLF001
from app.models.backtest import BacktestRun, BacktestStatus
from app.models.broker import BrokerAccount
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    OrderStatus,
    PaperOrder,
    PaperTrade,
)
from app.models.user import User, UserRole


async def _redis_state() -> str:
    try:
        r = await get_redis()
        if r is None:
            return "disabled"
        try:
            await r.ping()
            return "ok"
        except Exception:
            return "error"
    except Exception:  # pragma: no cover
        return "error"


async def compute_system_health(session: AsyncSession) -> dict[str, Any]:
    """Return the same shape as ``AdminSystemHealth`` but as a plain dict.

    Used by both the REST endpoint and the admin WebSocket broadcaster.
    """
    db_ok = "ok"
    try:
        await session.execute(select(func.count()).select_from(User))
    except Exception:  # pragma: no cover
        db_ok = "error"

    redis_state = await _redis_state()

    users_total = int(
        (await session.execute(select(func.count(User.id)))).scalar_one() or 0
    )
    users_active = int(
        (await session.execute(
            select(func.count(User.id)).where(User.is_active.is_(True))
        )).scalar_one() or 0
    )
    users_admins = int(
        (await session.execute(
            select(func.count(User.id)).where(User.role == UserRole.ADMIN)
        )).scalar_one() or 0
    )
    ba_total = int(
        (await session.execute(select(func.count(BrokerAccount.id)))).scalar_one() or 0
    )
    ba_active = int(
        (await session.execute(
            select(func.count(BrokerAccount.id)).where(BrokerAccount.is_active.is_(True))
        )).scalar_one() or 0
    )
    sess_total = int(
        (await session.execute(select(func.count(EngineSession.id)))).scalar_one() or 0
    )
    sess_running = int(
        (await session.execute(
            select(func.count(EngineSession.id)).where(
                EngineSession.status == EngineSessionStatus.RUNNING
            )
        )).scalar_one() or 0
    )
    bt_total = int(
        (await session.execute(select(func.count(BacktestRun.id)))).scalar_one() or 0
    )
    bt_done = int(
        (await session.execute(
            select(func.count(BacktestRun.id)).where(
                BacktestRun.status == BacktestStatus.COMPLETE
            )
        )).scalar_one() or 0
    )

    return {
        "status": "ok" if db_ok == "ok" else "degraded",
        "database": db_ok,
        "redis": redis_state,
        "engine_sessions_running": sess_running,
        "engine_sessions_total": sess_total,
        "users_total": users_total,
        "users_active": users_active,
        "users_admins": users_admins,
        "broker_accounts_total": ba_total,
        "broker_accounts_active": ba_active,
        "backtests_total": bt_total,
        "backtests_completed": bt_done,
        "market_data_providers": sorted(_mdp_registry.keys()),
        "historical_providers": list_historical(),
        "registered_strategies": sorted(_strategy_registry.keys()),
        "api_version": app_settings.APP_VERSION,
        "now": datetime.now(timezone.utc),
    }


async def compute_daily_pnl(session: AsyncSession) -> float:
    """Approximate today's realised paper P&L.

    Uses the same rule the mobile client uses:
    ``+price*qty`` for sells, ``-price*qty`` for buys, minus any realised
    P&L deltas already booked. This is deliberately simple — the engine's
    per-session ``day_pnl`` is authoritative.
    """
    today = date.today()
    stmt = select(EngineSession.day_pnl)
    rows = (await session.execute(stmt)).scalars().all()
    # ``EngineSession.day_pnl`` is a Decimal; float() safely handles None.
    return float(sum((r or 0) for r in rows))


async def compute_running_sessions(session: AsyncSession, limit: int = 25) -> list[dict[str, Any]]:
    """Return a compact list of the most recently touched running sessions."""
    stmt = (
        select(EngineSession)
        .where(EngineSession.status == EngineSessionStatus.RUNNING)
        .order_by(EngineSession.last_heartbeat_at.desc().nulls_last(), EngineSession.started_at.desc().nulls_last())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "user_id": r.user_id,
                "strategy_name": r.strategy_name,
                "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                "execution_mode": (
                    r.execution_mode.value if hasattr(r.execution_mode, "value") else str(r.execution_mode)
                ),
                "symbols": list(r.symbols or []),
                "initial_capital": float(r.initial_capital or 0),
                "day_pnl": float(r.day_pnl or 0),
                "realized_pnl": float(r.realized_pnl or 0),
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "last_heartbeat_at": r.last_heartbeat_at.isoformat() if r.last_heartbeat_at else None,
                "in_process": strategy_manager.is_running(r.id),
            }
        )
    return out


async def compute_recent_orders(session: AsyncSession, limit: int = 10) -> list[dict[str, Any]]:
    """Return the newest N paper orders across all users."""
    stmt = select(PaperOrder).order_by(PaperOrder.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "engine_session_id": r.engine_session_id,
            "symbol": r.symbol,
            "side": r.side.value if hasattr(r.side, "value") else str(r.side),
            "order_type": r.order_type.value if hasattr(r.order_type, "value") else str(r.order_type),
            "quantity": float(r.quantity),
            "filled_quantity": float(r.filled_quantity or 0),
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def compute_order_counters(session: AsyncSession) -> dict[str, int]:
    """Counts of open / filled / cancelled orders (for KPI cards)."""

    async def _count(status) -> int:
        return int(
            (await session.execute(
                select(func.count(PaperOrder.id)).where(PaperOrder.status == status)
            )).scalar_one() or 0
        )

    return {
        "orders_pending": await _count(OrderStatus.PENDING) + await _count(OrderStatus.OPEN),
        "orders_filled": await _count(OrderStatus.FILLED),
        "orders_partial": await _count(OrderStatus.PARTIALLY_FILLED),
        "orders_cancelled": await _count(OrderStatus.CANCELLED),
        "orders_rejected": await _count(OrderStatus.REJECTED),
    }


async def compute_admin_snapshot(session: AsyncSession) -> dict[str, Any]:
    """Full admin-dashboard snapshot payload used by the ``/ws/admin`` stream."""
    health = await compute_system_health(session)
    return {
        "health": health,
        "running_sessions": await compute_running_sessions(session),
        "recent_orders": await compute_recent_orders(session, limit=10),
        "order_counters": await compute_order_counters(session),
        "daily_pnl": await compute_daily_pnl(session),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
