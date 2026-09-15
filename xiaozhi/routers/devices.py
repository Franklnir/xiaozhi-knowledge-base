from fastapi import APIRouter, HTTPException, Request

from xiaozhi.dependencies import get_store, require_user

router = APIRouter()


@router.get("/api/devices")
async def list_devices(request: Request):
    user = require_user(request)
    store = get_store()
    devices = store.list_registered_devices(user["id"])
    return {"success": True, "devices": devices}


@router.get("/api/devices/check/{device_id}")
async def check_device(device_id: str, request: Request):
    """Cek apakah device sudah terdaftar dan milik siapa."""
    store = get_store()
    dev = store.find_device_by_id(device_id)
    if not dev:
        return {"success": True, "registered": False, "message": "Device belum terdaftar."}
    user = require_user(request)
    is_mine = int(dev.get("owner_id", 0)) == int(user["id"])
    return {
        "success": True,
        "registered": True,
        "is_mine": is_mine,
        "device_name": dev.get("device_name", ""),
        "message": "Device milik akun ini." if is_mine else "Device sudah terdaftar di akun lain.",
    }


@router.post("/api/devices/register")
async def register_device(request: Request):
    user = require_user(request)
    store = get_store()
    body = await request.json()
    device_name = str(body.get("name", "")).strip()
    device_type = str(body.get("type", "")).strip()
    device_id = str(body.get("device_id", "") or body.get("mac", "") or device_name).strip()
    if not device_name:
        raise HTTPException(status_code=400, detail="Nama perangkat diperlukan.")
    try:
        device = store.register_device(user["id"], device_id=device_id, name=device_name, device_type=device_type)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"success": True, "device": device}


@router.post("/api/devices/delete")
async def delete_device(request: Request):
    user = require_user(request)
    store = get_store()
    body = await request.json()
    device_id = str(body.get("device_id", "")).strip()
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id diperlukan.")
    deleted = store.delete_device(user["id"], device_id)
    return {"success": True, "deleted": deleted}
