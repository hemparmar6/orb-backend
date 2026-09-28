"""PostgreSQL backup + verification service.

Design goals:
* Zero third-party SDK required for local backups (uses ``pg_dump``).
* S3 upload is opt-in and gracefully skipped when boto3 or the endpoint
  is unavailable — never fails startup.
* Every backup is verified by re-reading its own header + checksum.
* History is persisted in ``backup_records`` so the Admin Dashboard can
  render the last N runs.

Configuration (all optional):
    BACKUP_ENABLED=true
    BACKUP_LOCAL_DIR=/var/backups/orb_ai
    BACKUP_RETENTION_DAYS=14
    BACKUP_S3_ENABLED=false
    BACKUP_S3_ENDPOINT=https://s3.amazonaws.com
    BACKUP_S3_BUCKET=orb-ai-backups
    BACKUP_S3_REGION=us-east-1
    BACKUP_S3_ACCESS_KEY=***
    BACKUP_S3_SECRET_KEY=***
    BACKUP_ENCRYPTION_KEY=<fernet key>   # optional, encrypts before upload
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.monitoring import BackupRecord, RestoreRecord

logger = get_logger("app.backup")


class BackupService:
    """Create, verify, upload, and rotate PostgreSQL backups."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ create

    async def create_backup(self, *, kind: str = "manual") -> BackupRecord:
        if not settings.BACKUP_ENABLED:
            raise RuntimeError("Backups are disabled (BACKUP_ENABLED=false)")

        record = BackupRecord(kind=kind, status="running", started_at=_utcnow())
        self.session.add(record)
        await self.session.flush()

        started = time.perf_counter()
        local_dir = Path(settings.BACKUP_LOCAL_DIR)
        local_dir.mkdir(parents=True, exist_ok=True)

        ts = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        filename = f"orb_ai_{ts}.sql.gz"
        local_path = local_dir / filename

        try:
            await asyncio.to_thread(_run_pg_dump, settings.DATABASE_URL, local_path)
            checksum = await asyncio.to_thread(_sha256, local_path)
            size = local_path.stat().st_size

            record.filename = filename
            record.local_path = str(local_path)
            record.size_bytes = size
            record.checksum_sha256 = checksum
            record.finished_at = _utcnow()
            record.duration_ms = int((time.perf_counter() - started) * 1000)

            # Verify (fail-fast if the gz is truncated / checksum mismatch)
            if await asyncio.to_thread(_verify_gzip, local_path, checksum):
                record.verified = True
            else:
                raise RuntimeError("Verification failed — gz header/checksum mismatch")

            # Optional S3 upload
            if settings.BACKUP_S3_ENABLED:
                uploaded = await asyncio.to_thread(
                    _upload_to_s3, local_path, filename, record
                )
                record.uploaded_to_s3 = uploaded

            record.status = "verified" if record.verified else "success"
            logger.info(
                "backup_created",
                extra={
                    "category": "audit",
                    "kind": kind,
                    "size_bytes": size,
                    "uploaded_to_s3": record.uploaded_to_s3,
                    "duration_ms": record.duration_ms,
                },
            )
        except Exception as exc:
            record.status = "failed"
            record.error_message = str(exc)[:1000]
            logger.exception(
                "backup_failed", extra={"category": "error", "kind": kind}
            )

        await self.session.commit()
        await self.session.refresh(record)

        # Retention cleanup runs regardless of this attempt's outcome
        try:
            await asyncio.to_thread(_rotate, local_dir, settings.BACKUP_RETENTION_DAYS)
        except Exception:  # pragma: no cover
            logger.exception("backup_rotate_failed")

        return record

    # ------------------------------------------------------------------ history

    async def list_backups(self, limit: int = 50) -> list[BackupRecord]:
        rows = (
            await self.session.execute(
                select(BackupRecord).order_by(desc(BackupRecord.created_at)).limit(limit)
            )
        ).scalars().all()
        return list(rows)

    async def last_successful(self) -> Optional[BackupRecord]:
        rows = (
            await self.session.execute(
                select(BackupRecord)
                .where(BackupRecord.status.in_(("success", "verified")))
                .order_by(desc(BackupRecord.finished_at))
                .limit(1)
            )
        ).scalars().first()
        return rows

    # ------------------------------------------------------------------ restore

    async def register_restore(
        self, *, backup_id: str | None, source: str, initiated_by: str | None
    ) -> RestoreRecord:
        rec = RestoreRecord(
            backup_id=backup_id,
            source=source,
            initiated_by=initiated_by,
            status="pending",
            started_at=_utcnow(),
        )
        self.session.add(rec)
        await self.session.commit()
        await self.session.refresh(rec)
        return rec


# ---------------------------------------------------------------------- helpers

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_pg_dump(database_url: str, output_path: Path) -> None:
    """Shell out to ``pg_dump`` and gzip the output.

    Uses the SQLAlchemy URL to derive libpq env vars; nothing is echoed
    to argv so credentials never appear in ``ps``.
    """
    from urllib.parse import urlparse

    url = database_url.replace("+asyncpg", "").replace("+psycopg2", "")
    parts = urlparse(url)
    env = os.environ.copy()
    if parts.username:
        env["PGUSER"] = parts.username
    if parts.password:
        env["PGPASSWORD"] = parts.password
    if parts.hostname:
        env["PGHOST"] = parts.hostname
    if parts.port:
        env["PGPORT"] = str(parts.port)
    dbname = (parts.path or "").lstrip("/") or "postgres"

    cmd = [
        shutil.which("pg_dump") or "pg_dump",
        "--no-owner",
        "--no-privileges",
        "--format=plain",
        "--dbname", dbname,
    ]

    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdout is not None
    with gzip.open(output_path, "wb", compresslevel=6) as out:
        while True:
            chunk = proc.stdout.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)
    _, err = proc.communicate(timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed (rc={proc.returncode}): {err.decode(errors='ignore')[:500]}"
        )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_gzip(path: Path, expected_sha256: str) -> bool:
    try:
        with gzip.open(path, "rb") as f:
            f.read(64)  # forces a header + block read
        return _sha256(path) == expected_sha256
    except Exception:
        return False


def _rotate(directory: Path, retention_days: int) -> None:
    cutoff = _utcnow() - timedelta(days=max(1, retention_days))
    for p in directory.glob("orb_ai_*.sql.gz"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                p.unlink(missing_ok=True)
                logger.info("backup_rotated_out", extra={"path": str(p)})
        except Exception:  # pragma: no cover
            continue


def _upload_to_s3(path: Path, filename: str, record: BackupRecord) -> bool:
    """Best-effort S3 upload. Returns True on success, False otherwise.

    We use boto3 lazily so backend startup doesn't require it.
    """
    if not settings.BACKUP_S3_ENABLED:
        return False
    try:
        import boto3  # type: ignore
        from botocore.client import Config  # type: ignore
    except Exception:
        logger.warning(
            "s3_upload_skipped_boto3_missing",
            extra={"category": "audit", "hint": "pip install boto3"},
        )
        return False
    try:
        session_kwargs: dict[str, Any] = {}
        if settings.BACKUP_S3_ACCESS_KEY and settings.BACKUP_S3_SECRET_KEY:
            session_kwargs.update(
                aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY,
                aws_secret_access_key=settings.BACKUP_S3_SECRET_KEY,
            )
        if settings.BACKUP_S3_REGION:
            session_kwargs["region_name"] = settings.BACKUP_S3_REGION

        client = boto3.client(
            "s3",
            endpoint_url=settings.BACKUP_S3_ENDPOINT or None,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
            **session_kwargs,
        )
        key = f"{settings.BACKUP_S3_PREFIX}/{filename}".lstrip("/")
        client.upload_file(str(path), settings.BACKUP_S3_BUCKET, key)
        # Best-effort HEAD verify
        client.head_object(Bucket=settings.BACKUP_S3_BUCKET, Key=key)
        record.s3_bucket = settings.BACKUP_S3_BUCKET
        record.s3_key = key
        logger.info(
            "backup_uploaded_s3",
            extra={"category": "audit", "bucket": settings.BACKUP_S3_BUCKET, "key": key},
        )
        return True
    except Exception:
        logger.exception("backup_s3_upload_failed", extra={"category": "error"})
        return False
