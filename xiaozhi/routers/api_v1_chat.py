"""
API v1 Chat, Profile, and Dashboard endpoints for mobile clients.
Authenticated using JWT Bearer tokens.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from xiaozhi.config import ALL_MCP_TOOLS_CATALOG, CHAT_HISTORY_DEFAULT_LIMIT
from xiaozhi.dependencies import get_current_user, get_store, require_mcp_connected_if_not_admin
from xiaozhi.services.mcp_service import is_mcp_connected, mcp_status_payload, signal_mcp_reload

router = APIRouter(prefix="/api/v1", tags=["API v1 Chat, Profile & Dashboard"])


# ── Response Models ────────────────────────────────────────────────────────

class ChatItem(BaseModel):
    id: Optional[int] = None
    role: str = ""
    user_message: Optional[str] = None
    xiaozhi_answer: Optional[str] = None
    tool_name: Optional[str] = None
    response_payload: Optional[Any] = None
    created_at: str = ""
    source: Optional[str] = None


class ChatHistoryResponse(BaseModel):
    success: bool = True
    total: int = 0
    items: List[Dict[str, Any]] = []
    date_list: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {}
    mcp_status: Dict[str, Any] = {}
    message: str = "OK"


class ProfileDataResponse(BaseModel):
    success: bool = True
    user: Dict[str, Any]
    persona_analysis: Optional[Dict[str, Any]] = None
    tools_catalog: List[Dict[str, Any]] = []
    tools_count: int = 0
    mcp_status: Dict[str, Any]
    message: str = "OK"


class DashboardDataResponse(BaseModel):
    success: bool = True
    data: Dict[str, Any]
    message: str = "OK"


class CategoryCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)


class SimpleActionResponse(BaseModel):
    success: bool = True
    message: str = "OK"


# ── Dashboard Endpoint ─────────────────────────────────────────────────────

@router.get("/dashboard", response_model=DashboardDataResponse)
async def get_dashboard_data(request: Request):
    """
    Get full dashboard data including stats, quota, categories, materials, and MCP status.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid atau telah kedaluwarsa.")
    require_mcp_connected_if_not_admin(request, user)

    store = get_store()
    categories = store.list_categories(user["id"])
    materials = store.list_materials(user["id"])
    quota = store.user_quota(user["id"])
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    token_preview = token_info.get("preview", "") if token_info else ""

    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_preview,
        token_hash=token_hash,
    )

    stats = {
        "total": len(materials),
        "tugas": sum(1 for m in materials if "tugas" in m.get("category", "").lower()),
        "pengumuman": sum(1 for m in materials if "pengumuman" in m.get("category", "").lower()),
        "materi": sum(1 for m in materials if "materi" in m.get("category", "").lower()),
    }

    device_mac = ""
    if hasattr(store, "get_user_mac_address"):
        try:
            device_mac = store.get_user_mac_address(user["id"]) or ""
        except Exception:
            pass

    return DashboardDataResponse(
        success=True,
        data={
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user.get("role", "user"),
                "ui_theme": user.get("ui_theme", "neo"),
                "created_at": user.get("created_at", ""),
                "device_mac": device_mac,
            },
            "stats": stats,
            "quota": quota,
            "mcp_status": mcp_status,
            "categories": categories,
            "materials": materials,
        },
        message="Data dashboard berhasil dimuat."
    )


# ── Category Management Endpoints ──────────────────────────────────────────

@router.get("/categories")
async def list_categories(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")
    store = get_store()
    categories = store.list_categories(user["id"])
    return {"success": True, "categories": categories}


@router.post("/categories", response_model=SimpleActionResponse)
async def create_category(body: CategoryCreateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")
    require_mcp_connected_if_not_admin(request, user)
    store = get_store()
    try:
        store.add_category(user["id"], body.name.strip())
        return SimpleActionResponse(success=True, message=f"Kategori '{body.name}' berhasil ditambahkan.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/categories/{cat_id}", response_model=SimpleActionResponse)
async def delete_category(cat_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")
    require_mcp_connected_if_not_admin(request, user)
    store = get_store()
    try:
        deleted = store.delete_category(user["id"], cat_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Kategori tidak ditemukan atau tidak dapat dihapus.")
        return SimpleActionResponse(success=True, message="Kategori berhasil dihapus.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ── Chat History Endpoints ─────────────────────────────────────────────────

@router.get("/chat/history", response_model=ChatHistoryResponse)
async def get_chat_history(
    request: Request,
    q: str = Query("", max_length=120),
    limit: int = Query(50, ge=1, le=200),
    date: str = Query("", max_length=10),
):
    """
    Get chat history list for the authenticated user with date list and stats.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid atau telah kedaluwarsa.")
    require_mcp_connected_if_not_admin(request, user)

    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    token_preview = token_info.get("preview", "") if token_info else ""

    histories = store.list_chat_history(user["id"], q, limit, token_hash=token_hash, date=date)
    if not histories and token_hash:
        histories = store.list_chat_history(user["id"], q, limit, token_hash="", date=date)

    stats = store.chat_history_stats(user["id"], token_hash=token_hash, date=date)
    if not stats.get("total") and token_hash:
        stats = store.chat_history_stats(user["id"], token_hash="", date=date)

    date_list = store.chat_history_dates(user["id"], token_hash=token_hash)
    if not date_list and token_hash:
        date_list = store.chat_history_dates(user["id"], token_hash="")

    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_preview,
        token_hash=token_hash,
    )

    return ChatHistoryResponse(
        success=True,
        total=stats.get("total", len(histories)),
        items=histories,
        date_list=date_list,
        stats=stats,
        mcp_status=mcp_status,
        message="Riwayat chat berhasil dimuat."
    )


@router.post("/chat/history/clear", response_model=SimpleActionResponse)
async def clear_chat_history_api(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")
    require_mcp_connected_if_not_admin(request, user)
    store = get_store()
    removed = store.clear_chat_history(user["id"])
    return SimpleActionResponse(success=True, message=f"{removed} riwayat chat berhasil dihapus.")


# ── Profile & AI Persona Endpoints ─────────────────────────────────────────

@router.get("/profile/data", response_model=ProfileDataResponse)
async def get_profile_data(request: Request):
    """
    Get full profile data including Persona RAG 1-100% metrics and 39 MCP tools catalog.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid atau telah kedaluwarsa.")

    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    token_preview = token_info.get("preview", "") if token_info else ""

    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_preview,
        token_hash=token_hash,
    )

    persona_analysis = store.get_user_persona_analysis(user["id"])
    features = store.get_user_features(user["id"]) if hasattr(store, "get_user_features") else {}
    toggles = store.get_mcp_tool_toggles(user["id"]) if hasattr(store, "get_mcp_tool_toggles") else {}

    tools_catalog = []
    for tool_info in ALL_MCP_TOOLS_CATALOG:
        tool_name = tool_info["name"]
        is_enabled = toggles.get(tool_name, True)
        if tool_name == "play_youtube_song" and not features.get("youtube_music", True):
            is_enabled = False
        tools_catalog.append({
            **tool_info,
            "enabled": is_enabled,
        })

    device_mac = ""
    if hasattr(store, "get_user_mac_address"):
        try:
            device_mac = store.get_user_mac_address(user["id"]) or ""
        except Exception:
            pass

    return ProfileDataResponse(
        success=True,
        user={
            "id": user["id"],
            "username": user["username"],
            "role": user.get("role", "user"),
            "ui_theme": user.get("ui_theme", "neo"),
            "created_at": user.get("created_at", ""),
            "google_id": user.get("google_id"),
            "google_email": user.get("google_email"),
            "registered_with_google": bool(user.get("registered_with_google", False)),
            "device_mac": device_mac,
        },
        persona_analysis=persona_analysis,
        tools_catalog=tools_catalog,
        tools_count=len(tools_catalog),
        mcp_status=mcp_status,
        message="Data profil berhasil dimuat."
    )


@router.post("/profile/scan-persona")
async def profile_scan_persona_api(request: Request):
    """
    Trigger AI Persona RAG Vector re-scan from chat history.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")
    store = get_store()
    analysis = store.get_user_persona_analysis(user["id"])
    return {
        "success": True,
        "message": "Profil persona berhasil dipindai ulang via RAG Vector.",
        "analysis": analysis,
    }
