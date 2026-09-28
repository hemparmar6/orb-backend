"""Admin endpoints (Module 7).

All endpoints require ``UserRole.ADMIN`` via the existing
``require_admin`` dependency — reuses the mobile app's JWT auth flow.
Read-only endpoints support search / filter / paginate. Mutations are
recorded to the audit log.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, or_, select

from app.api.deps import AdminUser, DBSession
from app.core.config import settings as app_settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.redis import get_redis
from app.engine.market_data.historical_base import list_historical
from app.engine.market_data.registry import _registry as _mdp_registry  # noqa: SLF001
from app.engine.strategy.manager import manager as strategy_manager
from app.engine.strategy.registry import _registry as _strategy_registry  # noqa: SLF001
from app.models.backtest import BacktestRun, BacktestStatus
from app.models.broker import BrokerAccount, BrokerType
from app.models.engine import (
    EngineSession,
    EngineSessionStatus,
    ExecutionMode,
    OrderStatus,
    PaperOrder,
    PaperTrade,
)
from app.models.strategy import Strategy, StrategyStatus
from app.models.user import User, UserRole
from app.repositories.audit_repository import AuditRepository
from app.schemas.admin import (
    AdminAuditLogRead,
    AdminBacktestRead,
    AdminBrokerAccountRead,
    AdminOrderRead,
    AdminRiskDefaults,
    AdminSessionRead,
    AdminStrategyRead,
    AdminStrategyRegistryEntry,
    AdminSystemHealth,
    AdminTradeRead,
    AdminUserRead,
    AdminUserUpdate,
)
from app.schemas.common import Message, PaginatedResponse
from app.services.audit_service import AuditService

router = APIRouter()

# In-memory risk defaults singleton — a real deployment can back this with a
# `system_settings` table. Kept in-memory here to avoid a schema change.
_RISK_DEFAULTS: dict[str, Any] = {
    "max_daily_loss": None,
    "max_position_size": None,
    "max_risk_per_trade_pct": None,
    "trading_session_start": app_settings.TRADING_SESSION_START,
    "trading_session_end": app_settings.TRADING_SESSION_END,
    "timezone": app_settings.TRADING_SESSION_TIMEZONE,
}


# ============================================================ users


@router.get(
    "/users",
    response_model=PaginatedResponse[AdminUserRead],
    summary="List all users (admin)",
)
async def list_users(
    _admin: AdminUser,
    session: DBSession,
    q: Optional[str] = Query(None, description="Search email / full_name (case-insensitive)"),
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[AdminUserRead]:
    stmt = select(User)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(User.email).like(needle), func.lower(User.full_name).like(needle))
        )
    if role is not None:
        stmt = stmt.where(User.role == role)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    offset = (page - 1) * page_size
    rows = (await session.execute(
        stmt.order_by(User.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all()
    return PaginatedResponse(
        items=[AdminUserRead.model_validate(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get(
    "/users/{user_id}",
    response_model=AdminUserRead,
    summary="Get a user by id (admin)",
)
async def get_user(user_id: str, _admin: AdminUser, session: DBSession) -> AdminUserRead:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="user_not_found")
    return AdminUserRead.model_validate(user)


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserRead,
    summary="Update a user (role / active / verified) — admin",
)
async def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    request: Request,
    admin: AdminUser,
    session: DBSession,
) -> AdminUserRead:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="user_not_found")

    # Prevent an admin from locking themselves out.
    if user.id == admin.id:
        if payload.role is not None and payload.role != UserRole.ADMIN:
            raise BadRequestError(
                "Admin cannot demote themselves", code="self_demote_forbidden"
            )
        if payload.is_active is False:
            raise BadRequestError(
                "Admin cannot deactivate themselves",
                code="self_deactivate_forbidden",
            )

    changed: dict[str, Any] = {}
    if payload.role is not None and payload.role != user.role:
        changed["role"] = {"from": user.role.value, "to": payload.role.value}
        user.role = payload.role
    if payload.is_active is not None and payload.is_active != user.is_active:
        changed["is_active"] = {"from": user.is_active, "to": payload.is_active}
        user.is_active = payload.is_active
    if payload.is_verified is not None and payload.is_verified != user.is_verified:
        changed["is_verified"] = {"from": user.is_verified, "to": payload.is_verified}
        user.is_verified = payload.is_verified
    if payload.full_name is not None and payload.full_name != user.full_name:
        changed["full_name"] = {"from": user.full_name, "to": payload.full_name}
        user.full_name = payload.full_name

    if changed:
        await AuditService(session).record(
            action="user.update",
            target_type="user",
            target_id=user.id,
            actor=admin,
            details=changed,
            ip_address=_client_ip(request),
        )
    await session.commit()
    await session.refresh(user)
    return AdminUserRead.model_validate(user)


# ==================================================== broker accounts


@router.get(
    "/broker-accounts",
    response_model=PaginatedResponse[AdminBrokerAccountRead],
    summary="List all broker accounts (admin)",
)
async def list_broker_accounts(
    _admin: AdminUser,
    session: DBSession,
    user_id: Optional[str] = None,
    broker_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[AdminBrokerAccountRead]:
    stmt = select(BrokerAccount)
    if user_id:
        stmt = stmt.where(BrokerAccount.user_id == user_id)
    if broker_type:
        try:
            stmt = stmt.where(BrokerAccount.broker_type == BrokerType(broker_type))
        except ValueError:
            stmt = stmt.where(BrokerAccount.broker_type == broker_type)
    if is_active is not None:
        stmt = stmt.where(BrokerAccount.is_active.is_(is_active))
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    offset = (page - 1) * page_size
    rows = (await session.execute(
        stmt.order_by(BrokerAccount.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all()
    return PaginatedResponse(
        items=[
            AdminBrokerAccountRead(
                id=r.id,
                user_id=r.user_id,
                broker_type=r.broker_type.value if hasattr(r.broker_type, "value") else str(r.broker_type),
                alias=r.alias or "",
                is_active=r.is_active,
                last_used_at=r.last_used_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.delete(
    "/broker-accounts/{account_id}",
    response_model=Message,
    summary="Disconnect a broker account (admin)",
)
async def delete_broker_account(
    account_id: str,
    request: Request,
    admin: AdminUser,
    session: DBSession,
) -> Message:
    acct = await session.get(BrokerAccount, account_id)
    if acct is None:
        raise NotFoundError("Broker account not found", code="broker_account_not_found")
    owner_id = acct.user_id
    broker_type = (
        acct.broker_type.value if hasattr(acct.broker_type, "value") else str(acct.broker_type)
    )
    await session.delete(acct)
    await AuditService(session).record(
        action="broker_account.delete",
        target_type="broker_account",
        target_id=account_id,
        actor=admin,
        details={"user_id": owner_id, "broker_type": broker_type},
        ip_address=_client_ip(request),
    )
    await session.commit()
    return Message(message="Broker account disconnected.")


# ==================================================== engine sessions


@router.get(
    "/sessions",
    response_model=PaginatedResponse[AdminSessionRead],
    summary="List engine sessions across all users (admin)",
)
async def list_sessions(
    _admin: AdminUser,
    session: DBSession,
    user_id: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    execution_mode: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[AdminSessionRead]:
    stmt = select(EngineSession)
    if user_id:
        stmt = stmt.where(EngineSession.user_id == user_id)
    if status_filter:
        try:
            stmt = stmt.where(EngineSession.status == EngineSessionStatus(status_filter))
        except ValueError:
            stmt = stmt.where(EngineSession.status == status_filter)
    if execution_mode:
        try:
            stmt = stmt.where(EngineSession.execution_mode == ExecutionMode(execution_mode))
        except ValueError:
            stmt = stmt.where(EngineSession.execution_mode == execution_mode)
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    offset = (page - 1) * page_size
    rows = (await session.execute(
        stmt.order_by(EngineSession.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all()
    return PaginatedResponse(
        items=[_engine_session_to_dto(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post(
    "/sessions/{session_id}/stop",
    response_model=AdminSessionRead,
    summary="Force-stop an engine session (admin)",
)
async def stop_session(
    session_id: str,
    request: Request,
    admin: AdminUser,
    session: DBSession,
) -> AdminSessionRead:
    sess = await session.get(EngineSession, session_id)
    if sess is None:
        raise NotFoundError("Session not found", code="session_not_found")
    if sess.status == EngineSessionStatus.STOPPED:
        return _engine_session_to_dto(sess)

    # If the runner is live in this process, stop it too.
    try:
        if strategy_manager.is_running(session_id):
            await strategy_manager.stop(session_id)
            await session.refresh(sess)
    except Exception:
        # If the in-process runner call fails, still mark the row STOPPED
        # so the audit trail is truthful.
        pass

    if sess.status != EngineSessionStatus.STOPPED:
        sess.status = EngineSessionStatus.STOPPED
        sess.stopped_at = datetime.now(timezone.utc)

    await AuditService(session).record(
        action="engine_session.stop",
        target_type="engine_session",
        target_id=session_id,
        actor=admin,
        details={"user_id": sess.user_id},
        ip_address=_client_ip(request),
    )
    await session.commit()
    await session.refresh(sess)
    return _engine_session_to_dto(sess)


# ==================================================== strategies


@router.get(
    "/strategies",
    response_model=PaginatedResponse[AdminStrategyRead],
    summary="List user-created strategies (admin)",
)
async def list_strategies(
    _admin: AdminUser,
    session: DBSession,
    user_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[AdminStrategyRead]:
    stmt = select(Strategy)
    if user_id:
        stmt = stmt.where(Strategy.user_id == user_id)
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    offset = (page - 1) * page_size
    rows = (await session.execute(
        stmt.order_by(Strategy.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all()

    items = [
        AdminStrategyRead(
            id=r.id,
            user_id=r.user_id,
            name=r.name,
            description=r.description,
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            is_public=bool(r.is_public),
            parameters=r.parameters or {},
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]
    return PaginatedResponse(
        items=items, total=int(total), page=page, page_size=page_size
    )


@router.get(
    "/strategies/registered",
    response_model=list[AdminStrategyRegistryEntry],
    summary="List strategy classes registered in this process (admin)",
)
async def list_registered_strategies(_admin: AdminUser) -> list[AdminStrategyRegistryEntry]:
    return [
        AdminStrategyRegistryEntry(
            name=name, class_name=cls.__name__, module=cls.__module__
        )
        for name, cls in sorted(_strategy_registry.items())
    ]


# ==================================================== backtests


@router.get(
    "/backtests",
    response_model=PaginatedResponse[AdminBacktestRead],
    summary="List backtest runs across all users (admin)",
)
async def list_backtests(
    _admin: AdminUser,
    session: DBSession,
    user_id: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    strategy_name: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[AdminBacktestRead]:
    stmt = select(BacktestRun)
    if user_id:
        stmt = stmt.where(BacktestRun.user_id == user_id)
    if status_filter:
        try:
            stmt = stmt.where(BacktestRun.status == BacktestStatus(status_filter))
        except ValueError:
            stmt = stmt.where(BacktestRun.status == status_filter)
    if strategy_name:
        stmt = stmt.where(BacktestRun.strategy_name == strategy_name)
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    offset = (page - 1) * page_size
    rows = (await session.execute(
        stmt.order_by(BacktestRun.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all()

    items = [
        AdminBacktestRead(
            id=r.id,
            user_id=r.user_id,
            strategy_name=r.strategy_name,
            strategy_id=r.strategy_id,
            symbols=list(r.symbols or []),
            start_date=r.start_date,
            end_date=r.end_date,
            initial_capital=float(r.initial_capital),
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            error_message=r.error_message,
            started_at=r.started_at,
            finished_at=r.finished_at,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return PaginatedResponse(
        items=items, total=int(total), page=page, page_size=page_size
    )


# ==================================================== orders / trades


@router.get(
    "/orders",
    response_model=PaginatedResponse[AdminOrderRead],
    summary="List paper orders across all users (admin)",
)
async def list_orders(
    _admin: AdminUser,
    session: DBSession,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    symbol: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[AdminOrderRead]:
    stmt = select(PaperOrder)
    if user_id:
        stmt = stmt.where(PaperOrder.user_id == user_id)
    if session_id:
        stmt = stmt.where(PaperOrder.engine_session_id == session_id)
    if symbol:
        stmt = stmt.where(PaperOrder.symbol == symbol)
    if status_filter:
        try:
            stmt = stmt.where(PaperOrder.status == OrderStatus(status_filter))
        except ValueError:
            stmt = stmt.where(PaperOrder.status == status_filter)
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    offset = (page - 1) * page_size
    rows = (await session.execute(
        stmt.order_by(PaperOrder.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all()

    def _o(r: PaperOrder) -> AdminOrderRead:
        return AdminOrderRead(
            id=r.id,
            user_id=r.user_id,
            engine_session_id=r.engine_session_id,
            symbol=r.symbol,
            side=r.side.value if hasattr(r.side, "value") else str(r.side),
            order_type=r.order_type.value if hasattr(r.order_type, "value") else str(r.order_type),
            quantity=float(r.quantity),
            limit_price=(float(r.limit_price) if r.limit_price is not None else None),
            trigger_price=(float(r.trigger_price) if r.trigger_price is not None else None),
            status=r.status.value if hasattr(r.status, "value") else str(r.status),
            filled_quantity=float(r.filled_quantity or 0),
            avg_fill_price=(float(r.avg_fill_price) if r.avg_fill_price is not None else None),
            broker_order_id=r.broker_order_id,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )

    return PaginatedResponse(
        items=[_o(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get(
    "/trades",
    response_model=PaginatedResponse[AdminTradeRead],
    summary="List paper trades (fills) across all users (admin)",
)
async def list_trades(
    _admin: AdminUser,
    session: DBSession,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    symbol: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[AdminTradeRead]:
    stmt = select(PaperTrade)
    if user_id:
        stmt = stmt.where(PaperTrade.user_id == user_id)
    if session_id:
        stmt = stmt.where(PaperTrade.engine_session_id == session_id)
    if symbol:
        stmt = stmt.where(PaperTrade.symbol == symbol)
    total = (await session.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one()
    offset = (page - 1) * page_size
    rows = (await session.execute(
        stmt.order_by(PaperTrade.created_at.desc()).offset(offset).limit(page_size)
    )).scalars().all()

    items = [
        AdminTradeRead(
            id=r.id,
            user_id=r.user_id,
            engine_session_id=r.engine_session_id,
            order_id=r.order_id,
            symbol=r.symbol,
            side=r.side.value if hasattr(r.side, "value") else str(r.side),
            quantity=float(r.quantity),
            price=float(r.price),
            fees=float(r.fees or 0),
            created_at=r.created_at,
        )
        for r in rows
    ]
    return PaginatedResponse(
        items=items, total=int(total), page=page, page_size=page_size
    )


# ==================================================== audit log


@router.get(
    "/audit-logs",
    response_model=PaginatedResponse[AdminAuditLogRead],
    summary="List admin audit-trail entries",
)
async def list_audit_logs(
    _admin: AdminUser,
    session: DBSession,
    actor_user_id: Optional[str] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[AdminAuditLogRead]:
    offset = (page - 1) * page_size
    items, total = await AuditRepository(session).list(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        offset=offset,
        limit=page_size,
    )
    return PaginatedResponse(
        items=[AdminAuditLogRead.model_validate(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ==================================================== system health


@router.get(
    "/system/health",
    response_model=AdminSystemHealth,
    summary="System health dashboard (admin)",
)
async def system_health(_admin: AdminUser, session: DBSession) -> AdminSystemHealth:
    # Delegate to the shared service so the REST payload matches what the
    # ``WS /admin`` snapshot streams (Module 7 live updates).
    from app.services.admin_dashboard_service import compute_system_health

    payload = await compute_system_health(session)
    return AdminSystemHealth(**payload)


# ==================================================== risk defaults


@router.get(
    "/risk-defaults",
    response_model=AdminRiskDefaults,
    summary="Global risk defaults (admin)",
)
async def get_risk_defaults(_admin: AdminUser) -> AdminRiskDefaults:
    return AdminRiskDefaults(**_RISK_DEFAULTS)


@router.put(
    "/risk-defaults",
    response_model=AdminRiskDefaults,
    summary="Update global risk defaults (admin)",
)
async def put_risk_defaults(
    payload: AdminRiskDefaults,
    request: Request,
    admin: AdminUser,
    session: DBSession,
) -> AdminRiskDefaults:
    changes: dict[str, Any] = {}
    for k, v in payload.model_dump(exclude_none=True).items():
        if _RISK_DEFAULTS.get(k) != v:
            changes[k] = {"from": _RISK_DEFAULTS.get(k), "to": v}
            _RISK_DEFAULTS[k] = v
    if changes:
        await AuditService(session).record(
            action="risk_defaults.update",
            target_type="risk_defaults",
            target_id=None,
            actor=admin,
            details=changes,
            ip_address=_client_ip(request),
        )
        await session.commit()
    return AdminRiskDefaults(**_RISK_DEFAULTS)


# ==================================================== helpers


def _engine_session_to_dto(r: EngineSession) -> AdminSessionRead:
    return AdminSessionRead(
        id=r.id,
        user_id=r.user_id,
        strategy_name=r.strategy_name,
        strategy_id=r.strategy_id,
        status=r.status.value if hasattr(r.status, "value") else str(r.status),
        execution_mode=(
            r.execution_mode.value if hasattr(r.execution_mode, "value") else str(r.execution_mode)
        ),
        broker_account_id=r.broker_account_id,
        symbols=list(r.symbols or []),
        initial_capital=float(r.initial_capital or 0),
        started_at=r.started_at,
        stopped_at=r.stopped_at,
        last_heartbeat_at=r.last_heartbeat_at,
        error_message=r.error_message,
        created_at=r.created_at,
    )


def _client_ip(request: Request) -> Optional[str]:
    if request.client:
        return request.client.host
    return None
