"""Module 9 — AI service fallback / timeout / disabled unit tests."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.providers.base import AIProvider, AIResponse
from app.ai.providers.fallback import RuleBasedProvider
from app.ai.service import AIRequestContext, AIService


class _EchoProvider(AIProvider):
    name = "echo"

    async def generate(self, prompt_name, prompt_version, payload):
        return AIResponse(
            content={"summary": f"ok:{prompt_name}"}, text="ok",
            provider=self.name, model="test",
            prompt_name=prompt_name, prompt_version=prompt_version,
        )


class _BoomProvider(AIProvider):
    name = "boom"

    async def generate(self, prompt_name, prompt_version, payload):
        raise RuntimeError("boom")


class _SlowProvider(AIProvider):
    name = "slow"

    async def generate(self, prompt_name, prompt_version, payload):
        await asyncio.sleep(1.0)
        return AIResponse(content={}, provider=self.name, model="test",
                          prompt_name=prompt_name, prompt_version=prompt_version)


class _NoopCache:
    async def get(self, k): return None
    async def set(self, k, v): return None


class _NoopAudit:
    async def log(self, ctx, resp, source): return None


def _ctx():
    return AIRequestContext(
        user_id="u1", request_type="trade_review",
        prompt_name="trade_review", prompt_version="v1",
        payload={"trade": {"id": "t1"}},
    )


@pytest.mark.asyncio
async def test_primary_success():
    svc = AIService(_EchoProvider(), _EchoProvider(), _NoopCache(), _NoopAudit())
    resp = await svc.analyse(_ctx())
    assert resp.provider == "echo"


@pytest.mark.asyncio
async def test_primary_exception_falls_back():
    svc = AIService(_BoomProvider(), RuleBasedProvider(), _NoopCache(), _NoopAudit())
    resp = await svc.analyse(_ctx())
    assert resp.provider == "rule_based"
    assert resp.meta["fallback_reason"].startswith("primary_error")


@pytest.mark.asyncio
async def test_timeout_falls_back():
    svc = AIService(_SlowProvider(), RuleBasedProvider(), _NoopCache(), _NoopAudit(),
                    request_timeout_s=0.05)
    resp = await svc.analyse(_ctx())
    assert resp.provider == "rule_based"
    assert resp.meta["fallback_reason"] == "primary_timeout"


@pytest.mark.asyncio
async def test_disabled_uses_fallback():
    svc = AIService(_EchoProvider(), RuleBasedProvider(), _NoopCache(), _NoopAudit(),
                    enabled=False)
    resp = await svc.analyse(_ctx())
    assert resp.provider == "rule_based"
    assert resp.meta["fallback_reason"] == "ai_disabled"


def test_get_ai_service_no_key_boots_ok(monkeypatch):
    """Missing EMERGENT_LLM_KEY must NOT crash the factory."""
    # Reset the module-level singleton so the factory reruns cleanly.
    import app.ai.service as ai_service_mod
    ai_service_mod._singleton = None
    from app.core.config import settings
    monkeypatch.setattr(settings, "EMERGENT_LLM_KEY", None, raising=False)
    monkeypatch.setattr(settings, "AI_ENABLED", True, raising=False)
    svc = ai_service_mod.get_ai_service()
    assert svc is not None
    assert svc._enabled is False  # degraded, but callable
