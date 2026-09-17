"""
SQLite-based store for Xiaozhi Indonesia.
Optimized for VPS deployment with efficient queries and proper indexing.
"""
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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
from xiaozhi.database.helpers import empty_database

logger = logging.getLogger("xiaozhi.sqlite")


class SQLiteStore:
    """SQLite-backed store with efficient indexing for production use."""

    def __init__(self, db_path: str = None) -> None:
        self.db_path = db_path or os.getenv("SQLITE_DB_PATH", "data/xiaozhi.db")
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_db(self) -> None:
        """Initialize database schema with proper indexes."""
        conn = self._get_conn()
        conn.executescript("""
            -- Users table
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                session_version INTEGER NOT NULL DEFAULT 1,
                ui_theme TEXT NOT NULL DEFAULT 'neo',
                created_at TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

            -- Categories table
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_categories_owner ON categories(owner_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_owner_name ON categories(owner_id, name);

            -- Materials table
            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                keywords TEXT,
                source_type TEXT,
                source_hash TEXT,
                source_key TEXT,
                api_url_ciphertext TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_materials_owner ON materials(owner_id);
            CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(owner_id, category);
            CREATE INDEX IF NOT EXISTS idx_materials_source ON materials(source_hash, source_key);
            CREATE INDEX IF NOT EXISTS idx_materials_created ON materials(owner_id, created_at DESC);

            -- Full-text search for materials
            CREATE VIRTUAL TABLE IF NOT EXISTS materials_fts USING fts5(
                title, content, keywords, category,
                content='materials',
                content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS materials_ai AFTER INSERT ON materials BEGIN
                INSERT INTO materials_fts(rowid, title, content, keywords, category)
                VALUES (new.id, new.title, new.content, new.keywords, new.category);
            END;
            CREATE TRIGGER IF NOT EXISTS materials_ad AFTER DELETE ON materials BEGIN
                INSERT INTO materials_fts(materials_fts, rowid, title, content, keywords, category)
                VALUES('delete', old.id, old.title, old.content, old.keywords, old.category);
            END;
            CREATE TRIGGER IF NOT EXISTS materials_au AFTER UPDATE ON materials BEGIN
                INSERT INTO materials_fts(materials_fts, rowid, title, content, keywords, category)
                VALUES('delete', old.id, old.title, old.content, old.keywords, old.category);
                INSERT INTO materials_fts(rowid, title, content, keywords, category)
                VALUES (new.id, new.title, new.content, new.keywords, new.category);
            END;

            -- XiaoZhi tokens (MCP endpoints)
            CREATE TABLE IF NOT EXISTS xiaozhi_tokens (
                user_id INTEGER PRIMARY KEY,
                token_ciphertext TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tokens_hash ON xiaozhi_tokens(token_hash);

            -- Chat history
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                token_hash TEXT,
                source TEXT NOT NULL DEFAULT 'mcp_tool',
                tool_name TEXT NOT NULL,
                user_message TEXT,
                xiaozhi_answer TEXT,
                request_payload TEXT,
                response_payload TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_chat_owner ON chat_history(owner_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_chat_token ON chat_history(token_hash);
            CREATE INDEX IF NOT EXISTS idx_chat_tool ON chat_history(tool_name);

            -- Relay rooms (physical ESP32 relays)
            CREATE TABLE IF NOT EXISTS relay_rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                nama_tempat TEXT NOT NULL,
                api_slug TEXT NOT NULL,
                api_token TEXT,
                api_client_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_relay_owner ON relay_rooms(owner_id);
            CREATE INDEX IF NOT EXISTS idx_relay_slug ON relay_rooms(api_slug);

            -- Relay devices (individual relays in rooms)
            CREATE TABLE IF NOT EXISTS relay_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                relay_number INTEGER NOT NULL,
                nama_relay TEXT,
                voice_command_on TEXT,
                voice_command_off TEXT,
                status TEXT NOT NULL DEFAULT 'OFF',
                FOREIGN KEY (room_id) REFERENCES relay_rooms(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_relay_device_room ON relay_devices(room_id);

            -- Audio queue
            CREATE TABLE IF NOT EXISTS audio_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                title TEXT,
                stream_url TEXT NOT NULL,
                video_url TEXT,
                duration TEXT,
                video_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_audio_owner ON audio_queue(owner_id, status);

            -- Registered devices
            CREATE TABLE IF NOT EXISTS registered_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                device_name TEXT,
                device_type TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_device_owner ON registered_devices(owner_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_device_id ON registered_devices(device_id);

            -- Feature settings per user
            CREATE TABLE IF NOT EXISTS feature_settings (
                user_id INTEGER PRIMARY KEY,
                virtual_smarthome_enabled INTEGER NOT NULL DEFAULT 1,
                youtube_music_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- User limits
            CREATE TABLE IF NOT EXISTS user_limits (
                user_id INTEGER PRIMARY KEY,
                max_materials INTEGER NOT NULL DEFAULT 3,
                max_words_per_material INTEGER NOT NULL DEFAULT 6000,
                max_live_apis INTEGER NOT NULL DEFAULT 2,
                max_relay_rooms INTEGER NOT NULL DEFAULT 7,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- Reminders
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                sent_at TEXT,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_reminders_owner ON reminders(owner_id, status);
            CREATE INDEX IF NOT EXISTS idx_reminders_scheduled ON reminders(scheduled_at, status);

            -- MCP User Settings (block status and tool toggles)
            CREATE TABLE IF NOT EXISTS mcp_user_settings (
                user_id INTEGER PRIMARY KEY,
                mcp_blocked INTEGER NOT NULL DEFAULT 0,
                blocked_at TEXT,
                blocked_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            -- MCP Tool Toggles per user
            CREATE TABLE IF NOT EXISTS mcp_tool_toggles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, tool_name)
            );
            CREATE INDEX IF NOT EXISTS idx_mcp_toggles_user ON mcp_tool_toggles(user_id);

            -- User Persona (Long-Term Preferences & Memory Profiling)
            CREATE TABLE IF NOT EXISTS user_persona (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT 'informasi_pribadi',
                preference_key TEXT NOT NULL,
                preference_value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(owner_id, preference_key)
            );
            CREATE INDEX IF NOT EXISTS idx_persona_owner ON user_persona(owner_id);
            CREATE INDEX IF NOT EXISTS idx_persona_owner_cat ON user_persona(owner_id, category);
        """)

        # Migration check: Ensure google_id, google_email, and registered_with_google exist in users table
        user_cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "google_id" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN google_id TEXT")
        if "google_email" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN google_email TEXT")
        if "registered_with_google" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN registered_with_google INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL")

        conn.commit()

    # ── User Management ────────────────────────────────────────────────────

    def create_user(self, username: str, password: str) -> Dict[str, Any]:
        username = normalize_username(username)
        password_hash = hash_password(password)
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'user', ?)",
                (username, password_hash, utc_now())
            )
            conn.commit()
            user_id = cursor.lastrowid
            self._seed_default_categories(user_id)
            return {"id": user_id, "username": username, "role": "user", "session_version": 1, "ui_theme": DEFAULT_UI_THEME, "registered_with_google": False}
        except sqlite3.IntegrityError:
            raise ValueError("Username sudah digunakan.")

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        keys = row.keys()
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "session_version": row["session_version"],
            "ui_theme": row["ui_theme"],
            "google_id": row["google_id"] if "google_id" in keys else None,
            "google_email": row["google_email"] if "google_email" in keys else None,
            "registered_with_google": bool(row["registered_with_google"]) if "registered_with_google" in keys else False,
            "created_at": row["created_at"] if "created_at" in keys else None,
        }

    get_user_by_id = get_user

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        username = normalize_username(username)
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        if not google_id:
            return None
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE google_id = ?", (str(google_id),)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if not email:
            return None
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE LOWER(google_email) = LOWER(?)", (email.strip(),)).fetchone()
        return dict(row) if row else None

    def link_google_account(self, user_id: int, google_id: str, google_email: str) -> None:
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id FROM users WHERE google_id = ? AND id != ?",
            (str(google_id), user_id)
        ).fetchone()
        if existing:
            raise ValueError("Akun Google ini sudah tertaut dengan akun lain.")
        conn.execute(
            "UPDATE users SET google_id = ?, google_email = ?, updated_at = ? WHERE id = ?",
            (str(google_id), str(google_email).strip().lower(), utc_now(), user_id)
        )
        conn.commit()

    def unlink_google_account(self, user_id: int) -> None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("Pengguna tidak ditemukan.")
        keys = row.keys()
        if "registered_with_google" in keys and row["registered_with_google"]:
            raise ValueError("Akun ini didaftarkan menggunakan Google sehingga tautan Google bersifat permanen dan tidak dapat dilepas.")
        conn.execute(
            "UPDATE users SET google_id = NULL, google_email = NULL, updated_at = ? WHERE id = ?",
            (utc_now(), user_id)
        )
        conn.commit()

    def create_google_user(self, username: str, google_id: str, google_email: str) -> Dict[str, Any]:
        username = normalize_username(username)
        random_pwd = secrets.token_urlsafe(32)
        password_hash = hash_password(random_pwd)
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, google_id, google_email, registered_with_google, created_at) VALUES (?, ?, 'user', ?, ?, 1, ?)",
                (username, password_hash, str(google_id), str(google_email).strip().lower(), utc_now())
            )
            conn.commit()
            user_id = cursor.lastrowid
            self._seed_default_categories(user_id)
            return {
                "id": user_id,
                "username": username,
                "role": "user",
                "session_version": 1,
                "ui_theme": DEFAULT_UI_THEME,
                "google_id": str(google_id),
                "google_email": str(google_email).strip().lower(),
                "registered_with_google": True,
                "created_at": utc_now(),
            }
        except sqlite3.IntegrityError as e:
            if "google_id" in str(e):
                raise ValueError("Akun Google ini sudah terdaftar.")
            raise ValueError("Username sudah digunakan.")

    def search_users_by_prefix(self, prefix: str, limit: int = 8) -> List[Dict[str, str]]:
        prefix = normalize_username_prefix(prefix)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT username FROM users WHERE username LIKE ? ORDER BY username LIMIT ?",
            (f"{prefix}%", limit)
        ).fetchall()
        return [{"username": row["username"]} for row in rows]

    def has_admin_user(self) -> bool:
        conn = self._get_conn()
        row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        return row is not None

    def ensure_admin_user(self, username: str, password: str) -> Dict[str, Any]:
        username = normalize_username(username)
        conn = self._get_conn()
        existing = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            if existing["role"] != "admin" or not verify_password(password, existing["password_hash"]):
                conn.execute(
                    "UPDATE users SET role='admin', password_hash=?, session_version=session_version+1, updated_at=? WHERE id=?",
                    (hash_password(password), utc_now(), existing["id"])
                )
                conn.commit()
            return self.get_user(existing["id"])
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
            (username, hash_password(password), utc_now())
        )
        conn.commit()
        return {"id": cursor.lastrowid, "username": username, "role": "admin", "session_version": 1, "ui_theme": DEFAULT_UI_THEME}

    def restore_regular_user_account(self, username: str, password: str, **kwargs) -> Dict[str, Any]:
        username = normalize_username(username)
        conn = self._get_conn()
        existing = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            if existing["role"] != "user" or not verify_password(password, existing["password_hash"]):
                conn.execute(
                    "UPDATE users SET role='user', password_hash=?, session_version=session_version+1, updated_at=? WHERE id=?",
                    (hash_password(password), utc_now(), existing["id"])
                )
                conn.commit()
            return self.get_user(existing["id"])
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'user', ?)",
            (username, hash_password(password), utc_now())
        )
        conn.commit()
        user_id = cursor.lastrowid
        self._seed_default_categories(user_id)
        return {"id": user_id, "username": username, "role": "user", "session_version": 1, "ui_theme": DEFAULT_UI_THEME}

    def rotate_user_session(self, user_id: int) -> None:
        conn = self._get_conn()
        conn.execute("UPDATE users SET session_version = session_version + 1, updated_at = ? WHERE id = ?", (utc_now(), user_id))
        conn.commit()

    def set_user_theme(self, user_id: int, theme: str) -> Dict[str, Any]:
        theme = str(theme or "default").strip().lower()
        if theme not in UI_THEMES:
            raise ValueError("Tema tidak valid.")
        conn = self._get_conn()
        conn.execute("UPDATE users SET ui_theme = ?, updated_at = ? WHERE id = ?", (theme, utc_now(), user_id))
        conn.commit()
        return self.get_user(user_id)

    # ── Categories ─────────────────────────────────────────────────────────

    def _seed_default_categories(self, user_id: int) -> None:
        conn = self._get_conn()
        for name in DEFAULT_CATEGORIES:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO categories (owner_id, name, created_at) VALUES (?, ?, ?)",
                    (user_id, name, utc_now())
                )
            except sqlite3.IntegrityError:
                pass
        conn.commit()

    def _ensure_live_api_category(self, user_id: int) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO categories (owner_id, name, created_at) VALUES (?, ?, ?)",
                (user_id, LIVE_API_CATEGORY, utc_now())
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass

    def list_categories(self, owner_id: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        self._ensure_live_api_category(owner_id)
        rows = conn.execute(
            "SELECT id, name FROM categories WHERE owner_id = ? ORDER BY name",
            (owner_id,)
        ).fetchall()
        return [{"id": row["id"], "name": row["name"]} for row in rows]

    def add_category(self, owner_id: int, name: str) -> None:
        name = clean_text(name, max_len=80, min_len=2, field="Nama kategori")
        conn = self._get_conn()
        try:
            conn.execute(
                "INSERT INTO categories (owner_id, name, created_at) VALUES (?, ?, ?)",
                (owner_id, name, utc_now())
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError("Kategori sudah ada.")

    def delete_category(self, owner_id: int, category_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT name FROM categories WHERE id = ? AND owner_id = ?",
            (category_id, owner_id)
        ).fetchone()
        if not row:
            return False
        if is_live_api_category(row["name"]):
            raise ValueError("Kategori data dari api bersifat permanen.")
        cursor = conn.execute(
            "DELETE FROM categories WHERE id = ? AND owner_id = ?",
            (category_id, owner_id)
        )
        conn.commit()
        return cursor.rowcount > 0

    def ensure_categories(self, owner_id: int, names: List[str]) -> List[str]:
        self._ensure_live_api_category(owner_id)
        conn = self._get_conn()
        result = []
        for name in names:
            name = clean_text(name, max_len=80, min_len=2, field="Nama kategori")
            try:
                conn.execute(
                    "INSERT INTO categories (owner_id, name, created_at) VALUES (?, ?, ?)",
                    (owner_id, name, utc_now())
                )
                result.append(name)
            except sqlite3.IntegrityError:
                result.append(name)
        conn.commit()
        return result

    # ── Materials ──────────────────────────────────────────────────────────

    def list_materials(self, owner_id: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM materials WHERE owner_id = ? ORDER BY id DESC",
            (owner_id,)
        ).fetchall()
        return [self._public_material(row) for row in rows]

    def get_material(self, owner_id: int, material_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM materials WHERE id = ? AND owner_id = ?",
            (material_id, owner_id)
        ).fetchone()
        return self._public_material(row) if row else None

    def search_materials(self, owner_id: int, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        if not keyword.strip():
            rows = conn.execute(
                "SELECT * FROM materials WHERE owner_id = ? ORDER BY id DESC LIMIT ?",
                (owner_id, limit)
            ).fetchall()
        else:
            rows = conn.execute("""
                SELECT m.* FROM materials m
                JOIN materials_fts fts ON m.id = fts.rowid
                WHERE m.owner_id = ? AND materials_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (owner_id, keyword, limit)).fetchall()
        return [self._public_material(row) for row in rows]

    def find_material_detail(self, owner_id: int, material_id: int, title: str = "", keyword: str = "") -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        if material_id:
            row = conn.execute(
                "SELECT * FROM materials WHERE id = ? AND owner_id = ?",
                (material_id, owner_id)
            ).fetchone()
            if row:
                return self._public_material(row)
        if title:
            row = conn.execute(
                "SELECT * FROM materials WHERE owner_id = ? AND LOWER(title) LIKE ? LIMIT 1",
                (owner_id, f"%{title.lower()}%")
            ).fetchone()
            if row:
                return self._public_material(row)
        if keyword:
            rows = conn.execute(
                "SELECT * FROM materials WHERE owner_id = ? ORDER BY id DESC LIMIT 1",
                (owner_id,)
            ).fetchall()
            if rows:
                return self._public_material(rows[0])
        return None

    def _is_admin(self, owner_id: int) -> bool:
        """Check if user has admin role."""
        conn = self._get_conn()
        row = conn.execute("SELECT role FROM users WHERE id = ?", (owner_id,)).fetchone()
        return bool(row and row["role"] == "admin")

    def _get_user_limits_dict(self, owner_id: int) -> Dict[str, int]:
        """Fetch limit dictionary for user with default fallbacks."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM user_limits WHERE user_id = ?", (owner_id,)).fetchone()
        limits = dict(USER_LIMIT_DEFAULTS)
        if row:
            limits = {k: dict(row).get(k, v) for k, v in USER_LIMIT_DEFAULTS.items()}
        return limits

    def _enforce_material_limits(self, owner_id: int, content: str = "", api_url: str = "", is_new: bool = True, material_id: int = 0) -> None:
        """Enforce maximum material, word count, and live API limits set by admin."""
        if self._is_admin(owner_id):
            return

        conn = self._get_conn()
        limits = self._get_user_limits_dict(owner_id)

        # 1. Batas total materi
        if is_new:
            max_materials = limits.get("max_materials", 3)
            if max_materials > 0:
                current_materials = conn.execute("SELECT COUNT(*) FROM materials WHERE owner_id = ?", (owner_id,)).fetchone()[0]
                if current_materials >= max_materials:
                    raise ValueError(f"Batas maksimal materi akun Anda telah tercapai ({max_materials} materi). Hapus materi yang tidak digunakan atau hubungi admin untuk menambah kuota.")

        # 2. Batas kata per materi
        max_words = limits.get("max_words_per_material", 6000)
        if max_words > 0 and content:
            words = count_text_words(content)
            if words > max_words:
                raise ValueError(f"Isi materi terlalu panjang. Maksimal {max_words} kata per materi (saat ini {words} kata).")

        # 3. Batas API realtime
        if api_url:
            max_live_apis = limits.get("max_live_apis", 2)
            if max_live_apis > 0:
                if not is_new and material_id:
                    existing = conn.execute("SELECT source_type FROM materials WHERE id = ? AND owner_id = ?", (material_id, owner_id)).fetchone()
                    if existing and existing["source_type"] == LIVE_API_SOURCE_TYPE:
                        return
                current_apis = conn.execute("SELECT COUNT(*) FROM materials WHERE owner_id = ? AND source_type = ?", (owner_id, LIVE_API_SOURCE_TYPE)).fetchone()[0]
                if current_apis >= max_live_apis:
                    raise ValueError(f"Batas endpoint API realtime akun Anda telah tercapai ({max_live_apis} endpoint). Hapus API lama atau hubungi admin.")

    def add_material(self, owner_id: int, title: str, category: str, content: str, keywords: str, api_url: str = "") -> None:
        self._enforce_material_limits(owner_id, content=content, api_url=api_url, is_new=True)
        conn = self._get_conn()
        api_url_ciphertext = encrypt_secret(api_url) if api_url else None
        source_type = LIVE_API_SOURCE_TYPE if api_url else None
        conn.execute(
            """INSERT INTO materials (owner_id, title, category, content, keywords, source_type, api_url_ciphertext, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (owner_id, title, category, content, keywords, source_type, api_url_ciphertext, utc_now(), utc_now())
        )
        conn.commit()

    def update_material(self, owner_id: int, material_id: int, title: str, category: str, content: str, keywords: str, api_url: str = "") -> bool:
        self._enforce_material_limits(owner_id, content=content, api_url=api_url, is_new=False, material_id=material_id)
        conn = self._get_conn()
        api_url_ciphertext = encrypt_secret(api_url) if api_url else None
        source_type = LIVE_API_SOURCE_TYPE if api_url else None
        cursor = conn.execute(
            """UPDATE materials SET title=?, category=?, content=?, keywords=?, source_type=?, api_url_ciphertext=?, updated_at=?
               WHERE id=? AND owner_id=?""",
            (title, category, content, keywords, source_type, api_url_ciphertext, utc_now(), material_id, owner_id)
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete_material(self, owner_id: int, material_id: int) -> bool:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM materials WHERE id = ? AND owner_id = ?", (material_id, owner_id))
        conn.commit()
        return cursor.rowcount > 0

    def list_live_api_materials(self, owner_id: int, keyword: str = "", limit: int = 5) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM materials WHERE owner_id = ? AND source_type = ? ORDER BY id DESC LIMIT ?",
            (owner_id, LIVE_API_SOURCE_TYPE, limit)
        ).fetchall()
        return [self._public_material(row) for row in rows]

    def list_material_database(self, owner_id: int, keyword: str = "", category: str = "", limit: int = 10) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        query = "SELECT * FROM materials WHERE owner_id = ?"
        params = [owner_id]
        if category:
            query += " AND LOWER(category) = ?"
            params.append(category.lower())
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [self._public_material(row) for row in rows]

    def _public_material(self, row) -> Dict[str, Any]:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        api_url = ""
        if row.get("api_url_ciphertext"):
            api_url = decrypt_secret(row["api_url_ciphertext"]) or ""
        return {
            "id": row["id"],
            "title": row.get("title", ""),
            "category": row.get("category", ""),
            "content": row.get("content", ""),
            "keywords": row.get("keywords", ""),
            "owner_id": row.get("owner_id", 0),
            "source_type": row.get("source_type", ""),
            "api_url": api_url,
            "api_label": api_url[:50] + "..." if len(api_url) > 50 else api_url,
        }

    # ── XiaoZhi Tokens (MCP) ──────────────────────────────────────────────

    def set_xiaozhi_token(self, owner_id: int, token: str) -> None:
        encrypted = encrypt_secret(token)
        token_hash = xiaozhi_token_hash(token)
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO xiaozhi_tokens (user_id, token_ciphertext, token_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                token_ciphertext=excluded.token_ciphertext,
                token_hash=excluded.token_hash,
                updated_at=excluded.updated_at
        """, (owner_id, encrypted, token_hash, utc_now(), utc_now()))
        conn.commit()

    def get_xiaozhi_token(self, owner_id: int) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT token_ciphertext FROM xiaozhi_tokens WHERE user_id = ?",
            (owner_id,)
        ).fetchone()
        if not row:
            return None
        return decrypt_secret(row["token_ciphertext"])

    def get_xiaozhi_token_info(self, owner_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM xiaozhi_tokens WHERE user_id = ?",
            (owner_id,)
        ).fetchone()
        if not row:
            return None
        token = decrypt_secret(row["token_ciphertext"])
        return {
            "token_hash": row["token_hash"],
            "preview": token[:30] + "..." if token and len(token) > 30 else token,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete_xiaozhi_token(self, owner_id: int) -> bool:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM xiaozhi_tokens WHERE user_id = ?", (owner_id,))
        conn.commit()
        return cursor.rowcount > 0

    def delete_xiaozhi_token_by_hash(self, token: str) -> bool:
        token_hash = xiaozhi_token_hash(token)
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM xiaozhi_tokens WHERE token_hash = ?", (token_hash,))
        conn.commit()
        return cursor.rowcount > 0

    def list_xiaozhi_tokens(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM xiaozhi_tokens").fetchall()
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
        token_hash = xiaozhi_token_hash(token)
        conn = self._get_conn()
        row = conn.execute("""
            SELECT t.user_id, t.created_at, u.username, u.role
            FROM xiaozhi_tokens t
            JOIN users u ON t.user_id = u.id
            WHERE t.token_hash = ?
        """, (token_hash,)).fetchone()
        if not row:
            return None
        return {
            "user_id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "created_at": row["created_at"],
        }

    # ── Chat History ───────────────────────────────────────────────────────

    def add_chat_history(self, owner_id: int, *, source: str, tool_name: str, user_message: str = "", xiaozhi_answer: str = "", request_payload=None, response_payload=None, token_hash: str = "") -> None:
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO chat_history (owner_id, token_hash, source, tool_name, user_message, xiaozhi_answer, request_payload, response_payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (owner_id, token_hash, source, tool_name, user_message, xiaozhi_answer,
             json.dumps(request_payload, default=str) if request_payload else None,
             json.dumps(response_payload, default=str) if response_payload else None,
             utc_now())
        )
        conn.commit()

    def upsert_chat_transcript(self, owner_id: int, *, tool_name: str, user_message: str = "", xiaozhi_answer: str = "", payload=None, token_hash: str = "") -> None:
        self.add_chat_history(
            owner_id, source="chat_transcript", tool_name=tool_name,
            user_message=user_message, xiaozhi_answer=xiaozhi_answer,
            request_payload=payload, token_hash=token_hash
        )

    def list_chat_history(
        self,
        owner_id: int,
        query: str = "",
        limit: int = 100,
        token_hash: str = "",
        semantic: bool = False,
        date: str = "",
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        sql = "SELECT * FROM chat_history WHERE owner_id = ?"
        params: List[Any] = [owner_id]

        if date:
            sql += " AND DATE(created_at) = ?"
            params.append(date)

        if token_hash:
            sql += " AND (token_hash = ? OR token_hash = '')"
            params.append(token_hash)

        # If semantic search is requested and query is provided, fetch a broader window and rank semantically
        if semantic and query.strip():
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(max(limit * 20, 250))
            rows = conn.execute(sql, params).fetchall()
            records = [dict(row) for row in rows]
            try:
                from xiaozhi.services.semantic_memory_service import rank_chat_history_semantically
                return rank_chat_history_semantically(query.strip(), records, top_k=limit)
            except Exception as e:
                logger.warning("Fallback semantic search to standard filter: %s", e)

        if query:
            sql += " AND (user_message LIKE ? OR xiaozhi_answer LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def chat_history_dates(self, owner_id: int, token_hash: str = "") -> List[Dict[str, Any]]:
        """Ambil daftar tanggal yang punya chat, dengan jumlah per hari."""
        conn = self._get_conn()
        sql = "SELECT DATE(created_at) as date, COUNT(*) as count FROM chat_history WHERE owner_id = ?"
        params: List[Any] = [owner_id]
        if token_hash:
            sql += " AND (token_hash = ? OR token_hash = '')"
            params.append(token_hash)
        sql += " GROUP BY DATE(created_at) ORDER BY DATE(created_at) DESC LIMIT 60"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def chat_history_stats(self, owner_id: int, token_hash: str = "", date: str = "") -> Dict[str, Any]:
        conn = self._get_conn()
        sql = "SELECT COUNT(*) as total FROM chat_history WHERE owner_id = ?"
        params: List[Any] = [owner_id]
        if date:
            sql += " AND DATE(created_at) = ?"
            params.append(date)
        if token_hash:
            sql += " AND (token_hash = ? OR token_hash = '')"
            params.append(token_hash)
        row = conn.execute(sql, params).fetchone()
        return {"total": row["total"] if row else 0}

    def clear_chat_history(self, owner_id: int) -> int:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM chat_history WHERE owner_id = ?", (owner_id,))
        conn.commit()
        return cursor.rowcount

    # ── User Persona & Preferences ─────────────────────────────────────────

    def save_user_preference(
        self,
        owner_id: int,
        category: str,
        preference_key: str,
        preference_value: str,
        confidence: float = 1.0
    ) -> Dict[str, Any]:
        conn = self._get_conn()
        now = utc_now()
        cat_clean = str(category or "informasi_pribadi").strip().lower()
        key_clean = str(preference_key or "").strip()
        val_clean = str(preference_value or "").strip()
        conf_val = max(0.1, min(float(confidence or 1.0), 1.0))

        conn.execute("""
            INSERT INTO user_persona (owner_id, category, preference_key, preference_value, confidence, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id, preference_key) DO UPDATE SET
                category=excluded.category,
                preference_value=excluded.preference_value,
                confidence=excluded.confidence,
                updated_at=excluded.updated_at
        """, (owner_id, cat_clean, key_clean, val_clean, conf_val, now, now))
        conn.commit()
        return {
            "success": True,
            "owner_id": owner_id,
            "category": cat_clean,
            "preference_key": key_clean,
            "preference_value": val_clean,
            "confidence": conf_val,
            "updated_at": now
        }

    def get_user_persona(self, owner_id: int, category: str = "") -> List[Dict[str, Any]]:
        conn = self._get_conn()
        sql = "SELECT * FROM user_persona WHERE owner_id = ?"
        params: List[Any] = [owner_id]
        if category:
            sql += " AND category = ?"
            params.append(category.strip().lower())
        sql += " ORDER BY category ASC, updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def delete_user_preference(self, owner_id: int, preference_key: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM user_persona WHERE owner_id = ? AND preference_key = ?",
            (owner_id, preference_key.strip())
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_user_persona_analysis(self, owner_id: int) -> Dict[str, Any]:
        """
        Runs RAG & Vector Semantic profiling on the user's chat history & stored personas.
        """
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
                    "description": "Sedang mempelajari kepribadian Anda melalui percakapan."
                },
                "hobbies": [],
                "challenges": [],
                "activities": [],
                "preferences": []
            }

    # ── Relay Rooms ────────────────────────────────────────────────────────

    def list_relay_rooms(self, owner_id: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rooms = conn.execute(
            "SELECT * FROM relay_rooms WHERE owner_id = ? ORDER BY id",
            (owner_id,)
        ).fetchall()
        result = []
        for room in rooms:
            devices = conn.execute(
                "SELECT * FROM relay_devices WHERE room_id = ? ORDER BY relay_number",
                (room["id"],)
            ).fetchall()
            result.append({
                "id": room["id"],
                "nama_tempat": room["nama_tempat"],
                "api_slug": room["api_slug"],
                "api_client_id": room["api_client_id"],
                "relays": [dict(d) for d in devices],
            })
        return result

    def add_relay_room(self, owner_id: int, nama_tempat: str, api_slug: str, api_token: str, api_client_id: str, relays: List[Dict]) -> int:
        if not self._is_admin(owner_id):
            conn = self._get_conn()
            limits = self._get_user_limits_dict(owner_id)
            max_relay_rooms = limits.get("max_relay_rooms", 7)
            if max_relay_rooms > 0:
                current_rooms = conn.execute("SELECT COUNT(*) FROM relay_rooms WHERE owner_id = ?", (owner_id,)).fetchone()[0]
                if current_rooms >= max_relay_rooms:
                    raise ValueError(f"Batas perangkat Relay Nyata akun Anda telah tercapai ({max_relay_rooms} ruangan). Hubungi admin jika membutuhkan kapasitas tambahan.")

        conn = self._get_conn()
        cursor = conn.execute(
            "INSERT INTO relay_rooms (owner_id, nama_tempat, api_slug, api_token, api_client_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, nama_tempat, api_slug, api_token, api_client_id, utc_now())
        )
        room_id = cursor.lastrowid
        for relay in relays:
            conn.execute(
                "INSERT INTO relay_devices (room_id, relay_number, nama_relay, voice_command_on, voice_command_off, status) VALUES (?, ?, ?, ?, ?, ?)",
                (room_id, relay.get("relay_number", 1), relay.get("nama_relay", ""), relay.get("voice_command_on", ""), relay.get("voice_command_off", ""), relay.get("status", "OFF"))
            )
        conn.commit()
        return room_id

    def get_relay_room(self, owner_id: int, room_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        room = conn.execute(
            "SELECT * FROM relay_rooms WHERE id = ? AND owner_id = ?",
            (room_id, owner_id)
        ).fetchone()
        if not room:
            return None
        devices = conn.execute(
            "SELECT * FROM relay_devices WHERE room_id = ? ORDER BY relay_number",
            (room_id,)
        ).fetchall()
        return {
            "id": room["id"],
            "nama_tempat": room["nama_tempat"],
            "api_slug": room["api_slug"],
            "relays": [dict(d) for d in devices],
        }

    def update_relay_room(self, owner_id: int, room_id: int, nama_tempat: str, relays: List[Dict]) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE relay_rooms SET nama_tempat=?, updated_at=? WHERE id=? AND owner_id=?",
            (nama_tempat, utc_now(), room_id, owner_id)
        )
        for relay in relays:
            conn.execute("""
                INSERT INTO relay_devices (room_id, relay_number, nama_relay, voice_command_on, voice_command_off, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(room_id, relay_number) DO UPDATE SET
                    nama_relay=excluded.nama_relay,
                    voice_command_on=excluded.voice_command_on,
                    voice_command_off=excluded.voice_command_off
            """, (room_id, relay.get("relay_number", 1), relay.get("nama_relay", ""), relay.get("voice_command_on", ""), relay.get("voice_command_off", ""), relay.get("status", "OFF")))
        conn.commit()

    def delete_relay_room(self, owner_id: int, room_id: int) -> bool:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM relay_rooms WHERE id = ? AND owner_id = ?", (room_id, owner_id))
        conn.commit()
        return cursor.rowcount > 0

    def update_relay_status(self, owner_id: int, room_id: int, relay_number: int, status: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE relay_devices SET status=? WHERE room_id=? AND relay_number=?",
            (status, room_id, relay_number)
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

    def queue_audio_command(self, owner_id: int, title: str = "", stream_url: str = "", video_url: str = "", duration: str = "", video_id: str = "") -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.execute(
            "INSERT INTO audio_queue (owner_id, title, stream_url, video_url, duration, video_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (owner_id, title, stream_url, video_url, duration, video_id, utc_now())
        )
        conn.commit()
        return {"id": cursor.lastrowid, "title": title, "stream_url": stream_url}

    def get_audio_commands(self, owner_id: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM audio_queue WHERE owner_id = ? AND status = 'pending' ORDER BY id",
            (owner_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_pending_audio_commands(self, owner_id: int) -> List[Dict[str, Any]]:
        return self.get_audio_commands(owner_id)

    def ack_audio_command(self, owner_id: int, command_id: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE audio_queue SET status='played' WHERE id=? AND owner_id=?",
            (command_id, owner_id)
        )
        conn.commit()

    def get_current_audio(self, owner_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM audio_queue WHERE owner_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (owner_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_now_playing(self, owner_id: int) -> Optional[Dict[str, Any]]:
        return self.get_current_audio(owner_id)

    # ── Devices ────────────────────────────────────────────────────────────

    def get_user_mac_address(self, user_id: int) -> Optional[str]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT device_id FROM registered_devices WHERE owner_id = ? ORDER BY id DESC LIMIT 1",
            (int(user_id),)
        ).fetchone()
        if row and row["device_id"]:
            return str(row["device_id"]).upper()
        return None

    def list_registered_devices(self, owner_id: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM registered_devices WHERE owner_id = ? ORDER BY created_at DESC",
            (owner_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def list_devices(self, owner_id: int) -> List[Dict[str, Any]]:
        return self.list_registered_devices(owner_id)

    def register_device(self, owner_id: int, device_id: str = "", name: str = "", device_type: str = "") -> Dict[str, Any]:
        conn = self._get_conn()
        normalized_id = normalize_mac_address(device_id)
        if not normalized_id:
            raise ValueError("Device ID / MAC address diperlukan.")
        device_name = name.strip() if name else f"ESP32 ({normalized_id[-5:]})"
        device_type = device_type.strip() if device_type else "esp32"
        existing = conn.execute(
            "SELECT id, owner_id, device_name FROM registered_devices WHERE LOWER(device_id) = ?",
            (normalized_id.lower(),)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE registered_devices SET owner_id = ?, device_id = ?, device_name = ?, device_type = ? WHERE id = ?",
                (int(owner_id), normalized_id, device_name, device_type, existing["id"])
            )
            conn.commit()
            return {"id": existing["id"], "device_id": normalized_id, "name": device_name, "updated": True}
        cursor = conn.execute(
            "INSERT INTO registered_devices (owner_id, device_id, device_name, device_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(owner_id), normalized_id, device_name, device_type, utc_now())
        )
        conn.commit()
        return {"id": cursor.lastrowid, "device_id": normalized_id, "name": device_name, "created": True}

    def delete_device(self, owner_id: int, device_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM registered_devices WHERE device_id = ? AND owner_id = ?", (device_id, owner_id))
        conn.commit()
        return cursor.rowcount > 0

    def find_device_by_id(self, device_id: str) -> Optional[Dict[str, Any]]:
        device_id = str(device_id or "").strip()
        if not device_id:
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM registered_devices WHERE LOWER(device_id) = ?",
            (device_id.lower(),)
        ).fetchone()
        return dict(row) if row else None

    def is_device_owned_by(self, device_id: str, owner_id: int) -> bool:
        dev = self.find_device_by_id(device_id)
        if not dev:
            return False
        return int(dev.get("owner_id", 0)) == int(owner_id)

    # ── Feature Settings ───────────────────────────────────────────────────

    def get_feature_settings(self, owner_id: int) -> Dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM feature_settings WHERE user_id = ?", (owner_id,)).fetchone()
        if not row:
            return {"virtual_smarthome_enabled": True, "youtube_music_enabled": True}
        return {
            "virtual_smarthome_enabled": bool(row["virtual_smarthome_enabled"]),
            "youtube_music_enabled": bool(row["youtube_music_enabled"]),
        }

    def set_feature_setting(self, owner_id: int, key: str, value: bool) -> None:
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO feature_settings (user_id, {key}, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET {key}=excluded.{key}, updated_at=excluded.updated_at
        """.format(key=key), (owner_id, int(value), utc_now()))
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

    # ── User Limits ────────────────────────────────────────────────────────

    def user_quota(self, owner_id: int) -> Dict[str, Any]:
        conn = self._get_conn()
        is_admin = self._is_admin(owner_id)
        usage = {
            "materials": conn.execute("SELECT COUNT(*) FROM materials WHERE owner_id = ?", (owner_id,)).fetchone()[0],
            "live_apis": conn.execute("SELECT COUNT(*) FROM materials WHERE owner_id = ? AND source_type = ?", (owner_id, LIVE_API_SOURCE_TYPE)).fetchone()[0],
            "relay_rooms": conn.execute("SELECT COUNT(*) FROM relay_rooms WHERE owner_id = ?", (owner_id,)).fetchone()[0],
        }
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
        conn = self._get_conn()
        limits = {k: parse_limit_value(v, field=k) for k, v in kwargs.items()}
        conn.execute("""
            INSERT INTO user_limits (user_id, max_materials, max_words_per_material, max_live_apis, max_relay_rooms, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                max_materials=excluded.max_materials,
                max_words_per_material=excluded.max_words_per_material,
                max_live_apis=excluded.max_live_apis,
                max_relay_rooms=excluded.max_relay_rooms,
                updated_at=excluded.updated_at
        """, (target_user_id, limits.get("max_materials", 3), limits.get("max_words_per_material", 6000), limits.get("max_live_apis", 2), limits.get("max_relay_rooms", 7), utc_now(), utc_now()))
        conn.commit()
        return limits

    def list_admin_manageable_users(self, admin_username: str = "") -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM users WHERE role != 'admin' ORDER BY id DESC").fetchall()
        result = []

        # Get all MCP connection states (import here to avoid circular import)
        try:
            from xiaozhi.services.mcp_service import mcp_connection_states, mcp_state_lock, mcp_bridge_tasks
            with mcp_state_lock:
                all_mcp_states = {int(uid): dict(state) for uid, state in mcp_connection_states.items()}
        except ImportError:
            all_mcp_states = {}

        for row in rows:
            user_id = row["id"]
            usage = {
                "materials": conn.execute("SELECT COUNT(*) FROM materials WHERE owner_id = ?", (user_id,)).fetchone()[0],
                "live_apis": conn.execute("SELECT COUNT(*) FROM materials WHERE owner_id = ? AND source_type = ?", (user_id, LIVE_API_SOURCE_TYPE)).fetchone()[0],
                "relay_rooms": conn.execute("SELECT COUNT(*) FROM relay_rooms WHERE owner_id = ?", (user_id,)).fetchone()[0],
            }
            limits_row = conn.execute("SELECT * FROM user_limits WHERE user_id = ?", (user_id,)).fetchone()
            limits = dict(USER_LIMIT_DEFAULTS)
            if limits_row:
                limits_dict = dict(limits_row)
                limits = {k: limits_dict.get(k, v) for k, v in USER_LIMIT_DEFAULTS.items()}

            # Get registered device / MAC address
            dev_row = conn.execute(
                "SELECT device_id, device_name FROM registered_devices WHERE owner_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,)
            ).fetchone()
            device_mac = str(dev_row["device_id"]).upper() if dev_row and dev_row["device_id"] else ""
            device_name = dev_row["device_name"] if dev_row and dev_row["device_name"] else ""

            # Check if user is currently playing music in playback_tracker
            active_session = None
            try:
                from xiaozhi.services.playback_tracker import playback_tracker
                active_session = playback_tracker.get_session_by_user(user_id)
            except Exception:
                pass

            is_playing = active_session is not None
            current_track = active_session.title if active_session else ""
            if not device_mac and active_session and active_session.device_mac:
                device_mac = str(active_session.device_mac).upper()
                device_name = f"ESP32 ({device_mac[-5:]})"
                try:
                    self.register_device(user_id, device_id=device_mac, name=device_name, device_type="esp32")
                except Exception:
                    pass

            # Check MCP status from real-time connection states
            mcp_state = all_mcp_states.get(user_id, {})
            has_token = conn.execute("SELECT COUNT(*) FROM xiaozhi_tokens WHERE user_id = ?", (user_id,)).fetchone()[0] > 0
            is_connected = mcp_state.get("connected", False)
            bridge_task = mcp_bridge_tasks.get(user_id) if 'mcp_bridge_tasks' in dir() else None
            bridge_running = bridge_task is not None and not bridge_task.done() if bridge_task else False

            result.append({
                "id": user_id,
                "username": row["username"],
                "role": row["role"],
                "created_at": row["created_at"],
                "limits": limits,
                "usage": usage,
                "features": self.get_user_features(user_id),
                "device_mac": device_mac,
                "device_name": device_name,
                "is_playing": is_playing,
                "current_track": current_track,
                "mcp_status": {
                    "has_token": has_token,
                    "connected": is_connected or bridge_running,
                    "message": mcp_state.get("message", ""),
                    "updated_at": mcp_state.get("updated_at", ""),
                },
            })
        # Prioritas Urutan Tampilan Admin:
        # 1. Paling atas: User yang sedang memutar YouTube Music
        # 2. Kedua: User yang sudah terhubung ke MCP
        # 3. Ketiga: User lainnya (diurutkan berdasarkan user baru / id desc)
        result.sort(key=lambda u: (
            0 if u.get("is_playing") else 1,
            0 if u.get("mcp_status", {}).get("connected") else 1,
            -int(u.get("id", 0))
        ))
        return result

    def get_pending_relay_commands(self, owner_id: int, room_id: int) -> List[Dict[str, Any]]:
        return []

    # ── Reminders ─────────────────────────────────────────────────────────

    def add_reminder(self, owner_id: int, reminder_id: str, message: str, scheduled_at: str) -> Dict[str, Any]:
        conn = self._get_conn()
        now = utc_now()
        conn.execute(
            "INSERT INTO reminders (id, owner_id, message, scheduled_at, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (reminder_id, owner_id, message, scheduled_at, now)
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
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM reminders WHERE owner_id = ? ORDER BY scheduled_at",
            (owner_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_reminder(self, owner_id: int, reminder_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND owner_id = ?",
            (reminder_id, owner_id)
        )
        conn.commit()
        return cursor.rowcount > 0

    def get_due_reminders(self) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        now = utc_now()
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND scheduled_at <= ?",
            (now,)
        ).fetchall()
        reminders = [dict(row) for row in rows]
        if reminders:
            ids = [r["id"] for r in reminders]
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE reminders SET status = 'triggered' WHERE id IN ({placeholders})",
                ids
            )
            conn.commit()
        return reminders

    def mark_reminder_sent(self, reminder_id: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE reminders SET status = 'sent', sent_at = ? WHERE id = ?",
            (utc_now(), reminder_id)
        )
        conn.commit()

    # ── MCP User Settings ─────────────────────────────────────────────────

    def get_mcp_settings(self, user_id: int) -> Dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM mcp_user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        if not row:
            return {"user_id": user_id, "mcp_blocked": False, "blocked_at": None, "blocked_reason": None}
        return {
            "user_id": row["user_id"],
            "mcp_blocked": bool(row["mcp_blocked"]),
            "blocked_at": row["blocked_at"],
            "blocked_reason": row["blocked_reason"],
        }

    def set_mcp_blocked(self, user_id: int, blocked: bool, reason: str = "") -> None:
        conn = self._get_conn()
        now = utc_now()
        conn.execute("""
            INSERT INTO mcp_user_settings (user_id, mcp_blocked, blocked_at, blocked_reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                mcp_blocked=excluded.mcp_blocked,
                blocked_at=excluded.blocked_at,
                blocked_reason=excluded.blocked_reason,
                updated_at=excluded.updated_at
        """, (user_id, int(blocked), now if blocked else None, reason if blocked else "", now, now))
        conn.commit()

    def is_mcp_blocked(self, user_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT mcp_blocked FROM mcp_user_settings WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return bool(row["mcp_blocked"]) if row else False

    # ── MCP Tool Toggles ──────────────────────────────────────────────────

    def get_mcp_tool_toggles(self, user_id: int) -> Dict[str, bool]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT tool_name, enabled FROM mcp_tool_toggles WHERE user_id = ?",
            (user_id,)
        ).fetchall()
        return {row["tool_name"]: bool(row["enabled"]) for row in rows}

    def set_mcp_tool_toggle(self, user_id: int, tool_name: str, enabled: bool) -> None:
        conn = self._get_conn()
        now = utc_now()
        conn.execute("""
            INSERT INTO mcp_tool_toggles (user_id, tool_name, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, tool_name) DO UPDATE SET
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
        """, (user_id, tool_name, int(enabled), now, now))
        conn.commit()

    def is_mcp_tool_enabled(self, user_id: int, tool_name: str) -> bool:
        """Check if a specific MCP tool is enabled for a user. Default: True."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT enabled FROM mcp_tool_toggles WHERE user_id = ? AND tool_name = ?",
            (user_id, tool_name)
        ).fetchone()
        # Default is True (enabled) if no record exists
        return bool(row["enabled"]) if row else True

    def get_all_mcp_settings_for_admin(self) -> List[Dict[str, Any]]:
        """Get MCP settings for all users (admin view)."""
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT u.id as user_id, u.username,
                   COALESCE(s.mcp_blocked, 0) as mcp_blocked,
                   s.blocked_at, s.blocked_reason
            FROM users u
            LEFT JOIN mcp_user_settings s ON u.id = s.user_id
            WHERE u.role != 'admin'
            ORDER BY u.username
        """).fetchall()
        return [dict(row) for row in rows]
