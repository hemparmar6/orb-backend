"""v1 WebSocket router."""

from fastapi import APIRouter

from app.api.v1.ws import admin, ai, notifications, orders, quotes, risk

ws_router = APIRouter()
ws_router.include_router(quotes.router)
ws_router.include_router(orders.router)
ws_router.include_router(admin.router)
ws_router.include_router(notifications.router)
ws_router.include_router(ai.router)
ws_router.include_router(risk.router)
