from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import JSONResponse
from typing import Optional

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
    mcp_slots_payload,
    set_mcp_connection_state,
    signal_mcp_reload,
)

router = APIRouter()


@router.post("/save_mcp")
async def save_mcp(
    request: Request,
    mcp_token: str = Form(...),
    csrf_token: str = Form(...),
    slot: int = Form(1),
    device_label: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Login required"}, status_code=401)
    store = get_store()
    validate_csrf(request, csrf_token, user)
    slot_num = int(slot or 1)
    if slot_num < 1 or slot_num > 3:
        return JSONResponse({"success": False, "detail": "Slot token hanya diizinkan antara 1 sampai 3."}, status_code=400)
    label_clean = (device_label or "").strip()[:60] or f"XiaoZhi {slot_num}"
    try:
        store.set_xiaozhi_token(user["id"], mcp_token, slot=slot_num, device_label=label_clean)
        token_info = store.get_xiaozhi_token_info(user["id"], slot=slot_num)
        token_hash = token_info.get("token_hash", "") if token_info else ""
        set_mcp_connection_state(
            user["id"],
            token_hash,
            connected=False,
            message=f"Menghubungkan Slot {slot_num} ({label_clean})...",
            slot=slot_num,
            device_label=label_clean,
        )
        task_key = f"{user['id']}:{slot_num}"
        user_task = mcp_bridge_tasks.pop(task_key, None)
        if user_task and not user_task.done():
            user_task.cancel()
        legacy_task = mcp_bridge_tasks.pop(user["id"], None)
        if legacy_task and not legacy_task.done():
            legacy_task.cancel()
        signal_mcp_reload()
        return JSONResponse({
            "success": True,
            "slot": slot_num,
            "message": f"Endpoint Slot {slot_num} ({label_clean}) berhasil disimpan. Menghubungkan ke XiaoZhi...",
        })
    except ValueError as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"success": False, "detail": f"Gagal menyimpan endpoint: {exc}"}, status_code=500)


@router.post("/delete_mcp")
async def delete_mcp_endpoint(
    request: Request,
    csrf_token: str = Form(...),
    slot: Optional[int] = Form(None),
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Login required"}, status_code=401)
    store = get_store()
    validate_csrf(request, csrf_token, user)
    
    if slot is not None:
        slot_num = int(slot)
        deleted = store.delete_xiaozhi_token(user["id"], slot=slot_num)
        clear_mcp_state(user["id"], slot=slot_num)
        task_key = f"{user['id']}:{slot_num}"
        user_task = mcp_bridge_tasks.pop(task_key, None)
        if user_task and not user_task.done():
            user_task.cancel()
        signal_mcp_reload()
        return JSONResponse({
            "success": True,
            "deleted": deleted,
            "slot": slot_num,
            "message": f"Endpoint Slot {slot_num} berhasil dihapus.",
        })
    else:
        deleted = store.delete_xiaozhi_token(user["id"])
        clear_mcp_state(user["id"])
        for s in (1, 2, 3):
            t = mcp_bridge_tasks.pop(f"{user['id']}:{s}", None)
            if t and not t.done():
                t.cancel()
        legacy_task = mcp_bridge_tasks.pop(user["id"], None)
        if legacy_task and not legacy_task.done():
            legacy_task.cancel()
        signal_mcp_reload()
        return JSONResponse({
            "success": True,
            "deleted": deleted,
            "message": "Semua endpoint MCP berhasil dihapus. Tautan Board ESP32 telah dipisahkan.",
        })


@router.post("/reconnect_mcp")
async def reconnect_mcp(
    request: Request,
    csrf_token: str = Form(...),
    slot: int = Form(1),
):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False, "detail": "Login required"}, status_code=401)
    store = get_store()
    validate_csrf(request, csrf_token, user)
    slot_num = int(slot or 1)
    if not store.get_xiaozhi_token(user["id"], slot=slot_num):
        return JSONResponse({"success": False, "detail": f"Endpoint Slot {slot_num} belum tersimpan."}, status_code=400)
    token_info = store.get_xiaozhi_token_info(user["id"], slot=slot_num)
    token_hash = token_info.get("token_hash", "") if token_info else ""
    label = token_info.get("device_label", f"XiaoZhi {slot_num}") if token_info else f"XiaoZhi {slot_num}"
    set_mcp_connection_state(
        user["id"],
        token_hash,
        connected=False,
        message=f"Memaksa reconnect Slot {slot_num}...",
        slot=slot_num,
        device_label=label,
    )
    task_key = f"{user['id']}:{slot_num}"
    user_task = mcp_bridge_tasks.pop(task_key, None)
    if user_task and not user_task.done():
        user_task.cancel()
    signal_mcp_reload()
    return JSONResponse({"success": True, "slot": slot_num, "message": f"Koneksi Slot {slot_num} ({label}) sedang di-refresh."})


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
        slot_label = f" (Slot {owner.get('slot_number', 1)}: {owner.get('device_label', 'XiaoZhi')})" if owner.get('slot_number') else ""
        return {
            "success": True,
            "found": True,
            "owner": {
                "user_id": owner["user_id"],
                "username": owner["username"],
                "role": owner["role"],
                "created_at": owner.get("created_at"),
                "slot_number": owner.get("slot_number", 1),
                "device_label": owner.get("device_label", "XiaoZhi"),
            },
            "message": f"Endpoint ini milik user: {owner['username']} (ID: {owner['user_id']}){slot_label}",
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
                "slot_number": t.get("slot_number", 1),
                "device_label": t.get("device_label", "XiaoZhi"),
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
    slot_num = owner.get("slot_number", 1)
    deleted = store.delete_xiaozhi_token_by_hash(token)
    clear_mcp_state(owner["user_id"], slot=slot_num)
    task_key = f"{owner['user_id']}:{slot_num}"
    user_task = mcp_bridge_tasks.pop(task_key, None)
    if user_task and not user_task.done():
        user_task.cancel()
    signal_mcp_reload()
    return {
        "success": True,
        "deleted": deleted,
        "message": f"Endpoint Slot {slot_num} milik {owner['username']} berhasil dihapus.",
    }


@router.get("/api/mcp/status")
async def mcp_status_api(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    store = get_store()
    multi_payload = mcp_slots_payload(user["id"], store)
    
    # Backward compatible primary status
    slot1_info = store.get_xiaozhi_token_info(user["id"], slot=1)
    legacy_status = mcp_status_payload(
        user["id"],
        token_saved=bool(slot1_info) or multi_payload["totalSaved"] > 0,
        token_preview=slot1_info.get("preview", "") if slot1_info else "",
        token_hash=slot1_info.get("token_hash", "") if slot1_info else "",
        slot=1,
    )
    if multi_payload["anyConnected"]:
        legacy_status["connected"] = True
        legacy_status["statusText"] = "XiaoZhi MCP terhubung"

    return {
        "success": True,
        "anyConnected": multi_payload["anyConnected"],
        "totalSaved": multi_payload["totalSaved"],
        "slots": multi_payload["slots"],
        "status": legacy_status,
    }
