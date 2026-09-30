#!/bin/sh
set -eu

BUNDLE_PATH="${1:-/tmp/xiaozhi-security-test.tar.gz}"
RESULT_PATH="${2:-/tmp/xiaozhi-security-last.log}"
WORK_DIR="$(mktemp -d /tmp/xiaozhi-security-suite.XXXXXX)"
SUFFIX="$$"
NETWORK_NAME="xiaozhi-security-${SUFFIX}"
PG_CONTAINER="xiaozhi-security-pg-${SUFFIX}"

case "$WORK_DIR" in
    /tmp/xiaozhi-security-suite.*) ;;
    *) echo "Refusing unsafe temporary directory: $WORK_DIR" >&2; exit 1 ;;
esac

cleanup() {
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
    case "$WORK_DIR" in
        /tmp/xiaozhi-security-suite.*) rm -rf -- "$WORK_DIR" ;;
    esac
}
trap cleanup EXIT INT TERM

test -f "$BUNDLE_PATH"
tar -xzf "$BUNDLE_PATH" -C "$WORK_DIR"
docker network create "$NETWORK_NAME" >/dev/null
docker run -d --name "$PG_CONTAINER" --network "$NETWORK_NAME" \
    -e POSTGRES_DB=xiaozhi_test \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=isolated-admin-password \
    postgres:16-alpine >/dev/null

ready=0
for _ in $(seq 1 30); do
    if docker exec "$PG_CONTAINER" pg_isready -U postgres -d xiaozhi_test >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
test "$ready" -eq 1

configured=0
for _ in $(seq 1 30); do
    if docker exec "$PG_CONTAINER" psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
        -c 'DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '\''xiaozhi_test'\'') THEN CREATE ROLE xiaozhi_test WITH LOGIN SUPERUSER PASSWORD '\''isolated-test-password'\''; END IF; END $$; ALTER DATABASE xiaozhi_test OWNER TO xiaozhi_test;' >/dev/null 2>&1; then
        configured=1
        break
    fi
    sleep 1
done
test "$configured" -eq 1

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
set +e
docker run --rm --user "$HOST_UID:$HOST_GID" --network "$NETWORK_NAME" \
    -v "$WORK_DIR:/workspace" -w /workspace \
    -e HOME=/tmp/test-home \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e ENVIRONMENT=test \
    -e APP_SECRET_KEY=app-test-secret-that-is-long-and-isolated-from-production \
    -e JWT_SECRET=jwt-test-secret-that-is-long-and-isolated-from-production \
    -e DATA_ENCRYPTION_KEY=YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE= \
    -e MARKETPLACE_DATA_ENCRYPTION_KEY=YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI= \
    -e DOWNLOAD_TOKEN_SECRET=download-test-secret-that-is-long-and-isolated \
    -e FIRMWARE_PRESET_SECRET=firmware-test-secret-that-is-long-and-isolated \
    -e GOOGLE_AUTH_ENABLED=false \
    -e PAYMENTS_ENABLED=true \
    -e MARKETPLACE_PAYMENT_PROVIDER=simulator \
    -e ALLOW_SIMULATOR_PAYMENTS=true \
    -e POSTGRES_HOST="$PG_CONTAINER" \
    -e POSTGRES_PORT=5432 \
    -e POSTGRES_DB=xiaozhi_test \
    -e POSTGRES_USER=xiaozhi_test \
    -e POSTGRES_PASSWORD=isolated-test-password \
    -e POSTGRES_ADMIN_USER=postgres \
    -e POSTGRES_ADMIN_PASSWORD=isolated-admin-password \
    python:3.11-slim sh scripts/run_isolated_security_tests.sh >"$RESULT_PATH" 2>&1
STATUS=$?
set -e

cat "$RESULT_PATH"
exit "$STATUS"
