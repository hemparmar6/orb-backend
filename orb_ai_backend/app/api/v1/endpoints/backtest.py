"""Backtest endpoints.

- POST   /api/v1/backtest/run
- GET    /api/v1/backtest/history
- GET    /api/v1/backtest/{id}
- GET    /api/v1/backtest/{id}/results
- GET    /api/v1/backtest/{id}/export?format=json|csv
"""
from __future__ import annotations

import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response, StreamingResponse

from app.api.deps import CurrentUser, DBSession
from app.schemas.backtest import (
    BacktestResultsResponse,
    BacktestRunFull,
    BacktestRunRequest,
    BacktestRunSummary,
)
from app.schemas.common import PaginatedResponse
from app.services.backtest_service import BacktestService

router = APIRouter()


# ---- POST /backtest/run ---------------------------------------------------


@router.post(
    "/run",
    response_model=BacktestRunFull,
    summary="Run a backtest for the ORB strategy over a date range",
)
async def run_backtest(
    payload: BacktestRunRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> BacktestRunFull:
    run = await BacktestService(session).run(user_id=current_user.id, payload=payload)
    await session.commit()
    return BacktestRunFull.model_validate(run)


# ---- GET /backtest/history -----------------------------------------------


@router.get(
    "/history",
    response_model=PaginatedResponse[BacktestRunSummary],
    summary="List the current user's backtest runs (most recent first)",
)
async def list_backtests(
    current_user: CurrentUser,
    session: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse[BacktestRunSummary]:
    offset = (page - 1) * page_size
    items, total = await BacktestService(session).list_for_user(
        user_id=current_user.id, offset=offset, limit=page_size
    )
    return PaginatedResponse[BacktestRunSummary](
        items=[BacktestRunSummary.model_validate(x) for x in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---- GET /backtest/{id} --------------------------------------------------


@router.get(
    "/{run_id}",
    response_model=BacktestRunFull,
    summary="Fetch a single backtest run (full payload)",
)
async def get_backtest(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> BacktestRunFull:
    run = await BacktestService(session).get_owned(
        user_id=current_user.id, run_id=run_id
    )
    return BacktestRunFull.model_validate(run)


# ---- GET /backtest/{id}/results ------------------------------------------


@router.get(
    "/{run_id}/results",
    response_model=BacktestResultsResponse,
    summary="Fetch a backtest's metrics + trades + equity curve",
)
async def get_backtest_results(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> BacktestResultsResponse:
    run = await BacktestService(session).get_owned(
        user_id=current_user.id, run_id=run_id
    )
    return BacktestResultsResponse(
        id=run.id,
        status=str(run.status.value if hasattr(run.status, "value") else run.status),
        summary=run.summary or {},
        trades=list(run.trades or []),
        equity_curve=list(run.equity_curve or []),
    )


# ---- GET /backtest/{id}/export -------------------------------------------


@router.get(
    "/{run_id}/export",
    summary="Download a backtest report as JSON or CSV",
    response_class=Response,
)
async def export_backtest(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
    format: Literal["json", "csv"] = Query("json"),
) -> Response:
    run = await BacktestService(session).get_owned(
        user_id=current_user.id, run_id=run_id
    )
    if format == "json":
        body = {
            "id": run.id,
            "strategy_name": run.strategy_name,
            "symbols": list(run.symbols),
            "start_date": run.start_date.isoformat(),
            "end_date": run.end_date.isoformat(),
            "initial_capital": float(run.initial_capital),
            "status": run.status.value if hasattr(run.status, "value") else run.status,
            "params": dict(run.params or {}),
            "summary": dict(run.summary or {}),
            "trades": list(run.trades or []),
            "equity_curve": list(run.equity_curve or []),
        }
        return Response(
            content=json.dumps(body, indent=2, default=str),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="backtest_{run.id}.json"'
            },
        )

    # ---- CSV --------------------------------------------------------------
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "symbol", "side", "entry_time", "entry_price",
        "exit_time", "exit_price", "quantity", "pnl", "fees", "exit_reason",
    ])
    for t in (run.trades or []):
        w.writerow([
            t.get("symbol", ""),
            t.get("side", ""),
            t.get("entry_time", ""),
            t.get("entry_price", ""),
            t.get("exit_time", ""),
            t.get("exit_price", ""),
            t.get("quantity", ""),
            t.get("pnl", ""),
            t.get("fees", ""),
            t.get("exit_reason", ""),
        ])
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="backtest_{run.id}.csv"'
        },
    )
