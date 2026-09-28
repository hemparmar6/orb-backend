"""Module 9 — market intelligence helpers unit tests."""

from __future__ import annotations

from app.market_intel import gap, liquidity, regime, session_stats, trend_strength, volatility


def _bars(closes, volumes=None):
    volumes = volumes or [1000] * len(closes)
    out = []
    for c, v in zip(closes, volumes):
        out.append({
            "open": c * 0.999, "high": c * 1.001, "low": c * 0.998,
            "close": c, "volume": v,
        })
    return out


def test_regime_trending():
    closes = [100 + i * 0.5 for i in range(60)]
    assert regime.detect(_bars(closes)) == "trending"


def test_regime_quiet():
    closes = [100 + 0.0001 * i for i in range(60)]
    assert regime.detect(_bars(closes)) == "quiet"


def test_trend_strength_range():
    s = trend_strength.score(_bars([100 + i * 0.2 for i in range(30)]))
    assert 0.0 <= s <= 100.0


def test_volatility_normal():
    quiet = _bars([100 + i * 0.001 for i in range(30)])
    assert volatility.regime(quiet) in {"low", "normal"}


def test_liquidity_thin_vs_deep():
    assert liquidity.classify(_bars([100] * 30, volumes=[50] * 30)) == "thin"
    assert liquidity.classify(_bars([100] * 30, volumes=[500_000] * 30)) == "deep"


def test_gap_no_gap():
    bars = _bars([100, 100.05])
    bars[-1]["open"] = 100.05
    assert gap.behaviour(bars) == "none"


def test_session_stats_shape():
    out = session_stats.summarise(_bars([100, 101, 102]))
    assert out["last_close"] == 102
    assert "avg_range_20" in out
