#!/usr/bin/env python3
"""Recover externally-issued MCP WebSocket URLs after an incorrect credential rotation.

The source is a read-only restored pre-rotation database. Values are decrypted
with the old key and re-encrypted with the active key. This script never prints
URLs, tokens, ciphertexts, user IDs, or database credentials.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys

import psycopg
from cryptography.fernet import Fernet, InvalidToken


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fernet_from_secret(secret: str) -> Fernet:
    try:
        return Fernet(secret.encode("utf-8"))
    except (ValueError, TypeError):
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def connect(prefix: str):
    return psycopg.connect(
        host=required(f"{prefix}_POSTGRES_HOST"),
        port=int(os.environ.get(f"{prefix}_POSTGRES_PORT", "5432")),
        dbname=required(f"{prefix}_POSTGRES_DB"),
        user=required(f"{prefix}_POSTGRES_USER"),
        password=required(f"{prefix}_POSTGRES_PASSWORD"),
    )


def main() -> int:
    old_cipher = fernet_from_secret(required("OLD_DATA_ENCRYPTION_KEY"))
    new_cipher = fernet_from_secret(required("DATA_ENCRYPTION_KEY"))
    hash_length = int(os.environ.get("MCP_TOKEN_HASH_LENGTH", "16"))
    if not 16 <= hash_length <= 64:
        raise RuntimeError("MCP_TOKEN_HASH_LENGTH must be between 16 and 64")

    with connect("SOURCE") as source, connect("TARGET") as target:
        with source.cursor() as source_cur, target.cursor() as target_cur:
            source_cur.execute(
                "SELECT user_id, slot_number, token_ciphertext FROM xiaozhi_tokens ORDER BY user_id, slot_number"
            )
            source_rows = source_cur.fetchall()
            target_cur.execute(
                "SELECT user_id, slot_number, token_ciphertext FROM xiaozhi_tokens ORDER BY user_id, slot_number FOR UPDATE"
            )
            target_rows = target_cur.fetchall()

            source_keys = {(int(row[0]), int(row[1])) for row in source_rows}
            target_keys = {(int(row[0]), int(row[1])) for row in target_rows}
            if not source_rows or source_keys != target_keys:
                raise RuntimeError("Source and target MCP slot sets differ; recovery aborted")

            # Refuse to overwrite an unexpected post-cutover edit.
            for user_id, slot_number, ciphertext in target_rows:
                try:
                    current = new_cipher.decrypt(str(ciphertext).encode("utf-8")).decode("utf-8")
                except (InvalidToken, UnicodeDecodeError, ValueError, TypeError) as exc:
                    raise RuntimeError("Target MCP endpoint cannot be decrypted with the active key") from exc
                if current and not (current.startswith("xz_") or current.startswith("wss://")):
                    raise RuntimeError("Target MCP endpoint has an unexpected format; recovery aborted")

            restored = 0
            quarantined = 0
            for user_id, slot_number, ciphertext in source_rows:
                endpoint = ""
                try:
                    endpoint = old_cipher.decrypt(str(ciphertext).encode("utf-8")).decode("utf-8")
                except (InvalidToken, UnicodeDecodeError, ValueError, TypeError):
                    endpoint = ""

                key = (int(user_id), int(slot_number))
                if endpoint.startswith("wss://"):
                    token_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:hash_length]
                    restored += 1
                else:
                    endpoint = ""
                    token_hash = hashlib.sha256(
                        f"quarantined-mcp-slot:{key[0]}:{key[1]}".encode("utf-8")
                    ).hexdigest()[:hash_length]
                    quarantined += 1

                replacement = new_cipher.encrypt(endpoint.encode("utf-8")).decode("utf-8")
                target_cur.execute(
                    """
                    UPDATE xiaozhi_tokens
                    SET token_ciphertext = %s, token_hash = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = %s AND slot_number = %s
                    """,
                    (replacement, token_hash, key[0], key[1]),
                )
                if target_cur.rowcount != 1:
                    raise RuntimeError("MCP endpoint recovery update count mismatch")

            target.commit()

            target_cur.execute("SELECT token_ciphertext FROM xiaozhi_tokens")
            verified_wss = 0
            verified_empty = 0
            for (ciphertext,) in target_cur.fetchall():
                plaintext = new_cipher.decrypt(str(ciphertext).encode("utf-8")).decode("utf-8")
                if plaintext.startswith("wss://"):
                    verified_wss += 1
                elif plaintext == "":
                    verified_empty += 1
                else:
                    raise RuntimeError("Recovered MCP endpoint verification failed")

    if (verified_wss, verified_empty) != (restored, quarantined):
        raise RuntimeError("Recovered MCP endpoint counts failed verification")
    print(
        "MCP endpoint recovery completed and verified: "
        f"restored_wss={restored}, quarantined_unrecoverable={quarantined}, total={restored + quarantined}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MCP endpoint recovery failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
