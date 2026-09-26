from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import JSONResponse

from xiaozhi.core.security import mask_secret, normalize_token_hash, xiaozhi_token_hash
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    validate_csrf,
    redirect_with_message,
)
from xiaozhi.services.mcp_service import (
    clear_mcp_state,
    is_mcp_connected,
    mcp_bridge_tasks,
    mcp_status_payload,
    set_mcp_connection_state,
    signal_mcp_reload,
)

router = APIRouter()


@router.post("/save_mcp")
async def save_mcp(
    request: Request,
    mcp_token: str = Form(...),
    csrf_token: str = Form(...),
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Login required"}, status_code=401)
    store = get_store()
    validate_csrf(request, csrf_token, user)
    try:
        store.set_xiaozhi_token(user["id"], mcp_token)
        token_info = store.get_xiaozhi_token_info(user["id"])
        token_hash = token_info.get("token_hash", "") if token_info else ""
        set_mcp_connection_state(user["id"], token_hash, connected=False, message="Menghubungkan ke XiaoZhi...")
        user_task = mcp_bridge_tasks.pop(user["id"], None)
        if user_task and not user_task.done():
            user_task.cancel()
        signal_mcp_reload()
        return JSONResponse({"success": True, "message": "Endpoint disimpan. Menghubungkan..."})
    except ValueError as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=400)
    except Exception:
        return JSONResponse({"success": False, "detail": "Gagal menyimpan endpoint."}, status_code=500)


@router.post("/delete_mcp")
async def delete_mcp_endpoint(request: Request, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Login required"}, status_code=401)
    store = get_store()
    validate_csrf(request, csrf_token, user)
    deleted = store.delete_xiaozhi_token(user["id"])
    clear_mcp_state(user["id"])
    user_task = mcp_bridge_tasks.pop(user["id"], None)
    if user_task and not user_task.done():
        user_task.cancel()
    signal_mcp_reload()
    return JSONResponse({"success": True, "deleted": deleted, "message": "Endpoint dihapus. Tautan Board ESP32 berhasil dipisahkan (riwayat board tetap tersimpan)."})


@router.post("/reconnect_mcp")
async def reconnect_mcp(request: Request, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Login required"}, status_code=401)
    store = get_store()
    validate_csrf(request, csrf_token, user)
    if not store.get_xiaozhi_token(user["id"]):
        return JSONResponse({"success": False, "detail": "Endpoint belum tersimpan."}, status_code=400)
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_hash = token_info.get("token_hash", "") if token_info else ""
    set_mcp_connection_state(user["id"], token_hash, connected=False, message="Memaksa reconnect...")
    user_task = mcp_bridge_tasks.pop(user["id"], None)
    if user_task and not user_task.done():
        user_task.cancel()
    signal_mcp_reload()
    return redirect_with_message("/dashboard", "Koneksi Xiaozhi sedang di-refresh.")


@router.post("/api/mcp/check")
async def check_mcp_endpoint(request: Request):
    body = await request.json()
    token = str(body.get("token", "")).strip()
    if not token or not token.startswith("wss://"):
        raise HTTPException(status_code=400, detail="Endpoint harus diawali wss://")
    store = get_store()

    # Try exact match first
    owner = store.find_user_by_mcp_token(token)
    if owner:
        return {
            "success": True,
            "found": True,
            "owner": {
                "user_id": owner["user_id"],
                "username": owner["username"],
                "role": owner["role"],
                "created_at": owner["created_at"],
            },
            "message": f"Endpoint ini milik user: {owner['username']} (ID: {owner['user_id']})",
        }

    # If not found, check all stored tokens for partial match (for debugging)
    all_tokens = store.list_xiaozhi_tokens()
    token_base = token.split("?")[0] if "?" in token else token

    similar_tokens = []
    for t in all_tokens:
        stored_token = t.get("token", "")
        if stored_token and stored_token.startswith(token_base[:30]):
            similar_tokens.append({
                "user_id": t["user_id"],
                "token_preview": stored_token[:50] + "..." if len(stored_token) > 50 else stored_token,
            })

    if similar_tokens:
        return {
            "success": True,
            "found": False,
            "similar": similar_tokens,
            "message": "Endpoint tidak cocok persis, tapi ditemukan token mirip. Pastikan URL lengkap benar.",
        }

    return {
        "success": True,
        "found": False,
        "total_endpoints": len(all_tokens),
        "message": "Endpoint ini belum terdaftar di user manapun.",
    }


@router.post("/api/mcp/delete-by-token")
async def delete_mcp_by_token(request: Request):
    body = await request.json()
    token = str(body.get("token", "")).strip()
    if not token or not token.startswith("wss://"):
        raise HTTPException(status_code=400, detail="Endpoint harus diawali wss://")
    store = get_store()
    owner = store.find_user_by_mcp_token(token)
    if not owner:
        return {"success": False, "message": "Endpoint tidak ditemukan di database."}
    deleted = store.delete_xiaozhi_token_by_hash(token)
    clear_mcp_state(owner["user_id"])
    user_task = mcp_bridge_tasks.pop(owner["user_id"], None)
    if user_task and not user_task.done():
        user_task.cancel()
    signal_mcp_reload()
    return {
        "success": True,
        "deleted": deleted,
        "message": f"Endpoint milik {owner['username']} berhasil dihapus dan tautan board telah dipisahkan.",
    }


@router.get("/api/mcp/status")
async def mcp_status_api(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    status = mcp_status_payload(
        user["id"],
        token_saved=bool(token_info),
        token_preview=token_info.get("preview", "") if token_info else "",
        token_hash=token_info.get("token_hash", "") if token_info else "",
    )
    return {"success": True, "status": status}
