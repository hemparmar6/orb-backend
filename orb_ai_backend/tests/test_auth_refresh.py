"""Kotak sid daily refresh scheduler."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.brokers.auth_refresh import KotakSidRefreshScheduler
from app.core.exceptions import EngineError


FULL_CREDENTIALS = {
    "consumer_key": "CK",
    "consumer_secret": "CS",
    "mobile_number": "+919999999999",
    "mpin": "1234",
}


def _happy_handler(state: dict[str, Any]):
    """Build an httpx handler that returns valid oauth + login responses.

    ``state`` tracks how many times each endpoint is hit so tests can assert.
    """
    state.setdefault("oauth", 0)
    state.setdefault("login", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            state["oauth"] += 1
            return httpx.Response(
                200, json={"access_token": f"VIEW{state['oauth']}"}
            )
        if request.url.path.endswith("/login/v6/validate"):
            state["login"] += 1
            return httpx.Response(
                200,
                json={
                    "data": {
                        "token": f"SESS{state['login']}",
                        "sid": f"SID{state['login']}",
                    }
                },
            )
        return httpx.Response(404)

    return handler


# --------------------------------------------------------------------- basics


def test_missing_credentials_rejected():
    with pytest.raises(ValueError):
        KotakSidRefreshScheduler(credentials={})
    with pytest.raises(ValueError):
        KotakSidRefreshScheduler(credentials={"consumer_key": "x"})


def test_invalid_time_rejected():
    with pytest.raises(ValueError):
        KotakSidRefreshScheduler(
            credentials=FULL_CREDENTIALS, refresh_hour_local=25
        )
    with pytest.raises(ValueError):
        KotakSidRefreshScheduler(
            credentials=FULL_CREDENTIALS, refresh_minute_local=99
        )


def test_next_refresh_computes_future_utc():
    tz = "Asia/Kolkata"
    sched = KotakSidRefreshScheduler(
        credentials=FULL_CREDENTIALS,
        refresh_hour_local=8,
        refresh_minute_local=0,
        timezone=tz,
    )
    # Feed a fixed "now" — mid-day IST, so next refresh is tomorrow 08:00 IST.
    now_local = datetime(2024, 6, 15, 12, 0, tzinfo=ZoneInfo(tz))
    nxt = sched._next_refresh_utc(now_local)
    # 08:00 IST tomorrow = 02:30 UTC tomorrow.
    assert nxt.tzinfo == timezone.utc
    assert nxt.date() == (now_local + timedelta(days=1)).astimezone(timezone.utc).date()
    assert nxt.hour == 2
    assert nxt.minute == 30


def test_next_refresh_today_when_before_time():
    tz = "Asia/Kolkata"
    sched = KotakSidRefreshScheduler(
        credentials=FULL_CREDENTIALS,
        refresh_hour_local=8,
        refresh_minute_local=0,
        timezone=tz,
    )
    now_local = datetime(2024, 6, 15, 5, 0, tzinfo=ZoneInfo(tz))
    nxt = sched._next_refresh_utc(now_local)
    # 08:00 IST TODAY = 02:30 UTC — same calendar date in IST (2024-06-15).
    assert nxt.astimezone(ZoneInfo(tz)).date() == now_local.date()
    assert (nxt.hour, nxt.minute) == (2, 30)


# ------------------------------------------------------------ refresh_now


@pytest.mark.asyncio
async def test_refresh_now_populates_current_and_calls_on_refresh():
    state: dict[str, Any] = {}
    client = httpx.AsyncClient(transport=httpx.MockTransport(_happy_handler(state)))

    received: list[dict[str, str]] = []
    sched = KotakSidRefreshScheduler(
        credentials=FULL_CREDENTIALS,
        http_client=client,
        on_refresh=lambda c: received.append(c),
    )
    new = await sched.refresh_now()
    assert new["sid"] == "SID1"
    assert new["session_token"] == "SESS1"
    assert new["view_token"] == "VIEW1"
    assert sched.current() == new
    assert len(received) == 1
    stats = sched.get_stats()
    assert stats["refresh_count"] == 1
    assert stats["consecutive_failures"] == 0
    assert stats["has_sid"] is True


@pytest.mark.asyncio
async def test_refresh_now_records_failure_and_reraises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sched = KotakSidRefreshScheduler(
        credentials=FULL_CREDENTIALS, http_client=client
    )
    with pytest.raises(EngineError):
        await sched.refresh_now()
    stats = sched.get_stats()
    assert stats["consecutive_failures"] == 1
    assert stats["refresh_count"] == 0
    assert stats["last_error"]


# ----------------------------------------------------- start / stop lifecycle


@pytest.mark.asyncio
async def test_start_performs_initial_refresh_and_task_running():
    state: dict[str, Any] = {}
    client = httpx.AsyncClient(transport=httpx.MockTransport(_happy_handler(state)))
    sched = KotakSidRefreshScheduler(
        credentials=FULL_CREDENTIALS, http_client=client
    )
    await sched.start()
    assert sched.get_stats()["is_running"] is True
    assert sched.current()["sid"] == "SID1"
    await sched.stop()
    assert sched.get_stats()["is_running"] is False
    assert state["oauth"] == 1  # exactly one initial refresh, no more


@pytest.mark.asyncio
async def test_start_without_immediate_refresh_defers():
    state: dict[str, Any] = {}
    client = httpx.AsyncClient(transport=httpx.MockTransport(_happy_handler(state)))
    sched = KotakSidRefreshScheduler(
        credentials=FULL_CREDENTIALS, http_client=client
    )
    await sched.start(refresh_immediately=False)
    assert sched.current() == {}
    await sched.stop()
    assert state.get("oauth", 0) == 0
