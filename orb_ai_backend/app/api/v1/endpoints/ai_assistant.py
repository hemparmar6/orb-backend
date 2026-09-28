"""ORB AI 2.0 — AI Strategy Assistant endpoint (Milestone 4).

Base path: ``/api/v1/ai-assistant``

Endpoints:
  POST  /generate     Natural language → strategy blueprint
  POST  /explain-indicator
  POST  /explain-conditions
  POST  /optimize     Optimise an existing blueprint (advisory only)
  POST  /analyze-backtest
  POST  /review-trades
  POST  /generate-docs
  GET   /status       AI availability + active provider + rate-limit info

The AI provider is chosen at process startup by :func:`app.ai.service.get_ai_service`
based on ``AI_PROVIDER`` + ``EMERGENT_LLM_KEY`` environment variables — this
endpoint layer contains **no provider-specific code**.

Access is gated to plans whose ``features.ai_assistant`` flag is True
(currently the ``pro`` plan). See :class:`AIAccessGate` below.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.ai.service import AIRequestContext, get_ai_service
from app.api.deps import CurrentUser, DBSession
from app.models.user import User, UserRole
from app.services.subscriptions.feature_gate import FeatureGate

router = APIRouter()


# ---------------------------------------------------------------------------
# Plan gate — AI Strategy Assistant is a Pro feature.
# ---------------------------------------------------------------------------
async def _require_ai_access(session: DBSession, user: User) -> None:
    """Raise 402 unless the user's plan grants AI access."""
    if user.role == UserRole.ADMIN:
        return
    try:
        plan = await FeatureGate(session).get_plan(user)
    except Exception:
        plan = None
    feats = (plan.features or {}) if plan else {}
    if plan and plan.ai_features_enabled:
        return
    if feats.get("ai_assistant"):
        return
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": "ai_requires_pro",
            "message": (
                "The AI Strategy Assistant is available on the Pro plan. "
                "Upgrade to unlock natural-language builder, optimizer, "
                "backtest analysis and trade review."
            ),
            "upgrade_url": "/plans",
        },
    )


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class NLRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000,
                        description="Plain-English strategy description")
    current_blueprint: Optional[dict[str, Any]] = None


class IndicatorReq(BaseModel):
    kind: str
    inputs: Optional[dict[str, Any]] = None
    label: Optional[str] = None


class BlueprintReq(BaseModel):
    blueprint: dict[str, Any] = Field(default_factory=dict)


class OptimizeReq(BaseModel):
    blueprint: dict[str, Any]
    kpis: Optional[dict[str, Any]] = None
    focus: Optional[str] = Field(default="risk_and_return")


class BacktestReq(BaseModel):
    kpis: dict[str, Any]
    strategy_name: Optional[str] = None


class TradeReviewReq(BaseModel):
    trades: list[dict[str, Any]]


class DocsReq(BaseModel):
    name: str
    description: Optional[str] = None
    blueprint: dict[str, Any]


class AIResponseOut(BaseModel):
    content: dict[str, Any]
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    cached: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
    # ORB AI 2.0 — compliance stamp on every response so mobile UI can
    # always render the mandatory disclaimer.
    disclaimer: str = (
        "ORB AI is an educational tool. It does not recommend specific "
        "securities, guarantee profits, or auto-execute trades. Trading "
        "involves risk of loss."
    )


def _to_out(resp) -> AIResponseOut:
    return AIResponseOut(
        content=resp.content or {},
        provider=resp.provider,
        model=resp.model,
        prompt_name=resp.prompt_name,
        prompt_version=resp.prompt_version,
        cached=resp.cached,
        meta=resp.meta or {},
    )


async def _analyse(ctx: AIRequestContext) -> AIResponseOut:
    svc = get_ai_service()
    resp = await svc.analyse(ctx)
    return _to_out(resp)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/status", summary="AI Assistant availability & active provider")
async def status_(user: CurrentUser, session: DBSession) -> dict[str, Any]:
    svc = get_ai_service()
    primary_name = getattr(svc._primary, "name", "unknown")  # noqa: SLF001
    try:
        plan = await FeatureGate(session).get_plan(user)
    except Exception:
        plan = None
    feats = (plan.features or {}) if plan else {}
    entitled = (
        user.role == UserRole.ADMIN
        or (plan.ai_features_enabled if plan else False)
        or bool(feats.get("ai_assistant"))
    )
    return {
        "enabled": svc._enabled,  # noqa: SLF001
        "primary_provider": primary_name,
        "fallback_provider": getattr(svc._fallback, "name", "rule_based"),  # noqa: SLF001
        "features": [
            "generate", "explain-indicator", "explain-conditions",
            "optimize", "analyze-backtest", "review-trades", "generate-docs",
        ],
        "entitled": entitled,
        "plan": plan.key if plan else None,
    }


@router.post("/generate", response_model=AIResponseOut,
             summary="Natural language → editable strategy blueprint")
async def generate(payload: NLRequest, user: CurrentUser, session: DBSession) -> AIResponseOut:
    await _require_ai_access(session, user)
    return await _analyse(AIRequestContext(
        user_id=user.id, request_type="ai_generate",
        prompt_name="ai_strategy_from_nl", prompt_version="v1",
        payload=payload.model_dump(),
    ))


@router.post("/explain-indicator", response_model=AIResponseOut,
             summary="Explain an indicator in plain English")
async def explain_indicator(payload: IndicatorReq, user: CurrentUser,
                            session: DBSession) -> AIResponseOut:
    await _require_ai_access(session, user)
    return await _analyse(AIRequestContext(
        user_id=user.id, request_type="ai_explain_indicator",
        prompt_name="ai_explain_indicator", prompt_version="v1",
        payload=payload.model_dump(),
    ))


@router.post("/explain-conditions", response_model=AIResponseOut,
             summary="Explain the strategy's entry / exit conditions")
async def explain_conditions(payload: BlueprintReq, user: CurrentUser,
                             session: DBSession) -> AIResponseOut:
    await _require_ai_access(session, user)
    return await _analyse(AIRequestContext(
        user_id=user.id, request_type="ai_explain_conditions",
        prompt_name="ai_explain_conditions", prompt_version="v1",
        payload=payload.model_dump(),
    ))


@router.post("/optimize", response_model=AIResponseOut,
             summary="Advisory optimisation of an existing blueprint")
async def optimize(payload: OptimizeReq, user: CurrentUser,
                   session: DBSession) -> AIResponseOut:
    await _require_ai_access(session, user)
    return await _analyse(AIRequestContext(
        user_id=user.id, request_type="ai_optimize",
        prompt_name="ai_optimize_strategy", prompt_version="v1",
        payload=payload.model_dump(),
    ))


@router.post("/analyze-backtest", response_model=AIResponseOut,
             summary="Interpret backtest KPIs")
async def analyze_backtest(payload: BacktestReq, user: CurrentUser,
                           session: DBSession) -> AIResponseOut:
    await _require_ai_access(session, user)
    return await _analyse(AIRequestContext(
        user_id=user.id, request_type="ai_analyze_backtest",
        prompt_name="ai_backtest_analysis", prompt_version="v1",
        payload=payload.model_dump(),
    ))


@router.post("/review-trades", response_model=AIResponseOut,
             summary="Review a batch of completed trades")
async def review_trades(payload: TradeReviewReq, user: CurrentUser,
                        session: DBSession) -> AIResponseOut:
    await _require_ai_access(session, user)
    return await _analyse(AIRequestContext(
        user_id=user.id, request_type="ai_review_trades",
        prompt_name="ai_trade_review", prompt_version="v1",
        payload=payload.model_dump(),
    ))


@router.post("/generate-docs", response_model=AIResponseOut,
             summary="Generate markdown-ready strategy documentation")
async def generate_docs(payload: DocsReq, user: CurrentUser,
                        session: DBSession) -> AIResponseOut:
    await _require_ai_access(session, user)
    return await _analyse(AIRequestContext(
        user_id=user.id, request_type="ai_generate_docs",
        prompt_name="ai_strategy_docs", prompt_version="v1",
        payload=payload.model_dump(),
    ))
