import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from xiaozhi.services.preset_approval_service import (
    list_presets,
    sanitize_preset_for_client,
)

logger = logging.getLogger("xiaozhi.changelog")

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CHANGELOG_FILE = DATA_DIR / "server_changelog.json"

ALLOWED_SERVER_UPDATE_TYPES = ("server_reconnect", "device_reconnect", "reflash")

DEFAULT_SEED_ENTRIES: List[Dict[str, Any]] = [
    {
        "id": "srv_init_mcp_sync",
        "title": "Pembaruan Server & MCP Tools (Musik YouTube, Relay Smart Home & Memori Chat)",
        "description": (
            "Perbaikan pada layanan server cloud & MCP tools berjalan otomatis di sisi server. "
            "Pengguna TIDAK perlu melakukan flash ulang firmware (.bin) — cukup pastikan perangkat terhubung "
            "atau lakukan reconnect jika perubahan belum langsung aktif."
        ),
        "update_type": "server_reconnect",
        "target": "Server & MCP",
        "created_at": "2026-11-11T18:00:00+00:00",
        "created_by": "system",
    }
]


def _get_changelog_file() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return CHANGELOG_FILE


def _normalize_server_update_type(raw_type: Optional[str]) -> str:
    val = str(raw_type or "server_reconnect").strip().lower()
    if val in ALLOWED_SERVER_UPDATE_TYPES:
        return val
    return "server_reconnect"


def _load_entries() -> List[Dict[str, Any]]:
    path = _get_changelog_file()
    if not path.exists():
        entries = [dict(item) for item in DEFAULT_SEED_ENTRIES]
        _save_entries(entries)
        return entries
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            entries = data.get("entries", [])
        elif isinstance(data, list):
            entries = data
        else:
            entries = []
        if not isinstance(entries, list):
            return []
        cleaned: List[Dict[str, Any]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            cleaned.append({
                "id": str(item.get("id") or str(uuid.uuid4())[:8]),
                "title": str(item.get("title") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "update_type": _normalize_server_update_type(item.get("update_type")),
                "target": str(item.get("target") or "Server & MCP").strip() or "Server & MCP",
                "created_at": str(item.get("created_at") or ""),
                "created_by": str(item.get("created_by") or "admin"),
            })
        cleaned.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return cleaned
    except Exception as exc:
        logger.error("Gagal memuat server_changelog.json: %s", exc)
        return []


def _save_entries(entries: List[Dict[str, Any]]) -> None:
    path = _get_changelog_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.with_suffix(f".tmp.{secrets.token_hex(4)}")
    payload = {"entries": entries}
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        try:
            os.replace(temp_file, path)
        except (PermissionError, OSError):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)
    except Exception as exc:
        logger.error("Gagal menyimpan server_changelog.json: %s", exc)
        if temp_file.exists():
            temp_file.unlink(missing_ok=True)
        raise


def list_server_updates() -> List[Dict[str, Any]]:
    """Kembalikan daftar catatan update server & MCP (urut terbaru dulu)."""
    return _load_entries()


def add_server_update(
    title: str,
    description: str,
    update_type: str = "server_reconnect",
    target: str = "Server & MCP",
    admin_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Tambahkan catatan pembaruan Server / MCP baru."""
    clean_title = str(title or "").strip()
    clean_desc = str(description or "").strip()
    if not clean_title:
        raise ValueError("Judul pembaruan wajib diisi.")
    if not clean_desc:
        raise ValueError("Deskripsi pembaruan wajib diisi.")

    clean_type = _normalize_server_update_type(update_type)
    clean_target = str(target or "Server & MCP").strip() or "Server & MCP"
    admin_name = str((admin_user or {}).get("username") or "admin")
    now_iso = datetime.now(timezone.utc).isoformat()

    entry = {
        "id": f"srv_{uuid.uuid4().hex[:8]}",
        "title": clean_title,
        "description": clean_desc,
        "update_type": clean_type,
        "target": clean_target,
        "created_at": now_iso,
        "created_by": admin_name,
    }

    entries = _load_entries()
    entries.insert(0, entry)
    _save_entries(entries)
    logger.info("Admin '%s' menambahkan changelog server '%s' (%s).", admin_name, clean_title, clean_type)
    return entry


def delete_server_update(entry_id: str) -> bool:
    """Hapus catatan pembaruan server berdasarkan ID."""
    target_id = str(entry_id or "").strip()
    if not target_id:
        return False
    entries = _load_entries()
    remaining = [e for e in entries if e.get("id") != target_id]
    if len(remaining) == len(entries):
        return False
    _save_entries(remaining)
    return True


def get_unified_changelog_payload() -> Dict[str, Any]:
    """
    Siapkan payload lengkap dan aman untuk halaman /changelog & /api/v1/changelog.
    Tidak menyertakan storage_bucket, storage_key, enc_rel_path, claim_codes, atau granted_users.
    """
    raw_presets = list_presets()
    safe_presets = [p for p in (sanitize_preset_for_client(item) for item in raw_presets) if p]
    server_updates = list_server_updates()

    total_fw_releases = sum(len(p.get("versions") or []) for p in safe_presets)
    return {
        "presets": safe_presets,
        "server_updates": server_updates,
        "stats": {
            "total_presets": len(safe_presets),
            "total_firmware_releases": total_fw_releases,
            "total_server_updates": len(server_updates),
        },
    }
