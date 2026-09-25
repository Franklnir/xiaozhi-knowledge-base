import hashlib
import json
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from xiaozhi.marketplace.storage import storage_service

logger = logging.getLogger("xiaozhi.preset_approval")

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

# Preset bawaan awal pabrik (Fallback / Seed)
DEFAULT_PRESETS: Dict[str, Dict[str, Any]] = {
    "esp32s3_cam": {
        "id": "esp32s3_cam",
        "title": "ESP32-S3 N16R8 / CAM (Full Factory Merged 0x0)",
        "name": "ESP32-S3 N16R8 / CAM (Full Factory Merged 0x0)",
        "filename": "esp32_s3_n16r8_cam_full_factory.bin",
        "enc_rel_path": "protected_assets/firmware/esp32_s3_n16r8_cam_full_factory.bin.enc",
        "offset": "0x0",
        "size_bytes": 9651352,
        "chip": "ESP32-S3",
        "description": "Full Factory Merged Binary untuk board baru kosong ESP32-S3 N16R8 dengan modul kamera & layar ST7789. Lengkap bootloader, partition table, OTA, app, suara & aset UI.",
        "active_version": "v001",
        "versions": [
            {
                "version": "v001",
                "filename": "esp32_s3_n16r8_cam_full_factory.bin",
                "storage_bucket": "local-private",
                "storage_key": "protected_assets/firmware/esp32_s3_n16r8_cam_full_factory.bin.enc",
                "size_bytes": 9651352,
                "sha256": "43928a47fb8427f79fbc5009a933f721532f86aa5f818b248a31ea41829e7436",
                "changelog": "Rilis awal Full Factory Merged",
                "uploaded_at": "2026-09-20T00:00:00+00:00",
                "uploaded_by": "system",
            }
        ],
    }
}

# Mapping global kompatibilitas
PRESETS = DEFAULT_PRESETS


def _get_data_file() -> Path:
    p1 = PROJECT_ROOT / "data" / "preset_flasher_access.json"
    if p1.parent.exists():
        return p1
    p2 = Path("data/preset_flasher_access.json")
    p2.parent.mkdir(parents=True, exist_ok=True)
    return p2


def _get_aesgcm() -> AESGCM:
    secret = os.getenv("FIRMWARE_PRESET_SECRET", "xiaozhi-esp32-preset-secure-token-2026-v1")
    key = hashlib.sha256(secret.encode()).digest()
    return AESGCM(key)


def _load_data() -> Dict[str, Any]:
    data_file = _get_data_file()
    if not data_file.exists():
        return {
            "requests": [],
            "granted_users": {},
            "presets": dict(DEFAULT_PRESETS),
            "claim_codes": [],
        }
    try:
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "requests" not in data:
                data["requests"] = []
            if "granted_users" not in data:
                data["granted_users"] = {}
            if "presets" not in data:
                data["presets"] = dict(DEFAULT_PRESETS)
            else:
                # Merge default presets if not present
                for k, v in DEFAULT_PRESETS.items():
                    if k not in data["presets"]:
                        data["presets"][k] = v
            if "claim_codes" not in data:
                data["claim_codes"] = []
            return data
    except Exception as e:
        logger.error(f"Gagal memuat preset_flasher_access.json: {e}")
        return {
            "requests": [],
            "granted_users": {},
            "presets": dict(DEFAULT_PRESETS),
            "claim_codes": [],
        }


def _save_data(data: Dict[str, Any]) -> None:
    data_file = _get_data_file()
    data_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = data_file.with_suffix(".tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_file.replace(data_file)
    except Exception as e:
        logger.error(f"Gagal menyimpan preset_flasher_access.json: {e}")
        if temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


def ensure_default_presets_in_bucket() -> None:
    """
    Jika server memiliki koneksi Object Storage S3/R2 aktif, otomatis upload
    file terenkripsi dari preset bawaan ke S3 private bucket agar tidak membebani VPS.
    """
    if not storage_service.has_s3:
        return
    data = _load_data()
    changed = False
    presets = data.get("presets", {})
    for pid, p in presets.items():
        versions = p.get("versions", [])
        for v in versions:
            if v.get("storage_bucket") == "local-private":
                candidates = [
                    BASE_DIR / (p.get("enc_rel_path") or ""),
                    BASE_DIR / "protected_assets" / "firmware" / (v.get("filename", "") + ".enc"),
                    BASE_DIR / "protected_assets" / "firmware" / "esp32_s3_n16r8_cam_full_factory.bin.enc",
                    PROJECT_ROOT / "xiaozhi" / (p.get("enc_rel_path") or ""),
                ]
                for c in candidates:
                    if c.exists() and c.is_file():
                        try:
                            enc_bytes = c.read_bytes()
                            b_name, s_key = storage_service.upload_encrypted_preset(
                                pid, v.get("version", "v001"), enc_bytes
                            )
                            v["storage_bucket"] = b_name
                            v["storage_key"] = s_key
                            changed = True
                            logger.info(
                                f"Otomatis menyinkronkan preset bawaan '{pid}' versi '{v.get('version')}' ke bucket S3 '{b_name}/{s_key}'."
                            )
                        except Exception as exc:
                            logger.warning(f"Gagal sinkronisasi preset bawaan '{pid}' ke S3: {exc}")
                        break
    if changed:
        _save_data(data)


def list_presets() -> List[Dict[str, Any]]:
    """Daftar seluruh preset yang terdaftar, diurutkan berdasarkan nama."""
    ensure_default_presets_in_bucket()
    data = _load_data()
    presets_dict = data.get("presets", {})
    return list(presets_dict.values())


def get_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    """Ambil detail metadata preset berdasarkan ID."""
    clean_id = str(preset_id).strip().lower()
    data = _load_data()
    return data.get("presets", {}).get(clean_id)


def _clean_slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower())
    return re.sub(r"_+", "_", cleaned).strip("_")


def save_or_update_preset(
    preset_id: str,
    title: str,
    chip: str = "ESP32-S3",
    offset: str = "0x0",
    description: str = "",
    file_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    admin_user: Optional[Dict[str, Any]] = None,
    changelog: str = "",
) -> Dict[str, Any]:
    """
    Tambah preset baru atau update preset yang sudah ada.
    Jika file_bytes disertakan pada preset yang sudah ada, nomor versi akan otomatis
    naik (v001 -> v002 -> v003) dan disimpan ke bucket S3 secara terenkripsi AES-GCM.
    """
    clean_id = _clean_slug(preset_id)
    if not clean_id:
        clean_id = f"preset_{uuid.uuid4().hex[:6]}"

    clean_title = (title or "").strip() or clean_id
    clean_chip = (chip or "ESP32-S3").strip()
    clean_offset = (offset or "0x0").strip()
    clean_desc = (description or "").strip()
    admin_name = str((admin_user or {}).get("username") or "admin")
    now_iso = datetime.now(timezone.utc).isoformat()

    data = _load_data()
    presets = data.setdefault("presets", {})

    existing = presets.get(clean_id)

    if existing:
        # UPDATE PRESET YANG SUDAH ADA
        existing["title"] = clean_title
        existing["name"] = clean_title
        existing["chip"] = clean_chip
        existing["offset"] = clean_offset
        existing["description"] = clean_desc
        existing["updated_at"] = now_iso

        versions = existing.setdefault("versions", [])

        if file_bytes and len(file_bytes) > 0:
            # Hitung versi berikutnya (v001, v002, dst.)
            max_ver_num = 0
            for v in versions:
                v_str = str(v.get("version", ""))
                match = re.search(r"(\d+)", v_str)
                if match:
                    max_ver_num = max(max_ver_num, int(match.group(1)))

            next_ver_num = max(max_ver_num + 1, 1)
            next_version_code = f"v{next_ver_num:03d}"

            # Enkripsi file binary menggunakan AES-256-GCM
            aesgcm = _get_aesgcm()
            nonce = secrets.token_bytes(12)
            ciphertext = aesgcm.encrypt(nonce, file_bytes, None)
            enc_bytes = nonce + ciphertext

            # Upload ke bucket Object Storage (S3 / R2 / local fallback)
            bucket_name, storage_key = storage_service.upload_encrypted_preset(
                clean_id, next_version_code, enc_bytes
            )

            version_record = {
                "version": next_version_code,
                "filename": filename or f"{clean_id}_{next_version_code}.bin",
                "storage_bucket": bucket_name,
                "storage_key": storage_key,
                "size_bytes": len(file_bytes),
                "sha256": hashlib.sha256(file_bytes).hexdigest(),
                "changelog": changelog.strip() or f"Pembaruan firmware versi {next_version_code}",
                "uploaded_at": now_iso,
                "uploaded_by": admin_name,
            }

            versions.append(version_record)
            existing["active_version"] = next_version_code
            existing["size_bytes"] = len(file_bytes)
            existing["filename"] = version_record["filename"]

        presets[clean_id] = existing
        _save_data(data)
        logger.info(f"Preset '{clean_id}' berhasil diperbarui ke versi {existing.get('active_version')}.")
        return existing

    else:
        # TAMBAH PRESET BARU
        init_version = "v001"
        versions = []
        size_bytes = 0
        default_fn = filename or f"{clean_id}_{init_version}.bin"

        if file_bytes and len(file_bytes) > 0:
            # Enkripsi file binary
            aesgcm = _get_aesgcm()
            nonce = secrets.token_bytes(12)
            ciphertext = aesgcm.encrypt(nonce, file_bytes, None)
            enc_bytes = nonce + ciphertext

            # Upload ke bucket
            bucket_name, storage_key = storage_service.upload_encrypted_preset(
                clean_id, init_version, enc_bytes
            )
            size_bytes = len(file_bytes)

            version_record = {
                "version": init_version,
                "filename": default_fn,
                "storage_bucket": bucket_name,
                "storage_key": storage_key,
                "size_bytes": size_bytes,
                "sha256": hashlib.sha256(file_bytes).hexdigest(),
                "changelog": changelog.strip() or "Rilis perdana preset firmware v001",
                "uploaded_at": now_iso,
                "uploaded_by": admin_name,
            }
            versions.append(version_record)

        new_preset = {
            "id": clean_id,
            "title": clean_title,
            "name": clean_title,
            "chip": clean_chip,
            "offset": clean_offset,
            "description": clean_desc,
            "filename": default_fn,
            "size_bytes": size_bytes,
            "active_version": init_version if versions else "v000",
            "created_at": now_iso,
            "updated_at": now_iso,
            "created_by": admin_name,
            "versions": versions,
        }

        presets[clean_id] = new_preset
        _save_data(data)
        logger.info(f"Preset baru '{clean_id}' berhasil dibuat dengan versi {new_preset['active_version']}.")
        return new_preset


# ─────────────────────────────────────────────────────────────────────────────
# SISTEM KODE LISENSI SEKALI PAKAI (ONE-TIME CLAIM CODES)
# ─────────────────────────────────────────────────────────────────────────────

def generate_claim_code(
    preset_id: str,
    note: str = "",
    admin_user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate kode lisensi unik yang hanya bisa digunakan 1 kali oleh user mana pun.
    Format: XZ-PRESET-XXXX-YYYY
    """
    clean_preset_id = str(preset_id).strip().lower()
    data = _load_data()
    claim_codes = data.setdefault("claim_codes", [])

    admin_name = str((admin_user or {}).get("username") or "admin")
    now_iso = datetime.now(timezone.utc).isoformat()

    # Generate token acak yang mudah dibaca namun kuat
    part1 = secrets.token_hex(2).upper()
    part2 = secrets.token_hex(2).upper()
    code_str = f"XZ-{clean_preset_id[:6].upper()}-{part1}-{part2}"

    code_record = {
        "id": str(uuid.uuid4())[:8],
        "code": code_str,
        "preset_id": clean_preset_id,
        "note": note.strip(),
        "is_used": False,
        "created_by": admin_name,
        "created_at": now_iso,
        "claimed_by": None,
        "claimed_user_id": None,
        "claimed_at": None,
    }

    claim_codes.insert(0, code_record)
    _save_data(data)
    logger.info(f"Kode lisensi sekali pakai '{code_str}' dibuat untuk preset '{clean_preset_id}'.")
    return code_record


def redeem_claim_code(raw_code: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Klaim kode lisensi sekali pakai.
    Jika kode valid dan belum pernah dipakai, langsung berikan izin akses ke user
    dan tandai kode sebagai HANGUS/TERPAKAI (tidak bisa dipakai lagi).
    """
    if not user:
        return {"success": False, "message": "Silakan login terlebih dahulu untuk mengklaim kode lisensi."}

    clean_code = str(raw_code or "").strip().upper()
    if not clean_code:
        return {"success": False, "message": "Kode lisensi tidak boleh kosong."}

    username = str(user.get("username") or "").lower()
    uid = int(user.get("id", 0))
    now_iso = datetime.now(timezone.utc).isoformat()

    data = _load_data()
    claim_codes = data.get("claim_codes", [])

    matched = None
    for item in claim_codes:
        if str(item.get("code", "")).upper() == clean_code:
            matched = item
            break

    if not matched:
        return {
            "success": False,
            "message": "Kode lisensi tidak valid atau salah ketik. Periksa kembali format kode Anda.",
        }

    if matched.get("is_used"):
        claimed_by = matched.get("claimed_by", "pengguna lain")
        claimed_at = str(matched.get("claimed_at") or "")[:10]
        return {
            "success": False,
            "message": f"Ditolak: Kode lisensi ini sudah pernah diklaim oleh '{claimed_by}' pada {claimed_at}. 1 kode hanya dapat digunakan 1 kali.",
        }

    # Buka kunci lisensi dan tandai kode hangus
    matched["is_used"] = True
    matched["claimed_by"] = username
    matched["claimed_user_id"] = uid
    matched["claimed_at"] = now_iso

    preset_id = matched.get("preset_id", "esp32s3_cam")

    # Berikan hak akses ke granted_users
    granted = data.setdefault("granted_users", {})
    granted[username] = {
        "preset_id": preset_id,
        "granted_by": f"code:{clean_code}",
        "granted_at": now_iso,
    }

    _save_data(data)
    logger.info(f"User '{username}' berhasil mengklaim kode '{clean_code}' untuk preset '{preset_id}'.")

    return {
        "success": True,
        "message": f"🎉 Selamat! Kode lisensi valid. Akses resmi ke preset '{preset_id}' kini terbuka 🔓.",
        "preset_id": preset_id,
    }


def list_claim_codes(preset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Daftar seluruh kode lisensi sekali pakai."""
    data = _load_data()
    codes = data.get("claim_codes", [])
    if preset_id and preset_id != "all":
        clean_p = preset_id.strip().lower()
        return [c for c in codes if c.get("preset_id") == clean_p]
    return codes


def delete_claim_code(code_id: str, admin_user: Dict[str, Any]) -> bool:
    """Hapus kode klaim (hanya untuk kode yang belum pernah dipakai)."""
    data = _load_data()
    codes = data.get("claim_codes", [])
    found = False
    new_codes = []
    for c in codes:
        if c.get("id") == code_id and not c.get("is_used"):
            found = True
        else:
            new_codes.append(c)
    if found:
        data["claim_codes"] = new_codes
        _save_data(data)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# PENGECEKAN HAK AKSES & STATUS LISENSI
# ─────────────────────────────────────────────────────────────────────────────

def is_user_authorized(user: Optional[Dict[str, Any]], preset_id: str = "esp32s3_cam") -> bool:
    if not user:
        return False
    if str(user.get("role") or "").lower() == "admin":
        return True

    username = str(user.get("username") or "").lower()
    data = _load_data()

    # Cek tabel granted_users (akses langsung dari admin atau melalui klaim kode lisensi)
    granted = data.get("granted_users", {})
    if username in granted:
        user_grant = granted[username]
        if user_grant.get("preset_id") in (preset_id, "all"):
            return True

    return False


def get_user_status(user: Optional[Dict[str, Any]], preset_id: str = "esp32s3_cam") -> Dict[str, Any]:
    data = _load_data()
    presets_dict = data.get("presets", {})
    preset_info = presets_dict.get(preset_id) or DEFAULT_PRESETS.get(preset_id)
    active_version = (preset_info or {}).get("active_version", "v001")

    if not user:
        return {
            "authorized": False,
            "status": "ANONYMOUS",
            "preset": preset_info,
            "active_version": active_version,
            "message": "Silakan masuk untuk mengakses preset berlisensi ini.",
        }

    if str(user.get("role") or "").lower() == "admin":
        return {
            "authorized": True,
            "status": "ADMIN",
            "preset": preset_info,
            "active_version": active_version,
            "message": "Akses Penuh Administrator Resmi",
        }

    username = str(user.get("username") or "").lower()
    granted = data.get("granted_users", {})
    if username in granted and granted[username].get("preset_id") in (preset_id, "all"):
        return {
            "authorized": True,
            "status": "APPROVED",
            "preset": preset_info,
            "active_version": active_version,
            "message": "Izin akses preset resmi aktif",
        }

    return {
        "authorized": False,
        "status": "NONE",
        "preset": preset_info,
        "active_version": active_version,
        "message": "Preset berlisensi ini memerlukan klaim Kode Lisensi sekali pakai atau akses langsung dari Admin.",
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


def get_decrypted_preset_binary(preset_id: str = "esp32s3_cam", version: Optional[str] = None) -> bytes:
    """
    Ambil bytes file .bin yang didekripsi secara streaming on-the-fly dari
    Object Storage S3 atau protected_assets lokal.
    """
    clean_id = str(preset_id).strip().lower()
    data = _load_data()
    presets_dict = data.get("presets", {})
    preset_info = presets_dict.get(clean_id) or DEFAULT_PRESETS.get(clean_id)

    if not preset_info:
        raise ValueError(f"Preset tidak ditemukan: {clean_id}")

    versions = preset_info.get("versions", [])
    target_ver = None

    if version:
        for v in versions:
            if v.get("version") == version:
                target_ver = v
                break

    if not target_ver and versions:
        # Gunakan versi aktif terbaru
        active_code = preset_info.get("active_version")
        for v in reversed(versions):
            if v.get("version") == active_code:
                target_ver = v
                break
        if not target_ver:
            target_ver = versions[-1]

    enc_bytes = None

    if target_ver and target_ver.get("storage_key"):
        bucket_name = target_ver.get("storage_bucket", "local-private")
        storage_key = target_ver.get("storage_key")
        try:
            enc_bytes = storage_service.get_encrypted_preset_bytes(bucket_name, storage_key)
        except Exception as e:
            logger.warning(f"Gagal mengambil dari storage_service: {e}. Mencoba jalur lokal...")

    if not enc_bytes:
        # Fallback ke path lokal protected_assets
        enc_rel = preset_info.get("enc_rel_path") or f"protected_assets/firmware/{clean_id}.bin.enc"
        candidates = [
            BASE_DIR / enc_rel,
            PROJECT_ROOT / "xiaozhi" / enc_rel,
            Path("xiaozhi") / enc_rel,
            BASE_DIR / "protected_assets" / "firmware" / Path(enc_rel).name,
        ]
        enc_path = None
        for cand in candidates:
            if cand.exists():
                enc_path = cand
                break

        if not enc_path:
            raise FileNotFoundError(f"File terenkripsi firmware preset '{clean_id}' tidak ditemukan di S3 maupun penyimpanan lokal.")

        enc_bytes = enc_path.read_bytes()

    if len(enc_bytes) < 28:
        raise ValueError(f"File terenkripsi rusak atau ukuran tidak valid: {len(enc_bytes)} bytes.")

    aesgcm = _get_aesgcm()
    nonce = enc_bytes[:12]
    ciphertext = enc_bytes[12:]
    raw_binary = aesgcm.decrypt(nonce, ciphertext, None)
    return raw_binary
