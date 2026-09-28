"""Strategy review prompt (v1)."""
from __future__ import annotations
import json
from typing import Any, Dict
from app.ai.prompts.registry import Prompt

_SYSTEM = """Review a strategy's aggregate performance. Return JSON:
{"strengths":string[],"weaknesses":string[],"parameter_hints":string[],"summary":string}. Advisory only."""


def _render(payload: Dict[str, Any]) -> str:
    return "Strategy performance:\n" + json.dumps(payload, default=str, indent=2)


PROMPT = Prompt(name="strategy_review", version="v1", system=_SYSTEM, render_user=_render)
