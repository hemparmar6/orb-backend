"""Upstox V3 historical & intraday candle REST fetcher.

Endpoints (V3)
==============

- Historical::

      GET https://api.upstox.com/v3/historical-candle/
          {instrument_key}/{unit}/{interval}/{to_date}/{from_date}

- Intraday (current trading day)::

      GET https://api.upstox.com/v3/historical-candle/intraday/
          {instrument_key}/{unit}/{interval}

``unit`` ∈ ``minutes|hours|days|weeks|months``; ``interval`` is the numeric
multiple (e.g. ``minutes/1``, ``minutes/15``, ``hours/1``, ``days/1``).

Response (both)::

    {"status":"success","data":{"candles":[
        ["2025-01-02T09:15:00+05:30", 100.0, 102.0, 99.0, 101.0, 12345, 0],
        ...
    ]}}

Each candle is ``[timestamp, open, high, low, close, volume, open_interest]``
and Upstox returns them **newest-first**. This fetcher merges the historical
range with the intraday day (when the window reaches today), normalises every
candle to a UTC-aware :class:`Candle`, **de-duplicates by timestamp** and returns
them **oldest-first** — matching the ORB candle contract.

Authentication is ``Authorization: Bearer <access_token>`` (never logged).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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

UPSTOX_HISTORICAL_BASE = "https://api.upstox.com/v3/historical-candle"

# ORB Interval -> (Upstox unit, interval). Used by the registry-based
# `get_candles()` (trading engine / backtests), which speak the ORB Interval enum.
_UPSTOX_UNIT_INTERVAL: dict[Interval, tuple[str, str]] = {
    Interval.ONE_MIN: ("minutes", "1"),
    Interval.THREE_MIN: ("minutes", "3"),
    Interval.FIVE_MIN: ("minutes", "5"),
    Interval.FIFTEEN_MIN: ("minutes", "15"),
    Interval.ONE_HOUR: ("hours", "1"),
    Interval.ONE_DAY: ("days", "1"),
}

# ORB chart timeframe string -> (Upstox unit, interval). Every timeframe the
# ORB mobile Chart screen offers is NATIVELY supported by Upstox V3
# (minutes 1-300, hours 1-5, days/weeks/months = 1) — so each maps to a real
# Upstox endpoint/interval. No aggregation and no mock substitution is required.
_UPSTOX_TF_UNIT_INTERVAL: dict[str, tuple[str, str]] = {
    "1m": ("minutes", "1"),
    "2m": ("minutes", "2"),
    "3m": ("minutes", "3"),
    "5m": ("minutes", "5"),
    "10m": ("minutes", "10"),
    "15m": ("minutes", "15"),
    "30m": ("minutes", "30"),
    "45m": ("minutes", "45"),
    "1H": ("hours", "1"),
    "2H": ("hours", "2"),
    "4H": ("hours", "4"),
    "1D": ("days", "1"),
    "1W": ("weeks", "1"),
    "1M": ("months", "1"),
}
# Intraday endpoint only serves sub-daily units.
_INTRADAY_UNITS = frozenset({"minutes", "hours"})


@register_historical("upstox")
class UpstoxHistoricalFetcher(HistoricalCandleFetcher):
    """Fetch historical + intraday candles from Upstox V3."""

    name = "upstox"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        symbol_map: dict[str, str] | None = None,
        base_url: str = UPSTOX_HISTORICAL_BASE,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_s: float = 15.0,
        include_intraday: bool = True,
    ) -> None:
        token = (credentials or {}).get("access_token")
        if not token or not str(token).strip():
            raise ValueError(
                "UpstoxHistoricalFetcher requires an 'access_token' credential"
            )
        self._credentials = dict(credentials)
        # symbol -> instrument_key (single opaque Upstox identifier)
        self._symbol_map: dict[str, str] = {
            str(k): str(v) for k, v in (symbol_map or {}).items()
        }
        self._base_url = base_url.rstrip("/")
        self._external_http = http_client
        self._owns_http = http_client is None
        self._timeout_s = timeout_s
        self._include_intraday = include_intraday

    def add_symbols(self, mapping: dict[str, str]) -> None:
        self._symbol_map.update({str(k): str(v) for k, v in mapping.items()})

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

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._credentials['access_token']}",
            "Accept": "application/json",
        }

    async def get_candles(
        self,
        symbol: str,
        interval: Interval,
        start: datetime,
        end: datetime,
        exchange: str = "",
    ) -> list[Candle]:
        instrument_key = self._symbol_map.get(symbol)
        if not instrument_key:
            raise EngineError(
                f"UpstoxHistoricalFetcher: symbol '{symbol}' is not in symbol_map",
                code="unknown_symbol",
            )
        if interval not in _UPSTOX_UNIT_INTERVAL:
            raise EngineError(
                f"UpstoxHistoricalFetcher: unsupported interval '{interval.value}'",
                code="unsupported_upstox_interval",
            )
        unit, mult = _UPSTOX_UNIT_INTERVAL[interval]
        exch_out = exchange or "NSE"
        rows = await self._collect(instrument_key, unit, mult, start, end)
        return [
            Candle(
                symbol=symbol, exchange=exch_out, interval=interval,
                ts=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                open=o, high=h, low=l, close=c, volume=v,
            )
            for ts_ms, (o, h, l, c, v) in sorted(rows.items())
        ]

    async def fetch_ohlcv(
        self,
        instrument_key: str,
        unit: str,
        interval_mult: str,
        start: datetime,
        end: datetime,
    ) -> list[list[float]]:
        """Return native Upstox candles as ``[ts_ms, o, h, l, c, v]`` rows.

        Decoupled from the ORB ``Interval`` enum so ANY natively-supported
        Upstox ``(unit, interval)`` (e.g. ``minutes/2``, ``minutes/30``,
        ``hours/4``, ``weeks/1``, ``months/1``) can be served without
        aggregation. Rows are UTC-normalised, oldest-first and de-duplicated.
        """
        rows = await self._collect(instrument_key, unit, interval_mult, start, end)
        return [
            [ts_ms, o, h, l, c, v]
            for ts_ms, (o, h, l, c, v) in sorted(rows.items())
        ]

    async def _collect(
        self, instrument_key: str, unit: str, mult: str,
        start: datetime, end: datetime,
    ) -> dict[int, tuple[float, float, float, float, float]]:
        """Fetch historical (+ intraday for sub-daily units) and de-dupe by ts."""
        by_ts: dict[int, tuple[float, float, float, float, float]] = {}

        # 1) Historical range (excludes the incomplete current day).
        for row in await self._fetch_historical(instrument_key, unit, mult, start, end):
            parsed = _parse_row(row)
            if parsed is not None:
                by_ts[parsed[0]] = parsed[1]

        # 2) Intraday (current day) — only for sub-daily units when the window
        #    reaches today; intraday values win on a timestamp collision.
        today = datetime.now(timezone.utc).date()
        if self._include_intraday and unit in _INTRADAY_UNITS and end.date() >= today:
            for row in await self._fetch_intraday(instrument_key, unit, mult):
                parsed = _parse_row(row)
                if parsed is not None:
                    by_ts[parsed[0]] = parsed[1]
        return by_ts

    # ---------------------------------------------------------------- REST

    async def _fetch_historical(
        self, instrument_key: str, unit: str, mult: str,
        start: datetime, end: datetime,
    ) -> list[list[Any]]:
        to_date = end.date().isoformat()
        from_date = start.date().isoformat()
        # instrument_key is placed as a path segment; httpx URL-encodes the '|'.
        url = f"{self._base_url}/{instrument_key}/{unit}/{mult}/{to_date}/{from_date}"
        return await self._get_candles_rows(url)

    async def _fetch_intraday(
        self, instrument_key: str, unit: str, mult: str,
    ) -> list[list[Any]]:
        url = f"{self._base_url}/intraday/{instrument_key}/{unit}/{mult}"
        return await self._get_candles_rows(url)

    async def _get_candles_rows(self, url: str) -> list[list[Any]]:
        resp = await self._http().get(url, headers=self._headers())
        if resp.status_code in (401, 403):
            raise EngineError(
                "Upstox historical rejected the access token",
                code="upstox_authentication_failed",
                details={"status": resp.status_code},
            )
        if resp.status_code >= 400:
            raise EngineError(
                "Upstox historical candles request failed",
                code="upstox_historical_failed",
                details={"status": resp.status_code, "body": _safe_text(resp)},
            )
        payload = resp.json() or {}
        data = payload.get("data") or {}
        rows = data.get("candles") or []
        return rows if isinstance(rows, list) else []


def _parse_row(
    row: list[Any],
) -> Optional[tuple[int, tuple[float, float, float, float, float]]]:
    """Parse one Upstox candle row -> (ts_ms, (open, high, low, close, volume))."""
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return None
    try:
        ts = _parse_ts(row[0])
        ts_ms = int(ts.timestamp() * 1000)
        return ts_ms, (
            float(row[1]), float(row[2]), float(row[3]),
            float(row[4]), float(row[5]),
        )
    except (TypeError, ValueError):
        return None


def _parse_ts(value: Any) -> datetime:
    """Upstox timestamps are ISO-8601 with an offset; normalise to UTC."""
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_text(resp: httpx.Response) -> str:
    try:
        return resp.text[:512]
    except Exception:  # pragma: no cover
        return ""
