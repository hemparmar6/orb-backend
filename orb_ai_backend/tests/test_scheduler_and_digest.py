"""Weekly P&L digest + scheduler tests (Module 8)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.audit import AuditLog
from app.models.notification import Notification
from app.models.report import ReportRun, ReportStatus, ReportType
from app.models.user import User
from app.services.subscriptions.feature_gate import FeatureGate
from tests._module8_helpers import register_and_login, seed_engine_data


@pytest.mark.asyncio
async def test_weekly_digest_generates_pdf_and_notifies(client, db_session, db_engine):
    user_id, _ = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    # Weekly digests are a Pro feature in the current ORB AI 2.0 catalog.
    # Exercise the successful path with an entitled account rather than the
    # default Standard account created by registration.
    user = await db_session.get(User, user_id)
    await FeatureGate(db_session).set_plan(user, "pro-monthly")
    await db_session.commit()

    # Point the digest at the test DB
    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    with patch("app.services.tasks.weekly_digest.async_session_factory", factory):
        from app.services.tasks.weekly_digest import run_weekly_digest
        result = await run_weekly_digest()

    assert result["processed"] == 1
    assert result["sent"] == 1
    assert result["skipped"] == 0

    # A GENERATED ReportRun row must exist
    async with factory() as s:
        runs = (await s.execute(
            select(ReportRun).where(ReportRun.user_id == user_id)
        )).scalars().all()
        assert any(r.report_type == ReportType.WEEKLY and r.status == ReportStatus.GENERATED for r in runs)
        weekly = next(r for r in runs if r.report_type == ReportType.WEEKLY)
        assert weekly.byte_size > 500
        assert weekly.filename and weekly.filename.startswith("orb_ai_weekly_digest_")

        # A Notification must have been created
        notifs = (await s.execute(
            select(Notification).where(Notification.user_id == user_id)
        )).scalars().all()
        assert any("Weekly P&L" in n.title for n in notifs)

        # An audit log entry must exist
        audits = (await s.execute(
            select(AuditLog).where(AuditLog.actor_user_id == user_id)
        )).scalars().all()
        assert any(a.action == "report.scheduled.generated" for a in audits)


@pytest.mark.asyncio
async def test_weekly_digest_skips_inactive_users(client, db_session, db_engine):
    user_id, _ = await register_and_login(client)
    await seed_engine_data(db_session, user_id)

    # Deactivate the user
    u = await db_session.get(User, user_id)
    u.is_active = False
    await db_session.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    with patch("app.services.tasks.weekly_digest.async_session_factory", factory):
        from app.services.tasks.weekly_digest import run_weekly_digest
        # Only active users are picked up by default query — expect zero
        result = await run_weekly_digest()

    assert result["processed"] == 0


@pytest.mark.asyncio
async def test_scheduler_status_requires_admin(client):
    _, headers = await register_and_login(client)
    r = await client.get("/api/v1/scheduler/status", headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_scheduler_status_admin_ok(client, db_session):
    _, headers = await register_and_login(client)
    # Promote the user to admin
    from app.models.user import UserRole
    u = (await db_session.execute(select(User))).scalars().first()
    u.role = UserRole.ADMIN
    await db_session.commit()

    r = await client.get("/api/v1/scheduler/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "enabled" in body
    assert "jobs" in body


@pytest.mark.asyncio
async def test_scheduler_trigger_unknown_job(client, db_session):
    _, headers = await register_and_login(client)
    from app.models.user import UserRole
    u = (await db_session.execute(select(User))).scalars().first()
    u.role = UserRole.ADMIN
    await db_session.commit()

    r = await client.post("/api/v1/scheduler/trigger/nope", headers=headers)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_scheduler_trigger_weekly_digest(client, db_session, db_engine):
    user_id, headers = await register_and_login(client)
    await seed_engine_data(db_session, user_id)
    from app.models.user import UserRole
    u = (await db_session.execute(select(User))).scalars().first()
    u.role = UserRole.ADMIN
    await db_session.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    with patch("app.services.tasks.weekly_digest.async_session_factory", factory):
        r = await client.post("/api/v1/scheduler/trigger/weekly_pnl_digest", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"] == "weekly_pnl_digest"
    assert body["processed"] >= 1
