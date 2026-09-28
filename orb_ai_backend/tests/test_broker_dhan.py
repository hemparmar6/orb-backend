"""Dhan v2 adapter — production behaviour tests using httpx.MockTransport.

We inject a MockTransport into the adapter's underlying httpx.AsyncClient so
tests exercise the *real* HTTP wiring (URL building, header propagation,
retry logic, response parsing) without touching the network.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

from app.brokers.base import BrokerOrderRequest, BrokerOrderStatus
from app.brokers.dhan import DhanBrokerAdapter, _BASE_URL
from app.core.exceptions import BrokerError

VALID_CREDS = {"client_id": "1100000001", "access_token": "eyJhbGciOi.dummy.value"}


def _install_mock(adapter: DhanBrokerAdapter, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Replace the adapter's httpx client with one backed by MockTransport."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url=_BASE_URL,
        transport=transport,
        headers=adapter._http._base_headers,  # type: ignore[attr-defined]
    )
    adapter._http._client = client  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dhan_place_order_success():
    adapter = DhanBrokerAdapter(VALID_CREDS)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v2/orders"
        # headers propagate
        assert request.headers["access-token"] == VALID_CREDS["access_token"]
        assert request.headers["client-id"] == VALID_CREDS["client_id"]
        body = request.read().decode()
        assert '"transactionType":"BUY"' in body.replace(" ", "")
        assert '"exchangeSegment":"NSE_EQ"' in body.replace(" ", "")
        assert '"correlationId":"local-1"' in body.replace(" ", "")
        return httpx.Response(
            200,
            json={"orderId": "DHAN-42", "orderStatus": "PENDING"},
        )

    _install_mock(adapter, handler)
    req = BrokerOrderRequest(
        client_order_id="local-1",
        symbol="11536",  # Dhan securityId
        exchange="NSE",
        side="buy",
        order_type="market",
        product="mis",
        quantity=1,
    )
    result = await adapter.place_order(req)
    assert result.broker_order_id == "DHAN-42"
    assert result.status == BrokerOrderStatus.PENDING
    assert result.client_order_id == "local-1"
    await adapter.stop()


@pytest.mark.asyncio
async def test_dhan_place_order_missing_order_id_raises():
    adapter = DhanBrokerAdapter(VALID_CREDS)
    _install_mock(adapter, lambda r: httpx.Response(200, json={"status": "OK"}))
    req = BrokerOrderRequest(
        client_order_id="local-2",
        symbol="1594",
        exchange="NSE",
        side="sell",
        order_type="limit",
        product="cnc",
        quantity=2,
        price=1450.5,
    )
    with pytest.raises(BrokerError, match="no orderId"):
        await adapter.place_order(req)
    await adapter.stop()


@pytest.mark.asyncio
async def test_dhan_modify_cancel_get():
    adapter = DhanBrokerAdapter(VALID_CREDS)
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "PUT":
            return httpx.Response(200, json={"data": {"orderId": "DHAN-99", "orderStatus": "OPEN"}})
        if request.method == "DELETE":
            return httpx.Response(200, json={"data": {"orderId": "DHAN-99", "orderStatus": "CANCELLED"}})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "orderId": "DHAN-99",
                    "orderStatus": "TRADED",
                    "filledQty": 5,
                    "averageTradedPrice": 1500.25,
                    "correlationId": "local-3",
                },
            )
        return httpx.Response(405)

    _install_mock(adapter, handler)

    modified = await adapter.modify_order("DHAN-99", quantity=10, price=1499)
    assert modified.status == BrokerOrderStatus.OPEN

    cancelled = await adapter.cancel_order("DHAN-99")
    assert cancelled.status == BrokerOrderStatus.CANCELLED

    got = await adapter.get_order("DHAN-99")
    assert got.status == BrokerOrderStatus.FILLED
    assert got.filled_quantity == 5.0
    assert got.average_fill_price == 1500.25
    assert got.client_order_id == "local-3"

    assert [m for m, _ in seen] == ["PUT", "DELETE", "GET"]
    await adapter.stop()


@pytest.mark.asyncio
async def test_dhan_list_orders_parses_variants():
    adapter = DhanBrokerAdapter(VALID_CREDS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"orderId": "D1", "orderStatus": "OPEN", "filledQty": 0},
                {"orderId": "D2", "orderStatus": "TRADED", "filledQty": 2, "averageTradedPrice": 100.5},
                {"orderId": "D3", "orderStatus": "REJECTED", "omsErrorDescription": "Insufficient funds"},
            ],
        )

    _install_mock(adapter, handler)
    orders = await adapter.list_orders()
    assert [o.broker_order_id for o in orders] == ["D1", "D2", "D3"]
    assert orders[1].status == BrokerOrderStatus.FILLED
    assert orders[2].status == BrokerOrderStatus.REJECTED
    assert orders[2].rejection_reason == "Insufficient funds"
    await adapter.stop()


@pytest.mark.asyncio
async def test_dhan_get_funds_and_positions():
    adapter = DhanBrokerAdapter(VALID_CREDS)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/fundlimit"):
            return httpx.Response(
                200,
                json={"availabelBalance": 25000.5, "utilizedAmount": 4500, "openingBalance": 30000},
            )
        if request.url.path.endswith("/positions"):
            return httpx.Response(
                200,
                json=[
                    {
                        "tradingSymbol": "INFY",
                        "exchangeSegment": "NSE_EQ",
                        "productType": "INTRADAY",
                        "netQty": 25,
                        "buyAvg": 1450.5,
                        "realizedProfit": 100,
                        "unrealizedProfit": 25.5,
                        "lastTradedPrice": 1452.0,
                    }
                ],
            )
        return httpx.Response(404)

    _install_mock(adapter, handler)
    funds = await adapter.get_funds()
    assert funds.available == pytest.approx(25000.5)
    assert funds.used == 4500
    assert funds.total == 30000
    assert funds.currency == "INR"

    positions = await adapter.list_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "INFY"
    assert p.product == "mis"  # INTRADAY reversed
    assert p.net_quantity == 25
    assert p.average_price == 1450.5
    assert p.last_price == 1452.0
    await adapter.stop()


@pytest.mark.asyncio
async def test_dhan_retries_on_5xx_then_succeeds():
    adapter = DhanBrokerAdapter(VALID_CREDS)
    # Speed up the test by shrinking the backoff.
    adapter._http._backoff_base_s = 0.01  # type: ignore[attr-defined]
    adapter._http._backoff_max_s = 0.02  # type: ignore[attr-defined]

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"message": "temporarily unavailable"})
        return httpx.Response(200, json={"availabelBalance": 100.0, "utilizedAmount": 0})

    _install_mock(adapter, handler)
    funds = await adapter.get_funds()
    assert funds.available == 100.0
    assert calls["n"] == 3
    await adapter.stop()


@pytest.mark.asyncio
async def test_dhan_4xx_bubbles_broker_error_with_details():
    adapter = DhanBrokerAdapter(VALID_CREDS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"message": "Invalid access token", "errorType": "DH-901"},
        )

    _install_mock(adapter, handler)
    with pytest.raises(BrokerError) as excinfo:
        await adapter.get_funds()
    assert "Invalid access token" in excinfo.value.message
    assert excinfo.value.details["http_status"] == 401
    assert excinfo.value.details["broker"] == "dhan"
    await adapter.stop()


@pytest.mark.asyncio
async def test_dhan_health_check_returns_true_and_false():
    adapter = DhanBrokerAdapter(VALID_CREDS)
    state = {"ok": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["ok"]:
            return httpx.Response(200, json={"availabelBalance": 1})
        return httpx.Response(401, json={"message": "expired"})

    _install_mock(adapter, handler)
    assert await adapter.health_check() is True
    state["ok"] = False
    assert await adapter.health_check() is False
    await adapter.stop()
