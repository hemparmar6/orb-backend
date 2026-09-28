"""Kotak Neo historical candles REST fetcher.

Endpoint
========

Kotak Neo exposes historical bars via::

    GET https://gw-napi.kotaksecurities.com/apim/historical/v1/
        {exchange}/{token}/{interval}?from={epoch}&to={epoch}

- ``{exchange}``: ``nse_cm`` / ``nse_fo`` / ``bse_cm`` etc.
- ``{token}``: numeric scrip token from the instrument master.
- ``{interval}``: ``1``, ``5``, ``15``, ``60`` (minutes) or ``1D``.
- Auth headers: ``Authorization: Bearer <view_token>``, ``sid: <sid>``,
  ``auth: <session_token>``, ``neo-fin-key: neotradeapi``.

Response::

    {
      "data": {
        "candles": [
          [1704067200, 100.0, 102.0, 99.0, 101.0, 1000],
          [1704067260, 101.0, 103.0, 100.0, 102.5, 1500],
          ...
        ]
      }
    }

Each row is ``[epoch_seconds, open, high, low, close, volume]``.

Credentials
===========

Two supported modes, matching :class:`KotakNeoMarketDataProvider`:

- Pre-signed: ``sid`` + ``session_token`` (+ optional ``view_token``) — skips
  OAuth+login. Use in production once you have a fresh daily session.
- Full: ``consumer_key`` + ``consumer_secret`` + ``mobile_number`` + ``mpin``
  — performs the two-step auth on first call.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.engine.market_data.base import Candle, Interval
from app.engine.market_data.historical_base import (
    HistoricalCandleFetcher,
    register_historical,
)

logger = get_logger(__name__)


KOTAK_OAUTH_URL = "https://gw-napi.kotaksecurities.com/oauth2/token"
KOTAK_LOGIN_URL = "https://gw-napi.kotaksecurities.com/login/1.0/login/v6/validate"
KOTAK_HISTORICAL_BASE = "https://gw-napi.kotaksecurities.com/apim/historical/v1"

_KOTAK_INTERVAL: dict[Interval, str] = {
    Interval.ONE_MIN: "1",
    Interval.THREE_MIN: "3",
    Interval.FIVE_MIN: "5",
    Interval.FIFTEEN_MIN: "15",
    Interval.ONE_HOUR: "60",
    Interval.ONE_DAY: "1D",
}


@register_historical("kotak_neo")
class KotakNeoHistoricalFetcher(HistoricalCandleFetcher):
    """Fetch historical candles from Kotak Neo."""

    name = "kotak_neo"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        symbol_map: dict[str, tuple[str, str]] | None = None,
        base_url: str = KOTAK_HISTORICAL_BASE,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_s: float = 15.0,
    ) -> None:
        creds = dict(credentials or {})
        has_signed = creds.get("sid") and creds.get("session_token")
        has_full = all(
            creds.get(k)
            for k in ("consumer_key", "consumer_secret", "mobile_number", "mpin")
        )
        if not has_signed and not has_full:
            raise ValueError(
                "KotakNeoHistoricalFetcher requires either "
                "(sid + session_token) or "
                "(consumer_key + consumer_secret + mobile_number + mpin) credentials"
            )
        self._credentials = creds
        self._symbol_map: dict[str, tuple[str, str]] = dict(symbol_map or {})
        self._base_url = base_url.rstrip("/")
        self._external_http = http_client
        self._owns_http = http_client is None
        self._timeout_s = timeout_s

        self._sid: Optional[str] = creds.get("sid")
        self._session_token: Optional[str] = creds.get("session_token")
        self._view_token: Optional[str] = creds.get("view_token")

    def add_symbols(self, mapping: dict[str, tuple[str, str]]) -> None:
        self._symbol_map.update(mapping)

    # ---------------------------------------------------------- lifecycle

    def _http(self) -> httpx.AsyncClient:
        if self._external_http is None:
            self._external_http = httpx.AsyncClient(timeout=self._timeout_s)
        return self._external_http

    async def close(self) -> None:
        if self._owns_http and self._external_http is not None:
            try:
                await self._external_http.aclose()
            except Exception:  # pragma: no cover
                pass
            self._external_http = None

    # ---------------------------------------------------------------- auth

    async def _authenticate(self) -> None:
        if self._sid and self._session_token:
            return
        basic = base64.b64encode(
            f"{self._credentials['consumer_key']}:"
            f"{self._credentials['consumer_secret']}".encode("utf-8")
        ).decode("ascii")
        oauth = await self._http().post(
            KOTAK_OAUTH_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        if oauth.status_code >= 400:
            raise EngineError(
                "Kotak Neo historical: OAuth failed",
                code="kotak_oauth_failed",
                details={"status": oauth.status_code, "body": _safe_text(oauth)},
            )
        self._view_token = str((oauth.json() or {}).get("access_token") or "")
        if not self._view_token:
            raise EngineError(
                "Kotak Neo historical: OAuth response missing access_token",
                code="kotak_oauth_failed",
            )
        login = await self._http().post(
            KOTAK_LOGIN_URL,
            headers={
                "Authorization": f"Bearer {self._view_token}",
                "Content-Type": "application/json",
            },
            json={
                "mobileNumber": str(self._credentials["mobile_number"]),
                "mpin": str(self._credentials["mpin"]),
            },
        )
        if login.status_code >= 400:
            raise EngineError(
                "Kotak Neo historical: login failed",
                code="kotak_login_failed",
                details={"status": login.status_code, "body": _safe_text(login)},
            )
        data = (login.json() or {}).get("data") or {}
        self._session_token = str(data.get("token") or data.get("sessionToken") or "")
        self._sid = str(data.get("sid") or "")
        if not self._session_token or not self._sid:
            raise EngineError(
                "Kotak Neo historical: login response missing token/sid",
                code="kotak_login_failed",
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._view_token or self._session_token or ''}",
            "sid": self._sid or "",
            "auth": self._session_token or "",
            "neo-fin-key": "neotradeapi",
            "Accept": "application/json",
        }

    # ---------------------------------------------------------------- fetch

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: datetime,
        exchange: str = "",
    ) -> list[Candle]:
        if symbol not in self._symbol_map:
            raise EngineError(
                f"KotakNeoHistoricalFetcher: symbol '{symbol}' is not in symbol_map",
                code="unknown_symbol",
            )
        token, segment = self._symbol_map[symbol]
        exch_out = exchange or segment
        kotak_interval = _KOTAK_INTERVAL.get(interval)
        if kotak_interval is None:
            raise EngineError(
                f"Kotak Neo does not support interval {interval.value}",
                code="unsupported_interval",
            )

        await self._authenticate()

        start_epoch = int(start.replace(tzinfo=start.tzinfo or timezone.utc).timestamp())
        end_epoch = int(end.replace(tzinfo=end.tzinfo or timezone.utc).timestamp())
        url = f"{self._base_url}/{segment}/{token}/{kotak_interval}"
        params = {"from": start_epoch, "to": end_epoch}

        resp = await self._http().get(url, headers=self._headers(), params=params)
        # Auto re-auth on 401 (one retry).
        if resp.status_code == 401:
            self._sid = None
            self._session_token = None
            self._view_token = None
            await self._authenticate()
            resp = await self._http().get(url, headers=self._headers(), params=params)
        if resp.status_code >= 400:
            raise EngineError(
                "Kotak Neo historical candles request failed",
                code="kotak_historical_failed",
                details={"status": resp.status_code, "body": _safe_text(resp)},
            )
        payload = resp.json() or {}
        return _decode_kotak_candles(
            payload=payload,
            symbol=symbol,
            exchange=exch_out,
            interval=interval,
        )


def _decode_kotak_candles(
    *,
    payload: dict[str, Any],
    symbol: str,
    exchange: str,
    interval: Interval,
) -> list[Candle]:
    data = payload.get("data") or payload
    rows = data.get("candles") or payload.get("candles") or []
    out: list[Candle] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            ts_epoch = float(row[0])
            if ts_epoch > 1e12:
                ts_epoch = ts_epoch / 1000.0
            ts = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
            out.append(
                Candle(
                    symbol=symbol,
                    exchange=exchange,
                    interval=interval,
                    ts=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def _safe_text(resp: httpx.Response) -> str:
    try:
        return resp.text[:512]
    except Exception:  # pragma: no cover
        return ""
