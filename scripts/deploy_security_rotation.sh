#!/bin/sh
set -eu

IMAGE="${1:?immutable image tag is required}"
BACKUP_DIR="${2:?verified backup directory is required}"
APP_ROOT="${3:-/opt/xiaozhi}"
STAGE="initialization"

case "$BACKUP_DIR" in
    "$APP_ROOT"/backups/security-*) ;;
    *) echo "Unsafe backup path: $BACKUP_DIR" >&2; exit 1 ;;
esac

failed() {
    status=$?
    trap - INT TERM HUP EXIT
    echo "SECURITY_DEPLOY_FAILED stage=$STAGE status=$status" >&2
    exit "$status"
}
trap failed INT TERM HUP EXIT

cd "$APP_ROOT"
test -s "$BACKUP_DIR/database.dump"
test -s .env.new
test -s .rotation.env
test -s .db-admin.env.new
test -s docker-compose.next.yml
test -s xiaozhi/protected_assets/firmware/esp32_s3_n16r8_cam_full_factory.bin.enc.new

DB_ADMIN_USER="$(docker exec xiaozhi-postgres printenv POSTGRES_USER)"
DB_NAME="$(docker exec xiaozhi-postgres printenv POSTGRES_DB)"
COUNT_SQL="
SELECT 'users=' || count(*) FROM users
UNION ALL SELECT 'products=' || count(*) FROM firmware_products
UNION ALL SELECT 'orders=' || count(*) FROM orders
UNION ALL SELECT 'entitlements=' || count(*) FROM purchase_entitlements
UNION ALL SELECT 'device_tokens=' || count(*) FROM xiaozhi_tokens
UNION ALL SELECT 'relay_rooms=' || count(*) FROM relay_rooms
ORDER BY 1;
"

STAGE="snapshot-before"
docker exec xiaozhi-postgres psql -At -U "$DB_ADMIN_USER" -d "$DB_NAME" \
    -c "$COUNT_SQL" >"$BACKUP_DIR/counts.before.txt"
chmod 600 "$BACKUP_DIR/counts.before.txt"

STAGE="stop-application"
docker stop xiaozhi >/dev/null

STAGE="restore-lfs-firmware"
mv xiaozhi/protected_assets/firmware/esp32_s3_n16r8_cam_full_factory.bin.enc.new \
   xiaozhi/protected_assets/firmware/esp32_s3_n16r8_cam_full_factory.bin.enc
find xiaozhi/protected_assets/firmware data/marketplace_storage/private/presets \
    -type f -name '*.bin.enc' -exec chmod 600 {} \;

STAGE="database-migration"
docker run --rm --network xiaozhi_default \
    --env-file .env.new \
    --env-file .rotation.env \
    "$IMAGE" alembic upgrade head

STAGE="secret-and-credential-rotation"
docker run --rm --user "$(id -u):$(id -g)" --network xiaozhi_default \
    --env-file .rotation.env \
    -e FIRMWARE_SEARCH_ROOTS=/workspace/xiaozhi/protected_assets/firmware:/workspace/data/marketplace_storage/private/presets \
    -e ALLOW_UNRECOVERABLE_MATERIAL_RESET=true \
    -e ROTATED_CREDENTIALS_OUTPUT="/workspace/backups/$(basename "$BACKUP_DIR")/rotated-relay-credentials.tsv" \
    -v "$APP_ROOT:/workspace" \
    -w /workspace \
    "$IMAGE" python scripts/rotate_production_secrets.py

STAGE="rotation-verification"
docker run --rm --user "$(id -u):$(id -g)" --network xiaozhi_default \
    --env-file .rotation.env \
    --env-file .env.new \
    -e FIRMWARE_SEARCH_ROOTS=/workspace/xiaozhi/protected_assets/firmware:/workspace/data/marketplace_storage/private/presets \
    -v "$APP_ROOT:/workspace:ro" \
    -w /workspace \
    "$IMAGE" python scripts/verify_secret_rotation.py

STAGE="activate-configuration"
mv .env "$BACKUP_DIR/env.activation-original"
mv docker-compose.yml "$BACKUP_DIR/docker-compose.activation-original.yml"
mv .rotation.env "$BACKUP_DIR/rotation.env.used"
mv .env.new .env
mv .db-admin.env.new .db-admin.env
mv docker-compose.next.yml docker-compose.yml
chmod 600 .env .db-admin.env docker-compose.yml
find "$BACKUP_DIR" -type d -exec chmod 700 {} \;
find "$BACKUP_DIR" -type f -exec chmod 600 {} \;

STAGE="start-hardened-application"
XIAOZHI_IMAGE="$IMAGE" docker compose up -d --no-deps xiaozhi

STAGE="health-check"
attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' xiaozhi 2>/dev/null || true)" = "healthy" ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        docker compose logs --tail 120 xiaozhi
        exit 1
    fi
    sleep 4
done

curl --fail --silent --show-error -H 'Host: xiaozhiscig.biz.id' \
    http://127.0.0.1:8080/login >/dev/null

STAGE="runtime-role-smoke"
docker exec -e PYTHONPATH=/app xiaozhi python /app/scripts/smoke_runtime_role.py

STAGE="snapshot-after"
docker exec xiaozhi-postgres psql -At -U "$DB_ADMIN_USER" -d "$DB_NAME" \
    -c "$COUNT_SQL" >"$BACKUP_DIR/counts.after.txt"
chmod 600 "$BACKUP_DIR/counts.after.txt"
cmp "$BACKUP_DIR/counts.before.txt" "$BACKUP_DIR/counts.after.txt" >/dev/null
docker exec xiaozhi-postgres psql -At -U "$DB_ADMIN_USER" -d "$DB_NAME" \
    -c "SELECT version_num FROM alembic_version" >"$BACKUP_DIR/alembic.after.txt"
chmod 600 "$BACKUP_DIR/alembic.after.txt"

STAGE="complete"
trap - INT TERM HUP EXIT
echo "SECURITY_DEPLOY_COMPLETE"
cat "$BACKUP_DIR/counts.after.txt"
cat "$BACKUP_DIR/alembic.after.txt"
