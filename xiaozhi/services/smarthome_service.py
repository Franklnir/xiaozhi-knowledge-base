import re
import secrets
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple, Union

from xiaozhi.config import (
    SMART_HOME_AMBIGUOUS_TARGETS,
    SMART_HOME_CHANNELS,
    SMART_HOME_OFF_ACTIONS,
    SMART_HOME_ON_ACTIONS,
    SMART_HOME_RELAYS,
    SMART_HOME_VALID_ROOM_HINT,
)
from xiaozhi.core.utils import slugify_topic_part, utc_now

# ── State ──────────────────────────────────────────────────────────────────
smart_home_lock = RLock()
smart_home_states: Dict[int, Dict[str, bool]] = {}
smart_home_updated_at: Dict[int, str] = {}


# ── Helpers ────────────────────────────────────────────────────────────────

def normalize_smart_home_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def smart_home_room_name(channel: Union[int, str]) -> str:
    return SMART_HOME_RELAYS[str(channel)]["name"]


def parse_smart_home_action(action: Any) -> bool:
    command = normalize_smart_home_text(action)
    if command in SMART_HOME_ON_ACTIONS:
        return True
    if command in SMART_HOME_OFF_ACTIONS:
        return False
    raise ValueError("Action harus on/off, nyala/mati, atau hidup/matikan.")


def resolve_smart_home_target(target: Any) -> Tuple[Optional[int], Optional[str]]:
    normalized_target = normalize_smart_home_text(target)
    if not normalized_target:
        return None, "Target ruangan kosong. Sebutkan ruangan spesifik atau channel 1-8."

    if normalized_target.isdigit() and normalized_target in SMART_HOME_RELAYS:
        return int(normalized_target), None

    if normalized_target in SMART_HOME_AMBIGUOUS_TARGETS:
        rooms = ", ".join(smart_home_room_name(ch) for ch in SMART_HOME_AMBIGUOUS_TARGETS[normalized_target])
        return None, f"Target '{target}' ambigu. Pilih salah satu: {rooms}."

    exact_matches = [
        int(channel)
        for channel, meta in SMART_HOME_RELAYS.items()
        if normalized_target in meta["aliases"]
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], None
    if len(exact_matches) > 1:
        rooms = ", ".join(smart_home_room_name(ch) for ch in exact_matches)
        return None, f"Target '{target}' ambigu. Kandidat: {rooms}."

    scored_matches: List[Tuple[int, int]] = []
    for channel, meta in SMART_HOME_RELAYS.items():
        best_score = 0
        for alias in meta["aliases"]:
            needle = f" {alias} "
            haystack = f" {normalized_target} "
            if needle in haystack:
                best_score = max(best_score, len(alias))
        if best_score:
            scored_matches.append((best_score, int(channel)))

    if not scored_matches:
        return (
            None,
            "Target tidak dikenali. Gunakan channel 1-8 atau salah satu ruangan ini: "
            f"{SMART_HOME_VALID_ROOM_HINT}.",
        )

    scored_matches.sort(reverse=True)
    top_score = scored_matches[0][0]
    top_channels = sorted(channel for score, channel in scored_matches if score == top_score)
    if len(top_channels) == 1:
        return top_channels[0], None

    rooms = ", ".join(smart_home_room_name(ch) for ch in top_channels)
    return None, f"Target '{target}' ambigu. Kandidat: {rooms}."


# ── State Management ───────────────────────────────────────────────────────

def _smart_home_state_unlocked(owner_id: int) -> Dict[str, bool]:
    user_id = int(owner_id)
    state = smart_home_states.setdefault(user_id, {channel: False for channel in SMART_HOME_CHANNELS})
    for channel in SMART_HOME_CHANNELS:
        state.setdefault(channel, False)
    smart_home_updated_at.setdefault(user_id, utc_now())
    return state


def set_smart_home_relay(owner_id: int, channel: Union[int, str], enabled: bool) -> Dict[str, bool]:
    channel_key = str(channel)
    if channel_key not in SMART_HOME_RELAYS:
        raise ValueError(f"Channel {channel} tidak valid. Gunakan channel 1-8.")
    with smart_home_lock:
        state = _smart_home_state_unlocked(int(owner_id))
        state[channel_key] = bool(enabled)
        smart_home_updated_at[int(owner_id)] = utc_now()
        return dict(state)


def set_all_smart_home_relays(owner_id: int, enabled: bool) -> Dict[str, bool]:
    with smart_home_lock:
        state = _smart_home_state_unlocked(int(owner_id))
        for channel in SMART_HOME_CHANNELS:
            state[channel] = bool(enabled)
        smart_home_updated_at[int(owner_id)] = utc_now()
        return dict(state)


def get_smart_home_state(owner_id: int) -> Dict[str, bool]:
    with smart_home_lock:
        return dict(_smart_home_state_unlocked(int(owner_id)))


def smart_home_status(owner_id: int) -> Dict[str, Dict[str, Any]]:
    state = get_smart_home_state(owner_id)
    return {
        channel: {
            "room": SMART_HOME_RELAYS[channel]["name"],
            "device": SMART_HOME_RELAYS[channel]["device"],
            "state": bool(state[channel]),
            "status": "ON" if state[channel] else "OFF",
        }
        for channel in SMART_HOME_CHANNELS
    }


def smart_home_payload(owner_id: int, *, token_saved: bool = False, mcp_connected: bool = False, virtual_enabled: bool = True) -> Dict[str, Any]:
    state = get_smart_home_state(owner_id)
    active_count = sum(1 for enabled in state.values() if enabled)
    return {
        "relays": [
            {
                "channel": int(channel),
                "name": SMART_HOME_RELAYS[channel]["name"],
                "location": SMART_HOME_RELAYS[channel]["location"],
                "color": SMART_HOME_RELAYS[channel]["color"],
                "device": SMART_HOME_RELAYS[channel]["device"],
                "state": bool(state[channel]),
            }
            for channel in SMART_HOME_CHANNELS
        ],
        "activeCount": active_count,
        "totalCount": len(SMART_HOME_CHANNELS),
        "mcpConnected": mcp_connected,
        "tokenSaved": bool(token_saved),
        "virtualEnabled": virtual_enabled,
        "updatedAt": smart_home_updated_at.get(int(owner_id), ""),
    }
