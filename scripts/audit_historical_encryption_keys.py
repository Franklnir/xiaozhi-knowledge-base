#!/usr/bin/env python3
"""Find historical repository keys that can decrypt rows, without printing keys."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import subprocess
from pathlib import Path

import psycopg
from cryptography.fernet import Fernet, InvalidToken


KEY_NAMES = ("APP_SECRET_KEY", "DATA_ENCRYPTION_KEY")


def load_env_file() -> None:
    env_file = os.environ.get("ENV_FILE", "").strip()
    if not env_file:
        return
    for line in Path(env_file).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def cipher(secret: str) -> Fernet:
    try:
        return Fernet(secret.encode("utf-8"))
    except (ValueError, TypeError):
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))


def add_candidate(found: dict[str, str], label: str, value: str) -> None:
    value = value.strip().strip('"\'')
    if not value or value.startswith("${") or value.lower() in {"none", "changeme", "change-me"}:
        return
    found.setdefault(value, label)


def candidates_from_history(repo: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for key in KEY_NAMES:
        add_candidate(found, f"current-env:{key}", os.environ.get(key, ""))

    commits = subprocess.check_output(
        ["git", "-C", str(repo), "rev-list", "--all"], text=True
    ).splitlines()
    patterns = (
        re.compile(r"(?:APP_SECRET_KEY|DATA_ENCRYPTION_KEY)\s*=\s*['\"]([^'\"]+)['\"]"),
        re.compile(r"(?:APP_SECRET_KEY|DATA_ENCRYPTION_KEY)=([^\s#]+)"),
        re.compile(r"\$\{(?:APP_SECRET_KEY|DATA_ENCRYPTION_KEY)(?::-|-)([^}]+)\}"),
        re.compile(r"getenv\(['\"](?:APP_SECRET_KEY|DATA_ENCRYPTION_KEY)['\"]\s*,\s*['\"]([^'\"]+)['\"]"),
        re.compile(r"(?:APP_SECRET_KEY|DATA_ENCRYPTION_KEY)[^\n]*?\bor\s*['\"]([^'\"]+)['\"]"),
    )
    files = (".env", ".env.example", "docker-compose.yml", "docker-compose.prod.yml", "xiaozhi/config.py")
    for commit in commits:
        for filename in files:
            proc = subprocess.run(
                ["git", "-C", str(repo), "show", f"{commit}:{filename}"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode:
                continue
            for index, pattern in enumerate(patterns):
                for match in pattern.finditer(proc.stdout):
                    add_candidate(found, f"{commit[:10]}:{filename}:pattern{index + 1}", match.group(1))
    return found


def main() -> int:
    load_env_file()
    repo = Path(os.environ.get("SOURCE_REPOSITORY", "/workspace"))
    secrets = candidates_from_history(repo)
    ciphers = [(label, cipher(value)) for value, label in secrets.items()]
    print(f"Historical candidates tested: {len(ciphers)}")

    conn = psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "xiaozhi"),
        user=os.environ.get("POSTGRES_USER", "xiaozhi_app"),
        password=os.environ["POSTGRES_PASSWORD"],
    )
    unresolved = 0
    try:
        with conn.cursor() as cur:
            for table, key_column, column in (
                ("materials", "id", "api_url_ciphertext"),
                ("xiaozhi_tokens", "user_id::text || ':' || slot_number::text", "token_ciphertext"),
                ("relay_rooms", "id", "api_token_ciphertext"),
            ):
                cur.execute(f"SELECT {key_column}, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} <> ''")
                for row_id, value in cur.fetchall():
                    matches: list[str] = []
                    for label, candidate in ciphers:
                        try:
                            candidate.decrypt(str(value).encode("utf-8"))
                        except (InvalidToken, ValueError, TypeError):
                            continue
                        matches.append(label)
                    if matches:
                        print(f"{table}[{row_id}]: {','.join(matches)}")
                    else:
                        print(f"{table}[{row_id}]: UNRESOLVED")
                        unresolved += 1
    finally:
        conn.close()
    return 1 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
