#!/usr/bin/env python3
"""
Automated Data Migration Script: SQLite -> PostgreSQL for Xiaozhi Indonesia.
Preserves data integrity, transforms types (booleans, timestamps, JSONB),
resets sequence counters, and verifies row counts post-migration.
"""
import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate")


def parse_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "t")
    return False


def parse_dt(val: Any) -> Optional[str]:
    if not val:
        return None
    val_str = str(val).strip()
    if not val_str:
        return None
    return val_str


def parse_json(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            # Validate valid json
            parsed = json.loads(s)
            return json.dumps(parsed)
        except Exception:
            return json.dumps({"raw": s})
    return json.dumps(val)


TABLE_MIGRATION_ORDER = [
    "users",
    "categories",
    "materials",
    "xiaozhi_tokens",
    "chat_history",
    "relay_rooms",
    "relay_devices",
    "audio_queue",
    "registered_devices",
    "feature_settings",
    "user_limits",
    "reminders",
    "mcp_user_settings",
    "mcp_tool_toggles",
    "user_persona",
    "community_chats",
    "user_chat_read_state",
]


def migrate_data(sqlite_path: str, pg_dsn: str, truncate_first: bool = False, verify_only: bool = False) -> bool:
    if not Path(sqlite_path).exists():
        logger.error("SQLite database file not found at: %s", sqlite_path)
        return False

    logger.info("Connecting to SQLite: %s", sqlite_path)
    sq_conn = sqlite3.connect(sqlite_path)
    sq_conn.row_factory = sqlite3.Row

    logger.info("Connecting to PostgreSQL...")
    try:
        pg_conn = psycopg.connect(pg_dsn, row_factory=dict_row)
    except Exception as exc:
        logger.error("Could not connect to PostgreSQL: %s", exc)
        return False

    # Check which tables exist in SQLite
    sq_tables = set(
        r[0] for r in sq_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    )

    if verify_only:
        logger.info("Running verification only...")
        success = True
        for table in TABLE_MIGRATION_ORDER:
            if table not in sq_tables:
                continue
            sq_count = sq_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            with pg_conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                pg_count = cur.fetchone()["cnt"]
            match = sq_count == pg_count
            status_str = "MATCH" if match else "MISMATCH"
            logger.info("Table %-22s | SQLite: %6d | PG: %6d | [%s]", table, sq_count, pg_count, status_str)
            if not match:
                success = False
        return success

    if truncate_first:
        logger.warning("Truncating existing PostgreSQL tables in reverse dependency order...")
        with pg_conn.cursor() as cur:
            for table in reversed(TABLE_MIGRATION_ORDER):
                cur.execute(f"TRUNCATE TABLE {table} CASCADE;")
        pg_conn.commit()
        logger.info("All target tables truncated.")

    # Execute Migration
    logger.info("Beginning data copy...")
    for table in TABLE_MIGRATION_ORDER:
        if table not in sq_tables:
            logger.info("Skipping '%s' (not found in SQLite)", table)
            continue

        rows = sq_conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            logger.info("Table '%s' is empty in SQLite, skipping rows.", table)
            continue

        logger.info("Migrating '%s' (%d rows)...", table, len(rows))
        with pg_conn.cursor() as cur:
            # Table-specific record transformations
            if table == "users":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO users (id, username, password_hash, role, session_version, ui_theme, google_id, google_email, registered_with_google, created_at, updated_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["username"],
                            r["password_hash"],
                            r["role"] if "role" in keys else "user",
                            r["session_version"] if "session_version" in keys else 1,
                            r["ui_theme"] if "ui_theme" in keys else "neo",
                            r["google_id"] if "google_id" in keys else None,
                            r["google_email"] if "google_email" in keys else None,
                            parse_bool(r["registered_with_google"]) if "registered_with_google" in keys else False,
                            parse_dt(r["created_at"]),
                            parse_dt(r["updated_at"]) if "updated_at" in keys else None,
                        ),
                    )

            elif table == "categories":
                for r in rows:
                    cur.execute(
                        """
                        INSERT INTO categories (id, owner_id, name, created_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (r["id"], r["owner_id"], r["name"], parse_dt(r["created_at"])),
                    )

            elif table == "materials":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO materials (id, owner_id, title, category, content, keywords, source_type, source_hash, source_key, api_url_ciphertext, created_at, updated_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["owner_id"],
                            r["title"],
                            r["category"],
                            r["content"],
                            r["keywords"] if "keywords" in keys else None,
                            r["source_type"] if "source_type" in keys else None,
                            r["source_hash"] if "source_hash" in keys else None,
                            r["source_key"] if "source_key" in keys else None,
                            r["api_url_ciphertext"] if "api_url_ciphertext" in keys else None,
                            parse_dt(r["created_at"]),
                            parse_dt(r["updated_at"]) if "updated_at" in keys else None,
                        ),
                    )

            elif table == "xiaozhi_tokens":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO xiaozhi_tokens (user_id, token_ciphertext, token_hash, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        (
                            r["user_id"],
                            r["token_ciphertext"],
                            r["token_hash"],
                            parse_dt(r["created_at"]),
                            parse_dt(r["updated_at"]) if "updated_at" in keys else None,
                        ),
                    )

            elif table == "chat_history":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO chat_history (id, owner_id, token_hash, source, tool_name, user_message, xiaozhi_answer, request_payload, response_payload, created_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["owner_id"],
                            r["token_hash"] if "token_hash" in keys else None,
                            r["source"] if "source" in keys else "mcp_tool",
                            r["tool_name"] if "tool_name" in keys else "",
                            r["user_message"] if "user_message" in keys else None,
                            r["xiaozhi_answer"] if "xiaozhi_answer" in keys else None,
                            parse_json(r["request_payload"]) if "request_payload" in keys else None,
                            parse_json(r["response_payload"]) if "response_payload" in keys else None,
                            parse_dt(r["created_at"]),
                        ),
                    )

            elif table == "relay_rooms":
                for r in rows:
                    keys = r.keys()
                    raw_token = r["api_token"] if "api_token" in keys else ""
                    token_hash = hashlib.sha256(raw_token.encode()).hexdigest() if raw_token else None
                    cur.execute(
                        """
                        INSERT INTO relay_rooms (id, owner_id, nama_tempat, api_slug, api_token_ciphertext, api_token_hash, api_client_id, created_at, updated_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["owner_id"],
                            r["nama_tempat"],
                            r["api_slug"],
                            raw_token or None,
                            token_hash,
                            r["api_client_id"] if "api_client_id" in keys else None,
                            parse_dt(r["created_at"]),
                            parse_dt(r["updated_at"]) if "updated_at" in keys else None,
                        ),
                    )

            elif table == "relay_devices":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO relay_devices (id, room_id, relay_number, nama_relay, voice_command_on, voice_command_off, status)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["room_id"],
                            r["relay_number"],
                            r["nama_relay"] if "nama_relay" in keys else "",
                            r["voice_command_on"] if "voice_command_on" in keys else "",
                            r["voice_command_off"] if "voice_command_off" in keys else "",
                            (r["status"] if "status" in keys and r["status"] else "OFF").upper(),
                        ),
                    )

            elif table == "audio_queue":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO audio_queue (id, owner_id, title, stream_url, video_url, duration, video_id, status, created_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["owner_id"],
                            r["title"] if "title" in keys else None,
                            r["stream_url"],
                            r["video_url"] if "video_url" in keys else None,
                            r["duration"] if "duration" in keys else None,
                            r["video_id"] if "video_id" in keys else None,
                            r["status"] if "status" in keys else "pending",
                            parse_dt(r["created_at"]),
                        ),
                    )

            elif table == "registered_devices":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO registered_devices (id, owner_id, device_id, device_name, device_type, created_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["owner_id"],
                            r["device_id"],
                            r["device_name"] if "device_name" in keys else None,
                            r["device_type"] if "device_type" in keys else None,
                            parse_dt(r["created_at"]),
                        ),
                    )

            elif table == "feature_settings":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO feature_settings (user_id, virtual_smarthome_enabled, youtube_music_enabled, updated_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        (
                            r["user_id"],
                            parse_bool(r["virtual_smarthome_enabled"]) if "virtual_smarthome_enabled" in keys else True,
                            parse_bool(r["youtube_music_enabled"]) if "youtube_music_enabled" in keys else True,
                            parse_dt(r["updated_at"]) if "updated_at" in keys else None,
                        ),
                    )

            elif table == "user_limits":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO user_limits (user_id, max_materials, max_words_per_material, max_live_apis, max_relay_rooms, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        (
                            r["user_id"],
                            r["max_materials"] if "max_materials" in keys else 3,
                            r["max_words_per_material"] if "max_words_per_material" in keys else 6000,
                            r["max_live_apis"] if "max_live_apis" in keys else 2,
                            r["max_relay_rooms"] if "max_relay_rooms" in keys else 7,
                            parse_dt(r["created_at"]) if "created_at" in keys else datetime.utcnow().isoformat(),
                            parse_dt(r["updated_at"]) if "updated_at" in keys else None,
                        ),
                    )

            elif table == "reminders":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO reminders (id, owner_id, message, scheduled_at, status, created_at, sent_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["owner_id"],
                            r["message"],
                            parse_dt(r["scheduled_at"]),
                            r["status"] if "status" in keys else "pending",
                            parse_dt(r["created_at"]),
                            parse_dt(r["sent_at"]) if "sent_at" in keys else None,
                        ),
                    )

            elif table == "mcp_user_settings":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO mcp_user_settings (user_id, mcp_blocked, blocked_at, blocked_reason, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        (
                            r["user_id"],
                            parse_bool(r["mcp_blocked"]) if "mcp_blocked" in keys else False,
                            parse_dt(r["blocked_at"]) if "blocked_at" in keys else None,
                            r["blocked_reason"] if "blocked_reason" in keys else None,
                            parse_dt(r["created_at"]) if "created_at" in keys else datetime.utcnow().isoformat(),
                            parse_dt(r["updated_at"]) if "updated_at" in keys else None,
                        ),
                    )

            elif table == "mcp_tool_toggles":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO mcp_tool_toggles (id, user_id, tool_name, enabled, created_at, updated_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["user_id"],
                            r["tool_name"],
                            parse_bool(r["enabled"]) if "enabled" in keys else True,
                            parse_dt(r["created_at"]) if "created_at" in keys else datetime.utcnow().isoformat(),
                            parse_dt(r["updated_at"]) if "updated_at" in keys else None,
                        ),
                    )

            elif table == "user_persona":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO user_persona (id, owner_id, category, preference_key, preference_value, confidence, created_at, updated_at)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["owner_id"],
                            r["category"] if "category" in keys else "informasi_pribadi",
                            r["preference_key"],
                            r["preference_value"],
                            float(r["confidence"]) if "confidence" in keys and r["confidence"] is not None else 1.0,
                            parse_dt(r["created_at"]) if "created_at" in keys else datetime.utcnow().isoformat(),
                            parse_dt(r["updated_at"]) if "updated_at" in keys else datetime.utcnow().isoformat(),
                        ),
                    )

            elif table == "community_chats":
                for r in rows:
                    keys = r.keys()
                    cur.execute(
                        """
                        INSERT INTO community_chats (id, user_id, username, role, content, msg_type, voice_filename, voice_duration, reply_to_id, created_at, created_date)
                        OVERRIDING SYSTEM VALUE
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            r["id"],
                            r["user_id"],
                            r["username"],
                            r["role"] if "role" in keys else "user",
                            r["content"] if "content" in keys else "",
                            r["msg_type"] if "msg_type" in keys else "text",
                            r["voice_filename"] if "voice_filename" in keys else None,
                            int(r["voice_duration"]) if "voice_duration" in keys and r["voice_duration"] else 0,
                            r["reply_to_id"] if "reply_to_id" in keys else None,
                            parse_dt(r["created_at"]),
                            r["created_date"] if "created_date" in keys else datetime.utcnow().strftime("%Y-%m-%d"),
                        ),
                    )

            elif table == "user_chat_read_state":
                for r in rows:
                    cur.execute(
                        """
                        INSERT INTO user_chat_read_state (user_id, last_read_message_id, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id) DO NOTHING
                        """,
                        (r["user_id"], r["last_read_message_id"], parse_dt(r["updated_at"])),
                    )

        pg_conn.commit()

    # Step 3: Reset Identity Sequences for all tables with generated identity
    logger.info("Resetting PostgreSQL identity sequences...")
    identity_tables = [
        "users",
        "categories",
        "materials",
        "chat_history",
        "relay_rooms",
        "relay_devices",
        "audio_queue",
        "registered_devices",
        "mcp_tool_toggles",
        "user_persona",
        "community_chats",
    ]
    with pg_conn.cursor() as cur:
        for t in identity_tables:
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE(MAX(id), 0) + 1, false) FROM {t};")
    pg_conn.commit()

    logger.info("Verifying migrated records count...")
    all_matched = True
    for table in TABLE_MIGRATION_ORDER:
        if table not in sq_tables:
            continue
        sq_count = sq_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        with pg_conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            pg_count = cur.fetchone()["cnt"]
        match = sq_count == pg_count
        status_str = "SUCCESS" if match else "MISMATCH"
        logger.info("Table %-22s | SQLite: %6d | PG: %6d | [%s]", table, sq_count, pg_count, status_str)
        if not match:
            all_matched = False

    sq_conn.close()
    pg_conn.close()
    return all_matched


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite to PostgreSQL for Xiaozhi")
    parser.add_argument("--sqlite-path", default=os.getenv("SQLITE_DB_PATH", "data/xiaozhi.db"))
    parser.add_argument("--pg-dsn", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--truncate", action="store_true", help="Truncate PostgreSQL tables before copying")
    parser.add_argument("--verify", action="store_true", help="Verify record counts only without migrating")
    args = parser.parse_args()

    dsn = args.pg_dsn
    if not dsn:
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "xiaozhi")
        user = os.getenv("POSTGRES_USER", "xiaozhi_app")
        pw = os.getenv("POSTGRES_PASSWORD", "xiaozhi_secret")
        dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    ok = migrate_data(args.sqlite_path, dsn, truncate_first=args.truncate, verify_only=args.verify)
    if not ok:
        sys.exit(1)
    logger.info("Migration completed successfully!")


if __name__ == "__main__":
    main()
