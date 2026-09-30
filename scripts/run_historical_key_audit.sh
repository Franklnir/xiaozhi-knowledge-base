#!/bin/sh
set -eu

docker run --rm --network host \
    -e ENV_FILE=/workspace/.env \
    -e POSTGRES_HOST=127.0.0.1 \
    -e SOURCE_REPOSITORY=/workspace \
    -v /opt/xiaozhi:/workspace:ro \
    -v /tmp/audit_historical_encryption_keys.py:/audit.py:ro \
    python:3.11-slim sh -c '
        apt-get update -qq
        apt-get install -y -qq git >/dev/null
        pip install --disable-pip-version-check -q "psycopg[binary]" cryptography
        git config --global --add safe.directory /workspace
        python /audit.py
    '
