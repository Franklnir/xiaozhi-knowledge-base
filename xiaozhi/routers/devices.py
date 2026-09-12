from fastapi import APIRouter, HTTPException, Request

from xiaozhi.dependencies import get_store, require_user

router = APIRouter()


@router.get("/api/devices")
async def list_devices(request: Request):
    user = require_user(request)
    store = get_store()
    devices = store.list_registered_devices(user["id"])
    return {"success": True, "devices": devices}


@router.post("/api/devices/register")
async def register_device(request: Request):
    user = require_user(request)
    store = get_store()
    body = await request.json()
    device_name = str(body.get("name", "")).strip()
    device_type = str(body.get("type", "")).strip()
    if not device_name:
        raise HTTPException(status_code=400, detail="Nama perangkat diperlukan.")
    device = store.register_device(user["id"], name=device_name, device_type=device_type)
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
