"""Redis-backed store adapters for Milestone 8 ExecutionSafetyService counters.

Adds a **transparent** Redis path for sliding-window counters, duplicate
detection and breach tracking. Interface is 100% source-compatible with
the in-process ``_CounterStore``; the switch is decided lazily by
``settings.REDIS_ENABLED`` + successful `PING`.

Design
------
* Sliding windows use sorted-sets: member = ``<now>:<monotonic_ns>``,
  score = wall-clock seconds. ``ZREMRANGEBYSCORE + ZCARD`` implement
  trim+count in a single pipelined round-trip.
* Duplicate fingerprints use ``SET fp 1 NX EX``. Returns True if new,
  False if already seen.
* Breach counters are stored the same way as event windows.
* All Redis operations are wrapped in try/except; on any error the
  operation returns a sentinel value that lets ExecutionSafetyService
  seamlessly fall through to its legacy in-process store — so a broken
  Redis never blocks order flow.
* Keys are namespaced under ``exec_safety:*`` so an operator can flush
  them without touching other Redis data.

Multi-worker safety
-------------------
The Redis SET/ZADD/ZREM operations are inherently atomic. Multiple
uvicorn workers pointing at the same Redis instance share the same
sliding windows, meaning per-user + global rate limits are enforced
correctly across workers.

Backward compatibility
----------------------
* If ``settings.REDIS_ENABLED=false`` (the default in the test env),
  ``CounterStoreAdapter`` proxies every call to the existing
  ``_CounterStore`` singleton without touching Redis. Behaviour is
  100% identical to pre-follow-up.
* If ``REDIS_ENABLED=true`` but Redis becomes unreachable at runtime,
  operations degrade to the in-process fallback with a warning log.
* ``reset_state_for_tests()`` also flushes the Redis namespace so the
  existing pytest suite continues to pass unchanged.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)


# ---- keys ---------------------------------------------------------------

NS = "exec_safety"
DUP_KEY = f"{NS}:dup:{{fp}}"
USER_TPS = f"{NS}:u:{{uid}}:tps"
USER_OPM = f"{NS}:u:{{uid}}:opm"
USER_OPH = f"{NS}:u:{{uid}}:oph"
GLOBAL_OPS = f"{NS}:g:ops"
GLOBAL_OPM = f"{NS}:g:opm"
USER_BREACH = f"{NS}:u:{{uid}}:breach"
BOT_BREACH = f"{NS}:b:{{bid}}:breach"


class RedisCounters:
    """Sliding-window counter + duplicate primitives on Redis.

    Every method returns ``None`` (or ``-1``) on Redis failure so the
    caller can fall back cleanly. Otherwise the return is what the
    equivalent in-memory operation would have produced.
    """

    def __init__(self) -> None:
        self._client_cached: Optional[object] = None
        self._lock = asyncio.Lock()

    # -- lazy client -----------------------------------------------------

    async def _client(self):
        if self._client_cached is not None:
            return self._client_cached
        async with self._lock:
            if self._client_cached is not None:
                return self._client_cached
            try:
                c = await get_redis()
                if c is None:
                    return None
                self._client_cached = c
                return c
            except Exception:  # pragma: no cover
                logger.exception("redis_counters_client_init_failed")
                return None

    @property
    def enabled(self) -> bool:
        """True when the operator has opted-in; runtime availability is
        checked lazily on each call."""
        return bool(settings.REDIS_ENABLED)

    # -- sliding window --------------------------------------------------

    async def trim_and_count(self, key: str, window_seconds: float) -> int:
        """Return current count in the window, trimming stale entries.
        Returns ``-1`` if Redis is unavailable so caller falls back."""
        c = await self._client()
        if c is None:
            return -1
        try:
            now = time.time()
            cutoff = now - window_seconds
            pipe = c.pipeline(transaction=True)
            pipe.zremrangebyscore(key, 0, cutoff)
            pipe.zcard(key)
            _, count = await pipe.execute()
            return int(count)
        except Exception:  # pragma: no cover
            logger.exception("redis_counters_trim_and_count_failed",
                             extra={"key": key})
            return -1

    async def oldest_score(self, key: str) -> Optional[float]:
        """Return the smallest score currently in the sorted-set (i.e.
        the timestamp of the oldest entry), or None on Redis error /
        empty set. Used to compute ``retry_after`` correctly."""
        c = await self._client()
        if c is None:
            return None
        try:
            res = await c.zrange(key, 0, 0, withscores=True)
            if not res:
                return None
            _, score = res[0]
            return float(score)
        except Exception:  # pragma: no cover
            logger.exception("redis_counters_oldest_score_failed",
                             extra={"key": key})
            return None

    async def add(self, key: str, window_seconds: float) -> bool:
        """Record one event. Sets a TTL so no key leaks forever."""
        c = await self._client()
        if c is None:
            return False
        try:
            now = time.time()
            # unique member so multiple events in the same microsecond
            # don't collapse into a single ZADD entry
            member = f"{now:.6f}:{time.monotonic_ns()}"
            pipe = c.pipeline(transaction=True)
            pipe.zadd(key, {member: now})
            # window * 2 + small buffer so an idle key eventually clears
            pipe.expire(key, int(max(60, window_seconds * 2 + 10)))
            await pipe.execute()
            return True
        except Exception:  # pragma: no cover
            logger.exception("redis_counters_add_failed",
                             extra={"key": key})
            return False

    # -- duplicate detection --------------------------------------------

    async def duplicate_seen(self, fingerprint: str, window_seconds: float
                             ) -> Optional[bool]:
        """SET NX EX. Returns True if the fp is a duplicate (i.e. already
        set), False if it's a fresh set, None on Redis error."""
        c = await self._client()
        if c is None:
            return None
        try:
            key = DUP_KEY.format(fp=fingerprint)
            # SET k 1 NX EX <ttl>
            ok = await c.set(key, "1", ex=int(max(1, window_seconds * 2)), nx=True)
            # redis-py returns True on success, None on nx-collision
            return not bool(ok)
        except Exception:  # pragma: no cover
            logger.exception("redis_counters_duplicate_failed")
            return None

    # -- flush (tests) ---------------------------------------------------

    async def flush_namespace(self) -> int:
        """Delete every key under ``exec_safety:*``. Test-only."""
        c = await self._client()
        if c is None:
            return 0
        try:
            removed = 0
            async for k in c.scan_iter(match=f"{NS}:*", count=200):
                await c.delete(k)
                removed += 1
            return removed
        except Exception:  # pragma: no cover
            return 0


# process-scoped singleton
_redis_counters = RedisCounters()


def get_redis_counters() -> RedisCounters:
    return _redis_counters
