"""Metrics-collector middleware.

Records latency and status code per request. Sits after RequestIDMiddleware
so the request-id is set when we log slow requests.
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.logging import get_logger
from app.monitoring.metrics import metrics

logger = get_logger("app.performance")


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            latency_ms = (time.perf_counter() - start) * 1000.0
            metrics.record_request(
                path=request.url.path or "/",
                method=request.method,
                status_code=status_code,
                latency_ms=latency_ms,
            )
            if latency_ms > settings.SLOW_REQUEST_THRESHOLD_MS:
                logger.warning(
                    "slow_request",
                    extra={
                        "category": "application",
                        "path": request.url.path,
                        "method": request.method,
                        "duration_ms": round(latency_ms, 2),
                        "status_code": status_code,
                    },
                )
