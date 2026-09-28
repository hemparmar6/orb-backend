"""Dhan v2 historical candles REST fetcher.

Endpoints
=========

- ``POST https://api.dhan.co/v2/charts/intraday`` — 1-minute up to daily
  intraday candles. Body::

      {
        "securityId": "11536",
        "exchangeSegment": "NSE_EQ",
        "instrument": "EQUITY",
        "interval": "1",             // in minutes: 1, 5, 15, 25, 60
        "fromDate": "2024-01-01",
        "toDate":   "2024-01-10"
      }

- ``POST https://api.dhan.co/v2/charts/historical`` — daily+ candles.

Response (both endpoints)::

    {
      "open":      [100.0, 101.0, ...],
      "high":      [102.0, 103.0, ...],
      "low":       [99.0, 100.0, ...],
      "close":     [101.0, 102.5, ...],
      "volume":    [1000, 1500, ...],
      "timestamp": [1704067200, 1704067260, ...]   // epoch seconds
    }

Authentication
==============

Headers ``access-token`` and ``client-id`` — same credential shape as
``DhanBrokerAdapter`` and ``DhanMarketDataProvider``.
"""
from __future__ import annotations

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


DHAN_HISTORICAL_URL = "https://api.dhan.co/v2/charts/historical"
DHAN_INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"

# Dhan expects intraday intervals in minutes as strings.
_DHAN_INTRADAY_INTERVAL: dict[Interval, str] = {
    Interval.ONE_MIN: "1",
    Interval.FIVE_MIN: "5",
    Interval.FIFTEEN_MIN: "15",
    Interval.ONE_HOUR: "60",
}
_DHAN_SUPPORTED_INTERVALS = set(_DHAN_INTRADAY_INTERVAL) | {Interval.ONE_DAY}


@register_historical("dhan")
class DhanHistoricalFetcher(HistoricalCandleFetcher):
    """Fetch historical candles from Dhan v2."""

    name = "dhan"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        symbol_map: dict[str, tuple[str, str]] | None = None,
        instrument: str = "EQUITY",
        instrument_map: dict[str, str] | None = None,
        intraday_url: str = DHAN_INTRADAY_URL,
        historical_url: str = DHAN_HISTORICAL_URL,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_s: float = 15.0,
    ) -> None:
        if not credentials.get("client_id") or not credentials.get("access_token"):
            raise ValueError(
                "DhanHistoricalFetcher requires client_id + access_token credentials"
            )
        self._credentials = dict(credentials)
        self._symbol_map: dict[str, tuple[str, str]] = dict(symbol_map or {})
        self._instrument = instrument
        self._instrument_map: dict[str, str] = {
            str(symbol): str(value).upper()
            for symbol, value in (instrument_map or {}).items()
            if value
        }
        self._intraday_url = intraday_url
        self._historical_url = historical_url
        self._external_http = http_client
        self._owns_http = http_client is None
        self._timeout_s = timeout_s

    def add_symbols(self, mapping: dict[str, tuple[str, str]]) -> None:
        self._symbol_map.update(mapping)

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
                f"DhanHistoricalFetcher: symbol '{symbol}' is not in symbol_map",
                code="unknown_symbol",
            )
        security_id, segment = self._symbol_map[symbol]
        exch_out = exchange or segment

        if interval not in _DHAN_SUPPORTED_INTERVALS:
            raise EngineError(
                f"DhanHistoricalFetcher: unsupported interval '{interval.value}'",
                code="unsupported_dhan_interval",
            )

        is_intraday = interval in _DHAN_INTRADAY_INTERVAL
        if is_intraday:
            url = self._intraday_url
            dhan_interval = _DHAN_INTRADAY_INTERVAL[interval]
        else:
            url = self._historical_url
            dhan_interval = "1D"  # daily fallback for Dhan historical

        body = {
            "securityId": str(security_id),
            "exchangeSegment": str(segment),
            "instrument": self._instrument_map.get(symbol, self._instrument),
            "interval": dhan_interval,
            "fromDate": start.date().isoformat(),
            "toDate": end.date().isoformat(),
        }
        headers = {
            "access-token": self._credentials["access_token"],
            "client-id": self._credentials["client_id"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        resp = await self._http().post(url, headers=headers, json=body)
        if resp.status_code >= 400:
            raise EngineError(
                "Dhan historical candles request failed",
                code="dhan_historical_failed",
                details={"status": resp.status_code, "body": _safe_text(resp)},
            )
        payload = resp.json() or {}
        return _decode_dhan_candles(
            payload=payload,
            symbol=symbol,
            exchange=exch_out,
            interval=interval,
        )


def _decode_dhan_candles(
    *,
    payload: dict[str, Any],
    symbol: str,
    exchange: str,
    interval: Interval,
) -> list[Candle]:
    opens = payload.get("open") or []
    highs = payload.get("high") or []
    lows = payload.get("low") or []
    closes = payload.get("close") or []
    vols = payload.get("volume") or []
    times = payload.get("timestamp") or []
    n = min(len(opens), len(highs), len(lows), len(closes), len(vols), len(times))
    if n == 0:
        return []
    out: list[Candle] = []
    for i in range(n):
        try:
            ts_epoch = float(times[i])
            if ts_epoch > 1e12:
                ts_epoch = ts_epoch / 1000.0
            ts = datetime.fromtimestamp(ts_epoch, tz=timezone.utc)
            out.append(
                Candle(
                    symbol=symbol,
                    exchange=exchange,
                    interval=interval,
                    ts=ts,
                    open=float(opens[i]),
                    high=float(highs[i]),
                    low=float(lows[i]),
                    close=float(closes[i]),
                    volume=float(vols[i]),
                )
            )
        except (TypeError, ValueError):
            # Skip any malformed row rather than kill the whole batch.
            continue
    return out


def _safe_text(resp: httpx.Response) -> str:
    try:
        return resp.text[:512]
    except Exception:  # pragma: no cover
        return ""
