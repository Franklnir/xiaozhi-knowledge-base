from typing import Any, Dict

from xiaozhi.config import DEFAULT_UI_THEME, UI_THEMES, USER_LIMIT_DEFAULTS


def empty_database() -> Dict[str, Any]:
    return {
        "version": 1,
        "next_ids": {"users": 1, "materials": 1, "categories": 1, "chat_history": 1, "relay_rooms": 1},
        "users": [],
        "materials": [],
        "categories": [],
        "xiaozhi_tokens": [],
        "chat_history": [],
        "relay_rooms": [],
        "audio_queue": [],
        "registered_devices": [],
        "feature_settings": [],
        "user_limits": [],
    }


def normalize_database(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = empty_database()
    data.setdefault("version", 1)
    data.setdefault("next_ids", {})
    for bucket in ("users", "materials", "categories", "xiaozhi_tokens", "chat_history", "relay_rooms", "audio_queue", "registered_devices", "feature_settings", "user_limits"):
        data.setdefault(bucket, [])
        if not isinstance(data[bucket], list):
            data[bucket] = []
    for user in data["users"]:
        role = str(user.get("role") or "user").lower()
        user["role"] = "admin" if role == "admin" else "user"
        try:
            user["session_version"] = max(1, int(user.get("session_version", 1) or 1))
        except (TypeError, ValueError):
            user["session_version"] = 1
        raw_theme = user.get("ui_theme")
        if not raw_theme or str(raw_theme) not in UI_THEMES:
            user["ui_theme"] = DEFAULT_UI_THEME
    for item in data["user_limits"]:
        for key, default in USER_LIMIT_DEFAULTS.items():
            try:
                item[key] = max(0, int(item.get(key, default) or 0))
            except (TypeError, ValueError):
                item[key] = default
    for bucket in ("users", "materials", "categories", "chat_history", "relay_rooms"):
        max_id = max((int(item.get("id", 0)) for item in data[bucket] if item.get("id")), default=0)
        data["next_ids"][bucket] = max(int(data["next_ids"].get(bucket, 1)), max_id + 1)
    return data
