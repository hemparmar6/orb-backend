"""Module 10 — backup service tests.

pg_dump is not present in most CI environments, so the actual dump call
is monkey-patched. What we validate here is the *plumbing*:
* records persisted to backup_records
* checksum + verification flag set
* S3 disabled -> uploaded_to_s3=False
* API endpoint returns history + last successful
"""
from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import backup_service as bs
from app.services.backup_service import BackupService


def _fake_pg_dump_factory(payload: bytes = b"-- fake dump\nSELECT 1;\n"):
    def _fake(_url: str, output_path: Path) -> None:
        with gzip.open(output_path, "wb") as f:
            f.write(payload)
    return _fake


@pytest.mark.asyncio
async def test_backup_disabled_raises(db_session):
    svc = BackupService(db_session)
    with pytest.raises(RuntimeError):
        await svc.create_backup(kind="manual")


@pytest.mark.asyncio
async def test_backup_creates_record_and_verifies(monkeypatch, tmp_path, db_session):
    monkeypatch.setattr(settings, "BACKUP_ENABLED", True)
    monkeypatch.setattr(settings, "BACKUP_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "BACKUP_S3_ENABLED", False)
    monkeypatch.setattr(bs, "_run_pg_dump", _fake_pg_dump_factory())

    svc = BackupService(db_session)
    rec = await svc.create_backup(kind="manual")

    assert rec.status in {"verified", "success"}
    assert rec.verified is True
    assert rec.checksum_sha256 and len(rec.checksum_sha256) == 64
    assert rec.size_bytes and rec.size_bytes > 0
    assert rec.uploaded_to_s3 is False
    assert rec.local_path and Path(rec.local_path).exists()


@pytest.mark.asyncio
async def test_backup_failure_marks_status_failed(monkeypatch, tmp_path, db_session):
    monkeypatch.setattr(settings, "BACKUP_ENABLED", True)
    monkeypatch.setattr(settings, "BACKUP_LOCAL_DIR", str(tmp_path))

    def _boom(*_a, **_k):
        raise RuntimeError("pg_dump: server closed the connection unexpectedly")

    monkeypatch.setattr(bs, "_run_pg_dump", _boom)

    svc = BackupService(db_session)
    rec = await svc.create_backup(kind="scheduled")

    assert rec.status == "failed"
    assert rec.verified is False
    assert rec.error_message and "server closed" in rec.error_message


@pytest.mark.asyncio
async def test_admin_backup_endpoints(monkeypatch, tmp_path, client, admin_headers):
    monkeypatch.setattr(settings, "BACKUP_ENABLED", True)
    monkeypatch.setattr(settings, "BACKUP_LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "BACKUP_S3_ENABLED", False)
    monkeypatch.setattr(bs, "_run_pg_dump", _fake_pg_dump_factory())

    # Empty history
    r = await client.get("/api/v1/monitoring/backups", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["config"]["enabled"] is True
    assert body["last_successful"] is None
    assert body["items"] == []

    # Trigger manual backup
    r = await client.post("/api/v1/monitoring/backups/run", headers=admin_headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] in {"success", "verified"}
    assert rec["verified"] is True

    # History now has 1 entry
    r = await client.get("/api/v1/monitoring/backups", headers=admin_headers)
    body = r.json()
    assert len(body["items"]) == 1
    assert body["last_successful"] is not None


@pytest.mark.asyncio
async def test_manual_backup_rejected_when_disabled(client, admin_headers, monkeypatch):
    monkeypatch.setattr(settings, "BACKUP_ENABLED", False)
    r = await client.post("/api/v1/monitoring/backups/run", headers=admin_headers)
    assert r.status_code == 409
    body = r.json()
    # AppError envelope: {"error": {"code": ..., "message": ..., ...}}
    assert body["error"]["code"] == "backups_disabled"
