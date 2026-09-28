"""Broker registry + adapter smoke tests.

Adapter *behaviour* tests (HTTP-mocked) live in
``test_broker_dhan.py`` and ``test_broker_kotak_neo.py``.
"""
from __future__ import annotations

import pytest

from app.brokers.base import BrokerOrderRequest, BrokerOrderStatus
from app.brokers.dhan import DhanBrokerAdapter
from app.brokers.kotak_neo import KotakNeoBrokerAdapter
from app.brokers.mock_live import MockLiveBroker
from app.brokers.registry import (
    get_broker_adapter,
    get_broker_adapter_class,
    list_brokers,
)
from app.core.exceptions import (
    BrokerCredentialsInvalidError,
    UnsupportedBrokerError,
)


def test_registry_lists_all_three_adapters():
    assert set(list_brokers()) >= {"mock_live", "dhan", "kotak_neo"}


def test_registry_returns_correct_class():
    assert get_broker_adapter_class("mock_live") is MockLiveBroker
    assert get_broker_adapter_class("dhan") is DhanBrokerAdapter
    assert get_broker_adapter_class("kotak_neo") is KotakNeoBrokerAdapter


def test_registry_unknown_broker_raises():
    with pytest.raises(UnsupportedBrokerError):
        get_broker_adapter_class("robinhood_pro")


def test_credentials_required_lists_are_stable():
    assert DhanBrokerAdapter.required_credentials() == ["client_id", "access_token"]
    assert set(KotakNeoBrokerAdapter.required_credentials()) == {
        "consumer_key",
        "consumer_secret",
        "mobile_number",
        "mpin",
        "access_token",
    }
    assert MockLiveBroker.required_credentials() == []


def test_credential_validation_rejects_missing():
    with pytest.raises(BrokerCredentialsInvalidError):
        get_broker_adapter("dhan", {"client_id": "abc"})  # missing access_token


def test_dhan_credential_validation_rejects_short_token():
    with pytest.raises(BrokerCredentialsInvalidError):
        get_broker_adapter("dhan", {"client_id": "abc", "access_token": "tok"})


def test_kotak_credential_validation_rejects_invalid_mobile():
    with pytest.raises(BrokerCredentialsInvalidError):
        get_broker_adapter(
            "kotak_neo",
            {
                "consumer_key": "k" * 12,
                "consumer_secret": "s" * 12,
                "mobile_number": "abc",  # no digits
                "mpin": "1234",
                "access_token": "seed-token-value",
            },
        )


def test_kotak_credential_validation_rejects_short_mpin():
    with pytest.raises(BrokerCredentialsInvalidError):
        get_broker_adapter(
            "kotak_neo",
            {
                "consumer_key": "k" * 12,
                "consumer_secret": "s" * 12,
                "mobile_number": "+919999999999",
                "mpin": "12",  # too short
                "access_token": "seed-token-value",
            },
        )


def test_dhan_adapter_constructs_with_valid_credentials():
    """The production adapter must instantiate cleanly with valid creds."""
    adapter = get_broker_adapter(
        "dhan",
        {"client_id": "1100000001", "access_token": "eyJhbGciOi.dummy.value"},
    )
    assert isinstance(adapter, DhanBrokerAdapter)
    assert adapter.broker_type == "dhan"


def test_kotak_adapter_constructs_with_valid_credentials():
    adapter = get_broker_adapter(
        "kotak_neo",
        {
            "consumer_key": "consumer-key-value",
            "consumer_secret": "consumer-secret-value",
            "mobile_number": "+919999999999",
            "mpin": "1234",
            "access_token": "seed-access-token",
        },
    )
    assert isinstance(adapter, KotakNeoBrokerAdapter)
    assert adapter.broker_type == "kotak_neo"


@pytest.mark.asyncio
async def test_mock_live_end_to_end():
    adapter = get_broker_adapter("mock_live", {})
    await adapter.start()
    assert await adapter.health_check() is True

    req = BrokerOrderRequest(
        client_order_id="local-1",
        symbol="A",
        exchange="MOCK",
        side="buy",
        order_type="market",
        product="mis",
        quantity=10,
    )
    result = await adapter.place_order(req)
    assert result.broker_order_id.startswith("MOCK-")
    assert result.client_order_id == "local-1"

    # Auto-fill schedules an event — drain it via the WS stream.
    gen = adapter.stream_order_updates()
    filled = await gen.__anext__()
    assert filled.status == BrokerOrderStatus.FILLED
    assert filled.filled_quantity == 10

    positions = await adapter.list_positions()
    assert len(positions) == 1
    assert positions[0].net_quantity == 10

    funds = await adapter.get_funds()
    assert funds.available > 0

    await adapter.stop()
