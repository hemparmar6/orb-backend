"""Redis-backed AI response cache.

Reuses the project's central ``app.core.redis.get_redis`` helper — this
means the same enable/disable rules and connection pooling as every
other Redis consumer in the platform.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


class AICache:
    def __init__(self, ttl_s: int = 600) -> None:
        self._ttl = ttl_s

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        client = await get_redis()
        if client is None:
            return None
        try:
            raw = await client.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("AICache.get failed: %s", exc)
            return None

    async def set(self, key: str, value: Dict[str, Any]) -> None:
        client = await get_redis()
        if client is None:
            return
        try:
            await client.set(key, json.dumps(value, default=str), ex=self._ttl)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AICache.set failed: %s", exc)
