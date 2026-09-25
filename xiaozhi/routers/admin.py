import asyncio
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from xiaozhi.config import ALL_MCP_TOOLS_CATALOG
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    make_csrf_token,
    render,
    require_admin,
    validate_csrf,
    redirect_with_message,
)
from xiaozhi.services.mcp_service import (
    clear_mcp_state,
    is_mcp_connected,
    mcp_bridge_tasks,
    mcp_connection_states,
    mcp_state_lock,
    mcp_status_payload,
    set_mcp_connection_state,
    signal_mcp_reload,
)

from xiaozhi.services.playback_tracker import playback_tracker

logger = logging.getLogger("xiaozhi.admin")
router = APIRouter()

# Active admin websocket connections for real-time dashboard sync
_admin_ws_clients: Set[WebSocket] = set()


def get_admin_dashboard_snapshot() -> Dict[str, Any]:
    """Generate complete real-time snapshot of users, streams, and system metrics."""
    store = get_store()
    managed_users = store.list_admin_manageable_users()

    # Active YouTube Music streams
    active_streams = playback_tracker.get_active_sessions()
    active_user_map = {int(s["user_id"]): s for s in active_streams if s.get("user_id")}
    active_mac_map = {
        str(s["device_mac"]).replace(":", "").replace("-", "").strip().lower(): s
        for s in active_streams if s.get("device_mac") and not str(s.get("device_mac", "")).lower().startswith("esp32 board")
    }

    # Fetch today's activity (stream youtube & mcp tools called)
    today_activities = {}
    try:
        today_activities = store.get_today_users_activity()
    except Exception as e:
        logger.warning("Failed to fetch today activity: %s", e)

    # Attach YouTube active stream info and today's activity to each user
    for u in managed_users:
        uid = int(u["id"])
        stream = active_user_map.get(uid)
        if not stream and u.get("device_mac"):
            clean_mac = str(u["device_mac"]).replace(":", "").replace("-", "").strip().lower()
            stream = active_mac_map.get(clean_mac)
        u["youtube_stream"] = stream
        if stream:
            u["is_playing"] = True
            u["current_track"] = stream.get("title", "")

        user_act = dict(today_activities.get(uid, {
            "tools_count": 0,
            "youtube_count": 0,
            "tools_list": [],
            "last_tool": "",
            "last_activity_time": "",
            "last_message_preview": "",
        }))
        if stream or u.get("is_playing"):
            user_act["youtube_count"] = max(1, user_act.get("youtube_count", 0))

        has_stream_today = bool(user_act.get("youtube_count", 0) > 0 or u.get("is_playing") or stream)
        has_tools_today = bool(user_act.get("tools_count", 0) > 0)
        has_activity_today = has_stream_today or has_tools_today

        user_act["has_activity"] = has_activity_today
        user_act["has_stream"] = has_stream_today
        user_act["has_tools"] = has_tools_today
        u["activity_today"] = user_act

    totals = {
        "users": len(managed_users),
        "materials": sum(u.get("usage", {}).get("materials", 0) for u in managed_users),
        "live_apis": sum(u.get("usage", {}).get("live_apis", 0) for u in managed_users),
        "relay_rooms": sum(u.get("usage", {}).get("relay_rooms", 0) for u in managed_users),
        "youtube_active": len(active_streams),
        "users_active_today": sum(1 for u in managed_users if u.get("activity_today", {}).get("has_activity")),
        "users_tools_today": sum(1 for u in managed_users if u.get("activity_today", {}).get("has_tools")),
        "users_stream_today": sum(1 for u in managed_users if u.get("activity_today", {}).get("has_stream")),
    }

    return {
        "users": managed_users,
        "active_streams": active_streams,
        "total_active_streams": len(active_streams),
        "totals": totals,
    }


async def broadcast_admin_users_update():
    """Broadcast real-time user list and playback update to all connected admin websockets."""
    if not _admin_ws_clients:
        return
    snapshot = get_admin_dashboard_snapshot()
    stale = set()
    for ws in list(_admin_ws_clients):
        try:
            await ws.send_json({"type": "update", "data": snapshot})
        except Exception:
            stale.add(ws)
    for ws in stale:
        _admin_ws_clients.discard(ws)


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    admin = require_admin(request)
    snapshot = get_admin_dashboard_snapshot()

    return render(
        request,
        "admin.html",
        {
            "user": admin,
            "managed_users": snapshot["users"],
            "totals": snapshot["totals"],
            "active_streams": snapshot["active_streams"],
            "total_active_streams": snapshot["total_active_streams"],
            "csrf_token": make_csrf_token(admin),
            "message": request.query_params.get("message", ""),
            "active_page": "admin",
        },
    )


@router.post("/admin/users/{target_user_id}/limits")
async def admin_set_limits(
    request: Request,
    target_user_id: int,
    max_materials: str = Form(""),
    max_words_per_material: str = Form(""),
    max_live_apis: str = Form(""),
    max_relay_rooms: str = Form(""),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)
    try:
        store.set_user_limits(
            target_user_id,
            max_materials=max_materials,
            max_words_per_material=max_words_per_material,
            max_live_apis=max_live_apis,
            max_relay_rooms=max_relay_rooms,
        )
        return redirect_with_message("/admin", "Batas user berhasil diperbarui.")
    except ValueError as exc:
        return redirect_with_message("/admin", f"Gagal: {exc}")


@router.post("/admin/users/{target_user_id}/feature")
async def admin_toggle_feature(
    request: Request,
    target_user_id: int,
    feature: str = Form(...),
    enabled: str = Form("true"),
):
    admin = require_admin(request)
    store = get_store()
    is_enabled = enabled.lower() in {"true", "1", "on"}
    try:
        store.set_user_feature(target_user_id, feature, is_enabled)
        # If admin disables youtube_music, immediately abort active playback for this user
        if feature == "youtube_music" and not is_enabled:
            stopped = playback_tracker.stop_user_playback(target_user_id)
            if stopped:
                logger.info(f"Active YouTube playback stopped for user {target_user_id} due to feature disable.")
        # Broadcast updated user list to all connected admin WebSocket clients
        await broadcast_admin_users_update()
        return {"success": True, "feature": feature, "enabled": is_enabled}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/admin/api/users-data")
async def admin_api_users_data(request: Request):
    """REST endpoint for admin user data snapshot (used for initial load and fallback)."""
    require_admin(request)
    snapshot = get_admin_dashboard_snapshot()
    return {"success": True, **snapshot}


@router.websocket("/ws/admin/users")
async def admin_users_websocket(websocket: WebSocket):
    """Real-time WebSocket connection for live Admin User List & YouTube Music sync."""
    user = get_current_user(websocket)
    if not user or user.get("role") != "admin":
        await websocket.close(code=1008)
        return

    await websocket.accept()
    _admin_ws_clients.add(websocket)
    try:
        # Send initial snapshot immediately upon connection
        snapshot = get_admin_dashboard_snapshot()
        await websocket.send_json({"type": "init", "data": snapshot})

        while True:
            try:
                # Wait for any client ping or message; tick every 2.5s to keep active stream timers fresh
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=2.5)
                if msg == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg == "refresh":
                    snapshot = get_admin_dashboard_snapshot()
                    await websocket.send_json({"type": "update", "data": snapshot})
            except asyncio.TimeoutError:
                snapshot = get_admin_dashboard_snapshot()
                await websocket.send_json({"type": "tick", "data": snapshot})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _admin_ws_clients.discard(websocket)


@router.get("/admin/mcp-monitor", response_class=HTMLResponse)
async def admin_mcp_monitor(request: Request):
    admin = require_admin(request)
    store = get_store()
    csrf_token = make_csrf_token(admin)

    with mcp_state_lock:
        all_states = {uid: dict(state) for uid, state in mcp_connection_states.items()}

    managed_users = store.list_admin_manageable_users()
    user_lookup = {int(u["id"]): u for u in managed_users}

    # Get MCP settings for all users
    mcp_settings = store.get_all_mcp_settings_for_admin()
    blocked_lookup = {int(s["user_id"]): bool(s["mcp_blocked"]) for s in mcp_settings}

    connections = []
    for uid, state in all_states.items():
        user_info = user_lookup.get(uid, {})
        task = mcp_bridge_tasks.get(uid)
        connections.append({
            "user_id": uid,
            "username": user_info.get("username", f"user-{uid}"),
            "connected": state.get("connected", False),
            "message": state.get("message", ""),
            "request_id": state.get("request_id", ""),
            "updated_at": state.get("updated_at", ""),
            "bridge_running": task is not None and not task.done() if task else False,
            "mcp_blocked": blocked_lookup.get(uid, False),
        })

    stored_tokens = store.list_xiaozhi_tokens()
    stored_user_ids = {int(t["user_id"]) for t in stored_tokens}
    active_user_ids = {c["user_id"] for c in connections}
    for uid in stored_user_ids - active_user_ids:
        user_info = user_lookup.get(uid, {})
        connections.append({
            "user_id": uid,
            "username": user_info.get("username", f"user-{uid}"),
            "connected": False,
            "message": "Tidak aktif",
            "request_id": "",
            "updated_at": "",
            "bridge_running": False,
            "mcp_blocked": blocked_lookup.get(uid, False),
        })

    connections.sort(key=lambda c: (not c["connected"], c["username"]))
    total_active = sum(1 for c in connections if c["connected"])
    total_stored = len(stored_tokens)
    total_errors = sum(1 for c in connections if not c["connected"] and "Error" in c.get("message", ""))

    return render(
        request,
        "admin_mcp.html",
        {
            "user": admin,
            "csrf_token": csrf_token,
            "connections": connections,
            "total_active": total_active,
            "total_stored": total_stored,
            "total_errors": total_errors,
            "message": request.query_params.get("message", ""),
            "active_page": "admin_mcp",
        },
    )


@router.get("/admin/api/mcp/connections")
async def admin_api_mcp_connections(request: Request):
    admin = require_admin(request)
    store = get_store()

    with mcp_state_lock:
        all_states = {int(uid): dict(state) for uid, state in mcp_connection_states.items()}

    managed_users = store.list_admin_manageable_users()
    user_lookup = {int(u["id"]): u for u in managed_users}

    result = []
    for uid, state in all_states.items():
        user_info = user_lookup.get(uid, {})
        task = mcp_bridge_tasks.get(uid)
        result.append({
            "user_id": uid,
            "username": user_info.get("username", f"user-{uid}"),
            "connected": state.get("connected", False),
            "message": state.get("message", ""),
            "request_id": state.get("request_id", ""),
            "updated_at": state.get("updated_at", ""),
            "bridge_running": task is not None and not task.done() if task else False,
        })

    return {"success": True, "connections": result, "total_active": sum(1 for c in result if c["connected"])}


@router.post("/admin/api/mcp/disconnect/{target_user_id}")
async def admin_api_mcp_disconnect(request: Request, target_user_id: int, csrf_token: str = Form(...)):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)

    user_task = mcp_bridge_tasks.pop(target_user_id, None)
    if user_task and not user_task.done():
        user_task.cancel()

    deleted = store.delete_xiaozhi_token(target_user_id)
    clear_mcp_state(target_user_id)
    signal_mcp_reload()

    return {"success": True, "message": f"User {target_user_id} MCP disconnected.", "deleted": deleted}


@router.post("/admin/api/mcp/reconnect/{target_user_id}")
async def admin_api_mcp_reconnect(request: Request, target_user_id: int, csrf_token: str = Form(...)):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)

    token = store.get_xiaozhi_token(target_user_id)
    if not token:
        raise HTTPException(status_code=404, detail="User ini tidak memiliki MCP endpoint tersimpan.")

    user_task = mcp_bridge_tasks.pop(target_user_id, None)
    if user_task and not user_task.done():
        user_task.cancel()

    token_info = store.get_xiaozhi_token_info(target_user_id)
    token_hash = token_info.get("token_hash", "") if token_info else ""
    set_mcp_connection_state(target_user_id, token_hash, connected=False, message="Admin memaksa reconnect...")
    signal_mcp_reload()

    return {"success": True, "message": f"User {target_user_id} MCP reconnecting."}


@router.get("/admin/api/mcp/health")
async def admin_api_mcp_health(request: Request):
    admin = require_admin(request)
    store = get_store()

    with mcp_state_lock:
        all_states = {int(uid): dict(state) for uid, state in mcp_connection_states.items()}

    stored_tokens = store.list_xiaozhi_tokens()
    active_bridges = sum(1 for task in mcp_bridge_tasks.values() if not task.done())
    connected_count = sum(1 for s in all_states.values() if s.get("connected"))
    error_count = sum(1 for s in all_states.values() if not s.get("connected") and "Error" in s.get("message", ""))

    return {
        "success": True,
        "health": {
            "total_stored_endpoints": len(stored_tokens),
            "active_bridges": active_bridges,
            "connected": connected_count,
            "disconnected": len(all_states) - connected_count,
            "errors": error_count,
            "bridge_tasks": len(mcp_bridge_tasks),
        },
    }


# ── YouTube Active Playback Monitor ─────────────────────────────────────

@router.get("/admin/api/youtube/active-streams")
async def admin_api_youtube_active_streams(request: Request):
    require_admin(request)
    streams = playback_tracker.get_active_sessions()
    return {
        "success": True,
        "total_active": len(streams),
        "streams": streams,
    }


@router.post("/admin/api/youtube/stop/{session_id}")
async def admin_api_youtube_stop(request: Request, session_id: str):
    admin = require_admin(request)
    stopped = playback_tracker.stop_session(session_id)
    return {
        "success": stopped,
        "message": "Pemutaran berhasil dihentikan." if stopped else "Sesi pemutaran tidak ditemukan atau sudah selesai.",
    }


# ── MCP Block/Unblock ────────────────────────────────────────────────────

@router.post("/admin/api/mcp/block/{target_user_id}")
async def admin_api_mcp_block(request: Request, target_user_id: int, csrf_token: str = Form(...), reason: str = Form("")):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)

    # Block the user
    store.set_mcp_blocked(target_user_id, True, reason)

    # Disconnect if currently connected
    user_task = mcp_bridge_tasks.pop(target_user_id, None)
    if user_task and not user_task.done():
        user_task.cancel()

    clear_mcp_state(target_user_id)
    signal_mcp_reload()

    return {"success": True, "message": f"User {target_user_id} MCP diblokir."}


@router.post("/admin/api/mcp/unblock/{target_user_id}")
async def admin_api_mcp_unblock(request: Request, target_user_id: int, csrf_token: str = Form(...)):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)

    store.set_mcp_blocked(target_user_id, False)
    signal_mcp_reload()

    return {"success": True, "message": f"User {target_user_id} MCP diaktifkan kembali."}


@router.get("/admin/api/mcp/settings")
async def admin_api_mcp_settings(request: Request):
    admin = require_admin(request)
    store = get_store()

    settings = store.get_all_mcp_settings_for_admin()
    return {"success": True, "settings": settings}


# ── MCP Tool Toggles ─────────────────────────────────────────────────────

@router.get("/admin/api/mcp/tools/{target_user_id}")
async def admin_api_mcp_tools(request: Request, target_user_id: int):
    admin = require_admin(request)
    store = get_store()

    toggles = store.get_mcp_tool_toggles(target_user_id)

    tools_with_status = []
    for tool_info in ALL_MCP_TOOLS_CATALOG:
        tool_name = tool_info["name"]
        tools_with_status.append({
            "name": tool_name,
            "title": tool_info.get("title", tool_name),
            "category": tool_info.get("category", "general"),
            "category_label": tool_info.get("category_label", "Umum"),
            "icon": tool_info.get("icon", "🔧"),
            "description": tool_info.get("description", ""),
            "enabled": toggles.get(tool_name, True),  # Default True
        })

    return {
        "success": True,
        "user_id": target_user_id,
        "tools": tools_with_status,
        "total_tools": len(tools_with_status)
    }


@router.post("/admin/api/mcp/tools/{target_user_id}/toggle")
async def admin_api_mcp_tool_toggle(
    request: Request,
    target_user_id: int,
    tool_name: str = Form(...),
    enabled: str = Form("true"),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)

    is_enabled = enabled.lower() in {"true", "1", "on"}
    store.set_mcp_tool_toggle(target_user_id, tool_name, is_enabled)
    if tool_name == "play_youtube_song" and not is_enabled:
        playback_tracker.stop_user_playback(target_user_id)
    await broadcast_admin_users_update()
    signal_mcp_reload()
    return {"success": True, "tool": tool_name, "enabled": is_enabled}


# ── User Analytics ─────────────────────────────────────────────────────────

@router.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics(request: Request):
    admin = require_admin(request)
    store = get_store()

    managed_users = store.list_admin_manageable_users()
    total_users = len(managed_users)
    total_materials = sum(u["usage"]["materials"] for u in managed_users)
    total_apis = sum(u["usage"]["live_apis"] for u in managed_users)
    total_relays = sum(u["usage"]["relay_rooms"] for u in managed_users)

    # Per-user stats
    user_stats = []
    for u in managed_users:
        user_stats.append({
            "id": u["id"],
            "username": u["username"],
            "materials": u["usage"]["materials"],
            "apis": u["usage"]["live_apis"],
            "relays": u["usage"]["relay_rooms"],
            "mcp_status": u.get("mcp_status", {}).get("connected", False),
        })

    return render(
        request,
        "admin_analytics.html",
        {
            "user": admin,
            "total_users": total_users,
            "total_materials": total_materials,
            "total_apis": total_apis,
            "total_relays": total_relays,
            "user_stats": user_stats,
            "active_page": "admin_analytics",
        },
    )


@router.get("/admin/api/analytics")
async def admin_api_analytics(request: Request):
    admin = require_admin(request)
    store = get_store()

    managed_users = store.list_admin_manageable_users()
    return {
        "success": True,
        "analytics": {
            "total_users": len(managed_users),
            "total_materials": sum(u["usage"]["materials"] for u in managed_users),
            "total_apis": sum(u["usage"]["live_apis"] for u in managed_users),
            "total_relays": sum(u["usage"]["relay_rooms"] for u in managed_users),
            "users": [
                {
                    "id": u["id"],
                    "username": u["username"],
                    "usage": u["usage"],
                    "mcp_connected": u.get("mcp_status", {}).get("connected", False),
                }
                for u in managed_users
            ],
        },
    }


# ── Broadcast ──────────────────────────────────────────────────────────────

@router.post("/admin/broadcast")
async def admin_broadcast(
    request: Request,
    message: str = Form(...),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)

    if not message.strip():
        return redirect_with_message("/admin", "Pesan tidak boleh kosong.")

    # Store broadcast message
    managed_users = store.list_admin_manageable_users()
    sent_count = 0
    for u in managed_users:
        try:
            store.add_chat_history(
                u["id"],
                source="broadcast",
                tool_name="admin_broadcast",
                user_message="",
                xiaozhi_answer=f"[Broadcast dari Admin] {message}",
            )
            sent_count += 1
        except Exception:
            pass

    return redirect_with_message("/admin", f"Pesan broadcast terkirim ke {sent_count} user.")


@router.get("/admin/api/broadcast")
async def admin_api_broadcast_status(request: Request):
    admin = require_admin(request)
    store = get_store()

    # Get recent broadcasts
    managed_users = store.list_admin_manageable_users()
    return {
        "success": True,
        "total_users": len(managed_users),
        "message": "Gunakan POST /admin/broadcast untuk mengirim pesan.",
    }


# ── PDF Upload ─────────────────────────────────────────────────────────────

@router.post("/admin/upload-pdf")
async def admin_upload_pdf(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)

    try:
        form = await request.form()
        pdf_file = form.get("pdf_file")
        if not pdf_file:
            return redirect_with_message("/dashboard", "File PDF tidak ditemukan.")

        pdf_bytes = await pdf_file.read()

        from xiaozhi.services.pdf_service import create_material_from_pdf
        result = create_material_from_pdf(pdf_bytes, title, category, admin["id"])

        if not result["success"]:
            return redirect_with_message("/dashboard", f"Gagal: {result['error']}")

        store.add_material(
            admin["id"],
            result["title"],
            result["category"],
            result["content"],
            f"PDF: {result['pages']} halaman",
        )
        signal_mcp_reload()
        return redirect_with_message("/dashboard", f"PDF berhasil diupload ({result['pages']} halaman).")
    except Exception as e:
        return redirect_with_message("/dashboard", f"Gagal upload PDF: {str(e)[:100]}")


# ── URL Scraper ────────────────────────────────────────────────────────────

@router.post("/admin/scrape-url")
async def admin_scrape_url(
    request: Request,
    url: str = Form(...),
    title: str = Form(""),
    category: str = Form(""),
    csrf_token: str = Form(...),
):
    admin = require_admin(request)
    store = get_store()
    validate_csrf(request, csrf_token, admin)

    try:
        from xiaozhi.services.scraper_service import create_material_from_url
        result = create_material_from_url(url, title, category)

        if not result["success"]:
            return redirect_with_message("/dashboard", f"Gagal: {result['error']}")

        store.add_material(
            admin["id"],
            result["title"],
            result.get("category", "Materi Perkuliahan"),
            result["content"],
            f"URL: {url}",
        )
        signal_mcp_reload()
        return redirect_with_message("/dashboard", f"URL berhasil di-scrape: {result['title'][:50]}")
    except Exception as e:
        return redirect_with_message("/dashboard", f"Gagal scrape URL: {str(e)[:100]}")


@router.get("/api/v1/admin/users")
async def api_v1_admin_users(request: Request):
    """API endpoint for mobile app: get manageable users with real-time status."""
    admin = require_admin(request)
    store = get_store()
    users = store.list_admin_manageable_users(admin["username"])
    return {
        "success": True,
        "users": users,
        "total": len(users),
        "message": "Daftar pengguna berhasil dimuat."
    }


@router.get("/admin/api/logs/stream")
async def admin_api_logs_stream(request: Request):
    """Real-time SSE event stream for live admin console and MCP logging."""
    require_admin(request)
    from xiaozhi.services.sse_service import stream_admin_logs
    return StreamingResponse(
        stream_admin_logs(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


