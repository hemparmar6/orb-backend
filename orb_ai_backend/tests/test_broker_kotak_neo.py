"""Kotak Neo adapter — production behaviour tests using httpx.MockTransport.

Kotak has a 2-step auth flow (OAuth token + login/validate). We inject a
MockTransport that services BOTH the auth endpoints AND the trade endpoints
so we can validate the adapter's re-auth behaviour end-to-end.
"""
from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from app.brokers.base import BrokerOrderRequest, BrokerOrderStatus
from app.brokers.kotak_neo import KotakNeoBrokerAdapter, _BASE_URL
from app.core.exceptions import BrokerError

VALID_CREDS = {
    "consumer_key": "consumer-key-value",
    "consumer_secret": "consumer-secret-value",
    "mobile_number": "+919999999999",
    "mpin": "1234",
    "access_token": "seed-view-token",
}


def _install_mock(
    adapter: KotakNeoBrokerAdapter,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        base_url=_BASE_URL,
        transport=transport,
        headers=adapter._http._base_headers,  # type: ignore[attr-defined]
    )
    adapter._http._client = client  # type: ignore[attr-defined]


def _login_success_handler(extra: Callable[[httpx.Request], httpx.Response]):
    """Return a handler that services login endpoints, then delegates."""

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "view-token-xyz", "expires_in": 3600})
        if p.endswith("/login/1.0/login/v6/validate"):
            body = json.loads(request.read().decode() or "{}")
            assert body.get("mobileNumber") == "+919999999999"
            assert body.get("mpin") == "1234"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "token": "signed-session-token",
                        "sid": "SID-1234",
                        "ucc": "UCC42",
                    }
                },
            )
        return extra(request)

    return handler


@pytest.mark.asyncio
async def test_kotak_login_flow_happy_path():
    adapter = KotakNeoBrokerAdapter(VALID_CREDS)

    def endpoint(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Orders/2.0/quick/user/limits"):
            assert request.headers["auth"] == "signed-session-token"
            assert request.headers["sid"] == "SID-1234"
            assert "Bearer" in request.headers["authorization"]
            return httpx.Response(200, json={"data": {"Net": 100000, "MarginUsed": 25000}})
        return httpx.Response(404)

    _install_mock(adapter, _login_success_handler(endpoint))

    funds = await adapter.get_funds()
    assert funds.available == 100000
    assert funds.used == 25000
    await adapter.stop()


@pytest.mark.asyncio
async def test_kotak_place_order_success():
    adapter = KotakNeoBrokerAdapter(VALID_CREDS)

    def endpoint(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Orders/2.0/quick/order/rule/ms/place"):
            # jData form-encoded
            body = request.read().decode()
            assert "jData=" in body
            # unquoted json payload
            j_json = body.split("jData=", 1)[1]
            # url-decoded parsing via httpx form parser
            import urllib.parse
            decoded = urllib.parse.unquote_plus(j_json)
            j = json.loads(decoded)
            assert j["tt"] == "B"
            assert j["pt"] == "MKT"
            assert j["pc"] == "MIS"
            assert j["es"] == "nse_cm"
            assert j["ts"] == "INFY-EQ"
            assert j["ig"] == "local-1"
            return httpx.Response(200, json={"data": {"nOrdNo": "KN-77", "stat": "Ok"}})
        return httpx.Response(404)

    _install_mock(adapter, _login_success_handler(endpoint))

    req = BrokerOrderRequest(
        client_order_id="local-1",
        symbol="INFY-EQ",
        exchange="NSE",
        side="buy",
        order_type="market",
        product="mis",
        quantity=10,
    )
    result = await adapter.place_order(req)
    assert result.broker_order_id == "KN-77"
    assert result.client_order_id == "local-1"
    await adapter.stop()


@pytest.mark.asyncio
async def test_kotak_modify_and_cancel():
    adapter = KotakNeoBrokerAdapter(VALID_CREDS)
    calls: list[str] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/Orders/2.0/quick/order/vr"):
            return httpx.Response(
                200,
                json={"data": {"nOrdNo": "KN-77", "ordSt": "OPN"}},
            )
        if request.url.path.endswith("/Orders/2.0/quick/order/cancel"):
            return httpx.Response(
                200,
                json={"data": {"nOrdNo": "KN-77", "ordSt": "CAN"}},
            )
        return httpx.Response(404)

    _install_mock(adapter, _login_success_handler(endpoint))

    modified = await adapter.modify_order("KN-77", quantity=5, price=1000)
    assert modified.status == BrokerOrderStatus.OPEN

    cancelled = await adapter.cancel_order("KN-77")
    assert cancelled.status == BrokerOrderStatus.CANCELLED
    await adapter.stop()


@pytest.mark.asyncio
async def test_kotak_list_orders_and_positions():
    adapter = KotakNeoBrokerAdapter(VALID_CREDS)

    def endpoint(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Orders/2.0/quick/user/orders"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"nOrdNo": "K1", "ordSt": "OPN", "fldQty": 0, "ig": "loc-1"},
                        {
                            "nOrdNo": "K2",
                            "ordSt": "TRAD",
                            "fldQty": 5,
                            "avgPrc": 250.5,
                            "ig": "loc-2",
                        },
                    ]
                },
            )
        if request.url.path.endswith("/Portfolio/1.0/portfolio/short/positions"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "trdSym": "INFY-EQ",
                            "exSeg": "nse_cm",
                            "prod": "MIS",
                            "flQty": 10,
                            "avgnetprc": 1450.0,
                            "urlPnl": 50.0,
                            "rlPnl": 0,
                            "ltp": 1455.0,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    _install_mock(adapter, _login_success_handler(endpoint))

    orders = await adapter.list_orders()
    assert [o.broker_order_id for o in orders] == ["K1", "K2"]
    assert orders[1].status == BrokerOrderStatus.FILLED
    assert orders[1].average_fill_price == 250.5
    assert orders[1].client_order_id == "loc-2"

    positions = await adapter.list_positions()
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "INFY-EQ"
    assert p.product == "mis"
    assert p.net_quantity == 10
    assert p.average_price == 1450.0
    assert p.unrealized_pnl == 50.0
    assert p.last_price == 1455.0
    await adapter.stop()


@pytest.mark.asyncio
async def test_kotak_401_triggers_re_auth_and_retry():
    """On a stale session, /place returns 401 → adapter re-runs auth and retries."""
    adapter = KotakNeoBrokerAdapter(VALID_CREDS)
    counter = {"place": 0, "oauth": 0, "login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/oauth2/token"):
            counter["oauth"] += 1
            return httpx.Response(200, json={"access_token": f"view-{counter['oauth']}"})
        if p.endswith("/login/1.0/login/v6/validate"):
            counter["login"] += 1
            return httpx.Response(
                200,
                json={"data": {"token": f"sess-{counter['login']}", "sid": "SID"}},
            )
        if p.endswith("/Orders/2.0/quick/order/rule/ms/place"):
            counter["place"] += 1
            if counter["place"] == 1:
                return httpx.Response(401, json={"message": "session expired"})
            return httpx.Response(200, json={"data": {"nOrdNo": "K-100", "stat": "Ok"}})
        return httpx.Response(404)

    _install_mock(adapter, handler)

    result = await adapter.place_order(
        BrokerOrderRequest(
            client_order_id="c-1",
            symbol="INFY-EQ",
            exchange="NSE",
            side="buy",
            order_type="market",
            product="mis",
            quantity=1,
        )
    )
    assert result.broker_order_id == "K-100"
    assert counter["place"] == 2  # one 401 then success
    # Login was re-run after the 401. OAuth exchange is skipped because the
    # caller provided a seed access_token (the production expectation).
    assert counter["login"] >= 2
    await adapter.stop()


@pytest.mark.asyncio
async def test_kotak_get_funds_error_bubbles():
    adapter = KotakNeoBrokerAdapter(VALID_CREDS)

    def endpoint(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Orders/2.0/quick/user/limits"):
            return httpx.Response(400, json={"message": "bad-request", "code": "E-100"})
        return httpx.Response(404)

    _install_mock(adapter, _login_success_handler(endpoint))
    with pytest.raises(BrokerError) as excinfo:
        await adapter.get_funds()
    assert "bad-request" in excinfo.value.message
    assert excinfo.value.details["http_status"] == 400
    await adapter.stop()


@pytest.mark.asyncio
async def test_kotak_pregenerated_session_token_skips_login():
    """When caller provides sid + session_token, adapter must not call login endpoints."""
    creds = {**VALID_CREDS, "sid": "PRE-SID", "session_token": "PRE-TOKEN"}
    adapter = KotakNeoBrokerAdapter(creds)

    login_called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/oauth2/token") or p.endswith("/login/1.0/login/v6/validate"):
            login_called["n"] += 1
            return httpx.Response(500)
        if p.endswith("/Orders/2.0/quick/user/limits"):
            assert request.headers["sid"] == "PRE-SID"
            assert request.headers["auth"] == "PRE-TOKEN"
            return httpx.Response(200, json={"data": {"Net": 1000}})
        return httpx.Response(404)

    _install_mock(adapter, handler)
    funds = await adapter.get_funds()
    assert funds.available == 1000
    assert login_called["n"] == 0
    await adapter.stop()
