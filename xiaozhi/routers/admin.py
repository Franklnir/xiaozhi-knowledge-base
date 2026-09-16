from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse

from xiaozhi.config import ALL_MCP_TOOLS_CATALOG
from xiaozhi.dependencies import (
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

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    admin = require_admin(request)
    store = get_store()
    managed_users = store.list_admin_manageable_users()
    totals = {
        "users": len(managed_users),
        "materials": sum(u["usage"]["materials"] for u in managed_users),
        "live_apis": sum(u["usage"]["live_apis"] for u in managed_users),
        "relay_rooms": sum(u["usage"]["relay_rooms"] for u in managed_users),
    }
    return render(
        request,
        "admin.html",
        {
            "user": admin,
            "managed_users": managed_users,
            "totals": totals,
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
    try:
        store.set_user_feature(target_user_id, feature, enabled.lower() in {"true", "1", "on"})
        return {"success": True, "feature": feature, "enabled": enabled.lower() in {"true", "1", "on"}}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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

    store.set_mcp_tool_toggle(target_user_id, tool_name, enabled.lower() in {"true", "1", "on"})
    return {"success": True, "tool": tool_name, "enabled": enabled.lower() in {"true", "1", "on"}}


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
