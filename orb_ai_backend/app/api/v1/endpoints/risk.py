"""Risk endpoints (Module 8)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentUser, DBSession
from app.services.risk_service import RiskService

router = APIRouter()


@router.get("/dashboard", summary="Full risk dashboard")
async def dashboard(current_user: CurrentUser, session: DBSession) -> dict[str, Any]:
    return await RiskService(session).dashboard(current_user.id)


@router.get("/exposure/symbol", summary="Exposure by symbol")
async def exposure_symbol(current_user: CurrentUser, session: DBSession) -> list[dict[str, Any]]:
    return await RiskService(session).exposure_by_symbol(current_user.id)


@router.get("/exposure/broker", summary="Exposure by broker")
async def exposure_broker(current_user: CurrentUser, session: DBSession) -> list[dict[str, Any]]:
    return await RiskService(session).exposure_by_broker(current_user.id)


@router.get("/daily", summary="Daily risk snapshot")
async def daily(current_user: CurrentUser, session: DBSession) -> dict[str, Any]:
    return await RiskService(session).daily_risk(current_user.id)


@router.get("/drawdown", summary="Maximum drawdown")
async def drawdown(current_user: CurrentUser, session: DBSession) -> dict[str, Any]:
    return await RiskService(session).max_drawdown(current_user.id)


@router.get("/margin", summary="Margin utilisation")
async def margin(current_user: CurrentUser, session: DBSession) -> dict[str, Any]:
    return await RiskService(session).margin_utilisation(current_user.id)


@router.get("/position-sizing", summary="Position sizing analysis")
async def position_sizing(current_user: CurrentUser, session: DBSession) -> list[dict[str, Any]]:
    return await RiskService(session).position_sizing(current_user.id)
