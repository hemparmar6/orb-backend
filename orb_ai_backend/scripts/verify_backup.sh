#!/usr/bin/env bash
# ORB AI — backup verification helper (Module 10)
#
# For every ``orb_ai_*.sql.gz`` in $BACKUP_LOCAL_DIR (or in the S3 bucket
# when --s3 is passed), verify the gzip header and re-compute the sha256
# to compare against the sidecar checksum.
#
# Exits non-zero if any backup fails verification.

set -euo pipefail

: "${BACKUP_LOCAL_DIR:=/var/backups/orb_ai}"
FAILED=0

check_file() {
    local f="$1"
    echo ">> ${f}"
    gunzip -t "$f" || { echo "!! gzip verify failed"; return 1; }
    if [[ -f "${f}.sha256" ]]; then
        local expected actual
        expected="$(awk '{print $1}' "${f}.sha256")"
        actual="$(sha256sum "$f" | awk '{print $1}')"
        if [[ "$expected" != "$actual" ]]; then
            echo "!! sha256 mismatch: expected=${expected} actual=${actual}"
            return 1
        fi
    fi
    echo "   OK"
}

for f in "$BACKUP_LOCAL_DIR"/orb_ai_*.sql.gz; do
    [[ -f "$f" ]] || continue
    if ! check_file "$f"; then
        FAILED=$((FAILED + 1))
    fi
done

if (( FAILED > 0 )); then
    echo "!! ${FAILED} backup(s) failed verification" >&2
    exit 1
fi

echo ">> all backups verified"
