"""Upstox instrument-master loader.

Upstox publishes its full instrument universe as a gzipped JSON file (no auth
required)::

    https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz

Each record carries an ``instrument_key`` — Upstox's single opaque identifier
used by every V3 market-data API. Unlike Dhan/Kotak (which split identity into
``(token, segment)``), Upstox needs only that one string to subscribe. We
therefore load it into the shared :class:`InstrumentMaster` with
``token = instrument_key`` and ``exchange_segment = segment`` so the existing
tooling (``lookup`` / ``to_ws_symbol_map``) keeps working, and expose
:func:`upstox_symbol_map` to emit the ``{symbol: instrument_key}`` shape the
:class:`~app.engine.market_data.upstox.UpstoxMarketDataProvider` expects.

Tests + offline environments inject a pre-fetched ``json_text`` (a JSON array of
records) to avoid any network access.
"""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from typing import Any, Iterable, Optional

import httpx

from app.brokers.instruments.base import Instrument, InstrumentKind, InstrumentMaster
from app.brokers.instruments.loader_base import InstrumentLoader, register_loader
from app.core.exceptions import EngineError
from app.core.logging import get_logger

logger = get_logger(__name__)

UPSTOX_MASTER_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
)

_TYPE_TO_KIND: dict[str, InstrumentKind] = {
    "EQ": InstrumentKind.EQUITY,
    "EQUITY": InstrumentKind.EQUITY,
    "INDEX": InstrumentKind.INDEX,
    "FUT": InstrumentKind.FUTURE,
    "FUTIDX": InstrumentKind.FUTURE,
    "FUTSTK": InstrumentKind.FUTURE,
    "FUTCOM": InstrumentKind.FUTURE,
    "FUTCUR": InstrumentKind.FUTURE,
    "CE": InstrumentKind.OPTION,
    "PE": InstrumentKind.OPTION,
    "OPTIDX": InstrumentKind.OPTION,
    "OPTSTK": InstrumentKind.OPTION,
    "COM": InstrumentKind.COMMODITY,
    "CUR": InstrumentKind.CURRENCY,
}


@register_loader("upstox")
class UpstoxInstrumentLoader(InstrumentLoader):
    """Fetch + parse the Upstox complete instrument master (gzipped JSON)."""

    name = "upstox"

    def __init__(
        self,
        *,
        url: str = UPSTOX_MASTER_URL,
        json_text: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_s: float = 60.0,
        credentials: Optional[dict[str, Any]] = None,  # accepted but unused
    ) -> None:
        self._url = url
        self._json_text = json_text
        self._external_http = http_client
        self._owns_http = http_client is None
        self._timeout_s = timeout_s
        self._credentials = credentials or {}

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

    async def fetch(self) -> InstrumentMaster:
        if self._json_text is not None:
            payload = json.loads(self._json_text)
        else:
            resp = await self._http().get(self._url)
            if resp.status_code >= 400:
                raise EngineError(
                    "Upstox instrument master download failed",
                    code="upstox_master_failed",
                    details={"status": resp.status_code},
                )
            content = resp.content
            if self._url.endswith(".gz") or content[:2] == b"\x1f\x8b":
                content = gzip.decompress(content)
            payload = json.loads(content)
        instruments = _parse_records(payload)
        return InstrumentMaster(broker="upstox", instruments=instruments)


def _parse_records(payload: Any) -> list[Instrument]:
    records: Iterable[dict[str, Any]]
    if isinstance(payload, dict):
        records = payload.get("data") or payload.get("instruments") or []
    else:
        records = payload or []
    out: list[Instrument] = []
    for row in records:
        try:
            inst = _row_to_instrument(row)
        except Exception:  # pragma: no cover — one bad row must not kill the batch
            logger.exception("upstox_master_row_parse_failed")
            continue
        if inst is not None:
            out.append(inst)
    return out


def _row_to_instrument(row: dict[str, Any]) -> Optional[Instrument]:
    def g(*keys: str) -> str:
        for k in keys:
            v = row.get(k)
            if v not in (None, ""):
                return str(v).strip()
        return ""

    instrument_key = g("instrument_key", "instrumentKey")
    symbol = g("trading_symbol", "tradingsymbol", "name")
    if not instrument_key or not symbol:
        return None

    segment = g("segment") or (instrument_key.split("|", 1)[0] if "|" in instrument_key else "")
    exchange = g("exchange") or (segment.split("_", 1)[0] if "_" in segment else "NSE")
    itype = (g("instrument_type") or "EQ").upper()
    kind = _TYPE_TO_KIND.get(itype, InstrumentKind.EQUITY)

    def _to_int(v: str, default: int = 1) -> int:
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def _to_float(v: str, default: float = 0.05) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _to_date(v: Any) -> Optional[date]:
        if not v:
            return None
        s = str(v)
        # Upstox often encodes expiry as epoch millis.
        if s.isdigit() and len(s) >= 11:
            try:
                return datetime.fromtimestamp(int(s) / 1000.0).date()
            except (ValueError, OSError):
                return None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    option_type = None
    if itype in ("CE", "PE"):
        option_type = itype

    return Instrument(
        symbol=symbol,
        token=instrument_key,               # Upstox identity == instrument_key
        exchange_segment=segment or "NSE_EQ",
        exchange=exchange,
        kind=kind,
        instrument_type=itype,
        lot_size=_to_int(g("lot_size", "lotSize"), 1),
        tick_size=_to_float(g("tick_size", "tickSize"), 0.05),
        isin=g("isin") or None,
        underlying=g("underlying_symbol", "underlying") or None,
        expiry=_to_date(row.get("expiry")),
        strike=(_to_float(g("strike_price", "strike"), 0.0) or None)
        if g("strike_price", "strike")
        else None,
        option_type=option_type,
    )


def upstox_symbol_map(
    master: InstrumentMaster,
    symbols: Optional[Iterable[str]] = None,
    *,
    exchange: Optional[str] = None,
) -> dict[str, str]:
    """Emit ``{symbol: instrument_key}`` for the Upstox provider.

    Built on top of :meth:`InstrumentMaster.to_ws_symbol_map`; the tuple's first
    element is the instrument key (see :class:`UpstoxInstrumentLoader`).
    """
    tuple_map = master.to_ws_symbol_map(symbols, exchange=exchange)
    return {sym: token for sym, (token, _segment) in tuple_map.items()}
