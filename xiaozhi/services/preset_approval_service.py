import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("xiaozhi.preset_approval")

PRESETS = {
    "esp32s3_cam": {
        "id": "esp32s3_cam",
        "name": "ESP32-S3 N16R8 / CAM (Full Factory Merged 0x0)",
        "filename": "esp32_s3_n16r8_cam_full_factory.bin",
        "enc_rel_path": "xiaozhi/protected_assets/firmware/esp32_s3_n16r8_cam_full_factory.bin.enc",
        "offset": "0x0",
        "size_bytes": 9651352,
        "chip": "ESP32-S3",
        "description": "Full Factory Merged Binary untuk board baru kosong ESP32-S3 N16R8 dengan modul kamera & layar ST7789. Lengkap bootloader, partition table, OTA, app, suara & aset UI.",
    }
}

DATA_FILE = Path("data/preset_flasher_access.json")


def _get_aesgcm() -> AESGCM:
    secret = os.getenv("FIRMWARE_PRESET_SECRET", "xiaozhi-esp32-preset-secure-token-2026-v1")
    key = hashlib.sha256(secret.encode()).digest()
    return AESGCM(key)


def _load_data() -> Dict[str, Any]:
    if not DATA_FILE.exists():
        return {"requests": [], "granted_users": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "requests" not in data:
                data["requests"] = []
            if "granted_users" not in data:
                data["granted_users"] = {}
            return data
    except Exception as e:
        logger.error(f"Gagal memuat preset_flasher_access.json: {e}")
        return {"requests": [], "granted_users": {}}


def _save_data(data: Dict[str, Any]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(DATA_FILE)
    except Exception as e:
        logger.error(f"Gagal menyimpan preset_flasher_access.json: {e}")
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


def is_user_authorized(user: Optional[Dict[str, Any]], preset_id: str = "esp32s3_cam") -> bool:
    if not user:
        return False
    if str(user.get("role") or "").lower() == "admin":
        return True

    username = str(user.get("username") or "").lower()
    data = _load_data()

    # Check granted_users table
    granted = data.get("granted_users", {})
    if username in granted:
        user_grant = granted[username]
        if user_grant.get("preset_id") in (preset_id, "all"):
            return True

    # Check approved requests
    uid = int(user.get("id", 0))
    for req in data.get("requests", []):
        if int(req.get("user_id", 0)) == uid and req.get("preset_id") == preset_id:
            if req.get("status") == "APPROVED":
                return True

    return False


def get_user_status(user: Optional[Dict[str, Any]], preset_id: str = "esp32s3_cam") -> Dict[str, Any]:
    preset_info = PRESETS.get(preset_id)
    if not user:
        return {
            "authorized": False,
            "status": "ANONYMOUS",
            "preset": preset_info,
            "message": "Silakan masuk untuk mengakses preset berlisensi ini.",
        }

    if str(user.get("role") or "").lower() == "admin":
        return {
            "authorized": True,
            "status": "ADMIN",
            "preset": preset_info,
            "message": "Akses Penuh Administrator Resmi",
        }

    username = str(user.get("username") or "").lower()
    data = _load_data()
    granted = data.get("granted_users", {})
    if username in granted and granted[username].get("preset_id") in (preset_id, "all"):
        return {
            "authorized": True,
            "status": "APPROVED",
            "preset": preset_info,
            "message": "Izin akses preset telah disetujui Admin",
        }

    uid = int(user.get("id", 0))
    for req in reversed(data.get("requests", [])):
        if int(req.get("user_id", 0)) == uid and req.get("preset_id") == preset_id:
            status = req.get("status")
            if status == "APPROVED":
                return {
                    "authorized": True,
                    "status": "APPROVED",
                    "preset": preset_info,
                    "message": "Izin akses preset disetujui",
                }
            elif status == "PENDING":
                return {
                    "authorized": False,
                    "status": "PENDING",
                    "preset": preset_info,
                    "message": "Permintaan izin sedang menunggu persetujuan Admin",
                }
            elif status == "REJECTED":
                return {
                    "authorized": False,
                    "status": "REJECTED",
                    "preset": preset_info,
                    "message": f"Permintaan izin ditolak: {req.get('rejection_reason', 'Tidak memenuhi syarat')}",
                }

    return {
        "authorized": False,
        "status": "NONE",
        "preset": preset_info,
        "message": "Preset berlisensi ini memerlukan persetujuan Admin",
    }


def request_access(user: Dict[str, Any], preset_id: str = "esp32s3_cam", note: str = "") -> Dict[str, Any]:
    if is_user_authorized(user, preset_id):
        return {
            "success": True,
            "status": "ALREADY_AUTHORIZED",
            "message": "Anda sudah memiliki akses penuh ke preset ini.",
        }

    data = _load_data()
    uid = int(user.get("id", 0))
    username = str(user.get("username") or "")

    for req in data.get("requests", []):
        if int(req.get("user_id", 0)) == uid and req.get("preset_id") == preset_id and req.get("status") == "PENDING":
            return {
                "success": True,
                "status": "PENDING",
                "message": "Permintaan Anda sebelumnya sudah berada dalam antrean review Admin.",
            }

    req_id = str(uuid.uuid4())[:8]
    now_iso = datetime.now(timezone.utc).isoformat()
    new_req = {
        "id": req_id,
        "user_id": uid,
        "username": username,
        "preset_id": preset_id,
        "status": "PENDING",
        "note": note.strip(),
        "requested_at": now_iso,
        "reviewed_by": None,
        "reviewed_at": None,
        "rejection_reason": None,
    }
    data["requests"].insert(0, new_req)
    _save_data(data)
    return {
        "success": True,
        "status": "PENDING",
        "message": "Permintaan izin berhasil dikirim ke Admin. Silakan tunggu konfirmasi.",
        "request_id": req_id,
    }


def approve_request(request_id: str, admin_user: Dict[str, Any]) -> bool:
    data = _load_data()
    found = False
    admin_name = str(admin_user.get("username") or "admin")
    now_iso = datetime.now(timezone.utc).isoformat()

    for req in data.get("requests", []):
        if req.get("id") == request_id:
            req["status"] = "APPROVED"
            req["reviewed_by"] = admin_name
            req["reviewed_at"] = now_iso
            username = str(req.get("username") or "").lower()
            if username:
                data.setdefault("granted_users", {})[username] = {
                    "preset_id": req.get("preset_id", "esp32s3_cam"),
                    "granted_by": admin_name,
                    "granted_at": now_iso,
                }
            found = True
            break

    if found:
        _save_data(data)
    return found


def reject_request(request_id: str, admin_user: Dict[str, Any], reason: str = "") -> bool:
    data = _load_data()
    found = False
    admin_name = str(admin_user.get("username") or "admin")
    now_iso = datetime.now(timezone.utc).isoformat()

    for req in data.get("requests", []):
        if req.get("id") == request_id:
            req["status"] = "REJECTED"
            req["reviewed_by"] = admin_name
            req["reviewed_at"] = now_iso
            req["rejection_reason"] = reason.strip() or "Ditolak oleh admin."
            username = str(req.get("username") or "").lower()
            if username in data.get("granted_users", {}):
                del data["granted_users"][username]
            found = True
            break

    if found:
        _save_data(data)
    return found


def grant_access_direct(username: str, admin_user: Dict[str, Any], preset_id: str = "esp32s3_cam") -> bool:
    username = username.strip().lower()
    if not username:
        return False
    data = _load_data()
    admin_name = str(admin_user.get("username") or "admin")
    now_iso = datetime.now(timezone.utc).isoformat()

    data.setdefault("granted_users", {})[username] = {
        "preset_id": preset_id,
        "granted_by": admin_name,
        "granted_at": now_iso,
    }
    for req in data.get("requests", []):
        if str(req.get("username") or "").lower() == username and req.get("preset_id") == preset_id:
            req["status"] = "APPROVED"
            req["reviewed_by"] = admin_name
            req["reviewed_at"] = now_iso

    _save_data(data)
    return True


def revoke_access(username: str, admin_user: Dict[str, Any], preset_id: str = "esp32s3_cam") -> bool:
    username = username.strip().lower()
    data = _load_data()
    changed = False
    if username in data.get("granted_users", {}):
        del data["granted_users"][username]
        changed = True

    for req in data.get("requests", []):
        if str(req.get("username") or "").lower() == username and req.get("preset_id") == preset_id:
            req["status"] = "REVOKED"
            changed = True

    if changed:
        _save_data(data)
    return changed


def list_all_requests() -> List[Dict[str, Any]]:
    data = _load_data()
    return data.get("requests", [])


def list_granted_users() -> Dict[str, Any]:
    data = _load_data()
    return data.get("granted_users", {})


def get_decrypted_preset_binary(preset_id: str = "esp32s3_cam") -> bytes:
    preset_info = PRESETS.get(preset_id)
    if not preset_info:
        raise ValueError(f"Preset tidak ditemukan: {preset_id}")

    enc_path = Path(preset_info["enc_rel_path"])
    if not enc_path.exists():
        raise FileNotFoundError(f"File terenkripsi tidak ditemukan di server: {enc_path}")

    aesgcm = _get_aesgcm()
    enc_data = enc_path.read_bytes()
    nonce = enc_data[:12]
    ciphertext = enc_data[12:]
    raw_binary = aesgcm.decrypt(nonce, ciphertext, None)
    return raw_binary
