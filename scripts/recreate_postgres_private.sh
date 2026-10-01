#!/bin/sh
set -eu

APP_ROOT="${1:-/opt/xiaozhi}"
BACKUP_DIR="${2:?verified backup directory is required}"

case "$BACKUP_DIR" in
    "$APP_ROOT"/backups/security-*) ;;
    *) echo "Unsafe backup path: $BACKUP_DIR" >&2; exit 1 ;;
esac

cd "$APP_ROOT"
test -s "$BACKUP_DIR/database.dump"
test -s "$APP_ROOT/.db-admin.env"
test "$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' xiaozhi-postgres)" = "xiaozhi_pgdata"
test -z "$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["postgres"].get("ports") or "")')"

read_env() {
    key="$1"
    file="$2"
    value="$(sed -n "s/^${key}=//p" "$file" | tail -n 1)"
    test -n "$value"
    printf '%s' "$value"
}

DB_ADMIN_USER="$(read_env POSTGRES_USER "$APP_ROOT/.db-admin.env")"
DB_NAME="$(read_env POSTGRES_DB "$APP_ROOT/.db-admin.env")"
APP_IMAGE="$(docker inspect --format '{{.Config.Image}}' xiaozhi)"
case "$APP_IMAGE" in
    ghcr.io/franklnir/xiaozhi:*) ;;
    *) echo "Unexpected application image: $APP_IMAGE" >&2; exit 1 ;;
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

docker exec xiaozhi-postgres psql -At -U "$DB_ADMIN_USER" -d "$DB_NAME" \
    -c "$COUNT_SQL" >"$BACKUP_DIR/counts.postgres-private.before.txt"
chmod 600 "$BACKUP_DIR/counts.postgres-private.before.txt"

docker stop xiaozhi >/dev/null
docker compose up -d --force-recreate postgres

attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' xiaozhi-postgres 2>/dev/null || true)" = "healthy" ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        docker compose logs --tail 120 postgres
        exit 1
    fi
    sleep 3
done

XIAOZHI_IMAGE="$APP_IMAGE" docker compose up -d --no-deps xiaozhi
attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' xiaozhi 2>/dev/null || true)" = "healthy" ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        docker compose logs --tail 120 xiaozhi
        exit 1
    fi
    sleep 4
done

test -z "$(docker port xiaozhi-postgres)"
docker exec xiaozhi-postgres psql -At -U "$DB_ADMIN_USER" -d "$DB_NAME" \
    -c "$COUNT_SQL" >"$BACKUP_DIR/counts.postgres-private.after.txt"
chmod 600 "$BACKUP_DIR/counts.postgres-private.after.txt"
cmp "$BACKUP_DIR/counts.postgres-private.before.txt" \
    "$BACKUP_DIR/counts.postgres-private.after.txt" >/dev/null

docker exec -e PYTHONPATH=/app xiaozhi python /app/scripts/smoke_runtime_role.py
echo "POSTGRES_PRIVATE_RECREATE_COMPLETE"
