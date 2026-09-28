"""Module 9 — AI push WebSocket.

Auth follows the project convention: JWT via ``?token=`` query param,
resolved by :func:`app.api.ws_deps.authenticate_ws`.

Events emitted server → client (JSON):
    {"event": "trade_review.created",   "data": {"trade_id": "...", "review_id": "..."}}
    {"event": "recommendation.created", "data": {"id": "...", "priority": "high"}}
    {"event": "optimisation.status",    "data": {"job_id": "...", "status": "done"}}

Other Module-9 services publish via ``ai_ws_manager.send(user_id, event, data)``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Set

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from app.api.ws_deps import authenticate_ws
from app.core.exceptions import (
    InactiveUserError,
    InvalidTokenError,
    UnauthorizedError,
)
from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class AIConnectionManager:
    def __init__(self) -> None:
        self._conns: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._conns.setdefault(user_id, set()).add(ws)

    async def disconnect(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._conns.get(user_id, set()).discard(ws)

    async def send(self, user_id: str, event: str, data: Dict[str, Any]) -> None:
        payload = json.dumps({"event": event, "data": data}, default=str)
        for ws in list(self._conns.get(user_id, set())):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                await self.disconnect(user_id, ws)


ai_ws_manager = AIConnectionManager()


@router.websocket("/ai")
async def ai_ws(ws: WebSocket, session=Depends(get_db)):
    try:
        user = await authenticate_ws(ws, session)
    except (UnauthorizedError, InvalidTokenError, InactiveUserError):
        await ws.close(code=4401)
        return

    await ai_ws_manager.connect(user.id, ws)
    try:
        while True:
            # We don't expect messages from the client; drain to keep the socket alive.
            await ws.receive_text()
    except WebSocketDisconnect:
        await ai_ws_manager.disconnect(user.id, ws)
