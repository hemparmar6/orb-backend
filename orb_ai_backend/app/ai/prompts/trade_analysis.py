"""Trade-review prompt (v1)."""
from __future__ import annotations

import json
from typing import Any, Dict

from app.ai.prompts.registry import Prompt

_SYSTEM = """You are an expert trading coach reviewing a completed trade.
Return ONLY a JSON object matching:
{
  "trade_quality_score": number (0-100),
  "entry_quality": number (0-100),
  "exit_quality": number (0-100),
  "risk_management_score": number (0-100),
  "rule_compliance": number (0-100),
  "emotional_flags": string[],
  "improvements": string[],
  "summary": string
}
Advisory only. Never instruct the system to place trades."""


def _render(payload: Dict[str, Any]) -> str:
    return "Trade:\n" + json.dumps(payload.get("trade", {}), default=str, indent=2)


PROMPT = Prompt(name="trade_review", version="v1", system=_SYSTEM, render_user=_render)
