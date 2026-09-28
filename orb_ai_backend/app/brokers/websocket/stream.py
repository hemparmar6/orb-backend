"""BrokerOrderStream — WebSocket primary, polling fallback.

Consumes broker order updates from ``adapter.stream_order_updates()``. If the
adapter doesn't implement WS or the stream disconnects, transparently falls
back to polling ``adapter.get_order(broker_order_id)`` for every open order
every ``poll_interval_s`` seconds.

Each update is:
1. Forwarded to the ``on_update`` async callback (this is how the engine
   reconciles broker fills into ``paper_orders`` / ``paper_positions``).
2. Published to Redis on channel ``orders:{engine_session_id}`` (if a Redis
   client is available). This gives future WS clients (Module 4) a real-time
   feed of order updates keyed by session.
"""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

from app.brokers.base import BrokerAdapter, BrokerOrderResult, BrokerOrderStatus
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)


UpdateCallback = Callable[[BrokerOrderResult], Awaitable[None]]
OpenOrderIdsProvider = Callable[[], Awaitable[list[str]]]


class BrokerOrderStream:
    def __init__(
        self,
        *,
        adapter: BrokerAdapter,
        engine_session_id: str,
        on_update: UpdateCallback,
        open_order_ids: OpenOrderIdsProvider,
        poll_interval_s: float = 3.0,
        ws_backoff_s: float = 2.0,
        publish_to_redis: bool = True,
    ) -> None:
        self.adapter = adapter
        self.engine_session_id = engine_session_id
        self.on_update = on_update
        self.open_order_ids = open_order_ids
        self.poll_interval_s = poll_interval_s
        self.ws_backoff_s = ws_backoff_s
        self.publish_to_redis = publish_to_redis

        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._mode: str = "idle"  # "ws" | "poll" | "idle"

    @property
    def mode(self) -> str:
        return self._mode

    # ---- lifecycle ------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"broker-stream-{self.engine_session_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._mode = "idle"

    # ---- core loop -----------------------------------------------

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._try_ws()
            except NotImplementedError:
                logger.info(
                    "broker_stream_ws_unsupported_falling_back_to_polling",
                    extra={"broker": self.adapter.broker_type, "engine_session_id": self.engine_session_id},
                )
                await self._poll_loop()
            except Exception as exc:  # pragma: no cover - network paths
                logger.warning(
                    "broker_stream_ws_error_falling_back_to_polling",
                    extra={"error": str(exc)},
                )
                await asyncio.sleep(self.ws_backoff_s)
                await self._poll_loop()

    async def _try_ws(self) -> None:
        self._mode = "ws"
        agen = self.adapter.stream_order_updates()
        async for update in agen:
            if self._stop.is_set():
                break
            await self._dispatch(update)

    async def _poll_loop(self) -> None:
        self._mode = "poll"
        while not self._stop.is_set():
            try:
                order_ids = await self.open_order_ids()
                for oid in order_ids:
                    if not oid:
                        continue
                    try:
                        result = await self.adapter.get_order(oid)
                    except NotImplementedError:
                        # Adapter is a pure skeleton — nothing to do.
                        return
                    except Exception as exc:  # pragma: no cover
                        logger.warning("broker_poll_get_order_failed",
                                       extra={"broker_order_id": oid, "error": str(exc)})
                        continue
                    await self._dispatch(result)
            except Exception:  # pragma: no cover
                logger.exception("broker_stream_poll_loop_error")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_s)
            except asyncio.TimeoutError:
                continue

    async def _dispatch(self, update: BrokerOrderResult) -> None:
        try:
            await self.on_update(update)
        except Exception:  # pragma: no cover
            logger.exception("broker_stream_on_update_callback_failed",
                             extra={"broker_order_id": update.broker_order_id})
        if self.publish_to_redis:
            await self._publish(update)

    async def _publish(self, update: BrokerOrderResult) -> None:
        client = await get_redis()
        if client is None:
            return
        payload = {
            "engine_session_id": self.engine_session_id,
            "broker_order_id": update.broker_order_id,
            "status": update.status.value if isinstance(update.status, BrokerOrderStatus) else str(update.status),
            "filled_quantity": update.filled_quantity,
            "average_fill_price": update.average_fill_price,
            "client_order_id": update.client_order_id,
            "rejection_reason": update.rejection_reason,
        }
        try:
            await client.publish(
                f"orders:{self.engine_session_id}",
                json.dumps(payload, default=str),
            )
        except Exception:  # pragma: no cover
            logger.warning("broker_stream_redis_publish_failed")
