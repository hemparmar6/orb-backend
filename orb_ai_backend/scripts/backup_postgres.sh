#!/usr/bin/env bash
# ORB AI — PostgreSQL backup helper (Module 10)
#
# Produces a gzipped SQL dump under $BACKUP_LOCAL_DIR (default /var/backups/orb_ai)
# and, when BACKUP_S3_ENABLED=true, uploads the file to the configured bucket.
#
# Usage:
#   ./scripts/backup_postgres.sh                # relies on env
#   BACKUP_LOCAL_DIR=/tmp ./scripts/backup_postgres.sh
#
# Env vars (read from environment / .env):
#   POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
#   BACKUP_LOCAL_DIR   (default /var/backups/orb_ai)
#   BACKUP_RETENTION_DAYS (default 14)
#   BACKUP_S3_ENABLED, BACKUP_S3_ENDPOINT, BACKUP_S3_BUCKET,
#   BACKUP_S3_REGION, BACKUP_S3_ACCESS_KEY, BACKUP_S3_SECRET_KEY,
#   BACKUP_S3_PREFIX (default postgres)

set -euo pipefail

: "${POSTGRES_HOST:=localhost}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${BACKUP_LOCAL_DIR:=/var/backups/orb_ai}"
: "${BACKUP_RETENTION_DAYS:=14}"
: "${BACKUP_S3_ENABLED:=false}"
: "${BACKUP_S3_PREFIX:=postgres}"

mkdir -p "$BACKUP_LOCAL_DIR"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="orb_ai_${TS}.sql.gz"
DEST="${BACKUP_LOCAL_DIR}/${FILE}"

echo ">> pg_dump -> ${DEST}"
PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
    --no-owner --no-privileges --format=plain \
    "$POSTGRES_DB" | gzip -6 > "$DEST"

echo ">> sha256"
SHA="$(sha256sum "$DEST" | awk '{print $1}')"
echo "$SHA  $FILE" > "${DEST}.sha256"

echo ">> verify"
gunzip -t "$DEST"

echo ">> rotate (>${BACKUP_RETENTION_DAYS} days)"
find "$BACKUP_LOCAL_DIR" -maxdepth 1 -type f -name 'orb_ai_*.sql.gz' \
     -mtime "+${BACKUP_RETENTION_DAYS}" -print -delete || true

if [[ "$BACKUP_S3_ENABLED" == "true" ]]; then
    if ! command -v aws >/dev/null 2>&1; then
        echo "!! aws CLI not installed — skipping S3 upload"
    else
        S3_URI="s3://${BACKUP_S3_BUCKET:?BACKUP_S3_BUCKET required}/${BACKUP_S3_PREFIX}/${FILE}"
        echo ">> uploading ${DEST} -> ${S3_URI}"
        AWS_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY:-}" \
        AWS_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_KEY:-}" \
        AWS_DEFAULT_REGION="${BACKUP_S3_REGION:-us-east-1}" \
        aws s3 cp "$DEST" "$S3_URI" \
            ${BACKUP_S3_ENDPOINT:+--endpoint-url "$BACKUP_S3_ENDPOINT"}
        echo ">> S3 upload OK"
    fi
fi

echo ">> DONE  file=${FILE}  sha256=${SHA}"
