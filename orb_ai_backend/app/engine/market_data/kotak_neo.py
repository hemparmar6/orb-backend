"""Kotak Neo market-data WebSocket provider.

Wire protocol
=============

Endpoint: ``wss://mlhsm.kotaksecurities.com/realtime?sId=<sid>`` (quote channel).

Subscribe frame (JSON, ``a`` = "mws" for subscribe, ``a`` = "muws" for
unsubscribe)::

    {
      "a": "mws",
      "v": [{"nse_cm|11536": "1"}],  // "1" = LTP+quote, "2" = full depth
      "m": "compact_marketdata"
    }

Tick frames (JSON)::

    {"e":"quote","tk":"11536","seg":"nse_cm","ltp":"1523.45","v":"100",
     "ltt":"1706531234"}

Two-mode credentials
====================

The provider accepts the same credential shape as
``KotakNeoBrokerAdapter``:

- Fully unsigned start: ``consumer_key`` + ``consumer_secret`` +
  ``mobile_number`` + ``mpin`` (the provider performs OAuth + login before the
  first connect).
- Pre-signed start: ``sid`` + ``session_token`` — the provider uses those
  directly and skips the two-step login.

Symbol mapping
==============

Callers pass ``symbol_map[user_symbol] = (token, segment)``::

    KotakNeoMarketDataProvider(
        credentials={"sid": "...", "session_token": "..."},
        symbol_map={"TCS": ("11536", "nse_cm")},
    )
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.engine.market_data.base import Quote
from app.engine.market_data.broker_ws import BrokerWSMarketDataProvider
from app.engine.market_data.registry import register_provider

logger = get_logger(__name__)


KOTAK_OAUTH_URL = "https://gw-napi.kotaksecurities.com/oauth2/token"
KOTAK_LOGIN_URL = "https://gw-napi.kotaksecurities.com/login/1.0/login/v6/validate"
KOTAK_WS_URL = "wss://mlhsm.kotaksecurities.com/realtime"


@register_provider("kotak_neo")
class KotakNeoMarketDataProvider(BrokerWSMarketDataProvider):
    """Kotak Neo real-time market data over WebSocket."""

    name = "kotak_neo"
    default_exchange = "nse_cm"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        symbol_map: dict[str, tuple[str, str]] | None = None,
        ws_url: str = KOTAK_WS_URL,
        subscribe_mode: str = "1",  # "1" = LTP+quote, "2" = full depth
        http_client: Optional[httpx.AsyncClient] = None,
        **kwargs: Any,
    ) -> None:
        # Either pre-signed (sid+session_token) or full credentials required.
        creds = dict(credentials or {})
        has_signed = creds.get("sid") and creds.get("session_token")
        has_full = all(
            creds.get(k)
            for k in ("consumer_key", "consumer_secret", "mobile_number", "mpin")
        )
        if not has_signed and not has_full:
            raise ValueError(
                "KotakNeoMarketDataProvider requires either "
                "(sid + session_token) or "
                "(consumer_key + consumer_secret + mobile_number + mpin) credentials"
            )
        super().__init__(credentials=creds, symbol_map=symbol_map, **kwargs)
        self._ws_url = ws_url
        self._subscribe_mode = str(subscribe_mode)
        self._sid: Optional[str] = creds.get("sid")
        self._session_token: Optional[str] = creds.get("session_token")
        self._view_token: Optional[str] = None
        self._external_http = http_client
        self._owns_http = http_client is None

    # ------------------------------------------------------------ lifecycle

    async def stop(self) -> None:
        await super().stop()
        if self._owns_http and self._external_http is not None:
            try:
                await self._external_http.aclose()
            except Exception:  # pragma: no cover
                pass
            self._external_http = None

    def _http(self) -> httpx.AsyncClient:
        if self._external_http is None:
            self._external_http = httpx.AsyncClient(timeout=15.0)
        return self._external_http

    # ------------------------------------------------------------- hooks

    async def _authenticate(self) -> None:
        if self._sid and self._session_token:
            return  # pre-signed — nothing to do
        client = self._http()

        # Step 1: exchange consumer_key + consumer_secret for view_token.
        basic = base64.b64encode(
            f"{self._credentials['consumer_key']}:"
            f"{self._credentials['consumer_secret']}".encode("utf-8")
        ).decode("ascii")
        oauth_resp = await client.post(
            KOTAK_OAUTH_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        if oauth_resp.status_code >= 400:
            raise EngineError(
                "Kotak Neo market-data: OAuth failed",
                code="kotak_oauth_failed",
                details={"status": oauth_resp.status_code, "body": _safe_text(oauth_resp)},
            )
        view_token = (oauth_resp.json() or {}).get("access_token") or ""
        if not view_token:
            raise EngineError(
                "Kotak Neo market-data: OAuth response missing access_token",
                code="kotak_oauth_failed",
            )
        self._view_token = str(view_token)

        # Step 2: mobile_number + mpin → sid + session_token.
        login_resp = await client.post(
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
        if login_resp.status_code >= 400:
            raise EngineError(
                "Kotak Neo market-data: login failed",
                code="kotak_login_failed",
                details={"status": login_resp.status_code, "body": _safe_text(login_resp)},
            )
        data = (login_resp.json() or {}).get("data") or {}
        self._session_token = str(data.get("token") or data.get("sessionToken") or "")
        self._sid = str(data.get("sid") or "")
        if not self._session_token or not self._sid:
            raise EngineError(
                "Kotak Neo market-data: login response missing token/sid",
                code="kotak_login_failed",
            )

    async def _resolve_url(self) -> str:
        if not self._sid:
            raise EngineError(
                "Kotak Neo market-data: sid is not set — authenticate first",
                code="kotak_not_authenticated",
            )
        return f"{self._ws_url}?sId={self._sid}"

    def _extra_headers(self) -> dict[str, str]:
        # Kotak's WS also inspects Authorization for the initial handshake.
        headers: dict[str, str] = {}
        if self._session_token:
            headers["Authorization"] = f"Bearer {self._session_token}"
        if self._sid:
            headers["Sid"] = self._sid
        return headers

    def _build_subscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        # items are (token, segment) tuples — Kotak's payload uses "seg|token".
        return {
            "a": "mws",
            "v": [{f"{seg}|{token}": self._subscribe_mode} for token, seg in items],
            "m": "compact_marketdata",
        }

    def _build_unsubscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        return {
            "a": "muws",
            "v": [{f"{seg}|{token}": self._subscribe_mode} for token, seg in items],
            "m": "compact_marketdata",
        }

    def _decode_frame(self, raw: Any) -> list[Quote]:
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
        # Kotak sometimes wraps frames in a list; unwrap.
        if isinstance(raw, list):
            out: list[Quote] = []
            for frame in raw:
                out.extend(self._decode_frame(frame))
            return out
        if not isinstance(raw, dict):
            return []
        # Filter out subscribe-acks and heartbeats.
        if "ltp" not in raw and "LTP" not in raw:
            return []
        token = str(raw.get("tk") or raw.get("token") or "")
        segment = str(raw.get("seg") or raw.get("segment") or self.default_exchange)
        key = f"{token}|{segment}"
        mapped = self._reverse_map.get(key) or self._reverse_map.get(token)
        if not mapped:
            return []
        user_symbol, exchange = mapped

        try:
            price = float(raw.get("ltp", raw.get("LTP")))
        except (TypeError, ValueError):
            return []
        try:
            volume = float(raw.get("v") or raw.get("vol") or 0)
        except (TypeError, ValueError):
            volume = 0.0

        ts_val = raw.get("ltt") or raw.get("LTT")
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
            )
        ]


def _parse_timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    ts = float(value)
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _safe_text(resp: httpx.Response) -> str:
    try:
        return resp.text[:512]
    except Exception:  # pragma: no cover
        return ""
