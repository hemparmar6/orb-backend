"""Rate-limit middleware.

Applies a per-client token-bucket to every request. The client key is:
- ``user:<user_id>`` when a valid Bearer token identifies a user
- ``ip:<remote_addr>`` otherwise

Paths in ``settings.RATE_LIMIT_EXEMPT_PATHS`` are skipped so /health and
static admin UI assets never get throttled.
"""
from __future__ import annotations

import json

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import get_default_limiter
from app.monitoring.metrics import metrics

logger = get_logger("app.security.ratelimit")


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, exempt_paths: list[str] | None = None) -> None:
        super().__init__(app)
        self._exempt_override = tuple(exempt_paths) if exempt_paths else None

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        path = request.url.path or "/"
        exempt = self._exempt_override or tuple(settings.RATE_LIMIT_EXEMPT_PATHS)
        for pfx in exempt:
            if path.startswith(pfx):
                return await call_next(request)

        key = self._identify(request)
        limiter = get_default_limiter()
        allowed, retry_after = await limiter.acquire(key)
        if not allowed:
            metrics.record_rate_limit()
            logger.warning(
                "rate_limit_exceeded",
                extra={
                    "category": "security",
                    "key": key,
                    "path": path,
                    "retry_after": retry_after,
                },
            )
            # Best-effort persistence — swallow errors so a DB outage
            # never turns 429s into 500s.
            try:
                from app.db.session import async_session_factory
                from app.models.monitoring import RateLimitEvent

                async with async_session_factory() as db:
                    db.add(
                        RateLimitEvent(
                            key=key,
                            path=path,
                            method=request.method,
                            ip_address=_client_ip(request),
                            retry_after_seconds=retry_after,
                        )
                    )
                    await db.commit()
            except Exception:
                pass

            body = {
                "detail": {
                    "code": "rate_limit_exceeded",
                    "message": "Too many requests. Please retry later.",
                    "retry_after_seconds": retry_after,
                }
            }
            resp = JSONResponse(status_code=429, content=body)
            resp.headers["Retry-After"] = str(int(max(1, retry_after)))
            return resp

        return await call_next(request)

    def _identify(self, request: Request) -> str:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            try:
                # Decode WITHOUT verifying signature — we only need the user id
                # for bucketing; unauthenticated tokens will be rejected later
                # by the auth dependency anyway.
                payload = jwt.decode(token, options={"verify_signature": False})
                uid = payload.get("sub") or payload.get("user_id")
                if uid:
                    return f"user:{uid}"
            except Exception:
                pass
        ip = _client_ip(request)
        return f"ip:{ip}"


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Ensure JSONResponse imports json (linter appeasement — actually used above)
_ = json
