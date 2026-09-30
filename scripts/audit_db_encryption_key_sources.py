#!/usr/bin/env python3
"""Count which named legacy key candidates decrypt database ciphertext."""

from __future__ import annotations

import base64
import hashlib
import os

import psycopg
from cryptography.fernet import Fernet, InvalidToken


def fernet(secret: str) -> Fernet:
    try:
        return Fernet(secret.encode("utf-8"))
    except (ValueError, TypeError):
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def main() -> int:
    candidates = {
        "APP_SECRET_KEY": os.environ.get("APP_SECRET_KEY", ""),
        "DATA_ENCRYPTION_KEY": os.environ.get("DATA_ENCRYPTION_KEY", ""),
    }
    candidates = {name: fernet(value) for name, value in candidates.items() if value}
    conn = psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "xiaozhi"),
        user=os.environ.get("POSTGRES_USER", "xiaozhi_app"),
        password=os.environ["POSTGRES_PASSWORD"],
    )
    failed = False
    try:
        with conn.cursor() as cur:
            tables = (
                ("materials", "api_url_ciphertext"),
                ("xiaozhi_tokens", "token_ciphertext"),
                ("relay_rooms", "api_token_ciphertext"),
            )
            for table, column in tables:
                cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cur.fetchone()[0] is None:
                    continue
                cur.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} <> ''")
                rows = cur.fetchall()
                counts = {name: 0 for name in candidates}
                unmatched = 0
                for (value,) in rows:
                    matches = 0
                    for name, cipher in candidates.items():
                        try:
                            cipher.decrypt(str(value).encode("utf-8"))
                        except (InvalidToken, ValueError, TypeError):
                            continue
                        counts[name] += 1
                        matches += 1
                    if not matches:
                        unmatched += 1
                print(
                    f"{table}.{column}: total={len(rows)}, "
                    + ", ".join(f"{name}={count}" for name, count in counts.items())
                    + f", unmatched={unmatched}"
                )
                failed = failed or unmatched > 0
    finally:
        conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
