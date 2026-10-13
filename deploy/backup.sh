#!/bin/sh
# Online backup of the SQLite database (safe while the app runs), kept for
# KEEP_DAYS days, optionally copied to S3. Run by spashta-backup.timer.
set -eu
cd "$(dirname "$0")"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%d-%H%M)"
mkdir -p data/backups
docker compose exec -T app python -c "
import sqlite3, sys
src = sqlite3.connect('/data/spashta.db')
dst = sqlite3.connect('/data/backups/spashta-$STAMP.db')
src.backup(dst)
dst.close()
ok = sqlite3.connect('/data/backups/spashta-$STAMP.db').execute('PRAGMA integrity_check').fetchone()[0]
sys.exit(0 if ok == 'ok' else 1)
"
gzip -9 "data/backups/spashta-$STAMP.db"
find data/backups -name 'spashta-*.db.gz' -mtime +"$KEEP_DAYS" -delete
if [ -n "${SPASHTA_BACKUP_S3:-}" ]; then
    aws s3 cp "data/backups/spashta-$STAMP.db.gz" "$SPASHTA_BACKUP_S3/" --only-show-errors
fi
echo "backup ok: data/backups/spashta-$STAMP.db.gz"
