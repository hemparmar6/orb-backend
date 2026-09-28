"""Upstox credential authority — READ-ONLY market data vs ORDER EXECUTION.

This module is the *single* place that decides which Upstox token is allowed
for which purpose. It exists to make the Analytics-token restriction explicit
and enforceable in code (see the ORB backend-repair Phase 4/5 requirements):

* **Read-only market data** (quotes, historical/intraday candles, the
  market-data WebSocket) may authenticate with either credential, in strict
  priority order:

      1. ``UPSTOX_ACCESS_TOKEN``   (full token — preferred when present)
      2. ``UPSTOX_ANALYTICS_TOKEN`` (read-only fallback)

* **Order execution** (place / modify / cancel / any write) may authenticate
  with the full ``UPSTOX_ACCESS_TOKEN`` **only**. The Analytics token is
  *rejected* — Upstox Analytics tokens cannot place orders, and even if they
  could we must never risk sending a read-only credential down a write path.

Hard rules honoured everywhere here:

* Never log a token.
* Never return a token from an API.
* Never persist a token.
* Never embed a real token in source.
* Fail **closed**: when no usable credential exists, raise a clear engine
  error rather than degrade to mock/unauthenticated behaviour.
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.exceptions import EngineError

__all__ = [
    "SOURCE_ACCESS_TOKEN",
    "SOURCE_ANALYTICS_TOKEN",
    "has_market_data_token",
    "resolve_market_data_token",
    "market_data_token_source",
    "market_data_credentials",
    "resolve_order_execution_token",
    "assert_not_analytics_for_execution",
]

SOURCE_ACCESS_TOKEN = "access_token"
SOURCE_ANALYTICS_TOKEN = "analytics_token"


def _access() -> str:
    return (settings.UPSTOX_ACCESS_TOKEN or "").strip()


def _analytics() -> str:
    return (settings.UPSTOX_ANALYTICS_TOKEN or "").strip()


# --------------------------------------------------------------------------- #
# READ-ONLY market data
# --------------------------------------------------------------------------- #
def has_market_data_token() -> bool:
    """True when *some* usable read-only market-data credential is present."""
    return bool(_access() or _analytics())


def market_data_token_source() -> str | None:
    """Which env var backs the read-only token, or ``None``.

    Returns ``"access_token"`` / ``"analytics_token"`` / ``None``. Safe to log:
    it never contains the token value.
    """
    if _access():
        return SOURCE_ACCESS_TOKEN
    if _analytics():
        return SOURCE_ANALYTICS_TOKEN
    return None


def resolve_market_data_token() -> str:
    """Return the READ-ONLY market-data token (access first, then analytics).

    Fails closed with ``EngineError(code="upstox_market_data_credential_missing")``
    when neither credential is configured — callers must translate this into a
    safe provider-unavailable (HTTP 503) response and never serve mock data.
    """
    access = _access()
    if access:
        return access
    analytics = _analytics()
    if analytics:
        return analytics
    raise EngineError(
        "No Upstox market-data credential configured "
        "(set UPSTOX_ACCESS_TOKEN or UPSTOX_ANALYTICS_TOKEN)",
        code="upstox_market_data_credential_missing",
    )


def market_data_credentials() -> dict[str, Any]:
    """Credentials dict for building a READ-ONLY Upstox provider/fetcher.

    Both :class:`UpstoxMarketDataProvider` and :class:`UpstoxHistoricalFetcher`
    authenticate with a Bearer ``access_token`` header. The Analytics token is
    a valid read-only Bearer credential, so it is passed through under the same
    key — the provider stays credential-source agnostic and unchanged.
    """
    return {"access_token": resolve_market_data_token()}


# --------------------------------------------------------------------------- #
# ORDER EXECUTION (write) — Analytics token is NEVER allowed
# --------------------------------------------------------------------------- #
def assert_not_analytics_for_execution(token: str | None) -> None:
    """Guard: reject the Analytics token on any order-execution path.

    Raises ``EngineError(code="upstox_analytics_token_forbidden_for_execution")``
    when ``token`` matches the configured read-only Analytics token. This is a
    defence-in-depth check so a read-only credential can never authenticate a
    write (order) operation, even if a future code path wires one in by mistake.
    """
    candidate = (token or "").strip()
    analytics = _analytics()
    if candidate and analytics and candidate == analytics and candidate != _access():
        raise EngineError(
            "The Upstox Analytics token is read-only and must never be used "
            "for order execution",
            code="upstox_analytics_token_forbidden_for_execution",
        )


def resolve_order_execution_token() -> str:
    """Return the token allowed for ORDER EXECUTION — ``UPSTOX_ACCESS_TOKEN`` only.

    Fails closed:
    * ``upstox_analytics_token_forbidden_for_execution`` when *only* the
      Analytics token is configured (it can never place orders).
    * ``upstox_access_token_missing`` when no access token is configured.
    """
    access = _access()
    if access:
        return access
    if _analytics():
        raise EngineError(
            "Only an Upstox Analytics (read-only) token is configured; it "
            "cannot be used for order execution. Set UPSTOX_ACCESS_TOKEN.",
            code="upstox_analytics_token_forbidden_for_execution",
        )
    raise EngineError(
        "No Upstox order-execution credential configured (UPSTOX_ACCESS_TOKEN)",
        code="upstox_access_token_missing",
    )
