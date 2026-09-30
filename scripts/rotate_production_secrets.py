#!/usr/bin/env python3
"""Atomically re-encrypt production data before switching application secrets.

All secret inputs are read from environment variables. The script never prints
secret values and aborts on the first decryption or validation failure.
Run it while the application container is stopped.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import shutil
import stat
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


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


def marketplace_fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def password_hash(password: str) -> str:
    if len(password) < 16:
        raise RuntimeError("Rotated account passwords must contain at least 16 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 310_000
    ).hex()
    return f"pbkdf2_sha256$310000${salt}${digest}"


def connect_database():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return psycopg.connect(database_url)
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "xiaozhi"),
        user=os.environ.get("POSTGRES_USER", "xiaozhi_app"),
        password=required("POSTGRES_PASSWORD"),
    )


def provision_runtime_role(cur, admin_role: str, runtime_role: str, runtime_password: str) -> None:
    """Create a least-privilege login used only by the running application."""
    if runtime_role == admin_role:
        raise RuntimeError("NEW_POSTGRES_USER must differ from the bootstrap admin role")
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (runtime_role,))
    statement = "ALTER ROLE" if cur.fetchone() else "CREATE ROLE"
    cur.execute(
        sql.SQL(
            f"{statement} {{}} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOBYPASSRLS CONNECTION LIMIT 20 PASSWORD {}"
        ).format(sql.Identifier(runtime_role), sql.Literal(runtime_password))
    )

    database_name = os.environ.get("POSTGRES_DB", "xiaozhi")
    cur.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database_name), sql.Identifier(runtime_role)
        )
    )
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(runtime_role)))
    cur.execute(
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
            sql.Identifier(runtime_role)
        )
    )
    cur.execute(
        sql.SQL("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {}").format(
            sql.Identifier(runtime_role)
        )
    )
    cur.execute(
        sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {}").format(
            sql.Identifier(runtime_role)
        )
    )
    cur.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(sql.Identifier(admin_role), sql.Identifier(runtime_role))
    )
    cur.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}"
        ).format(sql.Identifier(admin_role), sql.Identifier(runtime_role))
    )
    cur.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO {}"
        ).format(sql.Identifier(admin_role), sql.Identifier(runtime_role))
    )


def table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    return cur.fetchone()[0] is not None


def reencrypt_column(
    cur,
    table: str,
    key_columns: tuple[str, ...],
    column: str,
    old: Fernet,
    new: Fernet,
    *,
    reset_unrecoverable: bool = False,
) -> tuple[int, int]:
    if not table_exists(cur, table):
        return 0, 0
    selected = ", ".join((*key_columns, column))
    cur.execute(f"SELECT {selected} FROM {table} WHERE {column} IS NOT NULL AND {column} <> '' FOR UPDATE")
    rows = cur.fetchall()
    count = 0
    reset_count = 0
    for row in rows:
        *keys, ciphertext = row
        try:
            plaintext = old.decrypt(str(ciphertext).encode("utf-8"))
        except (InvalidToken, ValueError, TypeError) as exc:
            if reset_unrecoverable:
                where = " AND ".join(f"{name} = %s" for name in key_columns)
                cur.execute(f"UPDATE {table} SET {column} = NULL WHERE {where}", tuple(keys))
                reset_count += 1
                continue
            raise RuntimeError(f"Cannot decrypt {table}.{column}; rotation aborted") from exc
        replacement = new.encrypt(plaintext).decode("utf-8")
        where = " AND ".join(f"{name} = %s" for name in key_columns)
        cur.execute(f"UPDATE {table} SET {column} = %s WHERE {where}", (replacement, *keys))
        count += 1
    return count, reset_count


def safe_field(value: object) -> str:
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def rotate_service_credentials(cur, new_data: Fernet) -> tuple[dict[str, int], str]:
    """Replace every device/relay credential and return a private recovery mapping."""
    lines = ["type\tusername\tuser_id\tslot_or_room\tlabel\tboard_mac\tnew_token"]
    counts = {"device_credentials": 0, "relay_credentials": 0}
    device_hash_length = int(os.environ.get("MCP_TOKEN_HASH_LENGTH", "16"))
    if not 16 <= device_hash_length <= 64:
        raise RuntimeError("MCP_TOKEN_HASH_LENGTH must be between 16 and 64")

    if table_exists(cur, "xiaozhi_tokens"):
        cur.execute(
            """
            SELECT t.user_id, t.slot_number, t.device_label, t.board_mac, u.username
            FROM xiaozhi_tokens t
            JOIN users u ON u.id = t.user_id
            ORDER BY t.user_id, t.slot_number
            FOR UPDATE OF t
            """
        )
        for user_id, slot_number, label, board_mac, username in cur.fetchall():
            new_token = "xz_" + secrets.token_urlsafe(32)
            new_hash = hashlib.sha256(new_token.encode("utf-8")).hexdigest()[:device_hash_length]
            new_ciphertext = new_data.encrypt(new_token.encode("utf-8")).decode("utf-8")
            cur.execute(
                """
                UPDATE xiaozhi_tokens
                SET token_ciphertext = %s, token_hash = %s, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = %s AND slot_number = %s
                """,
                (new_ciphertext, new_hash, user_id, slot_number),
            )
            lines.append(
                "\t".join(
                    (
                        "device",
                        safe_field(username),
                        safe_field(user_id),
                        safe_field(slot_number),
                        safe_field(label),
                        safe_field(board_mac),
                        new_token,
                    )
                )
            )
            counts["device_credentials"] += 1

    if table_exists(cur, "relay_rooms"):
        cur.execute(
            """
            SELECT r.id, r.owner_id, r.nama_tempat, u.username
            FROM relay_rooms r
            JOIN users u ON u.id = r.owner_id
            ORDER BY r.owner_id, r.id
            FOR UPDATE OF r
            """
        )
        for room_id, owner_id, label, username in cur.fetchall():
            new_token = "relay_" + secrets.token_urlsafe(32)
            new_hash = hashlib.sha256(new_token.encode("utf-8")).hexdigest()
            new_ciphertext = new_data.encrypt(new_token.encode("utf-8")).decode("utf-8")
            cur.execute(
                """
                UPDATE relay_rooms
                SET api_token_ciphertext = %s, api_token_hash = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (new_ciphertext, new_hash, room_id),
            )
            lines.append(
                "\t".join(
                    (
                        "relay",
                        safe_field(username),
                        safe_field(owner_id),
                        safe_field(room_id),
                        safe_field(label),
                        "",
                        new_token,
                    )
                )
            )
            counts["relay_credentials"] += 1

    return counts, "\n".join(lines) + "\n"


def write_private_file(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def firmware_candidates() -> list[Path]:
    roots = [Path(item) for item in os.environ.get("FIRMWARE_SEARCH_ROOTS", "").split(os.pathsep) if item]
    if not roots:
        roots = [Path("xiaozhi/protected_assets/firmware"), Path("data/storage/private/presets")]
    found: set[Path] = set()
    for root in roots:
        if root.is_file() and root.name.endswith(".bin.enc"):
            found.add(root.resolve())
        elif root.is_dir():
            found.update(path.resolve() for path in root.rglob("*.bin.enc") if path.is_file())
    return sorted(found)


def stage_firmware(files: list[Path], old_secret: str, new_secret: str) -> list[tuple[Path, Path, Path]]:
    old_aes = AESGCM(hashlib.sha256(old_secret.encode("utf-8")).digest())
    new_aes = AESGCM(hashlib.sha256(new_secret.encode("utf-8")).digest())
    staged: list[tuple[Path, Path, Path]] = []
    for path in files:
        encrypted = path.read_bytes()
        if len(encrypted) < 29:
            raise RuntimeError(f"Encrypted firmware has invalid size: {path}")
        try:
            plaintext = old_aes.decrypt(encrypted[:12], encrypted[12:], None)
        except Exception as exc:
            raise RuntimeError(f"Cannot decrypt firmware; rotation aborted: {path}") from exc
        nonce = secrets.token_bytes(12)
        replacement = nonce + new_aes.encrypt(nonce, plaintext, None)
        # Verify the staged value before it can replace the original.
        if new_aes.decrypt(replacement[:12], replacement[12:], None) != plaintext:
            raise RuntimeError(f"Firmware verification failed: {path}")
        temp = path.with_name(f".{path.name}.rotation-new")
        backup = path.with_name(f".{path.name}.rotation-backup")
        temp.write_bytes(replacement)
        temp.chmod(stat.S_IRUSR | stat.S_IWUSR)
        staged.append((path, temp, backup))
    return staged


def install_staged_files(staged: list[tuple[Path, Path, Path]]) -> None:
    installed: list[tuple[Path, Path]] = []
    try:
        for original, temp, backup in staged:
            shutil.copy2(original, backup)
            backup.chmod(stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temp, original)
            installed.append((original, backup))
    except Exception:
        for original, backup in reversed(installed):
            if backup.exists():
                os.replace(backup, original)
        raise


def restore_firmware_files(staged: list[tuple[Path, Path, Path]]) -> None:
    for original, temp, backup in staged:
        if backup.exists():
            os.replace(backup, original)
        if temp.exists():
            temp.unlink()


def remove_firmware_backups(staged: list[tuple[Path, Path, Path]]) -> None:
    for _, temp, backup in staged:
        if temp.exists():
            temp.unlink()
        if backup.exists():
            backup.unlink()


def main() -> int:
    old_data = fernet_from_secret(required("OLD_DATA_ENCRYPTION_KEY"))
    new_data = fernet_from_secret(required("NEW_DATA_ENCRYPTION_KEY"))
    old_market = marketplace_fernet(required("OLD_APP_SECRET_KEY"))
    new_market = marketplace_fernet(required("NEW_MARKETPLACE_DATA_ENCRYPTION_KEY"))
    old_firmware_secret = required("OLD_FIRMWARE_PRESET_SECRET")
    new_firmware_secret = required("NEW_FIRMWARE_PRESET_SECRET")
    new_admin_password = required("NEW_ADMIN_PASSWORD")
    new_postgres_password = required("NEW_POSTGRES_PASSWORD")
    new_postgres_admin_password = required("NEW_POSTGRES_ADMIN_PASSWORD")

    admin_role = os.environ.get("POSTGRES_USER", "xiaozhi_app")
    configured_admin_role = os.environ.get("POSTGRES_ADMIN_USER", admin_role)
    runtime_role = required("NEW_POSTGRES_USER")
    if configured_admin_role != admin_role:
        raise RuntimeError("POSTGRES_ADMIN_USER must identify the current bootstrap role")
    credential_output = Path(required("ROTATED_CREDENTIALS_OUTPUT"))
    if credential_output.exists():
        raise RuntimeError("ROTATED_CREDENTIALS_OUTPUT already exists")

    firmware_files = firmware_candidates()
    if not firmware_files:
        raise RuntimeError("No encrypted firmware files found; refusing an incomplete rotation")
    staged = stage_firmware(firmware_files, old_firmware_secret, new_firmware_secret)

    counts: dict[str, int] = {}
    credential_file_written = False
    conn = connect_database()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")
            current_role = cur.fetchone()
            if current_role != (admin_role, True):
                raise RuntimeError("Rotation must connect as the bootstrap database superuser")
            allow_material_reset = os.environ.get("ALLOW_UNRECOVERABLE_MATERIAL_RESET", "").lower() == "true"
            counts["materials"], counts["materials_reset"] = reencrypt_column(
                cur,
                "materials",
                ("id",),
                "api_url_ciphertext",
                old_data,
                new_data,
                reset_unrecoverable=allow_material_reset,
            )
            credential_counts, credential_content = rotate_service_credentials(cur, new_data)
            counts.update(credential_counts)
            counts["withdrawals"], counts["withdrawals_reset"] = reencrypt_column(
                cur, "withdrawals", ("id",), "destination_account_encrypted", old_market, new_market
            )

            admin_username = os.environ.get("ADMIN_USERNAME", "admin")
            cur.execute(
                "UPDATE users SET password_hash = %s, session_version = session_version + 1 WHERE username = %s AND role = 'admin'",
                (password_hash(new_admin_password), admin_username),
            )
            if cur.rowcount != 1:
                raise RuntimeError("Expected exactly one configured admin account")

            restored_username = os.environ.get("RESTORED_USER_USERNAME", "").strip()
            restored_password = os.environ.get("NEW_RESTORED_USER_PASSWORD", "").strip()
            if restored_username and restored_password:
                cur.execute(
                    "UPDATE users SET password_hash = %s WHERE username = %s",
                    (password_hash(restored_password), restored_username),
                )
                if cur.rowcount != 1:
                    raise RuntimeError("Configured restored account was not found")

            # Revoke every remaining cookie/JWT even for accounts whose password did not change.
            cur.execute(
                "UPDATE users SET session_version = session_version + 1 WHERE username <> %s",
                (admin_username,),
            )
            provision_runtime_role(cur, admin_role, runtime_role, new_postgres_password)
            cur.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(admin_role), sql.Literal(new_postgres_admin_password)
                )
            )
            cur.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = %s",
                (runtime_role,),
            )
            if cur.fetchone() != (False, False, False, False):
                raise RuntimeError("PostgreSQL application role privilege verification failed")

            write_private_file(credential_output, credential_content)
            credential_file_written = True
            install_staged_files(staged)
            conn.commit()
    except Exception:
        conn.rollback()
        restore_firmware_files(staged)
        if credential_file_written:
            credential_output.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    remove_firmware_backups(staged)
    print("Rotation completed and verified.")
    print("Re-encrypted rows: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    print(f"Re-encrypted firmware files: {len(firmware_files)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Rotation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
