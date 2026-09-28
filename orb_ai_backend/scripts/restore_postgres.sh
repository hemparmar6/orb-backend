#!/usr/bin/env bash
# ORB AI — PostgreSQL restore helper (Module 10)
#
# Restores a backup produced by backup_postgres.sh. The target database
# is DROPPED and recreated, so this MUST be run against the correct
# environment. Requires the POSTGRES_* env vars.
#
# Usage:
#   ./scripts/restore_postgres.sh /var/backups/orb_ai/orb_ai_20260215T030000Z.sql.gz
#   ./scripts/restore_postgres.sh --from-s3 orb_ai_20260215T030000Z.sql.gz

set -euo pipefail

: "${POSTGRES_HOST:=localhost}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${BACKUP_LOCAL_DIR:=/var/backups/orb_ai}"
: "${BACKUP_S3_PREFIX:=postgres}"

SOURCE="${1:-}"
if [[ -z "$SOURCE" ]]; then
    echo "usage: $0 <path-to-backup>|--from-s3 <filename>" >&2
    exit 1
fi

if [[ "$SOURCE" == "--from-s3" ]]; then
    FILE="${2:?filename required}"
    LOCAL="${BACKUP_LOCAL_DIR}/${FILE}"
    mkdir -p "$BACKUP_LOCAL_DIR"
    S3_URI="s3://${BACKUP_S3_BUCKET:?}/${BACKUP_S3_PREFIX}/${FILE}"
    echo ">> downloading ${S3_URI} -> ${LOCAL}"
    AWS_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY:-}" \
    AWS_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_KEY:-}" \
    AWS_DEFAULT_REGION="${BACKUP_S3_REGION:-us-east-1}" \
    aws s3 cp "$S3_URI" "$LOCAL" \
        ${BACKUP_S3_ENDPOINT:+--endpoint-url "$BACKUP_S3_ENDPOINT"}
    SOURCE="$LOCAL"
fi

[[ -f "$SOURCE" ]] || { echo "!! not found: $SOURCE" >&2; exit 2; }

echo ">> verifying gzip"
gunzip -t "$SOURCE"

echo ">> WARNING — this will DROP and recreate database '${POSTGRES_DB}'"
if [[ "${FORCE:-false}" != "true" ]]; then
    read -r -p "Type YES to continue: " CONFIRM
    [[ "$CONFIRM" == "YES" ]] || { echo "aborted."; exit 3; }
fi

export PGPASSWORD="${POSTGRES_PASSWORD:-}"

echo ">> terminating existing connections"
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${POSTGRES_DB}' AND pid <> pg_backend_pid();" >/dev/null

echo ">> dropping ${POSTGRES_DB}"
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c \
    "DROP DATABASE IF EXISTS ${POSTGRES_DB};"

echo ">> creating ${POSTGRES_DB}"
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres -c \
    "CREATE DATABASE ${POSTGRES_DB};"

echo ">> restoring"
gunzip -c "$SOURCE" | psql \
    -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB"

echo ">> DONE — verify: psql \\l"
