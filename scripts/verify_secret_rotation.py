#!/usr/bin/env python3
"""Verify database, account, and firmware rotation without exposing secrets."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

import psycopg
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def connect(user: str, password: str):
    return psycopg.connect(
        host=required("POSTGRES_HOST"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=required("POSTGRES_DB"),
        user=user,
        password=password,
    )


def fernet_from_secret(secret: str) -> Fernet:
    try:
        return Fernet(secret.encode("utf-8"))
    except (ValueError, TypeError):
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def marketplace_fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def verify_password(password: str, encoded: str) -> bool:
    algorithm, iterations, salt, expected = encoded.split("$", 3)
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
    ).hex()
    return hmac.compare_digest(actual, expected)


def decrypt_column(cur, table: str, column: str, cipher: Fernet) -> int:
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    if cur.fetchone()[0] is None:
        return 0
    cur.execute(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL AND {column} <> ''")
    rows = cur.fetchall()
    for (value,) in rows:
        cipher.decrypt(str(value).encode("utf-8"))
    return len(rows)


def firmware_files() -> list[Path]:
    roots = [Path(item) for item in required("FIRMWARE_SEARCH_ROOTS").split(os.pathsep) if item]
    found: set[Path] = set()
    for root in roots:
        if root.is_file() and root.name.endswith(".bin.enc"):
            found.add(root)
        elif root.is_dir():
            found.update(path for path in root.rglob("*.bin.enc") if path.is_file())
    return sorted(found)


def main() -> int:
    app_user = required("POSTGRES_USER")
    new_password = required("POSTGRES_PASSWORD")
    old_password = required("OLD_POSTGRES_PASSWORD")

    conn = connect(app_user, new_password)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            if cur.fetchone() != (False, False, False, False):
                raise RuntimeError("Application database role is still privileged")
    finally:
        conn.close()

    admin_user = required("POSTGRES_ADMIN_USER")
    try:
        old_conn = connect(admin_user, old_password)
    except psycopg.OperationalError:
        pass
    else:
        old_conn.close()
        raise RuntimeError("Old bootstrap PostgreSQL password still authenticates")

    admin_conn = connect(admin_user, required("NEW_POSTGRES_ADMIN_PASSWORD"))
    try:
        with admin_conn.cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            if cur.fetchone() != (True,):
                raise RuntimeError("Out-of-band database admin verification failed")

            data_cipher = fernet_from_secret(required("DATA_ENCRYPTION_KEY"))
            market_cipher = marketplace_fernet(required("MARKETPLACE_DATA_ENCRYPTION_KEY"))
            decrypted = {
                "materials": decrypt_column(cur, "materials", "api_url_ciphertext", data_cipher),
                "device_tokens": decrypt_column(cur, "xiaozhi_tokens", "token_ciphertext", data_cipher),
                "relay_tokens": decrypt_column(cur, "relay_rooms", "api_token_ciphertext", data_cipher),
                "withdrawals": decrypt_column(
                    cur, "withdrawals", "destination_account_encrypted", market_cipher
                ),
            }

            cur.execute(
                "SELECT password_hash FROM users WHERE username = %s AND role = 'admin'",
                (required("ADMIN_USERNAME"),),
            )
            row = cur.fetchone()
            if not row or not verify_password(required("ADMIN_PASSWORD"), row[0]):
                raise RuntimeError("Admin password rotation verification failed")

            restored_user = os.environ.get("RESTORED_USER_USERNAME", "").strip()
            if restored_user:
                cur.execute("SELECT password_hash FROM users WHERE username = %s", (restored_user,))
                row = cur.fetchone()
                if not row or not verify_password(required("RESTORED_USER_PASSWORD"), row[0]):
                    raise RuntimeError("Restored account password rotation verification failed")
    finally:
        admin_conn.close()

    new_aes = AESGCM(hashlib.sha256(required("FIRMWARE_PRESET_SECRET").encode("utf-8")).digest())
    old_aes = AESGCM(hashlib.sha256(required("OLD_FIRMWARE_PRESET_SECRET").encode("utf-8")).digest())
    files = firmware_files()
    if not files:
        raise RuntimeError("No firmware files found during verification")
    for path in files:
        payload = path.read_bytes()
        new_aes.decrypt(payload[:12], payload[12:], None)
        try:
            old_aes.decrypt(payload[:12], payload[12:], None)
        except Exception:
            continue
        raise RuntimeError(f"Old firmware key still decrypts: {path}")

    print("Rotation verification passed.")
    print("Verified encrypted rows: " + ", ".join(f"{k}={v}" for k, v in sorted(decrypted.items())))
    print(f"Verified firmware files: {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
