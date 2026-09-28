"""Recommendation prompt (v1)."""
from __future__ import annotations

import json
from typing import Any, Dict

from app.ai.prompts.registry import Prompt

_SYSTEM = """You generate advisory trading recommendations. NEVER place, modify or cancel trades.
Return ONLY a JSON object matching:
{
  "recommendations": [
    {
      "type": "position_sizing" | "risk_reduction" | "capital_allocation" | "strategy" | "portfolio_balancing",
      "action": string,
      "rationale": string,
      "confidence": number (0.0-1.0),
      "priority": "low" | "medium" | "high"
    }
  ],
  "summary": string
}"""


def _render(payload: Dict[str, Any]) -> str:
    return "Portfolio context:\n" + json.dumps(payload, default=str, indent=2)


PROMPT = Prompt(name="recommendation", version="v1", system=_SYSTEM, render_user=_render)
