import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("xiaozhi.announcement")

# Locate data folder
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
ANNOUNCEMENT_FILE = DATA_DIR / "announcement.json"

DEFAULT_ANNOUNCEMENT: Dict[str, Any] = {
    "id": "ann_default",
    "title": "Selamat Datang di Xiaozhi & ESPBridge!",
    "content": "Halo! Selamat datang di platform asisten suara pintar Xiaozhi. Pastikan perangkat ESP32 Anda telah terkonfigurasi dengan baik.",
    "category": "info",  # 'info', 'warning', 'update', 'maintenance'
    "is_active": True,
    "created_at": None,
    "updated_at": None,
    "updated_by": "system"
}


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_announcement() -> Dict[str, Any]:
    """Retrieve the current announcement data from JSON file."""
    _ensure_data_dir()
    if not ANNOUNCEMENT_FILE.exists():
        # Initialize default file
        data = dict(DEFAULT_ANNOUNCEMENT)
        now_iso = datetime.now(timezone.utc).isoformat()
        data["created_at"] = now_iso
        data["updated_at"] = now_iso
        try:
            with open(ANNOUNCEMENT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to create default announcement file: {e}")
        return data

    try:
        with open(ANNOUNCEMENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure required keys exist
            for k, v in DEFAULT_ANNOUNCEMENT.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception as e:
        logger.error(f"Error reading announcement file: {e}")
        return dict(DEFAULT_ANNOUNCEMENT)


def get_active_announcement() -> Optional[Dict[str, Any]]:
    """Retrieve announcement only if it is active and has non-empty content."""
    ann = get_announcement()
    if ann.get("is_active") and str(ann.get("content", "")).strip():
        return ann
    return None


def save_announcement(
    title: str,
    content: str,
    category: str = "info",
    is_active: bool = True,
    updated_by: str = "admin"
) -> Dict[str, Any]:
    """Save or update announcement with a new revision ID and timestamp."""
    _ensure_data_dir()
    current = get_announcement()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Generate revision ID based on timestamp so clients detect update
    new_id = f"ann_{int(time.time())}"

    updated = {
        "id": new_id,
        "title": title.strip(),
        "content": content.strip(),
        "category": category if category in ("info", "warning", "update", "maintenance") else "info",
        "is_active": bool(is_active),
        "created_at": current.get("created_at") or now_iso,
        "updated_at": now_iso,
        "updated_by": updated_by
    }

    tmp_file = ANNOUNCEMENT_FILE.with_suffix(".tmp")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(updated, f, indent=2, ensure_ascii=False)
        os.replace(tmp_file, ANNOUNCEMENT_FILE)
        logger.info(f"Announcement updated successfully by {updated_by} (ID: {new_id}, active: {is_active})")
        return updated
    except Exception as e:
        logger.error(f"Error saving announcement: {e}")
        if tmp_file.exists():
            tmp_file.unlink(missing_ok=True)
        raise e


def delete_announcement(updated_by: str = "admin") -> Dict[str, Any]:
    """Clear announcement content and set active to False."""
    _ensure_data_dir()
    now_iso = datetime.now(timezone.utc).isoformat()
    new_id = f"ann_{int(time.time())}"

    cleared = {
        "id": new_id,
        "title": "",
        "content": "",
        "category": "info",
        "is_active": False,
        "created_at": now_iso,
        "updated_at": now_iso,
        "updated_by": updated_by
    }

    try:
        with open(ANNOUNCEMENT_FILE, "w", encoding="utf-8") as f:
            json.dump(cleared, f, indent=2, ensure_ascii=False)
        logger.info(f"Announcement cleared/deleted by {updated_by}")
        return cleared
    except Exception as e:
        logger.error(f"Error deleting announcement: {e}")
        raise e
