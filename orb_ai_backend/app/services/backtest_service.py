"""Backtest orchestration service.

Responsibilities
----------------
1. Persist a ``BacktestRun`` row (status=PENDING).
2. Build candles for the requested (symbols × window) using an optional
   :class:`HistoricalCandleFetcher`. When no fetcher is supplied, falls back
   to the deterministic synthetic generator so CI stays green.
3. Run :class:`BacktestEngine.run` (synchronous, CPU-bound; short backtests
   complete in ~ tens of ms).
4. Persist trades, equity curve, and metrics; flip status to COMPLETE.
5. On any exception, capture the message on the row and flip to FAILED —
   the row is always saved so the client can poll and inspect.

Runs are executed **synchronously** inside the request (spec calls for
`POST /backtest/run` returning results). Long-running batches can move to
a background task later without touching this API surface.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.engine.backtest.engine import BacktestEngine
from app.engine.backtest.synthetic_candles import generate_intraday_candles
from app.engine.market_data.base import Candle, Interval
from app.engine.market_data.historical_base import HistoricalCandleFetcher
from app.engine.strategy.orb_params import OrbParams
from app.engine.strategy.registry import get_strategy_class
from app.models.backtest import BacktestRun, BacktestStatus
from app.repositories.backtest_repository import BacktestRepository
from app.schemas.backtest import BacktestRunRequest

logger = get_logger(__name__)


class BacktestNotFoundError(AppError):
    status_code = 404
    code = "backtest_not_found"
    message = "Backtest not found"


class BacktestForbiddenError(AppError):
    status_code = 403
    code = "backtest_forbidden"
    message = "You do not own this backtest"


class BacktestService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        historical_fetcher: Optional[HistoricalCandleFetcher] = None,
        candle_interval: Interval = Interval.ONE_MIN,
    ) -> None:
        self.session = session
        self.repo = BacktestRepository(session)
        self._historical_fetcher = historical_fetcher
        self._candle_interval = candle_interval

    # ---- Public API ------------------------------------------------------

    async def run(
        self, *, user_id: str, payload: BacktestRunRequest
    ) -> BacktestRun:
        # Validate strategy exists (raises if not).
        get_strategy_class(payload.strategy_name)

        # Merge symbols from top-level + params.
        params_dict = payload.params.model_dump(exclude_none=True)
        params_dict.setdefault("symbols", payload.symbols)

        run = BacktestRun(
            user_id=user_id,
            strategy_name=payload.strategy_name,
            strategy_id=payload.strategy_id,
            symbols=list(payload.symbols),
            params=params_dict,
            start_date=payload.start_date,
            end_date=payload.end_date,
            initial_capital=float(payload.initial_capital),
            status=BacktestStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        run = await self.repo.add(run)

        try:
            orb = OrbParams.from_dict(params_dict)
            candles = await _load_candles(
                symbols=payload.symbols,
                start_date=payload.start_date,
                end_date=payload.end_date,
                params=orb,
                fetcher=self._historical_fetcher,
                interval=self._candle_interval,
            )
            engine = BacktestEngine(orb, initial_capital=float(payload.initial_capital))
            result = engine.run(candles)

            run.summary = result.summary
            run.trades = [t.to_dict() for t in result.trades]
            run.equity_curve = list(result.equity_curve)
            run.status = BacktestStatus.COMPLETE
            run.finished_at = datetime.now(timezone.utc)
        except Exception as e:  # noqa: BLE001 - persist & re-raise
            logger.exception("backtest_failed", extra={"run_id": run.id, "err": str(e)})
            run.status = BacktestStatus.FAILED
            run.error_message = str(e)
            run.finished_at = datetime.now(timezone.utc)

        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def get_owned(self, *, user_id: str, run_id: str) -> BacktestRun:
        run = await self.repo.get(run_id)
        if run is None:
            raise BacktestNotFoundError()
        if run.user_id != user_id:
            raise BacktestForbiddenError()
        return run

    async def list_for_user(
        self, *, user_id: str, offset: int, limit: int
    ) -> tuple[list[BacktestRun], int]:
        items = await self.repo.list_for_user(user_id, offset=offset, limit=limit)
        total = await self.repo.count_for_user(user_id)
        return items, total


# ---- Candle loader -------------------------------------------------------


async def _load_candles(
    *,
    symbols: list[str],
    start_date: datetime,
    end_date: datetime,
    params: OrbParams,
    fetcher: Optional[HistoricalCandleFetcher] = None,
    interval: Interval = Interval.ONE_MIN,
) -> list[Candle]:
    """Materialize candles for the backtest.

    When ``fetcher`` is provided, live broker candles are fetched via that
    :class:`HistoricalCandleFetcher`. Otherwise the deterministic synthetic
    generator is used so backtests remain runnable and reproducible in CI
    without any broker credentials.
    """
    if fetcher is not None:
        out: list[Candle] = []
        for symbol in symbols:
            out.extend(
                await fetcher.get_candles(
                    symbol=symbol,
                    interval=interval,
                    start=start_date,
                    end=end_date,
                    exchange=params.exchange,
                )
            )
        return out

    # Fallback — synthetic (unchanged from Module 5).
    candles: list[Candle] = []
    for i, symbol in enumerate(symbols):
        candles.extend(
            generate_intraday_candles(
                symbol=symbol,
                start_date=start_date.date(),
                end_date=end_date.date(),
                session_start=params.session_start,
                session_end=params.session_end,
                timezone=params.timezone,
                exchange=params.exchange,
                base_price=20_000.0 + i * 500.0,  # give each symbol a distinct scale
                seed=42 + i,
            )
        )
    return candles
