"""Strategy Catalog endpoints (v1.1.0).

Base path: /api/v1/strategy-catalog

Public read endpoints let the Expo mobile app and admin dashboard
render the marketplace UI. Admin-only endpoints let ops toggle strategy
status, min_plan_tier, and performance stats without a redeploy.

Endpoints
---------
GET  /              → public list (marketplace view; filtered by ?tier=&status=)
GET  /{key}         → public fetch one
POST /              → [Admin] create catalog entry
PUT  /{key}         → [Admin] update catalog entry (partial)
PUT  /{key}/status  → [Admin] change status (active/pending/deprecated/disabled)
DELETE /{key}       → [Admin] soft-disable (status='disabled')
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import AdminUser, DBSession
from app.core.exceptions import ConflictError, NotFoundError
from app.models.strategy_catalog import (
    StrategyCatalog,
    StrategyDifficulty,
    StrategyRiskLevel,
    StrategyStatus as CatalogStatus,
)

router = APIRouter()


# ---------------------------------------------------------------- schemas
class StrategyOut(BaseModel):
    id: str
    key: str
    name: str
    description: Optional[str] = None
    category: str
    difficulty: str
    risk_level: str
    supported_markets: Optional[list[str]] = None
    supported_timeframes: Optional[list[str]] = None
    version: str
    min_plan_tier: str
    automation_supported: bool
    ai_compatible: bool
    paper_trading_supported: bool
    live_trading_supported: bool
    status: str
    is_featured: bool
    display_order: int
    performance_stats: Optional[dict[str, Any]] = None
    default_params: Optional[dict[str, Any]] = None

    @classmethod
    def from_model(cls, s: StrategyCatalog) -> "StrategyOut":
        return cls(
            id=s.id, key=s.key, name=s.name, description=s.description,
            category=s.category, difficulty=s.difficulty, risk_level=s.risk_level,
            supported_markets=s.supported_markets,
            supported_timeframes=s.supported_timeframes,
            version=s.version, min_plan_tier=s.min_plan_tier,
            automation_supported=s.automation_supported, ai_compatible=s.ai_compatible,
            paper_trading_supported=s.paper_trading_supported,
            live_trading_supported=s.live_trading_supported,
            status=s.status, is_featured=s.is_featured, display_order=s.display_order,
            performance_stats=s.performance_stats, default_params=s.default_params,
        )


class StrategyCreateIn(BaseModel):
    key: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)
    category: str = "general"
    difficulty: str = StrategyDifficulty.INTERMEDIATE.value
    risk_level: str = StrategyRiskLevel.MEDIUM.value
    supported_markets: Optional[list[str]] = None
    supported_timeframes: Optional[list[str]] = None
    version: str = "1.0.0"
    min_plan_tier: str = "standard"
    automation_supported: bool = False
    ai_compatible: bool = True
    paper_trading_supported: bool = True
    live_trading_supported: bool = False
    status: str = CatalogStatus.ACTIVE.value
    is_featured: bool = False
    display_order: int = 0
    performance_stats: Optional[dict[str, Any]] = None
    default_params: Optional[dict[str, Any]] = None

    @field_validator("difficulty")
    @classmethod
    def _diff_ok(cls, v: str) -> str:
        v = v.lower()
        if v not in {d.value for d in StrategyDifficulty}:
            raise ValueError(f"difficulty must be one of {[d.value for d in StrategyDifficulty]}")
        return v

    @field_validator("risk_level")
    @classmethod
    def _risk_ok(cls, v: str) -> str:
        v = v.lower()
        if v not in {r.value for r in StrategyRiskLevel}:
            raise ValueError(f"risk_level must be one of {[r.value for r in StrategyRiskLevel]}")
        return v

    @field_validator("status")
    @classmethod
    def _status_ok(cls, v: str) -> str:
        v = v.lower()
        if v not in {s.value for s in CatalogStatus}:
            raise ValueError(f"status must be one of {[s.value for s in CatalogStatus]}")
        return v


class StrategyUpdateIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    difficulty: Optional[str] = None
    risk_level: Optional[str] = None
    supported_markets: Optional[list[str]] = None
    supported_timeframes: Optional[list[str]] = None
    version: Optional[str] = None
    min_plan_tier: Optional[str] = None
    automation_supported: Optional[bool] = None
    ai_compatible: Optional[bool] = None
    paper_trading_supported: Optional[bool] = None
    live_trading_supported: Optional[bool] = None
    status: Optional[str] = None
    is_featured: Optional[bool] = None
    display_order: Optional[int] = None
    performance_stats: Optional[dict[str, Any]] = None
    default_params: Optional[dict[str, Any]] = None


class StatusIn(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _status_ok(cls, v: str) -> str:
        v = v.lower()
        if v not in {s.value for s in CatalogStatus}:
            raise ValueError(f"status must be one of {[s.value for s in CatalogStatus]}")
        return v


# ---------------------------------------------------------------- helpers
async def _get(session, key: str) -> StrategyCatalog:
    row = (await session.execute(
        select(StrategyCatalog).where(StrategyCatalog.key == key.lower())
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Strategy '{key}' not found", code="strategy_not_found")
    return row


# ---------------------------------------------------------------- routes
@router.get("/", response_model=list[StrategyOut], summary="Public catalog list")
async def list_catalog(
    session: DBSession,
    tier: Optional[str] = Query(default=None, description="Filter by min_plan_tier"),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    featured: Optional[bool] = Query(default=None),
) -> list[StrategyOut]:
    q = select(StrategyCatalog)
    if tier:
        q = q.where(StrategyCatalog.min_plan_tier == tier.lower())
    if status_filter:
        q = q.where(StrategyCatalog.status == status_filter.lower())
    if featured is not None:
        q = q.where(StrategyCatalog.is_featured.is_(featured))
    q = q.order_by(StrategyCatalog.display_order.asc(), StrategyCatalog.name.asc())
    rows = (await session.execute(q)).scalars().all()
    return [StrategyOut.from_model(r) for r in rows]


@router.get("/{key}", response_model=StrategyOut, summary="Public catalog fetch")
async def get_catalog_entry(key: str, session: DBSession) -> StrategyOut:
    return StrategyOut.from_model(await _get(session, key))


@router.post(
    "/",
    response_model=StrategyOut,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create catalog entry",
)
async def create(
    payload: StrategyCreateIn, _: AdminUser, session: DBSession,
) -> StrategyOut:
    if (await session.execute(
        select(StrategyCatalog).where(StrategyCatalog.key == payload.key.lower())
    )).scalar_one_or_none() is not None:
        raise ConflictError(
            f"Strategy '{payload.key}' already exists", code="strategy_exists",
        )
    row = StrategyCatalog(**{**payload.model_dump(), "key": payload.key.lower()})
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return StrategyOut.from_model(row)


@router.put("/{key}", response_model=StrategyOut, summary="[Admin] Update catalog")
async def update(
    key: str, payload: StrategyUpdateIn, _: AdminUser, session: DBSession,
) -> StrategyOut:
    row = await _get(session, key)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    await session.commit()
    await session.refresh(row)
    return StrategyOut.from_model(row)


@router.put("/{key}/status", response_model=StrategyOut, summary="[Admin] Change status")
async def change_status(
    key: str, payload: StatusIn, _: AdminUser, session: DBSession,
) -> StrategyOut:
    row = await _get(session, key)
    row.status = payload.status
    await session.commit()
    await session.refresh(row)
    return StrategyOut.from_model(row)


@router.delete("/{key}", response_model=StrategyOut, summary="[Admin] Disable")
async def disable(key: str, _: AdminUser, session: DBSession) -> StrategyOut:
    row = await _get(session, key)
    row.status = CatalogStatus.DISABLED.value
    await session.commit()
    await session.refresh(row)
    return StrategyOut.from_model(row)
