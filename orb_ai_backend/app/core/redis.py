"""Optional Redis client.

Redis is *not* required for the API or engine to boot. We expose a lazy
singleton that returns `None` when disabled or unavailable, so callers can
guard with `if client:`.

Future modules (WS fan-out, Celery, rate limiting) can rely on `get_redis()`
without any wiring changes.
"""
from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: Optional[aioredis.Redis] = None
_initialized = False


async def get_redis() -> Optional[aioredis.Redis]:
    """Return a connected Redis client, or None if disabled/unreachable."""
    global _client, _initialized

    if _initialized:
        return _client
    _initialized = True

    if not settings.REDIS_ENABLED:
        logger.info("redis_disabled")
        return None

    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
        )
        await client.ping()
        _client = client
        logger.info("redis_connected", extra={"url": _sanitize(settings.REDIS_URL)})
    except Exception as exc:  # pragma: no cover
        # Production + Redis explicitly enabled (REDIS_ENABLED=true, checked
        # above) means Redis is a REQUIRED dependency here. A connection
        # failure must NOT be silently swallowed — fail fast so startup
        # aborts instead of degrading to a broken multi-worker state. The URL
        # is sanitized so the password is never logged.
        if settings.is_production:
            _initialized = False  # allow a later retry to reconnect
            logger.error(
                "redis_required_unavailable",
                extra={"error": str(exc), "url": _sanitize(settings.REDIS_URL)},
            )
            raise
        # Development / staging / test: Redis stays optional — warn and run
        # without it (behaviour unchanged).
        logger.warning(
            "redis_unavailable_will_run_without_it",
            extra={"error": str(exc), "url": _sanitize(settings.REDIS_URL)},
        )
        _client = None

    return _client


async def close_redis() -> None:
    global _client, _initialized
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:  # pragma: no cover
            pass
    _client = None
    _initialized = False


def _sanitize(url: str) -> str:
    """Strip password from a Redis URL for logging."""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        _, host = rest.split("@", 1)
        return f"{scheme}://***@{host}"
    return url
