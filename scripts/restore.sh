#!/bin/sh
# Restore a LinkForge backup. DESTRUCTIVE: replaces the contents of the target database.
# Usage: CONFIRM_RESTORE=yes sh scripts/restore.sh <backup.dump> [target-database-url]
# The target defaults to DATABASE_URL. Restore into a scratch database first to rehearse.
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: CONFIRM_RESTORE=yes sh scripts/restore.sh <backup.dump> [target-database-url]" >&2
  exit 2
fi

dump="$1"
target="${2:-${DATABASE_URL:-}}"

if [ ! -f "$dump" ]; then
  echo "Backup file not found: $dump" >&2
  exit 2
fi
if [ -z "$target" ]; then
  echo "Provide a target database URL or set DATABASE_URL" >&2
  exit 2
fi

if [ "${CONFIRM_RESTORE:-}" != "yes" ]; then
  echo "Refusing to restore: this replaces the data in the target database." >&2
  echo "Re-run with CONFIRM_RESTORE=yes to proceed." >&2
  exit 3
fi

command -v pg_restore > /dev/null 2>&1 || {
  echo "pg_restore not found; install the PostgreSQL client tools" >&2
  exit 2
}

# Fail early on a corrupt or wrong file, before touching the database
pg_restore --list "$dump" > /dev/null

# One transaction: a failed restore leaves the target exactly as it was
pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction \
  --dbname="$target" "$dump"

echo "Restore complete. Next: apply migrations, then check /health/ready."