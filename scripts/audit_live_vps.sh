#!/bin/sh
set -eu

APP_ROOT="${1:-/opt/xiaozhi}"
BACKUP_DIR="${2:-$APP_ROOT/backups/security-20261001T000021Z}"

case "$BACKUP_DIR" in
    "$APP_ROOT"/backups/security-*) ;;
    *) echo "Unsafe backup path: $BACKUP_DIR" >&2; exit 1 ;;
esac

docker ps --filter name=xiaozhi
docker inspect --format \
    '{{.Config.Image}} health={{.State.Health.Status}} readonly={{.HostConfig.ReadonlyRootfs}} pids={{.HostConfig.PidsLimit}} caps={{json .HostConfig.CapDrop}} security={{json .HostConfig.SecurityOpt}}' \
    xiaozhi

test -z "$(docker port xiaozhi-postgres)"
echo "POSTGRES_NOT_PUBLISHED"

test "$(docker exec xiaozhi printenv GOOGLE_AUTH_ENABLED)" = "false"
test -z "$(docker exec xiaozhi printenv GOOGLE_CLIENT_ID)"
test -z "$(docker exec xiaozhi printenv GOOGLE_CLIENT_SECRET)"
echo "GOOGLE_DISABLED"

if docker logs --since 5m xiaozhi 2>&1 | grep -q 'Token bukan wss'; then
    echo "INSECURE_MCP_WARNING_FOUND" >&2
    exit 1
fi
echo "INSECURE_MCP_WARNINGS=0"

if docker inspect xiaozhi-mcp-recovery-postgres >/dev/null 2>&1; then
    echo "Temporary recovery container still exists" >&2
    exit 1
fi
echo "RECOVERY_CONTAINER_REMOVED"

stat -c '%a %n' \
    "$APP_ROOT/.env" \
    "$APP_ROOT/.db-admin.env" \
    "$APP_ROOT/docker-compose.yml" \
    "$BACKUP_DIR" \
    "$BACKUP_DIR/rotated-relay-credentials.tsv"

docker exec -e PYTHONPATH=/app xiaozhi python /app/scripts/smoke_runtime_role.py

if sudo -n true 2>/dev/null; then
    echo "SUDO_NONINTERACTIVE=yes"
else
    echo "SUDO_NONINTERACTIVE=no"
fi
