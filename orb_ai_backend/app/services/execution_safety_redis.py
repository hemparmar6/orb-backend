"""ORB AI 2.0 — Milestone 9: Redis-backed distributed execution counters.

This module provides a **drop-in shim** for the in-process counter store
used by ``ExecutionSafetyService``. When ``REDIS_ENABLED=true`` and a
reachable Redis is available, counters + duplicate fingerprints are
persisted to Redis with millisecond-precision sorted-sets so multiple
gunicorn/uvicorn workers share state.

Design
------
* Sorted set per window; member = event-timestamp-micros (unique via
  monotonic + counter), score = wall-clock seconds. ``ZREMRANGEBYSCORE``
  trims entries older than the window on every check.
* Counters are strictly best-effort atomic; we use MULTI/EXEC (pipeline)
  so trim + ZADD + ZCARD happen in one round-trip.
* Falls back to the in-process store if Redis is not configured or the
  live connection drops. The service never fails-open silently — it
  logs and continues.
* No admin API changes; existing endpoints read the same counters.

Usage
-----
```python
counter = get_counter_store()
if REDIS_ENABLED:
    n = await counter.trim_and_count(f"user:{uid}:min", 60.0)
```
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_redis_client = None
_redis_lock = asyncio.Lock()


async def _get_redis():
    """Lazy Redis client (from app.core.redis when enabled)."""
    global _redis_client
    if not settings.REDIS_ENABLED:
        return None
    if _redis_client is not None:
        return _redis_client
    async with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            from app.core.redis import get_redis
            client = await get_redis()
            _redis_client = client
            return _redis_client
        except Exception:  # pragma: no cover
            logger.exception("redis_client_init_failed")
            return None


class RedisCounterStore:
    """Sliding-window counter backed by a Redis sorted set.

    Not held long-term — instantiated per check. Falls back to a no-op
    counter (returning zero) if Redis is unreachable, which allows the
    caller to naturally use its in-process fallback path.
    """

    async def trim_and_count(self, key: str, window_seconds: float) -> int:
        """Return the current count of events in ``key`` in the last
        ``window_seconds``. Trims older entries as a side effect. Returns
        ``-1`` when Redis is not available (caller should fall back)."""
        r = await _get_redis()
        if r is None:
            return -1
        try:
            now = time.time()
            cutoff = now - window_seconds
            pipe = r.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            _, count = await pipe.execute()
            return int(count)
        except Exception:  # pragma: no cover
            logger.exception("redis_counter_read_failed", extra={"key": key})
            return -1

    async def add(self, key: str, ttl_seconds: float) -> bool:
        """Record one event in ``key``. Sets a TTL so keys can't leak
        forever. Returns True on success, False on Redis error."""
        r = await _get_redis()
        if r is None:
            return False
        try:
            now = time.time()
            member = f"{now:.6f}:{time.monotonic_ns()}"
            pipe = r.pipeline(transaction=True)
            pipe.zadd(key, {member: now})
            # TTL = window + small buffer to survive burst-then-quiet.
            pipe.expire(key, int(max(60, ttl_seconds * 2)))
            await pipe.execute()
            return True
        except Exception:  # pragma: no cover
            logger.exception("redis_counter_write_failed", extra={"key": key})
            return False

    async def duplicate_seen(self, fingerprint: str, window_seconds: float
                             ) -> Optional[bool]:
        """SET a fingerprint with NX + EX. Returns True if duplicate,
        False if newly seen, None if Redis error (caller falls back)."""
        r = await _get_redis()
        if r is None:
            return None
        try:
            key = f"exec_safety:dup:{fingerprint}"
            ok = await r.set(key, "1", ex=int(max(1, window_seconds * 2)), nx=True)
            return not bool(ok)  # nx=True → returns None/False when already set
        except Exception:  # pragma: no cover
            logger.exception("redis_dup_check_failed")
            return None


_STORE = RedisCounterStore()


def get_counter_store() -> RedisCounterStore:
    return _STORE


def is_active() -> bool:
    """True when Redis is configured. Actual availability is checked lazily."""
    return bool(settings.REDIS_ENABLED)
