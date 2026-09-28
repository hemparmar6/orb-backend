"""Risk analysis prompt (v1)."""
from __future__ import annotations
import json
from typing import Any, Dict
from app.ai.prompts.registry import Prompt

_SYSTEM = """Assess portfolio and per-trade risk. Return JSON:
{"risk_grade":"A"|"B"|"C"|"D"|"F","key_risks":string[],"mitigations":string[],"summary":string}"""


def _render(payload: Dict[str, Any]) -> str:
    return "Risk context:\n" + json.dumps(payload, default=str, indent=2)


PROMPT = Prompt(name="risk_analysis", version="v1", system=_SYSTEM, render_user=_render)
