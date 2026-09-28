"""Dhan instrument master loader.

Downloads Dhan's public scrip master CSV and parses it into an
:class:`InstrumentMaster`.

Public URL (no auth required)::

    https://images.dhan.co/api-data/api-scrip-master.csv

Every row is one tradable instrument. Column names in the current schema
(subject to change on Dhan's side — the parser tolerates extra columns and
missing optional fields):

- ``SEM_SMST_SECURITY_ID`` — numeric security id (aka "token")
- ``SEM_TRADING_SYMBOL``  — tradingsymbol (``TCS``, ``NIFTY 24DEC 21000 CE``)
- ``SEM_EXM_EXCH_ID``     — exchange (``NSE`` / ``BSE`` / ``MCX`` / …)
- ``SEM_SEGMENT``         — ``E`` (equity) / ``D`` (derivatives) / ``I`` (index)
- ``SEM_EXCH_INSTRUMENT_TYPE`` — ``EQUITY`` / ``FUTIDX`` / ``OPTIDX`` / …
- ``SEM_LOT_UNITS``       — lot size (int)
- ``SEM_TICK_SIZE``       — tick size (float, in paise for equities)
- ``SEM_ISIN``            — ISIN (optional)
- ``SEM_EXPIRY_DATE``     — DD/MM/YYYY (F&O only)
- ``SEM_STRIKE_PRICE``    — strike (F&O options only)
- ``SEM_OPTION_TYPE``     — ``CE`` / ``PE`` (F&O options only)
- ``SEM_CUSTOM_SYMBOL``   — underlying (options only)

Callers can also inject a pre-fetched CSV string via ``csv_text`` — tests
+ offline environments use this to avoid hitting the network.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.brokers.instruments.base import (
    Instrument,
    InstrumentKind,
    InstrumentMaster,
)
from app.brokers.instruments.loader_base import (
    InstrumentLoader,
    register_loader,
)
from app.core.exceptions import EngineError
from app.core.logging import get_logger

logger = get_logger(__name__)

DHAN_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Map Dhan (SEM_EXM_EXCH_ID, SEM_SEGMENT) -> broker-native segment for the
# WS + REST APIs.
_SEGMENT_MAP: dict[tuple[str, str], str] = {
    ("NSE", "E"): "NSE_EQ",
    ("NSE", "D"): "NSE_FNO",
    ("NSE", "I"): "IDX_I",
    ("BSE", "E"): "BSE_EQ",
    ("BSE", "D"): "BSE_FNO",
    ("BSE", "I"): "IDX_I",
    ("MCX", "M"): "MCX_COMM",
    ("MCX", "D"): "MCX_COMM",
    ("NSE", "C"): "NSE_CURRENCY",
    ("BSE", "C"): "BSE_CURRENCY",
}

_TYPE_TO_KIND: dict[str, InstrumentKind] = {
    "EQUITY": InstrumentKind.EQUITY,
    "INDEX": InstrumentKind.INDEX,
    "FUTIDX": InstrumentKind.FUTURE,
    "FUTSTK": InstrumentKind.FUTURE,
    "FUTCUR": InstrumentKind.FUTURE,
    "FUTCOM": InstrumentKind.FUTURE,
    "OPTIDX": InstrumentKind.OPTION,
    "OPTSTK": InstrumentKind.OPTION,
    "OPTCUR": InstrumentKind.OPTION,
    "OPTCOM": InstrumentKind.OPTION,
    "COM": InstrumentKind.COMMODITY,
    "CUR": InstrumentKind.CURRENCY,
}


@register_loader("dhan")
class DhanInstrumentLoader(InstrumentLoader):
    """Fetch the Dhan scrip master CSV and parse it."""

    name = "dhan"

    def __init__(
        self,
        *,
        url: str = DHAN_MASTER_URL,
        csv_text: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_s: float = 30.0,
        credentials: Optional[dict[str, Any]] = None,  # accepted but unused
    ) -> None:
        self._url = url
        self._csv_text = csv_text
        self._external_http = http_client
        self._owns_http = http_client is None
        self._timeout_s = timeout_s
        # ``credentials`` is accepted so callers can uniformly instantiate
        # every loader with the same kwargs shape.
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
        if self._csv_text is not None:
            csv_text = self._csv_text
        else:
            resp = await self._http().get(self._url)
            if resp.status_code >= 400:
                raise EngineError(
                    "Dhan scrip master download failed",
                    code="dhan_master_failed",
                    details={"status": resp.status_code},
                )
            csv_text = resp.text
        instruments = _parse_dhan_csv(csv_text)
        return InstrumentMaster(broker="dhan", instruments=instruments)


def _parse_dhan_csv(text: str) -> list[Instrument]:
    reader = csv.DictReader(io.StringIO(text))
    out: list[Instrument] = []
    for row in reader:
        try:
            inst = _row_to_instrument(row)
        except Exception:  # pragma: no cover — never let one bad row kill the batch
            logger.exception("dhan_master_row_parse_failed")
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

    token = g("SEM_SMST_SECURITY_ID", "security_id", "SecurityId")
    symbol = g("SEM_TRADING_SYMBOL", "tradingsymbol", "TradingSymbol")
    if not token or not symbol:
        return None

    exchange = g("SEM_EXM_EXCH_ID", "exchange") or "NSE"
    segment = g("SEM_SEGMENT", "segment") or "E"
    exch_segment = _SEGMENT_MAP.get((exchange, segment), f"{exchange}_{segment}")

    itype = (g("SEM_EXCH_INSTRUMENT_TYPE", "instrument_type") or "EQUITY").upper()
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

    def _to_date(v: str) -> Optional[date]:
        if not v:
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
        return None

    return Instrument(
        symbol=symbol,
        token=token,
        exchange_segment=exch_segment,
        exchange=exchange,
        kind=kind,
        instrument_type=itype,
        lot_size=_to_int(g("SEM_LOT_UNITS", "lot_size"), 1),
        tick_size=_to_float(g("SEM_TICK_SIZE", "tick_size"), 0.05),
        isin=g("SEM_ISIN", "isin") or None,
        underlying=g("SEM_CUSTOM_SYMBOL", "underlying") or None,
        expiry=_to_date(g("SEM_EXPIRY_DATE", "expiry")),
        strike=(_to_float(g("SEM_STRIKE_PRICE", "strike"), 0.0) or None)
        if g("SEM_STRIKE_PRICE", "strike")
        else None,
        option_type=(g("SEM_OPTION_TYPE", "option_type") or None) or None,
    )
