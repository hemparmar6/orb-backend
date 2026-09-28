"""Shared HTTP client for live broker adapters.

Provides ``BrokerHTTPClient`` — a thin wrapper around ``httpx.AsyncClient`` with:
- Exponential-backoff retry on transient errors (network, 5xx, 429)
- Idempotency-key propagation for POST/PUT/DELETE (to make retries safe)
- Structured logging via ``app.core.logging``
- Timeout handling (connect / read / total)
- A single, consistent error normalisation path that converts every non-2xx
  broker response into ``BrokerError`` with the raw payload attached.

Design constraints:
- Never leaks credentials into logs or exceptions (credentials in ``base_headers``
  are redacted).
- All methods are async and reentrant — the underlying ``httpx.AsyncClient`` is
  created lazily on first use and closed by ``aclose()`` / ``__aexit__``.
- Callers pass ``idempotency_key`` explicitly; the client never invents keys.
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Iterable, Mapping, Optional

import httpx

from app.core.exceptions import BrokerError
from app.core.logging import get_logger

logger = get_logger(__name__)


# HTTP status codes that are safe to retry.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
# Header keys whose values must never be logged.
_REDACT_HEADERS = frozenset(
    {
        "authorization",
        "access-token",
        "x-api-key",
        "x-session-token",
        "sid",
        "auth",
        "consumer-key",
        "consumer-secret",
        "neo-fin-key",
    }
)


def _redact(headers: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values masked."""
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _REDACT_HEADERS:
            out[k] = "***"
        else:
            out[k] = v
    return out


class BrokerHTTPClient:
    """Retrying HTTP client for broker REST APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        broker: str,
        base_headers: Optional[Mapping[str, str]] = None,
        timeout_s: float = 15.0,
        connect_timeout_s: float = 5.0,
        max_retries: int = 3,
        backoff_base_s: float = 0.4,
        backoff_max_s: float = 4.0,
        retry_status: Iterable[int] = _RETRYABLE_STATUS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._broker = broker
        self._base_headers = dict(base_headers or {})
        self._timeout = httpx.Timeout(timeout_s, connect=connect_timeout_s)
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._retry_status = frozenset(retry_status)
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

    # ---- lifecycle -----------------------------------------------------

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        base_url=self._base_url,
                        timeout=self._timeout,
                        headers=self._base_headers,
                    )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # pragma: no cover
                pass
            self._client = None

    async def __aenter__(self) -> "BrokerHTTPClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def update_base_headers(self, headers: Mapping[str, str]) -> None:
        """Merge ``headers`` into the base header set (e.g. after token refresh)."""
        self._base_headers.update(headers)
        if self._client is not None:
            self._client.headers.update(headers)

    # ---- core request path --------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        extra_headers: Optional[Mapping[str, str]] = None,
        idempotency_key: Optional[str] = None,
        allow_status: Iterable[int] = (),
        retry: Optional[bool] = None,
    ) -> httpx.Response:
        """Perform an HTTP request with retry semantics.

        Retries on:
        - ``httpx.TransportError`` (connection / read / write errors)
        - HTTP status in ``self._retry_status``

        Raises ``BrokerError`` if all retries are exhausted or the response is
        a non-2xx status not present in ``allow_status``.
        """
        method_u = method.upper()
        # Default: retry only idempotent methods unless caller opts-in.
        if retry is None:
            retry = method_u in {"GET", "HEAD", "OPTIONS"} or idempotency_key is not None
        allow = frozenset(allow_status)
        client = await self._ensure_client()

        headers: dict[str, str] = {}
        if extra_headers:
            headers.update(extra_headers)
        if idempotency_key:
            headers.setdefault("Idempotency-Key", idempotency_key)

        attempt = 0
        last_exc: Optional[Exception] = None
        while True:
            attempt += 1
            try:
                response = await client.request(
                    method_u,
                    path,
                    params=params,
                    json=json_body,
                    headers=headers or None,
                )
            except httpx.TransportError as exc:
                last_exc = exc
                if not retry or attempt > self._max_retries:
                    logger.warning(
                        "broker_http_transport_error_final",
                        extra={
                            "broker": self._broker,
                            "method": method_u,
                            "path": path,
                            "attempt": attempt,
                            "error": str(exc),
                        },
                    )
                    raise BrokerError(
                        f"{self._broker} transport error: {exc}",
                        details={"path": path, "method": method_u},
                    ) from exc
                await self._sleep_backoff(attempt)
                continue

            # 2xx / allowed status → return.
            if response.status_code < 400 or response.status_code in allow:
                return response

            # Retry on 5xx / 429 / 408 for idempotent operations.
            if (
                retry
                and response.status_code in self._retry_status
                and attempt <= self._max_retries
            ):
                logger.info(
                    "broker_http_retry",
                    extra={
                        "broker": self._broker,
                        "method": method_u,
                        "path": path,
                        "status": response.status_code,
                        "attempt": attempt,
                    },
                )
                await self._sleep_backoff(attempt, response=response)
                continue

            # Non-retryable failure — surface as BrokerError.
            self._raise_http_error(response, method_u, path)

        # unreachable
        raise BrokerError(
            f"{self._broker} request failed: {last_exc}",
            details={"path": path, "method": method_u},
        )

    async def _sleep_backoff(
        self, attempt: int, *, response: Optional[httpx.Response] = None
    ) -> None:
        # Honour Retry-After when the broker returns one.
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                try:
                    await asyncio.sleep(min(float(retry_after), self._backoff_max_s))
                    return
                except ValueError:  # pragma: no cover - defensive
                    pass
        delay = min(self._backoff_base_s * (2 ** (attempt - 1)), self._backoff_max_s)
        # Full jitter — spread reconnects across clients.
        await asyncio.sleep(random.uniform(0, delay))

    def _raise_http_error(
        self, response: httpx.Response, method: str, path: str
    ) -> None:
        text = response.text[:1024]
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            body = {"raw": text}
        logger.warning(
            "broker_http_error",
            extra={
                "broker": self._broker,
                "method": method,
                "path": path,
                "status": response.status_code,
                "headers": _redact(response.request.headers if response.request else {}),
                "body_preview": text,
            },
        )
        msg = _extract_error_message(body, response.status_code)
        raise BrokerError(
            f"{self._broker} responded {response.status_code}: {msg}",
            status_code=502,
            details={
                "broker": self._broker,
                "http_status": response.status_code,
                "path": path,
                "method": method,
                "body": body,
            },
        )

    # ---- convenience wrappers ----------------------------------------

    async def get(self, path: str, **kwargs: Any) -> Any:
        r = await self.request("GET", path, **kwargs)
        return _safe_json(r)

    async def post(self, path: str, json_body: Any = None, **kwargs: Any) -> Any:
        r = await self.request("POST", path, json_body=json_body, **kwargs)
        return _safe_json(r)

    async def put(self, path: str, json_body: Any = None, **kwargs: Any) -> Any:
        r = await self.request("PUT", path, json_body=json_body, **kwargs)
        return _safe_json(r)

    async def delete(self, path: str, **kwargs: Any) -> Any:
        r = await self.request("DELETE", path, **kwargs)
        return _safe_json(r)


def _safe_json(response: httpx.Response) -> Any:
    if not response.content:
        return {}
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return {"raw": response.text}


def _extract_error_message(body: Any, status: int) -> str:
    """Best-effort extraction of a human-readable message from a broker error body."""
    if not isinstance(body, dict):
        return f"HTTP {status}"
    for key in ("message", "errorMessage", "error", "detail", "remarks", "internalErrorMessage"):
        val = body.get(key)
        if isinstance(val, str) and val:
            return val
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("message", "errorMessage", "error"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            for key in ("message", "errorMessage", "error"):
                val = first.get(key)
                if isinstance(val, str) and val:
                    return val
    return f"HTTP {status}"
