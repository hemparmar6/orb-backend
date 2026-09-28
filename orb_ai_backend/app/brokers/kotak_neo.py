"""Kotak Neo broker adapter — production implementation.

Reference: Kotak Neo trade API — https://napi.kotaksecurities.com/devportal/apis
and https://tradeapi.kotaksecurities.com/apis/trade-api.

Auth flow (two-step):

1. OAuth2 client-credentials: exchange base64(consumer_key:consumer_secret) for
   a short-lived ``view_token`` (the "session-token" for /session/1.0/session/*).

    POST /oauth2/token   grant_type=client_credentials
    Authorization: Basic <base64(consumer_key:consumer_secret)>

2. Login: obtain a signed ``session-token`` bound to the user's mobile_number
   + mpin (or password). ``view_token`` is used in the Authorization header.

    POST /login/1.0/login/v6/generateOTP        (only for OTP flow)
    POST /login/1.0/login/v6/validate           payload includes mobileNumber, mpin

The signed ``session-token`` (aka ``sid``) is the auth key for order and
portfolio calls. It expires daily — the adapter re-runs steps 1+2 automatically
on 401.

REST base:      https://gw-napi.kotaksecurities.com
WebSocket base: wss://mlhsm.kotaksecurities.com

REST endpoints used (base /Orders/2.0 and /Portfolio/1.0):

- POST /Orders/2.0/quick/order/rule/ms/place      Place order
- POST /Orders/2.0/quick/order/vr                 Modify order
- POST /Orders/2.0/quick/order/cancel             Cancel order
- GET  /Orders/2.0/quick/order/details            Order details (?nOrdNo=...)
- GET  /Orders/2.0/quick/user/orders              Order book
- GET  /Portfolio/1.0/portfolio/short/positions   Positions
- GET  /Portfolio/1.0/portfolio/short/holdings    Holdings
- GET  /Orders/2.0/quick/user/limits              Funds / limits

If ``session_token`` (aka ``sid``) is provided directly in credentials, the
adapter skips step 1+2 and uses it as-is. This is the preferred production
path for automated re-runs since we can't do interactive OTP validation
inside the engine.
"""
from __future__ import annotations

import base64
import json
import time
from typing import Any, AsyncIterator, Optional

from app.brokers._http import BrokerHTTPClient
from app.brokers.base import (
    BrokerAdapter,
    BrokerFunds,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerOrderStatus,
    BrokerPositionSnapshot,
)
from app.brokers.registry import register_broker
from app.brokers.websocket.reconnect import ReconnectingWSClient
from app.core.exceptions import BrokerError, BrokerCredentialsInvalidError
from app.core.logging import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://gw-napi.kotaksecurities.com"
_WS_URL = "wss://mlhsm.kotaksecurities.com"


# ---- Mapping tables --------------------------------------------------------

_SIDE_MAP = {"buy": "B", "sell": "S"}
_ORDER_TYPE_MAP = {"market": "MKT", "limit": "L", "sl": "SL", "sl_m": "SL-M"}
_PRODUCT_MAP = {"mis": "MIS", "cnc": "CNC", "nrml": "NRML"}
_EXCHANGE_MAP = {
    "NSE": "nse_cm",
    "BSE": "bse_cm",
    "NFO": "nse_fo",
    "BFO": "bse_fo",
    "MCX": "mcx_fo",
    "CDS": "cde_fo",
}
_STATUS_MAP = {
    "OPN": BrokerOrderStatus.OPEN,
    "OPEN": BrokerOrderStatus.OPEN,
    "PENDING": BrokerOrderStatus.PENDING,
    "TRIG": BrokerOrderStatus.OPEN,
    "TRIGGERED": BrokerOrderStatus.OPEN,
    "TRAD": BrokerOrderStatus.FILLED,
    "COMPLETE": BrokerOrderStatus.FILLED,
    "FILLED": BrokerOrderStatus.FILLED,
    "PARTIAL": BrokerOrderStatus.PARTIALLY_FILLED,
    "PART": BrokerOrderStatus.PARTIALLY_FILLED,
    "CAN": BrokerOrderStatus.CANCELLED,
    "CANCELLED": BrokerOrderStatus.CANCELLED,
    "REJ": BrokerOrderStatus.REJECTED,
    "REJECTED": BrokerOrderStatus.REJECTED,
}


def _normalise_status(raw: Any) -> BrokerOrderStatus:
    if not isinstance(raw, str):
        return BrokerOrderStatus.OPEN
    return _STATUS_MAP.get(raw.upper(), BrokerOrderStatus.OPEN)


@register_broker("kotak_neo")
class KotakNeoBrokerAdapter(BrokerAdapter):
    """Production adapter for Kotak Securities Neo (jNeoIT / Trade API v2)."""

    @classmethod
    def required_credentials(cls) -> list[str]:
        return [
            "consumer_key",
            "consumer_secret",
            "mobile_number",
            "mpin",
            "access_token",
        ]

    @classmethod
    def validate_credentials(cls, creds: dict[str, Any]) -> None:
        BrokerAdapter.validate_credentials.__func__(cls, creds)  # type: ignore[attr-defined]
        mobile = str(creds.get("mobile_number", ""))
        if not mobile or not any(c.isdigit() for c in mobile):
            raise BrokerCredentialsInvalidError(
                "Kotak Neo mobile_number must be a digit string (e.g. '+919999999999').",
                details={"field": "mobile_number"},
            )
        mpin = str(creds.get("mpin", ""))
        if len(mpin) < 4:
            raise BrokerCredentialsInvalidError(
                "Kotak Neo mpin must be at least 4 characters.",
                details={"field": "mpin"},
            )

    def __init__(self, credentials: dict[str, Any], *, alias: str = "") -> None:
        super().__init__(credentials, alias=alias)
        self._consumer_key = str(credentials["consumer_key"])
        self._consumer_secret = str(credentials["consumer_secret"])
        self._mobile_number = str(credentials["mobile_number"])
        self._mpin = str(credentials["mpin"])
        # ``access_token`` in our schema doubles as either:
        #   - the OAuth "view_token" (short lived) OR
        #   - a pre-generated session token (sid) that skips step 1+2.
        self._access_token_seed = str(credentials["access_token"])
        # Optional: some deployments pre-generate the signed session token.
        self._session_token: Optional[str] = credentials.get("session_token")
        # sid is the long-form JSON web token used by the trade API.
        self._sid: Optional[str] = credentials.get("sid")
        # user identifier returned from /session/1.0/session/login/userid
        self._user_id: Optional[str] = credentials.get("user_id")
        # view_token holds the client-credentials Bearer token.
        self._view_token: Optional[str] = None

        self._http: BrokerHTTPClient = BrokerHTTPClient(
            base_url=_BASE_URL,
            broker="kotak_neo",
            base_headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self._ws: Optional[ReconnectingWSClient] = None
        # Timestamp of last successful auth — lets us pre-emptively refresh.
        self._auth_ts: float = 0.0

    # ---- lifecycle ----------------------------------------------------

    async def start(self) -> None:
        await self._http._ensure_client()  # type: ignore[attr-defined]
        await self._ensure_auth()

    async def stop(self) -> None:
        try:
            if self._ws is not None:
                await self._ws.close()
        finally:
            await self._http.aclose()

    async def health_check(self) -> bool:
        try:
            await self._ensure_auth()
            await self._http.get(
                "/Orders/2.0/quick/user/limits",
                extra_headers=self._auth_headers(),
                params=self._session_params(),
            )
            return True
        except BrokerError as exc:
            logger.info("kotak_health_check_failed", extra={"error": str(exc)})
            return False

    # ---- auth ---------------------------------------------------------

    async def _ensure_auth(self) -> None:
        """Guarantee we have a valid ``sid`` + ``auth`` (session-token) pair."""
        if self._sid and self._session_token:
            return
        # If the caller supplied only ``access_token``, treat it as the seed
        # for step 2 (they've already exchanged consumer_key/secret).
        if not self._view_token:
            self._view_token = self._access_token_seed or await self._fetch_view_token()
        if not self._session_token:
            await self._login()
        self._auth_ts = time.time()

    async def _fetch_view_token(self) -> str:
        basic = base64.b64encode(
            f"{self._consumer_key}:{self._consumer_secret}".encode("utf-8")
        ).decode("ascii")
        body = await self._http.post(
            "/oauth2/token",
            json_body={"grant_type": "client_credentials"},
            extra_headers={"Authorization": f"Basic {basic}"},
            idempotency_key=f"kotak-view-{int(time.time())}",
        )
        token = (
            body.get("access_token") if isinstance(body, dict) else None
        )
        if not token:
            raise BrokerError(
                "Kotak Neo: /oauth2/token did not return an access_token",
                details={"response": body},
            )
        return str(token)

    async def _login(self) -> None:
        """Validate mobile_number + mpin, obtain signed session-token (sid)."""
        body = await self._http.post(
            "/login/1.0/login/v6/validate",
            json_body={
                "mobileNumber": self._mobile_number,
                "mpin": self._mpin,
            },
            extra_headers={"Authorization": f"Bearer {self._view_token}"},
            idempotency_key=f"kotak-login-{int(time.time())}",
        )
        # Response wraps data under {"data": {"token": "...", "sid": "...", "ucc": "..."}}
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise BrokerError(
                "Kotak Neo login: malformed response",
                details={"response": body},
            )
        self._session_token = str(data.get("token") or data.get("sessionToken") or "")
        self._sid = str(data.get("sid") or self._sid or "")
        self._user_id = str(data.get("ucc") or data.get("userId") or self._user_id or "")
        if not self._session_token or not self._sid:
            raise BrokerError(
                "Kotak Neo login: token/sid missing in response",
                details={"response": body},
            )

    def _auth_headers(self) -> dict[str, str]:
        if not self._session_token:
            raise BrokerError(
                "Kotak Neo: not authenticated (session_token missing)",
                details={"hint": "call start() first"},
            )
        # The Trade API uses view_token as Bearer + sid as 'sid' cookie/header.
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._view_token or self._access_token_seed}",
            "sid": self._sid or "",
            "auth": self._session_token,
        }
        if self._user_id:
            headers["neo-fin-key"] = "neotradeapi"
        return headers

    def _session_params(self) -> dict[str, str]:
        # Kotak requires sId query on portfolio calls in newer builds; safe to
        # pass on all authenticated requests.
        if not self._sid:
            return {}
        return {"sId": self._sid}

    async def _post_form(
        self, path: str, form: dict[str, Any], *, idempotency_key: Optional[str] = None
    ) -> Any:
        """Kotak's quick-order endpoints expect ``jData`` form-encoded JSON."""
        payload = {"jData": json.dumps(form, separators=(",", ":"))}
        # httpx.AsyncClient.request supports data=; use the low-level path.
        client = await self._http._ensure_client()  # type: ignore[attr-defined]
        headers = {
            **self._http._base_headers,  # type: ignore[attr-defined]
            **self._auth_headers(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if idempotency_key:
            headers.setdefault("Idempotency-Key", idempotency_key)
        response = await client.request(
            "POST",
            path,
            params=self._session_params(),
            data=payload,
            headers=headers,
        )
        if response.status_code == 401 and (self._view_token or self._session_token):
            # Token expired — re-auth once and retry.
            self._view_token = None
            self._session_token = None
            self._sid = None
            await self._ensure_auth()
            headers = {
                **self._http._base_headers,  # type: ignore[attr-defined]
                **self._auth_headers(),
                "Content-Type": "application/x-www-form-urlencoded",
            }
            if idempotency_key:
                headers.setdefault("Idempotency-Key", idempotency_key)
            response = await client.request(
                "POST",
                path,
                params=self._session_params(),
                data=payload,
                headers=headers,
            )
        if response.status_code >= 400:
            self._http._raise_http_error(response, "POST", path)  # type: ignore[attr-defined]
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError):
            return {"raw": response.text}

    # ---- order lifecycle ---------------------------------------------

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderResult:
        await self._ensure_auth()
        exchange = _EXCHANGE_MAP.get(req.exchange.upper(), req.exchange.lower())
        try:
            form: dict[str, Any] = {
                "am": "NO",           # after-market
                "dq": "0",             # disclosed qty
                "es": exchange,
                "mp": "0",             # market protection
                "pc": _PRODUCT_MAP[req.product.lower()],
                "pf": "N",             # pre-open flag
                "pr": str(req.price if req.price is not None else "0"),
                "pt": _ORDER_TYPE_MAP[req.order_type.lower()],
                "qt": str(int(req.quantity)),
                "rt": "DAY",           # retention
                "tp": str(req.trigger_price if req.trigger_price is not None else "0"),
                "ts": str(req.symbol),
                "tt": _SIDE_MAP[req.side.lower()],
                "ig": req.client_order_id,  # tag / correlation id
            }
        except KeyError as exc:
            raise BrokerError(
                f"Kotak Neo: unsupported field {exc}",
                details={"field": str(exc)},
            ) from exc
        body = await self._post_form(
            "/Orders/2.0/quick/order/rule/ms/place",
            form,
            idempotency_key=req.client_order_id,
        )
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            data = body if isinstance(body, dict) else {}
        broker_order_id = str(
            data.get("nOrdNo")
            or data.get("orderId")
            or data.get("orderNumber")
            or ""
        )
        if not broker_order_id:
            # Some Kotak responses put orderId in a nested "orderRes" key.
            nested = data.get("orderRes") if isinstance(data, dict) else None
            if isinstance(nested, dict):
                broker_order_id = str(nested.get("nOrdNo") or "")
        if not broker_order_id:
            raise BrokerError(
                "Kotak Neo: place_order returned no orderId",
                details={"response": body},
            )
        return BrokerOrderResult(
            broker_order_id=broker_order_id,
            status=_normalise_status(data.get("stat") or "OPEN"),
            client_order_id=req.client_order_id,
            raw={"broker": "kotak_neo", "response": body},
        )

    async def modify_order(
        self,
        broker_order_id: str,
        *,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
    ) -> BrokerOrderResult:
        await self._ensure_auth()
        form: dict[str, Any] = {"on": broker_order_id}
        if quantity is not None:
            form["qt"] = str(int(quantity))
        if price is not None:
            form["pr"] = str(price)
        if trigger_price is not None:
            form["tp"] = str(trigger_price)
        body = await self._post_form(
            "/Orders/2.0/quick/order/vr",
            form,
            idempotency_key=f"mod-{broker_order_id}-{int(time.time() * 1000)}",
        )
        return _parse_kotak_order(body, broker_order_id)

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        await self._ensure_auth()
        body = await self._post_form(
            "/Orders/2.0/quick/order/cancel",
            {"on": broker_order_id, "am": "NO"},
            idempotency_key=f"cancel-{broker_order_id}",
        )
        return _parse_kotak_order(
            body, broker_order_id, default_status=BrokerOrderStatus.CANCELLED
        )

    async def get_order(self, broker_order_id: str) -> BrokerOrderResult:
        await self._ensure_auth()
        body = await self._http.get(
            "/Orders/2.0/quick/order/details",
            params={**self._session_params(), "nOrdNo": broker_order_id},
            extra_headers=self._auth_headers(),
        )
        return _parse_kotak_order(body, broker_order_id)

    async def list_orders(self) -> list[BrokerOrderResult]:
        await self._ensure_auth()
        body = await self._http.get(
            "/Orders/2.0/quick/user/orders",
            params=self._session_params(),
            extra_headers=self._auth_headers(),
        )
        return [_parse_kotak_order_entry(e) for e in _kotak_list(body)]

    # ---- account state -----------------------------------------------

    async def get_funds(self) -> BrokerFunds:
        await self._ensure_auth()
        body = await self._http.get(
            "/Orders/2.0/quick/user/limits",
            params=self._session_params(),
            extra_headers=self._auth_headers(),
        )
        data = body if isinstance(body, dict) else {}
        # Kotak returns limits under "Limits" or "data".
        limits = data.get("Limits") or data.get("data") or data
        if isinstance(limits, list) and limits:
            limits = limits[0]
        if not isinstance(limits, dict):
            limits = {}
        available = _f(limits, "Net", "netCash", "cashAvailable", "availableMargin")
        used = _f(limits, "MarginUsed", "marginUsed", "utilizedMargin")
        total = _f(limits, "Total", "totalMargin", default=available + used)
        return BrokerFunds(
            available=available,
            used=used,
            total=total,
            currency="INR",
            raw={"broker": "kotak_neo", "response": body},
        )

    async def list_positions(self) -> list[BrokerPositionSnapshot]:
        await self._ensure_auth()
        body = await self._http.get(
            "/Portfolio/1.0/portfolio/short/positions",
            params=self._session_params(),
            extra_headers=self._auth_headers(),
        )
        result: list[BrokerPositionSnapshot] = []
        for e in _kotak_list(body):
            try:
                symbol = str(e.get("trdSym") or e.get("sym") or e.get("tradingSymbol") or "")
                if not symbol:
                    continue
                exchange = str(e.get("exSeg") or e.get("exch") or "nse_cm")
                product = _reverse_product(e.get("prod") or e.get("productType"))
                net_qty = _f(e, "flQty", "netTrdQtyLot", "netQty")
                avg_price = _f(e, "avgnetprc", "avgPrc", "avgBuyPrc", "buyAvgPrice")
                realized = _f(e, "rlPnl", "realisedPnl", "realizedPnl")
                unrealized = _f(e, "urlPnl", "unrealisedPnl", "unrealizedPnl")
                ltp = e.get("ltp") or e.get("lastTradedPrice")
                result.append(
                    BrokerPositionSnapshot(
                        symbol=symbol,
                        exchange=exchange,
                        product=product,
                        net_quantity=net_qty,
                        average_price=avg_price,
                        realized_pnl=realized,
                        unrealized_pnl=unrealized,
                        last_price=float(ltp) if ltp is not None else None,
                        raw={"broker": "kotak_neo", "response": e},
                    )
                )
            except (TypeError, ValueError):  # pragma: no cover
                logger.warning("kotak_position_parse_error", extra={"entry": e})
        return result

    async def list_holdings(self) -> list[BrokerPositionSnapshot]:
        await self._ensure_auth()
        body = await self._http.get(
            "/Portfolio/1.0/portfolio/short/holdings",
            params=self._session_params(),
            extra_headers=self._auth_headers(),
        )
        result: list[BrokerPositionSnapshot] = []
        for e in _kotak_list(body):
            try:
                symbol = str(e.get("displaySymbol") or e.get("symbol") or "")
                if not symbol:
                    continue
                qty = _f(e, "quantity", "totalQuantity")
                avg = _f(e, "averagePrice", "avgPrice")
                ltp = e.get("closingPrice") or e.get("lastTradedPrice")
                result.append(
                    BrokerPositionSnapshot(
                        symbol=symbol,
                        exchange=str(e.get("exchange") or "NSE"),
                        product="cnc",
                        net_quantity=qty,
                        average_price=avg,
                        last_price=float(ltp) if ltp is not None else None,
                        raw={"broker": "kotak_neo", "response": e, "source": "holdings"},
                    )
                )
            except (TypeError, ValueError):  # pragma: no cover
                continue
        return result

    # ---- streaming ---------------------------------------------------

    async def stream_order_updates(self) -> AsyncIterator[BrokerOrderResult]:
        await self._ensure_auth()

        sid = self._sid or ""
        session_token = self._session_token or ""

        async def _on_connect(ws) -> None:
            # Kotak Neo order feed requires a subscribe frame with sid + token.
            subscribe = {
                "type": "cn",
                "Authorization": session_token,
                "Sid": sid,
                "channel": "order",
            }
            await ws.send(json.dumps(subscribe))

        def _decode(raw: Any) -> Optional[BrokerOrderResult]:
            try:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="ignore")
                if isinstance(raw, str):
                    frame = json.loads(raw)
                else:
                    frame = raw
            except (ValueError, json.JSONDecodeError):
                return None
            if not isinstance(frame, dict):
                return None
            # Kotak wraps order updates under {"data": {...}, "type": "order"}.
            if frame.get("type") not in (None, "order", "orderUpdate", "trd"):
                return None
            entry = frame.get("data") or frame
            if not isinstance(entry, dict):
                return None
            oid = str(
                entry.get("nOrdNo") or entry.get("orderId") or entry.get("orderNumber") or ""
            )
            if not oid:
                return None
            return _parse_kotak_order_entry(entry)

        self._ws = ReconnectingWSClient(
            url_provider=lambda: f"{_WS_URL}/realtime?sId={sid}",
            broker="kotak_neo",
            on_connect=_on_connect,
            decode=_decode,
            extra_headers={"Authorization": f"Bearer {self._view_token or ''}"},
        )
        async for update in self._ws:
            yield update


# ---- helpers ---------------------------------------------------------------


def _kotak_list(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [e for e in body if isinstance(e, dict)]
    if isinstance(body, dict):
        for k in ("data", "Data", "Success", "Response"):
            v = body.get(k)
            if isinstance(v, list):
                return [e for e in v if isinstance(e, dict)]
            if isinstance(v, dict):
                for inner in v.values():
                    if isinstance(inner, list):
                        return [e for e in inner if isinstance(e, dict)]
    return []


def _parse_kotak_order(
    body: Any,
    broker_order_id: str,
    *,
    default_status: BrokerOrderStatus = BrokerOrderStatus.OPEN,
) -> BrokerOrderResult:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        data = body if isinstance(body, dict) else {}
    if not data:
        return BrokerOrderResult(
            broker_order_id=broker_order_id,
            status=default_status,
            raw={"broker": "kotak_neo", "response": body},
        )
    entry = _parse_kotak_order_entry(data)
    if broker_order_id and not entry.broker_order_id:
        entry.broker_order_id = broker_order_id
    return entry


def _parse_kotak_order_entry(entry: dict[str, Any]) -> BrokerOrderResult:
    oid = str(
        entry.get("nOrdNo")
        or entry.get("orderId")
        or entry.get("orderNumber")
        or entry.get("on")
        or ""
    )
    raw_status = (
        entry.get("ordSt")
        or entry.get("stat")
        or entry.get("orderStatus")
        or entry.get("status")
        or "OPEN"
    )
    filled_qty = _f(entry, "fldQty", "filledQty", "tradedQty", "cumQty")
    avg = entry.get("avgPrc") or entry.get("averagePrice") or entry.get("tradedPrc")
    reject = entry.get("rejRsn") or entry.get("rejectionReason") or entry.get("Rsn")
    corr = entry.get("ig") or entry.get("clientOrderId") or entry.get("correlationId")
    return BrokerOrderResult(
        broker_order_id=oid,
        status=_normalise_status(raw_status),
        filled_quantity=filled_qty,
        average_fill_price=float(avg) if avg is not None else None,
        rejection_reason=str(reject) if reject else None,
        client_order_id=str(corr) if corr else None,
        raw={"broker": "kotak_neo", "response": entry},
    )


def _f(d: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return float(default)


def _reverse_product(raw: Any) -> str:
    if not isinstance(raw, str):
        return "mis"
    inv = {v: k for k, v in _PRODUCT_MAP.items()}
    return inv.get(raw.upper(), raw.lower())


# ---- credential validation --------------------------------------------------

# validate_credentials is defined as a classmethod on the adapter class above.
