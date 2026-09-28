"""Portfolio insight prompt (v1)."""
from __future__ import annotations

import json
from typing import Any, Dict

from app.ai.prompts.registry import Prompt

_SYSTEM = """You produce a mobile-friendly portfolio insight.
Return ONLY a JSON object:
{
  "highlights": string[],
  "daily_headline": string,
  "weekly_headline": string,
  "alerts": [{"severity": "info"|"warn"|"critical", "message": string}],
  "summary": string
}"""


def _render(payload: Dict[str, Any]) -> str:
    return "Portfolio snapshot:\n" + json.dumps(payload, default=str, indent=2)


PROMPT = Prompt(name="portfolio_insight", version="v1", system=_SYSTEM, render_user=_render)
