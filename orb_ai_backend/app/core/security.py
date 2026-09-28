"""Password hashing and JWT helpers.

- Passwords are hashed with bcrypt via passlib (constant-time verify).
- JWT is HS256, encoded with PyJWT.
- Two token types are used:
    * "access"  — short-lived, sent as `Authorization: Bearer <token>`
    * "refresh" — long-lived, exchanged at /auth/refresh
  Each refresh token embeds a unique `jti` which is stored on the user row
  so logout / rotation can invalidate previous refresh tokens.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.exceptions import InvalidTokenError

# ---- Password hashing -------------------------------------------------------

_pwd_ctx = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.BCRYPT_ROUNDS,
)


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time password verification."""
    try:
        return _pwd_ctx.verify(plain, hashed)
    except ValueError:
        # Malformed hash — treat as no match.
        return False


# ---- JWT --------------------------------------------------------------------

TokenType = Literal["access", "refresh"]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    """Create a short-lived access token."""
    now = _now_utc()
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return _encode(payload)


def create_refresh_token(subject: str) -> tuple[str, str, datetime]:
    """Create a long-lived refresh token.

    Returns:
        (token, jti, expires_at) — the jti and expiry are persisted so the
        token can be revoked on logout / rotation.
    """
    now = _now_utc()
    expire = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    jti = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": jti,
    }
    return _encode(payload), jti, expire


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode & validate a JWT. Raises InvalidTokenError on any failure."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Invalid token") from exc

    if expected_type and payload.get("type") != expected_type:
        raise InvalidTokenError(f"Expected {expected_type} token")
    if "sub" not in payload:
        raise InvalidTokenError("Token missing subject")
    return payload
