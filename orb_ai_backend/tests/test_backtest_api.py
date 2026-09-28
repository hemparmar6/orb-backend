"""API integration tests for the backtest endpoints."""
from __future__ import annotations

import csv
import io
import json

import pytest


REG = {
    "email": "backtest@example.com",
    "password": "very-secret-1",
    "full_name": "Backtest User",
}


async def _login(client) -> str:
    await client.post("/api/v1/auth/register", json=REG)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": REG["email"], "password": REG["password"]},
    )
    return login.json()["access_token"]


def _base_payload() -> dict:
    return {
        "strategy_name": "orb",
        "symbols": ["NIFTY"],
        "start_date": "2026-06-01T00:00:00+00:00",
        "end_date": "2026-06-05T00:00:00+00:00",
        "initial_capital": 100_000,
        "params": {
            "opening_range_minutes": 15,
            "session_start": "09:15",
            "session_end": "15:15",
            "stop_loss_pct": 0.5,
            "target_pct": 1.5,
            "enable_long": True,
            "enable_short": True,
            "quantity": 5,
            "max_trades_per_day": 3,
        },
    }


# ---- Requires auth --------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_run_requires_auth(client):
    r = await client.post("/api/v1/backtest/run", json=_base_payload())
    assert r.status_code == 401


# ---- Happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_backtest_run_end_to_end(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    r = await client.post("/api/v1/backtest/run", headers=hdr, json=_base_payload())
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["status"] == "complete"
    assert body["strategy_name"] == "orb"
    assert body["symbols"] == ["NIFTY"]

    summary = body["summary"]
    for key in (
        "total_return_pct", "net_profit", "gross_profit", "gross_loss",
        "win_rate_pct", "avg_profit", "avg_loss", "profit_factor",
        "max_drawdown", "max_drawdown_pct", "sharpe_ratio",
        "number_of_trades", "long_stats", "short_stats",
        "monthly_performance", "final_equity", "initial_capital",
    ):
        assert key in summary, f"missing metric: {key}"

    # Some trades should have been generated on the synthetic dataset.
    assert body["summary"]["number_of_trades"] >= 1
    assert len(body["trades"]) == body["summary"]["number_of_trades"]
    assert isinstance(body["equity_curve"], list)
    assert body["initial_capital"] == 100_000.0
    return body["id"]


@pytest.mark.asyncio
async def test_backtest_get_and_results(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}

    r = await client.post("/api/v1/backtest/run", headers=hdr, json=_base_payload())
    run_id = r.json()["id"]

    # /backtest/{id}
    full = await client.get(f"/api/v1/backtest/{run_id}", headers=hdr)
    assert full.status_code == 200
    assert full.json()["id"] == run_id

    # /backtest/{id}/results
    res = await client.get(f"/api/v1/backtest/{run_id}/results", headers=hdr)
    assert res.status_code == 200
    payload = res.json()
    assert payload["id"] == run_id
    assert "summary" in payload and "trades" in payload and "equity_curve" in payload


@pytest.mark.asyncio
async def test_backtest_history_pagination(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}
    # Run two backtests.
    await client.post("/api/v1/backtest/run", headers=hdr, json=_base_payload())
    await client.post("/api/v1/backtest/run", headers=hdr, json=_base_payload())

    hist = await client.get("/api/v1/backtest/history", headers=hdr)
    assert hist.status_code == 200
    body = hist.json()
    assert body["total"] >= 2
    assert len(body["items"]) >= 2
    # Most-recent first.
    ts_a = body["items"][0]["created_at"]
    ts_b = body["items"][1]["created_at"]
    assert ts_a >= ts_b


@pytest.mark.asyncio
async def test_backtest_export_json_and_csv(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}
    r = await client.post("/api/v1/backtest/run", headers=hdr, json=_base_payload())
    run_id = r.json()["id"]

    # ---- JSON export ----
    j = await client.get(
        f"/api/v1/backtest/{run_id}/export",
        headers=hdr, params={"format": "json"},
    )
    assert j.status_code == 200
    assert "application/json" in j.headers["content-type"]
    assert "attachment" in j.headers["content-disposition"]
    j_body = json.loads(j.text)
    assert j_body["id"] == run_id
    assert "trades" in j_body and "equity_curve" in j_body and "summary" in j_body

    # ---- CSV export ----
    csv_r = await client.get(
        f"/api/v1/backtest/{run_id}/export",
        headers=hdr, params={"format": "csv"},
    )
    assert csv_r.status_code == 200
    assert "text/csv" in csv_r.headers["content-type"]
    lines = list(csv.reader(io.StringIO(csv_r.text)))
    assert lines[0] == [
        "symbol", "side", "entry_time", "entry_price",
        "exit_time", "exit_price", "quantity", "pnl", "fees", "exit_reason",
    ]
    # 1 header row + at least 1 data row if trades exist
    if r.json()["summary"]["number_of_trades"] > 0:
        assert len(lines) >= 2


# ---- Validation & auth cross-user isolation -------------------------------


@pytest.mark.asyncio
async def test_backtest_run_validation_rejects_bad_dates(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}
    payload = _base_payload()
    payload["end_date"] = payload["start_date"]  # invalid
    r = await client.post("/api/v1/backtest/run", headers=hdr, json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_backtest_run_unknown_strategy_returns_404(client):
    token = await _login(client)
    hdr = {"Authorization": f"Bearer {token}"}
    payload = _base_payload()
    payload["strategy_name"] = "not_a_real_strategy"
    r = await client.post("/api/v1/backtest/run", headers=hdr, json=payload)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "strategy_not_registered"


@pytest.mark.asyncio
async def test_backtest_cross_user_isolation(client):
    # User A creates a backtest.
    token_a = await _login(client)
    hdr_a = {"Authorization": f"Bearer {token_a}"}
    r = await client.post("/api/v1/backtest/run", headers=hdr_a, json=_base_payload())
    run_id = r.json()["id"]

    # User B tries to read it.
    reg_b = {"email": "hacker@example.com", "password": "very-secret-1", "full_name": "B"}
    await client.post("/api/v1/auth/register", json=reg_b)
    login_b = await client.post(
        "/api/v1/auth/login",
        json={"email": reg_b["email"], "password": reg_b["password"]},
    )
    hdr_b = {"Authorization": f"Bearer {login_b.json()['access_token']}"}
    forbidden = await client.get(f"/api/v1/backtest/{run_id}", headers=hdr_b)
    assert forbidden.status_code == 403

    # And user B's history is empty.
    hist_b = await client.get("/api/v1/backtest/history", headers=hdr_b)
    assert hist_b.json()["total"] == 0
