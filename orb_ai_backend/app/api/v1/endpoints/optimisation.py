"""Module 9 — Optimisation endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.deps import CurrentUser, DBSession
from app.db.session import async_session_factory
from app.schemas.ai import OptimJobCreate, OptimJobOut, OptimResultOut
from app.services.optimisation_service import OptimisationService

router = APIRouter()


@router.post("/jobs", response_model=OptimJobOut)
async def create_job(
    body: OptimJobCreate,
    bg: BackgroundTasks,
    user: CurrentUser,
    session: DBSession,
):
    svc = OptimisationService(session=session, runner=None)  # runner wired at deploy time
    job = await svc.create_job(
        user_id=user.id, strategy_id=body.strategy_id, kind=body.kind,
        params_space=body.params_space, config=body.config,
    )
    bg.add_task(_run_in_new_session, job.id)
    return job


@router.get("/jobs", response_model=List[OptimJobOut])
async def list_jobs(user: CurrentUser, session: DBSession):
    return await OptimisationService(session).list_jobs(user.id)


@router.get("/jobs/{job_id}", response_model=OptimJobOut)
async def get_job(job_id: str, user: CurrentUser, session: DBSession):
    job = await OptimisationService(session).get_job(user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return job


@router.get("/jobs/{job_id}/results", response_model=List[OptimResultOut])
async def job_results(job_id: str, user: CurrentUser, session: DBSession, top: int = 20):
    svc = OptimisationService(session)
    if await svc.get_job(user.id, job_id) is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return await svc.get_results(job_id, top=top)


async def _run_in_new_session(job_id: str) -> None:
    async with async_session_factory() as session:
        await OptimisationService(session=session, runner=None).run_job(job_id)
