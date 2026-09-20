"""
PostgreSQL-based store for Xiaozhi Indonesia.
Optimized for production deployment with connection pooling (Psycopg 3),
targeted partial indexing, FTS triggers, and low-latency queries.
"""
import hashlib
import json
import logging
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from xiaozhi.config import (
    DEFAULT_CATEGORIES,
    DEFAULT_UI_THEME,
    LIVE_API_CATEGORY,
    LIVE_API_SOURCE_TYPE,
    UI_THEMES,
    USER_LIMIT_DEFAULTS,
)
from xiaozhi.core.security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    normalize_token_hash,
    normalize_username,
    normalize_username_prefix,
    verify_password,
    xiaozhi_token_hash,
)
from xiaozhi.core.utils import (
    clean_text,
    compact_text,
    count_text_words,
    is_live_api_category,
    normalize_mac_address,
    parse_int_range,
    parse_limit_value,
    utc_now,
)

logger = logging.getLogger("xiaozhi.postgres")


def _format_ts(val: Any) -> Optional[str]:
    if val is None:
        return None
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)


class PostgresStore:
    """Production-grade PostgreSQL store for Xiaozhi Indonesia."""

    def __init__(
        self,
        dsn: Optional[str] = None,
        min_pool_size: Optional[int] = None,
        max_pool_size: Optional[int] = None,
    ) -> None:
        self.dsn = dsn or self._build_dsn()
        self.min_size = min_pool_size or int(os.getenv("PG_POOL_MIN", "5"))
        self.max_size = max_pool_size or int(os.getenv("PG_POOL_MAX", "15"))

        logger.info(
            "Initializing PostgreSQL connection pool (min=%d, max=%d)...",
            self.min_size,
            self.max_size,
        )
        self.pool = ConnectionPool(
            conninfo=self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        self._init_db()

    @staticmethod
    def _build_dsn() -> str:
        """Construct PostgreSQL DSN from environment variables."""
        if db_url := os.getenv("DATABASE_URL"):
            return db_url
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        dbname = os.getenv("POSTGRES_DB", "xiaozhi")
        user = os.getenv("POSTGRES_USER", "xiaozhi_app")
        password = os.getenv("POSTGRES_PASSWORD", "xiaozhi_secret")
        sslmode = os.getenv("POSTGRES_SSLMODE", "prefer")
        return f"postgresql://{user}:{password}@{host}:{port}/{dbname}?sslmode={sslmode}"

    @contextmanager
    def _get_conn(self, timeout: float = 5.0) -> Generator[psycopg.Connection, None, None]:
        """Acquire a connection from pool with a fail-fast timeout."""
        with self.pool.connection(timeout=timeout) as conn:
            yield conn

    def close(self) -> None:
        """Close connection pool cleanly."""
        if hasattr(self, "pool") and self.pool:
            self.pool.close()
            logger.info("PostgreSQL connection pool closed.")

    def _init_db(self) -> None:
        """Ensure extensions, schemas, triggers, and indexes exist."""
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    # 1. Extensions
                    cur.execute('CREATE EXTENSION IF NOT EXISTS "unaccent";')
                    cur.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm";')

                    # 2. Users Table
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            username VARCHAR(50) UNIQUE NOT NULL,
                            password_hash VARCHAR(255) NOT NULL,
                            role VARCHAR(20) NOT NULL DEFAULT 'user',
                            session_version INT NOT NULL DEFAULT 1,
                            ui_theme VARCHAR(30) NOT NULL DEFAULT 'neo',
                            google_id VARCHAR(100),
                            google_email VARCHAR(255),
                            registered_with_google BOOLEAN NOT NULL DEFAULT FALSE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ,
                            CONSTRAINT chk_user_role CHECK (role IN ('user', 'admin', 'operator'))
                        );
                        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
                        CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL;
                    """)

                    # 3. Categories Table
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS categories (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            name VARCHAR(100) NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            CONSTRAINT uq_categories_owner_name UNIQUE (owner_id, name)
                        );
                        CREATE INDEX IF NOT EXISTS idx_categories_owner ON categories(owner_id);
                    """)

                    # 4. Materials Table with search_vector & PL/pgSQL Trigger
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS materials (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            title VARCHAR(255) NOT NULL,
                            category VARCHAR(100) NOT NULL,
                            content TEXT NOT NULL,
                            keywords TEXT,
                            source_type VARCHAR(50),
                            source_hash VARCHAR(64),
                            source_key VARCHAR(128),
                            api_url_ciphertext TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ,
                            search_vector tsvector
                        );
                        CREATE INDEX IF NOT EXISTS idx_materials_owner ON materials(owner_id);
                        CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(owner_id, category);
                        CREATE INDEX IF NOT EXISTS idx_materials_source ON materials(source_hash, source_key);
                        CREATE INDEX IF NOT EXISTS idx_materials_created ON materials(owner_id, created_at DESC);
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_materials_owner_source_hash ON materials (owner_id, source_hash) 
                            WHERE source_hash IS NOT NULL AND source_hash != '';

                        CREATE OR REPLACE FUNCTION trg_materials_search_vector_fn()
                        RETURNS trigger 
                        LANGUAGE plpgsql AS $$
                        BEGIN
                            NEW.search_vector := 
                                setweight(to_tsvector('indonesian', coalesce(unaccent(NEW.title), '')), 'A') ||
                                setweight(to_tsvector('simple', coalesce(NEW.keywords, '')), 'B') ||
                                setweight(to_tsvector('indonesian', coalesce(unaccent(NEW.content), '')), 'C');
                            RETURN NEW;
                        END;
                        $$;

                        DROP TRIGGER IF EXISTS trg_materials_search_vector_update ON materials;
                        CREATE TRIGGER trg_materials_search_vector_update
                            BEFORE INSERT OR UPDATE OF title, keywords, content 
                            ON materials
                            FOR EACH ROW
                            EXECUTE FUNCTION trg_materials_search_vector_fn();

                        CREATE INDEX IF NOT EXISTS idx_materials_search_vector ON materials USING GIN (search_vector);
                        CREATE INDEX IF NOT EXISTS idx_materials_title_trgm ON materials USING GIN (title gin_trgm_ops);
                    """)

                    # 5. Tokens Table
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS xiaozhi_tokens (
                            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                            token_ciphertext TEXT NOT NULL,
                            token_hash VARCHAR(64) UNIQUE NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ
                        );
                        CREATE INDEX IF NOT EXISTS idx_tokens_hash ON xiaozhi_tokens(token_hash);
                    """)

                    # 6. Chat History Table
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS chat_history (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            token_hash VARCHAR(64),
                            source VARCHAR(50) NOT NULL DEFAULT 'mcp_tool',
                            tool_name VARCHAR(100) NOT NULL,
                            user_message TEXT,
                            xiaozhi_answer TEXT,
                            request_payload JSONB,
                            response_payload JSONB,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        );
                        CREATE INDEX IF NOT EXISTS idx_chat_owner ON chat_history(owner_id, id DESC);
                        CREATE INDEX IF NOT EXISTS idx_chat_token ON chat_history(token_hash);
                        CREATE INDEX IF NOT EXISTS idx_chat_tool ON chat_history(tool_name);
                    """)

                    # 7. Relay Rooms & Devices
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS relay_rooms (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            nama_tempat VARCHAR(100) NOT NULL,
                            api_slug VARCHAR(100) UNIQUE NOT NULL,
                            api_token_ciphertext TEXT,
                            api_token_hash VARCHAR(64),
                            api_client_id VARCHAR(100),
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ
                        );
                        CREATE INDEX IF NOT EXISTS idx_relay_owner ON relay_rooms(owner_id);
                        CREATE INDEX IF NOT EXISTS idx_relay_slug ON relay_rooms(api_slug);
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_relay_rooms_token_hash ON relay_rooms (api_token_hash) WHERE api_token_hash IS NOT NULL;

                        CREATE TABLE IF NOT EXISTS relay_devices (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            room_id BIGINT NOT NULL REFERENCES relay_rooms(id) ON DELETE CASCADE,
                            relay_number SMALLINT NOT NULL,
                            nama_relay VARCHAR(100),
                            voice_command_on VARCHAR(150),
                            voice_command_off VARCHAR(150),
                            status VARCHAR(10) NOT NULL DEFAULT 'OFF',
                            CONSTRAINT uq_room_relay_number UNIQUE (room_id, relay_number),
                            CONSTRAINT chk_relay_device_status CHECK (status IN ('ON', 'OFF'))
                        );
                        CREATE INDEX IF NOT EXISTS idx_relay_device_room ON relay_devices(room_id);
                    """)

                    # 8. Audio Queue
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS audio_queue (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            title VARCHAR(255),
                            stream_url TEXT NOT NULL,
                            video_url TEXT,
                            duration VARCHAR(50),
                            video_id VARCHAR(50),
                            status VARCHAR(20) NOT NULL DEFAULT 'pending',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            CONSTRAINT chk_audio_queue_status CHECK (status IN ('pending', 'playing', 'done', 'error', 'played', 'stopped', 'cancelled'))
                        );
                        CREATE INDEX IF NOT EXISTS idx_audio_owner ON audio_queue(owner_id, status);
                        CREATE INDEX IF NOT EXISTS idx_audio_queue_active ON audio_queue (owner_id, id ASC) WHERE status = 'pending';
                    """)

                    # 9. Registered Devices
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS registered_devices (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            device_id VARCHAR(100) NOT NULL,
                            device_name VARCHAR(100),
                            device_type VARCHAR(50),
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        );
                        CREATE INDEX IF NOT EXISTS idx_device_owner ON registered_devices(owner_id);
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_device_id ON registered_devices(device_id);
                    """)

                    # 10. Settings & Limits
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS feature_settings (
                            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                            virtual_smarthome_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                            youtube_music_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                            updated_at TIMESTAMPTZ
                        );

                        CREATE TABLE IF NOT EXISTS user_limits (
                            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                            max_materials INT NOT NULL DEFAULT 3,
                            max_words_per_material INT NOT NULL DEFAULT 6000,
                            max_live_apis INT NOT NULL DEFAULT 2,
                            max_relay_rooms INT NOT NULL DEFAULT 7,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ
                        );
                    """)

                    # 11. Reminders
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS reminders (
                            id VARCHAR(64) PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            message TEXT NOT NULL,
                            scheduled_at TIMESTAMPTZ NOT NULL,
                            status VARCHAR(20) NOT NULL DEFAULT 'pending',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            sent_at TIMESTAMPTZ,
                            CONSTRAINT chk_reminder_status CHECK (status IN ('pending', 'processing', 'triggered', 'sent', 'failed'))
                        );
                        CREATE INDEX IF NOT EXISTS idx_reminders_owner ON reminders(owner_id, status);
                        CREATE INDEX IF NOT EXISTS idx_reminders_pending_scheduled ON reminders (scheduled_at ASC) WHERE status = 'pending';
                    """)

                    # 12. MCP Settings & Toggles
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS mcp_user_settings (
                            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                            mcp_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                            blocked_at TIMESTAMPTZ,
                            blocked_reason TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ
                        );

                        CREATE TABLE IF NOT EXISTS mcp_tool_toggles (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            tool_name VARCHAR(100) NOT NULL,
                            enabled BOOLEAN NOT NULL DEFAULT TRUE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ,
                            CONSTRAINT uq_mcp_toggles_user_tool UNIQUE (user_id, tool_name)
                        );
                        CREATE INDEX IF NOT EXISTS idx_mcp_toggles_user ON mcp_tool_toggles(user_id);
                    """)

                    # 13. User Persona
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS user_persona (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            category VARCHAR(50) NOT NULL DEFAULT 'informasi_pribadi',
                            preference_key VARCHAR(100) NOT NULL,
                            preference_value TEXT NOT NULL,
                            confidence REAL NOT NULL DEFAULT 1.0,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            CONSTRAINT uq_persona_owner_key UNIQUE (owner_id, preference_key)
                        );
                        CREATE INDEX IF NOT EXISTS idx_persona_owner ON user_persona(owner_id);
                        CREATE INDEX IF NOT EXISTS idx_persona_owner_cat ON user_persona(owner_id, category);
                    """)

                    # 14. Community Chats
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS community_chats (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            username VARCHAR(50) NOT NULL,
                            role VARCHAR(20) NOT NULL DEFAULT 'user',
                            content TEXT,
                            msg_type VARCHAR(20) NOT NULL DEFAULT 'text',
                            voice_filename VARCHAR(255),
                            voice_duration INT DEFAULT 0,
                            reply_to_id BIGINT REFERENCES community_chats(id) ON DELETE SET NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            created_date VARCHAR(10) NOT NULL DEFAULT TO_CHAR(CURRENT_TIMESTAMP, 'YYYY-MM-DD'),
                            CONSTRAINT chk_community_msg_type CHECK (msg_type IN ('text', 'voice'))
                        );
                        CREATE INDEX IF NOT EXISTS idx_community_chats_pagination ON community_chats (id DESC, user_id);
                        CREATE INDEX IF NOT EXISTS idx_community_chats_user ON community_chats(user_id);
                        CREATE INDEX IF NOT EXISTS idx_community_chats_date ON community_chats(created_date);
                        CREATE INDEX IF NOT EXISTS idx_community_chats_reply ON community_chats(reply_to_id) WHERE reply_to_id IS NOT NULL;

                        CREATE TABLE IF NOT EXISTS user_chat_read_state (
                            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                            last_read_message_id BIGINT NOT NULL DEFAULT 0,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        );
                    """)
                conn.commit()
                logger.info("PostgreSQL database schema initialized successfully.")
        except Exception as exc:
            logger.error("Failed to initialize PostgreSQL schema: %s", exc)
            raise

    # ── User Management ────────────────────────────────────────────────────

    def create_user(self, username: str, password: str) -> Dict[str, Any]:
        username = normalize_username(username)
        password_hash = hash_password(password)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO users (username, password_hash, role, created_at)
                        VALUES (%s, %s, 'user', %s)
                        RETURNING id, username, role, session_version, ui_theme, registered_with_google
                        """,
                        (username, password_hash, utc_now()),
                    )
                    user = cur.fetchone()
                    conn.commit()
                    self._seed_default_categories(user["id"])
                    return user
                except psycopg.errors.UniqueViolation:
                    conn.rollback()
                    raise ValueError("Username sudah digunakan.")

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row["id"],
                    "username": row["username"],
                    "role": row["role"],
                    "session_version": row["session_version"],
                    "ui_theme": row["ui_theme"],
                    "google_id": row.get("google_id"),
                    "google_email": row.get("google_email"),
                    "registered_with_google": bool(row.get("registered_with_google")),
                    "created_at": _format_ts(row.get("created_at")),
                }

    get_user_by_id = get_user

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        username = normalize_username(username)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE username = %s", (username,))
                return cur.fetchone()

    def get_user_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        if not google_id:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE google_id = %s", (str(google_id),))
                return cur.fetchone()

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if not email:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE LOWER(google_email) = LOWER(%s)", (email.strip(),))
                return cur.fetchone()

    def link_google_account(self, user_id: int, google_id: str, google_email: str) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM users WHERE google_id = %s AND id != %s",
                    (str(google_id), int(user_id)),
                )
                if cur.fetchone():
                    raise ValueError("Akun Google ini sudah tertaut dengan akun lain.")
                cur.execute(
                    "UPDATE users SET google_id = %s, google_email = %s, updated_at = %s WHERE id = %s",
                    (str(google_id), str(google_email).strip().lower(), utc_now(), int(user_id)),
                )
            conn.commit()

    def unlink_google_account(self, user_id: int) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT registered_with_google FROM users WHERE id = %s", (int(user_id),))
                row = cur.fetchone()
                if not row:
                    raise ValueError("Pengguna tidak ditemukan.")
                if row.get("registered_with_google"):
                    raise ValueError("Akun ini didaftarkan menggunakan Google sehingga tautan bersifat permanen.")
                cur.execute(
                    "UPDATE users SET google_id = NULL, google_email = NULL, updated_at = %s WHERE id = %s",
                    (utc_now(), int(user_id)),
                )
            conn.commit()

    def create_google_user(self, username: str, google_id: str, google_email: str) -> Dict[str, Any]:
        username = normalize_username(username)
        random_pwd = secrets.token_urlsafe(32)
        password_hash = hash_password(random_pwd)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        INSERT INTO users (username, password_hash, role, google_id, google_email, registered_with_google, created_at)
                        VALUES (%s, %s, 'user', %s, %s, TRUE, %s)
                        RETURNING id, username, role, session_version, ui_theme, google_id, google_email, registered_with_google
                        """,
                        (username, password_hash, str(google_id), str(google_email).strip().lower(), utc_now()),
                    )
                    user = cur.fetchone()
                    conn.commit()
                    self._seed_default_categories(user["id"])
                    return user
                except psycopg.errors.UniqueViolation:
                    conn.rollback()
                    raise ValueError("Username sudah digunakan.")

    def search_users_by_prefix(self, prefix: str, limit: int = 8) -> List[Dict[str, str]]:
        prefix = normalize_username_prefix(prefix)
        if not prefix:
            return []
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT username FROM users WHERE username ILIKE %s ORDER BY username ASC LIMIT %s",
                    (f"{prefix}%", limit),
                )
                return cur.fetchall()

    def has_admin_user(self) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1")
                return cur.fetchone() is not None

    def ensure_admin_user(self, username: str, password: str) -> Dict[str, Any]:
        username = normalize_username(username)
        admin = self.get_user_by_username(username)
        if admin:
            if admin.get("role") != "admin":
                with self._get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("UPDATE users SET role = 'admin', updated_at = %s WHERE id = %s", (utc_now(), admin["id"]))
                    conn.commit()
                admin["role"] = "admin"
            return admin
        password_hash = hash_password(password)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (username, password_hash, role, created_at)
                    VALUES (%s, %s, 'admin', %s)
                    RETURNING id, username, role, session_version, ui_theme
                    """,
                    (username, password_hash, utc_now()),
                )
                admin = cur.fetchone()
            conn.commit()
        self._seed_default_categories(admin["id"])
        return admin

    def restore_regular_user_account(self, username: str, password: str, **kwargs) -> Dict[str, Any]:
        username = normalize_username(username)
        existing = self.get_user_by_username(username)
        pwd_hash = hash_password(password)
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if existing:
                    cur.execute(
                        "UPDATE users SET password_hash = %s, role = 'user', updated_at = %s WHERE id = %s RETURNING id, username, role",
                        (pwd_hash, now, existing["id"]),
                    )
                    user = cur.fetchone()
                else:
                    cur.execute(
                        "INSERT INTO users (username, password_hash, role, created_at) VALUES (%s, %s, 'user', %s) RETURNING id, username, role",
                        (username, pwd_hash, now),
                    )
                    user = cur.fetchone()
            conn.commit()
        self._seed_default_categories(user["id"])
        return user

    def rotate_user_session(self, user_id: int) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET session_version = session_version + 1, updated_at = %s WHERE id = %s",
                    (utc_now(), int(user_id)),
                )
            conn.commit()

    def set_user_theme(self, user_id: int, theme: str) -> Dict[str, Any]:
        theme = theme.strip().lower()
        if theme not in UI_THEMES:
            raise ValueError(f"Tema '{theme}' tidak valid.")
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET ui_theme = %s, updated_at = %s WHERE id = %s RETURNING ui_theme",
                    (theme, utc_now(), int(user_id)),
                )
                res = cur.fetchone()
            conn.commit()
        return res or {"ui_theme": theme}

    # ── Category Management ────────────────────────────────────────────────

    def _seed_default_categories(self, user_id: int) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                now = utc_now()
                for name in DEFAULT_CATEGORIES:
                    cur.execute(
                        """
                        INSERT INTO categories (owner_id, name, created_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (owner_id, name) DO NOTHING
                        """,
                        (int(user_id), name, now),
                    )
            conn.commit()

    def _ensure_live_api_category(self, user_id: int) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO categories (owner_id, name, created_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (owner_id, name) DO NOTHING
                    """,
                    (int(user_id), LIVE_API_CATEGORY, utc_now()),
                )
            conn.commit()

    def list_categories(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM categories WHERE owner_id = %s ORDER BY name ASC", (int(owner_id),))
                return cur.fetchall()

    def add_category(self, owner_id: int, name: str) -> None:
        name = clean_text(name)
        if not name:
            raise ValueError("Nama kategori tidak boleh kosong.")
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO categories (owner_id, name, created_at) VALUES (%s, %s, %s)",
                        (int(owner_id), name, utc_now()),
                    )
                    conn.commit()
                except psycopg.errors.UniqueViolation:
                    conn.rollback()
                    raise ValueError(f"Kategori '{name}' sudah ada.")

    def delete_category(self, owner_id: int, category_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM categories WHERE id = %s AND owner_id = %s", (int(category_id), int(owner_id)))
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def ensure_categories(self, owner_id: int, names: List[str]) -> List[str]:
        cleaned = [clean_text(name) for name in names if clean_text(name)]
        if not cleaned:
            return []
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                now = utc_now()
                for cat in cleaned:
                    cur.execute(
                        """
                        INSERT INTO categories (owner_id, name, created_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (owner_id, name) DO NOTHING
                        """,
                        (int(owner_id), cat, now),
                    )
            conn.commit()
        return cleaned

    # ── Materials Management ───────────────────────────────────────────────

    def list_materials(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM materials WHERE owner_id = %s ORDER BY id DESC", (int(owner_id),))
                rows = cur.fetchall()
                return [self._public_material(row) for row in rows]

    def get_material(self, owner_id: int, material_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM materials WHERE id = %s AND owner_id = %s", (int(material_id), int(owner_id)))
                row = cur.fetchone()
                return self._public_material(row) if row else None

    def search_materials(self, owner_id: int, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        keyword = keyword.strip()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if not keyword:
                    cur.execute(
                        "SELECT * FROM materials WHERE owner_id = %s ORDER BY id DESC LIMIT %s",
                        (int(owner_id), limit),
                    )
                    rows = cur.fetchall()
                else:
                    # Full Text Search utilizing search_vector GIN index & ranking
                    cur.execute(
                        """
                        SELECT m.*, ts_rank_cd(m.search_vector, query) as rank
                        FROM materials m,
                             plainto_tsquery('indonesian', %s) query
                        WHERE m.owner_id = %s AND (m.search_vector @@ query OR m.title ILIKE %s)
                        ORDER BY rank DESC, m.id DESC
                        LIMIT %s
                        """,
                        (keyword, int(owner_id), f"%{keyword}%", limit),
                    )
                    rows = cur.fetchall()
                return [self._public_material(row) for row in rows]

    def find_material_detail(
        self, owner_id: int, material_id: int = 0, title: str = "", keyword: str = ""
    ) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if material_id:
                    cur.execute("SELECT * FROM materials WHERE id = %s AND owner_id = %s", (int(material_id), int(owner_id)))
                    row = cur.fetchone()
                    if row:
                        return self._public_material(row)
                if title:
                    cur.execute(
                        "SELECT * FROM materials WHERE owner_id = %s AND LOWER(title) LIKE %s LIMIT 1",
                        (int(owner_id), f"%{title.lower()}%"),
                    )
                    row = cur.fetchone()
                    if row:
                        return self._public_material(row)
                if keyword:
                    cur.execute("SELECT * FROM materials WHERE owner_id = %s ORDER BY id DESC LIMIT 1", (int(owner_id),))
                    row = cur.fetchone()
                    if row:
                        return self._public_material(row)
        return None

    def _is_admin(self, owner_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT role FROM users WHERE id = %s", (int(owner_id),))
                row = cur.fetchone()
                return bool(row and row.get("role") == "admin")

    def _get_user_limits_dict(self, owner_id: int) -> Dict[str, int]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM user_limits WHERE user_id = %s", (int(owner_id),))
                row = cur.fetchone()
                if not row:
                    return dict(USER_LIMIT_DEFAULTS)
                return {k: int(row.get(k, v) or v) for k, v in USER_LIMIT_DEFAULTS.items()}

    def _enforce_material_limits(
        self, owner_id: int, content: str = "", api_url: str = "", is_new: bool = True, material_id: int = 0
    ) -> None:
        if self._is_admin(owner_id):
            return
        limits = self._get_user_limits_dict(owner_id)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if is_new:
                    cur.execute("SELECT COUNT(*) as cnt FROM materials WHERE owner_id = %s", (int(owner_id),))
                    cnt = cur.fetchone()["cnt"]
                    max_mat = limits.get("max_materials", 3)
                    if max_mat > 0 and cnt >= max_mat:
                        raise ValueError(f"Batas materi akun Anda telah tercapai ({max_mat} materi).")

                if api_url and is_new:
                    cur.execute(
                        "SELECT COUNT(*) as cnt FROM materials WHERE owner_id = %s AND source_type = %s",
                        (int(owner_id), LIVE_API_SOURCE_TYPE),
                    )
                    api_cnt = cur.fetchone()["cnt"]
                    max_api = limits.get("max_live_apis", 2)
                    if max_api > 0 and api_cnt >= max_api:
                        raise ValueError(f"Batas API realtime akun Anda telah tercapai ({max_api} API).")

                max_words = limits.get("max_words_per_material", 6000)
                if max_words > 0 and content:
                    words = count_text_words(content)
                    if words > max_words:
                        raise ValueError(f"Konten melebihi batas kata per materi ({words}/{max_words} kata).")

    def add_material(
        self,
        owner_id: int,
        title: str,
        category: str,
        content: str,
        keywords: str,
        api_url: str = "",
        **kwargs,
    ) -> int:
        self._enforce_material_limits(owner_id, content=content, api_url=api_url, is_new=True)
        now = utc_now()
        api_ciphertext = encrypt_secret(api_url.strip()) if api_url.strip() else None
        s_type = kwargs.get("source_type") or (LIVE_API_SOURCE_TYPE if api_url.strip() else None)
        source_hash = kwargs.get("source_hash")
        source_key = kwargs.get("source_key")

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO materials (
                        owner_id, title, category, content, keywords, source_type,
                        source_hash, source_key, api_url_ciphertext, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        int(owner_id),
                        title.strip(),
                        category.strip(),
                        content.strip(),
                        keywords.strip(),
                        s_type,
                        source_hash,
                        source_key,
                        api_ciphertext,
                        now,
                        now,
                    ),
                )
                material_id = cur.fetchone()["id"]
            conn.commit()
        return material_id

    def update_material(
        self, owner_id: int, material_id: int, title: str, category: str, content: str, keywords: str, api_url: str = ""
    ) -> bool:
        self._enforce_material_limits(owner_id, content=content, api_url=api_url, is_new=False, material_id=material_id)
        now = utc_now()
        api_ciphertext = encrypt_secret(api_url.strip()) if api_url.strip() else None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE materials SET
                        title = %s, category = %s, content = %s, keywords = %s,
                        api_url_ciphertext = %s, updated_at = %s
                    WHERE id = %s AND owner_id = %s
                    """,
                    (
                        title.strip(),
                        category.strip(),
                        content.strip(),
                        keywords.strip(),
                        api_ciphertext,
                        now,
                        int(material_id),
                        int(owner_id),
                    ),
                )
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def delete_material(self, owner_id: int, material_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM materials WHERE id = %s AND owner_id = %s", (int(material_id), int(owner_id)))
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def list_live_api_materials(self, owner_id: int, keyword: str = "", limit: int = 5) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM materials 
                    WHERE owner_id = %s AND source_type = %s 
                    ORDER BY id DESC LIMIT %s
                    """,
                    (int(owner_id), LIVE_API_SOURCE_TYPE, limit),
                )
                rows = cur.fetchall()
                return [self._public_material(row) for row in rows]

    def list_material_database(
        self, owner_id: int, keyword: str = "", category: str = "", limit: int = 10
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "SELECT * FROM materials WHERE owner_id = %s"
                params: List[Any] = [int(owner_id)]
                if category:
                    sql += " AND category = %s"
                    params.append(category)
                if keyword:
                    sql += " AND (title ILIKE %s OR content ILIKE %s)"
                    params.extend([f"%{keyword}%", f"%{keyword}%"])
                sql += " ORDER BY id DESC LIMIT %s"
                params.append(limit)
                cur.execute(sql, params)
                return [self._public_material(row) for row in cur.fetchall()]

    def _public_material(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Format material row for public view, decrypting api_url if needed."""
        d = dict(row)
        api_cipher = d.pop("api_url_ciphertext", None)
        d["api_url"] = decrypt_secret(api_cipher) if api_cipher else ""
        if "created_at" in d:
            d["created_at"] = _format_ts(d["created_at"])
        if "updated_at" in d:
            d["updated_at"] = _format_ts(d["updated_at"])
        return d

    # ── XiaoZhi Tokens ─────────────────────────────────────────────────────

    def set_xiaozhi_token(self, owner_id: int, token: str) -> None:
        token_clean = token.strip()
        t_hash = xiaozhi_token_hash(token_clean)
        t_cipher = encrypt_secret(token_clean)
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO xiaozhi_tokens (user_id, token_ciphertext, token_hash, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        token_ciphertext = EXCLUDED.token_ciphertext,
                        token_hash = EXCLUDED.token_hash,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (int(owner_id), t_cipher, t_hash, now, now),
                )
            conn.commit()

    def get_xiaozhi_token(self, owner_id: int) -> Optional[str]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT token_ciphertext FROM xiaozhi_tokens WHERE user_id = %s", (int(owner_id),))
                row = cur.fetchone()
                if row and row.get("token_ciphertext"):
                    return decrypt_secret(row["token_ciphertext"])
        return None

    def get_xiaozhi_token_info(self, owner_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT token_ciphertext, token_hash, created_at FROM xiaozhi_tokens WHERE user_id = %s", (int(owner_id),))
                row = cur.fetchone()
                if not row:
                    return None
                token = decrypt_secret(row["token_ciphertext"])
                preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else token
                return {
                    "preview": preview,
                    "token_hash": row["token_hash"],
                    "created_at": row["created_at"],
                }

    def delete_xiaozhi_token(self, owner_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM xiaozhi_tokens WHERE user_id = %s", (int(owner_id),))
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def delete_xiaozhi_token_by_hash(self, token: str) -> bool:
        t_hash = xiaozhi_token_hash(token)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM xiaozhi_tokens WHERE token_hash = %s", (t_hash,))
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def list_xiaozhi_tokens(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, token_ciphertext, token_hash FROM xiaozhi_tokens")
                rows = cur.fetchall()
        result = []
        for row in rows:
            token = decrypt_secret(row["token_ciphertext"])
            if token:
                result.append({
                    "user_id": row["user_id"],
                    "token": token,
                    "token_hash": row["token_hash"],
                })
        return result

    def find_user_by_mcp_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        t_hash = xiaozhi_token_hash(token)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.* FROM users u
                    JOIN xiaozhi_tokens t ON u.id = t.user_id
                    WHERE t.token_hash = %s
                    """,
                    (t_hash,),
                )
                return cur.fetchone()

    # ── Chat History ───────────────────────────────────────────────────────

    def add_chat_history(
        self,
        owner_id: int,
        *,
        source: str = "mcp_tool",
        tool_name: str,
        user_message: str = "",
        xiaozhi_answer: str = "",
        request_payload: Any = None,
        response_payload: Any = None,
        token_hash: str = "",
    ) -> None:
        """Log chat/tool execution with scoped asynchronous commit for maximum throughput."""
        req_json = json.dumps(request_payload) if request_payload is not None else None
        res_json = json.dumps(response_payload) if response_payload is not None else None
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # Scoped turbo: logging can tolerate async commit without threatening integrity of core tables
                cur.execute("SET LOCAL synchronous_commit = off;")
                cur.execute(
                    """
                    INSERT INTO chat_history (
                        owner_id, token_hash, source, tool_name, user_message,
                        xiaozhi_answer, request_payload, response_payload, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(owner_id),
                        token_hash or None,
                        source,
                        tool_name,
                        user_message,
                        xiaozhi_answer,
                        req_json,
                        res_json,
                        now,
                    ),
                )
            conn.commit()

    def upsert_chat_transcript(
        self, owner_id: int, *, tool_name: str, user_message: str = "", xiaozhi_answer: str = "", payload=None, token_hash: str = ""
    ) -> None:
        self.add_chat_history(
            owner_id=owner_id,
            source="transcript",
            tool_name=tool_name,
            user_message=user_message,
            xiaozhi_answer=xiaozhi_answer,
            request_payload=payload,
            token_hash=token_hash,
        )

    def list_chat_history(
        self,
        owner_id: int,
        token_hash: str = "",
        date: str = "",
        tool_name: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "SELECT * FROM chat_history WHERE owner_id = %s"
                params: List[Any] = [int(owner_id)]
                if token_hash:
                    sql += " AND token_hash = %s"
                    params.append(token_hash)
                if date:
                    sql += " AND TO_CHAR(created_at, 'YYYY-MM-DD') = %s"
                    params.append(date)
                if tool_name:
                    sql += " AND tool_name = %s"
                    params.append(tool_name)
                sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
                params.extend([limit, offset])
                cur.execute(sql, params)
                return cur.fetchall()

    def chat_history_dates(self, owner_id: int, token_hash: str = "") -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT TO_CHAR(created_at, 'YYYY-MM-DD') as date_str, COUNT(*) as count
                    FROM chat_history
                    WHERE owner_id = %s
                """
                params: List[Any] = [int(owner_id)]
                if token_hash:
                    sql += " AND token_hash = %s"
                    params.append(token_hash)
                sql += " GROUP BY date_str ORDER BY date_str DESC"
                cur.execute(sql, params)
                return cur.fetchall()

    def chat_history_stats(self, owner_id: int, token_hash: str = "", date: str = "") -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "SELECT COUNT(*) as total_calls FROM chat_history WHERE owner_id = %s"
                params: List[Any] = [int(owner_id)]
                if token_hash:
                    sql += " AND token_hash = %s"
                    params.append(token_hash)
                if date:
                    sql += " AND TO_CHAR(created_at, 'YYYY-MM-DD') = %s"
                    params.append(date)
                cur.execute(sql, params)
                row = cur.fetchone()
                return {"total_calls": row["total_calls"] if row else 0}

    def clear_chat_history(self, owner_id: int) -> int:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_history WHERE owner_id = %s", (int(owner_id),))
                count = cur.rowcount
            conn.commit()
        return count

    # ── User Persona & Preferences ─────────────────────────────────────────

    def save_user_preference(
        self,
        owner_id: int,
        category: str,
        preference_key: str,
        preference_value: str,
        confidence: float = 1.0,
    ) -> Dict[str, Any]:
        cat_clean = str(category or "informasi_pribadi").strip().lower()
        key_clean = str(preference_key or "").strip()
        val_clean = str(preference_value or "").strip()
        conf_val = max(0.1, min(float(confidence or 1.0), 1.0))
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_persona (owner_id, category, preference_key, preference_value, confidence, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(owner_id, preference_key) DO UPDATE SET
                        category = EXCLUDED.category,
                        preference_value = EXCLUDED.preference_value,
                        confidence = EXCLUDED.confidence,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (int(owner_id), cat_clean, key_clean, val_clean, conf_val, now, now),
                )
            conn.commit()
        return {
            "success": True,
            "owner_id": owner_id,
            "category": cat_clean,
            "preference_key": key_clean,
            "preference_value": val_clean,
            "confidence": conf_val,
            "updated_at": now,
        }

    def get_user_persona(self, owner_id: int, category: str = "") -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "SELECT * FROM user_persona WHERE owner_id = %s"
                params: List[Any] = [int(owner_id)]
                if category:
                    sql += " AND category = %s"
                    params.append(category.strip().lower())
                sql += " ORDER BY category ASC, updated_at DESC"
                cur.execute(sql, params)
                return cur.fetchall()

    def delete_user_preference(self, owner_id: int, preference_key: str) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_persona WHERE owner_id = %s AND preference_key = %s", (int(owner_id), preference_key.strip()))
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    # ── Relay Rooms & Devices ──────────────────────────────────────────────

    def list_relay_rooms(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM relay_rooms WHERE owner_id = %s ORDER BY id ASC", (int(owner_id),))
                rooms = cur.fetchall()
                result = []
                for room in rooms:
                    cur.execute("SELECT * FROM relay_devices WHERE room_id = %s ORDER BY relay_number ASC", (room["id"],))
                    devices = cur.fetchall()
                    result.append({
                        "id": room["id"],
                        "nama_tempat": room["nama_tempat"],
                        "api_slug": room["api_slug"],
                        "api_client_id": room["api_client_id"],
                        "relays": devices,
                    })
                return result

    def add_relay_room(
        self, owner_id: int, nama_tempat: str, api_slug: str, api_token: str, api_client_id: str, relays: List[Dict]
    ) -> int:
        if not self._is_admin(owner_id):
            limits = self._get_user_limits_dict(owner_id)
            max_rooms = limits.get("max_relay_rooms", 7)
            if max_rooms > 0:
                with self._get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) as cnt FROM relay_rooms WHERE owner_id = %s", (int(owner_id),))
                        if cur.fetchone()["cnt"] >= max_rooms:
                            raise ValueError(f"Batas perangkat Relay Nyata akun Anda telah tercapai ({max_rooms} ruangan).")

        t_cipher = encrypt_secret(api_token.strip()) if api_token.strip() else None
        t_hash = hashlib.sha256(api_token.strip().encode()).hexdigest() if api_token.strip() else None
        now = utc_now()

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO relay_rooms (owner_id, nama_tempat, api_slug, api_token_ciphertext, api_token_hash, api_client_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (int(owner_id), nama_tempat.strip(), api_slug.strip(), t_cipher, t_hash, api_client_id.strip(), now),
                )
                room_id = cur.fetchone()["id"]
                for relay in relays:
                    cur.execute(
                        """
                        INSERT INTO relay_devices (room_id, relay_number, nama_relay, voice_command_on, voice_command_off, status)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            room_id,
                            int(relay.get("relay_number", 1)),
                            relay.get("nama_relay", ""),
                            relay.get("voice_command_on", ""),
                            relay.get("voice_command_off", ""),
                            relay.get("status", "OFF").upper(),
                        ),
                    )
            conn.commit()
        return room_id

    def get_relay_room(self, owner_id: int, room_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM relay_rooms WHERE id = %s AND owner_id = %s", (int(room_id), int(owner_id)))
                room = cur.fetchone()
                if not room:
                    return None
                cur.execute("SELECT * FROM relay_devices WHERE room_id = %s ORDER BY relay_number ASC", (int(room_id),))
                return {
                    "id": room["id"],
                    "nama_tempat": room["nama_tempat"],
                    "api_slug": room["api_slug"],
                    "relays": cur.fetchall(),
                }

    def update_relay_room(self, owner_id: int, room_id: int, nama_tempat: str, relays: List[Dict]) -> None:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE relay_rooms SET nama_tempat = %s, updated_at = %s WHERE id = %s AND owner_id = %s",
                    (nama_tempat, now, int(room_id), int(owner_id)),
                )
                for relay in relays:
                    cur.execute(
                        """
                        INSERT INTO relay_devices (room_id, relay_number, nama_relay, voice_command_on, voice_command_off, status)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT(room_id, relay_number) DO UPDATE SET
                            nama_relay = EXCLUDED.nama_relay,
                            voice_command_on = EXCLUDED.voice_command_on,
                            voice_command_off = EXCLUDED.voice_command_off
                        """,
                        (
                            int(room_id),
                            int(relay.get("relay_number", 1)),
                            relay.get("nama_relay", ""),
                            relay.get("voice_command_on", ""),
                            relay.get("voice_command_off", ""),
                            relay.get("status", "OFF").upper(),
                        ),
                    )
            conn.commit()

    def delete_relay_room(self, owner_id: int, room_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM relay_rooms WHERE id = %s AND owner_id = %s", (int(room_id), int(owner_id)))
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def update_relay_status(self, owner_id: int, room_id: int, relay_number: int, status: str) -> None:
        status_clean = status.strip().upper()
        if status_clean not in ("ON", "OFF"):
            raise ValueError("Status relay harus ON atau OFF.")
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE relay_devices SET status = %s WHERE room_id = %s AND relay_number = %s",
                    (status_clean, int(room_id), int(relay_number)),
                )
            conn.commit()

    def match_real_relay_command(self, owner_id: int, user_message: str) -> Optional[Dict[str, Any]]:
        from xiaozhi.core.utils import normalize_voice_command
        normalized_msg = normalize_voice_command(user_message)
        rooms = self.list_relay_rooms(owner_id)
        for room in rooms:
            for relay in room.get("relays", []):
                on_cmd = normalize_voice_command(relay.get("voice_command_on", ""))
                off_cmd = normalize_voice_command(relay.get("voice_command_off", ""))
                if on_cmd and on_cmd in normalized_msg:
                    return {"room": room, "relay": relay, "command": "ON"}
                if off_cmd and off_cmd in normalized_msg:
                    return {"room": room, "relay": relay, "command": "OFF"}
        return None

    # ── Audio Queue ────────────────────────────────────────────────────────

    def queue_audio_command(
        self, owner_id: int, title: str = "", stream_url: str = "", video_url: str = "", duration: str = "", video_id: str = ""
    ) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audio_queue (owner_id, title, stream_url, video_url, duration, video_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (int(owner_id), title, stream_url, video_url, duration, video_id, utc_now()),
                )
                qid = cur.fetchone()["id"]
            conn.commit()
        return {"id": qid, "title": title, "stream_url": stream_url}

    def get_audio_commands(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM audio_queue WHERE owner_id = %s AND status = 'pending' ORDER BY id ASC",
                    (int(owner_id),),
                )
                return cur.fetchall()

    def get_pending_audio_commands(self, owner_id: int) -> List[Dict[str, Any]]:
        return self.get_audio_commands(owner_id)

    def ack_audio_command(self, owner_id: int, command_id: Union[int, str]) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE audio_queue SET status = 'played' WHERE id = %s AND owner_id = %s", (int(command_id), int(owner_id)))
            conn.commit()

    def get_current_audio(self, owner_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM audio_queue WHERE owner_id = %s AND status = 'pending' ORDER BY id DESC LIMIT 1",
                    (int(owner_id),),
                )
                return cur.fetchone()

    def get_now_playing(self, owner_id: int) -> Optional[Dict[str, Any]]:
        return self.get_current_audio(owner_id)

    # ── Devices ────────────────────────────────────────────────────────────

    def get_user_mac_address(self, user_id: int) -> Optional[str]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT device_id FROM registered_devices WHERE owner_id = %s ORDER BY id DESC LIMIT 1",
                    (int(user_id),),
                )
                row = cur.fetchone()
                return str(row["device_id"]).upper() if row and row.get("device_id") else None

    def list_registered_devices(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM registered_devices WHERE owner_id = %s ORDER BY created_at DESC",
                    (int(owner_id),),
                )
                return cur.fetchall()

    def list_devices(self, owner_id: int) -> List[Dict[str, Any]]:
        return self.list_registered_devices(owner_id)

    def register_device(
        self, owner_id: int, device_id: str = "", mac_address: str = "", name: str = "", device_name: str = "", device_type: str = ""
    ) -> Dict[str, Any]:
        raw_mac = device_id or mac_address
        normalized_id = normalize_mac_address(raw_mac)
        if not normalized_id:
            raise ValueError("Device ID / MAC address diperlukan.")
        dev_name = (name or device_name).strip() if (name or device_name) else f"ESP32 ({normalized_id[-5:]})"
        dev_type = device_type.strip() if device_type else "esp32"
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO registered_devices (owner_id, device_id, device_name, device_type, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT(device_id) DO UPDATE SET
                        owner_id = EXCLUDED.owner_id,
                        device_name = EXCLUDED.device_name,
                        device_type = EXCLUDED.device_type
                    RETURNING id
                    """,
                    (int(owner_id), normalized_id, dev_name, dev_type, now),
                )
                dev_id = cur.fetchone()["id"]
            conn.commit()
        return {"id": dev_id, "device_id": normalized_id, "name": dev_name}

    def delete_device(self, owner_id: int, device_id: str) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM registered_devices WHERE device_id = %s AND owner_id = %s",
                    (device_id, int(owner_id)),
                )
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def find_device_by_id(self, device_id: str) -> Optional[Dict[str, Any]]:
        if not device_id:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM registered_devices WHERE LOWER(device_id) = %s", (device_id.lower().strip(),))
                return cur.fetchone()

    def is_device_owned_by(self, device_id: str, owner_id: int) -> bool:
        dev = self.find_device_by_id(device_id)
        if not dev:
            return False
        return int(dev.get("owner_id", 0)) == int(owner_id)

    def get_user_persona_analysis(self, owner_id: int) -> Dict[str, Any]:
        """Runs RAG & Vector Semantic profiling on user chat history & stored personas."""
        from xiaozhi.services.semantic_memory_service import analyze_user_persona_from_chats
        try:
            chats = self.list_chat_history(owner_id, limit=300)
            stored_personas = self.get_user_persona(owner_id)
            return analyze_user_persona_from_chats(chats, stored_personas)
        except Exception as e:
            logger.exception("Error analyzing user persona for owner %s: %s", owner_id, e)
            return {
                "total_chats_analyzed": 0,
                "confidence_level": "Data Awal",
                "personality": {
                    "primary_trait": "Ambivert Seimbang",
                    "introvert_percent": 50,
                    "extrovert_percent": 50,
                    "dominant": "ambivert",
                    "description": "Sedang mempelajari kepribadian Anda melalui percakapan.",
                },
                "hobbies": [],
                "challenges": [],
                "activities": [],
                "preferences": [],
            }

    def get_pending_relay_commands(self, owner_id: int, room_id: int) -> List[Dict[str, Any]]:
        return []

    # ── Feature Settings & Quota ───────────────────────────────────────────

    def get_feature_settings(self, owner_id: int) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM feature_settings WHERE user_id = %s", (int(owner_id),))
                row = cur.fetchone()
                if not row:
                    return {"virtual_smarthome_enabled": True, "youtube_music_enabled": True}
                return {
                    "virtual_smarthome_enabled": bool(row["virtual_smarthome_enabled"]),
                    "youtube_music_enabled": bool(row["youtube_music_enabled"]),
                }

    def set_feature_setting(self, owner_id: int, key: str, value: bool) -> None:
        if key not in ("virtual_smarthome_enabled", "youtube_music_enabled"):
            raise ValueError(f"Feature setting '{key}' tidak dikenali.")
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                query = sql.SQL("""
                    INSERT INTO feature_settings (user_id, {field}, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        {field} = EXCLUDED.{field},
                        updated_at = EXCLUDED.updated_at
                """).format(field=sql.Identifier(key))
                cur.execute(query, (int(owner_id), bool(value), utc_now()))
            conn.commit()

    def get_user_features(self, owner_id: int) -> Dict[str, bool]:
        settings = self.get_feature_settings(owner_id)
        return {
            "youtube_music": settings.get("youtube_music_enabled", True),
            "virtual_smarthome": settings.get("virtual_smarthome_enabled", True),
        }

    def set_user_feature(self, owner_id: int, feature: str, enabled: bool) -> None:
        key = f"{feature}_enabled"
        self.set_feature_setting(owner_id, key, enabled)

    def user_quota(self, owner_id: int) -> Dict[str, Any]:
        is_admin = self._is_admin(owner_id)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as cnt FROM materials WHERE owner_id = %s", (int(owner_id),))
                mat_cnt = cur.fetchone()["cnt"]
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM materials WHERE owner_id = %s AND source_type = %s",
                    (int(owner_id), LIVE_API_SOURCE_TYPE),
                )
                api_cnt = cur.fetchone()["cnt"]
                cur.execute("SELECT COUNT(*) as cnt FROM relay_rooms WHERE owner_id = %s", (int(owner_id),))
                relay_cnt = cur.fetchone()["cnt"]

        usage = {"materials": mat_cnt, "live_apis": api_cnt, "relay_rooms": relay_cnt}
        if is_admin:
            return {
                "is_admin": True,
                "limits": {k: 0 for k in USER_LIMIT_DEFAULTS},
                "usage": usage,
                "materials": self._quota_item(usage["materials"], 0),
                "words_per_material": self._quota_item(0, 0),
                "live_apis": self._quota_item(usage["live_apis"], 0),
                "relay_rooms": self._quota_item(usage["relay_rooms"], 0),
                "alerts": [],
                "features": self.get_user_features(owner_id),
            }

        limits = self._get_user_limits_dict(owner_id)
        quota = {
            "is_admin": False,
            "limits": limits,
            "usage": usage,
            "materials": self._quota_item(usage["materials"], limits["max_materials"]),
            "words_per_material": self._quota_item(0, limits["max_words_per_material"]),
            "live_apis": self._quota_item(usage["live_apis"], limits["max_live_apis"]),
            "relay_rooms": self._quota_item(usage["relay_rooms"], limits["max_relay_rooms"]),
            "alerts": [],
            "features": self.get_user_features(owner_id),
        }
        if quota["materials"]["reached"]:
            quota["alerts"].append("Batas total materi sudah tercapai.")
        if quota["live_apis"]["reached"]:
            quota["alerts"].append("Batas API realtime sudah tercapai.")
        if quota["relay_rooms"]["reached"]:
            quota["alerts"].append("Batas perangkat Relay Nyata sudah tercapai.")
        return quota

    @staticmethod
    def _quota_item(used: int, limit: int) -> Dict[str, Any]:
        used = max(0, int(used or 0))
        limit = max(0, int(limit or 0))
        unlimited = limit == 0
        remaining = None if unlimited else max(0, limit - used)
        return {
            "used": used,
            "limit": limit,
            "unlimited": unlimited,
            "remaining": remaining,
            "reached": bool(limit and used >= limit),
            "label": "Tanpa batas" if unlimited else f"{used}/{limit}",
        }

    def set_user_limits(self, target_user_id: int, **kwargs) -> Dict[str, int]:
        limits = {k: parse_limit_value(v, field=k) for k, v in kwargs.items()}
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_limits (user_id, max_materials, max_words_per_material, max_live_apis, max_relay_rooms, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        max_materials = EXCLUDED.max_materials,
                        max_words_per_material = EXCLUDED.max_words_per_material,
                        max_live_apis = EXCLUDED.max_live_apis,
                        max_relay_rooms = EXCLUDED.max_relay_rooms,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        int(target_user_id),
                        limits.get("max_materials", 3),
                        limits.get("max_words_per_material", 6000),
                        limits.get("max_live_apis", 2),
                        limits.get("max_relay_rooms", 7),
                        now,
                        now,
                    ),
                )
            conn.commit()
        return limits

    def list_admin_manageable_users(self, admin_username: str = "") -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE role != 'admin' ORDER BY id DESC")
                rows = cur.fetchall()

        # Get all MCP connection states
        try:
            from xiaozhi.services.mcp_service import mcp_connection_states, mcp_state_lock, mcp_bridge_tasks
            with mcp_state_lock:
                all_mcp_states = {int(uid): dict(state) for uid, state in mcp_connection_states.items()}
        except ImportError:
            all_mcp_states = {}

        result = []
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    user_id = row["id"]
                    cur.execute("SELECT COUNT(*) as cnt FROM materials WHERE owner_id = %s", (user_id,))
                    mat_cnt = cur.fetchone()["cnt"]
                    cur.execute(
                        "SELECT COUNT(*) as cnt FROM materials WHERE owner_id = %s AND source_type = %s",
                        (user_id, LIVE_API_SOURCE_TYPE),
                    )
                    api_cnt = cur.fetchone()["cnt"]
                    cur.execute("SELECT COUNT(*) as cnt FROM relay_rooms WHERE owner_id = %s", (user_id,))
                    relay_cnt = cur.fetchone()["cnt"]
                    usage = {"materials": mat_cnt, "live_apis": api_cnt, "relay_rooms": relay_cnt}

                    cur.execute("SELECT * FROM user_limits WHERE user_id = %s", (user_id,))
                    limits_row = cur.fetchone()
                    limits = dict(USER_LIMIT_DEFAULTS)
                    if limits_row:
                        limits = {k: limits_row.get(k, v) for k, v in USER_LIMIT_DEFAULTS.items()}

                    cur.execute(
                        "SELECT device_id, device_name FROM registered_devices WHERE owner_id = %s ORDER BY id DESC LIMIT 1",
                        (user_id,),
                    )
                    dev_row = cur.fetchone()
                    device_mac = str(dev_row["device_id"]).upper() if dev_row and dev_row.get("device_id") else ""
                    device_name = dev_row["device_name"] if dev_row and dev_row.get("device_name") else ""

                    # Active music session
                    active_session = None
                    try:
                        from xiaozhi.services.playback_tracker import playback_tracker
                        active_session = playback_tracker.get_session_by_user(user_id)
                    except Exception:
                        pass
                    is_playing = active_session is not None
                    current_track = active_session.title if active_session else ""

                    # MCP status
                    mcp_state = all_mcp_states.get(user_id, {})
                    cur.execute("SELECT COUNT(*) as cnt FROM xiaozhi_tokens WHERE user_id = %s", (user_id,))
                    has_token = cur.fetchone()["cnt"] > 0
                    is_connected = mcp_state.get("connected", False)

                    result.append({
                        "id": user_id,
                        "username": row["username"],
                        "role": row["role"],
                        "created_at": _format_ts(row.get("created_at")) or "",
                        "limits": limits,
                        "usage": usage,
                        "features": self.get_user_features(user_id),
                        "device_mac": device_mac,
                        "device_name": device_name,
                        "is_playing": is_playing,
                        "current_track": current_track,
                        "mcp_status": {
                            "has_token": has_token,
                            "connected": is_connected,
                            "message": mcp_state.get("message", ""),
                            "updated_at": mcp_state.get("updated_at", ""),
                        },
                    })
        result.sort(key=lambda u: (
            0 if u.get("is_playing") else 1,
            0 if u.get("mcp_status", {}).get("connected") else 1,
            -int(u.get("id", 0))
        ))
        return result

    # ── Reminders ──────────────────────────────────────────────────────────

    def add_reminder(self, owner_id: int, reminder_id: str, message: str, scheduled_at: str) -> Dict[str, Any]:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reminders (id, owner_id, message, scheduled_at, status, created_at)
                    VALUES (%s, %s, %s, %s, 'pending', %s)
                    """,
                    (reminder_id, int(owner_id), message.strip(), scheduled_at, now),
                )
            conn.commit()
        return {
            "id": reminder_id,
            "owner_id": owner_id,
            "message": message,
            "scheduled_at": scheduled_at,
            "status": "pending",
            "created_at": now,
        }

    def list_reminders(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM reminders WHERE owner_id = %s ORDER BY scheduled_at ASC", (int(owner_id),))
                return cur.fetchall()

    def delete_reminder(self, owner_id: int, reminder_id: str) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM reminders WHERE id = %s AND owner_id = %s", (reminder_id, int(owner_id)))
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def get_due_reminders(self) -> List[Dict[str, Any]]:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM reminders WHERE status = 'pending' AND scheduled_at <= %s ORDER BY scheduled_at ASC",
                    (now,),
                )
                reminders = cur.fetchall()
                if reminders:
                    ids = [r["id"] for r in reminders]
                    cur.execute(
                        "UPDATE reminders SET status = 'triggered' WHERE id = ANY(%s)",
                        (ids,),
                    )
            conn.commit()
        return reminders

    def mark_reminder_sent(self, reminder_id: str) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE reminders SET status = 'sent', sent_at = %s WHERE id = %s", (utc_now(), reminder_id))
            conn.commit()

    # ── MCP User Settings & Toggles ────────────────────────────────────────

    def get_mcp_settings(self, user_id: int) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM mcp_user_settings WHERE user_id = %s", (int(user_id),))
                row = cur.fetchone()
                if not row:
                    return {"user_id": user_id, "mcp_blocked": False, "blocked_at": None, "blocked_reason": None}
                return {
                    "user_id": row["user_id"],
                    "mcp_blocked": bool(row["mcp_blocked"]),
                    "blocked_at": row["blocked_at"],
                    "blocked_reason": row["blocked_reason"],
                }

    def set_mcp_blocked(self, user_id: int, blocked: bool, reason: str = "") -> None:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_user_settings (user_id, mcp_blocked, blocked_at, blocked_reason, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        mcp_blocked = EXCLUDED.mcp_blocked,
                        blocked_at = EXCLUDED.blocked_at,
                        blocked_reason = EXCLUDED.blocked_reason,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (int(user_id), bool(blocked), now if blocked else None, reason if blocked else "", now, now),
                )
            conn.commit()

    def is_mcp_blocked(self, user_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT mcp_blocked FROM mcp_user_settings WHERE user_id = %s", (int(user_id),))
                row = cur.fetchone()
                return bool(row["mcp_blocked"]) if row else False

    def get_mcp_tool_toggles(self, user_id: int) -> Dict[str, bool]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tool_name, enabled FROM mcp_tool_toggles WHERE user_id = %s", (int(user_id),))
                return {row["tool_name"]: bool(row["enabled"]) for row in cur.fetchall()}

    def set_mcp_tool_toggle(self, user_id: int, tool_name: str, enabled: bool) -> None:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_tool_toggles (user_id, tool_name, enabled, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT(user_id, tool_name) DO UPDATE SET
                        enabled = EXCLUDED.enabled,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (int(user_id), tool_name, bool(enabled), now, now),
                )
            conn.commit()

    def is_mcp_tool_enabled(self, user_id: int, tool_name: str) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT enabled FROM mcp_tool_toggles WHERE user_id = %s AND tool_name = %s",
                    (int(user_id), tool_name),
                )
                row = cur.fetchone()
                return bool(row["enabled"]) if row else True

    def get_all_mcp_settings_for_admin(self) -> List[Dict[str, Any]]:
        """Get MCP settings for all users (admin view)."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT u.id as user_id, u.username,
                           COALESCE(s.mcp_blocked, FALSE) as mcp_blocked,
                           s.blocked_at, s.blocked_reason
                    FROM users u
                    LEFT JOIN mcp_user_settings s ON u.id = s.user_id
                    WHERE u.role != 'admin'
                    ORDER BY u.username
                """)
                return cur.fetchall()

    # ── Community Chat ─────────────────────────────────────────────────────

    def add_community_chat(
        self,
        user_id: int,
        username: str,
        role: str,
        content: str = "",
        msg_type: str = "text",
        voice_filename: Optional[str] = None,
        voice_duration: int = 0,
        reply_to_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Insert a community chat message and broadcast notification event."""
        now_dt = datetime.now()
        created_at = utc_now()
        created_date = now_dt.strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO community_chats (
                        user_id, username, role, content, msg_type,
                        voice_filename, voice_duration, reply_to_id, created_at, created_date
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        int(user_id),
                        username,
                        role,
                        content or "",
                        msg_type,
                        voice_filename,
                        int(voice_duration or 0),
                        reply_to_id,
                        created_at,
                        created_date,
                    ),
                )
                message_id = cur.fetchone()["id"]
                # Send lightweight NOTIFY signal (only message ID)
                cur.execute("SELECT pg_notify('community_chat', %s)", (json.dumps({"id": message_id}),))
            conn.commit()

        reply_to = None
        if reply_to_id:
            reply_to = self.get_community_chat(reply_to_id)

        return {
            "id": message_id,
            "user_id": user_id,
            "username": username,
            "role": role,
            "content": content or "",
            "msg_type": msg_type,
            "voice_filename": voice_filename,
            "voice_duration": int(voice_duration or 0),
            "reply_to_id": reply_to_id,
            "reply_to": reply_to,
            "created_at": created_at,
            "created_date": created_date,
        }

    def get_community_chat(self, message_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.*,
                           r.username AS reply_username,
                           r.role AS reply_role,
                           r.content AS reply_content,
                           r.msg_type AS reply_msg_type
                    FROM community_chats c
                    LEFT JOIN community_chats r ON c.reply_to_id = r.id
                    WHERE c.id = %s
                    """,
                    (int(message_id),),
                )
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                reply_data = None
                if data.get("reply_to_id") and data.get("reply_username"):
                    reply_data = {
                        "id": data["reply_to_id"],
                        "username": data["reply_username"],
                        "role": data["reply_role"],
                        "content": data["reply_content"],
                        "msg_type": data["reply_msg_type"],
                    }
                data["reply_to"] = reply_data
                return data

    def list_community_chats(self, limit: int = 100, before_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Retrieve chats using Keyset cursor pagination (before_id) for O(1) performance."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if before_id:
                    cur.execute(
                        """
                        SELECT c.*,
                               r.username AS reply_username,
                               r.role AS reply_role,
                               r.content AS reply_content,
                               r.msg_type AS reply_msg_type
                        FROM community_chats c
                        LEFT JOIN community_chats r ON c.reply_to_id = r.id
                        WHERE c.id < %s
                        ORDER BY c.id DESC
                        LIMIT %s
                        """,
                        (int(before_id), limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT c.*,
                               r.username AS reply_username,
                               r.role AS reply_role,
                               r.content AS reply_content,
                               r.msg_type AS reply_msg_type
                        FROM community_chats c
                        LEFT JOIN community_chats r ON c.reply_to_id = r.id
                        ORDER BY c.id DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()

        messages = []
        for r in rows:
            data = dict(r)
            reply_data = None
            if data.get("reply_to_id") and data.get("reply_username"):
                reply_data = {
                    "id": data["reply_to_id"],
                    "username": data["reply_username"],
                    "role": data["reply_role"],
                    "content": data["reply_content"],
                    "msg_type": data["reply_msg_type"],
                }
            data["reply_to"] = reply_data
            messages.append(data)

        messages.reverse()
        return messages

    def delete_community_chat(self, message_id: int, user_id: int, is_admin: bool = False) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM community_chats WHERE id = %s", (int(message_id),))
                msg = cur.fetchone()
                if not msg:
                    return False
                if not is_admin and msg["user_id"] != user_id:
                    return False
                cur.execute("DELETE FROM community_chats WHERE id = %s", (int(message_id),))
                cur.execute("SELECT pg_notify('community_chat', %s)", (json.dumps({"delete_id": message_id}),))
            conn.commit()
        return True

    def get_unread_chat_count(self, user_id: int) -> int:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) as cnt 
                    FROM community_chats 
                    WHERE id > (
                        SELECT COALESCE(last_read_message_id, 0)
                        FROM user_chat_read_state
                        WHERE user_id = %s
                    ) AND user_id != %s
                    """,
                    (int(user_id), int(user_id)),
                )
                row = cur.fetchone()
                return int(row["cnt"]) if row else 0

    def mark_chat_read(self, user_id: int, last_message_id: Optional[int] = None) -> None:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if last_message_id is None:
                    cur.execute("SELECT MAX(id) as max_id FROM community_chats")
                    max_row = cur.fetchone()
                    last_message_id = max_row["max_id"] if (max_row and max_row.get("max_id")) else 0
                cur.execute(
                    """
                    INSERT INTO user_chat_read_state (user_id, last_read_message_id, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        last_read_message_id = GREATEST(user_chat_read_state.last_read_message_id, EXCLUDED.last_read_message_id),
                        updated_at = EXCLUDED.updated_at
                    """,
                    (int(user_id), int(last_message_id or 0), now),
                )
            conn.commit()
