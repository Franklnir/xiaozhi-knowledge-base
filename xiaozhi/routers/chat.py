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


@router.get("/riwayat-chat", response_class=HTMLResponse)
async def chat_history_page(
    request: Request,
    q: str = Query("", max_length=120),
    limit: int = Query(CHAT_HISTORY_DEFAULT_LIMIT, ge=1, le=300),
):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    histories = store.list_chat_history(user["id"], q, limit, token_hash=token_hash)
    stats = store.chat_history_stats(user["id"], token_hash=token_hash)
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
):
    user = require_user(request)
    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    histories = store.list_chat_history(user["id"], q, limit, token_hash=token_hash)
    if after_id:
        recent_histories = histories[:5]
        new_histories = [item for item in histories if int(item.get("id", 0)) > int(after_id)]
        merged_by_id = {}
        for item in [*new_histories, *recent_histories]:
            merged_by_id[int(item.get("id", 0))] = item
        histories = sorted(merged_by_id.values(), key=lambda item: int(item.get("id", 0)), reverse=True)
    stats = store.chat_history_stats(user["id"], token_hash=token_hash)
    return {
        "success": True,
        "items": histories,
        "stats": stats,
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
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    return render(
        request,
        "documentation.html",
        {
            "user": user,
            "active_page": "documentation",
        },
    )
