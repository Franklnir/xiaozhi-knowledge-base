import hashlib
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple, Union

from cryptography.fernet import Fernet, InvalidToken

# Optional HuggingFace Hub imports
try:
    from huggingface_hub import HfApi, hf_hub_download
except ImportError:
    HfApi = None
    hf_hub_download = None

from xiaozhi.config import (
    ADMIN_USERNAME,
    API_IMPORT_MAX_BYTES,
    API_IMPORT_MAX_CONTENT_BYTES,
    API_IMPORT_MAX_ITEMS,
    CHAT_HISTORY_DEFAULT_LIMIT,
    CHAT_HISTORY_MAX_TEXT_BYTES,
    DEFAULT_CATEGORIES,
    DEFAULT_UI_THEME,
    IS_PRODUCTION,
    LIVE_API_CATEGORY,
    LIVE_API_SOURCE_TYPE,
    MCP_TOKEN_HASH_LENGTH,
    REAL_RELAY_MAX_RELAYS,
    RESTORED_USER_USERNAME,
    UI_THEMES,
    USER_FEATURE_FLAGS,
    USER_LIMIT_DEFAULTS,
    fernet,
    logger as config_logger,
)
from xiaozhi.core.security import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    mask_secret,
    normalize_token_hash,
    normalize_username,
    normalize_username_prefix,
    verify_password,
    xiaozhi_token_hash,
)
from xiaozhi.core.utils import (
    build_api_materials,
    clean_multiline,
    clean_text,
    clamp_text_bytes,
    compact_text,
    content_size_metadata,
    count_text_words,
    format_size_mb,
    is_live_api_category,
    parse_int_range,
    parse_limit_value,
    safe_api_label,
    serialize_history_value,
    truncate_material_content,
    utc_now,
    utf8_size,
    normalize_mac_address,
    validate_external_api_url,
)
from xiaozhi.database.helpers import empty_database, normalize_database
from xiaozhi.services.mcp_service import mcp_connection_states

logger = logging.getLogger("xiaozhi.store")

PROTECTED_DEVICE_MACS = {"E8:3D:C1:9B:B5:14"}


class HFJsonStore:
    def __init__(self) -> None:
        self.token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")
        self.repo_id = os.getenv("HF_DATASET_REPO")
        self.filename = os.getenv("HF_DB_FILENAME", "app_data.json")
        self.cache_dir = Path(os.getenv("HF_DB_CACHE_DIR", ".hf_db_cache"))
        self.local_only_requested = os.getenv("HF_DB_LOCAL_ONLY", "").lower() in {"1", "true", "yes"}
        if IS_PRODUCTION and self.local_only_requested and os.getenv("ALLOW_LOCAL_DB_IN_PRODUCTION", "").lower() not in {"1", "true", "yes"}:
            raise RuntimeError("HF_DB_LOCAL_ONLY tidak aman untuk production.")
        if IS_PRODUCTION and not self.token and not self.local_only_requested:
            logger.warning("HF_TOKEN belum diset di production. Database memakai file lokal sementara. Set HF_TOKEN di HuggingFace Space Secrets.")
            self.local_only = True
        else:
            self.local_only = self.local_only_requested or not self.token
        self.api = HfApi(token=self.token) if self.token and HfApi else None
        self._data: Optional[Dict[str, Any]] = None
        self._lock = RLock()
        self._hf_healthy = False

        if self.local_only:
            logger.warning("HF_TOKEN belum diset. Database memakai file lokal sementara.")
        elif not HfApi or not hf_hub_download:
            raise RuntimeError("Paket huggingface_hub belum tersedia. Jalankan pip install -r requirements.txt.")

    @property
    def local_path(self) -> Path:
        return self.cache_dir / self.filename

    def _ensure_remote_repo(self) -> None:
        if self.local_only:
            return
        if not self.api:
            raise RuntimeError("Hugging Face API tidak tersedia.")
        if not self.repo_id:
            identity = self.api.whoami(token=self.token)
            username = identity.get("name")
            if not username:
                raise RuntimeError("Tidak bisa membaca username dari HF_TOKEN.")
            self.repo_id = f"{username}/xiaozhi-indonesia-db"
        self.api.create_repo(
            repo_id=self.repo_id,
            repo_type="dataset",
            private=True,
            exist_ok=True,
            token=self.token,
        )

    def _read_local(self) -> Dict[str, Any]:
        if not self.local_path.exists():
            return empty_database()
        with self.local_path.open("r", encoding="utf-8") as handle:
            return normalize_database(json.load(handle))

    def _write_local(self, data: Dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.local_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(normalize_database(data), handle, ensure_ascii=False, indent=2)
        tmp_path.replace(self.local_path)
        try:
            os.chmod(self.local_path, 0o600)
        except OSError:
            pass

    def _load_remote(self) -> Dict[str, Any]:
        self._ensure_remote_repo()
        try:
            downloaded_path = hf_hub_download(
                repo_id=self.repo_id,
                filename=self.filename,
                repo_type="dataset",
                token=self.token,
                force_download=True,
            )
            with open(downloaded_path, "r", encoding="utf-8") as handle:
                data = normalize_database(json.load(handle))
            self._write_local(data)
            self._hf_healthy = True
            return data
        except Exception as exc:
            message = str(exc).lower()
            if "401" in message or "unauthorized" in message or "invalid username" in message:
                logger.error("HF_TOKEN tidak valid atau expired. Beralih ke mode lokal. Perbaiki HF_TOKEN di HuggingFace Space Secrets.")
                self.local_only = True
                self._hf_healthy = False
                if self.local_path.exists():
                    return self._read_local()
                return empty_database()
            if "404" in message or "entry not found" in message or "not found" in message:
                data = empty_database()
                self._write_local(data)
                self._upload_remote(data, "Initialize database")
                return data
            if self.local_path.exists():
                logger.warning("Gagal memuat HF DB, memakai cache lokal: %s", exc)
                self._hf_healthy = False
                return self._read_local()
            raise RuntimeError("Gagal memuat database dari Hugging Face.") from exc

    def _upload_remote(self, data: Dict[str, Any], message: str = "Update EduSmart database") -> None:
        if self.local_only:
            return
        self._ensure_remote_repo()
        self.api.upload_file(
            path_or_fileobj=str(self.local_path),
            path_in_repo=self.filename,
            repo_id=self.repo_id,
            repo_type="dataset",
            token=self.token,
            commit_message=message,
        )

    def _load(self) -> Dict[str, Any]:
        if self._data is None:
            if self.local_only:
                self._data = self._read_local()
            else:
                try:
                    self._data = self._load_remote()
                except Exception:
                    logger.warning("Gagal load remote, fallback ke lokal.")
                    self.local_only = True
                    self._data = self._read_local()
        return self._data

    def _commit(self, data: Dict[str, Any], message: str = "Update database") -> None:
        self._data = normalize_database(data)
        self._write_local(self._data)
        if not self.local_only:
            try:
                self._upload_remote(self._data, message)
            except Exception as exc:
                logger.warning("Gagal upload ke HF, data tersimpan lokal: %s", exc)

    @staticmethod
    def _next_id(data: Dict[str, Any], bucket: str) -> int:
        next_id = int(data["next_ids"].get(bucket, 1))
        data["next_ids"][bucket] = next_id + 1
        return next_id

    def _seed_default_categories(self, data: Dict[str, Any], user_id: int) -> bool:
        existing_names = {
            cat.get("name", "").lower()
            for cat in data["categories"]
            if int(cat.get("owner_id", 0)) == user_id
        }
        changed = False
        for name in DEFAULT_CATEGORIES:
            if name.lower() in existing_names:
                continue
            data["categories"].append(
                {
                    "id": self._next_id(data, "categories"),
                    "owner_id": user_id,
                    "name": name,
                    "created_at": utc_now(),
                }
            )
            existing_names.add(name.lower())
            changed = True
        return changed

    def _ensure_live_api_category(self, data: Dict[str, Any], user_id: int) -> bool:
        existing_names = {
            cat.get("name", "").lower()
            for cat in data["categories"]
            if int(cat.get("owner_id", 0)) == user_id
        }
        if LIVE_API_CATEGORY.lower() not in existing_names:
            data["categories"].append(
                {
                    "id": self._next_id(data, "categories"),
                    "owner_id": user_id,
                    "name": LIVE_API_CATEGORY,
                    "created_at": utc_now(),
                }
            )
            return True
        return False

    @staticmethod
    def _public_user(user: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": int(user["id"]),
            "username": user["username"],
            "role": str(user.get("role") or "user").lower(),
            "session_version": max(1, int(user.get("session_version", 1) or 1)),
            "ui_theme": str(user.get("ui_theme") or DEFAULT_UI_THEME) if str(user.get("ui_theme") or DEFAULT_UI_THEME) in UI_THEMES else DEFAULT_UI_THEME,
            "google_id": user.get("google_id"),
            "google_email": user.get("google_email"),
            "registered_with_google": bool(user.get("registered_with_google", False)),
            "created_at": user.get("created_at"),
        }

    @staticmethod
    def _is_admin_user(user: Optional[Dict[str, Any]]) -> bool:
        return bool(user) and str(user.get("role") or "").lower() == "admin"

    @staticmethod
    def _limits_from_row(row: Optional[Dict[str, Any]]) -> Dict[str, int]:
        limits = dict(USER_LIMIT_DEFAULTS)
        if row:
            for key in USER_LIMIT_DEFAULTS:
                try:
                    limits[key] = max(0, int(row.get(key, limits[key]) or 0))
                except (TypeError, ValueError):
                    limits[key] = USER_LIMIT_DEFAULTS[key]
        return limits

    def _limits_for_user_unlocked(self, data: Dict[str, Any], owner_id: int) -> Dict[str, int]:
        row = next(
            (item for item in data["user_limits"] if int(item.get("user_id", 0)) == int(owner_id)),
            None,
        )
        return self._limits_from_row(row)

    def _usage_for_user_unlocked(self, data: Dict[str, Any], owner_id: int) -> Dict[str, int]:
        user_id = int(owner_id)
        materials = [item for item in data["materials"] if int(item.get("owner_id", 0)) == user_id]
        return {
            "materials": len(materials),
            "live_apis": sum(1 for item in materials if item.get("source_type") == LIVE_API_SOURCE_TYPE),
            "relay_rooms": sum(1 for item in data["relay_rooms"] if int(item.get("owner_id", 0)) == user_id),
        }

    def _owner_is_admin_unlocked(self, data: Dict[str, Any], owner_id: int) -> bool:
        user = next((item for item in data["users"] if int(item.get("id", 0)) == int(owner_id)), None)
        return self._is_admin_user(user)

    def _enforce_material_word_limit_unlocked(self, data: Dict[str, Any], owner_id: int, content: str) -> None:
        if self._owner_is_admin_unlocked(data, owner_id):
            return
        limit = self._limits_for_user_unlocked(data, owner_id)["max_words_per_material"]
        words = count_text_words(content)
        if limit and words > limit:
            raise ValueError(f"Isi materi terlalu panjang. Maksimal {limit} kata per materi. Saat ini {words} kata.")

    def _enforce_material_total_limit_unlocked(self, data: Dict[str, Any], owner_id: int, additional: int = 1) -> None:
        if additional <= 0 or self._owner_is_admin_unlocked(data, owner_id):
            return
        limit = self._limits_for_user_unlocked(data, owner_id)["max_materials"]
        current = self._usage_for_user_unlocked(data, owner_id)["materials"]
        if limit and current + additional > limit:
            raise ValueError(f"Batas total materi akun ini adalah {limit}. Hapus materi lama atau minta admin menaikkan limit.")

    def _enforce_live_api_limit_unlocked(self, data: Dict[str, Any], owner_id: int, additional: int = 1) -> None:
        if additional <= 0 or self._owner_is_admin_unlocked(data, owner_id):
            return
        limit = self._limits_for_user_unlocked(data, owner_id)["max_live_apis"]
        current = self._usage_for_user_unlocked(data, owner_id)["live_apis"]
        if limit and current + additional > limit:
            raise ValueError(f"Batas API realtime akun ini adalah {limit} endpoint.")

    def _enforce_relay_room_limit_unlocked(self, data: Dict[str, Any], owner_id: int, additional: int = 1) -> None:
        if additional <= 0 or self._owner_is_admin_unlocked(data, owner_id):
            return
        limit = self._limits_for_user_unlocked(data, owner_id)["max_relay_rooms"]
        current = self._usage_for_user_unlocked(data, owner_id)["relay_rooms"]
        if limit and current + additional > limit:
            raise ValueError(f"Batas perangkat Relay Nyata akun ini adalah {limit} ruangan.")

    def has_admin_user(self) -> bool:
        with self._lock:
            data = self._load()
            return any(u.get("role") == "admin" for u in data.get("users", []))

    def ensure_admin_user(self, username: str, password: str) -> Dict[str, Any]:
        username = normalize_username(username)
        with self._lock:
            data = self._load()
            existing = next((user for user in data["users"] if user.get("username") == username), None)
            changed = False
            if existing:
                if existing.get("role") != "admin":
                    existing["role"] = "admin"
                    changed = True
                if password and not verify_password(password, existing.get("password_hash", "")):
                    existing["password_hash"] = hash_password(password)
                    existing["session_version"] = max(1, int(existing.get("session_version", 1) or 1)) + 1
                    changed = True
                if changed:
                    existing["updated_at"] = utc_now()
                    self._commit(data, "Ensure EduSmart admin")
                return self._public_user(existing)

            user = {
                "id": self._next_id(data, "users"),
                "username": username,
                "password_hash": hash_password(password),
                "role": "admin",
                "session_version": 1,
                "ui_theme": DEFAULT_UI_THEME,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            data["users"].append(user)
            self._commit(data, "Create EduSmart admin")
            return self._public_user(user)

    def restore_regular_user_account(
        self,
        username: str,
        password: str,
        *,
        possible_renamed_username: str = "",
    ) -> Dict[str, Any]:
        username = normalize_username(username)
        possible_renamed_username = normalize_username(possible_renamed_username) if possible_renamed_username else ""
        with self._lock:
            data = self._load()
            existing = next((user for user in data["users"] if user.get("username") == username), None)
            changed = False
            if not existing and possible_renamed_username:
                renamed = next(
                    (
                        user
                        for user in data["users"]
                        if user.get("username") == possible_renamed_username
                        and self._is_admin_user(user)
                    ),
                    None,
                )
                if renamed:
                    existing = renamed
                    existing["username"] = username
                    changed = True
            if existing:
                if existing.get("role") != "user":
                    existing["role"] = "user"
                    changed = True
                if password and not verify_password(password, existing.get("password_hash", "")):
                    existing["password_hash"] = hash_password(password)
                    existing["session_version"] = max(1, int(existing.get("session_version", 1) or 1)) + 1
                    changed = True
                if changed:
                    existing["updated_at"] = utc_now()
                    self._commit(data, "Restore EduSmart user")
                return self._public_user(existing)

            user = {
                "id": self._next_id(data, "users"),
                "username": username,
                "password_hash": hash_password(password),
                "role": "user",
                "session_version": 1,
                "ui_theme": DEFAULT_UI_THEME,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            data["users"].append(user)
            self._seed_default_categories(data, int(user["id"]))
            self._commit(data, "Create restored EduSmart user")
            return self._public_user(user)

    def list_admin_manageable_users(self, admin_username: str = ADMIN_USERNAME) -> List[Dict[str, Any]]:
        admin_username = normalize_username(admin_username)
        with self._lock:
            data = self._load()
            rows = []
            for user in data["users"]:
                if self._is_admin_user(user) or user.get("username") == admin_username:
                    continue
                user_id = int(user.get("id", 0))
                dev_mac = ""
                dev_name = ""
                for dev in data.get("registered_devices", []):
                    if int(dev.get("owner_id", 0)) == user_id:
                        dev_mac = str(dev.get("device_id", "")).upper()
                        dev_name = dev.get("device_name", "")
                        break

                # Check active playback in playback_tracker
                active_session = None
                try:
                    from xiaozhi.services.playback_tracker import playback_tracker
                    active_session = playback_tracker.get_session_by_user(user_id)
                except Exception:
                    pass

                is_playing = active_session is not None
                current_track = active_session.title if active_session else ""
                if not dev_mac and active_session and active_session.device_mac:
                    dev_mac = str(active_session.device_mac).upper()
                    dev_name = f"ESP32 ({dev_mac[-5:]})"

                # MCP status for this user
                has_token = any(
                    int(t.get("user_id", 0)) == user_id
                    for t in data.get("xiaozhi_tokens", [])
                )
                mcp_state = mcp_connection_states.get(user_id, {})
                mcp_connected = bool(mcp_state.get("connected"))
                mcp_message = mcp_state.get("message", "")
                rows.append(
                    {
                        **self._public_user(user),
                        "created_at": user.get("created_at", ""),
                        "limits": self._limits_for_user_unlocked(data, user_id),
                        "usage": self._usage_for_user_unlocked(data, user_id),
                        "features": self.get_user_features(user_id),
                        "device_mac": dev_mac,
                        "device_name": dev_name,
                        "is_playing": is_playing,
                        "current_track": current_track,
                        "mcp_status": {
                            "has_token": has_token,
                            "connected": mcp_connected,
                            "message": mcp_message,
                            "updated_at": mcp_state.get("updated_at", ""),
                        },
                    }
                )
            # Prioritas Urutan Tampilan Admin:
            # 1. Paling atas: User yang sedang memutar YouTube Music
            # 2. Kedua: User yang sudah terhubung ke MCP
            # 3. Ketiga: User lainnya (diurutkan berdasarkan user baru / id desc)
            return sorted(rows, key=lambda u: (
                0 if u.get("is_playing") else 1,
                0 if u.get("mcp_status", {}).get("connected") else 1,
                -int(u.get("id", 0))
            ))

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

    def get_user_features(self, owner_id: int) -> Dict[str, bool]:
        """Get feature flags for user. Admin always gets all enabled."""
        with self._lock:
            data = self._load()
            is_admin = self._owner_is_admin_unlocked(data, owner_id)
        if is_admin:
            return {k: True for k in USER_FEATURE_FLAGS}
        row = next(
            (item for item in data.get("user_limits", []) if int(item.get("user_id", 0)) == int(owner_id)),
            None,
        )
        features = dict(USER_FEATURE_FLAGS)
        if row:
            for key in USER_FEATURE_FLAGS:
                if f"feature_{key}" in row:
                    features[key] = bool(row[f"feature_{key}"])
        return features

    def set_user_feature(self, owner_id: int, feature: str, enabled: bool) -> None:
        if feature not in USER_FEATURE_FLAGS:
            raise ValueError(f"Feature tidak dikenal: {feature}")
        with self._lock:
            data = self._load()
            row = next(
                (item for item in data.get("user_limits", []) if int(item.get("user_id", 0)) == int(owner_id)),
                None,
            )
            if row:
                row[f"feature_{feature}"] = bool(enabled)
                row["updated_at"] = utc_now()
            else:
                data.setdefault("user_limits", []).append({
                    "user_id": int(owner_id),
                    **{k: v for k, v in USER_LIMIT_DEFAULTS.items()},
                    f"feature_{feature}": bool(enabled),
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                })
            self._commit(data, f"Set feature {feature}={enabled} for user {owner_id}")

    def user_quota(self, owner_id: int) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            limits = self._limits_for_user_unlocked(data, owner_id)
            usage = self._usage_for_user_unlocked(data, owner_id)
            is_admin = self._owner_is_admin_unlocked(data, owner_id)
        if is_admin:
            admin_limits = {key: 0 for key in USER_LIMIT_DEFAULTS}
            return {
                "is_admin": True,
                "limits": admin_limits,
                "usage": usage,
                "materials": self._quota_item(usage["materials"], 0),
                "words_per_material": self._quota_item(0, 0),
                "live_apis": self._quota_item(usage["live_apis"], 0),
                "relay_rooms": self._quota_item(usage["relay_rooms"], 0),
                "alerts": [],
            }
        quota = {
            "is_admin": False,
            "limits": limits,
            "usage": usage,
            "materials": self._quota_item(usage["materials"], limits["max_materials"]),
            "words_per_material": self._quota_item(0, limits["max_words_per_material"]),
            "live_apis": self._quota_item(usage["live_apis"], limits["max_live_apis"]),
            "relay_rooms": self._quota_item(usage["relay_rooms"], limits["max_relay_rooms"]),
            "alerts": [],
        }
        if quota["materials"]["reached"]:
            quota["alerts"].append("Batas total materi sudah tercapai.")
        if quota["live_apis"]["reached"]:
            quota["alerts"].append("Batas API realtime sudah tercapai.")
        if quota["relay_rooms"]["reached"]:
            quota["alerts"].append("Batas perangkat Relay Nyata sudah tercapai.")
        quota["features"] = self.get_user_features(owner_id)
        return quota

    def set_user_limits(
        self,
        target_user_id: int,
        *,
        max_materials: Any,
        max_words_per_material: Any,
        max_live_apis: Any,
        max_relay_rooms: Any,
    ) -> Dict[str, int]:
        limits = {
            "max_materials": parse_limit_value(max_materials, field="Batas total materi"),
            "max_words_per_material": parse_limit_value(max_words_per_material, field="Batas kata per materi"),
            "max_live_apis": parse_limit_value(max_live_apis, field="Batas API realtime"),
            "max_relay_rooms": parse_limit_value(max_relay_rooms, field="Batas perangkat relay"),
        }
        with self._lock:
            data = self._load()
            target = next((user for user in data["users"] if int(user.get("id", 0)) == int(target_user_id)), None)
            if not target:
                raise ValueError("User tidak ditemukan.")
            if self._is_admin_user(target):
                raise ValueError("Batas akun admin tidak bisa diubah dari halaman ini.")
            row = next(
                (item for item in data["user_limits"] if int(item.get("user_id", 0)) == int(target_user_id)),
                None,
            )
            if row:
                row.update(limits)
                row["updated_at"] = utc_now()
            else:
                data["user_limits"].append(
                    {
                        "user_id": int(target_user_id),
                        **limits,
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
            self._commit(data, "Update user limits")
        return limits

    def create_user(self, username: str, password: str) -> Dict[str, Any]:
        username = normalize_username(username)
        password_hash = hash_password(password)
        with self._lock:
            data = self._load()
            if any(user.get("username") == username for user in data["users"]):
                raise ValueError("Username sudah digunakan.")
            user = {
                "id": self._next_id(data, "users"),
                "username": username,
                "password_hash": password_hash,
                "role": "user",
                "session_version": 1,
                "ui_theme": DEFAULT_UI_THEME,
                "created_at": utc_now(),
            }
            data["users"].append(user)
            self._seed_default_categories(data, int(user["id"]))
            self._commit(data, "Create EduSmart user")
            return self._public_user(user)

    def rotate_user_session(self, user_id: int) -> None:
        with self._lock:
            data = self._load()
            user = next((item for item in data["users"] if int(item.get("id", 0)) == int(user_id)), None)
            if not user:
                return
            try:
                current = max(1, int(user.get("session_version", 1) or 1))
            except (TypeError, ValueError):
                current = 1
            user["session_version"] = current + 1
            user["updated_at"] = utc_now()
            self._commit(data, "Rotate user session")

    def set_user_theme(self, user_id: int, theme: str) -> Dict[str, Any]:
        theme = str(theme or "default").strip().lower()
        if theme not in UI_THEMES:
            raise ValueError("Tema tidak valid.")
        with self._lock:
            data = self._load()
            user = next((item for item in data["users"] if int(item.get("id", 0)) == int(user_id)), None)
            if not user:
                raise ValueError("User tidak ditemukan.")
            if user.get("ui_theme") != theme:
                user["ui_theme"] = theme
                user["updated_at"] = utc_now()
                self._commit(data, "Update user theme")
            return self._public_user(user)

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        username = normalize_username(username)
        with self._lock:
            data = self._load()
            return next((user for user in data["users"] if user.get("username") == username), None)

    def get_user_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        if not google_id:
            return None
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if str(u.get("google_id") or "") == str(google_id)), None)
            return dict(user) if user else None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        if not email:
            return None
        target = email.strip().lower()
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if str(u.get("google_email") or "").strip().lower() == target), None)
            return dict(user) if user else None

    def link_google_account(self, user_id: int, google_id: str, google_email: str) -> None:
        with self._lock:
            data = self._load()
            existing = next(
                (u for u in data["users"] if str(u.get("google_id") or "") == str(google_id) and int(u.get("id", 0)) != int(user_id)),
                None,
            )
            if existing:
                raise ValueError("Akun Google ini sudah tertaut dengan akun lain.")
            user = next((u for u in data["users"] if int(u.get("id", 0)) == int(user_id)), None)
            if not user:
                raise ValueError("Pengguna tidak ditemukan.")
            user["google_id"] = str(google_id)
            user["google_email"] = str(google_email).strip().lower()
            user["updated_at"] = utc_now()
            self._commit(data, "Link Google account")

    def unlink_google_account(self, user_id: int) -> None:
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if int(u.get("id", 0)) == int(user_id)), None)
            if not user:
                raise ValueError("Pengguna tidak ditemukan.")
            if user.get("registered_with_google"):
                raise ValueError("Akun ini didaftarkan menggunakan Google sehingga tautan Google bersifat permanen dan tidak dapat dilepas.")
            user["google_id"] = None
            user["google_email"] = None
            user["updated_at"] = utc_now()
            self._commit(data, "Unlink Google account")

    def link_firebase_account(self, user_id: int, firebase_uid: str, firebase_email: Optional[str] = None) -> None:
        with self._lock:
            data = self._load()
            user = next((u for u in data["users"] if int(u.get("id", 0)) == int(user_id)), None)
            if not user:
                raise ValueError("Pengguna tidak ditemukan.")
            user["firebase_uid"] = str(firebase_uid)
            if firebase_email:
                user["firebase_email"] = str(firebase_email).strip().lower()
            user["updated_at"] = utc_now()
            self._commit(data, "Link Firebase account")

    def create_google_user(self, username: str, google_id: str, google_email: str) -> Dict[str, Any]:
        username = normalize_username(username)
        random_pwd = secrets.token_urlsafe(32)
        password_hash = hash_password(random_pwd)
        with self._lock:
            data = self._load()
            if any(user.get("username") == username for user in data["users"]):
                raise ValueError("Username sudah digunakan.")
            if any(str(user.get("google_id") or "") == str(google_id) for user in data["users"]):
                raise ValueError("Akun Google ini sudah terdaftar.")
            user = {
                "id": self._next_id(data, "users"),
                "username": username,
                "password_hash": password_hash,
                "role": "user",
                "session_version": 1,
                "ui_theme": DEFAULT_UI_THEME,
                "google_id": str(google_id),
                "google_email": str(google_email).strip().lower(),
                "registered_with_google": True,
                "created_at": utc_now(),
            }
            data["users"].append(user)
            self._seed_default_categories(data, int(user["id"]))
            self._commit(data, "Create Google EduSmart user")
            return self._public_user(user)

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            user = next((user for user in data["users"] if int(user.get("id", 0)) == int(user_id)), None)
            return self._public_user(user) if user else None

    get_user_by_id = get_user

    def search_users_by_prefix(self, prefix: str, limit: int = 8) -> List[Dict[str, str]]:
        prefix = normalize_username_prefix(prefix)
        with self._lock:
            data = self._load()
            matches = [
                {"username": user.get("username", "")}
                for user in data["users"]
                if user.get("username", "").startswith(prefix)
            ]
            return sorted(matches, key=lambda item: item["username"])[:limit]

    def list_categories(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            seeded = self._ensure_live_api_category(data, owner_id)
            categories = [
                {"id": int(cat["id"]), "name": cat["name"]}
                for cat in data["categories"]
                if int(cat.get("owner_id", 0)) == int(owner_id)
            ]
            if seeded:
                self._commit(data, "Ensure live api category")
            return sorted(categories, key=lambda item: item["name"].lower())

    def add_category(self, owner_id: int, name: str) -> None:
        name = clean_text(name, max_len=80, min_len=2, field="Nama kategori")
        with self._lock:
            data = self._load()
            duplicate = any(
                int(cat.get("owner_id", 0)) == int(owner_id) and cat.get("name", "").lower() == name.lower()
                for cat in data["categories"]
            )
            if duplicate:
                raise ValueError("Kategori sudah ada.")
            data["categories"].append(
                {
                    "id": self._next_id(data, "categories"),
                    "owner_id": int(owner_id),
                    "name": name,
                    "created_at": utc_now(),
                }
            )
            self._commit(data, "Add category")

    def ensure_categories(self, owner_id: int, names: List[str]) -> List[str]:
        cleaned_names = []
        for name in names:
            cleaned = clean_text(name, max_len=80, min_len=2, field="Nama kategori")
            if cleaned.lower() not in {item.lower() for item in cleaned_names}:
                cleaned_names.append(cleaned)
        with self._lock:
            data = self._load()
            self._ensure_live_api_category(data, owner_id)
            existing_lookup = {
                cat.get("name", "").lower(): cat.get("name", "")
                for cat in data["categories"]
                if int(cat.get("owner_id", 0)) == int(owner_id)
            }
            changed = False
            final_names = []
            for name in cleaned_names:
                existing = existing_lookup.get(name.lower())
                if existing:
                    final_names.append(existing)
                    continue
                data["categories"].append(
                    {
                        "id": self._next_id(data, "categories"),
                        "owner_id": int(owner_id),
                        "name": name,
                        "created_at": utc_now(),
                    }
                )
                existing_lookup[name.lower()] = name
                final_names.append(name)
                changed = True
            if changed:
                self._commit(data, "Ensure API import categories")
            return final_names

    def delete_category(self, owner_id: int, category_id: int) -> bool:
        with self._lock:
            data = self._load()
            target = next(
                (
                    cat
                    for cat in data["categories"]
                    if int(cat.get("id", 0)) == int(category_id) and int(cat.get("owner_id", 0)) == int(owner_id)
                ),
                None,
            )
            if not target:
                return False
            if is_live_api_category(target.get("name", "")):
                raise ValueError("Kategori data dari api bersifat permanen.")
            before = len(data["categories"])
            data["categories"] = [
                cat
                for cat in data["categories"]
                if not (int(cat.get("id", 0)) == int(category_id) and int(cat.get("owner_id", 0)) == int(owner_id))
            ]
            changed = len(data["categories"]) != before
            if changed:
                self._commit(data, "Delete category")
            return changed

    def list_materials(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            materials = [
                self._public_material(item)
                for item in data["materials"]
                if int(item.get("owner_id", 0)) == int(owner_id)
            ]
            return sorted(materials, key=lambda item: int(item["id"]), reverse=True)

    def get_material(
        self,
        owner_id: int,
        material_id: int,
        *,
        include_live_api: bool = False,
        previous_live_hash: str = "",
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            row = next(
                (
                    dict(item)
                    for item in data["materials"]
                    if int(item.get("id", 0)) == int(material_id) and int(item.get("owner_id", 0)) == int(owner_id)
                ),
                None,
            )
        if not row:
            return None
        return self._public_material(row, include_live_api=include_live_api, previous_live_hash=previous_live_hash)

    @staticmethod
    def _material_api_url(item: Dict[str, Any]) -> str:
        encrypted = item.get("api_url_ciphertext", "")
        if encrypted:
            return decrypt_secret(encrypted) or ""
        return item.get("api_url", "")

    def _public_material(
        self,
        item: Dict[str, Any],
        *,
        include_live_api: bool = False,
        previous_live_hash: str = "",
    ) -> Dict[str, Any]:
        result = {
            "id": int(item["id"]),
            "title": item.get("title", ""),
            "category": item.get("category", ""),
            "content": item.get("content", ""),
            "keywords": item.get("keywords", ""),
            "owner_id": int(item.get("owner_id", 0)),
        }
        if item.get("source_type") == LIVE_API_SOURCE_TYPE:
            api_url = self._material_api_url(item)
            result.update(
                {
                    "source_type": LIVE_API_SOURCE_TYPE,
                    "api_url": api_url,
                    "api_label": safe_api_label(api_url) if api_url else "",
                }
            )
            if include_live_api and api_url:
                stored_content = result.get("content", "")
                result["stored_content"] = stored_content
                try:
                    preview = preview_live_api_content(api_url, previous_live_hash)
                    live_content = preview["content"]
                    result["live_content"] = live_content
                    result["live_content_hash"] = preview["content_hash"]
                    result["live_changed"] = preview["changed"]
                    if preview["changed"]:
                        result["content"] = combine_live_api_content(stored_content, live_content)
                        result.update(content_size_metadata(result["content"]))
                    result["live_updated_at"] = preview["updated_at"]
                    result["live_total_items"] = preview["total_items"]
                except ValueError as exc:
                    result["content"] = truncate_material_content(
                        "Data realtime dari API tidak bisa dibaca saat ini.\n"
                        f"Endpoint: {safe_api_label(api_url)}\n"
                        f"Error: {exc}\n\n"
                        "Data tersimpan sebelumnya:\n"
                        f"{stored_content or '-'}"
                    )
                    result["live_content"] = ""
                    result["live_content_hash"] = ""
                    result.update(content_size_metadata(result["content"]))
        if "content_size_bytes" not in result:
            result.update(content_size_metadata(result.get("content", "")))
        return result

    @staticmethod
    def _clean_material(title: str, category: str, content: str, keywords: str, api_url: str = "") -> Dict[str, str]:
        clean_category = clean_text(category, max_len=80, min_len=2, field="Kategori")
        payload = {
            "title": clean_text(title, max_len=160, min_len=3, field="Judul"),
            "category": clean_category,
            "keywords": clean_text(keywords, max_len=300, field="Kata kunci"),
        }
        if is_live_api_category(clean_category):
            clean_api_url = validate_external_api_url(api_url)
            content_value = content.strip() or f"Data realtime dari API: {safe_api_label(clean_api_url)}"
            payload["content"] = clean_multiline(
                content_value,
                max_bytes=API_IMPORT_MAX_CONTENT_BYTES,
                min_len=5,
                field="Isi data",
            )
            payload["api_url"] = clean_api_url
        else:
            payload["content"] = clean_multiline(
                content,
                max_bytes=API_IMPORT_MAX_CONTENT_BYTES,
                min_len=5,
                field="Isi data",
            )
        return payload

    def add_material(self, owner_id: int, title: str, category: str, content: str, keywords: str, api_url: str = "") -> None:
        payload = self._clean_material(title, category, content, keywords, api_url)
        clean_api_url = payload.pop("api_url", "")
        with self._lock:
            data = self._load()
            self._enforce_material_total_limit_unlocked(data, owner_id, additional=1)
            self._enforce_material_word_limit_unlocked(data, owner_id, payload.get("content", ""))
            if clean_api_url:
                self._enforce_live_api_limit_unlocked(data, owner_id, additional=1)
            row = {
                "id": self._next_id(data, "materials"),
                "owner_id": int(owner_id),
                **payload,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            if clean_api_url:
                row["source_type"] = LIVE_API_SOURCE_TYPE
                row["api_url_ciphertext"] = encrypt_secret(clean_api_url)
            data["materials"].append(row)
            self._commit(data, "Add material")

    def upsert_api_materials(self, owner_id: int, materials: List[Dict[str, str]]) -> Dict[str, int]:
        if not materials:
            return {"created": 0, "updated": 0, "skipped": 0, "total": 0}
        self.ensure_categories(owner_id, [item.get("category", "") for item in materials])
        created = updated = skipped = 0
        with self._lock:
            data = self._load()
            existing_by_source: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for item in data["materials"]:
                if int(item.get("owner_id", 0)) != int(owner_id):
                    continue
                if item.get("source_type") == "api" and item.get("source_hash") and item.get("source_key"):
                    existing_by_source[(str(item.get("source_hash")), str(item.get("source_key")))] = item

            prepared_materials = []
            potential_creates = 0
            for material in materials:
                payload = self._clean_material(
                    material.get("title", ""),
                    material.get("category", ""),
                    material.get("content", ""),
                    material.get("keywords", ""),
                )
                self._enforce_material_word_limit_unlocked(data, owner_id, payload.get("content", ""))
                source_hash = clean_text(material.get("source_hash", ""), max_len=80, min_len=16, field="Source API")
                source_key = clean_text(material.get("source_key", ""), max_len=80, min_len=16, field="ID data API")
                existing = existing_by_source.get((source_hash, source_key))
                if not existing:
                    potential_creates += 1
                prepared_materials.append((payload, source_hash, source_key, existing))

            self._enforce_material_total_limit_unlocked(data, owner_id, additional=potential_creates)

            for payload, source_hash, source_key, existing in prepared_materials:
                if existing:
                    changed = any(existing.get(key) != value for key, value in payload.items())
                    if changed:
                        existing.update(payload)
                        existing["updated_at"] = utc_now()
                        updated += 1
                    else:
                        skipped += 1
                    continue
                data["materials"].append(
                    {
                        "id": self._next_id(data, "materials"),
                        "owner_id": int(owner_id),
                        **payload,
                        "source_type": "api",
                        "source_hash": source_hash,
                        "source_key": source_key,
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
                created += 1
            if created or updated:
                self._commit(data, "Import API materials")
        return {"created": created, "updated": updated, "skipped": skipped, "total": len(materials)}

    def update_material(
        self,
        owner_id: int,
        material_id: int,
        title: str,
        category: str,
        content: str,
        keywords: str,
        api_url: str = "",
    ) -> bool:
        payload = self._clean_material(title, category, content, keywords, api_url)
        clean_api_url = payload.pop("api_url", "")
        with self._lock:
            data = self._load()
            for item in data["materials"]:
                if int(item.get("id", 0)) == int(material_id) and int(item.get("owner_id", 0)) == int(owner_id):
                    self._enforce_material_word_limit_unlocked(data, owner_id, payload.get("content", ""))
                    was_live_api = item.get("source_type") == LIVE_API_SOURCE_TYPE
                    if clean_api_url and not was_live_api:
                        self._enforce_live_api_limit_unlocked(data, owner_id, additional=1)
                    item.update(payload)
                    if clean_api_url:
                        item["source_type"] = LIVE_API_SOURCE_TYPE
                        item["api_url_ciphertext"] = encrypt_secret(clean_api_url)
                        item.pop("api_url", None)
                    else:
                        item.pop("source_type", None)
                        item.pop("api_url_ciphertext", None)
                        item.pop("api_url", None)
                    item["updated_at"] = utc_now()
                    self._commit(data, "Update material")
                    return True
            return False

    def delete_material(self, owner_id: int, material_id: int) -> bool:
        with self._lock:
            data = self._load()
            before = len(data["materials"])
            data["materials"] = [
                item
                for item in data["materials"]
                if not (int(item.get("id", 0)) == int(material_id) and int(item.get("owner_id", 0)) == int(owner_id))
            ]
            changed = len(data["materials"]) != before
            if changed:
                self._commit(data, "Delete material")
            return changed

    def set_xiaozhi_token(self, owner_id: int, token: str, slot: int = 1, device_label: str = "") -> None:
        slot_num = int(slot or 1)
        if slot_num < 1 or slot_num > 3:
            raise ValueError("Slot token XiaoZhi hanya diizinkan untuk Slot 1, 2, atau 3.")
        token = clean_multiline(token, max_len=2000, min_len=10, field="Endpoint Xiaozhi")
        if not token.startswith("wss://"):
            raise ValueError("Endpoint Xiaozhi harus diawali wss://")
        encrypted = encrypt_secret(token)
        token_hash = xiaozhi_token_hash(token)
        label_clean = (device_label or "").strip()[:60] or f"XiaoZhi {slot_num}"
        with self._lock:
            data = self._load()
            existing = next(
                (item for item in data["xiaozhi_tokens"] if int(item.get("user_id", 0)) == int(owner_id) and int(item.get("slot_number", 1)) == slot_num),
                None,
            )
            u_row = next((u for u in data.get("users", []) if int(u.get("id", 0)) == int(owner_id)), None)
            username = u_row["username"] if u_row else f"user_{owner_id}"
            default_label = f"{username} - Slot 1" if slot_num == 1 else f"XiaoZhi {slot_num}"
            label_clean = (device_label or "").strip()[:60] or (existing.get("device_label") if existing else "") or default_label

            if existing:
                if existing.get("token_hash") != token_hash:
                    # Token berubah -> lepas board_mac
                    existing["board_mac"] = ""
                existing["token_ciphertext"] = encrypted
                existing["token_hash"] = token_hash
                existing["device_label"] = label_clean
                existing["updated_at"] = utc_now()
            else:
                data["xiaozhi_tokens"].append(
                    {
                        "user_id": int(owner_id),
                        "slot_number": slot_num,
                        "device_label": label_clean,
                        "board_mac": "",
                        "token_ciphertext": encrypted,
                        "token_hash": token_hash,
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
            self._commit(data, "Save Xiaozhi token")

    def bind_board_to_slot(self, owner_id: int, slot: int = 1, device_mac: str = "", request_id: str = "") -> Dict[str, Any]:
        """Kunci board hardware MAC ke slot tertentu secara read-only (anti-spoofing)."""
        norm_mac = normalize_mac_address(device_mac)
        if not norm_mac:
            return {"success": False, "detail": "Format MAC address tidak valid."}
        slot_num = int(slot or 1)
        with self._lock:
            data = self._load()
            existing = next(
                (item for item in data["xiaozhi_tokens"] if int(item.get("user_id", 0)) == int(owner_id) and int(item.get("slot_number", 1)) == slot_num),
                None,
            )
            if not existing:
                return {"success": False, "detail": f"Slot {slot_num} belum tersimpan endpoint MCP."}

            current_mac = (existing.get("board_mac") or "").strip().upper()
            label = existing.get("device_label") or f"Slot {slot_num}"
            if current_mac and current_mac != norm_mac:
                return {
                    "success": False,
                    "detail": f"Slot {slot_num} ({label}) telah terkunci ke board MAC {current_mac}. Lepaskan board terlebih dahulu.",
                    "current_mac": current_mac,
                    "is_locked": True,
                }

            # Jika MAC ini sebelumnya ada di slot user lain, bersihkan
            for other in data["xiaozhi_tokens"]:
                if int(other.get("user_id", 0)) != int(owner_id):
                    other_mac = normalize_mac_address(other.get("board_mac", ""))
                    if other_mac and other_mac.lower() == norm_mac.lower():
                        other["board_mac"] = ""
                        other["updated_at"] = utc_now()

            existing["board_mac"] = norm_mac
            existing["updated_at"] = utc_now()

            for dev in data.get("registered_devices", []):
                d_mac = normalize_mac_address(dev.get("device_id", "") or dev.get("mac_address", ""))
                if d_mac and d_mac.lower() == norm_mac.lower():
                    dev["owner_id"] = int(owner_id)
                    dev["status"] = "ACTIVE"
                    dev["last_active_at"] = utc_now()

            self._commit(data, f"Bind board {norm_mac} to slot {slot_num}")
        return {"success": True, "slot": slot_num, "board_mac": norm_mac, "label": label, "is_locked": True}

    def detach_board_from_slot(self, owner_id: int, slot: int = 1, request_id: str = "") -> bool:
        slot_num = int(slot or 1)
        with self._lock:
            data = self._load()
            existing = next(
                (item for item in data["xiaozhi_tokens"] if int(item.get("user_id", 0)) == int(owner_id) and int(item.get("slot_number", 1)) == slot_num),
                None,
            )
            if not existing or not existing.get("board_mac"):
                return False
            old_mac = normalize_mac_address(existing.get("board_mac", ""))
            existing["board_mac"] = ""
            existing["updated_at"] = utc_now()
            if old_mac:
                for dev in data.get("registered_devices", []):
                    d_mac = normalize_mac_address(dev.get("device_id", "") or dev.get("mac_address", ""))
                    if d_mac and d_mac.lower() == old_mac.lower() and int(dev.get("owner_id", 0)) == int(owner_id):
                        dev["owner_id"] = None
                        dev["status"] = "DETACHED"
                        dev["last_active_at"] = utc_now()
            self._commit(data, f"Detach board from slot {slot_num}")
        return True

    def update_slot_label(self, owner_id: int, slot: int = 1, device_label: str = "", request_id: str = "") -> bool:
        slot_num = int(slot or 1)
        label_clean = (device_label or "").strip()[:60] or f"Slot {slot_num}"
        with self._lock:
            data = self._load()
            existing = next(
                (item for item in data["xiaozhi_tokens"] if int(item.get("user_id", 0)) == int(owner_id) and int(item.get("slot_number", 1)) == slot_num),
                None,
            )
            if not existing:
                return False
            existing["device_label"] = label_clean
            existing["updated_at"] = utc_now()
            self._commit(data, f"Update slot {slot_num} label")
        return True

    def get_xiaozhi_token(self, owner_id: int, slot: Optional[int] = None) -> Optional[str]:
        with self._lock:
            data = self._load()
            if slot is not None:
                existing = next(
                    (item for item in data["xiaozhi_tokens"] if int(item.get("user_id", 0)) == int(owner_id) and int(item.get("slot_number", 1)) == int(slot)),
                    None,
                )
            else:
                user_tokens = [item for item in data["xiaozhi_tokens"] if int(item.get("user_id", 0)) == int(owner_id)]
                user_tokens.sort(key=lambda x: int(x.get("slot_number", 1)))
                existing = user_tokens[0] if user_tokens else None
            if not existing:
                return None
            return decrypt_secret(existing.get("token_ciphertext", ""))

    def get_xiaozhi_token_info(self, owner_id: int, slot: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            if slot is not None:
                existing = next(
                    (item for item in data["xiaozhi_tokens"] if int(item.get("user_id", 0)) == int(owner_id) and int(item.get("slot_number", 1)) == int(slot)),
                    None,
                )
            else:
                user_tokens = [item for item in data["xiaozhi_tokens"] if int(item.get("user_id", 0)) == int(owner_id)]
                user_tokens.sort(key=lambda x: int(x.get("slot_number", 1)))
                existing = user_tokens[0] if user_tokens else None
            if not existing:
                return None
            token = decrypt_secret(existing.get("token_ciphertext", ""))
            if not token:
                return None
            token_hash = normalize_token_hash(existing.get("token_hash", "")) or xiaozhi_token_hash(token)
            slot_num = int(existing.get("slot_number", 1) or 1)
            board_mac = (existing.get("board_mac") or "").strip().upper()
            return {
                "user_id": int(owner_id),
                "slot_number": slot_num,
                "device_label": existing.get("device_label", f"XiaoZhi {slot_num}") or f"XiaoZhi {slot_num}",
                "board_mac": board_mac,
                "is_locked": bool(board_mac),
                "token": token,
                "token_hash": token_hash,
                "preview": f"{token[:4]}...{token[-4:]}" if len(token) > 8 else token,
                "created_at": existing.get("created_at", ""),
                "updated_at": existing.get("updated_at", ""),
            }

    def list_user_xiaozhi_tokens(self, owner_id: int) -> List[Dict[str, Any]]:
        """List all active token slots (up to 3) for a specific user."""
        with self._lock:
            data = self._load()
            tokens = []
            for item in data.get("xiaozhi_tokens", []):
                if int(item.get("user_id", 0)) == int(owner_id):
                    token = decrypt_secret(item.get("token_ciphertext", ""))
                    slot_num = int(item.get("slot_number", 1) or 1)
                    token_hash = normalize_token_hash(item.get("token_hash", "")) or xiaozhi_token_hash(token)
                    board_mac = (item.get("board_mac") or "").strip().upper()
                    tokens.append({
                        "slot_number": slot_num,
                        "device_label": item.get("device_label", f"XiaoZhi {slot_num}") or f"XiaoZhi {slot_num}",
                        "board_mac": board_mac,
                        "is_locked": bool(board_mac),
                        "preview": f"{token[:4]}...{token[-4:]}" if token and len(token) > 8 else token,
                        "token_hash": token_hash,
                        "created_at": item.get("created_at", ""),
                        "updated_at": item.get("updated_at", ""),
                    })
            tokens.sort(key=lambda x: x["slot_number"])
            return tokens

    def delete_xiaozhi_token(self, owner_id: int, slot: Optional[int] = None, request_id: str = "") -> bool:
        with self._lock:
            data = self._load()
            before = len(data["xiaozhi_tokens"])
            detached_macs = []
            if slot is not None:
                for item in data["xiaozhi_tokens"]:
                    if int(item.get("user_id", 0)) == int(owner_id) and int(item.get("slot_number", 1)) == int(slot):
                        if item.get("board_mac"):
                            detached_macs.append(normalize_mac_address(item["board_mac"]))
                data["xiaozhi_tokens"] = [
                    item for item in data["xiaozhi_tokens"]
                    if not (int(item.get("user_id", 0)) == int(owner_id) and int(item.get("slot_number", 1)) == int(slot))
                ]
            else:
                for item in data["xiaozhi_tokens"]:
                    if int(item.get("user_id", 0)) == int(owner_id) and item.get("board_mac"):
                        detached_macs.append(normalize_mac_address(item["board_mac"]))
                data["xiaozhi_tokens"] = [
                    item for item in data["xiaozhi_tokens"]
                    if int(item.get("user_id", 0)) != int(owner_id)
                ]
            for b_mac in detached_macs:
                if b_mac:
                    for dev in data.get("registered_devices", []):
                        d_mac = normalize_mac_address(dev.get("device_id", "") or dev.get("mac_address", ""))
                        if d_mac and d_mac.lower() == b_mac.lower() and int(dev.get("owner_id", 0)) == int(owner_id):
                            dev["owner_id"] = None
                            dev["status"] = "DETACHED"
                            dev["last_active_at"] = utc_now()
            changed = len(data["xiaozhi_tokens"]) != before
            if changed:
                self._commit(data, "Delete Xiaozhi token")
        remaining = any(int(item.get("user_id", 0)) == int(owner_id) for item in data.get("xiaozhi_tokens", []))
        if not remaining:
            try:
                self.detach_user_devices(int(owner_id), reason="Semua slot MCP Xiaozhi diputus / dihapus")
            except Exception as exc:
                logger.warning("Gagal detach devices memory user %s: %s", owner_id, exc)
        return changed

    def find_user_by_mcp_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Find user by MCP token hash. Returns user info if found."""
        token = str(token or "").strip()
        if not token or len(token) < 10:
            return None
        token_hash = xiaozhi_token_hash(token)
        with self._lock:
            data = self._load()
            for item in data.get("xiaozhi_tokens", []):
                stored_hash = normalize_token_hash(item.get("token_hash", ""))
                if stored_hash and stored_hash == token_hash:
                    user_id = int(item.get("user_id", 0))
                    user = next(
                        (u for u in data["users"] if int(u.get("id", 0)) == user_id),
                        None,
                    )
                    if user:
                        return {
                            "user_id": user_id,
                            "slot_number": int(item.get("slot_number", 1) or 1),
                            "device_label": item.get("device_label", "XiaoZhi 1"),
                            "username": user.get("username", ""),
                            "role": user.get("role", "user"),
                            "created_at": item.get("created_at", ""),
                            "updated_at": item.get("updated_at", ""),
                        }
        return None

    def delete_xiaozhi_token_by_hash(self, token: str) -> bool:
        """Delete MCP token by matching hash."""
        token = str(token or "").strip()
        if not token:
            return False
        token_hash = xiaozhi_token_hash(token)
        with self._lock:
            data = self._load()
            before = len(data.get("xiaozhi_tokens", []))
            data["xiaozhi_tokens"] = [
                item for item in data.get("xiaozhi_tokens", [])
                if normalize_token_hash(item.get("token_hash", "")) != token_hash
            ]
            changed = len(data["xiaozhi_tokens"]) != before
            if changed:
                self._commit(data, "Delete MCP token by lookup")
            return changed

    def list_xiaozhi_tokens(self) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            tokens = []
            for item in data.get("xiaozhi_tokens", []):
                token = decrypt_secret(item.get("token_ciphertext", ""))
                if token:
                    token_hash = normalize_token_hash(item.get("token_hash", "")) or xiaozhi_token_hash(token)
                    slot_num = int(item.get("slot_number", 1) or 1)
                    tokens.append({
                        "user_id": int(item.get("user_id", 0)),
                        "slot_number": slot_num,
                        "device_label": item.get("device_label", f"XiaoZhi {slot_num}") or f"XiaoZhi {slot_num}",
                        "token": token,
                        "token_hash": token_hash,
                    })
            return tokens

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
        **kwargs,
    ) -> None:
        user_message = clamp_text_bytes(user_message) if user_message else ""
        xiaozhi_answer = clamp_text_bytes(xiaozhi_answer) if xiaozhi_answer else ""
        token_hash = normalize_token_hash(token_hash)
        row = {
            "owner_id": int(owner_id),
            "token_hash": token_hash,
            "source": clean_text(source or "mcp_tool", max_len=40, min_len=2, field="Sumber riwayat"),
            "tool_name": clean_text(tool_name or "unknown", max_len=80, min_len=2, field="Nama tool"),
            "user_message": user_message,
            "xiaozhi_answer": xiaozhi_answer,
            "request_payload": serialize_history_value(request_payload),
            "response_payload": serialize_history_value(response_payload),
            "slot_number": int(slot_number or 1),
            "device_mac": (device_mac or "").strip().upper(),
            "request_id": (request_id or "").strip(),
            "created_at": utc_now(),
        }
        with self._lock:
            data = self._load()
            row["id"] = self._next_id(data, "chat_history")
            data["chat_history"].append(row)
            self._commit(data, "Add Xiaozhi chat history")

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
        **kwargs,
    ) -> None:
        user_message = clamp_text_bytes(user_message) if user_message else ""
        xiaozhi_answer = clamp_text_bytes(xiaozhi_answer) if xiaozhi_answer else ""
        token_hash = normalize_token_hash(token_hash)
        slot_num = int(slot_number or 1)
        clean_mac = (device_mac or "").strip().upper()
        clean_req_id = (request_id or "").strip()
        with self._lock:
            data = self._load()
            if xiaozhi_answer and not user_message:
                for item in reversed(data["chat_history"]):
                    item_slot = int(item.get("slot_number", 1) or 1)
                    slot_matches = (item_slot in (0, 1)) if slot_num == 1 else (item_slot == slot_num)
                    if (
                        int(item.get("owner_id", 0)) == int(owner_id)
                        and item.get("source") == "chat_transcript"
                        and slot_matches
                        and item.get("user_message")
                        and not item.get("xiaozhi_answer")
                    ):
                        item["xiaozhi_answer"] = xiaozhi_answer
                        if token_hash:
                            item["token_hash"] = token_hash
                        item["slot_number"] = slot_num
                        if clean_mac:
                            item["device_mac"] = clean_mac
                        if clean_req_id:
                            item["request_id"] = clean_req_id
                        item["response_payload"] = serialize_history_value(response_payload if response_payload is not None else payload)
                        self._commit(data, "Update Xiaozhi chat transcript")
                        return
            row = {
                "id": self._next_id(data, "chat_history"),
                "owner_id": int(owner_id),
                "token_hash": token_hash,
                "source": "chat_transcript",
                "tool_name": clean_text(tool_name or "xiaozhi_ws", max_len=80, min_len=2, field="Nama tool"),
                "user_message": user_message,
                "xiaozhi_answer": xiaozhi_answer,
                "request_payload": serialize_history_value(payload if user_message else {}),
                "response_payload": serialize_history_value(response_payload if response_payload is not None else (payload if xiaozhi_answer else {})),
                "slot_number": slot_num,
                "device_mac": clean_mac,
                "request_id": clean_req_id,
                "created_at": utc_now(),
            }
            data["chat_history"].append(row)
            self._commit(data, "Add Xiaozhi chat transcript")

    def list_chat_history(
        self,
        owner_id: int,
        query: str = "",
        limit: int = CHAT_HISTORY_DEFAULT_LIMIT,
        token_hash: str = "",
        semantic: bool = False,
        date: str = "",
        slot_number: Optional[int] = None,
        device_mac: str = "",
        **kwargs,
    ) -> List[Dict[str, Any]]:
        query = clean_text(query, max_len=120, field="Pencarian riwayat") if query else ""
        limit = max(1, min(int(limit or CHAT_HISTORY_DEFAULT_LIMIT), 300))
        token_hash = normalize_token_hash(token_hash)
        clean_mac = (device_mac or "").strip().upper()
        with self._lock:
            data = self._load()
            rows = []
            for item in data.get("chat_history", []):
                if int(item.get("owner_id", 0)) != int(owner_id):
                    continue
                if slot_number is not None:
                    item_slot = int(item.get("slot_number", 1) or 1)
                    if int(slot_number) == 1:
                        if item_slot not in (0, 1):
                            continue
                    else:
                        if item_slot != int(slot_number):
                            continue
                elif token_hash:
                    if not history_matches_token(item, token_hash):
                        continue
                if clean_mac and item.get("device_mac", "").upper() != clean_mac:
                    continue
                rows.append(dict(item))

        if date:
            rows = [
                item for item in rows
                if (item.get("created_at", "") or "")[:10] == date
            ]

        if semantic and query.strip():
            try:
                from xiaozhi.services.semantic_memory_service import rank_chat_history_semantically
                ranked = rank_chat_history_semantically(query.strip(), rows, top_k=limit)
                for item in ranked:
                    item["token_hash"] = normalize_token_hash(item.get("token_hash", ""))
                    item["request_size_label"] = format_size_mb(utf8_size(item.get("request_payload", "")))
                    item["response_size_label"] = format_size_mb(utf8_size(item.get("response_payload", "")))
                return ranked
            except Exception as e:
                logger.warning("Fallback semantic search in store.py: %s", e)

        if query:
            term = query.lower()
            rows = [
                item
                for item in rows
                if term
                in " ".join(
                    [
                        item.get("tool_name", ""),
                        item.get("source", ""),
                        item.get("user_message", ""),
                        item.get("xiaozhi_answer", ""),
                        item.get("request_payload", ""),
                        item.get("response_payload", ""),
                    ]
                ).lower()
            ]
        rows = sorted(rows, key=lambda item: int(item.get("id", 0)), reverse=True)[:limit]
        for item in rows:
            item["token_hash"] = normalize_token_hash(item.get("token_hash", ""))
            item["request_size_label"] = format_size_mb(utf8_size(item.get("request_payload", "")))
            item["response_size_label"] = format_size_mb(utf8_size(item.get("response_payload", "")))
        return rows

    def chat_history_dates(self, owner_id: int, token_hash: str = "", slot_number: Optional[int] = None, **kwargs) -> List[Dict[str, Any]]:
        """Ambil daftar tanggal yang punya chat, dengan jumlah per hari."""
        token_hash = normalize_token_hash(token_hash)
        with self._lock:
            data = self._load()
            rows = []
            for item in data.get("chat_history", []):
                if int(item.get("owner_id", 0)) != int(owner_id):
                    continue
                if slot_number is not None:
                    item_slot = int(item.get("slot_number", 1) or 1)
                    if int(slot_number) == 1:
                        if item_slot not in (0, 1):
                            continue
                    else:
                        if item_slot != int(slot_number):
                            continue
                elif token_hash:
                    if not history_matches_token(item, token_hash):
                        continue
                rows.append(item)
        date_counts: Dict[str, int] = {}
        for item in rows:
            d = (item.get("created_at", "") or "")[:10]
            if d:
                date_counts[d] = date_counts.get(d, 0) + 1
        return sorted(
            [{"date": d, "count": c} for d, c in date_counts.items()],
            key=lambda x: x["date"],
            reverse=True,
        )[:60]

    def chat_history_stats(self, owner_id: int, token_hash: str = "", date: str = "", slot_number: Optional[int] = None, **kwargs) -> Dict[str, int]:
        token_hash = normalize_token_hash(token_hash)
        with self._lock:
            data = self._load()
            rows = []
            for item in data.get("chat_history", []):
                if int(item.get("owner_id", 0)) != int(owner_id):
                    continue
                if slot_number is not None:
                    item_slot = int(item.get("slot_number", 1) or 1)
                    if int(slot_number) == 1:
                        if item_slot not in (0, 1):
                            continue
                    else:
                        if item_slot != int(slot_number):
                            continue
                elif token_hash:
                    if not history_matches_token(item, token_hash):
                        continue
                rows.append(item)
        if date:
            rows = [item for item in rows if (item.get("created_at", "") or "")[:10] == date]
        return {
            "total": len(rows),
            "transcript": sum(1 for item in rows if item.get("source") == "chat_transcript"),
            "tool": sum(1 for item in rows if item.get("source") == "mcp_tool"),
        }

    def clear_chat_history(self, owner_id: int) -> int:
        with self._lock:
            data = self._load()
            before = len(data.get("chat_history", []))
            data["chat_history"] = [
                item
                for item in data.get("chat_history", [])
                if int(item.get("owner_id", 0)) != int(owner_id)
            ]
            removed = before - len(data["chat_history"])
            if removed:
                self._commit(data, "Clear Xiaozhi chat history")
            return removed

    def delete_chat_history_item(self, owner_id: int, chat_id: int) -> bool:
        with self._lock:
            data = self._load()
            before = len(data.get("chat_history", []))
            data["chat_history"] = [
                item for item in data.get("chat_history", [])
                if not (int(item.get("owner_id", 0)) == int(owner_id) and int(item.get("id", 0)) == int(chat_id))
            ]
            removed = before - len(data["chat_history"])
            if removed:
                self._commit(data, "Delete chat history item")
            return removed > 0

    def get_today_users_activity(self, today_date: str = "") -> Dict[int, Dict[str, Any]]:
        """Ambil ringkasan aktivitas user hari ini (stream youtube, mcp tools yang terpanggil)."""
        if not today_date:
            from datetime import datetime, timezone, timedelta
            wib = timezone(timedelta(hours=7))
            today_date = datetime.now(wib).strftime("%Y-%m-%d")
        data = self._load()
        activity: Dict[int, Dict[str, Any]] = {}
        for r in data.get("chat_history", []):
            created_at = str(r.get("created_at", ""))
            if not (created_at.startswith(today_date)):
                continue
            uid = int(r.get("owner_id", 0))
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
            if created_at and created_at > activity[uid].get("last_activity_raw", ""):
                activity[uid]["last_activity_raw"] = created_at
                activity[uid]["last_activity_time"] = created_at[11:16] if len(created_at) >= 16 else created_at
            if not activity[uid]["last_message_preview"]:
                msg = str(r.get("user_message") or r.get("xiaozhi_answer") or "")
                if msg:
                    activity[uid]["last_message_preview"] = msg[:60]

        for a in data.get("audio_queue", []):
            created_at = str(a.get("created_at") or "")
            if not (created_at.startswith(today_date)):
                continue
            uid = int(a.get("owner_id", 0))
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
            if created_at and created_at > activity[uid].get("last_activity_raw", ""):
                activity[uid]["last_activity_raw"] = created_at
                activity[uid]["last_activity_time"] = created_at[11:16] if len(created_at) >= 16 else created_at

        return activity

    def get_daily_activity_monitor(
        self,
        target_date: str = "",
        owner_id: Optional[int] = None,
        activity_type: str = "all",
        search_query: str = "",
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Ambil detail aktivitas per hari (alat/tool yang dipanggil, lagu youtube yang diputar) di Base Store."""
        import re
        from datetime import datetime, timezone, timedelta

        wib = timezone(timedelta(hours=7))
        now_wib = datetime.now(wib)
        if not target_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(target_date).strip()):
            target_date = now_wib.strftime("%Y-%m-%d")
        else:
            target_date = str(target_date).strip()

        data = self._load()
        users_map = {int(u["id"]): dict(u) for u in data.get("users", [])}
        device_map: Dict[Any, str] = {}
        user_devices_map: Dict[int, List[str]] = {}
        for d in data.get("registered_devices", []):
            uid = int(d["owner_id"]) if d.get("owner_id") is not None else None
            mac = str(d.get("device_id") or "").strip().upper()
            name = d.get("device_name") or d.get("device_type") or mac
            if uid is not None:
                device_map[(uid, mac)] = name
                if uid not in user_devices_map:
                    user_devices_map[uid] = []
                display_dev = f"{name} ({mac})" if mac and mac not in name else name
                if display_dev not in user_devices_map[uid]:
                    user_devices_map[uid].append(display_dev)
            if mac:
                device_map[mac] = name

        music_items: List[Dict[str, Any]] = []
        for a in data.get("audio_queue", []):
            created_at = str(a.get("created_at") or "")
            if not created_at.startswith(target_date):
                continue
            uid = int(a.get("owner_id") or 0)
            if owner_id and uid != int(owner_id):
                continue
            title = str(a.get("title") or "")
            vid = str(a.get("video_id") or "")
            if search_query and search_query.lower() not in title.lower() and search_query.lower() not in vid.lower():
                continue
            u = users_map.get(uid, {})
            stream_url = str(a.get("stream_url") or "")
            mac_match = re.search(r"[?&]mac=([0-9a-fA-F:_-]+)", stream_url)
            mac_str = mac_match.group(1).upper() if mac_match else ""
            dev_name = device_map.get((uid, mac_str)) or device_map.get(mac_str) or (mac_str if mac_str else "-")

            music_items.append({
                "id": f"music_{a.get('id', 0)}",
                "raw_id": a.get("id", 0),
                "category": "music",
                "type": "music_stream",
                "type_label": "Pemutaran Lagu",
                "badge_color": "emerald",
                "owner_id": uid,
                "username": u.get("username", f"user-{uid}"),
                "email": u.get("google_email", ""),
                "title": title or "Lagu Tanpa Judul",
                "video_id": vid,
                "video_url": a.get("video_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else ""),
                "thumbnail_url": f"https://img.youtube.com/vi/{vid}/mqdefault.jpg" if vid else "",
                "stream_url": stream_url,
                "status": a.get("status") or "pending",
                "device_mac": mac_str,
                "device_name": dev_name,
                "time_wib": created_at,
                "time_short": created_at[11:19] if len(created_at) >= 19 else created_at,
                "epoch": 0.0,
            })

        tool_items: List[Dict[str, Any]] = []
        for c in data.get("chat_history", []):
            created_at = str(c.get("created_at") or "")
            if not created_at.startswith(target_date):
                continue
            tname = str(c.get("tool_name") or "").strip()
            source = str(c.get("source") or "").strip()
            if not tname and source != "mcp":
                continue
            uid = int(c.get("owner_id") or 0)
            if owner_id and uid != int(owner_id):
                continue
            umsg = str(c.get("user_message") or "")
            ans = str(c.get("xiaozhi_answer") or "")
            if search_query and search_query.lower() not in umsg.lower() and search_query.lower() not in tname.lower() and search_query.lower() not in ans.lower():
                continue
            u = users_map.get(uid, {})
            mac_str = str(c.get("device_mac") or "").strip().upper()
            dev_name = device_map.get((uid, mac_str)) or device_map.get(mac_str) or (mac_str if mac_str else "-")
            is_music_tool = any(m in tname.lower() for m in ["youtube", "playlist", "audio", "music", "playback"])

            tool_items.append({
                "id": f"tool_{c.get('id', 0)}",
                "raw_id": c.get("id", 0),
                "category": "tool",
                "type": "music_tool" if is_music_tool else "tool_call",
                "type_label": f"Tool: {tname}" if tname else "MCP Tool",
                "badge_color": "sky" if is_music_tool else "amber",
                "owner_id": uid,
                "username": u.get("username", f"user-{uid}"),
                "email": u.get("google_email", ""),
                "tool_name": tname,
                "source": source or "mcp",
                "user_message": umsg,
                "xiaozhi_answer": ans,
                "device_mac": mac_str,
                "device_name": dev_name,
                "time_wib": created_at,
                "time_short": created_at[11:19] if len(created_at) >= 19 else created_at,
                "epoch": 0.0,
            })

        if activity_type == "music":
            timeline = list(music_items)
        elif activity_type == "tools":
            timeline = list(tool_items)
        else:
            timeline = music_items + tool_items

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
                    "recent_songs": [],
                    "tools_called": {},
                }
            st = user_stats_dict[uid]
            st["music_count"] += 1
            st["total_events"] += 1
            if m["title"] not in st["recent_songs"] and len(st["recent_songs"]) < 5:
                st["recent_songs"].append(m["title"])
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
                    "recent_songs": [],
                    "tools_called": {},
                }
            st = user_stats_dict[uid]
            st["tools_count"] += 1
            st["total_events"] += 1
            tname = t["tool_name"] or "mcp"
            st["tools_called"][tname] = st["tools_called"].get(tname, 0) + 1
            tool_popularity[tname] = tool_popularity.get(tname, 0) + 1

        user_summaries = list(user_stats_dict.values())
        user_summaries.sort(key=lambda u: u["total_events"], reverse=True)

        return {
            "target_date": target_date,
            "target_date_formatted": target_date,
            "totals": {
                "total_activities": len(timeline),
                "total_music": len(music_items),
                "total_tools": len(tool_items),
                "active_users": len(user_summaries),
            },
            "user_summaries": user_summaries,
            "timeline": timeline,
            "popular_tools": [{"tool": k, "count": v} for k, v in sorted(tool_popularity.items(), key=lambda x: x[1], reverse=True)[:10]],
            "popular_songs": [{"song": k, "count": v} for k, v in sorted(song_popularity.items(), key=lambda x: x[1], reverse=True)[:10]],
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
        now = utc_now()
        cat_clean = str(category or "informasi_pribadi").strip().lower()
        key_clean = str(preference_key or "").strip()
        val_clean = str(preference_value or "").strip()
        conf_val = max(0.1, min(float(confidence or 1.0), 1.0))

        with self._lock:
            data = self._load()
            persona_list = data.setdefault("user_persona", [])
            existing = None
            for p in persona_list:
                if int(p.get("owner_id", 0)) == int(owner_id) and str(p.get("preference_key", "")).strip().lower() == key_clean.lower():
                    existing = p
                    break

            if existing:
                existing["category"] = cat_clean
                existing["preference_value"] = val_clean
                existing["confidence"] = conf_val
                existing["updated_at"] = now
            else:
                next_id = int(data.get("next_ids", {}).get("user_persona", 1))
                data.setdefault("next_ids", {})["user_persona"] = next_id + 1
                persona_list.append({
                    "id": next_id,
                    "owner_id": int(owner_id),
                    "category": cat_clean,
                    "preference_key": key_clean,
                    "preference_value": val_clean,
                    "confidence": conf_val,
                    "created_at": now,
                    "updated_at": now,
                })
            self._commit(data, f"Save user persona {key_clean} for user {owner_id}")
            return {
                "success": True,
                "owner_id": int(owner_id),
                "category": cat_clean,
                "preference_key": key_clean,
                "preference_value": val_clean,
                "confidence": conf_val,
                "updated_at": now,
            }

    def get_user_persona(self, owner_id: int, category: str = "") -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            items = [
                dict(p)
                for p in data.get("user_persona", [])
                if int(p.get("owner_id", 0)) == int(owner_id)
            ]
        if category:
            cat_filter = category.strip().lower()
            items = [p for p in items if str(p.get("category", "")).lower() == cat_filter]
        return sorted(items, key=lambda x: str(x.get("updated_at", "")), reverse=True)

    def delete_user_preference(self, owner_id: int, preference_key: str) -> bool:
        key_target = preference_key.strip().lower()
        with self._lock:
            data = self._load()
            before = len(data.get("user_persona", []))
            data["user_persona"] = [
                p
                for p in data.get("user_persona", [])
                if not (int(p.get("owner_id", 0)) == int(owner_id) and str(p.get("preference_key", "")).strip().lower() == key_target)
            ]
            removed = before - len(data["user_persona"])
            if removed:
                self._commit(data, f"Delete user persona {preference_key} for user {owner_id}")
            return removed > 0

    def get_user_persona_analysis(self, owner_id: int) -> Dict[str, Any]:
        """
        Runs RAG & Vector Semantic profiling on the user's chat history & stored personas.
        """
        from xiaozhi.services.semantic_memory_service import analyze_user_persona_from_chats
        try:
            chats = self.list_chat_history(owner_id, limit=300)
            stored_personas = self.get_user_persona(owner_id)
            return analyze_user_persona_from_chats(chats, stored_personas)
        except Exception:
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

    def get_feature_settings(self, owner_id: int) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            existing = next(
                (item for item in data["feature_settings"] if int(item.get("user_id", 0)) == int(owner_id)),
                None,
            )
            if not existing:
                return {"virtual_smarthome_enabled": True}
            return {"virtual_smarthome_enabled": bool(existing.get("virtual_smarthome_enabled", True))}

    def set_virtual_smarthome_enabled(self, owner_id: int, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            existing = next(
                (item for item in data["feature_settings"] if int(item.get("user_id", 0)) == int(owner_id)),
                None,
            )
            if existing:
                existing["virtual_smarthome_enabled"] = bool(enabled)
                existing["updated_at"] = utc_now()
            else:
                data["feature_settings"].append(
                    {
                        "user_id": int(owner_id),
                        "virtual_smarthome_enabled": bool(enabled),
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
            self._commit(data, "Update feature settings")
        return self.get_feature_settings(owner_id)

    @staticmethod
    def _public_relay_room(item: Dict[str, Any], *, include_credentials: bool = False) -> Dict[str, Any]:
        api_slug = HFJsonStore._relay_room_slug(item)
        api_token = HFJsonStore._relay_api_token(item)
        device_last_seen_at = str(item.get("device_last_seen_at", "") or "")
        device_seen_age = seconds_since_iso(device_last_seen_at)
        device_connected = device_seen_age is not None and device_seen_age <= REAL_RELAY_DEVICE_ONLINE_SECONDS
        if device_connected:
            device_connection_label = "ESP32/8266 terhubung"
        elif device_last_seen_at:
            device_connection_label = "ESP32/8266 tidak terhubung"
        else:
            device_connection_label = "ESP32/8266 belum terhubung"
        pending_commands = [
            command
            for command in item.get("pending_commands", [])
            if not command.get("acknowledged_at")
        ]
        pending_by_relay = {
            int(command.get("relay_number", 0)): command
            for command in pending_commands
            if command.get("relay_number")
        }
        relays = []
        for relay in item.get("relays", []):
            relay_number = int(relay.get("relay_number", 0))
            try:
                status = normalize_relay_state(relay.get("status", "OFF"))
            except ValueError:
                status = "OFF"
            desired_status = str(relay.get("desired_status", "") or "").upper()
            if desired_status not in {"ON", "OFF"}:
                desired_status = status
            pending = pending_by_relay.get(relay_number)
            relays.append(
                {
                    "id": int(relay.get("id", relay.get("relay_number", 0))),
                    "relay_room_id": int(item.get("id", 0)),
                    "relay_number": relay_number,
                    "nama_relay": relay.get("nama_relay", ""),
                    "voice_command_on": relay.get("voice_command_on", ""),
                    "voice_command_off": relay.get("voice_command_off", ""),
                    "status": status,
                    "desired_status": desired_status,
                    "pending_command_id": str((pending or {}).get("id") or relay.get("pending_command_id") or ""),
                    "pending_command": str((pending or {}).get("command") or ""),
                    "pending_since": str((pending or {}).get("created_at") or ""),
                    "last_seen_at": relay.get("last_seen_at", ""),
                    "created_at": relay.get("created_at", ""),
                    "updated_at": relay.get("updated_at", ""),
                }
            )
        room = {
            "id": int(item.get("id", 0)),
            "nama_tempat": item.get("nama_tempat", ""),
            "api_slug": api_slug,
            "api_base_path": f"/api/device/relay/{api_slug}",
            "api_commands_path": f"/api/device/relay/{api_slug}/commands",
            "api_status_path": f"/api/device/relay/{api_slug}/status",
            "api_client_id": normalize_relay_client_id(item.get("api_client_id") or item.get("mqtt_client_id"), api_slug),
            "api_token_masked": mask_secret(api_token),
            "jumlah_relay": int(item.get("jumlah_relay", len(relays))),
            "relays": sorted(relays, key=lambda relay: relay["relay_number"]),
            "pending_count": len(pending_commands),
            "device_last_seen_at": device_last_seen_at,
            "device_connected": device_connected,
            "device_connection_label": device_connection_label,
            "device_seen_age_seconds": int(device_seen_age) if device_seen_age is not None else None,
            "device_online_window_seconds": REAL_RELAY_DEVICE_ONLINE_SECONDS,
            "last_command_at": item.get("last_command_at", ""),
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
        }
        if include_credentials:
            room["api_token"] = api_token
        return room

    @staticmethod
    def _relay_room_slug(item: Dict[str, Any]) -> str:
        if item.get("api_slug"):
            return slugify_topic_part(str(item.get("api_slug", "")))
        topic = str(item.get("mqtt_topic", "") or "").strip("/")
        if topic:
            tail = topic.split("/")[-1]
            if tail:
                return slugify_topic_part(tail)
        return slugify_topic_part(str(item.get("nama_tempat", "relay")))

    @staticmethod
    def _relay_api_token(item: Dict[str, Any]) -> str:
        return (
            decrypt_secret(item.get("api_token_ciphertext", ""))
            or decrypt_secret(item.get("mqtt_token_ciphertext", ""))
            or ""
        )

    def list_real_relay_rooms(self, owner_id: int, *, include_credentials: bool = False) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            rows = [
                dict(item)
                for item in data["relay_rooms"]
                if int(item.get("owner_id", 0)) == int(owner_id)
            ]
        return [
            self._public_relay_room(item, include_credentials=include_credentials)
            for item in sorted(rows, key=lambda row: int(row.get("id", 0)), reverse=True)
        ]

    def get_real_relay_room(
        self,
        owner_id: int,
        room_id: int,
        *,
        include_credentials: bool = False,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            item = next(
                (
                    dict(row)
                    for row in data["relay_rooms"]
                    if int(row.get("id", 0)) == int(room_id)
                    and int(row.get("owner_id", 0)) == int(owner_id)
                ),
                None,
            )
        if not item:
            return None
        return self._public_relay_room(item, include_credentials=include_credentials)

    def _validate_relay_room_payload(
        self,
        data: Dict[str, Any],
        owner_id: int,
        room_id: Optional[int],
        nama_tempat: str,
        jumlah_relay: int,
        relays: List[Dict[str, Any]],
        api_slug: str,
    ) -> None:
        slug = slugify_topic_part(nama_tempat)
        if api_slug != slug:
            raise ValueError("Kode API perangkat harus sesuai nama tempat.")
        for item in data["relay_rooms"]:
            if int(item.get("owner_id", 0)) != int(owner_id):
                continue
            if room_id is not None and int(item.get("id", 0)) == int(room_id):
                continue
            if item.get("nama_tempat", "").lower() == nama_tempat.lower():
                raise ValueError("Gagal menyimpan Relay Nyata. Nama tempat / ruangan sudah digunakan. Gunakan nama lain.")
            if self._relay_room_slug(item).lower() == api_slug.lower():
                raise ValueError("Gagal menyimpan Relay Nyata. Kode API perangkat sudah digunakan. Gunakan nama tempat lain.")

        commands: Dict[str, str] = {}
        for item in data["relay_rooms"]:
            if int(item.get("owner_id", 0)) != int(owner_id):
                continue
            if room_id is not None and int(item.get("id", 0)) == int(room_id):
                continue
            for relay in item.get("relays", []):
                for key in ("voice_command_on", "voice_command_off"):
                    command = normalize_voice_command(relay.get(key, ""))
                    if command:
                        commands[command] = f"{item.get('nama_tempat', 'ruangan')} / {relay.get('nama_relay', 'relay')}"

        seen_current: Dict[str, str] = {}
        for relay in relays:
            for key in ("voice_command_on", "voice_command_off"):
                command = normalize_voice_command(relay.get(key, ""))
                if command in seen_current:
                    raise ValueError(
                        f"Gagal menyimpan Relay Nyata. Perintah suara '{command}' sudah dipakai di ruangan ini pada {seen_current[command]}."
                    )
                if command in commands:
                    raise ValueError(
                        f"Gagal menyimpan Relay Nyata. Perintah suara '{command}' sudah dipakai di {commands[command]}."
                    )
                seen_current[command] = relay.get("nama_relay", "relay")

        if len(relays) != jumlah_relay:
            raise ValueError("Jumlah detail relay tidak sesuai jumlah relay.")

    def save_real_relay_room(
        self,
        owner_id: int,
        *,
        nama_tempat: str,
        jumlah_relay: int,
        relays: List[Dict[str, Any]],
        api_slug: str,
        api_token: str,
        api_client_id: str,
        room_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        nama_tempat = clean_text(nama_tempat, max_len=80, min_len=2, field="Nama tempat")
        jumlah_relay = parse_int_range(jumlah_relay, field="Jumlah relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        api_slug = slugify_topic_part(nama_tempat)
        clean_relays = []
        for index in range(1, jumlah_relay + 1):
            relay = relays[index - 1] if index - 1 < len(relays) else {}
            try:
                status = normalize_relay_state(relay.get("status", "OFF"))
            except ValueError:
                status = "OFF"
            clean_relays.append(
                {
                    "id": int(relay.get("id") or index),
                    "relay_number": index,
                    "nama_relay": clean_text(relay.get("nama_relay", ""), max_len=80, min_len=2, field=f"Nama relay {index}"),
                    "voice_command_on": clean_text(relay.get("voice_command_on", ""), max_len=120, min_len=3, field=f"Perintah ON relay {index}"),
                    "voice_command_off": clean_text(relay.get("voice_command_off", ""), max_len=120, min_len=3, field=f"Perintah OFF relay {index}"),
                    "status": status,
                    "desired_status": status,
                    "pending_command_id": "",
                    "last_seen_at": relay.get("last_seen_at", ""),
                    "created_at": relay.get("created_at") or utc_now(),
                    "updated_at": utc_now(),
                }
            )

        with self._lock:
            data = self._load()
            existing_item = None
            if room_id is not None:
                existing_item = next(
                    (
                        item
                        for item in data["relay_rooms"]
                        if int(item.get("id", 0)) == int(room_id)
                        and int(item.get("owner_id", 0)) == int(owner_id)
                    ),
                    None,
                )
            if not api_token and existing_item:
                api_token = self._relay_api_token(existing_item)
            if not api_client_id and existing_item:
                api_client_id = existing_item.get("api_client_id") or existing_item.get("mqtt_client_id") or ""
            api_token = clean_text(api_token or secrets.token_urlsafe(24), max_len=200, min_len=12, field="Token API ESP32/8266 dll")
            api_client_id = clean_text(
                normalize_relay_client_id(api_client_id, f"{api_slug}-{secrets.token_hex(3)}"),
                max_len=120,
                min_len=4,
                field="Client ID perangkat",
            )
            self._validate_relay_room_payload(data, owner_id, room_id, nama_tempat, jumlah_relay, clean_relays, api_slug)
            if room_id is None:
                self._enforce_relay_room_limit_unlocked(data, owner_id, additional=1)
            if room_id is not None:
                for item in data["relay_rooms"]:
                    if int(item.get("id", 0)) == int(room_id) and int(item.get("owner_id", 0)) == int(owner_id):
                        previous_by_number = {
                            int(relay.get("relay_number", 0)): relay
                            for relay in item.get("relays", [])
                        }
                        for relay in clean_relays:
                            previous = previous_by_number.get(int(relay["relay_number"]))
                            if previous:
                                relay["id"] = int(previous.get("id", relay["relay_number"]))
                                relay["created_at"] = previous.get("created_at", relay["created_at"])
                                try:
                                    relay["status"] = normalize_relay_state(previous.get("status", relay["status"]))
                                except ValueError:
                                    relay["status"] = "OFF"
                                relay["desired_status"] = previous.get("desired_status", relay["status"])
                                relay["pending_command_id"] = previous.get("pending_command_id", "")
                                relay["last_seen_at"] = previous.get("last_seen_at", "")
                        active_numbers = {int(relay["relay_number"]) for relay in clean_relays}
                        pending_commands = [
                            command
                            for command in item.get("pending_commands", [])
                            if int(command.get("relay_number", 0)) in active_numbers and not command.get("acknowledged_at")
                        ]
                        item.update(
                            {
                                "nama_tempat": nama_tempat,
                                "api_slug": api_slug,
                                "api_client_id": api_client_id,
                                "api_token_ciphertext": encrypt_secret(api_token),
                                "jumlah_relay": jumlah_relay,
                                "relays": clean_relays,
                                "pending_commands": pending_commands,
                                "updated_at": utc_now(),
                            }
                        )
                        self._commit(data, "Update real relay room")
                        return self._public_relay_room(item, include_credentials=False)
                raise ValueError("Data relay tidak ditemukan atau bukan milik akun ini.")

            row = {
                "id": self._next_id(data, "relay_rooms"),
                "owner_id": int(owner_id),
                "nama_tempat": nama_tempat,
                "api_slug": api_slug,
                "api_client_id": api_client_id,
                "api_token_ciphertext": encrypt_secret(api_token),
                "jumlah_relay": jumlah_relay,
                "relays": clean_relays,
                "pending_commands": [],
                "device_last_seen_at": "",
                "last_command_at": "",
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            data["relay_rooms"].append(row)
            self._commit(data, "Add real relay room")
            return self._public_relay_room(row, include_credentials=False)

    def delete_real_relay_room(self, owner_id: int, room_id: int) -> bool:
        with self._lock:
            data = self._load()
            before = len(data["relay_rooms"])
            data["relay_rooms"] = [
                item
                for item in data["relay_rooms"]
                if not (int(item.get("id", 0)) == int(room_id) and int(item.get("owner_id", 0)) == int(owner_id))
            ]
            changed = len(data["relay_rooms"]) != before
            if changed:
                self._commit(data, "Delete real relay room")
            return changed

    def clear_real_relay_pending_commands(self, owner_id: int) -> int:
        cleared = 0
        with self._lock:
            data = self._load()
            for item in data["relay_rooms"]:
                if int(item.get("owner_id", 0)) != int(owner_id):
                    continue
                pending_commands = [
                    command
                    for command in item.get("pending_commands", [])
                    if not command.get("acknowledged_at")
                ]
                if not pending_commands:
                    continue
                cleared += len(pending_commands)
                item["pending_commands"] = []
                now = utc_now()
                for relay in item.get("relays", []):
                    try:
                        status = normalize_relay_state(relay.get("status", "OFF"))
                    except ValueError:
                        status = "OFF"
                    relay["desired_status"] = status
                    relay["pending_command_id"] = ""
                    relay["updated_at"] = now
                item["updated_at"] = now
            if cleared:
                self._commit(data, "Clear real relay pending commands")
        return cleared

    def queue_audio_command(self, owner_id: int, title: str, stream_url: str, video_url: str = "", duration: str = "", video_id: str = "") -> Dict[str, Any]:
        title = clean_text(title, max_len=200, min_len=1, field="Judul lagu")
        stream_url = clean_text(stream_url, max_len=2000, min_len=1, field="Audio URL")
        with self._lock:
            data = self._load()
            now = utc_now()
            command_id = secrets.token_hex(8)
            pending_user = [
                c for c in data.get("audio_queue", [])
                if int(c.get("owner_id", 0)) == int(owner_id) and not c.get("played_at")
            ]
            while len(pending_user) >= 3:
                oldest = pending_user.pop(0)
                data["audio_queue"] = [c for c in data["audio_queue"] if c.get("id") != oldest.get("id")]
            command_row = {
                "id": command_id,
                "owner_id": int(owner_id),
                "title": title,
                "stream_url": stream_url,
                "video_url": video_url or "",
                "duration": duration or "",
                "video_id": video_id or "",
                "created_at": now,
                "delivered_at": "",
                "played_at": "",
            }
            data.setdefault("audio_queue", []).append(command_row)
            self._commit(data, "Queue audio command")
            return command_row

    def get_audio_commands(self, owner_id: int) -> list:
        with self._lock:
            data = self._load()
            commands = [
                c for c in data.get("audio_queue", [])
                if int(c.get("owner_id", 0)) == int(owner_id) and not c.get("played_at")
            ]
            now = utc_now()
            for cmd in commands:
                if not cmd.get("delivered_at"):
                    cmd["delivered_at"] = now
            if commands:
                self._commit(data, "Mark audio commands delivered")
            return commands

    def ack_audio_command(self, owner_id: int, command_id: str) -> bool:
        with self._lock:
            data = self._load()
            for cmd in data.get("audio_queue", []):
                if cmd.get("id") == command_id and int(cmd.get("owner_id", 0)) == int(owner_id):
                    cmd["played_at"] = utc_now()
                    self._commit(data, "Ack audio command")
                    return True
            return False

    def is_device_protected(self, device_id: str) -> bool:
        if not device_id:
            return False
        norm = normalize_mac_address(device_id)
        if norm in PROTECTED_DEVICE_MACS:
            return True
        with self._lock:
            data = self._load()
            for d in data.get("registered_devices", []):
                stored_mac = normalize_mac_address(d.get("mac_address", "") or d.get("device_id", ""))
                if stored_mac and stored_mac.lower() == norm.lower():
                    return bool(d.get("is_protected"))
        return False

    def register_device(self, owner_id: int, device_id: str = "", mac_address: str = "", name: str = "", device_name: str = "", device_type: str = "", notes: str = "") -> Dict[str, Any]:
        raw_mac = device_id or mac_address
        normalized_id = normalize_mac_address(raw_mac)
        if not normalized_id:
            raise ValueError("Device ID / MAC address diperlukan.")
        dev_name = clean_text(name or device_name or f"ESP32 ({normalized_id[-5:]})", max_len=80, min_len=1, field="Nama device")
        dev_type = str(device_type or "esp32").strip()
        is_protected = (normalized_id in PROTECTED_DEVICE_MACS)
        now = utc_now()
        hist_notes = (notes or "").strip() or "Tautan aktif (Device Registered)"

        with self._lock:
            data = self._load()
            data.setdefault("registered_devices", [])
            data.setdefault("board_binding_history", [])

            # Lookup username
            username = f"user_{owner_id}"
            for u in data.get("users", []):
                if int(u.get("id", 0)) == int(owner_id):
                    username = u.get("username", username)
                    break

            existing = None
            for d in data["registered_devices"]:
                stored_mac = normalize_mac_address(d.get("mac_address", "") or d.get("device_id", ""))
                if stored_mac and stored_mac.lower() == normalized_id.lower():
                    existing = d
                    break

            if existing:
                prev_owner = existing.get("owner_id")
                if prev_owner and int(prev_owner) != int(owner_id):
                    for h in data["board_binding_history"]:
                        if (normalize_mac_address(h.get("device_mac", "")).lower() == normalized_id.lower() 
                                and int(h.get("user_id", 0)) == int(prev_owner) 
                                and h.get("status") == "ACTIVE"):
                            h["status"] = "DETACHED"
                            h["unlinked_at"] = now
                            h["notes"] = f"Dialihkan ke user {username} (ID: {owner_id})"

                existing["owner_id"] = int(owner_id)
                existing["mac_address"] = normalized_id
                existing["device_id"] = normalized_id
                existing["device_name"] = dev_name
                existing["device_type"] = dev_type
                existing["is_protected"] = bool(existing.get("is_protected") or is_protected)
                existing["status"] = "ACTIVE"
                existing["last_active_at"] = now
                existing["last_seen_at"] = now
                dev_res = existing
            else:
                dev_res = {
                    "id": secrets.token_hex(8),
                    "owner_id": int(owner_id),
                    "mac_address": normalized_id,
                    "device_id": normalized_id,
                    "device_name": dev_name,
                    "device_type": dev_type,
                    "is_protected": is_protected,
                    "status": "ACTIVE",
                    "is_audio_player": True,
                    "created_at": now,
                    "last_active_at": now,
                    "last_seen_at": now,
                }
                data["registered_devices"].append(dev_res)

            # Record history
            hist_found = False
            for h in data["board_binding_history"]:
                if (normalize_mac_address(h.get("device_mac", "")).lower() == normalized_id.lower()
                        and int(h.get("user_id", 0)) == int(owner_id)
                        and h.get("status") == "ACTIVE"):
                    h["last_active_at"] = now
                    h["device_name"] = dev_name
                    h["device_type"] = dev_type
                    h["username"] = username
                    if (notes or "").strip():
                        h["notes"] = (notes or "").strip()
                    hist_found = True
                    break

            if not hist_found:
                data["board_binding_history"].append({
                    "id": len(data["board_binding_history"]) + 1,
                    "device_mac": normalized_id,
                    "user_id": int(owner_id),
                    "username": username,
                    "device_name": dev_name,
                    "device_type": dev_type,
                    "linked_at": now,
                    "last_active_at": now,
                    "unlinked_at": None,
                    "status": "ACTIVE",
                    "notes": hist_notes,
                    "created_at": now,
                })

            self._commit(data, "Register/rebind device and update history")
            return dev_res

    def detach_device(self, device_id: str, owner_id: Optional[int] = None, reason: str = "Tautan dipisahkan") -> bool:
        norm = normalize_mac_address(device_id)
        if not norm:
            return False
        now = utc_now()
        with self._lock:
            data = self._load()
            data.setdefault("registered_devices", [])
            data.setdefault("board_binding_history", [])
            found = False
            for d in data["registered_devices"]:
                sm = normalize_mac_address(d.get("mac_address", "") or d.get("device_id", ""))
                if sm and sm.lower() == norm.lower():
                    if owner_id is not None and d.get("owner_id") and int(d.get("owner_id")) != int(owner_id):
                        return False
                    d["owner_id"] = None
                    d["status"] = "DETACHED"
                    d["last_active_at"] = now
                    found = True
                    break
            if found:
                for h in data["board_binding_history"]:
                    if normalize_mac_address(h.get("device_mac", "")).lower() == norm.lower() and h.get("status") == "ACTIVE":
                        h["status"] = "DETACHED"
                        h["unlinked_at"] = now
                        h["notes"] = reason
                self._commit(data, "Detach device")
            return found

    def detach_user_devices(self, user_id: int, reason: str = "MCP diputuskan") -> List[str]:
        now = utc_now()
        detached = []
        with self._lock:
            data = self._load()
            data.setdefault("registered_devices", [])
            data.setdefault("board_binding_history", [])
            for d in data["registered_devices"]:
                if d.get("owner_id") and int(d.get("owner_id")) == int(user_id):
                    mac = normalize_mac_address(d.get("mac_address", "") or d.get("device_id", ""))
                    d["owner_id"] = None
                    d["status"] = "DETACHED"
                    d["last_active_at"] = now
                    detached.append(mac)
                    for h in data["board_binding_history"]:
                        if (normalize_mac_address(h.get("device_mac", "")).lower() == mac.lower()
                                and int(h.get("user_id", 0)) == int(user_id)
                                and h.get("status") == "ACTIVE"):
                            h["status"] = "DETACHED"
                            h["unlinked_at"] = now
                            h["notes"] = reason
            if detached:
                self._commit(data, "Detach user devices")
        return detached

    def record_device_activity(self, device_id: str, owner_id: Optional[int] = None) -> None:
        norm = normalize_mac_address(device_id)
        if not norm:
            return
        now = utc_now()
        with self._lock:
            data = self._load()
            for d in data.get("registered_devices", []):
                sm = normalize_mac_address(d.get("mac_address", "") or d.get("device_id", ""))
                if sm and sm.lower() == norm.lower():
                    d["last_active_at"] = now
                    d["last_seen_at"] = now
            for h in data.get("board_binding_history", []):
                if normalize_mac_address(h.get("device_mac", "")).lower() == norm.lower() and h.get("status") == "ACTIVE":
                    if not owner_id or int(h.get("user_id", 0)) == int(owner_id):
                        h["last_active_at"] = now
            self._commit(data, "Record device activity")

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

        with self._lock:
            data = self._load()
            rows = []
            norm_filter = normalize_mac_address(device_mac).lower() if device_mac else ""
            clean_filter = device_mac.replace(":", "").replace("-", "").strip().lower() if device_mac else ""

            for h in data.get("board_binding_history", []):
                if user_id is not None and int(h.get("user_id", 0)) != int(user_id):
                    continue
                if target_slot is not None and int(h.get("slot_number", 1)) != int(target_slot):
                    continue
                if device_mac:
                    hmac = str(h.get("device_mac", "")).strip().lower()
                    hclean = hmac.replace(":", "").replace("-", "")
                    if hmac != norm_filter and hclean != clean_filter and hmac != device_mac.strip().lower():
                        continue
                rows.append(dict(h))
            rows.sort(key=lambda x: (str(x.get("linked_at", "")), int(x.get("id", 0))), reverse=True)
            res = rows[:limit]
            for r in res:
                r["linked_at_str"] = str(r.get("linked_at", ""))
                r["last_active_str"] = str(r.get("last_active_at", ""))
                r["unlinked_at_str"] = str(r.get("unlinked_at", "")) if r.get("unlinked_at") else None
                r["status_label"] = "Sedang Tertaut" if r.get("status") == "ACTIVE" else "Terputus / Riwayat Lampau"
            return res

    def get_user_board_history(self, user_id: int) -> List[Dict[str, Any]]:
        return self.get_board_binding_history(user_id=int(user_id))

    def list_all_devices(self) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            user_map = {int(u.get("id", 0)): u.get("username", "") for u in data.get("users", [])}
            res = []
            for d in data.get("registered_devices", []):
                item = dict(d)
                if item.get("owner_id"):
                    item["owner_username"] = user_map.get(int(item["owner_id"]), "")
                res.append(item)
            res.sort(key=lambda x: (not x.get("is_protected", False), str(x.get("created_at", ""))), reverse=False)
            return res

    def list_devices(self, owner_id: int) -> list:
        with self._lock:
            data = self._load()
            return [d for d in data.get("registered_devices", []) if d.get("owner_id") is not None and int(d.get("owner_id")) == int(owner_id)]

    def get_user_mac_address(self, user_id: int) -> Optional[str]:
        with self._lock:
            data = self._load()
            for dev in data.get("registered_devices", []):
                if dev.get("owner_id") is not None and int(dev.get("owner_id")) == int(user_id):
                    return str(dev.get("device_id", "")).upper()
            return None

    def list_registered_devices(self, owner_id: int) -> list:
        return self.list_devices(owner_id)

    def find_device_by_mac(self, mac_address: str) -> Optional[Dict[str, Any]]:
        normalized_id = normalize_mac_address(mac_address)
        if not normalized_id:
            return None
        with self._lock:
            data = self._load()
            for d in data.get("registered_devices", []):
                stored_mac = normalize_mac_address(d.get("mac_address", "") or d.get("device_id", ""))
                if stored_mac and stored_mac.lower() == normalized_id.lower():
                    d["last_seen_at"] = utc_now()
                    self._commit(data, "Device heartbeat")
                    return d
            return None

    def find_device_by_id(self, device_id: str) -> Optional[Dict[str, Any]]:
        device_id = str(device_id or "").strip()
        if not device_id:
            return None
        with self._lock:
            data = self._load()
            for d in data.get("registered_devices", []):
                if d.get("id") == device_id or d.get("mac_address", "").lower() == device_id.lower():
                    return d
            return None

    def is_device_owned_by(self, device_id: str, owner_id: int) -> bool:
        dev = self.find_device_by_id(device_id)
        if not dev:
            return False
        return bool(dev.get("owner_id") is not None and int(dev.get("owner_id")) == int(owner_id))

    def delete_device(self, owner_id: int, device_id: str) -> bool:
        norm = normalize_mac_address(device_id)
        if norm in PROTECTED_DEVICE_MACS or self.is_device_protected(norm):
            return self.detach_device(norm, owner_id=owner_id, reason="Perangkat terlindungi (protected) - dipisahkan bukan dihapus")
        with self._lock:
            data = self._load()
            data.setdefault("registered_devices", [])
            data.setdefault("board_binding_history", [])
            now = utc_now()
            for h in data["board_binding_history"]:
                if normalize_mac_address(h.get("device_mac", "")).lower() == norm.lower() and h.get("status") == "ACTIVE":
                    h["status"] = "DETACHED"
                    h["unlinked_at"] = now
                    h["notes"] = "Device dihapus dari sistem"
            before = len(data.get("registered_devices", []))
            data["registered_devices"] = [
                d for d in data.get("registered_devices", [])
                if not ((d.get("id") == device_id or normalize_mac_address(d.get("device_id", "") or d.get("mac_address", "")).lower() == norm.lower())
                        and int(d.get("owner_id", 0)) == int(owner_id))
            ]
            changed = len(data.get("registered_devices", [])) != before
            if changed:
                self._commit(data, "Delete registered device")
            return changed

    def find_recent_audio_command_by_video_id(self, video_id: str, minutes: int = 20) -> Optional[Dict[str, Any]]:
        if not video_id:
            return None
        with self._lock:
            data = self._load()
            for cmd in reversed(data.get("audio_queue", [])):
                if cmd.get("video_id") == video_id and cmd.get("owner_id"):
                    return cmd
        return None

    def find_recent_audio_command_by_mac(self, mac_address: str, minutes: int = 20) -> Optional[Dict[str, Any]]:
        if not mac_address:
            return None
        clean = mac_address.replace(":", "").replace("-", "").strip().lower()
        with self._lock:
            data = self._load()
            for cmd in reversed(data.get("audio_queue", [])):
                url = str(cmd.get("stream_url", "")).lower()
                if mac_address.lower() in url or clean in url:
                    return cmd
        return None

    def find_recent_pending_audio_command(self, minutes: int = 2) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            for cmd in reversed(data.get("audio_queue", [])):
                if cmd.get("status") == "pending" and cmd.get("owner_id"):
                    return cmd
        return None

    def find_user_by_active_token_mac(self, device_mac: str) -> Optional[int]:
        """Cari user_id yang secara aktif mengunci MAC ini di salah satu dari 3 slot MCP-nya."""
        norm_mac = normalize_mac_address(device_mac)
        if not norm_mac:
            return None
        with self._lock:
            data = self._load()
            for item in reversed(data.get("xiaozhi_tokens", [])):
                stored_mac = normalize_mac_address(item.get("board_mac", ""))
                if stored_mac and stored_mac.lower() == norm_mac.lower() and item.get("is_active", True):
                    return int(item["user_id"])
        return None

    def find_active_user_for_unassigned_device(self, device_mac: str, query: str = "", video_id: str = "") -> Optional[int]:
        """
        Jika board belum terikat ke slot manapun (atau baru dipindah):
        Cari user yang baru saja memanggil pemutaran lagu ini di audio_queue.
        """
        with self._lock:
            data = self._load()
            queue = data.get("audio_queue", [])
            if video_id:
                for cmd in reversed(queue):
                    if cmd.get("video_id") == video_id and cmd.get("owner_id"):
                        return int(cmd["owner_id"])
            if query and len(query) >= 3:
                clean_q = query.strip().lower()
                for cmd in reversed(queue):
                    if clean_q in str(cmd.get("title", "")).lower() and cmd.get("owner_id"):
                        return int(cmd["owner_id"])
            for cmd in reversed(queue):
                if cmd.get("status") == "pending" and cmd.get("owner_id"):
                    return int(cmd["owner_id"])
        return None

    def expire_audio_commands(self, minutes: int = 30) -> int:
        count = 0
        with self._lock:
            data = self._load()
            for cmd in data.get("audio_queue", []):
                if cmd.get("status") == "pending":
                    cmd["status"] = "played"
                    count += 1
            if count > 0:
                self._commit(data, "Expire pending audio commands")
        return count

    def ping(self) -> bool:
        return True

    def get_now_playing(self, owner_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            for cmd in reversed(data.get("audio_queue", [])):
                if int(cmd.get("owner_id", 0)) == int(owner_id) and cmd.get("played_at"):
                    return cmd
            return None

    def update_real_relay_status(self, owner_id: int, room_id: int, relay_number: int, status: str) -> Optional[Dict[str, Any]]:
        status = normalize_relay_state(status)
        with self._lock:
            data = self._load()
            for item in data["relay_rooms"]:
                if int(item.get("id", 0)) != int(room_id) or int(item.get("owner_id", 0)) != int(owner_id):
                    continue
                for relay in item.get("relays", []):
                    if int(relay.get("relay_number", 0)) == int(relay_number):
                        relay["status"] = status
                        relay["updated_at"] = utc_now()
                        item["updated_at"] = utc_now()
                        self._commit(data, "Update real relay status")
                        return self._public_relay_room(item)
        return None

    def queue_real_relay_command(self, owner_id: int, room_id: int, relay_number: int, command: str) -> Dict[str, Any]:
        relay_number = parse_int_range(relay_number, field="Nomor relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        command = normalize_relay_state(command, field="Perintah relay")
        with self._lock:
            data = self._load()
            for item in data["relay_rooms"]:
                if int(item.get("id", 0)) != int(room_id) or int(item.get("owner_id", 0)) != int(owner_id):
                    continue
                relay = next(
                    (relay for relay in item.get("relays", []) if int(relay.get("relay_number", 0)) == relay_number),
                    None,
                )
                if not relay:
                    raise ValueError("Relay tidak ditemukan.")
                now = utc_now()
                command_id = secrets.token_hex(8)
                pending = [
                    pending_command
                    for pending_command in item.get("pending_commands", [])
                    if int(pending_command.get("relay_number", 0)) != relay_number
                    and not pending_command.get("acknowledged_at")
                ]
                command_row = {
                    "id": command_id,
                    "relay_number": relay_number,
                    "command": command,
                    "created_at": now,
                    "delivered_at": "",
                    "acknowledged_at": "",
                }
                pending.append(command_row)
                relay["desired_status"] = command
                relay["pending_command_id"] = command_id
                relay["updated_at"] = now
                item["pending_commands"] = pending
                item["last_command_at"] = now
                item["updated_at"] = now
                self._commit(data, "Queue real relay API command")
                return {
                    "room": self._public_relay_room(item, include_credentials=False),
                    "command": command_row,
                }
        raise ValueError("Data relay tidak ditemukan atau bukan milik akun ini.")

    def _find_real_relay_room_for_device(
        self,
        data: Dict[str, Any],
        api_slug: str,
        token: str,
    ) -> Optional[Dict[str, Any]]:
        api_slug = slugify_topic_part(api_slug)
        token = str(token or "")
        if not token:
            return None
        for item in data["relay_rooms"]:
            if self._relay_room_slug(item).lower() != api_slug.lower():
                continue
            expected_token = self._relay_api_token(item)
            if expected_token and hmac.compare_digest(expected_token, token):
                return item
        return None

    def get_real_relay_device_commands(self, api_slug: str, token: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            item = self._find_real_relay_room_for_device(data, api_slug, token)
            if not item:
                return None
            owner_id = int(item.get("owner_id", 0))
            settings = next(
                (
                    setting
                    for setting in data.get("feature_settings", [])
                    if int(setting.get("user_id", 0)) == owner_id
                ),
                None,
            )
            virtual_enabled = True if settings is None else bool(settings.get("virtual_smarthome_enabled", True))
            active_commands = []
            if not virtual_enabled:
                active_commands = [
                    command
                    for command in item.get("pending_commands", [])
                    if not command.get("acknowledged_at")
                ]
            room = self._public_relay_room(item, include_credentials=False)
            return {
                "room": room,
                "relay_mode_enabled": not virtual_enabled,
                "disabled_reason": "Simulasi Smart Home Virtual sedang aktif." if virtual_enabled else "",
                "commands": [
                    {
                        "id": command.get("id", ""),
                        "relay": int(command.get("relay_number", 0)),
                        "relay_number": int(command.get("relay_number", 0)),
                        "command": command.get("command", ""),
                        "created_at": command.get("created_at", ""),
                    }
                    for command in active_commands
                ],
            }

    def record_real_relay_device_status(
        self,
        api_slug: str,
        token: str,
        relay_number: int,
        status: str,
        command_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        relay_number = parse_int_range(relay_number, field="Nomor relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        status = normalize_relay_state(status)
        command_id = str(command_id or "").strip()
        with self._lock:
            data = self._load()
            item = self._find_real_relay_room_for_device(data, api_slug, token)
            if not item:
                return None
            relay = next(
                (relay for relay in item.get("relays", []) if int(relay.get("relay_number", 0)) == relay_number),
                None,
            )
            if not relay:
                raise ValueError("Relay tidak ditemukan.")
            try:
                previous_status = normalize_relay_state(relay.get("status", "OFF"))
            except ValueError:
                previous_status = "OFF"
            previous_desired = str(relay.get("desired_status", previous_status) or previous_status).upper()
            if previous_desired not in {"ON", "OFF"}:
                previous_desired = previous_status
            previous_seen_age = seconds_since_iso(item.get("device_last_seen_at", ""))
            previous_seen_recent = previous_seen_age is not None and previous_seen_age < 30
            pending_before = [
                pending_command
                for pending_command in item.get("pending_commands", [])
                if int(pending_command.get("relay_number", 0)) == relay_number
                and not pending_command.get("acknowledged_at")
            ]
            pending_commands = []
            command_acked = False
            for pending_command in item.get("pending_commands", []):
                same_relay = int(pending_command.get("relay_number", 0)) == relay_number
                same_command = str(pending_command.get("id", "")) == command_id if command_id else False
                status_matches = same_relay and str(pending_command.get("command", "")).upper() == status
                if same_relay and (same_command or status_matches):
                    command_acked = True
                    continue
                if not pending_command.get("acknowledged_at"):
                    pending_commands.append(pending_command)
            has_pending_same_relay = any(
                int(pending_command.get("relay_number", 0)) == relay_number
                for pending_command in pending_commands
            )
            if (
                previous_status == status
                and previous_desired == status
                and not pending_before
                and not command_acked
                and previous_seen_recent
            ):
                return self._public_relay_room(item, include_credentials=False)
            now = utc_now()
            relay["status"] = status
            relay["last_seen_at"] = now
            relay["updated_at"] = now
            if command_acked or not has_pending_same_relay:
                relay["desired_status"] = status
                relay["pending_command_id"] = ""
            item["pending_commands"] = pending_commands
            item["device_last_seen_at"] = now
            item["updated_at"] = now
            self._commit(data, "Device update real relay status")
            return self._public_relay_room(item, include_credentials=False)

    def match_real_relay_command(self, owner_id: int, text: str) -> Optional[Dict[str, Any]]:
        command = normalize_voice_command(text)
        if not command:
            return None
        phrase_matches: List[Tuple[int, Dict[str, Any]]] = []
        with self._lock:
            data = self._load()
            rows = [
                dict(item)
                for item in data["relay_rooms"]
                if int(item.get("owner_id", 0)) == int(owner_id)
            ]
        for item in rows:
            room = self._public_relay_room(item, include_credentials=False)
            for relay in room.get("relays", []):
                for field, relay_command in (("voice_command_on", "ON"), ("voice_command_off", "OFF")):
                    voice_command = normalize_voice_command(relay.get(field, ""))
                    if not voice_command:
                        continue
                    result = {"room": room, "relay": relay, "command": relay_command}
                    if voice_command == command:
                        return result
                    if f" {voice_command} " in f" {command} ":
                        phrase_matches.append((len(voice_command), result))
        if phrase_matches:
            phrase_matches.sort(key=lambda item: item[0], reverse=True)
            return phrase_matches[0][1]
        return None

    def search_materials(self, owner_id: Optional[int], keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        keyword = clean_text(keyword, max_len=120, min_len=1, field="Kata pencarian")

        with self._lock:
            data = self._load()
            rows = [
                dict(item)
                for item in data["materials"]
                if owner_id is None or int(item.get("owner_id", 0)) == int(owner_id)
            ]
        public_rows = [
            self._public_material(item, include_live_api=item.get("source_type") == LIVE_API_SOURCE_TYPE)
            for item in rows
        ]
        results = rank_materials(public_rows, keyword)
        if not results and " " in keyword:
            first_word = keyword.split(" ", 1)[0]
            results = rank_materials(public_rows, first_word)
        return results[:limit]

    def list_live_api_materials(self, owner_id: int, keyword: str = "", limit: int = 5) -> List[Dict[str, Any]]:
        keyword = clean_text(keyword, max_len=120, field="Kata pencarian") if keyword else ""
        with self._lock:
            data = self._load()
            rows = [
                dict(item)
                for item in data["materials"]
                if int(item.get("owner_id", 0)) == int(owner_id)
                and item.get("source_type") == LIVE_API_SOURCE_TYPE
            ]
        public_rows = [self._public_material(item, include_live_api=True) for item in rows]
        if keyword:
            ranked = rank_materials(public_rows, keyword)
            if ranked:
                return ranked[:limit]
        return sorted(public_rows, key=lambda item: int(item["id"]), reverse=True)[:limit]

    def list_material_database(
        self,
        owner_id: int,
        keyword: str = "",
        category: str = "",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        keyword = clean_text(keyword, max_len=120, field="Kata pencarian") if keyword else ""
        category = clean_text(category, max_len=80, field="Kategori") if category else ""
        limit = max(1, min(int(limit or 10), 25))
        with self._lock:
            data = self._load()
            rows = [
                dict(item)
                for item in data["materials"]
                if int(item.get("owner_id", 0)) == int(owner_id)
                and (not category or item.get("category", "").lower() == category.lower())
            ]
        public_rows = [
            self._public_material(item, include_live_api=item.get("source_type") == LIVE_API_SOURCE_TYPE)
            for item in rows
        ]
        if keyword:
            ranked = rank_materials(public_rows, keyword, include_zero=True)
        else:
            ranked = sorted(public_rows, key=lambda item: int(item["id"]), reverse=True)
        return ranked[:limit]

    def find_material_detail(
        self,
        owner_id: int,
        material_id: int = 0,
        title: str = "",
        keyword: str = "",
    ) -> Optional[Dict[str, Any]]:
        if material_id:
            return self.get_material(owner_id, material_id, include_live_api=True)
        title = clean_text(title, max_len=160, field="Judul") if title else ""
        keyword = clean_text(keyword, max_len=120, field="Kata pencarian") if keyword else title
        with self._lock:
            data = self._load()
            rows = [
                dict(item)
                for item in data["materials"]
                if int(item.get("owner_id", 0)) == int(owner_id)
            ]
        public_rows = [
            self._public_material(item, include_live_api=item.get("source_type") == LIVE_API_SOURCE_TYPE)
            for item in rows
        ]
        if title:
            exact = next((item for item in public_rows if item.get("title", "").lower() == title.lower()), None)
            if exact:
                return exact
        ranked = rank_materials(public_rows, keyword, include_zero=False)
        return ranked[0] if ranked else None

    # ── Relay Rooms (stub for HFJsonStore) ─────────────────────────────────

    def list_relay_rooms(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            rooms = [
                r for r in data.get("relay_rooms", [])
                if int(r.get("owner_id", 0)) == int(owner_id)
            ]
            result = []
            for room in rooms:
                result.append({
                    "id": room.get("id"),
                    "nama_tempat": room.get("nama_tempat", ""),
                    "api_slug": room.get("api_slug", ""),
                    "api_client_id": room.get("api_client_id", ""),
                    "relays": room.get("relays", []),
                })
            return result

    def add_relay_room(self, owner_id: int, nama_tempat: str, api_slug: str, api_token: str, api_client_id: str, relays: List[Dict]) -> int:
        with self._lock:
            data = self._load()
            room_id = self._next_id(data, "relay_rooms")
            data["relay_rooms"].append({
                "id": room_id,
                "owner_id": int(owner_id),
                "nama_tempat": nama_tempat,
                "api_slug": api_slug,
                "api_token": api_token,
                "api_client_id": api_client_id,
                "relays": relays,
                "created_at": utc_now(),
            })
            self._commit(data, "Add relay room")
            return room_id

    def get_relay_room(self, owner_id: int, room_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            room = next(
                (r for r in data.get("relay_rooms", [])
                 if int(r.get("id", 0)) == int(room_id) and int(r.get("owner_id", 0)) == int(owner_id)),
                None,
            )
            return room if room else None

    def update_relay_room(self, owner_id: int, room_id: int, nama_tempat: str, relays: List[Dict]) -> None:
        with self._lock:
            data = self._load()
            for room in data.get("relay_rooms", []):
                if int(room.get("id", 0)) == int(room_id) and int(room.get("owner_id", 0)) == int(owner_id):
                    room["nama_tempat"] = nama_tempat
                    room["relays"] = relays
                    room["updated_at"] = utc_now()
                    break
            self._commit(data, "Update relay room")

    def delete_relay_room(self, owner_id: int, room_id: int) -> bool:
        with self._lock:
            data = self._load()
            before = len(data.get("relay_rooms", []))
            data["relay_rooms"] = [
                r for r in data.get("relay_rooms", [])
                if not (int(r.get("id", 0)) == int(room_id) and int(r.get("owner_id", 0)) == int(owner_id))
            ]
            changed = len(data.get("relay_rooms", [])) != before
            if changed:
                self._commit(data, "Delete relay room")
            return changed

    def update_relay_status(self, owner_id: int, room_id: int, relay_number: int, status: str) -> None:
        with self._lock:
            data = self._load()
            for room in data.get("relay_rooms", []):
                if int(room.get("id", 0)) == int(room_id) and int(room.get("owner_id", 0)) == int(owner_id):
                    for relay in room.get("relays", []):
                        if int(relay.get("relay_number", 0)) == int(relay_number):
                            relay["status"] = status
                            break
                    break
            self._commit(data, "Update relay status")

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

    def get_pending_relay_commands(self, owner_id: int, room_id: int) -> List[Dict[str, Any]]:
        return []

    # ── User Playlists ─────────────────────────────────────────────────────

    def add_playlist_track(
        self, owner_id: int, title: str, youtube_url: str, video_id: str, artist: str = "", duration: str = ""
    ) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            playlists = data.setdefault("user_playlists", [])
            user_tracks = [p for p in playlists if int(p.get("owner_id", 0)) == int(owner_id)]
            next_num = max([int(p.get("track_number", 0)) for p in user_tracks], default=0) + 1
            max_id = max([int(p.get("id", 0)) for p in playlists], default=0) + 1
            now = utc_now()
            new_item = {
                "id": max_id,
                "owner_id": int(owner_id),
                "track_number": next_num,
                "title": clean_text(title, max_len=200, field="Judul"),
                "youtube_url": str(youtube_url).strip(),
                "video_id": str(video_id).strip(),
                "artist": clean_text(artist or "", max_len=120, field="Artis"),
                "duration": clean_text(duration or "", max_len=30, field="Durasi"),
                "play_count": 0,
                "last_played_at": None,
                "created_at": now,
                "updated_at": now,
            }
            playlists.append(new_item)
            self._commit(data, "Add playlist track")
            return new_item

    def get_user_playlist(self, owner_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            items = [p for p in data.get("user_playlists", []) if int(p.get("owner_id", 0)) == int(owner_id)]
            items.sort(key=lambda x: (int(x.get("track_number", 0)), int(x.get("id", 0))))
            return [dict(x) for x in items]

    def get_playlist_track(self, owner_id: int, track_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            for p in data.get("user_playlists", []):
                if int(p.get("id", 0)) == int(track_id) and int(p.get("owner_id", 0)) == int(owner_id):
                    return dict(p)
            return None

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
        with self._lock:
            data = self._load()
            target = None
            for p in data.get("user_playlists", []):
                if int(p.get("id", 0)) == int(track_id) and int(p.get("owner_id", 0)) == int(owner_id):
                    target = p
                    break
            if not target:
                return None
            if title is not None:
                target["title"] = clean_text(title, max_len=200, field="Judul")
            if youtube_url is not None:
                target["youtube_url"] = str(youtube_url).strip()
            if video_id is not None:
                target["video_id"] = str(video_id).strip()
            if artist is not None:
                target["artist"] = clean_text(artist, max_len=120, field="Artis")
            if track_number is not None:
                target["track_number"] = int(track_number)
            target["updated_at"] = utc_now()
            self._commit(data, "Update playlist track")
            return dict(target)

    def delete_playlist_track(self, owner_id: int, track_id: int) -> bool:
        with self._lock:
            data = self._load()
            playlists = data.get("user_playlists", [])
            before = len(playlists)
            data["user_playlists"] = [
                p for p in playlists
                if not (int(p.get("id", 0)) == int(track_id) and int(p.get("owner_id", 0)) == int(owner_id))
            ]
            if len(data["user_playlists"]) != before:
                # Renumber remaining
                user_items = [p for p in data["user_playlists"] if int(p.get("owner_id", 0)) == int(owner_id)]
                user_items.sort(key=lambda x: (int(x.get("track_number", 0)), int(x.get("id", 0))))
                for idx, item in enumerate(user_items, start=1):
                    item["track_number"] = idx
                self._commit(data, "Delete playlist track")
                return True
            return False

    def increment_playlist_play_count(
        self, owner_id: int, track_id: Optional[int] = None, video_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if not track_id and not video_id:
            return None
        with self._lock:
            data = self._load()
            target = None
            for p in data.get("user_playlists", []):
                if int(p.get("owner_id", 0)) == int(owner_id):
                    if track_id and int(p.get("id", 0)) == int(track_id):
                        target = p
                        break
                    elif video_id and str(p.get("video_id", "")).strip() == str(video_id).strip():
                        target = p
                        break
            if not target:
                return None
            target["play_count"] = int(target.get("play_count", 0)) + 1
            now = utc_now()
            target["last_played_at"] = now
            target["updated_at"] = now
            self._commit(data, "Increment playlist play count")
            return dict(target)

    def get_top_played_playlist(self, owner_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            items = [
                p for p in data.get("user_playlists", [])
                if int(p.get("owner_id", 0)) == int(owner_id) and int(p.get("play_count", 0)) > 0
            ]
            items.sort(key=lambda x: (int(x.get("play_count", 0)), str(x.get("last_played_at") or "")), reverse=True)
            return [dict(x) for x in items[:limit]]

    def find_playlist_track_by_video_id(self, video_id: str, owner_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if not video_id:
            return None
        with self._lock:
            data = self._load()
            for p in data.get("user_playlists", []):
                if str(p.get("video_id", "")).strip() == str(video_id).strip():
                    if owner_id is None or int(p.get("owner_id", 0)) == int(owner_id):
                        return dict(p)
            return None

    def find_playlist_track_by_query(self, owner_id: int, query: str) -> Optional[Dict[str, Any]]:
        raw_q = (query or "").strip()
        if not raw_q:
            return None
        import re
        from xiaozhi.core.utils import detect_media_url_source, extract_youtube_video_id, is_generic_playlist_query

        user_tracks = self.get_user_playlist(owner_id)
        if not user_tracks:
            return None

        # Check track number: ONLY if query is literally a number or has explicit keyword ("nomor 2", "lagu 1", "track 3", "playlist 1")
        track_num = None
        if raw_q.isdigit():
            track_num = int(raw_q)
        else:
            explicit_track_match = re.search(r"\b(?:nomor|no\.?|ke-?|track|urutan|playlist|lagu|musik)\s*(\d+)\b", raw_q.lower())
            if explicit_track_match:
                track_num = int(explicit_track_match.group(1))

        if track_num is not None:
            for t in user_tracks:
                if int(t.get("track_number", 0)) == track_num:
                    return t

        # Check Video ID / URL (YouTube, TikTok, atau internal stream URL)
        media_info = detect_media_url_source(raw_q)
        target_vid = media_info["video_id"] if media_info else extract_youtube_video_id(raw_q)
        if target_vid:
            for t in user_tracks:
                if str(t.get("video_id", "")).strip() == target_vid:
                    return t

        # Jika query hanya permintaan umum memutar playlist tanpa nama lagu spesifik, kembalikan None agar asisten bertanya
        if is_generic_playlist_query(raw_q):
            return None

        # Check by title / artist
        clean_kw = re.sub(r"(?i)\b(putar|mainkan|setel|dengarkan|lagu|musik|dari playlist|di playlist|playlist|saya|favorit|pribadi)\b", "", raw_q).strip().lower()
        target_q = clean_kw if clean_kw else raw_q.lower()
        if not target_q:
            return None
        for t in user_tracks:
            if target_q in str(t.get("title", "")).lower() or target_q in str(t.get("artist", "")).lower():
                return t
        return None

    # ── Akses Plus Management (Multi-Slot & Playlist Quotas) ───────────────────

    def get_user_access_plus(self, user_id: int) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            cfg = data.get("access_plus_settings", {}).get(str(user_id), {})
            return {
                "user_id": int(user_id),
                "mcp_multislot_allowed": bool(cfg.get("mcp_multislot_allowed", True)),
                "playlist_quota_enabled": bool(cfg.get("playlist_quota_enabled", False)),
                "max_playlist_tracks": int(cfg.get("max_playlist_tracks", 15) or 15),
                "notes": cfg.get("notes", ""),
            }

    def set_user_access_plus(
        self,
        user_id: int,
        mcp_multislot_allowed: Optional[bool] = None,
        playlist_quota_enabled: Optional[bool] = None,
        max_playlist_tracks: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            settings = data.setdefault("access_plus_settings", {})
            current = settings.get(str(user_id), {})
            new_multislot = current.get("mcp_multislot_allowed", True) if mcp_multislot_allowed is None else bool(mcp_multislot_allowed)
            new_quota_enabled = current.get("playlist_quota_enabled", False) if playlist_quota_enabled is None else bool(playlist_quota_enabled)
            new_max_tracks = current.get("max_playlist_tracks", 15) if max_playlist_tracks is None else int(max_playlist_tracks)
            new_notes = current.get("notes", "") if notes is None else str(notes).strip()

            settings[str(user_id)] = {
                "user_id": int(user_id),
                "mcp_multislot_allowed": new_multislot,
                "playlist_quota_enabled": new_quota_enabled,
                "max_playlist_tracks": new_max_tracks,
                "notes": new_notes,
                "updated_at": utc_now().isoformat(),
            }

            # Update slot tokens is_active
            for item in data.get("xiaozhi_tokens", []):
                if int(item.get("user_id", 0)) == int(user_id) and int(item.get("slot_number", 1)) in (2, 3):
                    item["is_active"] = new_multislot

            # Update playlists is_active
            user_playlists = data.get("user_playlists", {}).get(str(user_id), [])
            for track in user_playlists:
                if new_quota_enabled:
                    track["is_active"] = int(track.get("track_number", 1)) <= new_max_tracks
                else:
                    track["is_active"] = True

            self._commit(data, f"Update access plus settings for user {user_id}")
            return settings[str(user_id)]

    def set_user_slot_active(self, user_id: int, slot: int, is_active: bool) -> bool:
        with self._lock:
            data = self._load()
            changed = False
            for item in data.get("xiaozhi_tokens", []):
                if int(item.get("user_id", 0)) == int(user_id) and int(item.get("slot_number", 1)) == int(slot):
                    item["is_active"] = bool(is_active)
                    changed = True
            if changed:
                self._commit(data, f"Set slot {slot} active={is_active} for user {user_id}")
            return changed

    def set_user_playlist_track_active(self, user_id: int, track_id: int, is_active: bool) -> bool:
        with self._lock:
            data = self._load()
            user_playlists = data.get("user_playlists", {}).get(str(user_id), [])
            changed = False
            for t in user_playlists:
                if int(t.get("id", 0)) == int(track_id):
                    t["is_active"] = bool(is_active)
                    changed = True
            if changed:
                self._commit(data, f"Set track {track_id} active={is_active} for user {user_id}")
            return changed

    def count_user_playlist_tracks(self, owner_id: int, active_only: bool = False) -> int:
        tracks = self.get_user_playlist(owner_id)
        if active_only:
            return sum(1 for t in tracks if t.get("is_active", True))
        return len(tracks)

    def list_access_plus_overview(self) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            users = [u for u in data.get("users", []) if u.get("role") != "admin"]
            settings = data.get("access_plus_settings", {})
            tokens = data.get("xiaozhi_tokens", [])
            playlists = data.get("user_playlists", {})

            result = []
            for u in users:
                uid = int(u["id"])
                plus_cfg = settings.get(str(uid), {})
                u_tokens = [t for t in tokens if int(t.get("user_id", 0)) == uid]
                u_tracks = playlists.get(str(uid), [])

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
                active_cnt = sum(1 for tr in u_tracks if tr.get("is_active", True))
                disabled_cnt = len(u_tracks) - active_cnt

                result.append({
                    "user_id": uid,
                    "username": u.get("username", f"user_{uid}"),
                    "role": u.get("role", "user"),
                    "created_at": u.get("created_at", ""),
                    "mcp_multislot_allowed": mcp_multislot_allowed,
                    "playlist_quota_enabled": playlist_quota_enabled,
                    "max_playlist_tracks": max_playlist_tracks,
                    "notes": plus_cfg.get("notes", ""),
                    "slots": slot_map,
                    "total_slots": len(u_tokens),
                    "has_multislot": has_multislot,
                    "playlist": {
                        "has_playlist": len(u_tracks) > 0,
                        "total_tracks": len(u_tracks),
                        "active_tracks": active_cnt,
                        "disabled_tracks": disabled_cnt,
                    },
                    "is_restricted": (not mcp_multislot_allowed) or playlist_quota_enabled,
                })
            return result


