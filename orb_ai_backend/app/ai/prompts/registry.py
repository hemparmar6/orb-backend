"""Versioned prompt registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    render_user: Callable[[Dict[str, Any]], str]


from app.ai.prompts import (  # noqa: E402
    backtest_interp,
    portfolio_insight,
    recommendation,
    risk_analysis,
    strategy_assistant,
    strategy_review,
    trade_analysis,
)

_REGISTRY: Dict[str, Dict[str, Prompt]] = {
    "trade_review": {trade_analysis.PROMPT.version: trade_analysis.PROMPT},
    "recommendation": {recommendation.PROMPT.version: recommendation.PROMPT},
    "portfolio_insight": {portfolio_insight.PROMPT.version: portfolio_insight.PROMPT},
    "strategy_review": {strategy_review.PROMPT.version: strategy_review.PROMPT},
    "risk_analysis": {risk_analysis.PROMPT.version: risk_analysis.PROMPT},
    "backtest_interp": {backtest_interp.PROMPT.version: backtest_interp.PROMPT},
}

# ORB AI 2.0 — Milestone 4: register the AI Strategy Assistant prompts.
for _p in strategy_assistant.ALL_PROMPTS:
    _REGISTRY.setdefault(_p.name, {})[_p.version] = _p

LATEST: Dict[str, str] = {name: max(versions) for name, versions in _REGISTRY.items()}


def get_prompt(name: str, version: str) -> Prompt:
    try:
        return _REGISTRY[name][version]
    except KeyError as exc:
        raise KeyError(f"Unknown prompt {name!r}@{version!r}") from exc
