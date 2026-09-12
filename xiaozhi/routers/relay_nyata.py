import re
import secrets
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse

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


@router.get("/relay-nyata", response_class=HTMLResponse)
async def relay_nyata_page(request: Request):
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
        },
    )


def real_relay_page_payload(owner_id: int) -> Dict[str, Any]:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    return {"rooms": rooms}


def control_real_relay(owner_id: int, room: dict, relay: dict, command: str) -> dict:
    store = get_store()
    status = normalize_relay_state(command)
    store.update_relay_status(owner_id, room["id"], relay["relay_number"], status)
    return {
        "success": True,
        "message": f"Relay {relay['nama_relay']} di {room['nama_tempat']} berhasil diubah ke {status}.",
        "room": room["nama_tempat"],
        "relay": relay["nama_relay"],
        "status": status,
    }


def control_all_real_relays(owner_id: int, command: str) -> dict:
    store = get_store()
    status = normalize_relay_state(command)
    rooms = store.list_relay_rooms(owner_id)
    count = 0
    for room in rooms:
        for relay in room.get("relays", []):
            store.update_relay_status(owner_id, room["id"], relay["relay_number"], status)
            count += 1
    return {
        "success": True,
        "message": f"Semua relay ({count}) berhasil diubah ke {status}.",
        "count": count,
        "status": status,
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
    body = await request.json()
    nama_tempat = str(body.get("nama_tempat", "")).strip()
    config = generate_real_relay_api_config(nama_tempat)
    return {"success": True, **config}


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
    try:
        form = await request.form()
        jumlah_relay = parse_int_range(form.get("jumlah_relay"), field="Jumlah relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        relays = []
        for index in range(1, jumlah_relay + 1):
            relays.append({
                "relay_number": index,
                "nama_relay": str(form.get(f"relay_{index}_nama", "")),
                "voice_command_on": str(form.get(f"relay_{index}_on", "")),
                "voice_command_off": str(form.get(f"relay_{index}_off", "")),
                "status": str(form.get(f"relay_{index}_status", "OFF")),
            })
        if not api_slug:
            config = generate_real_relay_api_config(nama_tempat)
            api_slug = config["api_slug"]
            api_token = config["api_token"]
            api_client_id = config["api_client_id"]
        store.add_relay_room(user["id"], nama_tempat, api_slug, api_token, api_client_id, relays)
        return redirect_with_message("/relay-nyata", f"Ruangan '{nama_tempat}' berhasil ditambahkan.")
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
    try:
        form = await request.form()
        nama_tempat = str(form.get("nama_tempat", "")).strip()
        jumlah_relay = parse_int_range(form.get("jumlah_relay"), field="Jumlah relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        relays = []
        for index in range(1, jumlah_relay + 1):
            relays.append({
                "relay_number": index,
                "nama_relay": str(form.get(f"relay_{index}_nama", "")),
                "voice_command_on": str(form.get(f"relay_{index}_on", "")),
                "voice_command_off": str(form.get(f"relay_{index}_off", "")),
                "status": str(form.get(f"relay_{index}_status", "OFF")),
            })
        store.update_relay_room(user["id"], room_id, nama_tempat, relays)
        return redirect_with_message("/relay-nyata", f"Ruangan '{nama_tempat}' berhasil diperbarui.")
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
    return {"success": True, "room": room}


@router.post("/api/relay-nyata/control")
async def relay_nyata_control(request: Request, cmd: RealRelayControlCommand):
    user = require_user(request)
    store = get_store()
    room = store.get_relay_room(user["id"], cmd.room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan.")
    relay = next((r for r in room.get("relays", []) if r["relay_number"] == cmd.relay_number), None)
    if not relay:
        raise HTTPException(status_code=404, detail="Relay tidak ditemukan.")
    try:
        response = control_real_relay(user["id"], room, relay, cmd.command)
        return response
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)


@router.get("/api/device/relay/{api_slug}/commands")
async def device_relay_commands(request: Request, api_slug: str, token: str = Query("")):
    store = get_store()
    if not token:
        raise HTTPException(status_code=400, detail="Token diperlukan.")
    owner = store.find_relay_room_by_slug(api_slug)
    if not owner:
        raise HTTPException(status_code=404, detail="API slug tidak ditemukan.")
    commands = store.get_pending_relay_commands(owner["user_id"], owner["room_id"])
    return {"success": True, "commands": commands}


@router.post("/api/device/relay/{api_slug}/status")
async def device_relay_status(request: Request, api_slug: str, cmd: RealRelayDeviceStatusCommand):
    store = get_store()
    if not cmd.token:
        raise HTTPException(status_code=400, detail="Token diperlukan.")
    owner = store.find_relay_room_by_slug(api_slug)
    if not owner:
        raise HTTPException(status_code=404, detail="API slug tidak ditemukan.")
    store.ack_relay_command(owner["user_id"], owner["room_id"], cmd.command_id)
    return {"success": True}
