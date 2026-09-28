"""Global exception handlers.

All handlers produce the same JSON envelope:

    { "error": {
        "code": "<machine_code>",
        "message": "<human-readable message>",
        "details": <optional structured details>,
        "request_id": "<uuid>"
    } }
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _envelope(*, code: str, message: str, details=None, request_id: str = "-", status_code: int = 500) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": request_id,
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:  # noqa: D401
        rid = getattr(request.state, "request_id", "-")
        return _envelope(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=rid,
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        rid = getattr(request.state, "request_id", "-")
        # Pydantic can stuff non-JSON objects (e.g. the original ValueError from
        # a custom @field_validator) into ctx. Sanitize before rendering.
        safe_errors: list = []
        for err in exc.errors():
            e = dict(err)
            ctx = e.get("ctx")
            if isinstance(ctx, dict):
                e["ctx"] = {k: (str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v)
                            for k, v in ctx.items()}
            safe_errors.append(e)
        return _envelope(
            code="validation_failed",
            message="Request validation failed",
            details=safe_errors,
            request_id=rid,
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        rid = getattr(request.state, "request_id", "-")
        return _envelope(
            code=f"http_{exc.status_code}",
            message=str(exc.detail) if exc.detail else "HTTP error",
            details=None,
            request_id=rid,
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", "-")
        logger.exception("unhandled_exception", extra={"path": request.url.path})
        return _envelope(
            code="internal_error",
            message="Internal server error",
            details=None,
            request_id=rid,
            status_code=500,
        )
