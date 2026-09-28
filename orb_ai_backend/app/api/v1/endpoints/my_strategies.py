"""ORB AI 2.0 — My Strategies (Milestone 1 + Milestone 3).

This module replaces the removed strategy Marketplace. In ORB AI 2.0 users
CREATE and OWN their strategies; the platform does NOT sell strategies.

Base path: ``/api/v1/my-strategies``

Endpoints:
  GET    /                     List my strategies (paginated)
  POST   /                     Create a new strategy
  GET    /templates            List built-in strategy templates (catalog)
  POST   /import               Import a strategy from JSON blueprint
  GET    /{id}                 Get strategy detail (blueprint + metadata)
  PATCH  /{id}                 Update strategy (auto-creates new version)
  DELETE /{id}                 Delete strategy
  GET    /{id}/export          Export strategy as JSON blueprint
  POST   /{id}/clone           Clone into a new strategy (own copy)
  GET    /{id}/versions        List version history (Milestone 3)

Strategy blueprint (Milestone 3 — Visual No-Code Builder DSL):
  Stored on ``strategies.parameters`` JSON column so no schema migration
  is required. Shape validated by ``StrategyBlueprint``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.models.strategy import Strategy, StrategyStatus
from app.models.strategy_catalog import StrategyCatalog
from app.schemas.common import PaginatedResponse
from app.services.strategy_service import StrategyService

router = APIRouter()


# ============================================================================
# Milestone 3 — Visual No-Code Strategy Blueprint DSL
# ============================================================================
class IndicatorBlock(BaseModel):
    id: str = Field(..., description="Client-generated block id")
    kind: Literal[
        "sma", "ema", "vwap", "rsi", "macd", "bollinger", "atr",
        "supertrend", "stochastic", "adx", "orb", "volume", "custom",
    ]
    inputs: dict[str, Any] = Field(default_factory=dict)
    label: Optional[str] = None


class ConditionBlock(BaseModel):
    id: str
    left: str = Field(..., description="Left operand (indicator id or price)")
    op: Literal[">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"]
    right: Any = Field(..., description="Right operand — number, indicator id, or price")


class EntryRule(BaseModel):
    direction: Literal["long", "short"] = "long"
    conditions: list[str] = Field(default_factory=list, description="Condition ids joined by ``combinator``")
    combinator: Literal["all", "any"] = "all"


class ExitRule(BaseModel):
    take_profit_pct: Optional[float] = Field(default=None, ge=0)
    stop_loss_pct: Optional[float] = Field(default=None, ge=0)
    trailing_stop_pct: Optional[float] = Field(default=None, ge=0)
    time_stop_minutes: Optional[int] = Field(default=None, ge=0)
    conditions: list[str] = Field(default_factory=list)
    combinator: Literal["all", "any"] = "any"


class RiskManagement(BaseModel):
    max_daily_loss_pct: Optional[float] = Field(default=None, ge=0, le=100)
    max_position_loss_pct: Optional[float] = Field(default=None, ge=0, le=100)
    max_concurrent_positions: Optional[int] = Field(default=None, ge=1)
    max_daily_trades: Optional[int] = Field(default=None, ge=1)
    consecutive_loss_stop: Optional[int] = Field(default=None, ge=1)


class PositionSizing(BaseModel):
    mode: Literal["fixed_qty", "fixed_capital", "risk_pct", "kelly"] = "fixed_qty"
    quantity: Optional[float] = Field(default=None, ge=0)
    capital_per_trade: Optional[float] = Field(default=None, ge=0)
    risk_per_trade_pct: Optional[float] = Field(default=None, ge=0, le=100)


class TradingSession(BaseModel):
    timezone: str = "Asia/Kolkata"
    start: str = Field(default="09:15", description="HH:MM")
    end: str = Field(default="15:15", description="HH:MM")
    days: list[Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]] = Field(
        default_factory=lambda: ["MON", "TUE", "WED", "THU", "FRI"]
    )


class StrategyBlueprint(BaseModel):
    """The full no-code strategy definition.

    Stored on ``strategies.parameters``. Backward compatible: if a strategy
    was created before Milestone 3, its ``parameters`` may not have a
    ``blueprint`` key — the frontend treats it as an empty blueprint.
    """
    model_config = ConfigDict(extra="allow")

    schema_version: str = "2.0"
    symbols: list[str] = Field(default_factory=list)
    timeframe: str = Field(default="15m")
    indicators: list[IndicatorBlock] = Field(default_factory=list)
    conditions: list[ConditionBlock] = Field(default_factory=list)
    entry: EntryRule = Field(default_factory=EntryRule)
    exit: ExitRule = Field(default_factory=ExitRule)
    risk_management: RiskManagement = Field(default_factory=RiskManagement)
    position_sizing: PositionSizing = Field(default_factory=PositionSizing)
    session: TradingSession = Field(default_factory=TradingSession)
    notes: Optional[str] = None


# ============================================================================
# Request / response schemas
# ============================================================================
class MyStrategyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)
    blueprint: StrategyBlueprint = Field(default_factory=StrategyBlueprint)
    status: StrategyStatus = StrategyStatus.DRAFT


class MyStrategyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)
    blueprint: Optional[StrategyBlueprint] = None
    status: Optional[StrategyStatus] = None
    change_note: Optional[str] = Field(default=None, max_length=500)


class MyStrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    description: Optional[str] = None
    blueprint: StrategyBlueprint = Field(default_factory=StrategyBlueprint)
    status: StrategyStatus
    created_at: datetime
    updated_at: datetime
    version: int = 1


class StrategyTemplate(BaseModel):
    key: str
    name: str
    description: Optional[str] = None
    category: str
    difficulty: str
    risk_level: str
    min_plan_tier: str
    is_featured: bool
    default_blueprint: StrategyBlueprint = Field(default_factory=StrategyBlueprint)


class VersionEntry(BaseModel):
    version: int
    saved_at: datetime
    change_note: Optional[str] = None
    blueprint: StrategyBlueprint = Field(default_factory=StrategyBlueprint)


# ============================================================================
# Helpers
# ============================================================================
def _to_out(s: Strategy) -> MyStrategyOut:
    params = s.parameters or {}
    bp = params.get("blueprint") or {}
    versions = params.get("_versions") or []
    current_version = len(versions) + 1
    return MyStrategyOut(
        id=s.id,
        user_id=s.user_id,
        name=s.name,
        description=s.description,
        blueprint=StrategyBlueprint(**bp) if bp else StrategyBlueprint(),
        status=s.status,
        created_at=s.created_at,
        updated_at=s.updated_at,
        version=current_version,
    )


def _pack_params(bp: StrategyBlueprint, versions: list[dict] | None = None) -> dict:
    return {
        "blueprint": bp.model_dump(mode="json"),
        "_versions": versions or [],
    }


# ============================================================================
# Endpoints
# ============================================================================
@router.get("", response_model=PaginatedResponse[MyStrategyOut],
            summary="List my strategies")
async def list_mine(
    user: CurrentUser,
    session: DBSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> PaginatedResponse[MyStrategyOut]:
    offset = (page - 1) * page_size
    items, total = await StrategyService(session).list_for_user(
        user, offset=offset, limit=page_size,
    )
    return PaginatedResponse[MyStrategyOut](
        items=[_to_out(s) for s in items],
        total=total, page=page, page_size=page_size,
    )


@router.post("", response_model=MyStrategyOut, status_code=status.HTTP_201_CREATED,
             summary="Create a new strategy (I own it)")
async def create_mine(
    payload: MyStrategyCreate, user: CurrentUser, session: DBSession,
) -> MyStrategyOut:
    # Milestone 2 — enforce the "saved strategies" quota from the user's plan.
    from app.services.subscriptions.feature_gate import FeatureGate  # local import
    try:
        plan = await FeatureGate(session).get_plan(user)
        feats = (plan.features or {}) if plan else {}
    except Exception:
        feats = {}
    quota = feats.get("max_saved_strategies")
    if isinstance(quota, int) and quota > 0:
        existing = (await session.execute(
            select(Strategy).where(Strategy.user_id == user.id)
        )).scalars().all()
        if len(existing) >= quota:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "strategy_quota_exceeded",
                    "message": (
                        f"Your current plan allows {quota} saved strategies. "
                        "Upgrade to Pro for unlimited strategies."
                    ),
                    "limit": quota,
                    "used": len(existing),
                    "upgrade_url": "/plans",
                },
            )

    strat = Strategy(
        user_id=user.id,
        name=payload.name,
        description=payload.description,
        parameters=_pack_params(payload.blueprint),
        status=payload.status,
        is_public=False,
    )
    session.add(strat)
    await session.commit()
    await session.refresh(strat)
    return _to_out(strat)


@router.get("/templates", response_model=list[StrategyTemplate],
            summary="Built-in strategy templates (starter blueprints)")
async def list_templates(session: DBSession) -> list[StrategyTemplate]:
    rows = (await session.execute(
        select(StrategyCatalog).where(StrategyCatalog.status == "active")
        .order_by(StrategyCatalog.display_order)
    )).scalars().all()
    return [
        StrategyTemplate(
            key=r.key, name=r.name, description=r.description or "",
            category=r.category, difficulty=str(r.difficulty),
            risk_level=str(r.risk_level), min_plan_tier=r.min_plan_tier,
            is_featured=r.is_featured,
            default_blueprint=StrategyBlueprint(
                **(r.default_params or {})
            ) if (r.default_params or {}) else StrategyBlueprint(),
        )
        for r in rows
    ]


@router.post("/import", response_model=MyStrategyOut,
             status_code=status.HTTP_201_CREATED,
             summary="Import strategy from a JSON blueprint export")
async def import_strategy(
    payload: MyStrategyCreate, user: CurrentUser, session: DBSession,
) -> MyStrategyOut:
    # Same shape as create — separate route so mobile UI can show a
    # dedicated "import" flow with clearer errors.
    return await create_mine(payload, user, session)


@router.get("/{strategy_id}", response_model=MyStrategyOut,
            summary="Get one of my strategies")
async def get_mine(
    strategy_id: str, user: CurrentUser, session: DBSession,
) -> MyStrategyOut:
    strat = await StrategyService(session).get_for_user(user, strategy_id)
    return _to_out(strat)


@router.patch("/{strategy_id}", response_model=MyStrategyOut,
              summary="Update a strategy (auto-versioned)")
async def update_mine(
    strategy_id: str, payload: MyStrategyUpdate,
    user: CurrentUser, session: DBSession,
) -> MyStrategyOut:
    strat = await StrategyService(session).get_for_user(user, strategy_id)

    if payload.name is not None:
        strat.name = payload.name
    if payload.description is not None:
        strat.description = payload.description
    if payload.status is not None:
        strat.status = payload.status

    if payload.blueprint is not None:
        old_params = dict(strat.parameters or {})
        old_bp = old_params.get("blueprint")
        versions = list(old_params.get("_versions") or [])
        if old_bp is not None:
            versions.append({
                "version": len(versions) + 1,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "change_note": payload.change_note,
                "blueprint": old_bp,
            })
        # Cap history to last 20 revisions to keep JSON row small.
        versions = versions[-20:]
        strat.parameters = _pack_params(payload.blueprint, versions)

    await session.commit()
    await session.refresh(strat)
    return _to_out(strat)


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_class=Response, summary="Delete a strategy")
async def delete_mine(
    strategy_id: str, user: CurrentUser, session: DBSession,
) -> Response:
    await StrategyService(session).delete(user, strategy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{strategy_id}/export", response_model=dict,
            summary="Export strategy as portable JSON blueprint")
async def export_strategy(
    strategy_id: str, user: CurrentUser, session: DBSession,
) -> dict:
    strat = await StrategyService(session).get_for_user(user, strategy_id)
    out = _to_out(strat)
    return {
        "schema_version": "2.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "name": out.name,
        "description": out.description,
        "blueprint": out.blueprint.model_dump(mode="json"),
    }


@router.post("/{strategy_id}/clone", response_model=MyStrategyOut,
             status_code=status.HTTP_201_CREATED,
             summary="Clone strategy into a new copy I own")
async def clone_strategy(
    strategy_id: str, user: CurrentUser, session: DBSession,
) -> MyStrategyOut:
    orig = await StrategyService(session).get_for_user(user, strategy_id)
    clone = Strategy(
        user_id=user.id,
        name=f"{orig.name} (copy)",
        description=orig.description,
        parameters=dict(orig.parameters or {}),
        status=StrategyStatus.DRAFT,
        is_public=False,
    )
    # Reset version history on the clone.
    p = dict(clone.parameters)
    p["_versions"] = []
    clone.parameters = p
    session.add(clone)
    await session.commit()
    await session.refresh(clone)
    return _to_out(clone)


@router.get("/{strategy_id}/versions", response_model=list[VersionEntry],
            summary="List strategy version history")
async def list_versions(
    strategy_id: str, user: CurrentUser, session: DBSession,
) -> list[VersionEntry]:
    strat = await StrategyService(session).get_for_user(user, strategy_id)
    params = strat.parameters or {}
    versions = params.get("_versions") or []
    out: list[VersionEntry] = []
    for v in versions:
        try:
            out.append(VersionEntry(
                version=int(v.get("version", 0)),
                saved_at=datetime.fromisoformat(v["saved_at"]),
                change_note=v.get("change_note"),
                blueprint=StrategyBlueprint(**(v.get("blueprint") or {})),
            ))
        except Exception:
            continue
    return out


# ============================================================================
# Milestone 1 note: Strategy marketplace purchase is DISABLED in ORB AI 2.0.
# The original ``POST /api/v1/marketplace/{key}/purchase`` route now returns
# HTTP 410 Gone (see ``app.api.v1.endpoints.marketplace``). Users create and
# own their own strategies through the endpoints above.
# ============================================================================
