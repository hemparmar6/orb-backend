"""WebSocket message protocol.

All frames are JSON. Client → server:

    { "action": "subscribe",   "channel": "quotes", "symbols": ["A", "B"] }
    { "action": "unsubscribe", "channel": "quotes", "symbols": ["A"] }
    { "action": "subscribe",   "channel": "orders", "session_ids": ["<uuid>"] }
    { "action": "ping" }

Server → client:

    { "type": "quote",             "data": {...} }
    { "type": "order_update",      "data": {...} }
    { "type": "subscription_ack",  "data": {"channel": "quotes", "action": "subscribe", "symbols": [...]} }
    { "type": "pong" }
    { "type": "error",             "data": {"code": "...", "message": "..."} }
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

# ---- Client → Server -----------------------------------------------------


class ClientMessage(BaseModel):
    action: Literal["subscribe", "unsubscribe", "ping"]
    channel: Optional[Literal["quotes", "orders"]] = None
    symbols: list[str] = Field(default_factory=list)
    session_ids: list[str] = Field(default_factory=list)


def parse_client_message(payload: dict[str, Any]) -> ClientMessage | ValidationError:
    try:
        return ClientMessage.model_validate(payload)
    except ValidationError as e:
        return e


# ---- Server → Client -----------------------------------------------------


def frame(type_: str, data: Any = None) -> dict[str, Any]:
    return {"type": type_, "data": data}


def error_frame(code: str, message: str) -> dict[str, Any]:
    return frame("error", {"code": code, "message": message})


def ack_frame(channel: str, action: str, symbols: list[str] | None = None, session_ids: list[str] | None = None) -> dict[str, Any]:
    return frame(
        "subscription_ack",
        {
            "channel": channel,
            "action": action,
            "symbols": symbols or [],
            "session_ids": session_ids or [],
        },
    )
