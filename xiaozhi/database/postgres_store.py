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

PROTECTED_DEVICE_MACS = {"E8:3D:C1:9B:B5:14"}



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

    @staticmethod
    def set_rls_context(cur, user_id: Optional[int] = None, user_role: Optional[str] = "user") -> None:
        """Sets transaction-local configuration for PostgreSQL Row-Level Security."""
        uid_str = str(user_id) if user_id is not None else ""
        role_str = str(user_role) if user_role else "user"
        is_admin_str = "true" if role_str in ("admin", "system") else "false"
        cur.execute("SELECT set_config('app.user_id', %s, true);", (uid_str,))
        cur.execute("SELECT set_config('app.current_user_id', %s, true);", (uid_str,))
        cur.execute("SELECT set_config('app.user_role', %s, true);", (role_str,))
        cur.execute("SELECT set_config('app.is_admin', %s, true);", (is_admin_str,))

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
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS firebase_uid VARCHAR(128);
                        ALTER TABLE users ADD COLUMN IF NOT EXISTS firebase_email VARCHAR(255);
                        CREATE INDEX IF NOT EXISTS idx_users_firebase_uid ON users(firebase_uid);
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

                    # 5. Tokens Table (Maksimal 3 slot per akun dengan isolasi RLS)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS xiaozhi_tokens (
                            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            slot_number INT NOT NULL DEFAULT 1,
                            device_label VARCHAR(60) NOT NULL DEFAULT 'XiaoZhi 1',
                            token_ciphertext TEXT NOT NULL,
                            token_hash VARCHAR(64) UNIQUE NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ,
                            PRIMARY KEY (user_id, slot_number),
                            CONSTRAINT chk_xiaozhi_tokens_slot CHECK (slot_number >= 1 AND slot_number <= 3)
                        );
                        -- Backward-compatible schema evolution untuk database eksisting
                        ALTER TABLE xiaozhi_tokens ADD COLUMN IF NOT EXISTS slot_number INT NOT NULL DEFAULT 1;
                        ALTER TABLE xiaozhi_tokens ADD COLUMN IF NOT EXISTS device_label VARCHAR(60) NOT NULL DEFAULT 'XiaoZhi 1';
                        ALTER TABLE xiaozhi_tokens ADD COLUMN IF NOT EXISTS board_mac VARCHAR(32) NOT NULL DEFAULT '';

                        CREATE INDEX IF NOT EXISTS idx_tokens_hash ON xiaozhi_tokens(token_hash);
                        CREATE INDEX IF NOT EXISTS idx_tokens_user_slot ON xiaozhi_tokens(user_id, slot_number);
                        CREATE INDEX IF NOT EXISTS idx_tokens_board_mac ON xiaozhi_tokens(board_mac);

                        -- Auto-backfill MAC eksisting ke Slot 1 user jika slot 1 belum memiliki board_mac
                        UPDATE xiaozhi_tokens t
                        SET board_mac = UPPER(d.device_id)
                        FROM (
                            SELECT owner_id, device_id
                            FROM registered_devices
                            WHERE device_id IS NOT NULL AND device_id != ''
                            ORDER BY id ASC
                        ) d
                        WHERE t.user_id = d.owner_id
                          AND t.slot_number = 1
                          AND (t.board_mac = '' OR t.board_mac IS NULL);

                        -- Fallback label otomatis menjadi [username] - Slot 1 jika masih default XiaoZhi 1
                        UPDATE xiaozhi_tokens t
                        SET device_label = u.username || ' - Slot 1'
                        FROM users u
                        WHERE t.user_id = u.id
                          AND t.slot_number = 1
                          AND (t.device_label = 'XiaoZhi 1' OR t.device_label = '' OR t.device_label IS NULL);

                        DO $$
                        BEGIN
                            IF EXISTS (
                                SELECT 1 FROM information_schema.table_constraints 
                                WHERE table_name = 'xiaozhi_tokens' AND constraint_type = 'PRIMARY KEY' AND constraint_name = 'xiaozhi_tokens_pkey'
                            ) THEN
                                IF NOT EXISTS (
                                    SELECT 1 FROM information_schema.key_column_usage 
                                    WHERE table_name = 'xiaozhi_tokens' AND constraint_name = 'xiaozhi_tokens_pkey' AND column_name = 'slot_number'
                                ) THEN
                                    ALTER TABLE xiaozhi_tokens DROP CONSTRAINT xiaozhi_tokens_pkey;
                                    ALTER TABLE xiaozhi_tokens ADD CONSTRAINT xiaozhi_tokens_pkey PRIMARY KEY (user_id, slot_number);
                                END IF;
                            END IF;
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.check_constraints 
                                WHERE constraint_name = 'chk_xiaozhi_tokens_slot'
                            ) THEN
                                ALTER TABLE xiaozhi_tokens ADD CONSTRAINT chk_xiaozhi_tokens_slot CHECK (slot_number >= 1 AND slot_number <= 3);
                            END IF;
                        END $$;

                        -- Penegakan Row-Level Security (RLS) pada xiaozhi_tokens
                        CREATE OR REPLACE FUNCTION app_current_user_id()
                        RETURNS BIGINT LANGUAGE sql STABLE AS $$
                            SELECT NULLIF(COALESCE(current_setting('app.user_id', true), current_setting('app.current_user_id', true)), '')::bigint;
                        $$;

                        CREATE OR REPLACE FUNCTION app_is_admin_or_system()
                        RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
                            SELECT COALESCE(
                                current_setting('app.user_role', true) IN ('admin', 'system') 
                                OR current_setting('app.is_admin', true) = 'true', 
                                false
                            );
                        $$;

                        ALTER TABLE xiaozhi_tokens ENABLE ROW LEVEL SECURITY;
                        ALTER TABLE xiaozhi_tokens FORCE ROW LEVEL SECURITY;

                        DROP POLICY IF EXISTS p_xiaozhi_tokens_all ON xiaozhi_tokens;
                        CREATE POLICY p_xiaozhi_tokens_all ON xiaozhi_tokens
                        FOR ALL USING (
                            user_id = app_current_user_id() OR app_is_admin_or_system()
                        ) WITH CHECK (
                            user_id = app_current_user_id() OR app_is_admin_or_system()
                        );
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
                        ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS slot_number INT NOT NULL DEFAULT 1;
                        ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS device_mac VARCHAR(32) NOT NULL DEFAULT '';
                        ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS request_id VARCHAR(64) NOT NULL DEFAULT '';
                        CREATE INDEX IF NOT EXISTS idx_chat_owner ON chat_history(owner_id, id DESC);
                        CREATE INDEX IF NOT EXISTS idx_chat_token ON chat_history(token_hash);
                        CREATE INDEX IF NOT EXISTS idx_chat_tool ON chat_history(tool_name);
                        CREATE INDEX IF NOT EXISTS idx_chat_slot ON chat_history(owner_id, slot_number);
                        CREATE INDEX IF NOT EXISTS idx_chat_mac ON chat_history(device_mac);
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

                    # 9. Registered Devices & Board Binding History
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS registered_devices (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                            device_id VARCHAR(100) NOT NULL,
                            device_name VARCHAR(100),
                            device_type VARCHAR(50),
                            is_protected BOOLEAN NOT NULL DEFAULT FALSE,
                            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                            last_active_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        );
                        CREATE INDEX IF NOT EXISTS idx_device_owner ON registered_devices(owner_id);
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_device_id ON registered_devices(device_id);

                        CREATE TABLE IF NOT EXISTS board_binding_history (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            device_mac VARCHAR(32) NOT NULL,
                            user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
                            username VARCHAR(50),
                            device_name VARCHAR(100),
                            device_type VARCHAR(50),
                            linked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            last_active_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            unlinked_at TIMESTAMPTZ,
                            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
                            notes TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        );
                        ALTER TABLE board_binding_history ADD COLUMN IF NOT EXISTS slot_number INT NOT NULL DEFAULT 1;
                        ALTER TABLE board_binding_history ADD COLUMN IF NOT EXISTS request_id VARCHAR(64) NOT NULL DEFAULT '';
                        CREATE INDEX IF NOT EXISTS idx_board_hist_mac ON board_binding_history(device_mac);
                        CREATE INDEX IF NOT EXISTS idx_board_hist_user ON board_binding_history(user_id);
                        CREATE INDEX IF NOT EXISTS idx_board_hist_status ON board_binding_history(status);
                        CREATE INDEX IF NOT EXISTS idx_board_hist_slot ON board_binding_history(user_id, slot_number);
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

                        -- 15. User Playlists
                        CREATE TABLE IF NOT EXISTS user_playlists (
                            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            track_number INT NOT NULL DEFAULT 1,
                            title VARCHAR(255) NOT NULL,
                            youtube_url TEXT NOT NULL,
                            video_id VARCHAR(64) NOT NULL,
                            artist VARCHAR(255) DEFAULT '',
                            duration VARCHAR(50) DEFAULT '',
                            play_count INT NOT NULL DEFAULT 0,
                            last_played_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        );
                        ALTER TABLE user_playlists ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
                        CREATE INDEX IF NOT EXISTS idx_user_playlists_owner ON user_playlists(owner_id, track_number);
                        CREATE INDEX IF NOT EXISTS idx_user_playlists_video ON user_playlists(video_id);
                        CREATE INDEX IF NOT EXISTS idx_user_playlists_top ON user_playlists(owner_id, play_count DESC);
                        CREATE INDEX IF NOT EXISTS idx_user_playlists_active ON user_playlists(owner_id, is_active);

                        -- 16. Access Plus Settings
                        ALTER TABLE xiaozhi_tokens ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
                        CREATE INDEX IF NOT EXISTS idx_tokens_active ON xiaozhi_tokens(user_id, slot_number, is_active);

                        CREATE TABLE IF NOT EXISTS user_access_plus_settings (
                            user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                            mcp_multislot_allowed BOOLEAN NOT NULL DEFAULT TRUE,
                            playlist_quota_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                            max_playlist_tracks INT NOT NULL DEFAULT 15,
                            notes TEXT DEFAULT '',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        );
                        CREATE INDEX IF NOT EXISTS idx_access_plus_user ON user_access_plus_settings(user_id);
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

    def link_firebase_account(self, user_id: int, firebase_uid: str, firebase_email: Optional[str] = None) -> None:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET firebase_uid = %s, firebase_email = %s, updated_at = %s WHERE id = %s",
                    (str(firebase_uid), str(firebase_email) if firebase_email else None, utc_now(), int(user_id)),
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

    # ── XiaoZhi Tokens (Maksimal 3 Slot per Akun dengan Proteksi RLS & Board MAC Lock) ──

    def set_xiaozhi_token(self, owner_id: int, token: str, slot: int = 1, device_label: str = "", request_id: str = "") -> None:
        slot_num = int(slot or 1)
        if slot_num < 1 or slot_num > 3:
            raise ValueError("Slot token XiaoZhi hanya diizinkan untuk Slot 1, 2, atau 3.")
        token_clean = token.strip()
        t_hash = xiaozhi_token_hash(token_clean)
        t_cipher = encrypt_secret(token_clean)
        now = utc_now()
        
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, owner_id)
                # Dapatkan data eksisting di slot ini
                cur.execute(
                    "SELECT token_hash, board_mac, device_label FROM xiaozhi_tokens WHERE user_id = %s AND slot_number = %s",
                    (int(owner_id), slot_num),
                )
                existing = cur.fetchone()
                
                # Resolusi label default
                cur.execute("SELECT username FROM users WHERE id = %s", (int(owner_id),))
                u_row = cur.fetchone()
                username = u_row["username"] if u_row else f"user_{owner_id}"
                
                default_label = f"{username} - Slot 1" if slot_num == 1 else f"XiaoZhi {slot_num}"
                label_clean = (device_label or "").strip()[:60] or (existing.get("device_label") if existing else "") or default_label

                new_board_mac = ""
                if existing:
                    old_hash = existing.get("token_hash", "")
                    old_mac = existing.get("board_mac", "")
                    if old_hash == t_hash:
                        # Token sama: pertahankan board_mac yang sudah terkunci
                        new_board_mac = old_mac
                    else:
                        # Token baru: board_mac dilepas otomatis karena endpoint baru
                        new_board_mac = ""
                        if old_mac:
                            cur.execute(
                                """
                                UPDATE board_binding_history
                                SET unlinked_at = %s, status = 'UNLINKED',
                                    notes = notes || ' | Token diperbarui [Req: ' || %s || ']'
                                WHERE user_id = %s AND device_mac = %s AND status = 'ACTIVE'
                                """,
                                (now, request_id or "renew", int(owner_id), old_mac),
                            )
                else:
                    new_board_mac = ""

                cur.execute(
                    """
                    INSERT INTO xiaozhi_tokens (user_id, slot_number, device_label, board_mac, token_ciphertext, token_hash, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(user_id, slot_number) DO UPDATE SET
                        device_label = EXCLUDED.device_label,
                        board_mac = EXCLUDED.board_mac,
                        token_ciphertext = EXCLUDED.token_ciphertext,
                        token_hash = EXCLUDED.token_hash,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (int(owner_id), slot_num, label_clean, new_board_mac, t_cipher, t_hash, now, now),
                )
            conn.commit()

    def bind_board_to_slot(self, owner_id: int, slot: int = 1, device_mac: str = "", request_id: str = "") -> Dict[str, Any]:
        """Kunci board hardware MAC ke slot tertentu secara read-only (anti-spoofing)."""
        norm_mac = normalize_mac_address(device_mac)
        if not norm_mac:
            return {"success": False, "detail": "Format MAC address tidak valid."}
        slot_num = int(slot or 1)
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, owner_id)
                cur.execute(
                    "SELECT board_mac, device_label FROM xiaozhi_tokens WHERE user_id = %s AND slot_number = %s",
                    (int(owner_id), slot_num),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "detail": f"Slot {slot_num} belum tersimpan endpoint MCP."}

                current_mac = (row.get("board_mac") or "").strip().upper()
                label = row.get("device_label") or f"Slot {slot_num}"
                if current_mac and current_mac != norm_mac:
                    # Slot sudah terkunci ke MAC lain -> dilarang override manual
                    return {
                        "success": False,
                        "detail": f"Slot {slot_num} ({label}) telah terkunci ke board MAC {current_mac}. Lepaskan board terlebih dahulu.",
                        "current_mac": current_mac,
                        "is_locked": True,
                    }

                # Kunci MAC ke slot
                cur.execute(
                    """
                    UPDATE xiaozhi_tokens
                    SET board_mac = %s, updated_at = %s
                    WHERE user_id = %s AND slot_number = %s
                    """,
                    (norm_mac, now, int(owner_id), slot_num),
                )

                # Dapatkan username untuk audit log
                cur.execute("SELECT username FROM users WHERE id = %s", (int(owner_id),))
                u_row = cur.fetchone()
                username = u_row["username"] if u_row else f"user_{owner_id}"

                # Rekam ke audit history
                cur.execute(
                    """
                    INSERT INTO board_binding_history 
                    (device_mac, user_id, username, device_name, device_type, slot_number, request_id, linked_at, last_active_at, status, notes)
                    VALUES (%s, %s, %s, %s, 'ESP32_SLOT', %s, %s, %s, %s, 'ACTIVE', %s)
                    """,
                    (norm_mac, int(owner_id), username, label, slot_num, request_id or "", now, now, f"Terkunci ke Slot {slot_num} ({label})"),
                )
            conn.commit()
        return {"success": True, "slot": slot_num, "board_mac": norm_mac, "label": label, "is_locked": True, "request_id": request_id}

    def detach_board_from_slot(self, owner_id: int, slot: int = 1, request_id: str = "") -> Dict[str, Any]:
        """Lepaskan board MAC dari slot (memungkinkan board baru ditautkan)."""
        slot_num = int(slot or 1)
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, owner_id)
                cur.execute(
                    "SELECT board_mac, device_label FROM xiaozhi_tokens WHERE user_id = %s AND slot_number = %s",
                    (int(owner_id), slot_num),
                )
                row = cur.fetchone()
                if not row or not row.get("board_mac"):
                    return {"success": False, "detail": f"Tidak ada board yang tertaut pada Slot {slot_num}."}
                old_mac = row["board_mac"]
                cur.execute(
                    """
                    UPDATE xiaozhi_tokens
                    SET board_mac = '', updated_at = %s
                    WHERE user_id = %s AND slot_number = %s
                    """,
                    (now, int(owner_id), slot_num),
                )
                cur.execute(
                    """
                    UPDATE board_binding_history
                    SET unlinked_at = %s, status = 'DETACHED', action = 'detach',
                        notes = notes || ' | Dilepas dari Slot ' || %s || ' [Req: ' || %s || ']'
                    WHERE user_id = %s AND LOWER(device_mac) = LOWER(%s) AND status = 'ACTIVE'
                    """,
                    (now, str(slot_num), request_id or "manual", int(owner_id), old_mac),
                )
            conn.commit()
        return {"success": True, "slot": slot_num, "detached": True, "device_mac": old_mac, "request_id": request_id}

    def update_slot_label(self, owner_id: int, slot: int = 1, device_label: str = "", request_id: str = "") -> bool:
        """Perbarui nama/ruangan slot tanpa mereset token atau melepas MAC yang terkunci."""
        slot_num = int(slot or 1)
        label_clean = (device_label or "").strip()[:60] or f"Slot {slot_num}"
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, owner_id)
                cur.execute(
                    """
                    UPDATE xiaozhi_tokens
                    SET device_label = %s, updated_at = %s
                    WHERE user_id = %s AND slot_number = %s
                    """,
                    (label_clean, now, int(owner_id), slot_num),
                )
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def get_xiaozhi_token(self, owner_id: int, slot: Optional[int] = None) -> Optional[str]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, owner_id)
                if slot is not None:
                    cur.execute(
                        "SELECT token_ciphertext FROM xiaozhi_tokens WHERE user_id = %s AND slot_number = %s",
                        (int(owner_id), int(slot)),
                    )
                else:
                    cur.execute(
                        "SELECT token_ciphertext FROM xiaozhi_tokens WHERE user_id = %s ORDER BY slot_number ASC LIMIT 1",
                        (int(owner_id),),
                    )
                row = cur.fetchone()
                if row and row.get("token_ciphertext"):
                    return decrypt_secret(row["token_ciphertext"])
        return None

    def get_xiaozhi_token_info(self, owner_id: int, slot: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, owner_id)
                if slot is not None:
                    cur.execute(
                        "SELECT slot_number, device_label, board_mac, token_ciphertext, token_hash, COALESCE(is_active, TRUE) as is_active, created_at, updated_at FROM xiaozhi_tokens WHERE user_id = %s AND slot_number = %s",
                        (int(owner_id), int(slot)),
                    )
                else:
                    cur.execute(
                        "SELECT slot_number, device_label, board_mac, token_ciphertext, token_hash, COALESCE(is_active, TRUE) as is_active, created_at, updated_at FROM xiaozhi_tokens WHERE user_id = %s ORDER BY slot_number ASC LIMIT 1",
                        (int(owner_id),),
                    )
                row = cur.fetchone()
                if not row:
                    return None
                token = decrypt_secret(row["token_ciphertext"])
                preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else token
                board_mac = (row.get("board_mac") or "").strip().upper()
                return {
                    "slot_number": row.get("slot_number", 1) or 1,
                    "device_label": row.get("device_label", "XiaoZhi 1") or "XiaoZhi 1",
                    "board_mac": board_mac,
                    "is_locked": bool(board_mac),
                    "is_active": bool(row.get("is_active", True)),
                    "preview": preview,
                    "token_hash": row["token_hash"],
                    "created_at": _format_ts(row["created_at"]) if "created_at" in row else "",
                    "updated_at": _format_ts(row.get("updated_at")) if "updated_at" in row else "",
                }

    def list_user_xiaozhi_tokens(self, owner_id: int) -> List[Dict[str, Any]]:
        """List all active token slots (up to 3) for a specific user with board MAC lock status."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, owner_id)
                cur.execute(
                    """
                    SELECT slot_number, device_label, board_mac, token_ciphertext, token_hash, COALESCE(is_active, TRUE) as is_active, created_at, updated_at
                    FROM xiaozhi_tokens
                    WHERE user_id = %s
                    ORDER BY slot_number ASC
                    """,
                    (int(owner_id),),
                )
                rows = cur.fetchall()
        result = []
        for row in rows:
            token = decrypt_secret(row["token_ciphertext"])
            preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else token
            board_mac = (row.get("board_mac") or "").strip().upper()
            result.append({
                "slot_number": row.get("slot_number", 1) or 1,
                "device_label": row.get("device_label", f"XiaoZhi {row.get('slot_number', 1)}") or f"XiaoZhi {row.get('slot_number', 1)}",
                "board_mac": board_mac,
                "is_locked": bool(board_mac),
                "is_active": bool(row.get("is_active", True)),
                "preview": preview,
                "token_hash": row["token_hash"],
                "created_at": _format_ts(row["created_at"]) if "created_at" in row else "",
                "updated_at": _format_ts(row.get("updated_at")) if "updated_at" in row else "",
            })
        return result

    def delete_xiaozhi_token(self, owner_id: int, slot: Optional[int] = None, request_id: str = "") -> bool:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, owner_id)
                # Dapatkan board_mac yang terhubung sebelum dihapus
                if slot is not None:
                    cur.execute("SELECT board_mac FROM xiaozhi_tokens WHERE user_id = %s AND slot_number = %s", (int(owner_id), int(slot)))
                    row = cur.fetchone()
                    if row and row.get("board_mac"):
                        cur.execute(
                            """
                            UPDATE board_binding_history
                            SET unlinked_at = %s, status = 'UNLINKED',
                                notes = notes || ' | Slot dihapus [Req: ' || %s || ']'
                            WHERE user_id = %s AND device_mac = %s AND status = 'ACTIVE'
                            """,
                            (now, request_id or "delete_slot", int(owner_id), row["board_mac"]),
                        )
                    cur.execute("DELETE FROM xiaozhi_tokens WHERE user_id = %s AND slot_number = %s", (int(owner_id), int(slot)))
                else:
                    cur.execute("SELECT board_mac FROM xiaozhi_tokens WHERE user_id = %s", (int(owner_id),))
                    rows = cur.fetchall()
                    for r in rows:
                        if r.get("board_mac"):
                            cur.execute(
                                """
                                UPDATE board_binding_history
                                SET unlinked_at = %s, status = 'UNLINKED',
                                    notes = notes || ' | Semua slot dihapus [Req: ' || %s || ']'
                                WHERE user_id = %s AND device_mac = %s AND status = 'ACTIVE'
                                """,
                                (now, request_id or "delete_all", int(owner_id), r["board_mac"]),
                            )
                    cur.execute("DELETE FROM xiaozhi_tokens WHERE user_id = %s", (int(owner_id),))
                affected = cur.rowcount > 0
                cur.execute("SELECT COUNT(*) as cnt FROM xiaozhi_tokens WHERE user_id = %s", (int(owner_id),))
                rem_row = cur.fetchone()
                remaining = rem_row["cnt"] if rem_row else 0
            conn.commit()

        if remaining == 0:
            try:
                self.detach_user_devices(int(owner_id), reason="Semua slot MCP Xiaozhi diputus / dihapus")
            except Exception as exc:
                logger.warning("Gagal memisahkan device saat hapus token user %s: %s", owner_id, exc)
        return affected

    def delete_xiaozhi_token_by_hash(self, token: str) -> bool:
        t_hash = xiaozhi_token_hash(token)
        target_user_id = None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, user_id=None, user_role="system")
                cur.execute("SELECT user_id FROM xiaozhi_tokens WHERE token_hash = %s", (t_hash,))
                row = cur.fetchone()
                if row:
                    target_user_id = row["user_id"]
                cur.execute("DELETE FROM xiaozhi_tokens WHERE token_hash = %s", (t_hash,))
                affected = cur.rowcount > 0
            conn.commit()
        if target_user_id:
            try:
                with self._get_conn() as conn:
                    with conn.cursor() as cur:
                        self.set_rls_context(cur, user_id=None, user_role="system")
                        cur.execute("SELECT COUNT(*) as cnt FROM xiaozhi_tokens WHERE user_id = %s", (int(target_user_id),))
                        rem = cur.fetchone()
                        if rem and rem["cnt"] == 0:
                            self.detach_user_devices(target_user_id, reason="MCP Token dihapus by hash")
            except Exception as exc:
                logger.warning("Gagal memisahkan device saat hapus token hash user %s: %s", target_user_id, exc)
        return affected

    def list_xiaozhi_tokens(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, user_id=None, user_role="system")
                cur.execute("SELECT user_id, slot_number, device_label, token_ciphertext, token_hash, COALESCE(is_active, TRUE) as is_active FROM xiaozhi_tokens ORDER BY user_id, slot_number")
                rows = cur.fetchall()
        result = []
        for row in rows:
            token = decrypt_secret(row["token_ciphertext"])
            if token:
                result.append({
                    "user_id": row["user_id"],
                    "slot_number": row.get("slot_number", 1) or 1,
                    "device_label": row.get("device_label", "XiaoZhi 1") or "XiaoZhi 1",
                    "token": token,
                    "token_hash": row["token_hash"],
                    "is_active": bool(row.get("is_active", True)),
                })
        return result

    def find_user_by_mcp_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        t_hash = xiaozhi_token_hash(token)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                self.set_rls_context(cur, user_id=None, user_role="system")
                cur.execute(
                    """
                    SELECT u.*, t.slot_number, t.device_label FROM users u
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
        slot_number: int = 1,
        device_mac: str = "",
        request_id: str = "",
    ) -> Dict[str, Any]:
        """Log chat/tool execution with scoped asynchronous commit for maximum throughput."""
        req_json = json.dumps(request_payload) if request_payload is not None else None
        res_json = json.dumps(response_payload) if response_payload is not None else None
        slot_num = int(slot_number or 1)
        clean_mac = (device_mac or "").strip().upper()
        clean_req_id = (request_id or "").strip()
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # Scoped turbo: logging can tolerate async commit without threatening integrity of core tables
                cur.execute("SET LOCAL synchronous_commit = off;")
                cur.execute(
                    """
                    INSERT INTO chat_history (
                        owner_id, token_hash, source, tool_name, user_message,
                        xiaozhi_answer, request_payload, response_payload, slot_number, device_mac, request_id, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, created_at
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
                        slot_num,
                        clean_mac,
                        clean_req_id,
                        now,
                    ),
                )
                row = cur.fetchone()
            conn.commit()

        inserted_id = row["id"] if row and "id" in row else 0
        ts_val = _format_ts(row["created_at"]) if row and "created_at" in row else now
        record = {
            "id": inserted_id,
            "owner_id": int(owner_id),
            "token_hash": token_hash or "",
            "source": source,
            "tool_name": tool_name,
            "user_message": user_message,
            "xiaozhi_answer": xiaozhi_answer,
            "request_payload": req_json,
            "response_payload": res_json,
            "slot_number": slot_num,
            "device_mac": clean_mac,
            "request_id": clean_req_id,
            "created_at": ts_val,
        }
        try:
            from xiaozhi.services.sse_service import broadcast_chat_history
            broadcast_chat_history(owner_id, record, event_type="new_chat")
        except Exception:
            pass
        return record

    def upsert_chat_transcript(
        self,
        owner_id: int,
        *,
        tool_name: str,
        user_message: str = "",
        xiaozhi_answer: str = "",
        payload: Any = None,
        response_payload: Any = None,
        token_hash: str = "",
        slot_number: int = 1,
        device_mac: str = "",
        request_id: str = "",
    ) -> Dict[str, Any]:
        user_message = str(user_message or "").strip()
        xiaozhi_answer = str(xiaozhi_answer or "").strip()
        res_json = json.dumps(response_payload) if response_payload is not None else None
        req_json = json.dumps(payload) if payload is not None else None
        slot_num = int(slot_number or 1)
        clean_mac = (device_mac or "").strip().upper()
        clean_req_id = (request_id or "").strip()

        # If we have an answer but no user_message, attempt to merge with the latest pending message without answer
        if xiaozhi_answer and not user_message:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    if slot_num == 1:
                        cur.execute(
                            """
                            SELECT id, user_message, request_payload, created_at
                            FROM chat_history
                            WHERE owner_id = %s AND (slot_number = 1 OR slot_number IS NULL OR slot_number = 0) AND (xiaozhi_answer IS NULL OR xiaozhi_answer = '')
                            ORDER BY id DESC LIMIT 1
                            """,
                            (int(owner_id),)
                        )
                    else:
                        cur.execute(
                            """
                            SELECT id, user_message, request_payload, created_at
                            FROM chat_history
                            WHERE owner_id = %s AND slot_number = %s AND (xiaozhi_answer IS NULL OR xiaozhi_answer = '')
                            ORDER BY id DESC LIMIT 1
                            """,
                            (int(owner_id), slot_num)
                        )
                    pending = cur.fetchone()
                    if pending:
                        p_id = pending["id"]
                        cur.execute(
                            """
                            UPDATE chat_history
                            SET xiaozhi_answer = %s,
                                response_payload = COALESCE(%s, response_payload),
                                tool_name = CASE WHEN tool_name LIKE '%%inbound%%' THEN %s ELSE tool_name END,
                                token_hash = COALESCE(%s, token_hash),
                                slot_number = %s,
                                device_mac = CASE WHEN %s != '' THEN %s ELSE device_mac END,
                                request_id = CASE WHEN %s != '' THEN %s ELSE request_id END
                            WHERE id = %s
                            RETURNING id, owner_id, token_hash, source, tool_name, user_message, xiaozhi_answer, request_payload, response_payload, slot_number, device_mac, request_id, created_at
                            """,
                            (xiaozhi_answer, res_json, tool_name, token_hash or None, slot_num, clean_mac, clean_mac, clean_req_id, clean_req_id, p_id)
                        )
                        row = cur.fetchone()
                        conn.commit()
                        if row:
                            updated_rec = dict(row)
                            if "created_at" in updated_rec:
                                updated_rec["created_at"] = _format_ts(updated_rec["created_at"]) or ""
                            try:
                                from xiaozhi.services.sse_service import broadcast_chat_history
                                broadcast_chat_history(owner_id, updated_rec, event_type="update_chat")
                            except Exception:
                                pass
                            return updated_rec

        # Otherwise create a new history record
        return self.add_chat_history(
            owner_id=owner_id,
            source="chat_transcript",
            tool_name=tool_name,
            user_message=user_message,
            xiaozhi_answer=xiaozhi_answer,
            request_payload=payload,
            response_payload=response_payload,
            token_hash=token_hash,
            slot_number=slot_num,
            device_mac=clean_mac,
            request_id=clean_req_id,
        )

    def list_chat_history(
        self,
        owner_id: int,
        query: str = "",
        limit: int = 100,
        token_hash: str = "",
        semantic: bool = False,
        date: str = "",
        offset: int = 0,
        tool_name: str = "",
        slot_number: Optional[int] = None,
        device_mac: str = "",
    ) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "SELECT * FROM chat_history WHERE owner_id = %s"
                params: List[Any] = [int(owner_id)]
                if date:
                    sql += " AND TO_CHAR(created_at, 'YYYY-MM-DD') = %s"
                    params.append(date)
                if slot_number is not None:
                    if int(slot_number) == 1:
                        sql += " AND (slot_number = 1 OR slot_number IS NULL OR slot_number = 0)"
                    else:
                        sql += " AND slot_number = %s"
                        params.append(int(slot_number))
                elif token_hash:
                    sql += " AND (token_hash = %s OR token_hash = '' OR token_hash IS NULL)"
                    params.append(token_hash)
                if tool_name:
                    sql += " AND tool_name = %s"
                    params.append(tool_name)
                if device_mac:
                    sql += " AND UPPER(device_mac) = %s"
                    params.append(device_mac.strip().upper())

                # Semantic search support
                if semantic and query.strip():
                    sql += " ORDER BY id DESC LIMIT %s"
                    params.append(max(int(limit or 100) * 20, 250))
                    cur.execute(sql, params)
                    records = [dict(row) for row in cur.fetchall()]
                    for r in records:
                        if "created_at" in r:
                            r["created_at"] = _format_ts(r["created_at"]) or ""
                    try:
                        from xiaozhi.services.semantic_memory_service import rank_chat_history_semantically
                        return rank_chat_history_semantically(query.strip(), records, top_k=limit)
                    except Exception as e:
                        logger.warning("Fallback semantic search to standard filter: %s", e)

                if query:
                    sql += " AND (user_message ILIKE %s OR xiaozhi_answer ILIKE %s)"
                    params.extend([f"%{query}%", f"%{query}%"])

                sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
                params.extend([int(limit or 100), int(offset or 0)])
                cur.execute(sql, params)
                rows = [dict(row) for row in cur.fetchall()]
                for r in rows:
                    if "created_at" in r:
                        r["created_at"] = _format_ts(r["created_at"]) or ""
                return rows

    def chat_history_dates(self, owner_id: int, token_hash: str = "", slot_number: Optional[int] = None) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT TO_CHAR(created_at, 'YYYY-MM-DD') as date, COUNT(*) as count
                    FROM chat_history
                    WHERE owner_id = %s
                """
                params: List[Any] = [int(owner_id)]
                if slot_number is not None:
                    if int(slot_number) == 1:
                        sql += " AND (slot_number = 1 OR slot_number IS NULL OR slot_number = 0)"
                    else:
                        sql += " AND slot_number = %s"
                        params.append(int(slot_number))
                elif token_hash:
                    sql += " AND (token_hash = %s OR token_hash = '' OR token_hash IS NULL)"
                    params.append(token_hash)
                sql += " GROUP BY date ORDER BY date DESC LIMIT 60"
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]

    def chat_history_stats(self, owner_id: int, token_hash: str = "", date: str = "", slot_number: Optional[int] = None) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = "SELECT COUNT(*) as total FROM chat_history WHERE owner_id = %s"
                params: List[Any] = [int(owner_id)]
                if date:
                    sql += " AND TO_CHAR(created_at, 'YYYY-MM-DD') = %s"
                    params.append(date)
                if slot_number is not None:
                    if int(slot_number) == 1:
                        sql += " AND (slot_number = 1 OR slot_number IS NULL OR slot_number = 0)"
                    else:
                        sql += " AND slot_number = %s"
                        params.append(int(slot_number))
                elif token_hash:
                    sql += " AND (token_hash = %s OR token_hash = '' OR token_hash IS NULL)"
                    params.append(token_hash)
                cur.execute(sql, params)
                row = cur.fetchone()
                total = row["total"] if row and "total" in row else 0
                return {"total": total, "total_calls": total}

    def clear_chat_history(self, owner_id: int) -> int:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_history WHERE owner_id = %s", (int(owner_id),))
                count = cur.rowcount
            conn.commit()
        return count

    def delete_chat_history_item(self, owner_id: int, chat_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_history WHERE id = %s AND owner_id = %s", (int(chat_id), int(owner_id)))
                count = cur.rowcount
            conn.commit()
        return count > 0

    def get_today_users_activity(self, today_date: str = "") -> Dict[int, Dict[str, Any]]:
        """Ambil ringkasan aktivitas user hari ini (stream youtube, mcp tools yang terpanggil) akurat WIB."""
        import re
        from datetime import datetime, timezone, timedelta

        wib = timezone(timedelta(hours=7))
        now_wib = datetime.now(wib)
        if not today_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(today_date).strip()):
            today_date = now_wib.strftime("%Y-%m-%d")
        else:
            today_date = str(today_date).strip()

        activity: Dict[int, Dict[str, Any]] = {}

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Chat history (tools, MCP, voice queries)
                sql_chat = """
                    SELECT owner_id, tool_name, source, user_message, xiaozhi_answer,
                           TO_CHAR(created_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as created_at
                    FROM chat_history
                    WHERE DATE(created_at AT TIME ZONE 'Asia/Jakarta') = %s::date
                    ORDER BY id DESC
                """
                cur.execute(sql_chat, (today_date,))
                chat_rows = [dict(r) for r in cur.fetchall()]

                # 2. Audio queue (YouTube & music streams played/queued today)
                sql_audio = """
                    SELECT owner_id, title, video_id, status,
                           TO_CHAR(created_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as created_at
                    FROM audio_queue
                    WHERE DATE(created_at AT TIME ZONE 'Asia/Jakarta') = %s::date
                    ORDER BY id DESC
                """
                cur.execute(sql_audio, (today_date,))
                audio_rows = [dict(r) for r in cur.fetchall()]

        # Process chat history
        for r in chat_rows:
            uid = int(r["owner_id"]) if r.get("owner_id") is not None else 0
            if not uid:
                continue
            if uid not in activity:
                activity[uid] = {
                    "tools_count": 0,
                    "youtube_count": 0,
                    "tools_list": [],
                    "last_tool": "",
                    "last_song": "",
                    "last_activity_time": "",
                    "last_activity_raw": "",
                    "last_message_preview": "",
                }
            tname = str(r.get("tool_name") or "").strip()
            source = str(r.get("source") or "").strip()
            is_yt = ("youtube" in tname.lower()) or ("youtube" in source.lower()) or ("audio" in tname.lower()) or ("playlist" in tname.lower())
            if is_yt:
                activity[uid]["youtube_count"] += 1
            if tname:
                activity[uid]["tools_count"] += 1
                if tname not in activity[uid]["tools_list"]:
                    activity[uid]["tools_list"].append(tname)
            if not activity[uid]["last_tool"] and tname:
                activity[uid]["last_tool"] = tname
            raw_time = str(r.get("created_at") or "")
            if raw_time and raw_time > activity[uid].get("last_activity_raw", ""):
                activity[uid]["last_activity_raw"] = raw_time
                activity[uid]["last_activity_time"] = raw_time[11:16] if len(raw_time) >= 16 else raw_time
            if not activity[uid]["last_message_preview"]:
                msg = str(r.get("user_message") or r.get("xiaozhi_answer") or "")
                if msg:
                    activity[uid]["last_message_preview"] = msg[:60]

        # Process audio queue (make sure all YouTube songs played are counted & last song is captured!)
        for a in audio_rows:
            uid = int(a["owner_id"]) if a.get("owner_id") is not None else 0
            if not uid:
                continue
            if uid not in activity:
                activity[uid] = {
                    "tools_count": 0,
                    "youtube_count": 0,
                    "tools_list": [],
                    "last_tool": "",
                    "last_song": "",
                    "last_activity_time": "",
                    "last_activity_raw": "",
                    "last_message_preview": "",
                }
            activity[uid]["youtube_count"] += 1
            if not activity[uid]["last_song"] and a.get("title"):
                activity[uid]["last_song"] = str(a["title"])
            raw_time = str(a.get("created_at") or "")
            if raw_time and raw_time > activity[uid].get("last_activity_raw", ""):
                activity[uid]["last_activity_raw"] = raw_time
                activity[uid]["last_activity_time"] = raw_time[11:16] if len(raw_time) >= 16 else raw_time

        return activity

    def get_daily_activity_monitor(
        self,
        target_date: str = "",
        owner_id: Optional[int] = None,
        activity_type: str = "all",
        search_query: str = "",
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Ambil detail aktivitas per hari (alat/tool yang dipanggil, lagu youtube yang diputar) secara akurat (WIB timezone)."""
        import re
        from datetime import datetime, timezone, timedelta

        wib = timezone(timedelta(hours=7))
        now_wib = datetime.now(wib)
        if not target_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(target_date).strip()):
            target_date = now_wib.strftime("%Y-%m-%d")
        else:
            target_date = str(target_date).strip()

        search_clean = str(search_query or "").strip()
        search_pattern = f"%{search_clean}%" if search_clean else ""

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # 1. User map
                cur.execute("SELECT id, username, google_email, role FROM users;")
                users_map = {int(r["id"]): dict(r) for r in cur.fetchall()}

                # 2. Devices map
                cur.execute("SELECT owner_id, device_id, device_name, device_type FROM registered_devices;")
                device_map: Dict[Any, str] = {}
                user_devices_map: Dict[int, List[str]] = {}
                for d in cur.fetchall():
                    uid = int(d["owner_id"]) if d.get("owner_id") is not None else None
                    mac = str(d["device_id"] or "").strip().upper()
                    name = d["device_name"] or d["device_type"] or mac
                    if uid is not None:
                        device_map[(uid, mac)] = name
                        if uid not in user_devices_map:
                            user_devices_map[uid] = []
                        display_dev = f"{name} ({mac})" if mac and mac not in name else name
                        if display_dev not in user_devices_map[uid]:
                            user_devices_map[uid].append(display_dev)
                    if mac:
                        device_map[mac] = name

                # 3. Audio queue query (YouTube / Music streams)
                audio_sql = """
                    SELECT aq.id, aq.owner_id, aq.title, aq.video_id, aq.video_url, aq.stream_url, aq.status,
                           TO_CHAR(aq.created_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as time_wib,
                           EXTRACT(EPOCH FROM (aq.created_at AT TIME ZONE 'Asia/Jakarta')) as epoch
                    FROM audio_queue aq
                    WHERE DATE(aq.created_at AT TIME ZONE 'Asia/Jakarta') = %s::date
                """
                audio_params: List[Any] = [target_date]
                if owner_id:
                    audio_sql += " AND aq.owner_id = %s"
                    audio_params.append(int(owner_id))
                if search_clean:
                    audio_sql += " AND (aq.title ILIKE %s OR aq.video_id ILIKE %s)"
                    audio_params.extend([search_pattern, search_pattern])
                audio_sql += " ORDER BY aq.id DESC LIMIT %s"
                audio_params.append(int(limit))

                cur.execute(audio_sql, tuple(audio_params))
                raw_audio_rows = [dict(r) for r in cur.fetchall()]

                # 4. Chat history tool calls query
                chat_sql = """
                    SELECT ch.id, ch.owner_id, ch.device_mac, ch.tool_name, ch.source,
                           ch.user_message, ch.xiaozhi_answer, ch.request_payload, ch.response_payload,
                           TO_CHAR(ch.created_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as time_wib,
                           EXTRACT(EPOCH FROM (ch.created_at AT TIME ZONE 'Asia/Jakarta')) as epoch
                    FROM chat_history ch
                    WHERE DATE(ch.created_at AT TIME ZONE 'Asia/Jakarta') = %s::date
                      AND ((ch.tool_name IS NOT NULL AND ch.tool_name != '') OR ch.source = 'mcp')
                """
                chat_params: List[Any] = [target_date]
                if owner_id:
                    chat_sql += " AND ch.owner_id = %s"
                    chat_params.append(int(owner_id))
                if search_clean:
                    chat_sql += " AND (ch.user_message ILIKE %s OR ch.tool_name ILIKE %s OR ch.xiaozhi_answer ILIKE %s)"
                    chat_params.extend([search_pattern, search_pattern, search_pattern])
                chat_sql += " ORDER BY ch.id DESC LIMIT %s"
                chat_params.append(int(limit))

                cur.execute(chat_sql, tuple(chat_params))
                raw_chat_rows = [dict(r) for r in cur.fetchall()]

        # Format audio items
        music_items: List[Dict[str, Any]] = []
        for a in raw_audio_rows:
            uid = int(a["owner_id"]) if a.get("owner_id") is not None else 0
            u = users_map.get(uid, {})
            stream_url = str(a.get("stream_url") or "")
            mac_match = re.search(r"[?&]mac=([0-9a-fA-F:_-]+)", stream_url)
            mac_str = mac_match.group(1).upper() if mac_match else ""
            dev_name = device_map.get((uid, mac_str)) or device_map.get(mac_str) or (mac_str if mac_str else "-")

            music_items.append({
                "id": f"music_{a['id']}",
                "raw_id": a["id"],
                "category": "music",
                "type": "music_stream",
                "type_label": "Pemutaran Lagu",
                "badge_color": "emerald",
                "owner_id": uid,
                "username": u.get("username", f"user-{uid}"),
                "email": u.get("google_email", ""),
                "title": a.get("title") or "Lagu Tanpa Judul",
                "video_id": a.get("video_id") or "",
                "video_url": a.get("video_url") or (f"https://www.youtube.com/watch?v={a['video_id']}" if a.get("video_id") else ""),
                "thumbnail_url": f"https://img.youtube.com/vi/{a['video_id']}/mqdefault.jpg" if a.get("video_id") else "",
                "stream_url": stream_url,
                "status": a.get("status") or "pending",
                "device_mac": mac_str,
                "device_name": dev_name,
                "time_wib": a.get("time_wib") or "",
                "time_short": str(a.get("time_wib") or "")[11:19],
                "epoch": float(a.get("epoch") or 0),
            })

        # Format tool items
        tool_items: List[Dict[str, Any]] = []
        for c in raw_chat_rows:
            uid = int(c["owner_id"]) if c.get("owner_id") is not None else 0
            u = users_map.get(uid, {})
            tname = str(c.get("tool_name") or "").strip()
            mac_str = str(c.get("device_mac") or "").strip().upper()
            dev_name = device_map.get((uid, mac_str)) or device_map.get(mac_str) or (mac_str if mac_str else "-")
            is_music_tool = any(m in tname.lower() for m in ["youtube", "playlist", "audio", "music", "playback"])

            tool_items.append({
                "id": f"tool_{c['id']}",
                "raw_id": c["id"],
                "category": "tool",
                "type": "music_tool" if is_music_tool else "tool_call",
                "type_label": f"Tool: {tname}" if tname else "MCP Tool",
                "badge_color": "sky" if is_music_tool else "amber",
                "owner_id": uid,
                "username": u.get("username", f"user-{uid}"),
                "email": u.get("google_email", ""),
                "tool_name": tname,
                "source": c.get("source") or "mcp",
                "user_message": c.get("user_message") or "",
                "xiaozhi_answer": c.get("xiaozhi_answer") or "",
                "device_mac": mac_str,
                "device_name": dev_name,
                "time_wib": c.get("time_wib") or "",
                "time_short": str(c.get("time_wib") or "")[11:19],
                "epoch": float(c.get("epoch") or 0),
            })

        # Unified timeline
        if activity_type == "music":
            timeline = list(music_items)
        elif activity_type == "tools":
            timeline = list(tool_items)
        else:
            timeline = music_items + tool_items
            timeline.sort(key=lambda x: x["epoch"], reverse=True)

        # Aggregate user statistics for the selected day
        user_stats_dict: Dict[int, Dict[str, Any]] = {}
        tool_popularity: Dict[str, int] = {}
        song_popularity: Dict[str, int] = {}

        for m in music_items:
            uid = m["owner_id"]
            if uid not in user_stats_dict:
                user_stats_dict[uid] = {
                    "owner_id": uid,
                    "username": m["username"],
                    "email": m["email"],
                    "music_count": 0,
                    "tools_count": 0,
                    "total_events": 0,
                    "devices": list(user_devices_map.get(uid, [])),
                    "last_active_time": m["time_short"],
                    "last_epoch": m["epoch"],
                    "recent_songs": [],
                    "tools_called": {},
                }
            st = user_stats_dict[uid]
            st["music_count"] += 1
            st["total_events"] += 1
            if m["epoch"] > st["last_epoch"]:
                st["last_epoch"] = m["epoch"]
                st["last_active_time"] = m["time_short"]
            if m["title"] not in st["recent_songs"] and len(st["recent_songs"]) < 5:
                st["recent_songs"].append(m["title"])
            if m["device_name"] and m["device_name"] != "-" and m["device_name"] not in st["devices"]:
                st["devices"].append(m["device_name"])

            song_popularity[m["title"]] = song_popularity.get(m["title"], 0) + 1

        for t in tool_items:
            uid = t["owner_id"]
            if uid not in user_stats_dict:
                user_stats_dict[uid] = {
                    "owner_id": uid,
                    "username": t["username"],
                    "email": t["email"],
                    "music_count": 0,
                    "tools_count": 0,
                    "total_events": 0,
                    "devices": list(user_devices_map.get(uid, [])),
                    "last_active_time": t["time_short"],
                    "last_epoch": t["epoch"],
                    "recent_songs": [],
                    "tools_called": {},
                }
            st = user_stats_dict[uid]
            st["tools_count"] += 1
            st["total_events"] += 1
            if t["epoch"] > st["last_epoch"]:
                st["last_epoch"] = t["epoch"]
                st["last_active_time"] = t["time_short"]
            tname = t["tool_name"] or "mcp"
            st["tools_called"][tname] = st["tools_called"].get(tname, 0) + 1
            if t["device_name"] and t["device_name"] != "-" and t["device_name"] not in st["devices"]:
                st["devices"].append(t["device_name"])

            tool_popularity[tname] = tool_popularity.get(tname, 0) + 1

        user_summaries = list(user_stats_dict.values())
        user_summaries.sort(key=lambda u: u["total_events"], reverse=True)

        for u in user_summaries:
            u["top_tools"] = sorted(u["tools_called"].keys(), key=lambda k: u["tools_called"][k], reverse=True)[:4]

        popular_tools_list = [
            {"tool": k, "count": v}
            for k, v in sorted(tool_popularity.items(), key=lambda item: item[1], reverse=True)[:10]
        ]
        popular_songs_list = [
            {"song": k, "count": v}
            for k, v in sorted(song_popularity.items(), key=lambda item: item[1], reverse=True)[:10]
        ]

        formatted_date = target_date
        try:
            formatted_date = datetime.strptime(target_date, "%Y-%m-%d").strftime("%d %b %Y")
        except Exception:
            pass

        return {
            "target_date": target_date,
            "target_date_formatted": formatted_date,
            "totals": {
                "total_activities": len(timeline),
                "total_music": len(music_items),
                "total_tools": len(tool_items),
                "active_users": len(user_summaries),
            },
            "user_summaries": user_summaries,
            "timeline": timeline,
            "popular_tools": popular_tools_list,
            "popular_songs": popular_songs_list,
            "users_list": [
                {"id": u["id"], "username": u.get("username", f"user-{u['id']}")}
                for u in sorted(users_map.values(), key=lambda x: str(x.get("username", "")))
            ]
        }

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
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if "created_at" in r:
                        r["created_at"] = _format_ts(r["created_at"]) or ""
                    if "updated_at" in r:
                        r["updated_at"] = _format_ts(r["updated_at"]) or ""
                return rows

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
                        "created_at": _format_ts(room.get("created_at")) or "",
                        "updated_at": _format_ts(room.get("updated_at")) or "",
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
                    "created_at": _format_ts(room.get("created_at")) or "",
                    "updated_at": _format_ts(room.get("updated_at")) or "",
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
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if "created_at" in r:
                        r["created_at"] = _format_ts(r["created_at"]) or ""
                return rows

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
                row = cur.fetchone()
                if not row:
                    return None
                d = dict(row)
                if "created_at" in d:
                    d["created_at"] = _format_ts(d["created_at"]) or ""
                return d

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

    def is_device_protected(self, device_id: str) -> bool:
        if not device_id:
            return False
        norm = normalize_mac_address(device_id)
        if norm in PROTECTED_DEVICE_MACS:
            return True
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT is_protected FROM registered_devices WHERE LOWER(device_id) = %s LIMIT 1",
                    (norm.lower(),)
                )
                row = cur.fetchone()
                return bool(row and row.get("is_protected"))

    def register_device(
        self, owner_id: int, device_id: str = "", mac_address: str = "", name: str = "", device_name: str = "", device_type: str = "", notes: str = ""
    ) -> Dict[str, Any]:
        raw_mac = device_id or mac_address
        normalized_id = normalize_mac_address(raw_mac)
        if not normalized_id:
            raise ValueError("Device ID / MAC address diperlukan.")
        dev_name = (name or device_name).strip() if (name or device_name) else f"ESP32 ({normalized_id[-5:]})"
        dev_type = device_type.strip() if device_type else "esp32"
        is_protected = (normalized_id in PROTECTED_DEVICE_MACS)
        now = utc_now()
        hist_notes = notes.strip() if notes else "Tautan aktif (Device Registered)"

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # 1. Ambil username untuk audit history
                cur.execute("SELECT username FROM users WHERE id = %s", (int(owner_id),))
                u_row = cur.fetchone()
                username = u_row["username"] if u_row else f"user_{owner_id}"

                # 2. Cek apakah board sebelumnya tertaut ke user lain
                cur.execute("SELECT id, owner_id, is_protected FROM registered_devices WHERE LOWER(device_id) = %s", (normalized_id.lower(),))
                existing = cur.fetchone()
                if existing:
                    prev_owner = existing.get("owner_id")
                    if prev_owner and int(prev_owner) != int(owner_id):
                        # Tutup riwayat aktif pemilik sebelumnya
                        cur.execute(
                            """
                            UPDATE board_binding_history 
                            SET status = 'DETACHED', unlinked_at = %s, notes = %s
                            WHERE LOWER(device_mac) = %s AND user_id = %s AND status = 'ACTIVE'
                            """,
                            (now, f"Dialihkan ke user {username} (ID: {owner_id})", normalized_id.lower(), int(prev_owner)),
                        )

                # 3. Upsert registered_devices
                cur.execute(
                    """
                    INSERT INTO registered_devices (owner_id, device_id, device_name, device_type, is_protected, status, created_at, last_active_at)
                    VALUES (%s, %s, %s, %s, %s, 'ACTIVE', %s, %s)
                    ON CONFLICT(device_id) DO UPDATE SET
                        owner_id = EXCLUDED.owner_id,
                        device_name = EXCLUDED.device_name,
                        device_type = EXCLUDED.device_type,
                        is_protected = CASE WHEN EXCLUDED.is_protected OR registered_devices.is_protected THEN TRUE ELSE FALSE END,
                        status = 'ACTIVE',
                        last_active_at = EXCLUDED.last_active_at
                    RETURNING id, is_protected
                    """,
                    (int(owner_id), normalized_id, dev_name, dev_type, is_protected, now, now),
                )
                ret_row = cur.fetchone()
                dev_db_id = ret_row["id"]
                final_is_protected = ret_row["is_protected"]

                # 4. Catat riwayat ke board_binding_history
                cur.execute(
                    """
                    SELECT id FROM board_binding_history 
                    WHERE LOWER(device_mac) = %s AND user_id = %s AND status = 'ACTIVE'
                    ORDER BY id DESC LIMIT 1
                    """,
                    (normalized_id.lower(), int(owner_id)),
                )
                hist_row = cur.fetchone()
                if hist_row:
                    cur.execute(
                        """
                        UPDATE board_binding_history 
                        SET last_active_at = %s, device_name = %s, device_type = %s, username = %s,
                            notes = CASE WHEN %s != '' THEN %s ELSE notes END
                        WHERE id = %s
                        """,
                        (now, dev_name, dev_type, username, (notes or "").strip(), (notes or "").strip(), hist_row["id"]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO board_binding_history 
                        (device_mac, user_id, username, device_name, device_type, linked_at, last_active_at, status, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s)
                        """,
                        (normalized_id, int(owner_id), username, dev_name, dev_type, now, now, hist_notes),
                    )
            conn.commit()
        return {"id": dev_db_id, "device_id": normalized_id, "name": dev_name, "is_protected": final_is_protected}

    def detach_device(self, device_id: str, owner_id: Optional[int] = None, reason: str = "Tautan dipisahkan") -> bool:
        normalized_id = normalize_mac_address(device_id)
        if not normalized_id:
            return False
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, owner_id FROM registered_devices WHERE LOWER(device_id) = %s", (normalized_id.lower(),))
                dev = cur.fetchone()
                if not dev:
                    return False
                dev_owner = dev.get("owner_id")
                if owner_id is not None and dev_owner and int(dev_owner) != int(owner_id):
                    return False

                # Update riwayat tautan
                cur.execute(
                    """
                    UPDATE board_binding_history
                    SET status = 'DETACHED', unlinked_at = %s, notes = %s
                    WHERE LOWER(device_mac) = %s AND status = 'ACTIVE'
                    """,
                    (now, reason, normalized_id.lower()),
                )

                # Pisahkan dari user: set owner_id = NULL (JANGAN PERNAH HAPUS ROW-NYA!)
                cur.execute(
                    """
                    UPDATE registered_devices 
                    SET owner_id = NULL, status = 'DETACHED', last_active_at = %s
                    WHERE LOWER(device_id) = %s
                    """,
                    (now, normalized_id.lower()),
                )
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def detach_user_devices(self, user_id: int, reason: str = "MCP diputuskan") -> List[str]:
        now = utc_now()
        detached_macs = []
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT device_id FROM registered_devices WHERE owner_id = %s", (int(user_id),))
                devices = cur.fetchall()
                for d in devices:
                    mac = str(d["device_id"])
                    detached_macs.append(mac)
                    cur.execute(
                        """
                        UPDATE board_binding_history
                        SET status = 'DETACHED', unlinked_at = %s, notes = %s
                        WHERE LOWER(device_mac) = %s AND user_id = %s AND status = 'ACTIVE'
                        """,
                        (now, reason, mac.lower(), int(user_id)),
                    )
                if detached_macs:
                    cur.execute(
                        """
                        UPDATE registered_devices 
                        SET owner_id = NULL, status = 'DETACHED', last_active_at = %s
                        WHERE owner_id = %s
                        """,
                        (now, int(user_id)),
                    )
            conn.commit()
        return detached_macs

    def delete_device(self, owner_id: int, device_id: str) -> bool:
        normalized_id = normalize_mac_address(device_id)
        if not normalized_id:
            return False
        # Board ID Master E8:3D:C1:9B:B5:14 dan board berstatus protected JANGAN PERNAH DIHAPUS!
        if normalized_id in PROTECTED_DEVICE_MACS or self.is_device_protected(normalized_id):
            return self.detach_device(normalized_id, owner_id=owner_id, reason="Perangkat terlindungi (protected) - dipisahkan bukan dihapus")

        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE board_binding_history
                    SET status = 'DETACHED', unlinked_at = %s, notes = %s
                    WHERE LOWER(device_mac) = %s AND status = 'ACTIVE'
                    """,
                    (now, "Device dihapus dari sistem", normalized_id.lower()),
                )
                cur.execute(
                    "DELETE FROM registered_devices WHERE LOWER(device_id) = %s AND owner_id = %s",
                    (normalized_id.lower(), int(owner_id)),
                )
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def record_device_activity(self, device_id: str, owner_id: Optional[int] = None) -> None:
        norm = normalize_mac_address(device_id)
        if not norm:
            return
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE registered_devices SET last_active_at = %s WHERE LOWER(device_id) = %s",
                    (now, norm.lower()),
                )
                if owner_id:
                    cur.execute(
                        """
                        UPDATE board_binding_history 
                        SET last_active_at = %s 
                        WHERE LOWER(device_mac) = %s AND user_id = %s AND status = 'ACTIVE'
                        """,
                        (now, norm.lower(), int(owner_id)),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE board_binding_history 
                        SET last_active_at = %s 
                        WHERE LOWER(device_mac) = %s AND status = 'ACTIVE'
                        """,
                        (now, norm.lower()),
                    )
            conn.commit()

    def get_board_binding_history(
        self,
        device_mac: str = "",
        user_id: Optional[int] = None,
        slot: Optional[int] = None,
        slot_number: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        if isinstance(device_mac, int):
            user_id = device_mac
            device_mac = ""
        target_slot = slot_number if slot_number is not None else slot

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT 
                        bbh.id,
                        bbh.device_mac,
                        bbh.user_id,
                        bbh.username,
                        bbh.device_name,
                        bbh.device_type,
                        bbh.slot_number,
                        bbh.request_id,
                        bbh.linked_at,
                        bbh.last_active_at,
                        bbh.unlinked_at,
                        bbh.status,
                        bbh.notes,
                        bbh.created_at,
                        rd.is_protected,
                        CASE 
                            WHEN bbh.status = 'ACTIVE' THEN 'Sedang Tertaut' 
                            ELSE 'Terputus / Riwayat Lampau' 
                        END as status_label
                    FROM board_binding_history bbh
                    LEFT JOIN registered_devices rd ON LOWER(rd.device_id) = LOWER(bbh.device_mac)
                    WHERE 1=1
                """
                params: List[Any] = []
                if user_id is not None:
                    sql += " AND bbh.user_id = %s"
                    params.append(int(user_id))
                if target_slot is not None:
                    sql += " AND bbh.slot_number = %s"
                    params.append(int(target_slot))
                if device_mac:
                    norm = normalize_mac_address(device_mac) or str(device_mac).strip()
                    clean = norm.replace(":", "").replace("-", "").lower()
                    sql += " AND (LOWER(bbh.device_mac) = %s OR LOWER(REPLACE(REPLACE(bbh.device_mac, ':', ''), '-', '')) = %s)"
                    params.extend([norm.lower(), clean])
                sql += " ORDER BY bbh.id DESC LIMIT %s"
                params.append(int(limit or 50))
                cur.execute(sql, params)
                rows = cur.fetchall()
                res = []
                for r in rows:
                    item = dict(r)
                    item["linked_at_str"] = _format_ts(item.get("linked_at"))
                    item["last_active_str"] = _format_ts(item.get("last_active_at"))
                    item["unlinked_at_str"] = _format_ts(item.get("unlinked_at"))
                    res.append(item)
                return res

    def get_user_board_history(self, user_id: int) -> List[Dict[str, Any]]:
        return self.get_board_binding_history(user_id=int(user_id))

    def list_all_devices(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 
                        rd.*,
                        u.username as owner_username
                    FROM registered_devices rd
                    LEFT JOIN users u ON rd.owner_id = u.id
                    ORDER BY rd.is_protected DESC, rd.created_at DESC
                    """
                )
                rows = cur.fetchall()
                res = []
                for r in rows:
                    item = dict(r)
                    item["created_at_str"] = _format_ts(item.get("created_at"))
                    item["last_active_str"] = _format_ts(item.get("last_active_at"))
                    res.append(item)
                return res

    def find_device_by_id(self, device_id: str) -> Optional[Dict[str, Any]]:
        if not device_id:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM registered_devices WHERE LOWER(device_id) = %s", (device_id.lower().strip(),))
                return cur.fetchone()

    def find_device_by_mac(self, mac_address: str) -> Optional[Dict[str, Any]]:
        if not mac_address:
            return None
        raw = mac_address.strip().lower()
        clean = raw.replace(":", "").replace("-", "")
        parts = [clean[i:i+2] for i in range(0, len(clean), 2)] if len(clean) == 12 else []
        with_colons = ":".join(parts) if parts else raw
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM registered_devices 
                    WHERE LOWER(device_id) = %s 
                       OR LOWER(device_id) = %s 
                       OR LOWER(REPLACE(REPLACE(device_id, ':', ''), '-', '')) = %s
                    ORDER BY id DESC LIMIT 1
                    """,
                    (raw, with_colons, clean),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def find_recent_audio_command_by_video_id(self, video_id: str, minutes: int = 20) -> Optional[Dict[str, Any]]:
        if not video_id:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT owner_id, title, status FROM audio_queue 
                    WHERE video_id = %s 
                      AND status IN ('pending', 'playing', 'played') 
                      AND created_at >= NOW() - (%s || ' minutes')::INTERVAL 
                    ORDER BY id DESC LIMIT 1
                    """,
                    (video_id, str(int(minutes))),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def find_recent_audio_command_by_mac(self, mac_address: str, minutes: int = 20) -> Optional[Dict[str, Any]]:
        if not mac_address:
            return None
        clean = mac_address.replace(":", "").replace("-", "").strip().lower()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT owner_id, title, status, video_id FROM audio_queue 
                    WHERE status IN ('pending', 'playing', 'played') 
                      AND (stream_url ILIKE %s OR stream_url ILIKE %s)
                      AND created_at >= NOW() - (%s || ' minutes')::INTERVAL 
                    ORDER BY id DESC LIMIT 1
                    """,
                    (f"%{mac_address}%", f"%{clean}%", str(int(minutes))),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def find_recent_pending_audio_command(self, minutes: int = 2) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT owner_id, title, status, video_id FROM audio_queue 
                    WHERE status = 'pending' 
                      AND created_at >= NOW() - (%s || ' minutes')::INTERVAL 
                    ORDER BY id DESC LIMIT 1
                    """,
                    (str(int(minutes)),),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def expire_audio_commands(self, minutes: int = 30) -> int:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE audio_queue 
                    SET status = 'played' 
                    WHERE status = 'pending' 
                      AND created_at < NOW() - (%s || ' minutes')::INTERVAL
                    """,
                    (str(int(minutes)),),
                )
                conn.commit()
                return cur.rowcount

    def ping(self) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return True

    def is_device_owned_by(self, device_id: str, owner_id: int) -> bool:
        dev = self.find_device_by_id(device_id)
        if not dev:
            return False
        return bool(dev.get("owner_id") is not None and int(dev.get("owner_id")) == int(owner_id))

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
            from xiaozhi.services.mcp_service import mcp_connection_states, mcp_state_lock
            with mcp_state_lock:
                all_mcp_states = {str(k): dict(state) for k, state in mcp_connection_states.items()}
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

                    # Detail 3 slot XiaoZhi tokens
                    cur.execute(
                        "SELECT slot_number, device_label, board_mac, token_hash FROM xiaozhi_tokens WHERE user_id = %s ORDER BY slot_number ASC",
                        (user_id,),
                    )
                    tok_rows = cur.fetchall()
                    user_slots = []
                    for tr in tok_rows:
                        s_num = int(tr.get("slot_number", 1) or 1)
                        s_mac = (tr.get("board_mac") or "").strip().upper()
                        s_label = tr.get("device_label") or f"Slot {s_num}"
                        s_hash = tr.get("token_hash", "")
                        s_state = all_mcp_states.get(f"{user_id}:{s_num}", {})
                        s_connected = bool(s_state.get("connected", False))
                        user_slots.append({
                            "slot_number": s_num,
                            "device_label": s_label,
                            "board_mac": s_mac,
                            "is_locked": bool(s_mac),
                            "connected": s_connected,
                            "message": s_state.get("message", ""),
                            "token_hash": s_hash,
                        })

                    # Fallback MAC address dari slot jika belum ada di registered_devices
                    if not device_mac and user_slots:
                        for s_item in user_slots:
                            if s_item["board_mac"]:
                                device_mac = s_item["board_mac"]
                                break

                    # Active music session
                    active_session = None
                    try:
                        from xiaozhi.services.playback_tracker import playback_tracker
                        active_session = playback_tracker.get_session_by_user(user_id)
                        if not active_session and device_mac:
                            active_session = playback_tracker.get_session_by_mac(device_mac)
                    except Exception:
                        pass
                    is_playing = active_session is not None
                    current_track = active_session.title if active_session else ""

                    # MCP status
                    mcp_state = all_mcp_states.get(str(user_id), {}) or all_mcp_states.get(f"{user_id}:1", {})
                    has_token = len(user_slots) > 0
                    is_connected = any(s["connected"] for s in user_slots) if user_slots else mcp_state.get("connected", False)

                    user_email = (row.get("google_email") or row.get("firebase_email") or row.get("email") or "").strip().lower()
                    result.append({
                        "id": user_id,
                        "username": row["username"],
                        "email": user_email,
                        "google_email": row.get("google_email") or "",
                        "firebase_email": row.get("firebase_email") or "",
                        "role": row["role"],
                        "created_at": _format_ts(row.get("created_at")) or "",
                        "limits": limits,
                        "usage": usage,
                        "features": self.get_user_features(user_id),
                        "device_mac": device_mac,
                        "device_name": device_name,
                        "slots": user_slots,
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
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    if "created_at" in r:
                        r["created_at"] = _format_ts(r["created_at"]) or ""
                return rows

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
                reminders = [dict(r) for r in cur.fetchall()]
                for r in reminders:
                    if "created_at" in r:
                        r["created_at"] = _format_ts(r["created_at"]) or ""
                if reminders:
                    ids = [r["id"] for r in reminders]
                    cur.execute(
                        "UPDATE reminders SET status = 'processing' WHERE id = ANY(%s)",
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
                if "created_at" in data:
                    data["created_at"] = _format_ts(data["created_at"]) or ""
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
            if "created_at" in data:
                data["created_at"] = _format_ts(data["created_at"]) or ""
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

    # ── User Playlists ─────────────────────────────────────────────────────

    def add_playlist_track(
        self, owner_id: int, title: str, youtube_url: str, video_id: str, artist: str = "", duration: str = ""
    ) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(track_number), 0) + 1 AS next_num FROM user_playlists WHERE owner_id = %s",
                    (int(owner_id),),
                )
                next_num = cur.fetchone()["next_num"]
                now = utc_now()
                cur.execute(
                    """
                    INSERT INTO user_playlists (owner_id, track_number, title, youtube_url, video_id, artist, duration, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        int(owner_id),
                        int(next_num),
                        clean_text(title, max_len=200, field="Judul"),
                        str(youtube_url).strip(),
                        str(video_id).strip(),
                        clean_text(artist or "", max_len=120, field="Artis"),
                        clean_text(duration or "", max_len=30, field="Durasi"),
                        now,
                        now,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)

    def get_user_playlist(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM user_playlists WHERE owner_id = %s ORDER BY track_number ASC, id ASC",
                    (int(owner_id),),
                )
                return [dict(r) for r in cur.fetchall()]

    def get_playlist_track(self, owner_id: int, track_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM user_playlists WHERE id = %s AND owner_id = %s",
                    (int(track_id), int(owner_id)),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    def update_playlist_track(
        self,
        owner_id: int,
        track_id: int,
        title: Optional[str] = None,
        youtube_url: Optional[str] = None,
        video_id: Optional[str] = None,
        artist: Optional[str] = None,
        track_number: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        updates = []
        params = []
        if title is not None:
            updates.append("title = %s")
            params.append(clean_text(title, max_len=200, field="Judul"))
        if youtube_url is not None:
            updates.append("youtube_url = %s")
            params.append(str(youtube_url).strip())
        if video_id is not None:
            updates.append("video_id = %s")
            params.append(str(video_id).strip())
        if artist is not None:
            updates.append("artist = %s")
            params.append(clean_text(artist, max_len=120, field="Artis"))
        if track_number is not None:
            updates.append("track_number = %s")
            params.append(int(track_number))
        if not updates:
            return self.get_playlist_track(owner_id, track_id)

        updates.append("updated_at = %s")
        params.append(utc_now())
        params.extend([int(track_id), int(owner_id)])

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE user_playlists SET {', '.join(updates)} WHERE id = %s AND owner_id = %s RETURNING *",
                    params,
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def delete_playlist_track(self, owner_id: int, track_id: int) -> bool:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM user_playlists WHERE id = %s AND owner_id = %s",
                    (int(track_id), int(owner_id)),
                )
                deleted = cur.rowcount > 0
                if deleted:
                    cur.execute(
                        """
                        WITH reordered AS (
                            SELECT id, ROW_NUMBER() OVER (ORDER BY track_number ASC, id ASC) as new_num
                            FROM user_playlists
                            WHERE owner_id = %s
                        )
                        UPDATE user_playlists u
                        SET track_number = r.new_num
                        FROM reordered r
                        WHERE u.id = r.id
                        """,
                        (int(owner_id),),
                    )
            conn.commit()
            return deleted

    def increment_playlist_play_count(
        self, owner_id: int, track_id: Optional[int] = None, video_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if not track_id and not video_id:
            return None
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if track_id:
                    cur.execute(
                        """
                        UPDATE user_playlists
                        SET play_count = play_count + 1, last_played_at = %s, updated_at = %s
                        WHERE id = %s AND owner_id = %s
                        RETURNING *
                        """,
                        (now, now, int(track_id), int(owner_id)),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE user_playlists
                        SET play_count = play_count + 1, last_played_at = %s, updated_at = %s
                        WHERE video_id = %s AND owner_id = %s
                        RETURNING *
                        """,
                        (now, now, str(video_id).strip(), int(owner_id)),
                    )
                row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def get_top_played_playlist(self, owner_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM user_playlists 
                    WHERE owner_id = %s AND play_count > 0
                    ORDER BY play_count DESC, last_played_at DESC, id ASC 
                    LIMIT %s
                    """,
                    (int(owner_id), int(limit)),
                )
                return [dict(r) for r in cur.fetchall()]

    def find_playlist_track_by_video_id(self, video_id: str, owner_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if not video_id:
            return None
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if owner_id is not None:
                    cur.execute(
                        "SELECT * FROM user_playlists WHERE video_id = %s AND owner_id = %s LIMIT 1",
                        (str(video_id).strip(), int(owner_id)),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM user_playlists WHERE video_id = %s LIMIT 1",
                        (str(video_id).strip(),),
                    )
                row = cur.fetchone()
                return dict(row) if row else None

    def find_playlist_track_by_query(self, owner_id: int, query: str) -> Optional[Dict[str, Any]]:
        raw_q = (query or "").strip()
        if not raw_q:
            return None

        import re
        from xiaozhi.core.utils import detect_media_url_source, extract_youtube_video_id

        # Check track number in query: ONLY if query is literally a number (e.g. "1") or has explicit keyword ("nomor 2", "track 3", "playlist 1")
        track_num = None
        if raw_q.isdigit():
            track_num = int(raw_q)
        else:
            explicit_track_match = re.search(r"\b(?:nomor|no\.?|ke-?|track|urutan|playlist)\s*(\d+)\b", raw_q.lower())
            if explicit_track_match:
                track_num = int(explicit_track_match.group(1))

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if track_num is not None:
                    cur.execute(
                        "SELECT * FROM user_playlists WHERE owner_id = %s AND track_number = %s",
                        (int(owner_id), track_num),
                    )
                    row = cur.fetchone()
                    if row:
                        return dict(row)

                # Check if query is Media URL or Video ID (YouTube or TikTok)
                media_info = detect_media_url_source(raw_q)
                vid = media_info["video_id"] if media_info else extract_youtube_video_id(raw_q)
                if vid:
                    cur.execute(
                        "SELECT * FROM user_playlists WHERE owner_id = %s AND video_id = %s",
                        (int(owner_id), vid),
                    )
                    row = cur.fetchone()
                    if row:
                        return dict(row)

                # Check by title / artist similarity
                clean_kw = re.sub(r"(?i)\b(putar|lagu|musik|dari playlist|di playlist|playlist)\b", "", raw_q).strip()
                target_q = clean_kw if clean_kw else raw_q
                cur.execute(
                    """
                    SELECT * FROM user_playlists 
                    WHERE owner_id = %s AND (title ILIKE %s OR artist ILIKE %s)
                    ORDER BY (LOWER(title) = LOWER(%s)) DESC, (title ILIKE %s) DESC, play_count DESC, id ASC
                    LIMIT 1
                    """,
                    (int(owner_id), f"%{target_q}%", f"%{target_q}%", target_q, f"{target_q}%"),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    # ── Akses Plus Management (Multi-Slot & Playlist Quotas) ───────────────────

    def get_user_access_plus(self, user_id: int) -> Dict[str, Any]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT mcp_multislot_allowed, playlist_quota_enabled, max_playlist_tracks, notes, updated_at FROM user_access_plus_settings WHERE user_id = %s",
                    (int(user_id),),
                )
                row = cur.fetchone()
                if not row:
                    return {
                        "user_id": int(user_id),
                        "mcp_multislot_allowed": True,
                        "playlist_quota_enabled": False,
                        "max_playlist_tracks": 15,
                        "notes": "",
                    }
                return {
                    "user_id": int(user_id),
                    "mcp_multislot_allowed": bool(row["mcp_multislot_allowed"]),
                    "playlist_quota_enabled": bool(row["playlist_quota_enabled"]),
                    "max_playlist_tracks": int(row["max_playlist_tracks"] or 15),
                    "notes": row.get("notes") or "",
                }

    def set_user_access_plus(
        self,
        user_id: int,
        mcp_multislot_allowed: Optional[bool] = None,
        playlist_quota_enabled: Optional[bool] = None,
        max_playlist_tracks: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now()
        current = self.get_user_access_plus(user_id)
        new_multislot = current["mcp_multislot_allowed"] if mcp_multislot_allowed is None else bool(mcp_multislot_allowed)
        new_quota_enabled = current["playlist_quota_enabled"] if playlist_quota_enabled is None else bool(playlist_quota_enabled)
        new_max_tracks = current["max_playlist_tracks"] if max_playlist_tracks is None else int(max_playlist_tracks)
        new_notes = current["notes"] if notes is None else str(notes).strip()

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_access_plus_settings (user_id, mcp_multislot_allowed, playlist_quota_enabled, max_playlist_tracks, notes, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(user_id) DO UPDATE SET
                        mcp_multislot_allowed = EXCLUDED.mcp_multislot_allowed,
                        playlist_quota_enabled = EXCLUDED.playlist_quota_enabled,
                        max_playlist_tracks = EXCLUDED.max_playlist_tracks,
                        notes = EXCLUDED.notes,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (int(user_id), new_multislot, new_quota_enabled, new_max_tracks, new_notes, now, now),
                )
                
                # Apply multislot flag on xiaozhi_tokens
                if not new_multislot:
                    cur.execute(
                        "UPDATE xiaozhi_tokens SET is_active = FALSE, updated_at = %s WHERE user_id = %s AND slot_number IN (2, 3)",
                        (now, int(user_id)),
                    )
                else:
                    cur.execute(
                        "UPDATE xiaozhi_tokens SET is_active = TRUE, updated_at = %s WHERE user_id = %s AND slot_number IN (2, 3)",
                        (now, int(user_id)),
                    )

                # Apply playlist quota on user_playlists
                if new_quota_enabled:
                    cur.execute(
                        """
                        UPDATE user_playlists
                        SET is_active = (track_number <= %s), updated_at = %s
                        WHERE owner_id = %s
                        """,
                        (new_max_tracks, now, int(user_id)),
                    )
                else:
                    cur.execute(
                        "UPDATE user_playlists SET is_active = TRUE, updated_at = %s WHERE owner_id = %s",
                        (now, int(user_id)),
                    )
            conn.commit()

        return {
            "user_id": int(user_id),
            "mcp_multislot_allowed": new_multislot,
            "playlist_quota_enabled": new_quota_enabled,
            "max_playlist_tracks": new_max_tracks,
            "notes": new_notes,
        }

    def set_user_slot_active(self, user_id: int, slot: int, is_active: bool) -> bool:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE xiaozhi_tokens SET is_active = %s, updated_at = %s WHERE user_id = %s AND slot_number = %s",
                    (bool(is_active), now, int(user_id), int(slot)),
                )
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def set_user_playlist_track_active(self, user_id: int, track_id: int, is_active: bool) -> bool:
        now = utc_now()
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE user_playlists SET is_active = %s, updated_at = %s WHERE owner_id = %s AND id = %s",
                    (bool(is_active), now, int(user_id), int(track_id)),
                )
                affected = cur.rowcount > 0
            conn.commit()
        return affected

    def count_user_playlist_tracks(self, owner_id: int, active_only: bool = False) -> int:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                if active_only:
                    cur.execute("SELECT COUNT(*) as cnt FROM user_playlists WHERE owner_id = %s AND COALESCE(is_active, TRUE) = TRUE", (int(owner_id),))
                else:
                    cur.execute("SELECT COUNT(*) as cnt FROM user_playlists WHERE owner_id = %s", (int(owner_id),))
                row = cur.fetchone()
                return int(row["cnt"]) if row else 0

    def list_access_plus_overview(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, username, role, google_email, firebase_email, created_at FROM users WHERE role != 'admin' ORDER BY id ASC")
                users = [dict(r) for r in cur.fetchall()]

                cur.execute("SELECT * FROM user_access_plus_settings")
                settings_map = {int(r["user_id"]): dict(r) for r in cur.fetchall()}

                cur.execute("SELECT user_id, slot_number, device_label, board_mac, token_ciphertext, token_hash, COALESCE(is_active, TRUE) as is_active FROM xiaozhi_tokens")
                token_rows = cur.fetchall()
                tokens_by_user = {}
                for r in token_rows:
                    uid = int(r["user_id"])
                    tokens_by_user.setdefault(uid, []).append(dict(r))

                cur.execute("""
                    SELECT owner_id,
                           COUNT(*) as total_tracks,
                           COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = TRUE) as active_tracks,
                           COUNT(*) FILTER (WHERE COALESCE(is_active, TRUE) = FALSE) as disabled_tracks
                    FROM user_playlists
                    GROUP BY owner_id
                """)
                playlist_map = {int(r["owner_id"]): dict(r) for r in cur.fetchall()}

        result = []
        for u in users:
            uid = u["id"]
            plus_cfg = settings_map.get(uid, {})
            u_tokens = tokens_by_user.get(uid, [])
            pl_stats = playlist_map.get(uid, {"total_tracks": 0, "active_tracks": 0, "disabled_tracks": 0})
            u_email = (u.get("google_email") or u.get("firebase_email") or "").strip().lower()

            slot_map = {}
            for t in u_tokens:
                s_num = int(t.get("slot_number", 1) or 1)
                cipher = t.get("token_ciphertext", "")
                plain = decrypt_secret(cipher) if cipher else ""
                preview = f"{plain[:4]}...{plain[-4:]}" if len(plain) > 8 else plain
                slot_map[s_num] = {
                    "slot_number": s_num,
                    "device_label": t.get("device_label") or f"Slot {s_num}",
                    "board_mac": (t.get("board_mac") or "").upper(),
                    "preview": preview,
                    "token_hash": t.get("token_hash", ""),
                    "is_active": bool(t.get("is_active", True)),
                }

            has_multislot = len(u_tokens) > 1 or 2 in slot_map or 3 in slot_map
            mcp_multislot_allowed = bool(plus_cfg.get("mcp_multislot_allowed", True))
            playlist_quota_enabled = bool(plus_cfg.get("playlist_quota_enabled", False))
            max_playlist_tracks = int(plus_cfg.get("max_playlist_tracks", 15) or 15)

            result.append({
                "user_id": uid,
                "username": u["username"],
                "email": u_email,
                "role": u["role"],
                "created_at": _format_ts(u.get("created_at")),
                "mcp_multislot_allowed": mcp_multislot_allowed,
                "playlist_quota_enabled": playlist_quota_enabled,
                "max_playlist_tracks": max_playlist_tracks,
                "notes": plus_cfg.get("notes", ""),
                "slots": slot_map,
                "total_slots": len(u_tokens),
                "has_multislot": has_multislot,
                "playlist": {
                    "has_playlist": int(pl_stats["total_tracks"]) > 0,
                    "total_tracks": int(pl_stats["total_tracks"]),
                    "active_tracks": int(pl_stats["active_tracks"]),
                    "disabled_tracks": int(pl_stats["disabled_tracks"]),
                },
                "is_restricted": (not mcp_multislot_allowed) or playlist_quota_enabled,
            })
        return result



