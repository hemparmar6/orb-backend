"""Module 9 — AI subsystem.

Public entrypoint is :class:`app.ai.service.AIService`. Everything else
(providers, prompts, cache, audit) is an implementation detail and MUST
NOT be imported directly from trading / broker / engine code.

If ``EMERGENT_LLM_KEY`` is unset the service silently falls back to the
deterministic rule-based provider — the API keeps working, no startup
failure. See ``app.core.config.Settings.ai_enabled_effective``.
"""

from app.ai.service import AIRequestContext, AIService, get_ai_service  # noqa: F401
