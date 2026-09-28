"""AI audit logger — persists every request/response to ``ai_audit_log``.

Uses ``app.db.session.async_session_factory`` directly (independent
short-lived session) so audit never contends with the request-scoped
session that produced the AI call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from app.ai.providers.base import AIResponse
from app.db.session import async_session_factory
from app.models.ai import AIAuditLog

logger = logging.getLogger(__name__)


class AIAuditLogger:
    async def log(self, ctx, resp: AIResponse, source: str) -> None:
        record: Dict[str, Any] = {
            "user_id": getattr(ctx, "user_id", None),
            "request_type": ctx.request_type,
            "prompt_name": ctx.prompt_name,
            "prompt_version": ctx.prompt_version,
            "provider": resp.provider,
            "model": resp.model,
            "source": source,
            "cached": bool(resp.cached),
            "meta": resp.meta or {},
        }
        logger.info("ai.audit %s", {k: v for k, v in record.items() if k != "meta"})
        try:
            async with async_session_factory() as db:
                db.add(AIAuditLog(
                    user_id=record["user_id"],
                    request_type=record["request_type"],
                    prompt_name=record["prompt_name"],
                    prompt_version=record["prompt_version"],
                    provider=record["provider"],
                    model=record["model"],
                    source=record["source"],
                    cached=record["cached"],
                    meta_data=record["meta"],
                    created_at=datetime.now(timezone.utc),
                ))
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            # Audit MUST NOT break user-facing flows.
            logger.warning("ai.audit persist failed: %s", exc)
