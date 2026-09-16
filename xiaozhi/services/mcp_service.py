import asyncio
import logging
from threading import RLock
from typing import Any, Dict, Optional

from xiaozhi.core.security import normalize_token_hash
from xiaozhi.core.utils import utc_now

logger = logging.getLogger("xiaozhi.mcp")

# ── MCP State ──────────────────────────────────────────────────────────────
mcp_state_lock = RLock()
mcp_connection_states: Dict[int, Dict[str, Any]] = {}
mcp_bridge_tasks: Dict[int, asyncio.Task] = {}
mcp_reload_event: Optional[asyncio.Event] = None


def clear_mcp_state(owner_id: int) -> None:
    user_id = int(owner_id)
    with mcp_state_lock:
        mcp_connection_states.pop(user_id, None)
    logger.info("MCP state cleared for user_id=%s", user_id)


def set_mcp_connection_state(owner_id: int, token_hash: str, *, connected: bool, message: str = "", request_id: str = "") -> None:
    user_id = int(owner_id)
    token_hash = normalize_token_hash(token_hash)
    with mcp_state_lock:
        previous = dict(mcp_connection_states.get(user_id, {}))
        previous.update(
            {
                "connected": bool(connected),
                "token_hash": token_hash or previous.get("token_hash", ""),
                "message": message,
                "request_id": request_id or previous.get("request_id", ""),
                "updated_at": utc_now(),
            }
        )
        mcp_connection_states[user_id] = previous


def is_mcp_connected(owner_id: int, token_hash: str = "") -> bool:
    token_hash = normalize_token_hash(token_hash)
    with mcp_state_lock:
        state = dict(mcp_connection_states.get(int(owner_id), {}))
    if not state.get("connected"):
        return False
    active_hash = normalize_token_hash(str(state.get("token_hash", "")))
    return not token_hash or not active_hash or active_hash == token_hash


def current_mcp_token_hash(owner_id: Optional[int]) -> str:
    if owner_id is None:
        return ""
    with mcp_state_lock:
        state = dict(mcp_connection_states.get(int(owner_id), {}))
    return normalize_token_hash(str(state.get("token_hash", "")))


def mcp_status_payload(
    owner_id: int,
    *,
    token_saved: bool = False,
    token_preview: str = "",
    token_hash: str = "",
) -> Dict[str, Any]:
    token_hash = normalize_token_hash(token_hash)
    connected = is_mcp_connected(owner_id, token_hash)
    with mcp_state_lock:
        state = dict(mcp_connection_states.get(int(owner_id), {}))
    state_hash = normalize_token_hash(str(state.get("token_hash", "")))
    if token_hash and state_hash and state_hash != token_hash:
        state = {}
    if connected:
        status_text = "Token MCP terhubung"
    elif token_saved:
        status_text = state.get("message") or "Endpoint tersimpan, menunggu bridge MCP"
    else:
        status_text = "Endpoint MCP belum tersimpan"
    return {
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
) -> None:
    """Record MCP tool invocation to chat history."""
    if _store_ref is None:
        return
    if owner_id is None:
        return
    try:
        ans = xiaozhi_answer
        if not ans and isinstance(response, dict):
            ans = str(response.get("message") or response.get("result") or response.get("text") or "")
        _store_ref.add_chat_history(
            owner_id,
            source=source,
            tool_name=tool_name,
            user_message=query,
            xiaozhi_answer=ans or str(response),
            request_payload=arguments,
            response_payload=response,
        )
    except Exception:
        logger.warning("Failed to record MCP tool history")
