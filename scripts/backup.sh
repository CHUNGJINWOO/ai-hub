#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="/mnt/data/ai-hub"
BACKUP_ROOT="${PROJECT_ROOT}/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

DB_BACKUP_DIR="${BACKUP_ROOT}/db"
DOCUMENT_BACKUP_DIR="${BACKUP_ROOT}/documents"

mkdir -p "${DB_BACKUP_DIR}" "${DOCUMENT_BACKUP_DIR}"

DB_BACKUP="${DB_BACKUP_DIR}/aihub_${TIMESTAMP}.dump"
DOCUMENT_BACKUP="${DOCUMENT_BACKUP_DIR}/documents_${TIMESTAMP}.tar.gz"

echo "[1/4] PostgreSQL backup"
docker compose -f "${PROJECT_ROOT}/compose.yml" exec -T postgres \
    sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
    > "${DB_BACKUP}"

echo "[2/4] Uploaded documents backup"
tar -czf "${DOCUMENT_BACKUP}" \
    -C "${PROJECT_ROOT}/documents" .

echo "[3/4] Verify backup files"

test -s "${DB_BACKUP}"
test -s "${DOCUMENT_BACKUP}"

docker compose -f "${PROJECT_ROOT}/compose.yml" exec -T postgres \
    pg_restore --list < "${DB_BACKUP}" > /dev/null

tar -tzf "${DOCUMENT_BACKUP}" > /dev/null

echo "[4/4] Backup completed"
echo "DB:        ${DB_BACKUP}"
echo "Documents: ${DOCUMENT_BACKUP}"
