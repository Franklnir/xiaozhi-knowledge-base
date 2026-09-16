from fastapi import APIRouter, Query, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse

from xiaozhi.config import CHAT_HISTORY_DEFAULT_LIMIT
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    render,
    require_user,
    validate_csrf,
    redirect_with_message,
)
from xiaozhi.services.mcp_service import mcp_status_payload
from xiaozhi.core.utils import utc_now

router = APIRouter()


def _recent_date_range(days: int = 5) -> str:
    """Return YYYY-MM-DD for N days ago."""
    from datetime import date, timedelta
    return (date.today() - timedelta(days=days)).isoformat()


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
    return render(
        request,
        "profile.html",
        {
            "user": user,
            "features": features,
            "mcp_status": mcp_status,
            "persona_analysis": persona_analysis,
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

