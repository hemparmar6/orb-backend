"""Kotak Neo daily ``sid`` refresh scheduler.

Kotak's ``session_token`` + ``sid`` pair is issued for one trading day and
must be regenerated every morning before market open (typically 08:30 IST
so quotes + orders work from the 09:15 opening bell).

Both :class:`app.engine.market_data.KotakNeoMarketDataProvider` and
:class:`app.engine.market_data.KotakNeoHistoricalFetcher` re-auth
on-demand when they receive a 401. That's sufficient for correctness but
means the *first* request after market open pays the auth round-trip.
This scheduler pre-emptively refreshes the credentials so the first
request is warm.

Usage
-----

.. code-block:: python

    scheduler = KotakSidRefreshScheduler(
        credentials={
            "consumer_key": ..., "consumer_secret": ...,
            "mobile_number": ..., "mpin": ...,
        },
        refresh_hour_local=8,        # 08:30 IST → refresh at 08:00 IST
        refresh_minute_local=0,
        timezone="Asia/Kolkata",
        on_refresh=lambda creds: ...,  # invoked with the new {sid, session_token, view_token}
    )
    await scheduler.start()

Every listener passed via ``on_refresh`` receives the fresh credentials
dict. Providers can be re-created with these new credentials, or callers
can push them directly into an existing provider's ``_credentials`` dict.
"""
from __future__ import annotations

import asyncio
import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import httpx

from app.core.exceptions import EngineError
from app.core.logging import get_logger

logger = get_logger(__name__)

KOTAK_OAUTH_URL = "https://gw-napi.kotaksecurities.com/oauth2/token"
KOTAK_LOGIN_URL = "https://gw-napi.kotaksecurities.com/login/1.0/login/v6/validate"


RefreshCallback = Callable[[dict[str, str]], Any]


class KotakSidRefreshScheduler:
    """Refresh Kotak ``sid`` daily before market open."""

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        refresh_hour_local: int = 8,
        refresh_minute_local: int = 0,
        timezone: str = "Asia/Kolkata",
        on_refresh: Optional[RefreshCallback] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        max_consecutive_failures: Optional[int] = 6,
        oauth_url: str = KOTAK_OAUTH_URL,
        login_url: str = KOTAK_LOGIN_URL,
    ) -> None:
        if not all(
            credentials.get(k)
            for k in ("consumer_key", "consumer_secret", "mobile_number", "mpin")
        ):
            raise ValueError(
                "KotakSidRefreshScheduler requires full credentials: "
                "consumer_key, consumer_secret, mobile_number, mpin"
            )
        if not (0 <= refresh_hour_local <= 23):
            raise ValueError("refresh_hour_local must be in [0, 23]")
        if not (0 <= refresh_minute_local <= 59):
            raise ValueError("refresh_minute_local must be in [0, 59]")

        self._credentials = dict(credentials)
        self._hour = int(refresh_hour_local)
        self._minute = int(refresh_minute_local)
        self._tz = ZoneInfo(timezone)
        self._on_refresh = on_refresh
        self._external_http = http_client
        self._owns_http = http_client is None
        self._max_failures = max_consecutive_failures
        self._oauth_url = oauth_url
        self._login_url = login_url

        self._task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()
        self._current: dict[str, str] = {}
        self._consecutive_failures = 0
        self._refresh_count = 0
        self._last_refresh_at: Optional[float] = None
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------- lifecycle

    def _http(self) -> httpx.AsyncClient:
        if self._external_http is None:
            self._external_http = httpx.AsyncClient(timeout=15.0)
        return self._external_http

    async def start(self, *, refresh_immediately: bool = True) -> None:
        """Kick off the scheduler.

        If ``refresh_immediately`` is True (the default), an initial refresh
        runs synchronously so callers receive a valid ``sid`` before the
        first market-data request.
        """
        if refresh_immediately:
            await self.refresh_now()
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(), name="kotak-sid-refresh"
            )

    async def stop(self) -> None:
        self._closed.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._owns_http and self._external_http is not None:
            try:
                await self._external_http.aclose()
            except Exception:  # pragma: no cover
                pass
            self._external_http = None

    # ----------------------------------------------------------- inspection

    def current(self) -> dict[str, str]:
        """Return the most recently obtained credentials (copy)."""
        return dict(self._current)

    def get_stats(self) -> dict[str, Any]:
        return {
            "is_running": self._task is not None and not self._task.done(),
            "refresh_count": self._refresh_count,
            "consecutive_failures": self._consecutive_failures,
            "last_refresh_at": self._last_refresh_at,
            "last_error": self._last_error,
            "next_refresh_at": self._next_refresh_utc().isoformat(),
            "has_sid": bool(self._current.get("sid")),
        }

    # --------------------------------------------------------------- refresh

    async def refresh_now(self) -> dict[str, str]:
        """Force an immediate refresh (bypasses the schedule)."""
        try:
            new = await self._perform_refresh()
        except Exception as exc:
            self._consecutive_failures += 1
            self._last_error = str(exc)
            logger.exception(
                "kotak_sid_refresh_failed",
                extra={"failures": self._consecutive_failures},
            )
            raise
        self._consecutive_failures = 0
        self._last_error = None
        self._refresh_count += 1
        self._last_refresh_at = time.time()
        self._current = new
        if self._on_refresh is not None:
            try:
                result = self._on_refresh(dict(new))
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # pragma: no cover
                logger.exception("kotak_sid_on_refresh_failed")
        return new

    # -------------------------------------------------------- schedule loop

    def _next_refresh_utc(self, now: Optional[datetime] = None) -> datetime:
        """Compute the next scheduled refresh time as a UTC datetime."""
        now = now or datetime.now(tz=self._tz)
        if now.tzinfo is None:
            now = now.replace(tzinfo=self._tz)
        else:
            now = now.astimezone(self._tz)
        target_local = now.replace(
            hour=self._hour, minute=self._minute, second=0, microsecond=0
        )
        if target_local <= now:
            target_local = target_local + timedelta(days=1)
        return target_local.astimezone(timezone.utc)

    async def _loop(self) -> None:
        try:
            while not self._closed.is_set():
                next_utc = self._next_refresh_utc()
                sleep_s = max(
                    1.0,
                    (next_utc - datetime.now(tz=timezone.utc)).total_seconds(),
                )
                try:
                    await asyncio.wait_for(self._closed.wait(), timeout=sleep_s)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    await self.refresh_now()
                except Exception:
                    if (
                        self._max_failures is not None
                        and self._consecutive_failures >= self._max_failures
                    ):
                        logger.error(
                            "kotak_sid_refresh_giving_up",
                            extra={"failures": self._consecutive_failures},
                        )
                        return
        except asyncio.CancelledError:
            return

    # ---------------------------------------------------------- HTTP flow

    async def _perform_refresh(self) -> dict[str, str]:
        basic = base64.b64encode(
            f"{self._credentials['consumer_key']}:"
            f"{self._credentials['consumer_secret']}".encode("utf-8")
        ).decode("ascii")
        oauth = await self._http().post(
            self._oauth_url,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        if oauth.status_code >= 400:
            raise EngineError(
                "Kotak sid refresh: OAuth failed",
                code="kotak_oauth_failed",
                details={"status": oauth.status_code},
            )
        view_token = str((oauth.json() or {}).get("access_token") or "")
        if not view_token:
            raise EngineError(
                "Kotak sid refresh: OAuth response missing access_token",
                code="kotak_oauth_failed",
            )
        login = await self._http().post(
            self._login_url,
            headers={
                "Authorization": f"Bearer {view_token}",
                "Content-Type": "application/json",
            },
            json={
                "mobileNumber": str(self._credentials["mobile_number"]),
                "mpin": str(self._credentials["mpin"]),
            },
        )
        if login.status_code >= 400:
            raise EngineError(
                "Kotak sid refresh: login failed",
                code="kotak_login_failed",
                details={"status": login.status_code},
            )
        data = (login.json() or {}).get("data") or {}
        session_token = str(data.get("token") or data.get("sessionToken") or "")
        sid = str(data.get("sid") or "")
        if not session_token or not sid:
            raise EngineError(
                "Kotak sid refresh: login response missing token/sid",
                code="kotak_login_failed",
            )
        return {
            "sid": sid,
            "session_token": session_token,
            "view_token": view_token,
        }
