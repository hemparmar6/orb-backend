"""Instrument-loader ABC + on-disk cache + background refresh scheduler.

Design goals
------------

* Every broker has its own ``InstrumentLoader`` (Dhan CSV, Kotak JSON, …).
  All loaders return an :class:`InstrumentMaster` via one async method:
  ``fetch()``.
* Loaders are pluggable via :func:`register_loader` — same pattern the
  market-data + historical registries use.
* A wrapping :class:`CachedInstrumentLoader` persists the latest master on
  disk (JSON) and reuses it while it's still fresh (TTL default 20 h).
  Deployments that restart mid-session don't re-download several MB of
  instrument data on every boot.
* A background :class:`RefreshScheduler` reloads the master at a
  configurable cadence (default: once per calendar day, aligned to a
  wall-clock hour). Failures are logged and retried; the old master keeps
  serving traffic until a new one is successfully materialised.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional, Type

from app.brokers.instruments.base import Instrument, InstrumentKind, InstrumentMaster
from app.core.exceptions import EngineError
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_CACHE_DIR = os.environ.get(
    "INSTRUMENT_CACHE_DIR", "/tmp/orb_ai_instruments"
)


# =============================================================================
# ABC + registry
# =============================================================================


class InstrumentLoader(ABC):
    """Broker-specific loader that returns a fresh :class:`InstrumentMaster`."""

    name: str = "abstract"

    @abstractmethod
    async def fetch(self) -> InstrumentMaster: ...

    async def close(self) -> None:
        """Release transient resources (HTTP clients, etc.)."""


_registry: dict[str, Type[InstrumentLoader]] = {}


def register_loader(name: str) -> Callable[[Type[InstrumentLoader]], Type[InstrumentLoader]]:
    def _decorator(cls: Type[InstrumentLoader]) -> Type[InstrumentLoader]:
        _registry[name.lower()] = cls
        cls.name = name
        return cls

    return _decorator


def get_loader(name: str, **kwargs: Any) -> InstrumentLoader:
    key = name.lower()
    if key not in _registry:
        raise EngineError(
            f"Instrument loader '{name}' is not registered",
            code="unknown_instrument_loader",
        )
    return _registry[key](**kwargs)


def list_loaders() -> list[str]:
    return sorted(_registry.keys())


# =============================================================================
# On-disk cache wrapper
# =============================================================================


class CachedInstrumentLoader(InstrumentLoader):
    """Wrap another loader and cache its output on disk.

    - ``ttl_s`` — reuse an on-disk cache younger than this. Default 20 h so a
      snapshot taken at 08:00 local is still valid at market open the next
      trading day.
    - ``cache_dir`` — where to persist. Defaults to ``$INSTRUMENT_CACHE_DIR``
      or ``/tmp/orb_ai_instruments``.
    - ``force_refresh`` — bypass the cache on the next ``fetch()`` regardless
      of freshness (used by :class:`RefreshScheduler`).
    """

    def __init__(
        self,
        inner: InstrumentLoader,
        *,
        ttl_s: float = 20 * 3600,
        cache_dir: str = _DEFAULT_CACHE_DIR,
        broker_key: Optional[str] = None,
    ) -> None:
        self.inner = inner
        self.ttl_s = float(ttl_s)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._broker_key = broker_key or inner.name
        self._force_refresh = False

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.inner.name

    @property
    def cache_path(self) -> Path:
        return self.cache_dir / f"{self._broker_key}.json"

    def _is_fresh(self, mtime: float) -> bool:
        return (time.time() - mtime) < self.ttl_s

    def _load_cached(self) -> Optional[InstrumentMaster]:
        if not self.cache_path.exists():
            return None
        try:
            mtime = self.cache_path.stat().st_mtime
            if not self._is_fresh(mtime):
                return None
            with self.cache_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            return _master_from_payload(payload)
        except Exception:  # pragma: no cover — corrupt cache
            logger.exception(
                "instrument_cache_read_failed",
                extra={"broker": self._broker_key},
            )
            return None

    def _write_cache(self, master: InstrumentMaster) -> None:
        try:
            tmp = self.cache_path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(_master_to_payload(master), f)
            os.replace(tmp, self.cache_path)
        except Exception:  # pragma: no cover
            logger.exception(
                "instrument_cache_write_failed",
                extra={"broker": self._broker_key},
            )

    def force_refresh(self) -> None:
        """Tell the next ``fetch()`` to bypass the cache."""
        self._force_refresh = True

    async def fetch(self) -> InstrumentMaster:
        if not self._force_refresh:
            cached = self._load_cached()
            if cached is not None:
                logger.info(
                    "instrument_cache_hit",
                    extra={
                        "broker": self._broker_key,
                        "count": len(cached.instruments),
                    },
                )
                return cached

        master = await self.inner.fetch()
        self._write_cache(master)
        self._force_refresh = False
        logger.info(
            "instrument_master_loaded",
            extra={"broker": self._broker_key, "count": len(master.instruments)},
        )
        return master

    async def close(self) -> None:
        await self.inner.close()


# =============================================================================
# Background refresh
# =============================================================================


class RefreshScheduler:
    """Reload an :class:`InstrumentMaster` on a background schedule.

    The scheduler owns the *reference* to the current master. Consumers
    (WS providers, historical fetchers) get a fresh symbol_map every time
    they call :meth:`current`; the underlying instrument list is swapped
    atomically once a new master is materialised.

    Parameters
    ----------
    loader
        Any :class:`InstrumentLoader` (usually a :class:`CachedInstrumentLoader`).
    interval_s
        Seconds between refreshes. Default 6 h so a container that boots
        overnight picks up the next-day master before market open.
    max_consecutive_failures
        After this many back-to-back failures, the scheduler stops and logs
        an error. ``None`` = never give up.
    on_update
        Optional callback invoked with the new master after each successful
        reload. Providers wire this to their ``add_symbols`` methods.
    """

    def __init__(
        self,
        loader: InstrumentLoader,
        *,
        interval_s: float = 6 * 3600,
        max_consecutive_failures: Optional[int] = 6,
        on_update: Optional[Callable[[InstrumentMaster], Any]] = None,
    ) -> None:
        self.loader = loader
        self.interval_s = float(interval_s)
        self.max_consecutive_failures = max_consecutive_failures
        self._on_update = on_update

        self._master: Optional[InstrumentMaster] = None
        self._task: Optional[asyncio.Task] = None
        self._closed = asyncio.Event()
        self._consecutive_failures = 0
        self._refresh_count = 0
        self._last_refresh_at: Optional[float] = None
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Do one initial load then kick off the background loop."""
        await self._refresh_once()
        if self._task is None:
            self._task = asyncio.create_task(
                self._loop(), name=f"{self.loader.name}-instrument-refresh"
            )

    async def stop(self) -> None:
        self._closed.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.loader.close()
        except Exception:  # pragma: no cover
            pass

    # ----------------------------------------------------- consumer access

    def current(self) -> Optional[InstrumentMaster]:
        return self._master

    def get_stats(self) -> dict[str, Any]:
        return {
            "broker": self.loader.name,
            "is_running": self._task is not None and not self._task.done(),
            "refresh_count": self._refresh_count,
            "consecutive_failures": self._consecutive_failures,
            "last_refresh_at": self._last_refresh_at,
            "last_error": self._last_error,
            "current_size": len(self._master.instruments) if self._master else 0,
        }

    async def refresh_now(self) -> Optional[InstrumentMaster]:
        """Force an immediate refresh (used by tests + admin endpoints)."""
        if isinstance(self.loader, CachedInstrumentLoader):
            self.loader.force_refresh()
        await self._refresh_once()
        return self._master

    # ---------------------------------------------------------- internals

    async def _refresh_once(self) -> None:
        try:
            master = await self.loader.fetch()
        except Exception as exc:
            self._consecutive_failures += 1
            self._last_error = str(exc)
            logger.exception(
                "instrument_refresh_failed",
                extra={
                    "broker": self.loader.name,
                    "failures": self._consecutive_failures,
                },
            )
            return
        self._consecutive_failures = 0
        self._last_error = None
        self._refresh_count += 1
        self._last_refresh_at = time.time()
        self._master = master
        if self._on_update is not None:
            try:
                result = self._on_update(master)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # pragma: no cover
                logger.exception(
                    "instrument_on_update_failed",
                    extra={"broker": self.loader.name},
                )

    async def _loop(self) -> None:
        try:
            while not self._closed.is_set():
                try:
                    await asyncio.wait_for(
                        self._closed.wait(), timeout=self.interval_s
                    )
                    return  # closed
                except asyncio.TimeoutError:
                    pass
                await self._refresh_once()
                if (
                    self.max_consecutive_failures is not None
                    and self._consecutive_failures >= self.max_consecutive_failures
                ):
                    logger.error(
                        "instrument_refresh_giving_up",
                        extra={
                            "broker": self.loader.name,
                            "failures": self._consecutive_failures,
                        },
                    )
                    return
        except asyncio.CancelledError:
            return


# =============================================================================
# Serialisation helpers
# =============================================================================


def _master_to_payload(master: InstrumentMaster) -> dict[str, Any]:
    return {
        "broker": master.broker,
        "loaded_at": master.loaded_at,
        "instruments": [
            {
                "symbol": i.symbol,
                "token": i.token,
                "exchange_segment": i.exchange_segment,
                "exchange": i.exchange,
                "kind": i.kind.value,
                "instrument_type": i.instrument_type,
                "lot_size": i.lot_size,
                "tick_size": i.tick_size,
                "isin": i.isin,
                "underlying": i.underlying,
                "expiry": i.expiry.isoformat() if i.expiry else None,
                "strike": i.strike,
                "option_type": i.option_type,
            }
            for i in master.instruments
        ],
    }


def _master_from_payload(payload: dict[str, Any]) -> InstrumentMaster:
    def _parse_date(v: Any) -> Optional[date]:
        if not v:
            return None
        try:
            return date.fromisoformat(str(v))
        except ValueError:
            return None

    instruments = [
        Instrument(
            symbol=str(row["symbol"]),
            token=str(row["token"]),
            exchange_segment=str(row["exchange_segment"]),
            exchange=str(row.get("exchange") or "NSE"),
            kind=InstrumentKind(row.get("kind") or "EQUITY"),
            instrument_type=(
                str(row["instrument_type"]).upper()
                if row.get("instrument_type")
                else None
            ),
            lot_size=int(row.get("lot_size") or 1),
            tick_size=float(row.get("tick_size") or 0.05),
            isin=row.get("isin"),
            underlying=row.get("underlying"),
            expiry=_parse_date(row.get("expiry")),
            strike=(float(row["strike"]) if row.get("strike") is not None else None),
            option_type=row.get("option_type"),
        )
        for row in (payload.get("instruments") or [])
    ]
    return InstrumentMaster(
        broker=str(payload.get("broker") or "unknown"),
        instruments=instruments,
        loaded_at=float(payload.get("loaded_at") or time.time()),
    )
