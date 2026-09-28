"""Provider registry — lets Module 3 plug in Dhan/Kotak without touching the engine.

Register providers with the decorator:

    @register_provider("dhan")
    class DhanMarketData(MarketDataProvider): ...

Then obtain them by name (name comes from ``settings.MARKET_DATA_PROVIDER`` in
production; tests pass their own instance).
"""
from __future__ import annotations

from typing import Callable, Type

from app.core.exceptions import EngineError
from app.engine.market_data.base import MarketDataProvider

_registry: dict[str, Type[MarketDataProvider]] = {}


def register_provider(name: str) -> Callable[[Type[MarketDataProvider]], Type[MarketDataProvider]]:
    def _decorator(cls: Type[MarketDataProvider]) -> Type[MarketDataProvider]:
        _registry[name.lower()] = cls
        cls.name = name
        return cls
    return _decorator


def get_provider(name: str, **kwargs) -> MarketDataProvider:
    key = name.lower()
    if key not in _registry:
        raise EngineError(
            f"Market data provider '{name}' is not registered",
            code="unknown_market_data_provider",
        )
    return _registry[key](**kwargs)


# ---- Live-mode provider policy -------------------------------------------
#
# SAFETY INVARIANT: a strategy running with execution_mode="live" must NEVER
# use the mock market-data provider. Live sessions must explicitly resolve a
# real, configured provider (Dhan / Kotak Neo). If that cannot be done, we
# FAIL CLOSED and refuse to start the session.

MOCK_PROVIDER_NAME = "mock"

# Maps a real broker account type -> its matching real-time market-data
# provider. ``mock_live`` is deliberately absent: it is a sanctioned
# simulation broker (no real money) and is handled separately.
_LIVE_PROVIDER_FOR_BROKER: dict[str, str] = {
    "dhan": "dhan",
    "kotak_neo": "kotak_neo",
}


def is_mock_provider_name(name: str | None) -> bool:
    return (name or "").strip().lower() == MOCK_PROVIDER_NAME


def resolve_live_provider(
    *,
    broker_type: str | None,
    requested_provider: str | None = None,
    credentials: dict | None = None,
    provider_kwargs: dict | None = None,
) -> MarketDataProvider:
    """Resolve a market-data provider for a LIVE session — fail closed.

    Rules:
    - An explicit request for the mock provider is rejected for real brokers.
    - ``mock_live`` is a sanctioned simulation broker (no real money) and may
      use the mock provider.
    - Real brokers (Dhan / Kotak Neo) must resolve to their matching real
      provider, built with the broker's credentials. If the provider is not
      registered, or credentials are missing/invalid/unauthenticated, we raise
      ``LiveMarketDataError`` and do NOT start the strategy.
    """
    from app.core.exceptions import LiveMarketDataError

    bt = (broker_type or "").strip().lower()
    kwargs = dict(provider_kwargs or {})

    # Never allow an explicit mock request for real live trading.
    if is_mock_provider_name(requested_provider) and bt != "mock_live":
        raise LiveMarketDataError(
            "Live execution cannot use the mock market-data provider",
            code="mock_market_data_forbidden_in_live",
        )

    # Sanctioned simulation broker: mock market data is allowed.
    if bt == "mock_live":
        return get_provider(MOCK_PROVIDER_NAME, **kwargs)

    # Real broker → must resolve its matching real-time provider.
    provider_name = _LIVE_PROVIDER_FOR_BROKER.get(bt)
    if not provider_name:
        raise LiveMarketDataError(
            "No real market-data provider is configured for live execution "
            f"(broker_type={broker_type!r})",
            code="live_market_data_provider_unavailable",
        )

    if credentials:
        kwargs.setdefault("credentials", credentials)

    try:
        provider = get_provider(provider_name, **kwargs)
    except (EngineError, ValueError, KeyError, TypeError) as exc:
        # Unknown provider, missing/invalid credentials, unauthenticated, etc.
        raise LiveMarketDataError(
            f"Live market-data provider '{provider_name}' is unavailable or "
            f"unauthenticated: {exc}",
            code="live_market_data_provider_unavailable",
        ) from exc

    # Defense in depth: never let a mock instance slip through in live mode.
    if is_mock_provider_name(getattr(provider, "name", "")):
        raise LiveMarketDataError(
            "Resolved provider is mock; refusing to start live execution",
            code="mock_market_data_forbidden_in_live",
        )
    return provider


def resolve_provider(
    *,
    execution_mode: str,
    provider_name: str | None = None,
    broker_type: str | None = None,
    credentials: dict | None = None,
    provider_kwargs: dict | None = None,
) -> MarketDataProvider:
    """Resolve the market-data provider for a session based on execution mode.

    - ``live`` → delegates to :func:`resolve_live_provider` (fail closed; no
      mock fallback).
    - ``paper`` / ``demo`` / any testing mode → keeps the existing behaviour of
      defaulting to the mock provider.
    """
    mode = (execution_mode or "paper").strip().lower()
    if mode == "live":
        return resolve_live_provider(
            broker_type=broker_type,
            requested_provider=provider_name,
            credentials=credentials,
            provider_kwargs=provider_kwargs,
        )
    return get_provider(provider_name or MOCK_PROVIDER_NAME, **(provider_kwargs or {}))


# ---- Ship the built-in mock as the default registered provider ---------
from app.engine.market_data.mock import MockMarketDataProvider  # noqa: E402

_registry["mock"] = MockMarketDataProvider
MockMarketDataProvider.name = "mock"
