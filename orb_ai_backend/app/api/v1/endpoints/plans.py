"""Admin plan-limits + subscription-plan CRUD endpoints (v1.1.0).

Every route requires admin privileges. Plans, price points, and limits
are configurable at runtime — no redeploy required to change them.

Base path: /api/v1/plans   (mounted by app.api.v1.router)

Endpoints
---------
GET    /                       → list every plan (active + inactive)
GET    /public                 → PUBLIC list of active plans (no auth)
GET    /{key}                  → fetch one plan
POST   /                       → create a plan
PUT    /{key}                  → update a plan (partial)
PUT    /{key}/limits           → update just the limit fields
DELETE /{key}                  → soft-deactivate (is_active=False)
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import AdminUser, DBSession
from app.core.exceptions import ConflictError, NotFoundError
from app.models.subscription import PlanTier, SubscriptionPlan

router = APIRouter()


# ---------------------------------------------------------------- schemas
class PlanOut(BaseModel):
    id: str
    key: str
    name: str
    tier: str
    price_cents: int
    currency: str
    interval: str
    is_active: bool
    provider: str
    description: Optional[str] = None
    display_order: int
    max_running_bots: int
    max_open_positions: int
    automation_enabled: bool
    paper_trading_only: bool
    ai_features_enabled: bool
    unlimited_bots: bool
    features: Optional[dict[str, Any]] = None

    @classmethod
    def from_model(cls, p: SubscriptionPlan) -> "PlanOut":
        return cls(
            id=p.id, key=p.key, name=p.name,
            tier=p.tier.value if hasattr(p.tier, "value") else str(p.tier),
            price_cents=p.price_cents, currency=p.currency, interval=p.interval,
            is_active=p.is_active, provider=p.provider,
            description=p.description, display_order=p.display_order,
            max_running_bots=p.max_running_bots, max_open_positions=p.max_open_positions,
            automation_enabled=p.automation_enabled, paper_trading_only=p.paper_trading_only,
            ai_features_enabled=p.ai_features_enabled, unlimited_bots=p.unlimited_bots,
            features=p.features,
        )


class PlanCreateIn(BaseModel):
    key: str = Field(..., max_length=64, min_length=2)
    name: str = Field(..., max_length=128)
    tier: str = Field(...)
    price_cents: int = Field(ge=0, default=0)
    currency: str = Field(default="INR", max_length=8)
    interval: str = Field(default="monthly", max_length=16)
    description: Optional[str] = Field(default=None, max_length=2000)
    display_order: int = 0
    max_running_bots: int = 0
    max_open_positions: int = 0
    automation_enabled: bool = False
    paper_trading_only: bool = True
    ai_features_enabled: bool = False
    unlimited_bots: bool = False
    features: Optional[dict[str, Any]] = None

    @field_validator("tier")
    @classmethod
    def _tier_ok(cls, v: str) -> str:
        v = v.lower()
        if v not in {t.value for t in PlanTier}:
            raise ValueError(f"tier must be one of {[t.value for t in PlanTier]}")
        return v


class PlanUpdateIn(BaseModel):
    name: Optional[str] = None
    tier: Optional[str] = None
    price_cents: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = None
    interval: Optional[str] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None
    display_order: Optional[int] = None
    features: Optional[dict[str, Any]] = None


class PlanLimitsIn(BaseModel):
    max_running_bots: Optional[int] = Field(default=None, ge=-1)
    max_open_positions: Optional[int] = Field(default=None, ge=-1)
    automation_enabled: Optional[bool] = None
    paper_trading_only: Optional[bool] = None
    ai_features_enabled: Optional[bool] = None
    unlimited_bots: Optional[bool] = None


# ---------------------------------------------------------------- helpers
async def _get_by_key(session, key: str) -> SubscriptionPlan:
    p = (await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.key == key.lower())
    )).scalar_one_or_none()
    if p is None:
        raise NotFoundError(f"Plan '{key}' not found", code="plan_not_found")
    return p


# ---------------------------------------------------------------- routes
@router.get("/public", response_model=list[PlanOut], summary="Public plan list")
async def list_public(session: DBSession) -> list[PlanOut]:
    rows = (await session.execute(
        select(SubscriptionPlan)
        .where(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.display_order.asc(), SubscriptionPlan.price_cents.asc())
    )).scalars().all()
    return [PlanOut.from_model(p) for p in rows]


@router.get("/", response_model=list[PlanOut], summary="[Admin] All plans")
async def list_all(_: AdminUser, session: DBSession) -> list[PlanOut]:
    rows = (await session.execute(
        select(SubscriptionPlan).order_by(
            SubscriptionPlan.display_order.asc(),
            SubscriptionPlan.price_cents.asc(),
        )
    )).scalars().all()
    return [PlanOut.from_model(p) for p in rows]


@router.get("/{key}", response_model=PlanOut, summary="[Admin] Fetch one plan")
async def get_one(key: str, _: AdminUser, session: DBSession) -> PlanOut:
    return PlanOut.from_model(await _get_by_key(session, key))


@router.post(
    "/",
    response_model=PlanOut,
    status_code=status.HTTP_201_CREATED,
    summary="[Admin] Create plan",
)
async def create(payload: PlanCreateIn, _: AdminUser, session: DBSession) -> PlanOut:
    existing = (await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.key == payload.key.lower())
    )).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"Plan '{payload.key}' already exists", code="plan_exists")
    plan = SubscriptionPlan(
        key=payload.key.lower(),
        name=payload.name,
        tier=PlanTier(payload.tier),
        price_cents=payload.price_cents,
        currency=payload.currency,
        interval=payload.interval,
        is_active=True,
        provider="noop",
        description=payload.description,
        display_order=payload.display_order,
        max_running_bots=payload.max_running_bots,
        max_open_positions=payload.max_open_positions,
        automation_enabled=payload.automation_enabled,
        paper_trading_only=payload.paper_trading_only,
        ai_features_enabled=payload.ai_features_enabled,
        unlimited_bots=payload.unlimited_bots,
        features=payload.features,
    )
    session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return PlanOut.from_model(plan)


@router.put("/{key}", response_model=PlanOut, summary="[Admin] Update plan")
async def update(
    key: str, payload: PlanUpdateIn, _: AdminUser, session: DBSession,
) -> PlanOut:
    plan = await _get_by_key(session, key)
    data = payload.model_dump(exclude_none=True)
    if "tier" in data:
        data["tier"] = PlanTier(data["tier"])
    for k, v in data.items():
        setattr(plan, k, v)
    await session.commit()
    await session.refresh(plan)
    return PlanOut.from_model(plan)


@router.put(
    "/{key}/limits",
    response_model=PlanOut,
    summary="[Admin] Update plan limits (bots / positions / automation / AI)",
)
async def update_limits(
    key: str, payload: PlanLimitsIn, _: AdminUser, session: DBSession,
) -> PlanOut:
    plan = await _get_by_key(session, key)
    data = payload.model_dump(exclude_none=True)
    for k, v in data.items():
        setattr(plan, k, v)
    await session.commit()
    await session.refresh(plan)
    return PlanOut.from_model(plan)


@router.delete(
    "/{key}", response_model=PlanOut, summary="[Admin] Deactivate plan"
)
async def deactivate(key: str, _: AdminUser, session: DBSession) -> PlanOut:
    plan = await _get_by_key(session, key)
    plan.is_active = False
    await session.commit()
    await session.refresh(plan)
    return PlanOut.from_model(plan)
