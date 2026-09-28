"""Dhan v2 market-data WebSocket provider.

Wire protocol
=============

Endpoint: ``wss://api-feed.dhan.co`` (query params carry auth):

    wss://api-feed.dhan.co?version=2&token=<access_token>&clientId=<client_id>&authType=2

Subscribe frame (RequestCode 15 = TickerPacket, 21 = QuotePacket)::

    {
      "RequestCode": 15,
      "InstrumentCount": 2,
      "InstrumentList": [
        {"ExchangeSegment": "NSE_EQ", "SecurityId": "11536"},
        {"ExchangeSegment": "NSE_FNO", "SecurityId": "58330"}
      ]
    }

Unsubscribe frame::  same shape with ``RequestCode: 16``

Tick frames (JSON mode)::

    {"type":"ticker","exchangeSegment":"NSE_EQ","securityId":"11536",
     "LTP":1523.45,"LTQ":10,"LTT":1706531234}

Configuration
=============

Callers register instruments with a ``symbol_map`` — user-facing symbol →
``(security_id, exchange_segment)``::

    provider = DhanMarketDataProvider(
        credentials={"client_id": "1234567", "access_token": "..."},
        symbol_map={
            "TCS":   ("11536", "NSE_EQ"),
            "NIFTY": ("58330", "NSE_FNO"),
        },
    )
    await provider.start()
    await provider.subscribe(["TCS"])
    async for q in provider.stream():
        ...

Broker-native items in the frame layer are ``(security_id, exchange_segment)``
tuples. The provider translates them back to the user-facing symbol using
Dhan's ``securityId``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.engine.market_data.base import Quote
from app.engine.market_data.broker_ws import BrokerWSMarketDataProvider
from app.engine.market_data.registry import register_provider


DHAN_WS_URL = "wss://api-feed.dhan.co"


@register_provider("dhan")
class DhanMarketDataProvider(BrokerWSMarketDataProvider):
    """Dhan v2 real-time market data over WebSocket."""

    name = "dhan"
    default_exchange = "NSE_EQ"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        symbol_map: dict[str, tuple[str, str]] | None = None,
        ws_url: str = DHAN_WS_URL,
        request_code: int = 15,  # 15 = ticker; 21 = quote packet
        **kwargs: Any,
    ) -> None:
        if not credentials.get("client_id") or not credentials.get("access_token"):
            raise ValueError(
                "DhanMarketDataProvider requires client_id + access_token credentials"
            )
        super().__init__(credentials=credentials, symbol_map=symbol_map, **kwargs)
        self._ws_url = ws_url
        self._request_code = int(request_code)

    # ---------------------------------------------------------------- hooks

    async def _resolve_url(self) -> str:
        return (
            f"{self._ws_url}"
            f"?version=2"
            f"&token={self._credentials['access_token']}"
            f"&clientId={self._credentials['client_id']}"
            f"&authType=2"
        )

    def _build_subscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        return {
            "RequestCode": self._request_code,
            "InstrumentCount": len(items),
            "InstrumentList": [
                {"ExchangeSegment": str(seg), "SecurityId": str(sid)}
                for sid, seg in items
            ],
        }

    def _build_unsubscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        return {
            "RequestCode": 16,  # UnsubscribeInstrument
            "InstrumentCount": len(items),
            "InstrumentList": [
                {"ExchangeSegment": str(seg), "SecurityId": str(sid)}
                for sid, seg in items
            ],
        }

    def _decode_frame(self, raw: Any) -> list[Quote]:
        # Dhan sends binary in production; the JSON mode is used for tests +
        # dev. We accept both str, bytes, and pre-parsed dicts.
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except Exception:
                return []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return []
        if not isinstance(raw, dict):
            return []
        # Control frames (server disconnect, error notices) have no LTP.
        if "LTP" not in raw and "ltp" not in raw:
            return []

        security_id = str(raw.get("securityId") or raw.get("SecurityId") or "")
        segment = str(
            raw.get("exchangeSegment")
            or raw.get("ExchangeSegment")
            or self.default_exchange
        )
        key = f"{security_id}|{segment}"
        mapped = self._reverse_map.get(key)
        if not mapped:
            # Fallback: some tick payloads only carry security_id — try that.
            mapped = self._reverse_map.get(security_id)
        if not mapped:
            return []
        user_symbol, exchange = mapped

        try:
            price = float(raw.get("LTP", raw.get("ltp", 0)))
        except (TypeError, ValueError):
            return []
        try:
            volume = float(raw.get("LTQ", raw.get("ltq", raw.get("volume", 0)) or 0))
        except (TypeError, ValueError):
            volume = 0.0

        # LTT: seconds since epoch (Dhan doc); some payloads use ms.
        ts_val = raw.get("LTT") or raw.get("ltt")
        try:
            ts = _parse_timestamp(ts_val)
        except Exception:
            ts = datetime.now(timezone.utc)

        return [
            Quote(
                symbol=user_symbol,
                exchange=exchange,
                price=price,
                volume=volume,
                ts=ts,
                instrument_token=security_id,
                sequence=_optional_sequence(raw),
                bid=_optional_float(raw, "bid", "bidPrice", "bidP"),
                ask=_optional_float(raw, "ask", "askPrice", "askP"),
            )
        ]


def _parse_timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    ts = float(value)
    # Values > 1e12 are milliseconds since epoch.
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _optional_sequence(raw: dict[str, Any]) -> int | str | None:
    for key in ("sequence", "sequenceNumber", "seq"):
        if raw.get(key) is not None:
            return raw[key]
    return None


def _optional_float(raw: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if raw.get(key) is not None:
            try:
                return float(raw[key])
            except (TypeError, ValueError):
                return None
    return None
