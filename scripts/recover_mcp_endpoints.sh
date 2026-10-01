#!/bin/sh
set -eu

BACKUP_DIR="${1:?verified backup directory is required}"
IMAGE="${2:?immutable image tag is required}"
APP_ROOT="${3:-/opt/xiaozhi}"
RECOVERY_CONTAINER="xiaozhi-mcp-recovery-postgres"
RECOVERY_ENV="$BACKUP_DIR/.mcp-recovery.env"
SOURCE_ENV="$BACKUP_DIR/.mcp-recovery-source.env"

case "$BACKUP_DIR" in
    "$APP_ROOT"/backups/security-*) ;;
    *) echo "Unsafe backup path: $BACKUP_DIR" >&2; exit 1 ;;
esac

cleanup() {
    trap - INT TERM HUP EXIT
    docker rm -f "$RECOVERY_CONTAINER" >/dev/null 2>&1 || true
    rm -f "$RECOVERY_ENV" "$SOURCE_ENV"
}
trap cleanup INT TERM HUP EXIT

read_env() {
    key="$1"
    file="$2"
    value="$(sed -n "s/^${key}=//p" "$file" | tail -n 1)"
    test -n "$value"
    printf '%s' "$value"
}

test -s "$BACKUP_DIR/database.dump"
test -s "$BACKUP_DIR/rotation.env.used"
test -s "$APP_ROOT/.env"
test -s "$APP_ROOT/.db-admin.env"
test -s "$APP_ROOT/scripts/recover_mcp_endpoints.py"
if docker inspect "$RECOVERY_CONTAINER" >/dev/null 2>&1; then
    echo "Recovery container already exists; refusing ambiguous cleanup" >&2
    exit 1
fi

RECOVERY_PASSWORD="$(openssl rand -hex 32)"
umask 077
printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_DB=recovery\n' "$RECOVERY_PASSWORD" >"$SOURCE_ENV"
printf '%s\n' \
    "SOURCE_POSTGRES_HOST=$RECOVERY_CONTAINER" \
    "SOURCE_POSTGRES_PORT=5432" \
    "SOURCE_POSTGRES_DB=recovery" \
    "SOURCE_POSTGRES_USER=postgres" \
    "SOURCE_POSTGRES_PASSWORD=$RECOVERY_PASSWORD" \
    "TARGET_POSTGRES_HOST=$(read_env POSTGRES_HOST "$APP_ROOT/.db-admin.env")" \
    "TARGET_POSTGRES_PORT=$(read_env POSTGRES_PORT "$APP_ROOT/.db-admin.env")" \
    "TARGET_POSTGRES_DB=$(read_env POSTGRES_DB "$APP_ROOT/.db-admin.env")" \
    "TARGET_POSTGRES_USER=$(read_env POSTGRES_USER "$APP_ROOT/.db-admin.env")" \
    "TARGET_POSTGRES_PASSWORD=$(read_env POSTGRES_PASSWORD "$APP_ROOT/.db-admin.env")" \
    "OLD_DATA_ENCRYPTION_KEY=$(read_env OLD_DATA_ENCRYPTION_KEY "$BACKUP_DIR/rotation.env.used")" \
    "DATA_ENCRYPTION_KEY=$(read_env DATA_ENCRYPTION_KEY "$APP_ROOT/.env")" \
    "MCP_TOKEN_HASH_LENGTH=16" >"$RECOVERY_ENV"
chmod 600 "$SOURCE_ENV" "$RECOVERY_ENV"

docker run -d --name "$RECOVERY_CONTAINER" --network xiaozhi_default \
    --env-file "$SOURCE_ENV" postgres:16-alpine >/dev/null
attempt=0
until docker exec "$RECOVERY_CONTAINER" pg_isready -U postgres -d recovery >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        echo "Recovery PostgreSQL did not become ready" >&2
        exit 1
    fi
    sleep 2
done

docker cp "$BACKUP_DIR/database.dump" "$RECOVERY_CONTAINER:/tmp/database.dump" >/dev/null
docker exec "$RECOVERY_CONTAINER" pg_restore -U postgres -d recovery \
    --no-owner --no-privileges --exit-on-error /tmp/database.dump

docker exec xiaozhi-postgres pg_dump \
    -U "$(docker exec xiaozhi-postgres printenv POSTGRES_USER)" \
    -d "$(docker exec xiaozhi-postgres printenv POSTGRES_DB)" \
    --format=custom --table=public.xiaozhi_tokens \
    --file=/tmp/xiaozhi-tokens-post-rotation.dump
docker cp xiaozhi-postgres:/tmp/xiaozhi-tokens-post-rotation.dump \
    "$BACKUP_DIR/xiaozhi-tokens-post-rotation.dump" >/dev/null
docker exec xiaozhi-postgres rm -f /tmp/xiaozhi-tokens-post-rotation.dump
chmod 600 "$BACKUP_DIR/xiaozhi-tokens-post-rotation.dump"

docker run --rm --network xiaozhi_default \
    --env-file "$RECOVERY_ENV" \
    -v "$APP_ROOT/scripts:/workspace/scripts:ro" \
    -w /workspace \
    "$IMAGE" python scripts/recover_mcp_endpoints.py

if [ -s "$BACKUP_DIR/rotated-device-relay-credentials.tsv" ]; then
    awk -F '\t' 'NR == 1 || $1 == "relay"' \
        "$BACKUP_DIR/rotated-device-relay-credentials.tsv" \
        >"$BACKUP_DIR/rotated-relay-credentials.tsv"
    chmod 600 "$BACKUP_DIR/rotated-relay-credentials.tsv"
    mv "$BACKUP_DIR/rotated-device-relay-credentials.tsv" \
        "$BACKUP_DIR/superseded-device-relay-credentials.tsv"
fi

docker restart xiaozhi >/dev/null
attempt=0
until [ "$(docker inspect --format '{{.State.Health.Status}}' xiaozhi 2>/dev/null || true)" = "healthy" ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        docker logs --tail 120 xiaozhi
        exit 1
    fi
    sleep 4
done

cleanup
echo "MCP_ENDPOINT_RECOVERY_COMPLETE"
