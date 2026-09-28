"""GPT-5.2 provider via the Emergent Universal LLM key.

Uses the pre-installed ``emergentintegrations`` package. Failure to
import that package or missing API key does NOT crash the process — the
factory in :mod:`app.ai.service` degrades to the rule-based provider
before this class is ever instantiated when the key is missing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict

from app.ai.prompts.registry import get_prompt
from app.ai.providers.base import AIProvider, AIResponse

logger = logging.getLogger(__name__)


class GPT52Provider(AIProvider):
    name = "gpt52"

    def __init__(self, api_key: str, model: str = "gpt-5.2") -> None:
        self._api_key = api_key
        self._model = model

    async def generate(
        self, prompt_name: str, prompt_version: str, payload: Dict[str, Any]
    ) -> AIResponse:
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # local import

        prompt = get_prompt(prompt_name, prompt_version)
        user_text = prompt.render_user(payload)
        session_id = self._session_id(prompt_name, prompt_version, payload)

        chat = LlmChat(
            api_key=self._api_key,
            session_id=session_id,
            system_message=prompt.system,
        ).with_model("openai", self._model)

        raw = await chat.send_message(UserMessage(text=user_text))
        content = self._parse_json(raw)

        return AIResponse(
            content=content,
            text=content.get("summary", "") if isinstance(content, dict) else str(raw)[:500],
            provider=self.name,
            model=self._model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            meta={"raw_length": len(raw or "")},
        )

    # ---------------- helpers ----------------

    @staticmethod
    def _session_id(prompt_name: str, version: str, payload: Dict[str, Any]) -> str:
        h = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        return f"{prompt_name}:{version}:{h}"

    @staticmethod
    def _parse_json(raw: str) -> Dict[str, Any]:
        if not raw:
            return {}
        m = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S)
        candidate = m.group(1) if m else raw
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            m2 = re.search(r"\{.*\}", candidate, re.S)
            if m2:
                try:
                    return json.loads(m2.group(0))
                except json.JSONDecodeError:
                    pass
            logger.warning("gpt52 non-JSON response: %r", raw[:200])
            return {"summary": raw.strip()[:1000]}
