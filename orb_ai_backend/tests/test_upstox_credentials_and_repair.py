"""Regression tests for the ORB backend-repair (Upstox credential + safety).

Covers the Phase 4/5/6/7/9/10 requirements of the backend-repair task:

* Clean repository structure + Railway Docker configuration.
* Nifty 50 / Nifty Bank + display-name variant resolution.
* UPSTOX_ACCESS_TOKEN precedence over UPSTOX_ANALYTICS_TOKEN.
* UPSTOX_ANALYTICS_TOKEN read-only fallback.
* Missing credential fails closed.
* Analytics token -> market data ALLOWED; -> order execution REJECTED.
* Credentials never logged / never returned by the API.
* No mock fallback when MARKET_DATA_PROVIDER=upstox (safe 503 instead).
* All 14 canonical timeframes preserved via the single centralized mapping.

Only DUMMY tokens are used. No real credential, no network access.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.exceptions import EngineError
from app.engine.market_data import upstox_credentials as uc
from app.engine.market_data.upstox_instruments import resolve_instrument_key

DUMMY_ACCESS = "DUMMY-ACCESS-TOKEN-abc123"
DUMMY_ANALYTICS = "DUMMY-ANALYTICS-TOKEN-xyz789"

NIFTY_KEY = "NSE_INDEX|Nifty 50"
BANKNIFTY_KEY = "NSE_INDEX|Nifty Bank"

_REPO_ROOT = Path(__file__).resolve().parents[2]      # …/orb-ai-backend-v2
_BACKEND_DIR = Path(__file__).resolve().parents[1]    # …/orb_ai_backend


@pytest.fixture(autouse=True)
def _clear_upstox_env(monkeypatch):
    """Every test starts with no Upstox tokens and provider=mock unless set."""
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", None, raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", None, raising=False)
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "mock", raising=False)
    yield


# --------------------------------------------------------------------------- #
# Phase 10 #1/#2 — clean repository structure + Railway Docker configuration
# --------------------------------------------------------------------------- #
def test_repo_root_layout():
    assert (_REPO_ROOT / "orb_ai_backend").is_dir()
    assert (_REPO_ROOT / "admin-web").is_dir()
    assert (_REPO_ROOT / "railway.json").is_file()
    assert (_BACKEND_DIR / "Dockerfile").is_file()
    # The outer Emergent template must NOT be at the deployed repo root.
    assert not (_REPO_ROOT / "backend" / "server.py").exists()
    assert not (_REPO_ROOT / ".emergent").exists()


def test_railway_json_uses_dockerfile_builder():
    cfg = json.loads((_REPO_ROOT / "railway.json").read_text())
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert cfg["build"]["dockerfilePath"] == "orb_ai_backend/Dockerfile"
    assert cfg["deploy"]["healthcheckPath"] == "/api/v1/health"


# --------------------------------------------------------------------------- #
# Phase 3 — instrument resolution (Nifty 50 / Nifty Bank + variants)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("Nifty 50", NIFTY_KEY),
        ("NIFTY", NIFTY_KEY),
        ("NIFTY50", NIFTY_KEY),
        ("nifty 50", NIFTY_KEY),
        ("  Nifty   50 ", NIFTY_KEY),
        ("Nifty Bank", BANKNIFTY_KEY),
        ("BANKNIFTY", BANKNIFTY_KEY),
        ("NIFTYBANK", BANKNIFTY_KEY),
        ("nifty bank", BANKNIFTY_KEY),
    ],
)
def test_display_name_variants_resolve(symbol, expected):
    assert resolve_instrument_key(symbol) == expected


def test_unknown_symbol_not_guessed():
    assert resolve_instrument_key("TOTALLY_UNKNOWN") is None


# --------------------------------------------------------------------------- #
# Phase 4 — token precedence + analytics fallback + fail closed
# --------------------------------------------------------------------------- #
def test_access_token_takes_precedence(monkeypatch):
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", DUMMY_ACCESS, raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", DUMMY_ANALYTICS, raising=False)
    assert uc.resolve_market_data_token() == DUMMY_ACCESS
    assert uc.market_data_token_source() == uc.SOURCE_ACCESS_TOKEN
    assert settings.upstox_market_data_token == DUMMY_ACCESS
    assert settings.upstox_market_data_token_source == "access_token"


def test_analytics_token_read_only_fallback(monkeypatch):
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", DUMMY_ANALYTICS, raising=False)
    assert uc.has_market_data_token() is True
    assert uc.resolve_market_data_token() == DUMMY_ANALYTICS
    assert uc.market_data_token_source() == uc.SOURCE_ANALYTICS_TOKEN
    assert uc.market_data_credentials() == {"access_token": DUMMY_ANALYTICS}
    assert settings.upstox_market_data_token_source == "analytics_token"


def test_missing_credential_fails_closed():
    assert uc.has_market_data_token() is False
    assert uc.market_data_token_source() is None
    assert settings.upstox_market_data_token is None
    with pytest.raises(EngineError) as exc:
        uc.resolve_market_data_token()
    assert exc.value.code == "upstox_market_data_credential_missing"


# --------------------------------------------------------------------------- #
# Phase 5 — HARD SAFETY GUARD (analytics token never for order execution)
# --------------------------------------------------------------------------- #
def test_analytics_token_market_data_allowed(monkeypatch):
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", DUMMY_ANALYTICS, raising=False)
    # Read-only market-data credential resolves fine.
    assert uc.market_data_credentials()["access_token"] == DUMMY_ANALYTICS


def test_analytics_token_order_execution_rejected(monkeypatch):
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", DUMMY_ANALYTICS, raising=False)
    with pytest.raises(EngineError) as exc:
        uc.resolve_order_execution_token()
    assert exc.value.code == "upstox_analytics_token_forbidden_for_execution"
    # The explicit guard also rejects the analytics token on write paths.
    with pytest.raises(EngineError):
        uc.assert_not_analytics_for_execution(DUMMY_ANALYTICS)


def test_access_token_order_execution_allowed(monkeypatch):
    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", DUMMY_ACCESS, raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", DUMMY_ANALYTICS, raising=False)
    assert uc.resolve_order_execution_token() == DUMMY_ACCESS
    # Access token must pass the guard even when an analytics token is also set.
    uc.assert_not_analytics_for_execution(DUMMY_ACCESS)


def test_order_execution_without_any_token_fails_closed():
    with pytest.raises(EngineError) as exc:
        uc.resolve_order_execution_token()
    assert exc.value.code == "upstox_access_token_missing"


# --------------------------------------------------------------------------- #
# Phase 5/11 — credential never logged, never in mask output
# --------------------------------------------------------------------------- #
def test_mask_redacts_both_tokens(monkeypatch):
    from app.engine.market_data import upstox as upstox_mod

    monkeypatch.setattr(settings, "UPSTOX_ACCESS_TOKEN", DUMMY_ACCESS, raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", DUMMY_ANALYTICS, raising=False)
    masked = upstox_mod._mask(f"err {DUMMY_ACCESS} and {DUMMY_ANALYTICS} leaked")
    assert DUMMY_ACCESS not in masked
    assert DUMMY_ANALYTICS not in masked
    assert "***" in masked


def test_token_source_label_is_safe_to_log(monkeypatch):
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", DUMMY_ANALYTICS, raising=False)
    label = uc.market_data_token_source()
    assert DUMMY_ANALYTICS not in str(label)
    assert label == "analytics_token"


# --------------------------------------------------------------------------- #
# Phase 9 — all 14 canonical timeframes, single centralized mapping
# --------------------------------------------------------------------------- #
FOURTEEN_TF = [
    "1m", "2m", "3m", "5m", "10m", "15m", "30m", "45m",
    "1H", "2H", "4H", "1D", "1W", "1M",
]


def test_all_14_timeframes_in_central_mapping():
    from app.engine.market_data.upstox_historical import _UPSTOX_TF_UNIT_INTERVAL
    assert list(_UPSTOX_TF_UNIT_INTERVAL.keys()) == FOURTEEN_TF
    # Every timeframe maps to a real Upstox (unit, interval) pair.
    for tf in FOURTEEN_TF:
        unit, interval = _UPSTOX_TF_UNIT_INTERVAL[tf]
        assert unit in {"minutes", "hours", "days", "weeks", "months"}
        assert interval.isdigit()


def test_endpoint_timeframes_reuse_central_mapping():
    # The endpoint imports the mapping from the fetcher (no duplicate logic).
    from app.api.v1.endpoints import market_data as md
    from app.engine.market_data.upstox_historical import _UPSTOX_TF_UNIT_INTERVAL
    assert md._UPSTOX_TF is _UPSTOX_TF_UNIT_INTERVAL
    assert md.TIMEFRAMES == FOURTEEN_TF


@pytest.mark.asyncio
async def test_timeframes_endpoint_returns_14(client, user_headers):
    r = await client.get("/api/v1/market-data/timeframes", headers=user_headers)
    assert r.status_code == 200
    assert r.json()["timeframes"] == FOURTEEN_TF


# --------------------------------------------------------------------------- #
# Phase 7 — NO mock fallback when MARKET_DATA_PROVIDER=upstox
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_upstox_no_credential_returns_503_not_mock(client, user_headers, monkeypatch):
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    r = await client.get(
        "/api/v1/market-data/candles",
        params={"symbol": "NIFTY", "timeframe": "15m", "exchange": "NSE_INDEX", "count": 10},
        headers=user_headers,
    )
    assert r.status_code == 503
    blob = json.dumps(r.json()).lower()
    # Fail closed on the upstox provider — never a mock candle payload.
    assert "upstox" in blob
    assert "market_data_provider_unavailable" in blob
    assert '"source": "mock"' not in blob and '"source":"mock"' not in blob


@pytest.mark.asyncio
async def test_upstox_unresolved_instrument_returns_503_not_mock(client, user_headers, monkeypatch):
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    monkeypatch.setattr(settings, "UPSTOX_ANALYTICS_TOKEN", DUMMY_ANALYTICS, raising=False)
    # Unknown symbol → instrument cannot be resolved → safe 503, never mock.
    r = await client.get(
        "/api/v1/market-data/candles",
        params={"symbol": "NO_SUCH_SYMBOL", "timeframe": "15m", "exchange": "NSE_INDEX", "count": 5},
        headers=user_headers,
    )
    assert r.status_code == 503
    blob = json.dumps(r.json()).lower()
    assert "upstox" in blob
    # The token value must never leak into the error response.
    assert DUMMY_ANALYTICS not in r.text


@pytest.mark.asyncio
async def test_upstox_tick_no_credential_returns_503_not_mock(client, user_headers, monkeypatch):
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "upstox", raising=False)
    r = await client.get(
        "/api/v1/market-data/tick",
        params={"symbol": "NIFTY", "exchange": "NSE_INDEX"},
        headers=user_headers,
    )
    assert r.status_code == 503
    blob = json.dumps(r.json()).lower()
    assert "upstox" in blob
    assert '"source": "mock"' not in blob and '"source":"mock"' not in blob


@pytest.mark.asyncio
async def test_mock_provider_still_returns_mock_source(client, user_headers, monkeypatch):
    # Mock is permitted ONLY when the provider is explicitly mock.
    monkeypatch.setattr(settings, "MARKET_DATA_PROVIDER", "mock", raising=False)
    r = await client.get(
        "/api/v1/market-data/candles",
        params={"symbol": "NIFTY", "timeframe": "15m", "exchange": "MOCK", "count": 10},
        headers=user_headers,
    )
    assert r.status_code == 200
    assert r.json()["source"] == "mock"
