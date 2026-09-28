from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import AdminUser, DBSession
from app.schemas.trading_mode import (
    AutoRevertRead,
    AutoRevertUpdate,
    TradingModeRead,
    TradingModeUpdate,
)
from app.services.trading_mode_service import TradingModeService

router = APIRouter()


@router.get("", response_model=TradingModeRead, summary="Read global trading mode")
async def get_trading_mode(_admin: AdminUser, session: DBSession) -> TradingModeRead:
    return TradingModeRead(**(await TradingModeService(session).snapshot()))


@router.post("", response_model=TradingModeRead, summary="Change global trading mode")
async def set_trading_mode(
    payload: TradingModeUpdate,
    request: Request,
    admin: AdminUser,
    session: DBSession,
) -> TradingModeRead:
    snapshot = await TradingModeService(session).set_mode(
        target=payload.mode,
        operator=admin,
        confirmation=payload.confirmation,
        reason=payload.reason,
        ip_address=request.client.host if request.client else None,
    )
    await session.commit()
    return TradingModeRead(**snapshot)


@router.get(
    "/auto-revert",
    response_model=AutoRevertRead,
    summary="Read automatic PAPER-revert configuration",
)
async def get_auto_revert(_admin: AdminUser, session: DBSession) -> AutoRevertRead:
    return AutoRevertRead(**(await TradingModeService(session).get_auto_revert()))


@router.post(
    "/auto-revert",
    response_model=AutoRevertRead,
    summary="Configure automatic PAPER-revert",
)
async def set_auto_revert(
    payload: AutoRevertUpdate,
    request: Request,
    admin: AdminUser,
    session: DBSession,
) -> AutoRevertRead:
    updated = await TradingModeService(session).set_auto_revert(
        operator=admin,
        enabled=payload.enabled,
        at_time=payload.at_time,
        timezone_name=payload.timezone,
        ip_address=request.client.host if request.client else None,
    )
    await session.commit()
    return AutoRevertRead(**updated)
