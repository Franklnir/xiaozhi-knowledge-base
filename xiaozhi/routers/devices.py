from typing import Optional
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
    is_mine = int(dev.get("owner_id", 0) or 0) == int(user["id"])
    is_protected = bool(dev.get("is_protected")) or (str(device_id).upper() == "E8:3D:C1:9B:B5:14")
    return {
        "success": True,
        "registered": True,
        "is_mine": is_mine,
        "is_protected": is_protected,
        "device_name": dev.get("device_name", ""),
        "message": "Device milik akun ini." if is_mine else "Device sudah terdaftar di akun lain.",
    }


@router.post("/api/devices/register")
async def register_device(request: Request):
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Akses ditolak: Hanya administrator yang memiliki izin untuk mendaftarkan atau mengubah MAC Address perangkat (Board ID)."
        )
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


@router.post("/api/devices/detach")
async def detach_device_endpoint(request: Request):
    """
    Memisahkan ID Board dari user saat user ingin ganti board atau putus tautan.
    Board ID dan riwayatnya tetap tersimpan aman di database.
    """
    user = require_user(request)
    store = get_store()
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    device_id = str(body.get("device_id", "")).strip()

    if device_id:
        dev = store.find_device_by_id(device_id)
        if not dev:
            raise HTTPException(status_code=404, detail="Device tidak ditemukan.")
        dev_owner = dev.get("owner_id")
        if user.get("role") != "admin" and dev_owner and int(dev_owner) != int(user["id"]):
            raise HTTPException(status_code=403, detail="Akses ditolak: Anda bukan pemilik perangkat ini.")
        detached = store.detach_device(device_id, owner_id=int(dev_owner) if dev_owner else None, reason=f"Diputus manual oleh user {user.get('username')}")
        return {
            "success": True,
            "detached": detached,
            "device_id": device_id,
            "message": f"Board {device_id} berhasil dipisahkan dari akun. Anda sekarang bebas menautkan board lain.",
        }
    else:
        # Pisahkan semua board yang sedang tertaut ke user ini
        detached_list = store.detach_user_devices(user["id"], reason=f"Semua board diputus manual oleh {user.get('username')}")
        return {
            "success": True,
            "detached_count": len(detached_list),
            "devices": detached_list,
            "message": "Semua board berhasil dipisahkan dari akun. Anda sekarang bebas menautkan board baru.",
        }


@router.post("/api/devices/delete")
async def delete_device(request: Request):
    user = require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Akses ditolak: Hanya administrator yang memiliki izin untuk menghapus MAC Address perangkat."
        )
    store = get_store()
    body = await request.json()
    device_id = str(body.get("device_id", "")).strip()
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id diperlukan.")

    # Proteksi Master Board E8:3D:C1:9B:B5:14 (JANGAN PERNAH DI HAPUS!)
    if device_id.upper() == "E8:3D:C1:9B:B5:14" or (hasattr(store, "is_device_protected") and store.is_device_protected(device_id)):
        store.detach_device(device_id, reason="Perangkat protected - ditautkan lepas (detach) bukan hapus")
        return {
            "success": True,
            "deleted": False,
            "detached": True,
            "is_protected": True,
            "message": f"Board ID {device_id} adalah perangkat terlindungi (protected) dan tidak pernah dihapus dari sistem. Perangkat berhasil dipisahkan dari user.",
        }

    deleted = store.delete_device(user["id"], device_id)
    return {"success": True, "deleted": deleted}


@router.get("/api/devices/history/{device_id}")
async def get_device_history_endpoint(device_id: str, request: Request):
    """
    Melihat riwayat lengkap board: pernah tertaut dengan user siapa saja, kapan tertaut, dan kapan terakhir tertaut.
    """
    user = require_user(request)
    store = get_store()
    history = store.get_board_binding_history(device_id) if hasattr(store, "get_board_binding_history") else []
    
    # Jika bukan admin, pastikan user pernah memiliki atau sedang memiliki board ini
    if user.get("role") != "admin":
        user_in_history = any(int(h.get("user_id", 0) or 0) == int(user["id"]) for h in history)
        if not user_in_history:
            raise HTTPException(status_code=403, detail="Tidak memiliki izin untuk melihat riwayat board ini.")

    is_protected = (device_id.upper() == "E8:3D:C1:9B:B5:14") or (hasattr(store, "is_device_protected") and store.is_device_protected(device_id))
    return {
        "success": True,
        "device_id": device_id,
        "is_protected": is_protected,
        "total_records": len(history),
        "history": history,
    }


@router.get("/api/devices/my-history")
async def get_my_device_history_endpoint(request: Request):
    """Riwayat semua board yang pernah tertaut ke akun pengguna saat ini."""
    user = require_user(request)
    store = get_store()
    history = store.get_user_board_history(user["id"]) if hasattr(store, "get_user_board_history") else []
    return {
        "success": True,
        "user_id": user["id"],
        "total_records": len(history),
        "history": history,
    }
