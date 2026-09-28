#!/usr/bin/env python3
"""Upstox V3 staging live smoke test — REAL connection (no mocks).

Reads the token ONLY from the environment (never printed/committed) and runs a
short real session against Upstox V3: authorize -> connect -> subscribe
NIFTY/BANKNIFTY -> receive/decoded ticks -> (optional) reconnect -> clean stop.

Usage (staging only; requires NSE market hours for live ticks)::

    export UPSTOX_ACCESS_TOKEN=<staging daily token>
    python -m scripts.upstox_staging_smoke --ticks 10 --seconds 30

Exit code 0 only if at least one NIFTY tick AND one BANKNIFTY tick were decoded.
Does NOT touch production, credentials, trading, or Railway.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone

from app.engine.market_data import get_provider


async def _run(symbols: list[str], target_ticks: int, seconds: float) -> int:
    token = (os.environ.get("UPSTOX_ACCESS_TOKEN") or "").strip()
    if not token:
        print("ERROR: UPSTOX_ACCESS_TOKEN is not set in the environment.")
        return 2

    provider = get_provider(
        "upstox",
        credentials={"access_token": token},
        mode="full",
        backoff_base_s=1.0,
        backoff_max_s=10.0,
    )
    await provider.subscribe(symbols)

    # Optional initial snapshot via V3 LTP REST (recovery/priming).
    try:
        snaps = await provider.prime_snapshots(symbols)
        for sym, q in snaps.items():
            print(f"[snapshot] {sym} ltp={q.price} vol={q.volume}")
    except Exception as exc:  # noqa: BLE001
        print(f"[snapshot] unavailable: {type(exc).__name__}")

    await provider.start()
    print(f"[status] {provider.status.value}")

    seen: dict[str, int] = {s: 0 for s in symbols}
    total = 0

    async def _consume() -> None:
        nonlocal total
        async for q in provider.stream():
            total += 1
            seen[q.symbol] = seen.get(q.symbol, 0) + 1
            ohlc = provider.latest_ohlc(q.symbol)
            day = ohlc.get("1d") or ohlc.get("I1")
            ohlc_s = (
                f" ohlc(1d) O={day.open} H={day.high} L={day.low} C={day.close}"
                if day else ""
            )
            print(
                f"[tick] provider=upstox {q.symbol} ltp={q.price} vol={q.volume} "
                f"ts={q.ts.isoformat()}{ohlc_s}"
            )
            if all(seen.get(s, 0) > 0 for s in symbols) and total >= target_ticks:
                return

    try:
        await asyncio.wait_for(_consume(), timeout=seconds)
    except asyncio.TimeoutError:
        print(f"[timeout] stopped after {seconds}s")
    finally:
        stats = provider.get_stats()
        print(f"[stats] {stats}")
        await provider.stop()
        print(f"[status] {provider.status.value} (after clean stop)")

    ok = all(seen.get(s, 0) > 0 for s in symbols)
    print(f"[result] {'PASS' if ok else 'FAIL'} — ticks per symbol: {seen} "
          f"(now={datetime.now(timezone.utc).isoformat()})")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NIFTY,BANKNIFTY")
    ap.add_argument("--ticks", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=30.0)
    args = ap.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    sys.exit(asyncio.run(_run(symbols, args.ticks, args.seconds)))


if __name__ == "__main__":
    main()
