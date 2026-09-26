from fastapi import APIRouter, Query, Request, Form, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from xiaozhi.services.preset_approval_service import (
    get_user_status,
    is_user_authorized,
    get_decrypted_preset_binary,
    get_preset,
    list_presets,
    redeem_claim_code,
    PRESETS,
)
import asyncio
import json
import logging
from typing import Optional

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
from xiaozhi.services.community_chat_service import chat_hub
from xiaozhi.core.utils import utc_now

logger = logging.getLogger("xiaozhi.community_chat")

router = APIRouter()


@router.get("/chat", response_class=HTMLResponse)
async def community_chat_page(request: Request):
    """Interactive Community / Global Chat Room."""
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()

    # Fetch initial recent messages
    messages = store.list_community_chats(limit=100)
    # Mark messages as read for this user
    store.mark_chat_read(user["id"])

    return render(
        request,
        "chat.html",
        {
            "user": user,
            "messages": messages,
            "active_page": "chat",
        },
    )


@router.get("/api/community/chat/messages")
async def api_community_chat_messages(
    request: Request,
    before_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Retrieve community chat message history."""
    user = require_user(request)
    store = get_store()
    messages = store.list_community_chats(limit=limit, before_id=before_id)
    return JSONResponse({"success": True, "messages": messages})


@router.post("/api/community/chat/send")
async def api_community_chat_send(request: Request):
    """Send a text chat message with optional reply_to reference."""
    user = require_user(request)
    store = get_store()

    content = ""
    reply_to_id = None

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        content = body.get("content", "")
        reply_to_id = body.get("reply_to_id")
    else:
        form = await request.form()
        content = form.get("content", "")
        reply_to_id = form.get("reply_to_id")

    content = str(content or "").strip()
    if not content:
        return JSONResponse({"success": False, "message": "Pesan tidak boleh kosong."}, status_code=400)

    # Ensure reply_to_id is integer or None
    try:
        if reply_to_id:
            reply_to_id = int(reply_to_id)
        else:
            reply_to_id = None
    except (ValueError, TypeError):
        reply_to_id = None

    # Save to database store
    message_data = store.add_community_chat(
        user_id=user["id"],
        username=user["username"],
        role=user.get("role", "user"),
        content=content,
        msg_type="text",
        reply_to_id=reply_to_id,
    )

    # Update reader status for author
    store.mark_chat_read(user["id"], message_data["id"])

    # Broadcast via WebSocket to all connected clients
    await chat_hub.broadcast_new_message(message_data)

    return JSONResponse({"success": True, "message": message_data})


@router.post("/api/community/chat/delete/{message_id}")
async def api_community_chat_delete(request: Request, message_id: int):
    """Delete a community chat message (admin or message owner only)."""
    user = require_user(request)
    store = get_store()
    is_admin = (user.get("role") == "admin")

    success = store.delete_community_chat(message_id, user["id"], is_admin=is_admin)
    if not success:
        return JSONResponse({"success": False, "message": "Tidak dapat menghapus pesan ini."}, status_code=403)

    # Broadcast deletion
    await chat_hub.broadcast_delete_message(message_id)
    return JSONResponse({"success": True, "message_id": message_id})


@router.get("/api/community/chat/unread-count")
async def api_community_chat_unread_count(request: Request):
    """Get the unread chat count for the navbar badge."""
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": True, "unread_count": 0})
    store = get_store()
    unread_count = store.get_unread_chat_count(user["id"])
    return JSONResponse({"success": True, "unread_count": unread_count})


@router.post("/api/community/chat/read")
async def api_community_chat_read(request: Request):
    """Mark all community chat messages as read for current user."""
    user = require_user(request)
    store = get_store()
    store.mark_chat_read(user["id"])
    return JSONResponse({"success": True})


@router.websocket("/ws/community/chat")
async def community_chat_websocket(websocket: WebSocket):
    """Real-time WebSocket endpoint for instant community chat delivery."""
    user = get_current_user(websocket)
    if not user:
        await websocket.close(code=1008)
        return

    await chat_hub.connect(websocket)
    try:
        while True:
            # Keep alive and receive heartbeats/ping from client
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        chat_hub.disconnect(websocket)
    except Exception as e:
        logger.debug("WebSocket exception: %s", e)
        chat_hub.disconnect(websocket)


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

    # Default filter: Tampilkan percakapan HARI INI, atau tanggal percakapan terakhir jika hari ini belum ada obrolan
    from datetime import datetime
    today_str = datetime.now().strftime("%Y-%m-%d")

    has_chats_today = any(d.get("date") == today_str for d in date_list) if date_list else False

    if date == "all":
        effective_date = ""
        active_date_val = ""
    elif date:
        effective_date = date
        active_date_val = date
    else:
        # Smart Default: jika hari ini belum ada chat suara tapi ada riwayat tanggal sebelumnya,
        # otomatis tampilkan tanggal terakhir agar halaman tidak kosong.
        if not has_chats_today and date_list and isinstance(date_list[0], dict) and date_list[0].get("date"):
            effective_date = date_list[0]["date"]
            active_date_val = effective_date
        else:
            effective_date = today_str
            active_date_val = today_str

    histories = store.list_chat_history(user["id"], q, limit, token_hash=token_hash, date=effective_date)
    # If no results with token_hash, fallback to fetching without token_hash
    if not histories and token_hash:
        histories = store.list_chat_history(user["id"], q, limit, token_hash="", date=effective_date)

    stats = store.chat_history_stats(user["id"], token_hash=token_hash, date=effective_date)
    if not stats.get("total") and token_hash:
        stats = store.chat_history_stats(user["id"], token_hash="", date=effective_date)

    # Also compute all-time stats for reference
    all_stats = store.chat_history_stats(user["id"], token_hash=token_hash, date="")

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
            "all_stats": all_stats,
            "mcp_status": mcp_status,
            "query": q,
            "limit": limit,
            "active_date": active_date_val,
            "today_date": today_str,
            "recent_days": days,
            "date_list": date_list,
            "message": request.query_params.get("message", ""),
            "active_page": "chat_history",
        },
    )


@router.get("/api/chat-history/stream")
async def api_chat_history_stream(
    request: Request,
    date: str = Query("", max_length=10),
):
    """Real-time SSE token/chat event streaming endpoint for Chat History."""
    user = require_user(request)
    from xiaozhi.services.sse_service import stream_chat_history_events
    effective_date = "" if date == "all" else date
    return StreamingResponse(
        stream_chat_history_events(user["id"], request, active_date=effective_date),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
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
    req_date = "" if date == "all" else date
    histories = store.list_chat_history(user["id"], q, limit, token_hash=token_hash, date=req_date)
    if not histories and not q and token_hash:
        histories = store.list_chat_history(user["id"], "", limit, token_hash="", date=req_date)
    if after_id:
        recent_histories = histories[:5]
        new_histories = [item for item in histories if int(item.get("id", 0)) > int(after_id)]
        merged_by_id = {}
        for item in [*new_histories, *recent_histories]:
            merged_by_id[int(item.get("id", 0))] = item
        histories = sorted(merged_by_id.values(), key=lambda item: int(item.get("id", 0)), reverse=True)
    stats = store.chat_history_stats(user["id"], token_hash=token_hash, date=req_date)
    if not stats.get("total") and token_hash:
        stats = store.chat_history_stats(user["id"], token_hash="", date=req_date)
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


@router.post("/riwayat-chat/delete/{chat_id}")
async def delete_single_chat_history(request: Request, chat_id: int, csrf_token: str = Form("")):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    if not csrf_token:
        csrf_token = request.headers.get("X-CSRF-Token", "")
    try:
        validate_csrf(request, csrf_token, user)
    except Exception:
        pass
    success = store.delete_chat_history_item(user["id"], chat_id)
    accept = request.headers.get("accept", "")
    if "application/json" in accept or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return {"success": success, "message": "Riwayat chat berhasil dihapus." if success else "Gagal menghapus riwayat chat."}
    return redirect_with_message("/riwayat-chat", "Riwayat chat berhasil dihapus." if success else "Chat tidak ditemukan.")


@router.delete("/api/chat-history/{chat_id}")
async def delete_chat_history_api(request: Request, chat_id: int):
    user = require_user(request)
    store = get_store()
    success = store.delete_chat_history_item(user["id"], chat_id)
    return {"success": success, "message": "Riwayat chat berhasil dihapus." if success else "Item tidak ditemukan."}



@router.get("/web-flasher", response_class=HTMLResponse)
async def web_flasher_page(request: Request):
    user = get_current_user(request)
    all_presets = list_presets()
    default_preset_id = all_presets[0]["id"] if all_presets else "esp32s3_cam"
    preset_status = get_user_status(user, default_preset_id)
    return render(
        request,
        "web_flasher.html",
        {
            "user": user,
            "page": "web_flasher",
            "active_page": "web_flasher",
            "preset_status": preset_status,
            "presets": all_presets,
            "mcp_required": False,
        },
    )


@router.get("/api/v1/flasher/preset/{preset_id}/status")
async def get_preset_status_api(request: Request, preset_id: str):
    user = get_current_user(request)
    return get_user_status(user, preset_id)


@router.post("/api/v1/flasher/preset/claim")
async def claim_preset_code_api(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Silakan masuk terlebih dahulu untuk mengklaim kode lisensi.")
    try:
        body = await request.json()
        code = str(body.get("code") or "").strip()
    except Exception:
        code = ""
    res = redeem_claim_code(code, user)
    return res


@router.get("/api/v1/flasher/preset/{preset_id}/stream")
async def stream_preset_binary_api(request: Request, preset_id: str, version: Optional[str] = None):
    user = get_current_user(request)
    if not is_user_authorized(user, preset_id):
        raise HTTPException(
            status_code=403,
            detail="Akses Ditolak: Preset komersial ini membutuhkan izin lisensi dari Administrator atau klaim kode sekali pakai. Silakan masukkan kode lisensi atau ajukan izin akses.",
        )
    try:
        preset_info = get_preset(preset_id) or PRESETS.get(preset_id)
        if not preset_info:
            raise HTTPException(status_code=404, detail="Preset tidak ditemukan.")
        raw_bytes = get_decrypted_preset_binary(preset_id, version=version)
        fn = preset_info.get("filename", f"{preset_id}.bin")
        offset = preset_info.get("offset", "0x0")
        return Response(
            content=raw_bytes,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'inline; filename="{fn}"',
                "Content-Length": str(len(raw_bytes)),
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "X-Firmware-Offset": offset,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error streaming preset binary: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Gagal memuat binary preset: {str(exc)}")


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
async def profile_page(request: Request, mode: Optional[str] = "profile", sub: Optional[str] = "products"):
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

    # Load Marketplace Data for Mode Marketplace
    seller_products = []
    purchases = []
    financial_data = {
        "wallet": {"available_balance": 0},
        "summary": {"gross_sales": 0, "total_fees": 0, "net_sales": 0, "total_orders": 0},
        "ledger": [],
        "withdrawals": [],
        "orders": [],
        "minimum_withdrawal": 10000,
        "withdrawal_fee": 2500,
    }
    db_ready = False
    chat_unread_total = 0
    try:
        from xiaozhi.marketplace.deps import get_marketplace_repo, get_wallet_service, get_chat_service
        repo = get_marketplace_repo()
        wallet_service = get_wallet_service()
        chat_service = get_chat_service()
        seller_products = repo.get_seller_products(int(user["id"]))
        purchases = repo.get_buyer_purchases(int(user["id"]))
        financial_data = wallet_service.get_seller_financial_data(int(user["id"]))
        chat_unread_total = chat_service.get_total_unread_count(int(user["id"]))
        db_ready = repo.is_db_ready()
    except Exception as exc:
        logger.warning("Error loading marketplace data in profile: %s", exc)

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
            "active_mode": mode or "profile",
            "active_sub": sub or "products",
            "seller_products": seller_products,
            "purchases": purchases,
            "financial_data": financial_data,
            "chat_unread_total": chat_unread_total,
            "db_ready": db_ready,
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

