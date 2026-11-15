"""
API v1 MCP endpoints for mobile and external clients.
Authenticated using JWT Bearer tokens.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from xiaozhi.dependencies import get_current_user, get_store
from xiaozhi.mcp.bridge import reset_bridge_failure, validate_mcp_ws_url
from xiaozhi.services.mcp_service import (
    cancel_mcp_bridges,
    clear_mcp_state,
    get_mcp_connection_restriction,
    is_mcp_connected,
    mcp_bridge_tasks,
    mcp_status_payload,
    set_mcp_connection_state,
    signal_mcp_reload,
)

router = APIRouter(prefix="/api/v1/mcp", tags=["API v1 MCP"])


class SaveMcpRequest(BaseModel):
    mcp_token: str = Field(..., min_length=5, max_length=1000)


class McpStatusResponse(BaseModel):
    success: bool = True
    connected: bool = False
    status_text: str = ""
    token_preview: str = ""
    token_saved: bool = False
    message: str = "OK"


class SimpleResponse(BaseModel):
    success: bool = True
    message: str = "OK"


@router.get("/status", response_model=McpStatusResponse)
async def get_mcp_status(request: Request):
    """
    Get real-time MCP connection status for current user.
    Requires Authorization: Bearer <token>
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid atau telah kedaluwarsa.")

    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_saved = bool(token_info)
    token_preview = token_info.get("preview", "") if token_info else ""
    token_hash = token_info.get("token_hash", "") if token_info else ""

    status_data = mcp_status_payload(
        user["id"],
        token_saved=token_saved,
        token_preview=token_preview,
        token_hash=token_hash,
    )

    return McpStatusResponse(
        success=True,
        connected=bool(status_data.get("connected", False)),
        status_text=str(status_data.get("status_text", "")),
        token_preview=str(status_data.get("token_preview", "")),
        token_saved=token_saved,
        message="Status MCP berhasil dimuat."
    )


@router.post("/save", response_model=SimpleResponse)
async def save_mcp_token(body: SaveMcpRequest, request: Request):
    """
    Save MCP WebSocket endpoint/token for current user and initiate connection.
    Requires Authorization: Bearer <token>
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid atau telah kedaluwarsa.")

    token = body.mcp_token.strip()
    valid, message = validate_mcp_ws_url(token)
    if not valid:
        raise HTTPException(status_code=400, detail=message)

    store = get_store()
    restriction = get_mcp_connection_restriction(store, user["id"], 1)
    if restriction:
        raise HTTPException(status_code=403, detail=restriction)
    try:
        if hasattr(store, "detach_user_devices"):
            store.detach_user_devices(user["id"], reason="MCP endpoint diperbarui oleh user")
        store.set_xiaozhi_token(user["id"], token)
        token_info = store.get_xiaozhi_token_info(user["id"])
        token_hash = token_info.get("token_hash", "") if token_info else ""
        set_mcp_connection_state(user["id"], token_hash, connected=False, message="Menghubungkan ke XiaoZhi...")
        
        # Cancel any previous bridge task for this user and signal reload
        task_key = f"{user['id']}:1"
        user_task = mcp_bridge_tasks.pop(task_key, None)
        if user_task and not user_task.done():
            user_task.cancel()
        reset_bridge_failure(task_key)
        signal_mcp_reload()

        return SimpleResponse(
            success=True,
            message="Endpoint MCP tersimpan. Menghubungkan ke bridge..."
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan endpoint: {exc}")


@router.post("/delete", response_model=SimpleResponse)
async def delete_mcp_token(request: Request):
    """
    Delete MCP token for current user.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")

    store = get_store()
    store.delete_xiaozhi_token(user["id"])
    if hasattr(store, "detach_user_devices"):
        store.detach_user_devices(user["id"], reason="MCP diputuskan/dihapus oleh user")
    clear_mcp_state(user["id"])
    cancel_mcp_bridges(user["id"])
    signal_mcp_reload()

    return SimpleResponse(success=True, message="Endpoint MCP berhasil dihapus.")


@router.post("/reconnect", response_model=SimpleResponse)
async def reconnect_mcp(request: Request):
    """
    Reconnect MCP WebSocket bridge for current user.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")

    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    if not token_info:
        raise HTTPException(status_code=400, detail="Belum ada endpoint MCP tersimpan.")

    token_hash = token_info.get("token_hash", "")
    slot = int(token_info.get("slot_number") or 1)
    restriction = get_mcp_connection_restriction(store, user["id"], slot)
    if restriction:
        raise HTTPException(status_code=403, detail=restriction)
    set_mcp_connection_state(user["id"], token_hash, connected=False, message="Menghubungkan ulang ke XiaoZhi...", slot=slot)

    task_key = f"{user['id']}:{slot}"
    user_task = mcp_bridge_tasks.pop(task_key, None)
    if user_task and not user_task.done():
        user_task.cancel()
    reset_bridge_failure(task_key)
    signal_mcp_reload()

    return SimpleResponse(success=True, message="Mencoba menghubungkan ulang MCP...")
