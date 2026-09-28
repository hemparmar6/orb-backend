"""Token-bucket rate limiter.

Backed by Redis when available (production, multi-worker), with an
in-memory fallback so tests, local dev, and single-process deployments
work without extra infrastructure.

Public API:
    limiter = RateLimiter(default_rate=60, per_seconds=60, burst=100)
    allowed, retry_after = await limiter.acquire("user:42")
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class BucketState:
    tokens: float
    last_refill: float


class RateLimiter:
    """Token bucket with optional Redis persistence."""

    def __init__(
        self,
        default_rate: float | None = None,
        per_seconds: int | None = None,
        burst: int | None = None,
    ) -> None:
        self.rate = float(default_rate if default_rate is not None else settings.RATE_LIMIT_PER_MINUTE)
        self.per = int(per_seconds if per_seconds is not None else 60)
        self.burst = int(burst if burst is not None else settings.RATE_LIMIT_BURST)
        self._local: dict[str, BucketState] = {}
        self._lock = RLock()

    async def acquire(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Try to consume ``cost`` tokens for ``key``.

        Returns (allowed, retry_after_seconds).
        """
        now = time.time()
        refill_per_second = self.rate / self.per if self.per else self.rate

        # ---- Try Redis first ----
        redis_ok = False
        client = None
        if settings.REDIS_ENABLED:
            try:
                from app.core.redis import get_redis
                client = await get_redis()
                redis_ok = client is not None
            except Exception:
                redis_ok = False

        if redis_ok and client is not None:
            try:
                r_key = f"rl:{key}"
                # Fetch current bucket
                data = await client.hgetall(r_key)
                tokens = float(data.get("tokens", self.burst)) if data else float(self.burst)
                last = float(data.get("last", now)) if data else now
                # Refill
                elapsed = max(0.0, now - last)
                tokens = min(float(self.burst), tokens + elapsed * refill_per_second)
                if tokens >= cost:
                    tokens -= cost
                    await client.hset(r_key, mapping={"tokens": tokens, "last": now})
                    await client.expire(r_key, max(self.per * 2, 60))
                    return True, 0.0
                needed = cost - tokens
                retry = needed / refill_per_second if refill_per_second > 0 else self.per
                await client.hset(r_key, mapping={"tokens": tokens, "last": now})
                await client.expire(r_key, max(self.per * 2, 60))
                return False, round(retry, 2)
            except Exception as exc:  # pragma: no cover
                logger.warning("rate_limit_redis_failed_fallback_memory", extra={"error": str(exc)})

        # ---- In-memory fallback ----
        with self._lock:
            state = self._local.get(key)
            if state is None:
                state = BucketState(tokens=float(self.burst), last_refill=now)
                self._local[key] = state
            elapsed = max(0.0, now - state.last_refill)
            state.tokens = min(float(self.burst), state.tokens + elapsed * refill_per_second)
            state.last_refill = now
            if state.tokens >= cost:
                state.tokens -= cost
                return True, 0.0
            needed = cost - state.tokens
            retry = needed / refill_per_second if refill_per_second > 0 else self.per
            return False, round(retry, 2)


# Process-scoped default limiter (also used by the middleware)
default_limiter: Optional[RateLimiter] = None


def get_default_limiter() -> RateLimiter:
    global default_limiter
    if default_limiter is None:
        default_limiter = RateLimiter()
    return default_limiter
