"""Module 9 — rule-based provider (fallback) output coverage."""

from __future__ import annotations

import pytest

from app.ai.prompts.registry import LATEST, get_prompt
from app.ai.providers.fallback import RuleBasedProvider


def test_all_prompts_registered():
    for name in ["trade_review", "recommendation", "portfolio_insight",
                 "strategy_review", "risk_analysis", "backtest_interp"]:
        assert name in LATEST
        p = get_prompt(name, LATEST[name])
        assert p.system and callable(p.render_user)


@pytest.mark.asyncio
async def test_rule_based_trade_review():
    resp = await RuleBasedProvider().generate("trade_review", "v1", {
        "trade": {
            "id": "t1", "pnl": 100, "quantity": 10,
            "entry_price": 100, "exit_price": 110, "side": "buy",
            "stop_loss": 95, "take_profit": 115,
        },
    })
    c = resp.content
    assert 0 <= c["trade_quality_score"] <= 100
    assert c["risk_management_score"] >= 60
    assert isinstance(c["improvements"], list)


@pytest.mark.asyncio
async def test_rule_based_recommendation_flags_high_dd():
    resp = await RuleBasedProvider().generate("recommendation", "v1", {
        "portfolio_stats": {"win_rate": 0.3, "max_drawdown_pct": 20},
    })
    types = {r["type"] for r in resp.content["recommendations"]}
    assert "risk_reduction" in types


@pytest.mark.asyncio
async def test_rule_based_risk_grade():
    resp = await RuleBasedProvider().generate("risk_analysis", "v1", {
        "stats": {"max_drawdown_pct": 2.0},
    })
    assert resp.content["risk_grade"] in {"A", "B", "C", "D", "F"}
