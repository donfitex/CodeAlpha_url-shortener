#!/bin/sh
# Create a verified PostgreSQL backup of the LinkForge database.
# Usage: DATABASE_URL=postgresql://... BACKUP_DIR=/var/backups/linkforge sh scripts/backup.sh
# Optional: BACKUP_KEEP=14 (how many of the newest backups to keep)
set -eu

: "${DATABASE_URL:?DATABASE_URL must be set}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

case "$BACKUP_KEEP" in
  '' | *[!0-9]*)
    echo "BACKUP_KEEP must be a whole number" >&2
    exit 2
    ;;
esac
if [ "$BACKUP_KEEP" -lt 1 ]; then
  echo "BACKUP_KEEP must be at least 1" >&2
  exit 2
fi

for tool in pg_dump pg_restore; do
  command -v "$tool" > /dev/null 2>&1 || {
    echo "$tool not found; install the PostgreSQL client tools" >&2
    exit 2
  }
done

umask 077
mkdir -p "$BACKUP_DIR"

stamp="$(date -u +%Y%m%d-%H%M%SZ)"
file="$BACKUP_DIR/linkforge-$stamp.dump"
partial="$file.partial"
trap 'rm -f "$partial"' EXIT

pg_dump --format=custom --no-owner --no-privileges --file="$partial" "$DATABASE_URL"

# Keep the archive only if it is readable and really contains the urls table
if ! pg_restore --list "$partial" | grep -Eq 'TABLE public urls( |$)'; then
  echo "Backup verification failed: the archive does not contain the urls table" >&2
  exit 1
fi

mv "$partial" "$file"
trap - EXIT

ls -1t "$BACKUP_DIR"/linkforge-*.dump | tail -n +"$((BACKUP_KEEP + 1))" | while read -r old; do
  rm -f -- "$old"
done

echo "Backup written: $file ($(wc -c < "$file") bytes). Keeping the newest $BACKUP_KEEP."