"""Scheduler tick that checks whether an automatic LIVE → PAPER revert is
due and, if so, performs it safely through :class:`TradingModeService`.

The tick is idempotent and DB-guarded — see
``TradingModeService.run_auto_revert_if_due`` for the safety contract.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.db.session import async_session_factory
from app.services.trading_mode_service import TradingModeService

logger = get_logger(__name__)


async def run_auto_revert_tick() -> None:
    if async_session_factory is None:  # pragma: no cover — startup-order guard
        logger.warning("trading_mode_auto_revert_tick_no_session_factory")
        return
    try:
        async with async_session_factory() as session:
            result = await TradingModeService(session).run_auto_revert_if_due()
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("trading_mode_auto_revert_tick_failed")
        return
    if result.get("ran"):
        logger.info("trading_mode_auto_revert_ran", extra=result)
    elif result.get("reason") in {"live_exposure", "error"}:
        logger.warning("trading_mode_auto_revert_skipped", extra=result)
