"""
SQLite-based store for Xiaozhi Indonesia.
Optimized for VPS deployment with efficient queries and proper indexing.
"""
import hashlib
import json
import logging
import os
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
        """)
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
            return {"id": user_id, "username": username, "role": "user", "session_version": 1, "ui_theme": DEFAULT_UI_THEME}
        except sqlite3.IntegrityError:
            raise ValueError("Username sudah digunakan.")

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "session_version": row["session_version"],
            "ui_theme": row["ui_theme"],
        }

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        username = normalize_username(username)
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def search_users_by_prefix(self, prefix: str, limit: int = 8) -> List[Dict[str, str]]:
        prefix = normalize_username_prefix(prefix)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT username FROM users WHERE username LIKE ? ORDER BY username LIMIT ?",
            (f"{prefix}%", limit)
        ).fetchall()
        return [{"username": row["username"]} for row in rows]

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

    def list_categories(self, owner_id: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        self._seed_default_categories(owner_id)
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
        cursor = conn.execute(
            "DELETE FROM categories WHERE id = ? AND owner_id = ? AND LOWER(name) != ?",
            (category_id, owner_id, LIVE_API_CATEGORY)
        )
        conn.commit()
        return cursor.rowcount > 0

    def ensure_categories(self, owner_id: int, names: List[str]) -> List[str]:
        self._seed_default_categories(owner_id)
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

    def add_material(self, owner_id: int, title: str, category: str, content: str, keywords: str, api_url: str = "") -> None:
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

    def list_chat_history(self, owner_id: int, query: str = "", limit: int = 100, token_hash: str = "") -> List[Dict[str, Any]]:
        conn = self._get_conn()
        sql = "SELECT * FROM chat_history WHERE owner_id = ?"
        params = [owner_id]
        if query:
            sql += " AND (user_message LIKE ? OR xiaozhi_answer LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def chat_history_stats(self, owner_id: int, token_hash: str = "") -> Dict[str, Any]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as total FROM chat_history WHERE owner_id = ?",
            (owner_id,)
        ).fetchone()
        return {"total": row["total"] if row else 0}

    def clear_chat_history(self, owner_id: int) -> int:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM chat_history WHERE owner_id = ?", (owner_id,))
        conn.commit()
        return cursor.rowcount

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
        try:
            cursor = conn.execute(
                "INSERT INTO registered_devices (owner_id, device_id, device_name, device_type, created_at) VALUES (?, ?, ?, ?, ?)",
                (owner_id, device_id, name, device_type, utc_now())
            )
            conn.commit()
            return {"id": cursor.lastrowid, "device_id": device_id, "name": name}
        except sqlite3.IntegrityError:
            raise ValueError("Device sudah terdaftar.")

    def delete_device(self, owner_id: int, device_id: str) -> bool:
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM registered_devices WHERE device_id = ? AND owner_id = ?", (device_id, owner_id))
        conn.commit()
        return cursor.rowcount > 0

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
        row = conn.execute("SELECT * FROM user_limits WHERE user_id = ?", (owner_id,)).fetchone()
        limits = dict(USER_LIMIT_DEFAULTS)
        if row:
            limits = {k: row.get(k, v) for k, v in USER_LIMIT_DEFAULTS.items()}
        usage = {
            "materials": conn.execute("SELECT COUNT(*) FROM materials WHERE owner_id = ?", (owner_id,)).fetchone()[0],
            "live_apis": conn.execute("SELECT COUNT(*) FROM materials WHERE owner_id = ? AND source_type = ?", (owner_id, LIVE_API_SOURCE_TYPE)).fetchone()[0],
            "relay_rooms": conn.execute("SELECT COUNT(*) FROM relay_rooms WHERE owner_id = ?", (owner_id,)).fetchone()[0],
        }
        return {
            "is_admin": False,
            "limits": limits,
            "usage": usage,
            "materials": self._quota_item(usage["materials"], limits["max_materials"]),
            "words_per_material": self._quota_item(0, limits["max_words_per_material"]),
            "live_apis": self._quota_item(usage["live_apis"], limits["max_live_apis"]),
            "relay_rooms": self._quota_item(usage["relay_rooms"], limits["max_relay_rooms"]),
            "alerts": [],
        }

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
        rows = conn.execute("SELECT * FROM users WHERE role != 'admin' ORDER BY username").fetchall()
        result = []
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
            result.append({
                "id": user_id,
                "username": row["username"],
                "role": row["role"],
                "created_at": row["created_at"],
                "limits": limits,
                "usage": usage,
                "features": self.get_user_features(user_id),
                "mcp_status": {"has_token": False, "connected": False, "message": "", "updated_at": ""},
            })
        return result

    def get_pending_relay_commands(self, owner_id: int, room_id: int) -> List[Dict[str, Any]]:
        return []
