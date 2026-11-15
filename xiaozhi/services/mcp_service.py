import asyncio
import logging
from threading import RLock
from typing import Any, Dict, Optional

from xiaozhi.core.security import normalize_token_hash
from xiaozhi.core.utils import utc_now

logger = logging.getLogger("xiaozhi.mcp")


def get_mcp_connection_restriction(store, owner_id: int, slot: int) -> str:
    """Use the same account restrictions for manual connection entry points."""
    if hasattr(store, "is_mcp_blocked") and store.is_mcp_blocked(owner_id) is True:
        return "Koneksi MCP akun ini diblokir oleh admin."
    if hasattr(store, "get_user_access_plus"):
        access = store.get_user_access_plus(owner_id)
        if slot > 1 and access.get("mcp_multislot_allowed") is False:
            return "Slot tambahan dibatasi oleh admin melalui Akses Plus."
        if slot in (access.get("disabled_mcp_slots") or ()):
            return "Slot MCP ini dinonaktifkan oleh admin."
    token_info = store.get_xiaozhi_token_info(owner_id, slot=slot)
    if token_info and token_info.get("is_active") is False:
        return "Slot MCP ini dinonaktifkan oleh admin."
    return ""

# ── MCP State (Maksimal 3 Slot per Akun) ─────────────────────────────────
mcp_state_lock = RLock()
mcp_connection_states: Dict[str, Dict[str, Any]] = {}
mcp_bridge_tasks: Dict[str, asyncio.Task] = {}
mcp_reload_event: Optional[asyncio.Event] = None
_mcp_reload_loop: Optional[asyncio.AbstractEventLoop] = None
_MCP_FAILURE_REASONS = {
    "invalid_endpoint": "Format endpoint MCP tidak valid.",
    "unauthorized": "Token MCP ditolak (HTTP 401). Salin ulang endpoint dari konsol XiaoZhi.",
    "no_upgrade": "Endpoint menjawab HTTP 200 tanpa upgrade WebSocket.",
    "handshake": "Server tujuan menolak koneksi WebSocket.",
    "closed": "Koneksi WebSocket terputus.",
    "network": "Koneksi jaringan MCP gagal.",
    "connection": "Koneksi MCP gagal.",
}


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
    failure_code: str = "",
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
        if token_hash and previous.get("token_hash") != token_hash:
            previous = {}
        updated_at = utc_now()
        previous.update(
            {
                "connected": bool(connected),
                "token_hash": token_hash or previous.get("token_hash", ""),
                "message": message,
                "request_id": request_id or previous.get("request_id", ""),
                "slot": slot_num,
                "device_label": label,
                "board_mac": mac_val or previous.get("board_mac", ""),
                "updated_at": updated_at,
                "failure_code": failure_code if not connected else "",
                "last_connected_at": updated_at if connected else previous.get("last_connected_at"),
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

    access_plus = store.get_user_access_plus(user_id) if hasattr(store, "get_user_access_plus") else {}
    multislot_allowed = access_plus.get("mcp_multislot_allowed", True)

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

            is_slot_restricted = (s in (2, 3)) and not multislot_allowed
            is_active = bool(t_info.get("is_active", True)) if t_info else True
            if is_slot_restricted:
                is_active = False
                status_text = f"Slot {s} dinonaktifkan oleh Admin (Akses Plus)"
            elif connected:
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
                    "is_active": is_active,
                    "is_restricted": is_slot_restricted,
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


def get_mcp_slot_diagnostics(owner_id: int, store: Any, slot: int = 0) -> Dict[str, Any]:
    """Return owner-scoped status without credentials or raw bridge messages."""
    tokens = {int(info["slot_number"]): info for info in store.list_user_xiaozhi_tokens(owner_id)}
    access_plus = store.get_user_access_plus(owner_id)
    blocked = store.is_mcp_blocked(owner_id) if hasattr(store, "is_mcp_blocked") else False
    slots = []
    with mcp_state_lock:
        for number in ((slot,) if slot else (1, 2, 3)):
            token = tokens.get(number)
            token_hash = normalize_token_hash(token.get("token_hash", "")) if token else ""
            state = mcp_connection_states.get(_slot_key(owner_id, number), {})
            if not token_hash or state.get("token_hash") != token_hash:
                state = {}
            failure = state.get("failure_code")
            restricted = number > 1 and not access_plus.get("mcp_multislot_allowed", True)
            if blocked or restricted or (token and not token.get("is_active", True)):
                status, reason = "disabled", "Akses MCP untuk slot ini dinonaktifkan."
            elif not token:
                status, reason = "not_configured", None
            elif state.get("connected"):
                status, reason = "connected", None
            else:
                status, reason = "disconnected", _MCP_FAILURE_REASONS.get(failure)
            slots.append({
                "slot": number,
                "status": status,
                "last_connected_at": state.get("last_connected_at"),
                "failure_reason": reason,
            })
    return {"success": True, "slots": slots}


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


def init_mcp_reload() -> None:
    global mcp_reload_event, _mcp_reload_loop
    mcp_reload_event = asyncio.Event()
    _mcp_reload_loop = asyncio.get_running_loop()


def clear_mcp_reload() -> None:
    global mcp_reload_event, _mcp_reload_loop
    mcp_reload_event = None
    _mcp_reload_loop = None


def cancel_mcp_bridges(owner_id: int) -> None:
    for key in (owner_id, *(_slot_key(owner_id, slot) for slot in (1, 2, 3))):
        task = mcp_bridge_tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()


def signal_mcp_reload() -> None:
    event = mcp_reload_event
    loop = _mcp_reload_loop
    if event is None:
        return
    try:
        caller_loop = asyncio.get_running_loop()
    except RuntimeError:
        caller_loop = None
    if loop is None or loop is caller_loop:
        event.set()
    elif loop.is_running():
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass  # Application shutdown.


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

