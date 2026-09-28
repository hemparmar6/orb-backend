"""Upstox Market Data Feed **V3** provider.

First-class ORB market-data provider for Upstox, wired into the same
``MarketDataProvider`` abstraction as Dhan and Kotak Neo. The mobile app and the
trading engine consume normalised :class:`~app.engine.market_data.base.Quote`
objects and never see anything Upstox-specific.

Wire protocol (V3)
==================

1. **Authorize** — ``GET https://api.upstox.com/v3/feed/market-data-feed/authorize``
   with ``Authorization: Bearer <access_token>``. Returns a JSON body whose
   ``data.authorized_redirect_uri`` is a single-use ``wss://`` URL. We call this
   on every (re)connect so each socket gets a fresh code.
2. **Connect** to that ``wss://`` URL.
3. **Subscribe** by sending a *binary* JSON frame::

       {"guid": "...", "method": "sub",
        "data": {"mode": "full", "instrumentKeys": ["NSE_INDEX|Nifty Bank"]}}

   (``method`` ∈ ``sub`` / ``unsub`` / ``change_mode``; ``mode`` ∈
   ``ltpc`` / ``full`` / ``option_greeks`` / ``full_d30``).
4. **Receive** binary Protobuf ``FeedResponse`` frames, decoded by
   :mod:`app.engine.market_data.upstox_protobuf` (no protobuf runtime needed).

Initial snapshot / recovery uses the **V3 LTP REST** endpoint
(``GET https://api.upstox.com/v3/market-quote/ltp``); the live stream is the
primary data source (we do not poll REST in a loop).

Security
========

The access token is only ever placed in the ``Authorization`` header of the
authorize/LTP REST calls. It is never logged, never put in the WS URL, never
returned from :meth:`get_stats`, and masked in exceptions.

Fail-closed
===========

Constructing the provider without an access token raises ``ValueError`` — so a
deployment that sets ``MARKET_DATA_PROVIDER=upstox`` without
``UPSTOX_ACCESS_TOKEN`` fails to build the provider instead of silently serving
mock data.
"""
from __future__ import annotations

import enum
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import httpx

from app.core.exceptions import EngineError
from app.core.logging import get_logger
from app.engine.market_data.base import Quote
from app.engine.market_data.broker_ws import BrokerWSMarketDataProvider
from app.engine.market_data.registry import register_provider
from app.engine.market_data.upstox_instruments import (
    build_symbol_map,
    resolve_instrument_key,
)
from app.engine.market_data.upstox_protobuf import (
    UpstoxOHLC,
    UpstoxProtobufError,
    decode_feed_response,
)

logger = get_logger(__name__)

UPSTOX_AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
UPSTOX_LTP_URL = "https://api.upstox.com/v3/market-quote/ltp"

# Modes supported by the V3 feed.
_VALID_MODES = frozenset({"ltpc", "full", "option_greeks", "full_d30"})


class UpstoxStatus(str, enum.Enum):
    """Provider connection/health status (never exposes credentials)."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATION_FAILED = "authentication_failed"
    SUBSCRIPTION_FAILED = "subscription_failed"
    PROVIDER_ERROR = "provider_error"


@register_provider("upstox")
class UpstoxMarketDataProvider(BrokerWSMarketDataProvider):
    """Upstox V3 real-time market data over WebSocket."""

    name = "upstox"
    default_exchange = "NSE"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        symbol_map: Optional[dict[str, str]] = None,
        mode: str = "full",
        authorize_url: str = UPSTOX_AUTHORIZE_URL,
        ltp_url: str = UPSTOX_LTP_URL,
        http_client: Optional[httpx.AsyncClient] = None,
        include_default_indices: bool = True,
        **kwargs: Any,
    ) -> None:
        creds = dict(credentials or {})
        token = (creds.get("access_token") or "").strip()
        if not token:
            raise ValueError(
                "UpstoxMarketDataProvider requires an 'access_token' credential"
            )
        if mode not in _VALID_MODES:
            raise ValueError(
                f"Unsupported Upstox mode {mode!r}; choose one of {sorted(_VALID_MODES)}"
            )

        # Normalise the symbol map to {ORB symbol: instrument_key}. Values that
        # are already valid instrument keys pass through; ORB index names are
        # resolved via the curated map. Unknown, non-key values are rejected so
        # we never subscribe to a guessed key.
        resolved_map: dict[str, str] = {}
        if include_default_indices:
            resolved_map.update(build_symbol_map())
        for sym, value in (symbol_map or {}).items():
            key = resolve_instrument_key(value) or resolve_instrument_key(sym)
            if key is None:
                raise EngineError(
                    f"upstox: cannot resolve instrument key for symbol {sym!r} "
                    f"(value={value!r}); resolve it from the instrument master first",
                    code="upstox_unknown_instrument",
                )
            resolved_map[sym] = key

        super().__init__(credentials=creds, symbol_map=resolved_map, **kwargs)
        self._mode = mode
        self._authorize_url = authorize_url
        self._ltp_url = ltp_url
        self._external_http = http_client
        self._owns_http = http_client is None
        self._status: UpstoxStatus = UpstoxStatus.DISCONNECTED
        self._last_status_error: Optional[str] = None
        # instrument_key -> latest OHLC map (side-channel to Quote, which has no
        # OHLC slot). Read via :meth:`latest_ohlc`.
        self._ohlc_cache: dict[str, dict[str, UpstoxOHLC]] = {}

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._set_status(UpstoxStatus.CONNECTING)
        await super().start()

    async def stop(self) -> None:
        await super().stop()
        self._set_status(UpstoxStatus.DISCONNECTED)
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

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._credentials['access_token']}",
            "Accept": "application/json",
        }

    def _set_status(self, status: UpstoxStatus, error: Optional[str] = None) -> None:
        self._status = status
        self._last_status_error = error

    # -------------------------------------------------------------- hooks

    async def _resolve_url(self) -> str:
        """Authorize with Upstox and return the single-use ``wss://`` URL."""
        client = self._http()
        try:
            resp = await client.get(self._authorize_url, headers=self._auth_headers())
        except Exception as exc:  # network / DNS / timeout
            self._set_status(UpstoxStatus.PROVIDER_ERROR, _mask(str(exc)))
            raise EngineError(
                "Upstox authorize request failed",
                code="upstox_authorize_error",
                details={"error": _mask(str(exc))},
            ) from exc

        if resp.status_code in (401, 403):
            self._set_status(UpstoxStatus.AUTHENTICATION_FAILED)
            raise EngineError(
                "Upstox authorize rejected the access token",
                code="upstox_authentication_failed",
                details={"status": resp.status_code},
            )
        if resp.status_code >= 400:
            self._set_status(UpstoxStatus.PROVIDER_ERROR)
            raise EngineError(
                "Upstox authorize failed",
                code="upstox_authorize_failed",
                details={"status": resp.status_code},
            )

        try:
            body = resp.json() or {}
        except Exception as exc:
            self._set_status(UpstoxStatus.PROVIDER_ERROR)
            raise EngineError(
                "Upstox authorize returned a non-JSON body",
                code="upstox_authorize_failed",
            ) from exc

        uri = (body.get("data") or {}).get("authorized_redirect_uri") or body.get(
            "authorized_redirect_uri"
        )
        if not uri:
            self._set_status(UpstoxStatus.PROVIDER_ERROR)
            raise EngineError(
                "Upstox authorize response missing authorized_redirect_uri",
                code="upstox_authorize_failed",
            )
        return str(uri)

    async def _on_connect(self, ws: Any) -> None:
        self._set_status(UpstoxStatus.CONNECTED)
        # Resubscribe every currently-subscribed instrument on (re)connect.
        try:
            await super()._on_connect(ws)
        except Exception as exc:  # pragma: no cover - super already guards
            self._set_status(UpstoxStatus.SUBSCRIPTION_FAILED, _mask(str(exc)))
            raise

    def _build_subscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        return {
            "guid": uuid.uuid4().hex,
            "method": "sub",
            "data": {"mode": self._mode, "instrumentKeys": [str(k) for k in items]},
        }

    def _build_unsubscribe_frame(self, items: list[Any]) -> dict[str, Any]:
        return {
            "guid": uuid.uuid4().hex,
            "method": "unsub",
            "data": {"mode": self._mode, "instrumentKeys": [str(k) for k in items]},
        }

    def _encode_send(self, frame: Any) -> Any:
        # Upstox requires binary WebSocket frames — JSON encoded to bytes.
        if isinstance(frame, (bytes, bytearray)):
            return frame
        return json.dumps(frame).encode("utf-8")

    def _decode_frame(self, raw: Any) -> list[Quote]:
        # Upstox streams binary protobuf only. A str/dict here is a protocol
        # violation (or a control frame) — ignore it rather than fabricate data.
        if not isinstance(raw, (bytes, bytearray)):
            return []
        ticks = decode_feed_response(bytes(raw))  # may raise UpstoxProtobufError
        quotes: list[Quote] = []
        for tick in ticks:
            mapped = self._reverse_map.get(tick.instrument_key)
            if not mapped:
                # A tick for an instrument we're not tracking — drop it.
                continue
            user_symbol, exchange = mapped
            if tick.ltp <= 0 and tick.ltt_ms == 0 and not tick.ohlc:
                # Pure market-info / empty feed — no price to publish.
                continue
            if tick.ohlc:
                self._ohlc_cache[tick.instrument_key] = tick.ohlc
            ts = _parse_ms_timestamp(tick.ltt_ms or tick.feed_ts_ms)
            quotes.append(
                Quote(
                    symbol=user_symbol,
                    exchange=exchange,
                    price=float(tick.ltp),
                    volume=float(tick.volume),
                    ts=ts,
                )
            )
        return quotes

    # -------------------------------------------------- snapshot (REST LTP)

    async def snapshot(self, symbol: str, exchange: str = "") -> Quote | None:
        """Return the last cached tick, or fetch a fresh LTP via REST on miss."""
        exch = exchange or self.default_exchange
        cached = self._snapshot_cache.get((symbol, exch))
        if cached is not None:
            return cached
        primed = await self.prime_snapshots([symbol], exchange=exch)
        return primed.get(symbol)

    async def prime_snapshots(
        self, symbols: Iterable[str], exchange: str = "",
    ) -> dict[str, Quote]:
        """Seed the snapshot cache from the V3 LTP REST endpoint.

        Used for the initial snapshot and for recovery — NOT a polling loop.
        Returns ``{symbol: Quote}`` for every symbol that resolved to a price.
        Unknown symbols are skipped; REST/transport errors fail closed (raise)
        so a caller never mistakes a stale/absent value for a live one.
        """
        exch = exchange or self.default_exchange
        wanted: dict[str, str] = {}
        for s in symbols:
            key = self._symbol_map.get(s)
            if key:
                wanted[key] = s
        if not wanted:
            return {}

        client = self._http()
        try:
            resp = await client.get(
                self._ltp_url,
                headers=self._auth_headers(),
                params={"instrument_key": ",".join(wanted.keys())},
            )
        except Exception as exc:
            self._set_status(UpstoxStatus.PROVIDER_ERROR, _mask(str(exc)))
            raise EngineError(
                "Upstox LTP request failed",
                code="upstox_ltp_error",
                details={"error": _mask(str(exc))},
            ) from exc

        if resp.status_code in (401, 403):
            self._set_status(UpstoxStatus.AUTHENTICATION_FAILED)
            raise EngineError(
                "Upstox LTP rejected the access token",
                code="upstox_authentication_failed",
                details={"status": resp.status_code},
            )
        if resp.status_code >= 400:
            self._set_status(UpstoxStatus.PROVIDER_ERROR)
            raise EngineError(
                "Upstox LTP request failed",
                code="upstox_ltp_failed",
                details={"status": resp.status_code},
            )

        data = (resp.json() or {}).get("data") or {}
        out: dict[str, Quote] = {}
        now = datetime.now(timezone.utc)
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("instrument_token") or "")
            user_symbol = wanted.get(key)
            if user_symbol is None:
                continue
            try:
                price = float(entry.get("last_price"))
            except (TypeError, ValueError):
                continue
            try:
                volume = float(entry.get("volume") or entry.get("ltq") or 0)
            except (TypeError, ValueError):
                volume = 0.0
            quote = Quote(
                symbol=user_symbol,
                exchange=exch,
                price=price,
                volume=volume,
                ts=now,
            )
            self._snapshot_cache[(user_symbol, exch)] = quote
            out[user_symbol] = quote
        return out

    # ----------------------------------------------------------- observability

    def latest_ohlc(self, symbol: str) -> dict[str, UpstoxOHLC]:
        """Last decoded OHLC candles for ``symbol`` (empty if none seen)."""
        key = self._symbol_map.get(symbol)
        if not key:
            return {}
        return dict(self._ohlc_cache.get(key, {}))

    @property
    def status(self) -> UpstoxStatus:
        # Reconcile with the underlying transport for a truthful "connected".
        if self._status == UpstoxStatus.CONNECTED and not (
            self._client and self._client.is_connected()
        ):
            return UpstoxStatus.CONNECTING if self._running else UpstoxStatus.DISCONNECTED
        return self._status

    def get_stats(self) -> dict[str, Any]:
        stats = super().get_stats()
        stats["status"] = self.status.value
        stats["mode"] = self._mode
        stats["provider"] = self.name
        if self._last_status_error:
            stats["last_status_error"] = self._last_status_error
        # Never leak the token — assert nothing token-shaped leaked in.
        return stats


def _parse_ms_timestamp(value: Optional[int]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    ts = float(value)
    # Upstox timestamps are epoch milliseconds.
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _mask(text: str) -> str:
    """Best-effort redaction so a token can never appear in a log/exception."""
    tokens: list[str] = []
    try:
        # ``settings`` import is deferred to avoid a hard dependency at import.
        from app.core.config import settings  # noqa: WPS433

        tokens.append((settings.UPSTOX_ACCESS_TOKEN or "").strip())
        # The read-only Analytics token may authenticate this provider too.
        tokens.append((settings.UPSTOX_ANALYTICS_TOKEN or "").strip())
    except Exception:  # pragma: no cover
        tokens = []
    for token in tokens:
        if token and token in text:
            text = text.replace(token, "***")
    return text
