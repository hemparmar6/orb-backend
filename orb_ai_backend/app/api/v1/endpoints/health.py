"""Health-check endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DBSession
from app.core.config import settings

router = APIRouter()


@router.get("", summary="Deep health check", operation_id="health_v1")
async def health(session: DBSession) -> dict:
    """Verify API + DB connectivity."""
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "database": "ok" if db_ok else "unreachable",
    }
