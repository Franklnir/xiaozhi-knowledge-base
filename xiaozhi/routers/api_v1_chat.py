"""
API v1 Chat and Profile endpoints for mobile clients.
Authenticated using JWT Bearer tokens.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from xiaozhi.config import ALL_MCP_TOOLS_CATALOG, CHAT_HISTORY_DEFAULT_LIMIT
from xiaozhi.dependencies import get_current_user, get_store
from xiaozhi.services.mcp_service import is_mcp_connected, mcp_status_payload

router = APIRouter(prefix="/api/v1", tags=["API v1 Chat & Profile"])


# ── Response Models ────────────────────────────────────────────────────────

class ChatItem(BaseModel):
    id: Optional[int] = None
    role: str
    message: str
    tool_name: Optional[str] = None
    created_at: str


class ChatHistoryResponse(BaseModel):
    success: bool = True
    total: int = 0
    items: List[Dict[str, Any]] = []
    message: str = "OK"


class ProfileDataResponse(BaseModel):
    success: bool = True
    user: Dict[str, Any]
    persona_analysis: Optional[Dict[str, Any]] = None
    mcp_status: Dict[str, Any]
    tools_count: int = 0
    message: str = "OK"


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/chat/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    request: Request,
    q: str = Query("", max_length=120),
    limit: int = Query(50, ge=1, le=200),
    date: str = Query("", max_length=10),
):
    """
    Get chat history list for the authenticated user.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid atau telah kedaluwarsa.")

    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""

    histories = store.list_chat_history(user["id"], q, limit, token_hash=token_hash, date=date)
    if not histories and token_hash:
        histories = store.list_chat_history(user["id"], q, limit, token_hash="", date=date)

    stats = store.chat_history_stats(user["id"], token_hash=token_hash, date=date)

    return ChatHistoryResponse(
        success=True,
        total=stats.get("total", len(histories)),
        items=histories,
        message="Riwayat chat berhasil dimuat."
    )


@router.get("/profile/data", response_model=ProfileDataResponse)
async def get_profile_data(request: Request):
    """
    Get full profile data including Persona RAG 1-100% metrics and MCP tools catalog.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid atau telah kedaluwarsa.")

    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""

    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_info.get("preview", "") if token_info else "",
        token_hash=token_hash,
    )

    persona_analysis = store.get_user_persona_analysis(user["id"])
    tools_count = len(ALL_MCP_TOOLS_CATALOG)

    return ProfileDataResponse(
        success=True,
        user={
            "id": user["id"],
            "username": user["username"],
            "role": user.get("role", "user"),
            "ui_theme": user.get("ui_theme", "neo"),
            "created_at": user.get("created_at", ""),
        },
        persona_analysis=persona_analysis,
        mcp_status=mcp_status,
        tools_count=tools_count,
        message="Data profil berhasil dimuat."
    )
