"""Kotak Neo instrument master loader.

Kotak Neo publishes daily scrip masters as JSON files, one per segment,
under::

    GET https://gw-napi.kotaksecurities.com/apim/masters/v1/scripmaster/{segment}

with the standard Kotak auth headers (``Authorization: Bearer <view_token>``,
``sid``, ``auth``, ``neo-fin-key``). The response is a JSON object whose
``data`` key holds a list of instrument rows.

Row shape (subject to Kotak's schema; parser tolerates missing fields)::

    {
      "pSymbol":     "11536",
      "pTrdSymbol":  "TCS",
      "pExchSeg":    "nse_cm",
      "pExch":       "NSE",
      "pInstType":   "EQUITY",
      "pLotSize":    "1",
      "pTickSize":   "0.05",
      "pISIN":       "INE467B01029",
      "pExpiryDate": "26DEC2024",
      "pStrikePrice":"21000",
      "pOptType":    "CE",
      "pAssetName":  "NIFTY"
    }

Callers can also inject a pre-fetched dict via ``json_payload`` — tests use
this to avoid the network. Multi-segment loads are handled by passing a
list of segments in ``segments``.
"""
from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Any, Iterable, Optional

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

KOTAK_OAUTH_URL = "https://gw-napi.kotaksecurities.com/oauth2/token"
KOTAK_LOGIN_URL = "https://gw-napi.kotaksecurities.com/login/1.0/login/v6/validate"
KOTAK_MASTER_BASE = "https://gw-napi.kotaksecurities.com/apim/masters/v1/scripmaster"

_DEFAULT_SEGMENTS: tuple[str, ...] = ("nse_cm",)  # cash equities only by default

_TYPE_TO_KIND: dict[str, InstrumentKind] = {
    "EQUITY": InstrumentKind.EQUITY,
    "EQ": InstrumentKind.EQUITY,
    "INDEX": InstrumentKind.INDEX,
    "IDX": InstrumentKind.INDEX,
    "FUT": InstrumentKind.FUTURE,
    "FUTIDX": InstrumentKind.FUTURE,
    "FUTSTK": InstrumentKind.FUTURE,
    "OPT": InstrumentKind.OPTION,
    "OPTIDX": InstrumentKind.OPTION,
    "OPTSTK": InstrumentKind.OPTION,
    "OPTCUR": InstrumentKind.OPTION,
    "CE": InstrumentKind.OPTION,
    "PE": InstrumentKind.OPTION,
    "CUR": InstrumentKind.CURRENCY,
    "COM": InstrumentKind.COMMODITY,
}


@register_loader("kotak_neo")
class KotakInstrumentLoader(InstrumentLoader):
    """Fetch and parse Kotak Neo's scrip master."""

    name = "kotak_neo"

    def __init__(
        self,
        *,
        credentials: dict[str, Any],
        segments: Iterable[str] = _DEFAULT_SEGMENTS,
        base_url: str = KOTAK_MASTER_BASE,
        json_payload: Optional[dict[str, Any]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_s: float = 30.0,
    ) -> None:
        creds = dict(credentials or {})
        has_signed = creds.get("sid") and creds.get("session_token")
        has_full = all(
            creds.get(k)
            for k in ("consumer_key", "consumer_secret", "mobile_number", "mpin")
        )
        if not has_signed and not has_full and json_payload is None:
            raise ValueError(
                "KotakInstrumentLoader requires either "
                "(sid + session_token), "
                "(consumer_key + consumer_secret + mobile_number + mpin), "
                "or a pre-fetched json_payload"
            )
        self._credentials = creds
        self._segments = tuple(segments) if segments else _DEFAULT_SEGMENTS
        self._base_url = base_url.rstrip("/")
        self._json_payload = json_payload
        self._external_http = http_client
        self._owns_http = http_client is None
        self._timeout_s = timeout_s

        self._sid: Optional[str] = creds.get("sid")
        self._session_token: Optional[str] = creds.get("session_token")
        self._view_token: Optional[str] = creds.get("view_token")

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

    # ------------------------------------------------------------------ auth

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
                "Kotak Neo master: OAuth failed",
                code="kotak_oauth_failed",
                details={"status": oauth.status_code},
            )
        self._view_token = str((oauth.json() or {}).get("access_token") or "")
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
                "Kotak Neo master: login failed",
                code="kotak_login_failed",
                details={"status": login.status_code},
            )
        data = (login.json() or {}).get("data") or {}
        self._session_token = str(data.get("token") or "")
        self._sid = str(data.get("sid") or "")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._view_token or self._session_token or ''}",
            "sid": self._sid or "",
            "auth": self._session_token or "",
            "neo-fin-key": "neotradeapi",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------ fetch()

    async def fetch(self) -> InstrumentMaster:
        if self._json_payload is not None:
            rows = _extract_rows(self._json_payload)
            return InstrumentMaster(
                broker="kotak_neo", instruments=_parse_rows(rows)
            )
        await self._authenticate()
        all_rows: list[dict[str, Any]] = []
        for seg in self._segments:
            url = f"{self._base_url}/{seg}"
            resp = await self._http().get(url, headers=self._headers())
            if resp.status_code >= 400:
                raise EngineError(
                    "Kotak Neo master download failed",
                    code="kotak_master_failed",
                    details={"status": resp.status_code, "segment": seg},
                )
            payload = resp.json() or {}
            all_rows.extend(_extract_rows(payload))
        return InstrumentMaster(
            broker="kotak_neo", instruments=_parse_rows(all_rows)
        )


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Kotak wraps rows under ``data`` (sometimes as a list, sometimes as a
    dict with ``masters``). Handle both.
    """
    data = payload.get("data") or payload
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Some responses use "masters" or a segment-keyed dict.
        rows = data.get("masters") or data.get("scripMasters") or data.get("rows")
        if isinstance(rows, list):
            return rows
        # Segment-keyed dict — flatten values.
        flat: list[dict[str, Any]] = []
        for v in data.values():
            if isinstance(v, list):
                flat.extend(v)
        return flat
    return []


def _parse_rows(rows: list[dict[str, Any]]) -> list[Instrument]:
    out: list[Instrument] = []
    for row in rows:
        try:
            inst = _row_to_instrument(row)
        except Exception:  # pragma: no cover
            logger.exception("kotak_master_row_parse_failed")
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

    token = g("pSymbol", "token", "sToken")
    symbol = g("pTrdSymbol", "trdSym", "tradingsymbol", "symbol")
    if not token or not symbol:
        return None

    segment = g("pExchSeg", "exchange_segment", "exchSeg") or "nse_cm"
    exchange = g("pExch", "exchange") or _exchange_from_segment(segment)
    itype = (g("pInstType", "instrument_type") or "EQUITY").upper()
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
        for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d%m%Y"):
            try:
                return datetime.strptime(v.upper(), fmt).date()
            except ValueError:
                continue
        return None

    return Instrument(
        symbol=symbol,
        token=token,
        exchange_segment=segment,
        exchange=exchange,
        kind=kind,
        lot_size=_to_int(g("pLotSize", "lot_size"), 1),
        tick_size=_to_float(g("pTickSize", "tick_size"), 0.05),
        isin=g("pISIN", "isin") or None,
        underlying=g("pAssetName", "underlying") or None,
        expiry=_to_date(g("pExpiryDate", "expiry")),
        strike=(_to_float(g("pStrikePrice", "strike"), 0.0) or None)
        if g("pStrikePrice", "strike")
        else None,
        option_type=(g("pOptType", "option_type") or None) or None,
    )


def _exchange_from_segment(segment: str) -> str:
    seg = segment.lower()
    if seg.startswith("nse"):
        return "NSE"
    if seg.startswith("bse"):
        return "BSE"
    if seg.startswith("mcx"):
        return "MCX"
    return "NSE"
