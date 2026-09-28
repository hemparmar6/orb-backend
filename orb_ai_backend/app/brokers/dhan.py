"""Dhan v2 broker adapter — production implementation.

Reference: Dhan API v2 docs — https://dhanhq.co/docs/v2/

REST endpoints (base https://api.dhan.co/v2):
- POST   /orders                     Place order
- PUT    /orders/{order_id}          Modify order
- DELETE /orders/{order_id}          Cancel order
- GET    /orders                     Order book (list)
- GET    /orders/{order_id}          Order details
- GET    /orders/external/{corr_id}  Look up by correlationId (echoes client_order_id)
- GET    /fundlimit                  Fund limits
- GET    /positions                  Net positions
- GET    /holdings                   Long-term holdings

WebSocket:
- Order updates: wss://api-order-update.dhan.co (JSON frames)

Auth:
- Every REST request MUST carry:
    access-token: <credentials.access_token>
    client-id:    <credentials.client_id>
    Content-Type: application/json
- Dhan tokens are LONG-LIVED (24h+). No OAuth exchange is required. If the
  provided access_token expires the broker returns HTTP 401 and callers must
  supply a fresh token — this adapter surfaces that as ``BrokerError``.

WS auth handshake (Dhan v2 order update feed):
- Client sends: {"LoginReq": {"MsgCode": 42, "ClientId": "...", "Token": "..."}}
- Server pushes order update frames as JSON.
"""
from __future__ import annotations

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

_BASE_URL = "https://api.dhan.co/v2"
_WS_ORDER_UPDATE_URL = "wss://api-order-update.dhan.co"
_WS_MARKET_FEED_URL = "wss://api-feed.dhan.co"

# ---- Mapping tables ---------------------------------------------------------

_SIDE_MAP = {"buy": "BUY", "sell": "SELL"}
_ORDER_TYPE_MAP = {
    "market": "MARKET",
    "limit": "LIMIT",
    "sl": "STOP_LOSS",
    "sl_m": "STOP_LOSS_MARKET",
}
_PRODUCT_MAP = {"mis": "INTRADAY", "cnc": "CNC", "nrml": "MARGIN"}
_EXCHANGE_MAP = {
    "NSE": "NSE_EQ",
    "BSE": "BSE_EQ",
    "NFO": "NSE_FNO",
    "BFO": "BSE_FNO",
    "MCX": "MCX_COMM",
    "CDS": "NSE_CURRENCY",
}

# Dhan order status strings we've seen in the wild → our normalised enum.
_STATUS_MAP = {
    "PENDING": BrokerOrderStatus.PENDING,
    "TRANSIT": BrokerOrderStatus.PENDING,
    "OPEN": BrokerOrderStatus.OPEN,
    "TRIGGERED": BrokerOrderStatus.OPEN,
    "PART_TRADED": BrokerOrderStatus.PARTIALLY_FILLED,
    "PARTIALLY_FILLED": BrokerOrderStatus.PARTIALLY_FILLED,
    "TRADED": BrokerOrderStatus.FILLED,
    "FILLED": BrokerOrderStatus.FILLED,
    "CANCELLED": BrokerOrderStatus.CANCELLED,
    "CANCELED": BrokerOrderStatus.CANCELLED,
    "REJECTED": BrokerOrderStatus.REJECTED,
    "EXPIRED": BrokerOrderStatus.CANCELLED,
}


def _normalise_status(raw_status: Any) -> BrokerOrderStatus:
    if not isinstance(raw_status, str):
        return BrokerOrderStatus.OPEN
    return _STATUS_MAP.get(raw_status.upper(), BrokerOrderStatus.OPEN)


@register_broker("dhan")
class DhanBrokerAdapter(BrokerAdapter):
    """Production adapter for Dhan (dhanhq.co) v2 REST + WS."""

    @classmethod
    def required_credentials(cls) -> list[str]:
        return ["client_id", "access_token"]

    @classmethod
    def validate_credentials(cls, creds: dict[str, Any]) -> None:
        # First: missing/empty required keys.
        BrokerAdapter.validate_credentials.__func__(cls, creds)  # type: ignore[attr-defined]
        token = str(creds.get("access_token", ""))
        if len(token) < 8:
            raise BrokerCredentialsInvalidError(
                "Dhan access_token looks too short — expected a JWT-like token.",
                details={"field": "access_token"},
            )

    def __init__(self, credentials: dict[str, Any], *, alias: str = "") -> None:
        super().__init__(credentials, alias=alias)
        self._client_id = str(credentials["client_id"])
        self._access_token = str(credentials["access_token"])
        self._http: BrokerHTTPClient = BrokerHTTPClient(
            base_url=_BASE_URL,
            broker="dhan",
            base_headers={
                "access-token": self._access_token,
                "client-id": self._client_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        self._ws: Optional[ReconnectingWSClient] = None

    # ---- lifecycle ----------------------------------------------------

    async def start(self) -> None:
        # Warm the connection pool. Actual auth is header-based so no login call.
        await self._http._ensure_client()  # type: ignore[attr-defined]

    async def stop(self) -> None:
        try:
            if self._ws is not None:
                await self._ws.close()
        finally:
            await self._http.aclose()

    async def health_check(self) -> bool:
        """Lightweight ping — a funds call is the cheapest authenticated endpoint."""
        try:
            await self._http.get("/fundlimit")
            return True
        except BrokerError as exc:
            logger.info("dhan_health_check_failed", extra={"error": str(exc)})
            return False

    # ---- order lifecycle ---------------------------------------------

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderResult:
        exchange = _EXCHANGE_MAP.get(req.exchange.upper(), req.exchange.upper())
        try:
            payload: dict[str, Any] = {
                "dhanClientId": self._client_id,
                "correlationId": req.client_order_id,  # so we can look up by our id
                "transactionType": _SIDE_MAP[req.side.lower()],
                "exchangeSegment": exchange,
                "productType": _PRODUCT_MAP[req.product.lower()],
                "orderType": _ORDER_TYPE_MAP[req.order_type.lower()],
                "validity": "DAY",
                "securityId": str(req.symbol),
                "quantity": int(req.quantity),
                "disclosedQuantity": 0,
                "afterMarketOrder": False,
            }
        except KeyError as exc:  # pragma: no cover - defensive
            raise BrokerError(
                f"Dhan: unsupported field {exc}",
                details={"field": str(exc)},
            ) from exc
        if req.price is not None:
            payload["price"] = float(req.price)
        if req.trigger_price is not None:
            payload["triggerPrice"] = float(req.trigger_price)
        if req.tag:
            payload["boProfitValue"] = None  # unused
            payload["remarks"] = req.tag

        body = await self._http.post(
            "/orders",
            json_body=payload,
            idempotency_key=req.client_order_id,
        )
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            data = body if isinstance(body, dict) else {}
        broker_order_id = str(
            data.get("orderId") or data.get("order_id") or body.get("orderId") or ""
        )
        if not broker_order_id:
            raise BrokerError(
                "Dhan: place_order returned no orderId",
                details={"response": body},
            )
        raw_status = data.get("orderStatus") or body.get("orderStatus") or "PENDING"
        return BrokerOrderResult(
            broker_order_id=broker_order_id,
            status=_normalise_status(raw_status),
            filled_quantity=0.0,
            average_fill_price=None,
            client_order_id=req.client_order_id,
            raw={"broker": "dhan", "response": body},
        )

    async def modify_order(
        self,
        broker_order_id: str,
        *,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
    ) -> BrokerOrderResult:
        payload: dict[str, Any] = {
            "dhanClientId": self._client_id,
            "orderId": broker_order_id,
        }
        if quantity is not None:
            payload["quantity"] = int(quantity)
        if price is not None:
            payload["price"] = float(price)
        if trigger_price is not None:
            payload["triggerPrice"] = float(trigger_price)
        body = await self._http.put(
            f"/orders/{broker_order_id}",
            json_body=payload,
            idempotency_key=f"mod-{broker_order_id}-{int(time.time() * 1000)}",
        )
        return _parse_order_body(body, broker_order_id)

    async def cancel_order(self, broker_order_id: str) -> BrokerOrderResult:
        body = await self._http.delete(
            f"/orders/{broker_order_id}",
            idempotency_key=f"cancel-{broker_order_id}",
        )
        return _parse_order_body(body, broker_order_id, default_status=BrokerOrderStatus.CANCELLED)

    async def get_order(self, broker_order_id: str) -> BrokerOrderResult:
        body = await self._http.get(f"/orders/{broker_order_id}")
        return _parse_order_body(body, broker_order_id)

    async def get_order_by_client_id(self, client_order_id: str) -> BrokerOrderResult:
        """Look up by our internal correlationId — Dhan-specific convenience."""
        body = await self._http.get(f"/orders/external/{client_order_id}")
        return _parse_order_body(body, "")

    async def list_orders(self) -> list[BrokerOrderResult]:
        body = await self._http.get("/orders")
        entries = _as_list(body)
        return [_parse_order_entry(e) for e in entries]

    # ---- account state -----------------------------------------------

    async def get_funds(self) -> BrokerFunds:
        body = await self._http.get("/fundlimit")
        data = body if isinstance(body, dict) else {}
        # Dhan v2 returns the payload directly as a dict.
        available = _f(data, "availabelBalance", "availableBalance", "availabalBalance")
        used = _f(data, "utilizedAmount", "utilisedAmount")
        total = _f(data, "openingBalance", "sodLimit", default=available + used)
        return BrokerFunds(
            available=available,
            used=used,
            total=total,
            currency="INR",
            raw={"broker": "dhan", "response": body},
        )

    async def list_positions(self) -> list[BrokerPositionSnapshot]:
        body = await self._http.get("/positions")
        entries = _as_list(body)
        result: list[BrokerPositionSnapshot] = []
        for e in entries:
            try:
                symbol = str(e.get("tradingSymbol") or e.get("securityId") or "")
                if not symbol:
                    continue
                exchange = str(e.get("exchangeSegment") or "NSE_EQ")
                product = _reverse_product(e.get("productType"))
                net_qty = float(e.get("netQty") or e.get("netQuantity") or 0)
                avg_price = float(
                    e.get("buyAvg") or e.get("sellAvg") or e.get("avgCostPrice") or 0
                )
                realized = float(e.get("realizedProfit") or e.get("realizedPnl") or 0)
                unrealized = float(e.get("unrealizedProfit") or e.get("unrealizedPnl") or 0)
                ltp = e.get("lastTradedPrice") or e.get("ltp")
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
                        raw={"broker": "dhan", "response": e},
                    )
                )
            except (TypeError, ValueError):  # pragma: no cover
                logger.warning("dhan_position_parse_error", extra={"entry": e})
        return result

    async def list_holdings(self) -> list[BrokerPositionSnapshot]:
        """Long-term holdings (CNC). Same DTO shape as ``list_positions``."""
        body = await self._http.get("/holdings")
        entries = _as_list(body)
        result: list[BrokerPositionSnapshot] = []
        for e in entries:
            try:
                symbol = str(e.get("tradingSymbol") or e.get("securityId") or "")
                if not symbol:
                    continue
                qty = float(e.get("totalQty") or e.get("availableQty") or 0)
                avg = float(e.get("avgCostPrice") or 0)
                ltp = e.get("lastTradedPrice") or e.get("ltp")
                result.append(
                    BrokerPositionSnapshot(
                        symbol=symbol,
                        exchange=str(e.get("exchange") or "NSE"),
                        product="cnc",
                        net_quantity=qty,
                        average_price=avg,
                        last_price=float(ltp) if ltp is not None else None,
                        raw={"broker": "dhan", "response": e, "source": "holdings"},
                    )
                )
            except (TypeError, ValueError):  # pragma: no cover
                continue
        return result

    # ---- streaming ---------------------------------------------------

    async def stream_order_updates(self) -> AsyncIterator[BrokerOrderResult]:
        """Yield order updates from Dhan's WS order-update feed.

        Sends the ``LoginReq`` handshake with client_id + access_token on every
        (re)connect, then decodes frames into ``BrokerOrderResult``.
        """
        client_id = self._client_id
        access_token = self._access_token

        async def _on_connect(ws) -> None:
            login = {
                "LoginReq": {
                    "MsgCode": 42,
                    "ClientId": client_id,
                    "Token": access_token,
                }
            }
            await ws.send(json.dumps(login))

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
            # Dhan wraps order updates under "Data" or top-level.
            data = frame.get("Data") if "Data" in frame else frame
            if not isinstance(data, dict):
                return None
            oid = str(data.get("orderNo") or data.get("orderId") or "")
            if not oid:
                return None
            return _parse_order_entry(data)

        self._ws = ReconnectingWSClient(
            url_provider=_WS_ORDER_UPDATE_URL,
            broker="dhan",
            on_connect=_on_connect,
            decode=_decode,
        )
        async for update in self._ws:
            yield update


# ---- helpers ---------------------------------------------------------------


def _as_list(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [e for e in body if isinstance(e, dict)]
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
        if isinstance(data, dict):
            # some endpoints wrap the list under {"data": {"orderBook": [...]}}
            for v in data.values():
                if isinstance(v, list):
                    return [e for e in v if isinstance(e, dict)]
    return []


def _parse_order_body(
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
            raw={"broker": "dhan", "response": body},
        )
    entry = _parse_order_entry(data)
    if broker_order_id and not entry.broker_order_id:
        entry.broker_order_id = broker_order_id
    return entry


def _parse_order_entry(entry: dict[str, Any]) -> BrokerOrderResult:
    oid = str(
        entry.get("orderId")
        or entry.get("orderNo")
        or entry.get("order_id")
        or ""
    )
    raw_status = entry.get("orderStatus") or entry.get("status") or "OPEN"
    filled_qty = float(
        entry.get("filledQty")
        or entry.get("filled_qty")
        or entry.get("tradedQty")
        or 0
    )
    avg = entry.get("averageTradedPrice") or entry.get("avgPrice") or entry.get("tradedPrice")
    reject = entry.get("omsErrorDescription") or entry.get("rejectionReason") or entry.get("errorMessage")
    corr = entry.get("correlationId") or entry.get("clientOrderId")
    return BrokerOrderResult(
        broker_order_id=oid,
        status=_normalise_status(raw_status),
        filled_quantity=filled_qty,
        average_fill_price=float(avg) if avg is not None else None,
        rejection_reason=str(reject) if reject else None,
        client_order_id=str(corr) if corr else None,
        raw={"broker": "dhan", "response": entry},
    )


def _f(d: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None:
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
