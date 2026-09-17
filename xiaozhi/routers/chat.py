from fastapi import APIRouter, Query, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from xiaozhi.config import CHAT_HISTORY_DEFAULT_LIMIT, ALL_MCP_TOOLS_CATALOG
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    render,
    require_user,
    validate_csrf,
    redirect_with_message,
)
from xiaozhi.services.mcp_service import is_mcp_connected, mcp_status_payload
from xiaozhi.services.sse_service import stream_ai_chat
from xiaozhi.core.utils import utc_now

router = APIRouter()


@router.get("/chat", response_class=HTMLResponse)
async def ai_chat_page(request: Request):
    """Dedicated interactive AI Chat Assistant page with real-time SSE streaming."""
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_info.get("preview", "") if token_info else "",
        token_hash=token_hash,
    )
    # Quick suggestion prompts based on user's knowledge materials
    materials = store.list_materials(user["id"])
    suggestions = [m.get("title") for m in materials[:4] if m.get("title")]
    return render(
        request,
        "chat.html",
        {
            "user": user,
            "mcp_status": mcp_status,
            "suggestions": suggestions,
            "active_page": "chat",
        },
    )


@router.post("/api/chat/stream")
async def api_chat_stream(request: Request):
    """Real-time SSE token-by-token streaming endpoint for AI Chat."""
    user = require_user(request)
    store = get_store()

    # Extract query from JSON or Form
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        query = body.get("query", "")
    else:
        form = await request.form()
        query = form.get("query", "")

    return StreamingResponse(
        stream_ai_chat(query, user["id"], store, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/riwayat-chat", response_class=HTMLResponse)
async def chat_history_page(
    request: Request,
    q: str = Query("", max_length=120),
    limit: int = Query(CHAT_HISTORY_DEFAULT_LIMIT, ge=1, le=300),
    date: str = Query("", max_length=10),
    days: int = Query(5, ge=1, le=60),
):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    role = str(user.get("role") or "user").lower()
    if role != "admin" and not is_mcp_connected(user["id"]):
        return redirect_with_message("/login", "Endpoint WebSocket MCP wajib dihubungkan sebelum mengakses Riwayat Chat.")

    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    # Always get full date list (all time)
    date_list = store.chat_history_dates(user["id"], token_hash=token_hash)
    if not date_list and token_hash:
        date_list = store.chat_history_dates(user["id"], token_hash="")
    # If no date filter, fetch recent N days
    effective_date = date if date else ""
    histories = store.list_chat_history(user["id"], q, limit, token_hash=token_hash, date=effective_date)
    # If no results with token_hash, fallback to fetching all user chats
    if not histories and token_hash:
        histories = store.list_chat_history(user["id"], q, limit, token_hash="", date=effective_date)
    stats = store.chat_history_stats(user["id"], token_hash=token_hash, date=effective_date)
    if not stats.get("total") and token_hash:
        stats = store.chat_history_stats(user["id"], token_hash="", date=effective_date)
    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_info.get("preview", "") if token_info else "",
        token_hash=token_hash,
    )
    return render(
        request,
        "chat_history.html",
        {
            "user": user,
            "histories": histories,
            "stats": stats,
            "mcp_status": mcp_status,
            "query": q,
            "limit": limit,
            "active_date": date,
            "recent_days": days,
            "date_list": date_list,
            "message": request.query_params.get("message", ""),
            "active_page": "chat_history",
        },
    )


@router.get("/api/chat-history")
async def chat_history_api(
    request: Request,
    after_id: int = Query(0, ge=0),
    q: str = Query("", max_length=120),
    limit: int = Query(CHAT_HISTORY_DEFAULT_LIMIT, ge=1, le=300),
    date: str = Query("", max_length=10),
):
    user = require_user(request)
    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    histories = store.list_chat_history(user["id"], q, limit, token_hash=token_hash, date=date)
    if not histories and not q and token_hash:
        histories = store.list_chat_history(user["id"], "", limit, token_hash="", date=date)
    if after_id:
        recent_histories = histories[:5]
        new_histories = [item for item in histories if int(item.get("id", 0)) > int(after_id)]
        merged_by_id = {}
        for item in [*new_histories, *recent_histories]:
            merged_by_id[int(item.get("id", 0))] = item
        histories = sorted(merged_by_id.values(), key=lambda item: int(item.get("id", 0)), reverse=True)
    stats = store.chat_history_stats(user["id"], token_hash=token_hash, date=date)
    if not stats.get("total") and token_hash:
        stats = store.chat_history_stats(user["id"], token_hash="", date=date)
    date_list = store.chat_history_dates(user["id"], token_hash=token_hash)
    if not date_list and token_hash:
        date_list = store.chat_history_dates(user["id"], token_hash="")
    return {
        "success": True,
        "items": histories,
        "stats": stats,
        "date_list": date_list,
        "mcp_status": mcp_status_payload(
            user["id"],
            token_saved=bool(token_info),
            token_preview=token_info.get("preview", "") if token_info else "",
            token_hash=token_hash,
        ),
        "max_id": max((int(item.get("id", 0)) for item in histories), default=after_id),
        "server_time": utc_now(),
    }


@router.post("/riwayat-chat/clear")
async def clear_chat_history(request: Request, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    removed = store.clear_chat_history(user["id"])
    return redirect_with_message("/riwayat-chat", f"{removed} riwayat chat dihapus.")


@router.get("/dokumentasi", response_class=HTMLResponse)
async def documentation_page(request: Request):
    user = get_current_user(request)
    return render(
        request,
        "documentation.html",
        {
            "user": user,
            "active_page": "documentation",
        },
    )


@router.get("/profil", response_class=HTMLResponse)
async def profile_page(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    features = store.get_user_features(user["id"])
    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_info.get("preview", "") if token_info else "",
        token_hash=token_hash,
    )
    persona_analysis = store.get_user_persona_analysis(user["id"])
    device_mac = store.get_user_mac_address(user["id"]) if hasattr(store, "get_user_mac_address") else None

    # Load MCP tool toggles for this user
    toggles = store.get_mcp_tool_toggles(user["id"]) if hasattr(store, "get_mcp_tool_toggles") else {}
    tools_catalog = []
    total_active = 0
    total_disabled = 0
    for tool_info in ALL_MCP_TOOLS_CATALOG:
        tool_name = tool_info["name"]
        is_enabled = toggles.get(tool_name, True)
        if tool_name == "play_youtube_song" and not features.get("youtube_music", True):
            is_enabled = False
        if is_enabled:
            total_active += 1
        else:
            total_disabled += 1
        tools_catalog.append({
            **tool_info,
            "enabled": is_enabled,
        })

    return render(
        request,
        "profile.html",
        {
            "user": user,
            "features": features,
            "mcp_status": mcp_status,
            "persona_analysis": persona_analysis,
            "tools_catalog": tools_catalog,
            "total_tools": len(tools_catalog),
            "total_active": total_active,
            "total_disabled": total_disabled,
            "device_mac": device_mac,
            "active_page": "profile",
        },
    )


@router.post("/api/profile/scan-persona")
async def api_scan_persona(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    store = get_store()
    analysis = store.get_user_persona_analysis(user["id"])
    return JSONResponse({
        "success": True,
        "message": "Profil persona dan karakter berhasil dipindai ulang via RAG Vector.",
        "analysis": analysis
    })

