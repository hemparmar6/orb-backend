"""AIService — the single entrypoint every AI feature depends on.

* Primary provider is chosen by ``settings.AI_PROVIDER``.
* If ``EMERGENT_LLM_KEY`` is unset / ``AI_ENABLED=false`` we route all
  calls to the rule-based fallback provider (no startup failure).
* Cache: Redis, keyed by prompt + input hash. Missing Redis = no cache.
* Audit: every call persisted to ``ai_audit_log`` (never blocks user).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.ai.audit import AIAuditLogger
from app.ai.cache import AICache
from app.ai.providers.base import AIProvider, AIResponse
from app.ai.providers.fallback import RuleBasedProvider
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AIRequestContext:
    user_id: Optional[str]
    request_type: str
    prompt_name: str
    prompt_version: str
    payload: Dict[str, Any]


class AIService:
    def __init__(
        self,
        primary: AIProvider,
        fallback: AIProvider,
        cache: AICache,
        audit: AIAuditLogger,
        enabled: bool = True,
        request_timeout_s: float = 25.0,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._cache = cache
        self._audit = audit
        self._enabled = enabled
        self._timeout = request_timeout_s

    async def analyse(self, ctx: AIRequestContext) -> AIResponse:
        cache_key = self._cache_key(ctx)

        cached = await self._cache.get(cache_key)
        if cached is not None:
            logger.debug("ai.cache_hit %s", cache_key)
            resp = AIResponse(**cached, cached=True)
            await self._audit.log(ctx, resp, source="cached")
            return resp

        if not self._enabled:
            return await self._run_fallback(ctx, "ai_disabled")

        try:
            resp = await asyncio.wait_for(
                self._primary.generate(ctx.prompt_name, ctx.prompt_version, ctx.payload),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            return await self._run_fallback(ctx, "primary_timeout")
        except Exception as exc:  # noqa: BLE001
            logger.exception("ai.primary_failed")
            return await self._run_fallback(ctx, f"primary_error:{type(exc).__name__}")

        await self._cache.set(cache_key, resp.to_dict())
        await self._audit.log(ctx, resp, source="primary")
        return resp

    async def _run_fallback(self, ctx: AIRequestContext, reason: str) -> AIResponse:
        resp = await self._fallback.generate(ctx.prompt_name, ctx.prompt_version, ctx.payload)
        resp.meta["fallback_reason"] = reason
        await self._audit.log(ctx, resp, source="fallback")
        return resp

    def _cache_key(self, ctx: AIRequestContext) -> str:
        h = hashlib.sha256(
            json.dumps(ctx.payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        return f"ai:{ctx.request_type}:{ctx.prompt_name}:{ctx.prompt_version}:{h}"


# --- process-wide factory ------------------------------------------------

_singleton: Optional[AIService] = None


def get_ai_service() -> AIService:
    global _singleton
    if _singleton is not None:
        return _singleton

    key = (settings.EMERGENT_LLM_KEY or "").strip()
    ai_enabled = bool(settings.AI_ENABLED and key)

    primary: AIProvider
    if ai_enabled and settings.AI_PROVIDER in {"gpt52", "openai", "anthropic", "gemini"}:
        # Map v1.0.0 alias 'gpt52' -> openai family for backward compat.
        family = "openai" if settings.AI_PROVIDER in {"gpt52", "openai"} else settings.AI_PROVIDER
        try:
            from app.ai.providers.emergent_llm_provider import EmergentLLMProvider
            primary = EmergentLLMProvider(
                api_key=key,
                family=family,
                model=settings.AI_MODEL,
                temperature=settings.AI_TEMPERATURE,
                max_tokens=settings.AI_MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            logger.warning("ai.emergent_llm_unavailable: %s", exc)
            primary = RuleBasedProvider()
            ai_enabled = False
    else:
        # Either explicitly disabled, key missing, or unknown provider.
        primary = RuleBasedProvider()
        ai_enabled = False if not key else ai_enabled

    _singleton = AIService(
        primary=primary,
        fallback=RuleBasedProvider(),
        cache=AICache(ttl_s=settings.AI_CACHE_TTL_S),
        audit=AIAuditLogger(),
        enabled=ai_enabled,
        request_timeout_s=settings.AI_REQUEST_TIMEOUT_S,
    )
    return _singleton
