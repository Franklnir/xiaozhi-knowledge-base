#!/usr/bin/env python3
"""Smoke-test the production runtime role without mutating application data."""

from __future__ import annotations

import psycopg

from xiaozhi.database.postgres_store import PostgresStore
from xiaozhi.marketplace.repository import MarketplaceRepository


def main() -> int:
    store = PostgresStore(min_pool_size=1, max_pool_size=2)
    try:
        if not store.ping():
            raise RuntimeError("Runtime database ping failed")
        with store._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users ORDER BY id LIMIT 1")
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("Runtime role cannot read users")
                user_id = int(row["id"])

        if store.get_user(user_id) is None:
            raise RuntimeError("Runtime store cannot fetch a user")
        store.list_categories(user_id)
        MarketplaceRepository(store).get_published_products(limit=1)

        with store._get_conn() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("CREATE TABLE public._runtime_role_must_not_create (id integer)")
                conn.commit()
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
            else:
                with conn.cursor() as cur:
                    cur.execute("DROP TABLE public._runtime_role_must_not_create")
                conn.commit()
                raise RuntimeError("Runtime role unexpectedly has schema DDL privileges")
    finally:
        store.close()
    print("Runtime role smoke test passed; application queries work and schema DDL is denied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
