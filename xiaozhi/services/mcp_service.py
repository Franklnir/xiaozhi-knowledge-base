import asyncio
import logging
from threading import RLock
from typing import Any, Dict, Optional

from xiaozhi.core.security import normalize_token_hash
from xiaozhi.core.utils import utc_now

logger = logging.getLogger("xiaozhi.mcp")

# ── MCP State (Maksimal 3 Slot per Akun) ─────────────────────────────────
mcp_state_lock = RLock()
mcp_connection_states: Dict[str, Dict[str, Any]] = {}
mcp_bridge_tasks: Dict[str, asyncio.Task] = {}
mcp_reload_event: Optional[asyncio.Event] = None


def _slot_key(owner_id: int, slot: int = 1) -> str:
    return f"{int(owner_id)}:{int(slot)}"


def clear_mcp_state(owner_id: int, slot: Optional[int] = None) -> None:
    user_id = int(owner_id)
    with mcp_state_lock:
        if slot is not None:
            mcp_connection_states.pop(_slot_key(user_id, slot), None)
            has_connected = any(
                dict(mcp_connection_states.get(_slot_key(user_id, s), {})).get("connected")
                for s in (1, 2, 3)
            )
            if not has_connected:
                mcp_connection_states.pop(str(user_id), None)
                mcp_connection_states.pop(user_id, None)  # type: ignore
        else:
            for s in (1, 2, 3):
                mcp_connection_states.pop(_slot_key(user_id, s), None)
            mcp_connection_states.pop(str(user_id), None)
            mcp_connection_states.pop(user_id, None)  # type: ignore
    logger.info("MCP state cleared for user_id=%s slot=%s", user_id, slot or "all")


def set_mcp_connection_state(
    owner_id: int,
    token_hash: str,
    *,
    connected: bool,
    message: str = "",
    request_id: str = "",
    slot: int = 1,
    device_label: str = "",
    board_mac: str = "",
    **kwargs: Any,
) -> None:
    user_id = int(owner_id)
    slot_num = int(slot or 1)
    token_hash = normalize_token_hash(token_hash)
    key = _slot_key(user_id, slot_num)
    label = device_label or f"XiaoZhi {slot_num}"
    mac_val = (board_mac or "").strip()
    with mcp_state_lock:
        previous = dict(mcp_connection_states.get(key, {}))
        previous.update(
            {
                "connected": bool(connected),
                "token_hash": token_hash or previous.get("token_hash", ""),
                "message": message,
                "request_id": request_id or previous.get("request_id", ""),
                "slot": slot_num,
                "device_label": label,
                "board_mac": mac_val or previous.get("board_mac", ""),
                "updated_at": utc_now(),
            }
        )
        mcp_connection_states[key] = previous

        # Backward compatibility for legacy callers checking integer or string user_id
        if slot_num == 1 or connected:
            legacy_dict = dict(previous)
            mcp_connection_states[user_id] = legacy_dict  # type: ignore
            mcp_connection_states[str(user_id)] = legacy_dict
        elif not connected:
            # Check if any other slot is connected
            has_other = any(
                dict(mcp_connection_states.get(_slot_key(user_id, s), {})).get("connected")
                for s in (1, 2, 3) if s != slot_num
            )
            if not has_other:
                legacy_dict = dict(previous)
                legacy_dict["connected"] = False
                mcp_connection_states[user_id] = legacy_dict  # type: ignore
                mcp_connection_states[str(user_id)] = legacy_dict


def is_mcp_connected(owner_id: int, token_hash: str = "", slot: Optional[int] = None) -> bool:
    token_hash = normalize_token_hash(token_hash)
    user_id = int(owner_id)
    with mcp_state_lock:
        if slot is not None:
            state = dict(mcp_connection_states.get(_slot_key(user_id, slot), {}))
            if not state.get("connected"):
                return False
            active_hash = normalize_token_hash(str(state.get("token_hash", "")))
            return not token_hash or not active_hash or active_hash == token_hash

        # If token_hash provided, check matching slot
        if token_hash:
            for s in (1, 2, 3):
                state = dict(mcp_connection_states.get(_slot_key(user_id, s), {}))
                if state.get("connected"):
                    active_hash = normalize_token_hash(str(state.get("token_hash", "")))
                    if not active_hash or active_hash == token_hash:
                        return True
            return False

        # If neither slot nor hash, return True if ANY slot is connected
        for s in (1, 2, 3):
            state = dict(mcp_connection_states.get(_slot_key(user_id, s), {}))
            if state.get("connected"):
                return True
        return False


def current_mcp_token_hash(owner_id: Optional[int], slot: Optional[int] = None) -> str:
    if owner_id is None:
        return ""
    user_id = int(owner_id)
    with mcp_state_lock:
        if slot is not None:
            state = dict(mcp_connection_states.get(_slot_key(user_id, slot), {}))
            return normalize_token_hash(str(state.get("token_hash", "")))
        # Search for first connected slot
        for s in (1, 2, 3):
            state = dict(mcp_connection_states.get(_slot_key(user_id, s), {}))
            if state.get("connected") and state.get("token_hash"):
                return normalize_token_hash(str(state.get("token_hash", "")))
        # Fallback to slot 1
        state1 = dict(mcp_connection_states.get(_slot_key(user_id, 1), {}))
        return normalize_token_hash(str(state1.get("token_hash", "")))


def mcp_slots_payload(owner_id: int, store: Any) -> Dict[str, Any]:
    """Generates a complete multi-slot status payload for Slots 1, 2, and 3."""
    user_id = int(owner_id)
    user_tokens = []
    if hasattr(store, "list_user_xiaozhi_tokens"):
        try:
            user_tokens = store.list_user_xiaozhi_tokens(user_id)
        except Exception:
            pass

    token_map = {int(t.get("slot_number", 1)): t for t in user_tokens}

    slots_list = []
    any_connected = False
    total_saved = 0

    with mcp_state_lock:
        for s in (1, 2, 3):
            t_info = token_map.get(s)
            saved = bool(t_info)
            if saved:
                total_saved += 1
            t_hash = normalize_token_hash(t_info.get("token_hash", "")) if t_info else ""
            connected = is_mcp_connected(user_id, t_hash, slot=s)
            if connected:
                any_connected = True

            state = dict(mcp_connection_states.get(_slot_key(user_id, s), {}))
            label = (t_info.get("device_label") if t_info else "") or f"XiaoZhi {s}"

            if connected:
                status_text = f"Slot {s} ({label}) terhubung"
            elif saved:
                status_text = state.get("message") or f"Slot {s} tersimpan, menunggu bridge"
            else:
                status_text = f"Slot {s} belum dikonfigurasi"

            board_mac = (t_info.get("board_mac") or "").strip().upper() if t_info else ""
            is_locked = bool(board_mac)

            slots_list.append(
                {
                    "slot": s,
                    "label": label,
                    "saved": saved,
                    "connected": connected,
                    "board_mac": board_mac,
                    "is_locked": is_locked,
                    "preview": t_info.get("preview", "") if t_info else "",
                    "tokenHash": t_hash,
                    "statusText": status_text,
                    "updatedAt": state.get("updated_at") or (t_info.get("updated_at") if t_info else ""),
                }
            )

    return {
        "anyConnected": any_connected,
        "totalSaved": total_saved,
        "slots": slots_list,
    }


def mcp_status_payload(
    owner_id: int,
    *,
    token_saved: bool = False,
    token_preview: str = "",
    token_hash: str = "",
    slot: int = 1,
) -> Dict[str, Any]:
    user_id = int(owner_id)
    token_hash = normalize_token_hash(token_hash)
    connected = is_mcp_connected(user_id, token_hash, slot=slot)
    with mcp_state_lock:
        state = dict(mcp_connection_states.get(_slot_key(user_id, slot), {}))
    state_hash = normalize_token_hash(str(state.get("token_hash", "")))
    if token_hash and state_hash and state_hash != token_hash:
        state = {}
    if connected:
        status_text = f"Token MCP Slot {slot} terhubung"
    elif token_saved:
        status_text = state.get("message") or f"Slot {slot} tersimpan, menunggu bridge MCP"
    else:
        status_text = f"Slot {slot} belum tersimpan"
    return {
        "slot": slot,
        "connected": connected,
        "tokenSaved": bool(token_saved),
        "tokenPreview": token_preview,
        "tokenHash": token_hash,
        "statusText": status_text,
        "updatedAt": state.get("updated_at", ""),
    }


def signal_mcp_reload() -> None:
    if mcp_reload_event:
        mcp_reload_event.set()
        logger.info("MCP reload signal sent")


_store_ref = None


def set_store_ref(store):
    global _store_ref
    _store_ref = store


def record_mcp_tool_history_to_store(
    owner_id: int,
    tool_name: str,
    query: str,
    arguments: Dict[str, Any],
    response: Dict[str, Any],
    *,
    xiaozhi_answer: str = "",
    source: str = "mcp_tool",
    token_hash: str = "",
    slot_number: Optional[int] = None,
    device_mac: str = "",
    request_id: str = "",
) -> None:
    """Record MCP tool invocation to chat history with full Raw JSON Response and strict slot isolation."""
    if _store_ref is None:
        return
    if owner_id is None:
        return
    try:
        ans = xiaozhi_answer
        if not ans and isinstance(response, dict):
            ans = str(response.get("message") or response.get("result") or response.get("text") or "")
        
        # Retrieve context from current async bridge context if available
        slot_val = slot_number
        mac_val = device_mac
        req_val = request_id
        th = token_hash

        try:
            from xiaozhi.mcp.context import (
                mcp_active_slot_ctx,
                mcp_active_token_hash_ctx,
                mcp_active_mac_ctx,
                mcp_request_id_ctx,
            )
            if slot_val is None:
                slot_val = mcp_active_slot_ctx.get()
            if not th:
                th = mcp_active_token_hash_ctx.get()
            if not mac_val:
                mac_val = mcp_active_mac_ctx.get()
            if not req_val:
                req_val = mcp_request_id_ctx.get()
        except Exception:
            pass

        slot_num = int(slot_val or 1)
        if not th and hasattr(_store_ref, "get_xiaozhi_token_info"):
            try:
                t_info = _store_ref.get_xiaozhi_token_info(owner_id, slot=slot_num)
                if t_info:
                    th = t_info.get("token_hash", "")
            except Exception:
                pass

        _store_ref.add_chat_history(
            owner_id,
            source=source,
            tool_name=tool_name,
            user_message=query,
            xiaozhi_answer=ans or str(response),
            request_payload=arguments,
            response_payload=response,
            token_hash=th or "",
            slot_number=slot_num,
            device_mac=mac_val or "",
            request_id=req_val or "",
        )
    except Exception:
        logger.warning("Failed to record MCP tool history", exc_info=True)

