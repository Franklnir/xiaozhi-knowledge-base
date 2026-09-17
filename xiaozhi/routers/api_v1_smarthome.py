"""
API v1 Smart Home endpoints for mobile and API clients.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from xiaozhi.dependencies import require_user, get_store, require_mcp_connected_if_not_admin
from xiaozhi.services.mcp_service import is_mcp_connected
from xiaozhi.services.smarthome_service import (
    get_smart_home_state,
    set_all_smart_home_relays,
    set_smart_home_relay,
    smart_home_payload,
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


class AllRelaysCommand(BaseModel):
    state: bool


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/state", response_model=ApiResponse)
async def get_state(request: Request):
    """Get current smart home state for all relays."""
    user = require_user(request)
    require_mcp_connected_if_not_admin(request, user)
    store = get_store()

    feature_settings = store.get_feature_settings(user["id"])
    virtual_enabled = bool(feature_settings.get("virtual_smarthome_enabled", True))
    mcp_connected = is_mcp_connected(user["id"])
    token_info = store.get_xiaozhi_token_info(user["id"])

    payload = smart_home_payload(
        user["id"],
        token_saved=bool(token_info),
        mcp_connected=mcp_connected,
        virtual_enabled=virtual_enabled,
    )

    return ApiResponse(
        success=True,
        data=payload,
        message="OK"
    )


@router.post("/relay", response_model=ApiResponse)
async def control_relay(request: Request, cmd: RelayCommand):
    """Control a specific relay (1-8)."""
    user = require_user(request)
    require_mcp_connected_if_not_admin(request, user)
    store = get_store()

    feature_settings = store.get_feature_settings(user["id"])
    if not bool(feature_settings.get("virtual_smarthome_enabled", True)):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(success=False, message="Simulasi Smart Home Virtual sedang nonaktif.")
        )

    try:
        state = set_smart_home_relay(user["id"], cmd.channel, cmd.state)
        return ApiResponse(
            success=True,
            data={"channel": cmd.channel, "state": cmd.state, "relays": state},
            message=f"Relay {cmd.channel} berhasil diubah."
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=ApiResponse(success=False, message=str(exc)))


@router.post("/all", response_model=ApiResponse)
async def control_all_relays(request: Request, cmd: AllRelaysCommand):
    """Turn all relays on or off."""
    user = require_user(request)
    require_mcp_connected_if_not_admin(request, user)
    store = get_store()

    feature_settings = store.get_feature_settings(user["id"])
    if not bool(feature_settings.get("virtual_smarthome_enabled", True)):
        raise HTTPException(
            status_code=400,
            detail=ApiResponse(success=False, message="Simulasi Smart Home Virtual sedang nonaktif.")
        )

    state = set_all_smart_home_relays(user["id"], cmd.state)
    action = "dinyalakan" if cmd.state else "dimatikan"

    return ApiResponse(
        success=True,
        data={"state": cmd.state, "relays": state},
        message=f"Semua relay berhasil {action}."
    )
