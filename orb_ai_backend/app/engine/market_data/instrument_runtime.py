"""Process-wide Dhan instrument-master lifecycle and symbol resolution."""
from __future__ import annotations

from typing import Optional

from app.brokers.instruments import (
    CachedInstrumentLoader,
    Instrument,
    InstrumentMaster,
    RefreshScheduler,
    get_loader,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class DhanInstrumentRuntime:
    """Own one cached, refreshable Dhan instrument-master snapshot."""

    def __init__(self) -> None:
        self._scheduler: Optional[RefreshScheduler] = None

    async def start(self) -> None:
        """Start the loader only when Dhan is the configured market provider."""
        if (settings.MARKET_DATA_PROVIDER or "").strip().lower() != "dhan":
            return
        if self._scheduler is not None:
            return

        scheduler: Optional[RefreshScheduler] = None
        try:
            loader = CachedInstrumentLoader(
                get_loader("dhan"),
                broker_key="dhan",
            )
            scheduler = RefreshScheduler(loader)
            self._scheduler = scheduler
            await scheduler.start()
            current = scheduler.current()
            if current is None:
                logger.warning("dhan_instrument_master_unavailable")
            else:
                logger.info(
                    "dhan_instrument_master_ready",
                    extra={"count": len(current.instruments)},
                )
        except Exception:
            logger.exception("dhan_instrument_runtime_start_failed")
            if scheduler is not None:
                try:
                    await scheduler.stop()
                except Exception:  # pragma: no cover
                    logger.exception("dhan_instrument_runtime_cleanup_failed")
            self._scheduler = None

    async def stop(self) -> None:
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler is not None:
            await scheduler.stop()

    def current(self) -> Optional[InstrumentMaster]:
        return self._scheduler.current() if self._scheduler is not None else None

    def resolve(self, symbol: str, exchange: str) -> Optional[Instrument]:
        """Resolve a user symbol through the current master only.

        Dhan's master stores derivatives under their underlying exchange (for
        example ``NSE``) while clients may call that venue ``NFO``. Exact
        lookup is attempted first, followed by only those known aliases.
        """
        master = self.current()
        requested_exchange = str(exchange or "").strip().upper()
        requested_symbol = str(symbol or "").strip()
        if master is None or not requested_symbol or requested_exchange in {"", "MOCK"}:
            return None

        symbols = (requested_symbol, requested_symbol.upper())
        for candidate in dict.fromkeys(symbols):
            resolved = master.lookup(candidate, exchange=requested_exchange)
            if resolved is not None:
                return resolved

        aliases = {
            "NFO": "NSE",
            "NSE_FNO": "NSE",
            "BFO": "BSE",
            "BSE_FNO": "BSE",
            "NSE_EQ": "NSE",
            "BSE_EQ": "BSE",
            "IDX_I": "NSE",
            "MCX_COMM": "MCX",
        }
        canonical_exchange = aliases.get(requested_exchange)
        if canonical_exchange is None:
            return None

        for candidate in dict.fromkeys(symbols):
            resolved = master.lookup(candidate, exchange=canonical_exchange)
            if resolved is None:
                continue
            if (
                requested_exchange in {"NFO", "NSE_FNO", "BFO", "BSE_FNO"}
                and "FNO" not in resolved.exchange_segment
            ):
                continue
            if requested_exchange == "IDX_I" and resolved.exchange_segment != "IDX_I":
                continue
            if requested_exchange == "MCX_COMM" and resolved.exchange_segment != "MCX_COMM":
                continue
            return resolved
        return None


dhan_instrument_runtime = DhanInstrumentRuntime()


async def start_dhan_instrument_runtime() -> None:
    await dhan_instrument_runtime.start()


async def stop_dhan_instrument_runtime() -> None:
    await dhan_instrument_runtime.stop()


def resolve_dhan_instrument(symbol: str, exchange: str) -> Optional[Instrument]:
    return dhan_instrument_runtime.resolve(symbol, exchange)