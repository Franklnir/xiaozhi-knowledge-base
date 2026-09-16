"""
API v1 MCP endpoints for mobile and external clients.
Authenticated using JWT Bearer tokens.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from xiaozhi.dependencies import get_current_user, get_store
from xiaozhi.services.mcp_service import (
    clear_mcp_state,
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
    if not token.startswith("wss://") and not token.startswith("ws://"):
        raise HTTPException(
            status_code=400,
            detail="URL endpoint MCP harus diawali dengan wss:// atau ws://"
        )

    store = get_store()
    try:
        store.set_xiaozhi_token(user["id"], token)
        token_info = store.get_xiaozhi_token_info(user["id"])
        token_hash = token_info.get("token_hash", "") if token_info else ""
        set_mcp_connection_state(user["id"], token_hash, connected=False, message="Menghubungkan ke XiaoZhi...")
        
        # Cancel any previous bridge task for this user and signal reload
        user_task = mcp_bridge_tasks.pop(user["id"], None)
        if user_task and not user_task.done():
            user_task.cancel()
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
    clear_mcp_state(user["id"])
    user_task = mcp_bridge_tasks.pop(user["id"], None)
    if user_task and not user_task.done():
        user_task.cancel()
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
    set_mcp_connection_state(user["id"], token_hash, connected=False, message="Menghubungkan ulang ke XiaoZhi...")

    user_task = mcp_bridge_tasks.pop(user["id"], None)
    if user_task and not user_task.done():
        user_task.cancel()
    signal_mcp_reload()

    return SimpleResponse(success=True, message="Mencoba menghubungkan ulang MCP...")
