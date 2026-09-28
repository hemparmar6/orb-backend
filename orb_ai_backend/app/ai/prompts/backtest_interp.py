"""Backtest interpretation prompt (v1)."""
from __future__ import annotations
import json
from typing import Any, Dict
from app.ai.prompts.registry import Prompt

_SYSTEM = """Interpret backtest results (walk-forward, Monte Carlo, or plain). Return JSON:
{"verdict":"robust"|"acceptable"|"fragile"|"unreliable","key_findings":string[],"risks":string[],"next_steps":string[],"summary":string}"""


def _render(payload: Dict[str, Any]) -> str:
    return "Backtest results:\n" + json.dumps(payload, default=str, indent=2)


PROMPT = Prompt(name="backtest_interp", version="v1", system=_SYSTEM, render_user=_render)
