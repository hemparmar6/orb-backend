"""HTTP security headers middleware.

Adds a conservative but modern baseline:
* Strict-Transport-Security (HSTS) — production only
* X-Content-Type-Options: nosniff
* X-Frame-Options: DENY (also enforced by CSP frame-ancestors)
* Referrer-Policy: no-referrer
* Permissions-Policy: locked-down defaults
* Content-Security-Policy: strict, but allows same-origin scripts+styles
  so the standalone React Admin Dashboard SPA continues to work.

Values are configurable via ``settings.SECURITY_*``.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings


DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        # Strict-Transport-Security — production only
        if settings.APP_ENV == "production" and settings.SECURITY_HSTS_ENABLED:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={settings.SECURITY_HSTS_MAX_AGE}; includeSubDomains; preload",
            )

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=()",
        )
        response.headers.setdefault(
            "Cross-Origin-Opener-Policy", "same-origin"
        )
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")

        if settings.SECURITY_CSP_ENABLED:
            csp = settings.SECURITY_CSP or DEFAULT_CSP
            response.headers.setdefault("Content-Security-Policy", csp)

        return response
