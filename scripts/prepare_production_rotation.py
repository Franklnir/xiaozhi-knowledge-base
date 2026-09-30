#!/usr/bin/env python3
"""Prepare one-time rotation inputs without printing any secret values."""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import stat
from pathlib import Path


def parse_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return lines, values


def required(values: dict[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Current environment is missing {name}")
    return value


def token(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


def fernet_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def legacy_firmware_secret(values: dict[str, str], source: Path) -> str:
    configured = values.get("FIRMWARE_PRESET_SECRET", "").strip()
    if configured:
        return configured
    text = source.read_text(encoding="utf-8")
    patterns = (
        r'FIRMWARE_PRESET_SECRET[^\n]*?\bor\s*["\']([^"\']+)["\']',
        r'getenv\(["\']FIRMWARE_PRESET_SECRET["\'][^)]*?["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    raise RuntimeError("Cannot locate the legacy firmware preset secret")


def render_env(original: list[str], updates: dict[str, str]) -> str:
    pending = dict(updates)
    rendered: list[str] = []
    for line in original:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in pending:
                rendered.append(f"{key}={pending.pop(key)}")
                continue
        rendered.append(line)
    if pending:
        rendered.append("")
        rendered.append("# Security rotation managed values")
        rendered.extend(f"{key}={value}" for key, value in pending.items())
    return "\n".join(rendered).rstrip() + "\n"


def write_private(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-env", type=Path, required=True)
    parser.add_argument("--legacy-firmware-source", type=Path, required=True)
    parser.add_argument("--new-env", type=Path, required=True)
    parser.add_argument("--rotation-env", type=Path, required=True)
    parser.add_argument("--db-admin-env", type=Path, required=True)
    parser.add_argument("--recovery-file", type=Path, required=True)
    parser.add_argument("--firmware-roots", required=True)
    args = parser.parse_args()

    original, current = parse_env(args.current_env)
    old_app = required(current, "APP_SECRET_KEY")
    old_data = current.get("DATA_ENCRYPTION_KEY", "").strip() or old_app
    old_firmware = legacy_firmware_secret(current, args.legacy_firmware_source)

    generated = {
        "APP_SECRET_KEY": token(64),
        "JWT_SECRET": token(64),
        "DATA_ENCRYPTION_KEY": fernet_key(),
        "MARKETPLACE_DATA_ENCRYPTION_KEY": token(64),
        "DOWNLOAD_TOKEN_SECRET": token(64),
        "FIRMWARE_PRESET_SECRET": token(64),
        "ADMIN_PASSWORD": token(24),
        "POSTGRES_PASSWORD": token(32),
        "POSTGRES_ADMIN_PASSWORD": token(40),
    }
    restored_username = current.get("RESTORED_USER_USERNAME", "").strip()
    restored_password = token(24) if restored_username else ""
    current_db_user = required(current, "POSTGRES_USER")
    runtime_db_user = "xiaozhi_runtime"
    if runtime_db_user == current_db_user:
        runtime_db_user = "xiaozhi_app_runtime"

    app_updates = {
        **{key: value for key, value in generated.items() if key != "POSTGRES_ADMIN_PASSWORD"},
        "POSTGRES_USER": runtime_db_user,
        "RESTORED_USER_PASSWORD": restored_password,
        "ENVIRONMENT": "production",
        "DB_AUTO_INIT_SCHEMA": "false",
        "ALLOWED_HOSTS": "xiaozhiscig.biz.id,localhost,127.0.0.1",
        "ALLOWED_ORIGINS": "https://xiaozhiscig.biz.id",
        "GOOGLE_AUTH_ENABLED": "false",
        "GOOGLE_CLIENT_ID": "",
        "GOOGLE_CLIENT_SECRET": "",
        "PAYMENTS_ENABLED": "false",
        "ALLOW_SIMULATOR_PAYMENTS": "false",
        "MARKETPLACE_PAYMENT_PROVIDER": "disabled",
        "MIDTRANS_SERVER_KEY": "",
        "MIDTRANS_WEBHOOK_SECRET": "",
        "XENDIT_SECRET_KEY": "",
        "XENDIT_WEBHOOK_TOKEN": "",
        "HF_TOKEN": "",
        "HUGGING_FACE_TOKEN": "",
        "HUGGING_FACE_HUB_TOKEN": "",
    }

    rotation = {
        "OLD_APP_SECRET_KEY": old_app,
        "OLD_DATA_ENCRYPTION_KEY": old_data,
        "OLD_FIRMWARE_PRESET_SECRET": old_firmware,
        "NEW_DATA_ENCRYPTION_KEY": generated["DATA_ENCRYPTION_KEY"],
        "NEW_MARKETPLACE_DATA_ENCRYPTION_KEY": generated["MARKETPLACE_DATA_ENCRYPTION_KEY"],
        "NEW_FIRMWARE_PRESET_SECRET": generated["FIRMWARE_PRESET_SECRET"],
        "NEW_ADMIN_PASSWORD": generated["ADMIN_PASSWORD"],
        "NEW_RESTORED_USER_PASSWORD": restored_password,
        "NEW_POSTGRES_PASSWORD": generated["POSTGRES_PASSWORD"],
        "NEW_POSTGRES_ADMIN_PASSWORD": generated["POSTGRES_ADMIN_PASSWORD"],
        "POSTGRES_ADMIN_USER": current_db_user,
        "NEW_POSTGRES_USER": runtime_db_user,
        "FIRMWARE_SEARCH_ROOTS": args.firmware_roots,
        "ADMIN_USERNAME": required(current, "ADMIN_USERNAME"),
        "RESTORED_USER_USERNAME": restored_username,
        "POSTGRES_HOST": required(current, "POSTGRES_HOST"),
        "POSTGRES_PORT": current.get("POSTGRES_PORT", "5432"),
        "POSTGRES_DB": required(current, "POSTGRES_DB"),
        "POSTGRES_USER": current_db_user,
        "POSTGRES_PASSWORD": required(current, "POSTGRES_PASSWORD"),
        "OLD_POSTGRES_PASSWORD": required(current, "POSTGRES_PASSWORD"),
    }

    recovery = {
        "ADMIN_USERNAME": required(current, "ADMIN_USERNAME"),
        "ADMIN_PASSWORD": generated["ADMIN_PASSWORD"],
        "RESTORED_USER_USERNAME": restored_username,
        "RESTORED_USER_PASSWORD": restored_password,
        "POSTGRES_USER": runtime_db_user,
        "POSTGRES_PASSWORD": generated["POSTGRES_PASSWORD"],
        "POSTGRES_ADMIN_USER": current_db_user,
        "POSTGRES_ADMIN_PASSWORD": generated["POSTGRES_ADMIN_PASSWORD"],
    }

    db_admin = {
        "POSTGRES_HOST": required(current, "POSTGRES_HOST"),
        "POSTGRES_PORT": current.get("POSTGRES_PORT", "5432"),
        "POSTGRES_DB": required(current, "POSTGRES_DB"),
        "POSTGRES_USER": current_db_user,
        "POSTGRES_PASSWORD": generated["POSTGRES_ADMIN_PASSWORD"],
    }

    for path in (args.new_env, args.rotation_env, args.db_admin_env, args.recovery_file):
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
    write_private(args.new_env, render_env(original, app_updates))
    write_private(args.rotation_env, "".join(f"{key}={value}\n" for key, value in rotation.items()))
    write_private(args.db_admin_env, "".join(f"{key}={value}\n" for key, value in db_admin.items()))
    write_private(args.recovery_file, "".join(f"{key}={value}\n" for key, value in recovery.items()))
    print("Rotation files prepared with mode 0600; no secret values were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
