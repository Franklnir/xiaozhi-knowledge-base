from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse

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
