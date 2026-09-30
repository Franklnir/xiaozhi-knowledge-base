#!/bin/sh
set -eu

APP_ROOT="${1:-/opt/xiaozhi}"
WORK_DIR="$(mktemp -d /tmp/xiaozhi-rotation-clone.XXXXXX)"
SUFFIX="$$"
NETWORK_NAME="xiaozhi-rotation-${SUFFIX}"
PG_CONTAINER="xiaozhi-rotation-pg-${SUFFIX}"
APP_IMAGE="$(docker inspect -f '{{.Config.Image}}' xiaozhi)"

case "$WORK_DIR" in
    /tmp/xiaozhi-rotation-clone.*) ;;
    *) echo "Unsafe temporary directory: $WORK_DIR" >&2; exit 1 ;;
esac

cleanup() {
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
    case "$WORK_DIR" in
        /tmp/xiaozhi-rotation-clone.*) rm -rf -- "$WORK_DIR" ;;
    esac
}
trap cleanup EXIT INT TERM

chmod 700 "$WORK_DIR"
mkdir -m 700 "$WORK_DIR/firmware" "$WORK_DIR/firmware/core" "$WORK_DIR/firmware/presets"
mkdir -m 700 "$WORK_DIR/source"
tar -xzf /tmp/xiaozhi-security-test.tar.gz -C "$WORK_DIR/source"
cp -a "$APP_ROOT/xiaozhi/protected_assets/firmware/." "$WORK_DIR/firmware/core/"
cp -a "$APP_ROOT/data/marketplace_storage/private/presets/." "$WORK_DIR/firmware/presets/"
CORE_FIRMWARE="$WORK_DIR/firmware/core/esp32_s3_n16r8_cam_full_factory.bin.enc"
if grep -q '^version https://git-lfs.github.com/spec/' "$CORE_FIRMWARE" 2>/dev/null; then
    test -f /tmp/esp32_s3_n16r8_cam_full_factory.bin.enc
    cp /tmp/esp32_s3_n16r8_cam_full_factory.bin.enc "$CORE_FIRMWARE"
fi

docker exec xiaozhi-postgres sh -c \
    'exec pg_dump --no-owner --no-acl --format=custom -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    >"$WORK_DIR/production.dump"
chmod 600 "$WORK_DIR/production.dump"

python3 /tmp/prepare_production_rotation.py \
    --current-env "$APP_ROOT/.env" \
    --legacy-firmware-source "$APP_ROOT/xiaozhi/services/preset_approval_service.py" \
    --new-env "$WORK_DIR/new.env" \
    --rotation-env "$WORK_DIR/rotation.env" \
    --db-admin-env "$WORK_DIR/db-admin.env" \
    --recovery-file "$WORK_DIR/recovery.txt" \
    --firmware-roots /firmware

docker network create "$NETWORK_NAME" >/dev/null
docker run -d --name "$PG_CONTAINER" --network "$NETWORK_NAME" \
    -e POSTGRES_DB=xiaozhi_rotation \
    -e POSTGRES_USER=rotation_test_app \
    -e POSTGRES_PASSWORD=isolated-old-password \
    postgres:16-alpine >/dev/null

ready=0
for _ in $(seq 1 30); do
    if docker exec "$PG_CONTAINER" pg_isready -U rotation_test_app -d xiaozhi_rotation >/dev/null 2>&1 \
       && docker exec "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -U rotation_test_app -d xiaozhi_rotation -c 'SELECT 1' >/dev/null 2>&1; then
        sleep 2
        if docker exec "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -U rotation_test_app -d xiaozhi_rotation -c 'SELECT 1' >/dev/null 2>&1; then
            ready=1
            break
        fi
    fi
    sleep 1
done
test "$ready" -eq 1
docker exec -i "$PG_CONTAINER" pg_restore --exit-on-error --no-owner --no-acl \
    -U rotation_test_app -d xiaozhi_rotation <"$WORK_DIR/production.dump" >/dev/null

COUNT_SQL="SELECT (SELECT count(*) FROM users), (SELECT count(*) FROM materials), (SELECT count(*) FROM xiaozhi_tokens), (SELECT count(*) FROM relay_rooms), (SELECT count(*) FROM withdrawals);"
docker exec "$PG_CONTAINER" psql -At -U rotation_test_app -d xiaozhi_rotation -c "$COUNT_SQL" >"$WORK_DIR/counts-before"

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
docker run --rm --user "$HOST_UID:$HOST_GID" --network "$NETWORK_NAME" \
    --env-file "$WORK_DIR/rotation.env" \
    -e POSTGRES_HOST="$PG_CONTAINER" \
    -e POSTGRES_PORT=5432 \
    -e POSTGRES_DB=xiaozhi_rotation \
    -e POSTGRES_USER=rotation_test_app \
    -e POSTGRES_PASSWORD=isolated-old-password \
    -e POSTGRES_ADMIN_USER=rotation_test_app \
    -e NEW_POSTGRES_USER=rotation_test_runtime \
    -e FIRMWARE_SEARCH_ROOTS=/firmware \
    -e ALLOW_UNRECOVERABLE_MATERIAL_RESET=true \
    -e ROTATED_CREDENTIALS_OUTPUT=/rotation-work/rotated-credentials.tsv \
    -v /tmp/rotate_production_secrets.py:/scripts/rotate_production_secrets.py:ro \
    -v "$WORK_DIR:/rotation-work" \
    -v "$WORK_DIR/firmware:/firmware" \
    "$APP_IMAGE" python3 /scripts/rotate_production_secrets.py

docker exec "$PG_CONTAINER" psql -At -U rotation_test_app -d xiaozhi_rotation -c "$COUNT_SQL" >"$WORK_DIR/counts-after"
cmp "$WORK_DIR/counts-before" "$WORK_DIR/counts-after" >/dev/null

docker run --rm --user "$HOST_UID:$HOST_GID" --network "$NETWORK_NAME" \
    --env-file "$WORK_DIR/rotation.env" \
    --env-file "$WORK_DIR/new.env" \
    -e POSTGRES_HOST="$PG_CONTAINER" \
    -e POSTGRES_PORT=5432 \
    -e POSTGRES_DB=xiaozhi_rotation \
    -e POSTGRES_USER=rotation_test_runtime \
    -e POSTGRES_ADMIN_USER=rotation_test_app \
    -e OLD_POSTGRES_PASSWORD=isolated-old-password \
    -e FIRMWARE_SEARCH_ROOTS=/firmware \
    -v /tmp/verify_secret_rotation.py:/scripts/verify_secret_rotation.py:ro \
    -v "$WORK_DIR/firmware:/firmware:ro" \
    "$APP_IMAGE" python3 /scripts/verify_secret_rotation.py

docker run --rm --user "$HOST_UID:$HOST_GID" --network "$NETWORK_NAME" \
    --env-file "$WORK_DIR/new.env" \
    -e POSTGRES_HOST="$PG_CONTAINER" \
    -e POSTGRES_PORT=5432 \
    -e POSTGRES_DB=xiaozhi_rotation \
    -e POSTGRES_USER=rotation_test_runtime \
    -e ENVIRONMENT=production \
    -e DB_AUTO_INIT_SCHEMA=false \
    -e PYTHONPATH=/source \
    -v "$WORK_DIR/source:/source:ro" \
    -w /source \
    "$APP_IMAGE" python3 scripts/smoke_runtime_role.py

echo "Production-clone rotation test passed; row counts are unchanged."
