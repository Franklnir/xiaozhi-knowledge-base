import hashlib
import re
import secrets
import time
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from xiaozhi.config import (
    REAL_RELAY_MAX_RELAYS,
    REAL_RELAY_POLL_INTERVAL_MS,
    REAL_RELAY_DEVICE_ONLINE_SECONDS,
)
from xiaozhi.core.utils import (
    clean_text,
    normalize_relay_client_id,
    normalize_relay_state,
    normalize_voice_command,
    parse_int_range,
    slugify_topic_part,
    utc_now,
)
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    render,
    require_user,
    validate_csrf,
    redirect_with_message,
)
from xiaozhi.services.mcp_service import is_mcp_connected
from xiaozhi.database.models import RealRelayControlCommand, RealRelayDeviceStatusCommand

router = APIRouter()
_relay_device_last_broadcast: Dict[int, float] = {}


@router.get("/relay-nyata", response_class=HTMLResponse)
async def relay_nyata_page(request: Request, mode: str = Query(default=None)):
    if mode in ("virtual", "simulasi"):
        return RedirectResponse("/simulasi-smarthome-virtual", status_code=303)
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    payload = real_relay_page_payload(user["id"])
    feature_settings = store.get_feature_settings(user["id"])
    virtual_enabled = bool(feature_settings.get("virtual_smarthome_enabled", True))
    quota = store.user_quota(user["id"])
    api_defaults = {
        "max_relays": REAL_RELAY_MAX_RELAYS,
        "poll_interval_ms": REAL_RELAY_POLL_INTERVAL_MS,
        "device_online_window_seconds": REAL_RELAY_DEVICE_ONLINE_SECONDS,
    }
    return render(
        request,
        "relay_nyata.html",
        {
            "user": user,
            "rooms": payload["rooms"],
            "quota": quota,
            "api_defaults": api_defaults,
            "virtual_smarthome_enabled": virtual_enabled,
            "mcp_connected": is_mcp_connected(user["id"]),
            "message": request.query_params.get("message", ""),
            "active_page": "relay_nyata",
            "page": "relay_nyata",
            "active_mode": "real",
        },
    )


def real_relay_page_payload(owner_id: int) -> Dict[str, Any]:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    return {"rooms": rooms}


def _forward_mqtt_command(slug: str, relay_number: int, command: str, cmd_id: str) -> bool:
    import json
    import os
    import urllib.request
    
    clean_slug = (slug or "").strip().lower()
    if not clean_slug:
        return False
        
    base_urls = [
        os.getenv("GATEWAY_URL", "").rstrip("/"),
        "http://xiaozhi-gateway:8080",
        "http://127.0.0.1:8085",
        "http://gateway:8080",
        "http://localhost:8085",
    ]
    
    payload = json.dumps({
        "relay": int(relay_number),
        "command": str(command).upper(),
        "cmd_id": str(cmd_id),
    }).encode("utf-8")
    
    for base in base_urls:
        if not base:
            continue
        try:
            target = f"{base}/api/device/relay/{clean_slug}/control"
            req = urllib.request.Request(
                target,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            continue
    return False


def control_real_relay(owner_id: int, room: dict, relay: dict, command: str, source: str = "web_ui", wait_for_ack: bool = None) -> dict:
    target_status = normalize_relay_state(command)
    current_status = str(relay.get("status", "OFF")).upper()
    relay_num = int(relay.get("relay_number", 1))
    if relay_num < 1 or relay_num > REAL_RELAY_MAX_RELAYS:
        raise ValueError(f"Nomor channel relay {relay_num} di luar batas yang diizinkan (1 - {REAL_RELAY_MAX_RELAYS}).")
    slug = room.get("slug") or room.get("api_slug") or ""
    
    import time
    cmd_id = f"cmd_{int(time.time() * 1000)}_{relay_num}"

    # 1. Forward instant command to Go Gateway -> Mosquitto MQTT: xiaozhi/relay/{slug}/set
    _forward_mqtt_command(slug, relay_num, target_status, cmd_id)

    # 2. Broadcast SSE update with status = current_status, pending = target_status, desired = target_status
    try:
        from xiaozhi.services.sse_service import broadcast_relay_update
        broadcast_relay_update(
            user_id=owner_id,
            room_id=room["id"],
            relay_number=relay_num,
            status=current_status,  # Still previous confirmed state!
            source=source,
            room_name=room.get("nama_tempat", ""),
            relay_name=relay.get("nama_relay", ""),
            pending=target_status,  # Waiting for ESP32 hardware ACK!
            desired=target_status,
        )
    except Exception:
        pass

    if wait_for_ack is None:
        wait_for_ack = (source == "mcp_voice")

    store = get_store()

    # 3. Jika dipanggil oleh XiaoZhi Voice (MCP), tunggu konfirmasi fisik hardware ESP32 secara aktif (hingga 2.5 detik)
    if wait_for_ack:
        deadline = time.time() + 2.5
        confirmed = False
        while time.time() < deadline:
            time.sleep(0.08)
            try:
                refreshed_room = store.get_relay_room(owner_id, room["id"])
                if refreshed_room:
                    for r in refreshed_room.get("relays", []):
                        if int(r.get("relay_number", 0)) == relay_num:
                            if str(r.get("status", "")).upper() == target_status:
                                confirmed = True
                                break
            except Exception:
                pass
            if confirmed:
                break

        action_word = "dinyalakan" if target_status == "ON" else "dimatikan"
        status_word = "aktif (ON)" if target_status == "ON" else "mati (OFF)"
        if confirmed:
            return {
                "success": True,
                "pending": False,
                "validated": True,
                "message": f"Berhasil! {relay.get('nama_relay', 'Relay')} di {room.get('nama_tempat', '')} telah {action_word} dan terkonfirmasi fisik {status_word} oleh perangkat ESP32.",
                "room": room.get("nama_tempat", ""),
                "relay": relay.get("nama_relay", ""),
                "current_status": target_status,
                "desired_status": target_status,
                "cmd_id": cmd_id,
                "source": source,
            }
        else:
            return {
                "success": False,
                "pending": True,
                "validated": False,
                "message": f"Perintah {action_word} {relay.get('nama_relay', 'relay')} telah dikirim, namun mikrokontroler ESP32 di {room.get('nama_tempat', '')} belum memberikan konfirmasi fisik balik. Silakan pastikan perangkat menyala dan terhubung.",
                "room": room.get("nama_tempat", ""),
                "relay": relay.get("nama_relay", ""),
                "current_status": current_status,
                "desired_status": target_status,
                "cmd_id": cmd_id,
                "source": source,
            }

    return {
        "success": True,
        "pending": True,
        "message": f"Perintah {target_status} dikirim ke ESP32 ({room.get('nama_tempat', '')}). Menunggu validasi fisik...",
        "room": room.get("nama_tempat", ""),
        "relay": relay.get("nama_relay", ""),
        "current_status": current_status,
        "desired_status": target_status,
        "cmd_id": cmd_id,
        "source": source,
    }


def control_all_real_relays(owner_id: int, command: str, source: str = "web_ui", wait_for_ack: bool = None) -> dict:
    target_status = normalize_relay_state(command)
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    count = 0
    import time
    for room in rooms:
        slug = room.get("slug") or room.get("api_slug") or ""
        for relay in room.get("relays", []):
            relay_num = int(relay.get("relay_number", 1))
            current_status = str(relay.get("status", "OFF")).upper()
            cmd_id = f"cmd_{int(time.time() * 1000)}_{relay_num}"
            _forward_mqtt_command(slug, relay_num, target_status, cmd_id)
            count += 1
            try:
                from xiaozhi.services.sse_service import broadcast_relay_update
                broadcast_relay_update(
                    user_id=owner_id,
                    room_id=room["id"],
                    relay_number=relay_num,
                    status=current_status,
                    source=source,
                    room_name=room.get("nama_tempat", ""),
                    relay_name=relay.get("nama_relay", ""),
                    pending=target_status,
                    desired=target_status,
                )
            except Exception:
                pass

    if wait_for_ack is None:
        wait_for_ack = (source == "mcp_voice")

    if wait_for_ack and count > 0:
        deadline = time.time() + 2.5
        all_confirmed = False
        while time.time() < deadline:
            time.sleep(0.1)
            try:
                refreshed_rooms = store.list_relay_rooms(owner_id)
                all_match = True
                for r_room in refreshed_rooms:
                    for r_relay in r_room.get("relays", []):
                        if str(r_relay.get("status", "")).upper() != target_status:
                            all_match = False
                            break
                    if not all_match:
                        break
                if all_match:
                    all_confirmed = True
                    break
            except Exception:
                pass

        action_word = "dinyalakan" if target_status == "ON" else "dimatikan"
        status_word = "aktif (ON)" if target_status == "ON" else "mati (OFF)"
        if all_confirmed:
            return {
                "success": True,
                "pending": False,
                "validated": True,
                "message": f"Berhasil! Seluruh {count} relay telah {action_word} dan terkonfirmasi fisik {status_word} oleh perangkat ESP32.",
                "count": count,
                "desired_status": target_status,
                "source": source,
            }
        else:
            return {
                "success": False,
                "pending": True,
                "validated": False,
                "message": f"Perintah {action_word} untuk {count} relay telah dikirim, namun sebagian perangkat ESP32 belum memberikan konfirmasi balik fisik lengkap.",
                "count": count,
                "desired_status": target_status,
                "source": source,
            }

    return {
        "success": True,
        "pending": True,
        "message": f"Perintah {target_status} untuk {count} relay dikirim ke ESP32. Menunggu konfirmasi fisik...",
        "count": count,
        "desired_status": target_status,
        "source": source,
    }


def match_real_relay_command(owner_id: int, user_message: str) -> dict:
    store = get_store()
    normalized_msg = normalize_voice_command(user_message)
    rooms = store.list_relay_rooms(owner_id)
    for room in rooms:
        for relay in room.get("relays", []):
            on_cmd = normalize_voice_command(relay.get("voice_command_on", ""))
            off_cmd = normalize_voice_command(relay.get("voice_command_off", ""))
            if on_cmd and on_cmd in normalized_msg:
                return {"room": room, "relay": relay, "command": "ON"}
            if off_cmd and off_cmd in normalized_msg:
                return {"room": room, "relay": relay, "command": "OFF"}
    return None


def match_real_relay_for_target(owner_id: int, *, target: str = "", action: str = "", channel: int = None) -> dict:
    store = get_store()
    normalized_target = normalize_voice_command(target)
    rooms = store.list_relay_rooms(owner_id)
    for room in rooms:
        room_name = normalize_voice_command(room.get("nama_tempat", ""))
        if normalized_target and room_name not in normalized_target and normalized_target not in room_name:
            continue
        for relay in room.get("relays", []):
            on_cmd = normalize_voice_command(relay.get("voice_command_on", ""))
            off_cmd = normalize_voice_command(relay.get("voice_command_off", ""))
            action_normalized = normalize_voice_command(action)
            if on_cmd and (on_cmd in action_normalized or action_normalized in on_cmd):
                return {"room": room, "relay": relay, "command": "ON"}
            if off_cmd and (off_cmd in action_normalized or action_normalized in off_cmd):
                return {"room": room, "relay": relay, "command": "OFF"}
    return None


def real_relay_status_payload(owner_id: int) -> dict:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    relays = []
    for room in rooms:
        for relay in room.get("relays", []):
            relays.append({
                "room": room["nama_tempat"],
                "relay": relay["nama_relay"],
                "status": relay.get("status", "OFF"),
                "relay_number": relay["relay_number"],
            })
    return {"relays": relays}


def generate_real_relay_api_config(nama_tempat: str) -> Dict[str, Any]:
    nama_tempat = clean_text(nama_tempat, max_len=80, min_len=2, field="Nama tempat")
    slug = slugify_topic_part(nama_tempat)
    suffix = secrets.token_hex(3)
    return {
        "api_slug": slug,
        "api_client_id": f"device-{slug}-{suffix}",
        "api_token": secrets.token_urlsafe(32),
    }


def generate_relay_voice_commands(nama_tempat: str, nama_relay: str, relay_number: int) -> tuple[str, str]:
    """
    Generate natural, unique Indonesian voice commands for turning ON and OFF a relay device.
    Combines device/load name and room name to guarantee uniqueness across different rooms and devices.
    Example:
      nama_tempat='Kamar Utama', nama_relay='Lampu', relay_number=1
      -> ('nyalakan lampu kamar utama', 'matikan lampu kamar utama')
    """
    room_clean = clean_text(nama_tempat or "ruangan", max_len=80, min_len=1, field="Nama tempat").lower().strip()
    relay_clean = clean_text(nama_relay or f"relay {relay_number}", max_len=80, min_len=1, field="Nama relay").lower().strip()

    # Normalize punctuation and special symbols
    room_norm = re.sub(r"[^a-zA-Z0-9\s]+", " ", room_clean).strip()
    relay_norm = re.sub(r"[^a-zA-Z0-9\s]+", " ", relay_clean).strip()

    # If the relay name already contains the room name, avoid repeating it
    if room_norm and room_norm in relay_norm:
        target = relay_norm
    elif room_norm:
        target = f"{relay_norm} {room_norm}"
    else:
        target = relay_norm or f"relay {relay_number}"

    target = re.sub(r"\s+", " ", target).strip()
    return f"nyalakan {target}", f"matikan {target}"


@router.post("/relay-nyata/toggle-simulasi")
async def toggle_simulasi(request: Request, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    feature_settings = store.get_feature_settings(user["id"])
    current = bool(feature_settings.get("virtual_smarthome_enabled", True))
    store.set_feature_setting(user["id"], "virtual_smarthome_enabled", not current)
    status = "dinonaktifkan" if current else "diaktifkan"
    return redirect_with_message("/relay-nyata", f"Simulasi Smart Home Virtual {status}.")


@router.post("/api/relay-nyata/generate-api")
async def generate_api(request: Request):
    user = require_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    nama_tempat = str(body.get("nama_tempat", "")).strip()
    if not nama_tempat or len(nama_tempat) < 2:
        nama_tempat = "Ruangan Relay"
    try:
        config = generate_real_relay_api_config(nama_tempat)
    except Exception:
        slug = slugify_topic_part(nama_tempat) if nama_tempat else "relay"
        suffix = secrets.token_hex(3)
        config = {
            "api_slug": f"{slug}-{suffix}",
            "api_client_id": f"device-{slug}-{suffix}",
            "api_token": secrets.token_urlsafe(32),
        }
    return {"success": True, "config": config, **config}


@router.post("/relay-nyata/add")
async def add_relay_room(
    request: Request,
    csrf_token: str = Form(...),
    nama_tempat: str = Form(...),
    api_slug: str = Form(""),
    api_token: str = Form(""),
    api_client_id: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)

    # 1. Pastikan simulasi virtual otomatis dinonaktifkan saat user menambahkan Relay Fisik
    feature_settings = store.get_feature_settings(user["id"])
    was_virtual_active = bool(feature_settings.get("virtual_smarthome_enabled", False))
    if was_virtual_active:
        store.set_feature_setting(user["id"], "virtual_smarthome_enabled", False)

    # 2. Cek kuota relay rooms user
    quota = store.user_quota(user["id"])
    if quota.get("relay_rooms", {}).get("reached"):
        return redirect_with_message(
            "/relay-nyata",
            f"Gagal: Batas perangkat Relay Nyata akun Anda ({quota.get('relay_rooms', {}).get('limit', 7)} ruangan) telah tercapai."
        )

    try:
        form = await request.form()
        jumlah_relay = parse_int_range(form.get("jumlah_relay"), field="Jumlah relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        relays = []
        for index in range(1, jumlah_relay + 1):
            nama_relay = str(form.get(f"relay_{index}_nama", "")).strip() or f"Relay {index}"
            voice_on = str(form.get(f"relay_{index}_on", "")).strip()
            voice_off = str(form.get(f"relay_{index}_off", "")).strip()
            if not voice_on or not voice_off:
                auto_on, auto_off = generate_relay_voice_commands(nama_tempat, nama_relay, index)
                voice_on = voice_on or auto_on
                voice_off = voice_off or auto_off
            relays.append({
                "relay_number": index,
                "nama_relay": nama_relay,
                "voice_command_on": voice_on,
                "voice_command_off": voice_off,
                "status": str(form.get(f"relay_{index}_status", "OFF")),
            })
        if not api_slug or not api_token or not api_client_id:
            config = generate_real_relay_api_config(nama_tempat)
            api_slug = api_slug or config["api_slug"]
            api_token = api_token or config["api_token"]
            api_client_id = api_client_id or config["api_client_id"]
        store.add_relay_room(user["id"], nama_tempat, api_slug, api_token, api_client_id, relays)
        success_msg = f"Ruangan '{nama_tempat}' berhasil ditambahkan."
        if was_virtual_active:
            success_msg += " Simulasi Virtual otomatis dinonaktifkan agar perintah suara langsung mengontrol Relay Fisik."
        return redirect_with_message("/relay-nyata", success_msg)
    except ValueError as exc:
        return redirect_with_message("/relay-nyata", f"Gagal: {exc}")


@router.post("/relay-nyata/update/{room_id}")
async def update_relay_room(
    request: Request,
    room_id: int,
    csrf_token: str = Form(...),
):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)

    # 1. Pastikan simulasi virtual otomatis dinonaktifkan saat edit relay fisik
    feature_settings = store.get_feature_settings(user["id"])
    was_virtual_active = bool(feature_settings.get("virtual_smarthome_enabled", False))
    if was_virtual_active:
        store.set_feature_setting(user["id"], "virtual_smarthome_enabled", False)

    # 2. Pastikan ruangan milik user yang login
    existing_room = store.get_relay_room(user["id"], room_id)
    if not existing_room:
        return redirect_with_message("/relay-nyata", "Gagal: Ruangan tidak ditemukan atau Anda tidak memiliki akses.")

    try:
        form = await request.form()
        nama_tempat = str(form.get("nama_tempat", "")).strip()
        jumlah_relay = parse_int_range(form.get("jumlah_relay"), field="Jumlah relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        relays = []
        for index in range(1, jumlah_relay + 1):
            nama_relay = str(form.get(f"relay_{index}_nama", "")).strip() or f"Relay {index}"
            voice_on = str(form.get(f"relay_{index}_on", "")).strip()
            voice_off = str(form.get(f"relay_{index}_off", "")).strip()
            if not voice_on or not voice_off:
                auto_on, auto_off = generate_relay_voice_commands(nama_tempat, nama_relay, index)
                voice_on = voice_on or auto_on
                voice_off = voice_off or auto_off
            relays.append({
                "relay_number": index,
                "nama_relay": nama_relay,
                "voice_command_on": voice_on,
                "voice_command_off": voice_off,
                "status": str(form.get(f"relay_{index}_status", "OFF")),
            })
        store.update_relay_room(user["id"], room_id, nama_tempat, relays)
        success_msg = f"Ruangan '{nama_tempat}' berhasil diperbarui."
        if was_virtual_active:
            success_msg += " Simulasi Virtual otomatis dinonaktifkan agar perintah suara langsung mengontrol Relay Fisik."
        return redirect_with_message("/relay-nyata", success_msg)
    except ValueError as exc:
        return redirect_with_message("/relay-nyata", f"Gagal: {exc}")


@router.post("/relay-nyata/delete/{room_id}")
async def delete_relay_room(request: Request, room_id: int, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    deleted = store.delete_relay_room(user["id"], room_id)
    return redirect_with_message("/relay-nyata", "Ruangan dihapus." if deleted else "Ruangan tidak ditemukan.")


@router.get("/api/relay-nyata/state")
async def relay_nyata_state(request: Request):
    user = require_user(request)
    payload = real_relay_status_payload(user["id"])
    return {"success": True, **payload}


@router.get("/api/relay-nyata/{room_id}/config")
async def relay_room_config(request: Request, room_id: int):
    user = require_user(request)
    store = get_store()
    room = store.get_relay_room(user["id"], room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan.")
    base_url = str(request.base_url).rstrip("/")
    room["api_commands_url"] = f"{base_url}{room.get('api_commands_path', '')}"
    room["api_status_url"] = f"{base_url}{room.get('api_status_path', '')}"
    room["api_token_header"] = "X-Device-Token"
    return {"success": True, "room": room}


@router.post("/api/relay-nyata/control")
async def relay_nyata_control(request: Request, cmd: RealRelayControlCommand):
    user = require_user(request)
    store = get_store()

    # 1. Keamanan Mode: Tolak jika simulasi virtual sedang aktif
    feature_settings = store.get_feature_settings(user["id"])
    if bool(feature_settings.get("virtual_smarthome_enabled", True)):
        return JSONResponse({
            "success": False,
            "message": "Simulasi Smart Home Virtual sedang aktif. Nonaktifkan simulasi virtual terlebih dahulu untuk mengontrol relay fisik.",
        }, status_code=400)

    # 2. Keamanan Batas Sistem Relay
    if cmd.relay_number < 1 or cmd.relay_number > REAL_RELAY_MAX_RELAYS:
        return JSONResponse({
            "success": False,
            "message": f"Akses ditolak: Channel relay {cmd.relay_number} di luar batas yang diizinkan (1 s/d {REAL_RELAY_MAX_RELAYS}).",
        }, status_code=400)

    # 3. Keamanan Otorisasi Ruangan
    room = store.get_relay_room(user["id"], cmd.room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan atau bukan milik akun Anda.")

    # 4. Keamanan Batas Ruangan: Pastikan relay terdaftar pada ruangan tersebut
    relay = next((r for r in room.get("relays", []) if r["relay_number"] == cmd.relay_number), None)
    if not relay:
        registered_numbers = [r["relay_number"] for r in room.get("relays", [])]
        return JSONResponse({
            "success": False,
            "message": f"Akses ditolak: Channel relay {cmd.relay_number} tidak terdaftar pada ruangan '{room.get('nama_tempat')}'. Ruangan ini hanya memiliki {len(registered_numbers)} relay terdaftar.",
        }, status_code=403)

    try:
        response = control_real_relay(user["id"], room, relay, cmd.command, source="web_ui")
        response["updated_room"] = store.get_relay_room(user["id"], cmd.room_id)
        return response
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)


@router.post("/api/relay-nyata/room-control")
async def relay_nyata_room_control(request: Request):
    """Batch control all relays in a specific room (All ON / All OFF)."""
    user = require_user(request)
    store = get_store()

    # 1. Keamanan Mode: Tolak jika simulasi virtual sedang aktif
    feature_settings = store.get_feature_settings(user["id"])
    if bool(feature_settings.get("virtual_smarthome_enabled", True)):
        return JSONResponse({
            "success": False,
            "message": "Simulasi Smart Home Virtual sedang aktif. Nonaktifkan simulasi virtual terlebih dahulu untuk mengontrol relay fisik.",
        }, status_code=400)

    body = await request.json()
    room_id = body.get("room_id")
    command = str(body.get("command", "OFF")).upper()
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id diperlukan.")

    room = store.get_relay_room(user["id"], int(room_id))
    if not room:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan atau bukan milik akun Anda.")

    for relay in room.get("relays", []):
        control_real_relay(user["id"], room, relay, command, source="web_ui")
    updated_room = store.get_relay_room(user["id"], int(room_id))
    return {"success": True, "command": command, "updated_room": updated_room}


@router.get("/api/relay-nyata/stream")
async def relay_nyata_stream(request: Request):
    """
    Real-time Server-Sent Events stream for 2-way live relay synchronization.
    Instantly pushes state updates triggered by XiaoZhi Voice Assistant (MCP),
    Web UI switches, or ESP32 hardware ACKs without polling.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")
    from xiaozhi.services.sse_service import stream_relay_events
    return StreamingResponse(
        stream_relay_events(user["id"], request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/device/relay/{api_slug}/commands")
async def device_relay_commands(request: Request, api_slug: str, token: str = Query("")):
    store = get_store()
    auth_token = token or request.headers.get("X-Device-Token", "")
    if not auth_token:
        raise HTTPException(status_code=400, detail="Token diperlukan.")
    owner = store.find_relay_room_by_slug(api_slug)
    if not owner:
        raise HTTPException(status_code=404, detail="API slug tidak ditemukan.")
    if owner.get("api_token") and auth_token != owner["api_token"]:
        auth_hash = hashlib.sha256(auth_token.encode()).hexdigest()
        if auth_hash != owner.get("api_token_hash"):
            raise HTTPException(status_code=401, detail="Token perangkat tidak valid.")

    try:
        store.ack_relay_command(owner["user_id"], owner["room_id"])
    except Exception:
        pass

    # Broadcast status online ke SSE browser (dibatasi 5 detik per ruangan agar tidak banjir event)
    now_ts = time.time()
    room_id = owner["room_id"]
    last_broadcast = _relay_device_last_broadcast.get(room_id, 0.0)
    if (now_ts - last_broadcast) >= 5.0:
        _relay_device_last_broadcast[room_id] = now_ts
        try:
            from xiaozhi.services.sse_service import broadcast_relay_device_status
            broadcast_relay_device_status(
                user_id=owner["user_id"],
                room_id=room_id,
                connected=True,
                last_seen_at=utc_now(),
                device_label="ESP32 Terhubung",
            )
        except Exception:
            pass

    room = store.get_relay_room(owner["user_id"], owner["room_id"])
    commands = store.get_pending_relay_commands(owner["user_id"], owner["room_id"])
    relays = [
        {
            "relay": r["relay_number"],
            "name": r.get("nama_relay", f"Relay {r['relay_number']}"),
            "status": r.get("status", "OFF"),
            "desired_status": r.get("desired_status", r.get("status", "OFF")),
        }
        for r in (room.get("relays", []) if room else [])
    ]
    return {"success": True, "commands": commands, "relays": relays}


@router.post("/api/device/relay/{api_slug}/status")
async def device_relay_status(request: Request, api_slug: str, cmd: RealRelayDeviceStatusCommand):
    store = get_store()
    auth_token = cmd.token or request.headers.get("X-Device-Token", "")
    if not auth_token:
        raise HTTPException(status_code=400, detail="Token diperlukan.")
    owner = store.find_relay_room_by_slug(api_slug)
    if not owner:
        raise HTTPException(status_code=404, detail="API slug tidak ditemukan.")
    if owner.get("api_token") and auth_token != owner["api_token"]:
        auth_hash = hashlib.sha256(auth_token.encode()).hexdigest()
        if auth_hash != owner.get("api_token_hash"):
            raise HTTPException(status_code=401, detail="Token perangkat tidak valid.")

    store.ack_relay_command(owner["user_id"], owner["room_id"], cmd.command_id)

    # If single relay was reported
    relay_num = cmd.relay or cmd.relay_number
    relay_st = cmd.status or cmd.command or cmd.state
    if relay_num and relay_st:
        try:
            st_clean = normalize_relay_state(str(relay_st))
            store.update_relay_status(owner["user_id"], owner["room_id"], int(relay_num), st_clean)
        except Exception:
            pass

    # If batch relays array was reported
    if cmd.relays:
        for r in cmd.relays:
            if isinstance(r, dict) and "relay" in r and "status" in r:
                try:
                    st_clean = normalize_relay_state(str(r["status"]))
                    store.update_relay_status(owner["user_id"], owner["room_id"], int(r["relay"]), st_clean)
                except Exception:
                    pass

    # 2-way broadcast: device is online and command acknowledged
    try:
        from xiaozhi.services.sse_service import broadcast_relay_device_status, broadcast_relay_update
        broadcast_relay_device_status(
            user_id=owner["user_id"],
            room_id=owner["room_id"],
            connected=True,
            last_seen_at=utc_now(),
            device_label="ESP32/8266 terhubung",
        )
        updated_room = store.get_relay_room(owner["user_id"], owner["room_id"])
        if updated_room:
            for r in updated_room.get("relays", []):
                broadcast_relay_update(
                    user_id=owner["user_id"],
                    room_id=owner["room_id"],
                    relay_number=r["relay_number"],
                    status=r.get("status", "OFF"),
                    source="device_ack",
                    room_name=updated_room.get("nama_tempat", ""),
                    relay_name=r.get("nama_relay", ""),
                    pending="",
                    desired=r.get("status", "OFF"),
                )
    except Exception:
        pass

    return {"success": True}


@router.post("/api/internal/relay/broadcast")
async def internal_relay_broadcast(request: Request):
    """
    Internal webhook called by gateway-go to notify Python's SSE stream
    whenever ESP32 polls commands or confirms hardware status.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "detail": "JSON tidak valid"}, status_code=400)

    user_id = data.get("user_id")
    room_id = data.get("room_id")
    room_name = str(data.get("room_name", ""))
    relays = data.get("relays", [])

    if not user_id or not room_id:
        return JSONResponse({"success": False, "detail": "user_id dan room_id diperlukan."}, status_code=400)

    try:
        from xiaozhi.services.sse_service import broadcast_relay_device_status, broadcast_relay_update
        broadcast_relay_device_status(
            user_id=int(user_id),
            room_id=int(room_id),
            connected=True,
            last_seen_at=utc_now(),
            device_label="ESP32 Terhubung",
        )

        for r in relays:
            if isinstance(r, dict) and "relay" in r:
                r_num = int(r["relay"])
                r_status = str(r.get("status", "OFF")).upper()
                r_name = str(r.get("name", f"Relay {r_num}"))
                r_desired = str(r.get("desired_status", r_status)).upper()
                broadcast_relay_update(
                    user_id=int(user_id),
                    room_id=int(room_id),
                    relay_number=r_num,
                    status=r_status,
                    source="device_ack",
                    room_name=room_name,
                    relay_name=r_name,
                    pending="",
                    desired=r_desired,
                )
    except Exception as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=500)

    return {"success": True}


