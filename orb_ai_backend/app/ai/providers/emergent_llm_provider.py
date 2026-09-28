"""Universal Emergent LLM provider (OpenAI / Claude / Gemini).

Uses the pre-installed ``emergentintegrations`` package. A single
``EMERGENT_LLM_KEY`` works across OpenAI, Anthropic Claude, and Google
Gemini — the provider family + model are selected via configuration:

    AI_LLM_FAMILY = "openai" | "anthropic" | "gemini"
    AI_MODEL      = "gpt-5.2" | "claude-sonnet-4.5" | "gemini-3-flash" ...

Failing to import ``emergentintegrations`` or missing key does NOT
crash — the AIService factory falls back to :class:`RuleBasedProvider`
before this class is instantiated.
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


_ALLOWED_FAMILIES = {"openai", "anthropic", "gemini"}


class EmergentLLMProvider(AIProvider):
    """Provider-agnostic LLM adapter over the Emergent Universal LLM key.

    ``family`` selects the underlying provider family used by
    ``emergentintegrations`` (``openai``, ``anthropic`` or ``gemini``).
    ``model`` is the concrete model name (e.g. ``gpt-5.2``,
    ``claude-sonnet-4.5``, ``gemini-3-flash``).
    """

    def __init__(
        self,
        *,
        api_key: str,
        family: str = "openai",
        model: str = "gpt-5.2",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        family_norm = (family or "openai").strip().lower()
        if family_norm not in _ALLOWED_FAMILIES:
            raise ValueError(
                f"Unsupported AI family {family!r}. "
                f"Choose from {sorted(_ALLOWED_FAMILIES)}."
            )
        self._api_key = api_key
        self._family = family_norm
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self.name = f"emergent_{family_norm}"

    async def generate(
        self, prompt_name: str, prompt_version: str, payload: Dict[str, Any]
    ) -> AIResponse:
        # Lazy import so tests without emergentintegrations don't crash on import.
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore

        prompt = get_prompt(prompt_name, prompt_version)
        user_text = prompt.render_user(payload)
        session_id = self._session_id(prompt_name, prompt_version, payload)

        chat = LlmChat(
            api_key=self._api_key,
            session_id=session_id,
            system_message=prompt.system,
        ).with_model(self._family, self._model)

        raw = await chat.send_message(UserMessage(text=user_text))
        content = self._parse_json(raw)

        return AIResponse(
            content=content,
            text=content.get("summary", "") if isinstance(content, dict) else str(raw)[:500],
            provider=self.name,
            model=self._model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            meta={
                "raw_length": len(raw or ""),
                "family": self._family,
            },
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
            logger.warning("emergent_llm non-JSON response: %r", raw[:200])
            return {"summary": raw.strip()[:1000]}
