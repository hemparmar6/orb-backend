"""LIVE trading must never use mock market data — fail-closed guard.

These tests lock in the safety invariant introduced in the market-data
resolver (``app.engine.market_data.registry.resolve_provider`` /
``resolve_live_provider``):

- LIVE + mock provider          -> rejected
- LIVE + missing/invalid provider -> rejected (fail closed)
- LIVE + valid real provider     -> allowed (returns the real provider)
- PAPER + mock provider          -> allowed (unchanged behaviour)

The resolver is the single decision point used by ``StrategyManager.start``
for both paper and live sessions, so exercising it directly proves the
production behaviour without opening real broker WebSockets.
"""
from __future__ import annotations

import pytest

# Importing the package triggers @register_provider for dhan / kotak_neo / mock.
import app.engine.market_data  # noqa: F401
from app.core.exceptions import LiveMarketDataError
from app.engine.market_data.mock import MockMarketDataProvider
from app.engine.market_data.dhan import DhanMarketDataProvider
from app.engine.market_data.registry import (
    is_mock_provider_name,
    resolve_live_provider,
    resolve_provider,
)


VALID_DHAN_CREDS = {"client_id": "1100123456", "access_token": "valid-token"}


# ---- LIVE + mock provider -> rejected ------------------------------------


def test_live_with_explicit_mock_provider_is_rejected():
    """Asking for the mock provider on a real broker in live mode is refused."""
    with pytest.raises(LiveMarketDataError) as exc:
        resolve_provider(
            execution_mode="live",
            provider_name="mock",
            broker_type="dhan",
            credentials=VALID_DHAN_CREDS,
        )
    assert exc.value.code == "mock_market_data_forbidden_in_live"


def test_live_never_returns_a_mock_instance():
    """Even the low-level live resolver never yields a mock provider."""
    with pytest.raises(LiveMarketDataError):
        resolve_live_provider(broker_type="dhan", requested_provider="MOCK",
                              credentials=VALID_DHAN_CREDS)


# ---- LIVE + missing provider -> rejected (fail closed) -------------------


def test_live_with_missing_broker_provider_is_rejected():
    """A broker with no configured real market-data provider fails closed."""
    with pytest.raises(LiveMarketDataError) as exc:
        resolve_provider(
            execution_mode="live",
            provider_name=None,
            broker_type=None,
            credentials=None,
        )
    assert exc.value.code == "live_market_data_provider_unavailable"


def test_live_with_missing_credentials_is_rejected():
    """A real provider without credentials cannot authenticate -> fail closed."""
    with pytest.raises(LiveMarketDataError) as exc:
        resolve_provider(
            execution_mode="live",
            provider_name=None,
            broker_type="dhan",
            credentials=None,
        )
    assert exc.value.code == "live_market_data_provider_unavailable"


def test_live_with_invalid_credentials_is_rejected():
    """Incomplete Dhan credentials (missing access_token) fail closed."""
    with pytest.raises(LiveMarketDataError):
        resolve_provider(
            execution_mode="live",
            provider_name=None,
            broker_type="dhan",
            credentials={"client_id": "1100123456"},  # no access_token
        )


def test_live_with_unknown_broker_is_rejected():
    with pytest.raises(LiveMarketDataError) as exc:
        resolve_provider(
            execution_mode="live",
            provider_name=None,
            broker_type="some_unregistered_broker",
            credentials={"anything": "x"},
        )
    assert exc.value.code == "live_market_data_provider_unavailable"


# ---- LIVE + valid real provider -> allowed -------------------------------


def test_live_with_valid_dhan_provider_is_allowed():
    provider = resolve_provider(
        execution_mode="live",
        provider_name=None,
        broker_type="dhan",
        credentials=VALID_DHAN_CREDS,
    )
    assert isinstance(provider, DhanMarketDataProvider)
    assert provider.name == "dhan"
    assert not is_mock_provider_name(provider.name)


def test_live_with_valid_kotak_provider_is_allowed():
    provider = resolve_provider(
        execution_mode="live",
        provider_name=None,
        broker_type="kotak_neo",
        credentials={"sid": "s-123", "session_token": "t-123"},
    )
    assert provider.name == "kotak_neo"
    assert not is_mock_provider_name(provider.name)


# ---- PAPER + mock provider -> allowed (unchanged) ------------------------


def test_paper_with_mock_provider_is_allowed():
    provider = resolve_provider(execution_mode="paper", provider_name="mock")
    assert isinstance(provider, MockMarketDataProvider)
    assert provider.name == "mock"


def test_paper_defaults_to_mock_provider():
    provider = resolve_provider(execution_mode="paper", provider_name=None)
    assert isinstance(provider, MockMarketDataProvider)


def test_demo_mode_still_allows_mock():
    """Any non-live mode keeps the existing mock behaviour."""
    provider = resolve_provider(execution_mode="demo", provider_name=None)
    assert isinstance(provider, MockMarketDataProvider)


# ---- Sanctioned simulation broker (mock_live) still works ----------------


def test_mock_live_broker_is_allowed_to_use_mock():
    """``mock_live`` is a no-real-money simulation broker; the existing
    end-to-end live test relies on it using mock market data."""
    provider = resolve_live_provider(broker_type="mock_live", requested_provider=None)
    assert isinstance(provider, MockMarketDataProvider)
