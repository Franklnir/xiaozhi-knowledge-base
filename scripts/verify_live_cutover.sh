#!/bin/sh
set -eu

BACKUP_DIR="${1:?verified backup directory is required}"
APP_ROOT="${2:-/opt/xiaozhi}"

case "$BACKUP_DIR" in
    "$APP_ROOT"/backups/security-*) ;;
    *) echo "Unsafe backup path: $BACKUP_DIR" >&2; exit 1 ;;
esac

DB_ADMIN_USER="$(docker exec xiaozhi-postgres printenv POSTGRES_USER)"
DB_NAME="$(docker exec xiaozhi-postgres printenv POSTGRES_DB)"
RUNTIME_USER="$(docker exec xiaozhi printenv POSTGRES_USER)"

case "$DB_ADMIN_USER:$DB_NAME:$RUNTIME_USER" in
    *[!A-Za-z0-9_:.-]*) echo "Unexpected database identifier" >&2; exit 1 ;;
esac

COUNT_SQL="
SELECT 'users=' || count(*) FROM users
UNION ALL SELECT 'products=' || count(*) FROM firmware_products
UNION ALL SELECT 'orders=' || count(*) FROM orders
UNION ALL SELECT 'entitlements=' || count(*) FROM purchase_entitlements
UNION ALL SELECT 'device_tokens=' || count(*) FROM xiaozhi_tokens
UNION ALL SELECT 'relay_rooms=' || count(*) FROM relay_rooms
ORDER BY 1;
"

test "$(docker inspect --format '{{.State.Health.Status}}' xiaozhi)" = "healthy"
docker exec -e PYTHONPATH=/app xiaozhi python /app/scripts/smoke_runtime_role.py

docker exec xiaozhi-postgres psql -At -U "$DB_ADMIN_USER" -d "$DB_NAME" \
    -c "$COUNT_SQL" >"$BACKUP_DIR/counts.after.txt"
chmod 600 "$BACKUP_DIR/counts.after.txt"
cmp "$BACKUP_DIR/counts.before.txt" "$BACKUP_DIR/counts.after.txt" >/dev/null

docker exec xiaozhi-postgres psql -At -U "$DB_ADMIN_USER" -d "$DB_NAME" \
    -c "SELECT version_num FROM alembic_version" >"$BACKUP_DIR/alembic.after.txt"
chmod 600 "$BACKUP_DIR/alembic.after.txt"

docker exec xiaozhi-postgres psql -At -U "$DB_ADMIN_USER" -d "$DB_NAME" \
    -c "SELECT rolname || ':' || rolsuper || ':' || rolbypassrls FROM pg_roles WHERE rolname IN ('$RUNTIME_USER', '$DB_ADMIN_USER') ORDER BY rolname"

stat -c '%a %n' \
    "$APP_ROOT/.env" \
    "$APP_ROOT/.db-admin.env" \
    "$APP_ROOT/docker-compose.yml" \
    "$BACKUP_DIR" \
    "$BACKUP_DIR/rotated-relay-credentials.tsv"

docker inspect --format \
    'health={{.State.Health.Status}} readonly={{.HostConfig.ReadonlyRootfs}} pids={{.HostConfig.PidsLimit}} caps={{json .HostConfig.CapDrop}} security={{json .HostConfig.SecurityOpt}}' \
    xiaozhi

echo "DATA_COUNTS_MATCH"
cat "$BACKUP_DIR/counts.after.txt"
echo "ALEMBIC_VERSION"
cat "$BACKUP_DIR/alembic.after.txt"
