"""Auth endpoints: register, login, refresh, logout."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, DBSession
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.models.monitoring import LoginActivity
from app.monitoring.metrics import metrics
from app.schemas.auth import LoginRequest, LogoutResponse, RefreshRequest, TokenPair
from app.schemas.user import UserCreate, UserRead
from app.services.auth_service import AuthService

router = APIRouter()
_sec_log = get_logger("app.security.auth")


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(payload: UserCreate, session: DBSession) -> UserRead:
    user = await AuthService(session).register(payload)
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Login with email + password",
)
async def login(
    payload: LoginRequest, request: Request, session: DBSession
) -> TokenPair:
    ip = _client_ip(request)
    ua = (request.headers.get("user-agent") or "")[:512]
    try:
        tokens = await AuthService(session).login(payload.email, payload.password)
        # Record success (best-effort; never break login on audit failure)
        try:
            session.add(
                LoginActivity(
                    email=payload.email,
                    user_id=None,
                    success=True,
                    reason=None,
                    ip_address=ip,
                    user_agent=ua,
                )
            )
            await session.commit()
        except Exception:
            _sec_log.exception("login_activity_write_failed")
        metrics.record_auth(success=True)
        _sec_log.info(
            "login_success",
            extra={"category": "security", "email": payload.email, "ip": ip},
        )
        return tokens
    except (HTTPException, AppError) as exc:
        # Any auth failure — rollback the failed transaction and record
        # the attempt on a *fresh* session so the write actually commits.
        try:
            await session.rollback()
        except Exception:
            pass
        try:
            from app.db.session import async_session_factory

            reason = getattr(exc, "code", None) or (
                str(getattr(exc, "detail", "invalid_credentials"))[:64]
            )
            async with async_session_factory() as audit_db:
                audit_db.add(
                    LoginActivity(
                        email=payload.email,
                        user_id=None,
                        success=False,
                        reason=reason,
                        ip_address=ip,
                        user_agent=ua,
                    )
                )
                await audit_db.commit()
        except Exception:
            _sec_log.exception("login_activity_write_failed")
        metrics.record_auth(success=False)
        _sec_log.warning(
            "login_failed",
            extra={"category": "security", "email": payload.email, "ip": ip},
        )
        raise


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate the refresh token and get a new access token",
)
async def refresh(payload: RefreshRequest, session: DBSession) -> TokenPair:
    return await AuthService(session).refresh(payload.refresh_token)


@router.post(
    "/logout",
    response_model=LogoutResponse,
    summary="Invalidate the current refresh token",
)
async def logout(current_user: CurrentUser, session: DBSession) -> LogoutResponse:
    await AuthService(session).logout(current_user)
    return LogoutResponse()
