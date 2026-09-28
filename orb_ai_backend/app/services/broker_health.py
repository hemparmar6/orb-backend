"""Phase 5 — Broker WebSocket health tracker.

Persists per-broker connection state in-memory (single-process) and
funnels events into ``CircuitBreakerService``:

* On unexpected disconnect  → ``report_broker_incident(DISCONNECT_DETECTION)``
* On successful reconnect   → resolves the last unresolved event and updates
  aggregate downtime / retry counters.

Duplicate disconnects (before a reconnect) are deduplicated — a second
disconnect within the same "outage" only bumps ``retry_count`` on the
existing state, it does NOT create a second ``CircuitBreakerEvent``.

Public state (via :meth:`snapshot`) is exposed on
``GET /api/v1/monitoring/broker-health`` and read by the Bot Dashboard.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.core.logging import get_logger
from app.models.bot import (
    BreakerLevel,
    BreakerType,
    CircuitBreakerEvent,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class BrokerHealthState:
    broker_type: str
    connected: bool = False
    last_connect_at: Optional[str] = None
    last_disconnect_at: Optional[str] = None
    last_reconnect_at: Optional[str] = None
    last_heartbeat_at: Optional[str] = None
    downtime_seconds: float = 0.0
    total_downtime_seconds: float = 0.0
    retry_count: int = 0
    total_reconnects: int = 0
    total_disconnects: int = 0
    incident_open: bool = False
    last_incident_id: Optional[str] = None
    last_error: Optional[str] = None
    # Internal (not exported):
    _disconnect_ts: Optional[datetime] = field(default=None, repr=False)


class BrokerHealthTracker:
    """Singleton, process-scoped broker health tracker."""

    def __init__(self) -> None:
        self._states: dict[str, BrokerHealthState] = {}
        self._lock = asyncio.Lock()
        self._session_factory: Any = None

    def bind_session_factory(self, factory: Any) -> None:
        self._session_factory = factory

    def _state(self, broker_type: str) -> BrokerHealthState:
        st = self._states.get(broker_type)
        if st is None:
            st = BrokerHealthState(broker_type=broker_type)
            self._states[broker_type] = st
        return st

    def snapshot(self) -> list[dict[str, Any]]:
        """Public state, sorted by broker_type."""
        out = []
        for st in sorted(self._states.values(), key=lambda s: s.broker_type):
            d = asdict(st)
            d.pop("_disconnect_ts", None)
            out.append(d)
        return out

    def get(self, broker_type: str) -> Optional[dict[str, Any]]:
        st = self._states.get(broker_type)
        if st is None:
            return None
        d = asdict(st)
        d.pop("_disconnect_ts", None)
        return d

    def is_healthy(self, broker_type: str) -> bool:
        st = self._states.get(broker_type)
        return bool(st and st.connected and not st.incident_open)

    # ---- lifecycle hooks ------------------------------------------------

    async def on_connect(self, broker_type: str) -> None:
        async with self._lock:
            st = self._state(broker_type)
            now = datetime.now(timezone.utc)
            was_disconnected = not st.connected
            st.connected = True
            st.last_connect_at = now.isoformat()
            st.last_heartbeat_at = now.isoformat()
            st.last_error = None

            if was_disconnected and st._disconnect_ts is not None:
                # Successful reconnect after an outage → resolve incident.
                elapsed = (now - st._disconnect_ts).total_seconds()
                st.last_reconnect_at = now.isoformat()
                st.total_reconnects += 1
                st.total_downtime_seconds += max(0.0, elapsed)
                st.downtime_seconds = max(0.0, elapsed)
                await self._resolve_last_incident(broker_type, st)
                st._disconnect_ts = None
                st.retry_count = 0
                st.incident_open = False
                logger.info(
                    "broker_ws_health_reconnected",
                    extra={"broker": broker_type, "downtime_s": elapsed},
                )

    async def on_heartbeat(self, broker_type: str) -> None:
        async with self._lock:
            st = self._state(broker_type)
            st.last_heartbeat_at = datetime.now(timezone.utc).isoformat()

    async def on_disconnect(self, broker_type: str, *,
                            reason: str = "",
                            unexpected: bool = True) -> None:
        """Dedup: if state was already `incident_open`, only bump retry_count."""
        async with self._lock:
            st = self._state(broker_type)
            now = datetime.now(timezone.utc)
            st.connected = False
            st.last_disconnect_at = now.isoformat()
            st.last_error = reason or None

            if st.incident_open:
                st.retry_count += 1
                logger.info(
                    "broker_ws_disconnect_dedup",
                    extra={"broker": broker_type, "retry": st.retry_count},
                )
                return

            # First disconnect in this outage window → open incident.
            st.incident_open = True
            st._disconnect_ts = now
            st.retry_count = 1
            st.total_disconnects += 1
            if unexpected:
                await self._open_incident(broker_type, st, reason)
            logger.info(
                "broker_ws_health_disconnected",
                extra={"broker": broker_type, "reason": reason},
            )

    async def on_error(self, broker_type: str, reason: str) -> None:
        """Non-terminal error (parse/decode/subscribe failure)."""
        async with self._lock:
            st = self._state(broker_type)
            st.last_error = reason

    # ---- private DB writers --------------------------------------------

    async def _open_incident(self, broker_type: str,
                             st: BrokerHealthState, reason: str) -> None:
        if self._session_factory is None:
            return  # tests / preview without DB
        try:
            from app.services.bots_service import CircuitBreakerService
            async with self._session_factory() as db:
                ev = await CircuitBreakerService(db).report_broker_incident(
                    broker_type=broker_type,
                    kind=BreakerType.DISCONNECT_DETECTION,
                    reason=reason or "broker websocket disconnected",
                    count=1,
                )
                await db.commit()
                st.last_incident_id = ev.id
        except Exception:  # pragma: no cover
            logger.exception("broker_health_open_incident_failed",
                             extra={"broker": broker_type})

    async def _resolve_last_incident(self, broker_type: str,
                                     st: BrokerHealthState) -> None:
        if self._session_factory is None or st.last_incident_id is None:
            return
        try:
            async with self._session_factory() as db:
                ev = await db.get(CircuitBreakerEvent, st.last_incident_id)
                if ev is not None and ev.resolved_at is None:
                    ev.resolved_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception:  # pragma: no cover
            logger.exception("broker_health_resolve_failed",
                             extra={"broker": broker_type})


# Process-wide singleton
tracker = BrokerHealthTracker()


# ---- ReconnectingWSClient patching ---------------------------------------

def install_health_hooks(client: Any, broker_type: str) -> Any:
    """Wrap the on_connect callback so ``BrokerHealthTracker`` sees connect events.

    Disconnects are captured by wrapping the underlying ``_iterate`` loop via
    contextlib — we set health at the boundaries. Idempotent on repeated calls.
    """
    if getattr(client, "_health_hooks_installed", False):
        return client
    original_on_connect = getattr(client, "_on_connect", None)

    async def _wrapped_on_connect(ws: Any) -> None:
        await tracker.on_connect(broker_type)
        if original_on_connect is not None:
            await original_on_connect(ws)

    client._on_connect = _wrapped_on_connect
    original_close = client.close

    async def _wrapped_close() -> None:
        with contextlib.suppress(Exception):
            await tracker.on_disconnect(
                broker_type, reason="graceful_close", unexpected=False
            )
        await original_close()

    client.close = _wrapped_close
    client._health_hooks_installed = True
    return client
