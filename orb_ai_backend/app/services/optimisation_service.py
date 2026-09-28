"""Optimisation service — creates + runs jobs, ranks results.

The concrete backtest execution is delegated via a ``runner`` callable
so this module has no dependency on the Module-5 backtester's internals.
Wire your runner at API layer::

    OptimisationService(session, runner=my_runner)
"""

from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OptimisationJob, OptimisationResult

logger = logging.getLogger(__name__)

# runner(strategy_id, params, config) -> metrics dict (may be async or sync)
Runner = Callable[[str, Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]] | Dict[str, Any]]


class OptimisationService:
    def __init__(
        self, session: AsyncSession, runner: Runner | None = None,
        rank_key: str = "sharpe",
    ) -> None:
        self.session = session
        self.runner = runner
        self.rank_key = rank_key

    async def create_job(
        self, user_id: str, strategy_id: str, kind: str,
        params_space: Dict[str, List[Any]], config: Dict[str, Any] | None = None,
    ) -> OptimisationJob:
        job = OptimisationJob(
            user_id=user_id,
            strategy_id=strategy_id,
            kind=kind,
            status="queued",
            params_space=params_space,
            config=config or {},
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def list_jobs(self, user_id: str, limit: int = 50) -> List[OptimisationJob]:
        stmt = (
            select(OptimisationJob)
            .where(OptimisationJob.user_id == user_id)
            .order_by(OptimisationJob.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_job(self, user_id: str, job_id: str) -> OptimisationJob | None:
        stmt = select(OptimisationJob).where(
            OptimisationJob.id == job_id, OptimisationJob.user_id == user_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_results(self, job_id: str, top: int = 20) -> List[OptimisationResult]:
        stmt = (
            select(OptimisationResult)
            .where(OptimisationResult.job_id == job_id)
            .order_by(OptimisationResult.rank.asc())
            .limit(top)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def run_job(self, job_id: str) -> OptimisationJob | None:
        job = (await self.session.execute(
            select(OptimisationJob).where(OptimisationJob.id == job_id)
        )).scalar_one_or_none()
        if job is None:
            return None

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await self.session.commit()

        try:
            combos = _expand(job.params_space)
            results: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
            for params in combos:
                if self.runner is None:
                    # No runner wired — record zero metrics so the pipeline still works.
                    metrics = {"sharpe": 0.0, "net_profit": 0.0, "win_rate": 0.0}
                else:
                    out = self.runner(job.strategy_id, params, job.config)
                    metrics = await out if hasattr(out, "__await__") else out  # type: ignore[assignment]
                results.append((params, metrics))
            await self._persist_results(job.id, results)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            logger.exception("optim.run failed")
            job.status = "failed"
            job.error = str(exc)[:500]
        finally:
            job.finished_at = datetime.now(timezone.utc)
            await self.session.commit()
            await self.session.refresh(job)
        return job

    async def _persist_results(
        self, job_id: str, results: List[tuple[Dict[str, Any], Dict[str, Any]]],
    ) -> None:
        ranked = sorted(
            results,
            key=lambda r: r[1].get(self.rank_key, float("-inf")),
            reverse=True,
        )
        for idx, (params, metrics) in enumerate(ranked, start=1):
            self.session.add(OptimisationResult(
                job_id=job_id, rank=idx, params=params, metrics=metrics,
                is_best=(idx == 1),
            ))
        await self.session.commit()


def _expand(space: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    if not space:
        return [{}]
    keys = list(space.keys())
    values = [space[k] if isinstance(space[k], list) else [space[k]] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]
