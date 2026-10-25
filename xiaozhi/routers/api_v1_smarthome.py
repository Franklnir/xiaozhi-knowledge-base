"""
API v1 Smart Home endpoints for mobile and API clients.
Supports both Real Hardware Relays (IoT/ESP32 via MQTT & Go Gateway)
and Virtual Smart Home Simulation.
"""
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from xiaozhi.dependencies import require_user, get_store
from xiaozhi.services.mcp_service import is_mcp_connected
from xiaozhi.services.smarthome_service import (
    get_smart_home_state,
    set_all_smart_home_relays,
    set_smart_home_relay,
    smart_home_payload,
)
from xiaozhi.routers.relay_nyata import (
    control_real_relay,
    control_all_real_relays,
    real_relay_page_payload,
)

router = APIRouter(prefix="/api/v1/smarthome", tags=["API v1 Smart Home"])


# ── Response Models ────────────────────────────────────────────────────────

class ApiResponse(BaseModel):
    success: bool = True
    data: Optional[dict] = None
    message: str = "OK"


class RelayCommand(BaseModel):
    channel: int
    state: bool
    room_id: Optional[int] = None


class AllRelaysCommand(BaseModel):
    state: bool


class ModeSwitchCommand(BaseModel):
    mode: str  # "real" or "virtual"


# ── Helpers ────────────────────────────────────────────────────────────────

def build_smarthome_response(user_id: int) -> dict:
    store = get_store()
    feature_settings = store.get_feature_settings(user_id)
    virtual_enabled = bool(feature_settings.get("virtual_smarthome_enabled", True))
    mcp_connected = is_mcp_connected(user_id)
    token_info = store.get_xiaozhi_token_info(user_id)
    real_rooms = store.list_relay_rooms(user_id)

    # Mode determination:
    # If user explicitly disabled virtual OR has real rooms and virtual is false -> "real"
    active_mode = "real" if (real_rooms and not virtual_enabled) else "virtual"

    if active_mode == "real":
        relays_list = []
        for room in real_rooms:
            room_name = room.get("nama_tempat", "")
            room_id = room.get("id")
            device_connected = bool(room.get("device_connected", False))
            for r in room.get("relays", []):
                channel_num = int(r.get("relay_number", 1))
                status_str = str(r.get("status", "OFF")).upper()
                is_on = (status_str == "ON")
                relays_list.append({
                    "channel": channel_num,
                    "name": r.get("nama_relay", f"Relay {channel_num}"),
                    "room_name": room_name,
                    "room_id": room_id,
                    "state": is_on,
                    "is_virtual": False,
                    "device_connected": device_connected,
                    "device_last_seen_at": room.get("device_last_seen_at", ""),
                })

        active_count = sum(1 for r in relays_list if r["state"])
        total_count = len(relays_list)

        return {
            "active_mode": "real",
            "virtual_enabled": False,
            "mcp_connected": mcp_connected,
            "token_saved": bool(token_info),
            "active_count": active_count,
            "total_count": total_count,
            "relays": relays_list,
            "real_rooms": real_rooms,
            "has_real_hardware": True,
        }
    else:
        v_payload = smart_home_payload(
            user_id,
            token_saved=bool(token_info),
            mcp_connected=mcp_connected,
            virtual_enabled=virtual_enabled,
        )
        relays_list = []
        for r in v_payload.get("relays", []):
            relays_list.append({
                "channel": r["channel"],
                "name": r["name"],
                "room_name": r.get("location", r["name"]),
                "room_id": None,
                "state": r["state"],
                "is_virtual": True,
                "device_connected": mcp_connected,
                "device_last_seen_at": "",
            })

        return {
            "active_mode": "virtual",
            "virtual_enabled": virtual_enabled,
            "mcp_connected": mcp_connected,
            "token_saved": bool(token_info),
            "active_count": v_payload.get("activeCount", 0),
            "total_count": v_payload.get("totalCount", 8),
            "relays": relays_list,
            "real_rooms": real_rooms,
            "has_real_hardware": len(real_rooms) > 0,
        }


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/state", response_model=ApiResponse)
async def get_state(request: Request):
    """Get current smart home state for all relays (Real or Virtual)."""
    user = require_user(request)
    data = build_smarthome_response(user["id"])
    return ApiResponse(
        success=True,
        data=data,
        message="OK"
    )


@router.post("/relay", response_model=ApiResponse)
@router.post("/relays/channel", response_model=ApiResponse)
async def control_relay(request: Request, cmd: RelayCommand):
    """Control a specific relay channel (supports both Real & Virtual)."""
    user = require_user(request)
    store = get_store()
    feature_settings = store.get_feature_settings(user["id"])
    virtual_enabled = bool(feature_settings.get("virtual_smarthome_enabled", True))
    real_rooms = store.list_relay_rooms(user["id"])

    use_real = (cmd.room_id is not None) or (real_rooms and not virtual_enabled)

    if use_real and real_rooms:
        target_room = None
        if cmd.room_id is not None:
            target_room = next((rm for rm in real_rooms if rm["id"] == cmd.room_id), None)
        if not target_room:
            # Fallback to the room that contains this relay number
            for rm in real_rooms:
                if any(int(r.get("relay_number", 0)) == cmd.channel for r in rm.get("relays", [])):
                    target_room = rm
                    break
        if not target_room and real_rooms:
            target_room = real_rooms[0]

        if not target_room:
            raise HTTPException(status_code=404, detail="Ruangan relay fisik tidak ditemukan.")

        target_relay = next((r for r in target_room.get("relays", []) if int(r.get("relay_number", 0)) == cmd.channel), None)
        if not target_relay:
            # Create a virtual placeholder relay object if needed
            target_relay = {"relay_number": cmd.channel, "nama_relay": f"Relay {cmd.channel}", "status": "OFF"}

        action_cmd = "ON" if cmd.state else "OFF"
        try:
            res = control_real_relay(user["id"], target_room, target_relay, action_cmd, source="mobile_app")
            new_data = build_smarthome_response(user["id"])
            return ApiResponse(
                success=True,
                data=new_data,
                message=res.get("message", f"Relay {cmd.channel} diubah ke {action_cmd}.")
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        # Virtual Smart Home Mode
        try:
            state = set_smart_home_relay(user["id"], cmd.channel, cmd.state)
            new_data = build_smarthome_response(user["id"])
            return ApiResponse(
                success=True,
                data=new_data,
                message=f"Relay {cmd.channel} berhasil {'dinyalakan' if cmd.state else 'dimatikan'}."
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@router.post("/all", response_model=ApiResponse)
async def control_all_relays(request: Request, cmd: AllRelaysCommand):
    """Turn all relays ON or OFF (Real or Virtual)."""
    user = require_user(request)
    store = get_store()
    feature_settings = store.get_feature_settings(user["id"])
    virtual_enabled = bool(feature_settings.get("virtual_smarthome_enabled", True))
    real_rooms = store.list_relay_rooms(user["id"])

    use_real = (real_rooms and not virtual_enabled)

    if use_real and real_rooms:
        action_cmd = "ON" if cmd.state else "OFF"
        control_all_real_relays(user["id"], action_cmd, source="mobile_app")
    else:
        set_all_smart_home_relays(user["id"], cmd.state)

    new_data = build_smarthome_response(user["id"])
    action = "dinyalakan" if cmd.state else "dimatikan"
    return ApiResponse(
        success=True,
        data=new_data,
        message=f"Semua relay berhasil {action}."
    )


@router.post("/mode", response_model=ApiResponse)
async def switch_mode(request: Request, cmd: ModeSwitchCommand):
    """Switch between 'real' (Relay Fisik IoT) and 'virtual' (Simulasi Smart Home)."""
    user = require_user(request)
    store = get_store()
    virtual_enabled = (cmd.mode.lower() == "virtual")
    store.set_feature_settings(user["id"], {"virtual_smarthome_enabled": virtual_enabled})

    new_data = build_smarthome_response(user["id"])
    mode_label = "Simulasi Virtual" if virtual_enabled else "Relay Fisik Nyata"
    return ApiResponse(
        success=True,
        data=new_data,
        message=f"Mode Smart Home beralih ke: {mode_label}"
    )
