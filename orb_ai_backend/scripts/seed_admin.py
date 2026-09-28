"""Idempotent admin seeder for the ORB AI Admin Dashboard.

Reads `ADMIN_SEED_EMAIL`, `ADMIN_SEED_PASSWORD`, and `ADMIN_SEED_FULL_NAME`
from the environment. If the email doesn't exist, it creates an active,
verified user with ``role=admin``. If it exists but isn't an admin, it
promotes them. Never overwrites an existing password.

Security (Task 5):
- In **production** (`APP_ENV=production`) there are NO default credentials.
  ``ADMIN_SEED_EMAIL`` and ``ADMIN_SEED_PASSWORD`` must be supplied explicitly,
  the well-known development defaults (``admin@orb.com`` / ``Admin@123``) are
  rejected, and the password must be reasonably strong. Missing/insecure
  values raise ``AdminSeedConfigError`` instead of seeding a guessable admin.
- In development/test the historical convenience defaults are preserved so
  existing local + CI seeding keeps working unchanged.

Called from ``scripts/entrypoint_local.sh`` at container / pod startup so
Module 7 has a known-good admin account for the dashboard.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow running from anywhere
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.user import User, UserRole

# Well-known development defaults that must never be usable in production.
_BLOCKED_ADMIN_EMAILS = {"admin@orb.com"}
_BLOCKED_ADMIN_PASSWORDS = {"Admin@123"}
_DEV_DEFAULT_EMAIL = "admin@orb.com"
_DEV_DEFAULT_PASSWORD = "Admin@123"
_MIN_PROD_PASSWORD_LEN = 12


class AdminSeedConfigError(RuntimeError):
    """Raised when production admin seeding is misconfigured/insecure."""


def _resolve_admin_credentials() -> tuple[str, str, str]:
    """Return (email, password, full_name) or raise in production.

    Never logs or echoes the password.
    """
    app_env = os.environ.get("APP_ENV", "development").strip().lower()
    email = os.environ.get("ADMIN_SEED_EMAIL")
    password = os.environ.get("ADMIN_SEED_PASSWORD")
    full_name = os.environ.get("ADMIN_SEED_FULL_NAME", "ORB Admin")

    if app_env == "production":
        if not (email and email.strip()) or not (password and password.strip()):
            raise AdminSeedConfigError(
                "ADMIN_SEED_EMAIL and ADMIN_SEED_PASSWORD must be set "
                "explicitly in production (no default admin credentials)"
            )
        if "replace_me" in email.lower() or "replace_me" in password.lower():
            raise AdminSeedConfigError(
                "Replace the ADMIN_SEED_EMAIL and ADMIN_SEED_PASSWORD "
                "template values before production startup"
            )
        if email.strip().lower() in _BLOCKED_ADMIN_EMAILS:
            raise AdminSeedConfigError(
                "Refusing to seed a well-known default admin email in production"
            )
        if password in _BLOCKED_ADMIN_PASSWORDS:
            raise AdminSeedConfigError(
                "Refusing to seed a well-known default admin password in production"
            )
        if len(password) < _MIN_PROD_PASSWORD_LEN:
            raise AdminSeedConfigError(
                f"ADMIN_SEED_PASSWORD must be at least {_MIN_PROD_PASSWORD_LEN} "
                "characters in production"
            )
        return email.strip().lower(), password, full_name

    # development / staging / test — preserve the historical convenience defaults.
    email = (email or _DEV_DEFAULT_EMAIL).strip().lower()
    password = password or _DEV_DEFAULT_PASSWORD
    return email, password, full_name


async def main() -> None:
    email, password, full_name = _resolve_admin_credentials()

    # Ensure tables exist (safe for SQLite dev; Alembic owns prod).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        existing = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing is None:
            user = User(
                email=email,
                hashed_password=hash_password(password),
                full_name=full_name,
                role=UserRole.ADMIN,
                is_active=True,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            print(f"[seed_admin] Created admin user {email}")
        else:
            changed = False
            if existing.role != UserRole.ADMIN:
                existing.role = UserRole.ADMIN
                changed = True
            if not existing.is_active:
                existing.is_active = True
                changed = True
            if not existing.is_verified:
                existing.is_verified = True
                changed = True
            if changed:
                await session.commit()
                print(f"[seed_admin] Promoted existing user {email} to admin")
            else:
                print(f"[seed_admin] Admin user {email} already exists — no changes")


if __name__ == "__main__":
    asyncio.run(main())
