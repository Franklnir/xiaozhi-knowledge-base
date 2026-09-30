#!/bin/sh
set -eu

python -m venv /tmp/xiaozhi-test-venv
. /tmp/xiaozhi-test-venv/bin/activate
python -m pip install --disable-pip-version-check --no-cache-dir -q -r requirements-prod.txt pytest
python -m alembic upgrade head
python - <<'PY'
import os
import psycopg
from psycopg import sql

role = os.environ["POSTGRES_USER"]
with psycopg.connect(
    host=os.environ["POSTGRES_HOST"],
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ.get("POSTGRES_ADMIN_USER", role),
    password=os.environ.get("POSTGRES_ADMIN_PASSWORD", os.environ["POSTGRES_PASSWORD"]),
) as conn:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("ALTER ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS").format(
                sql.Identifier(role)
            )
        )
PY
python -m pytest tests -q -p no:cacheprovider
