from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse

from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    render,
    require_user,
    validate_csrf,
    redirect_with_message,
)
from xiaozhi.services.mcp_service import is_mcp_connected, mcp_status_payload
from xiaozhi.services.smarthome_service import (
    get_smart_home_state,
    set_all_smart_home_relays,
    set_smart_home_relay,
    smart_home_payload,
)
from xiaozhi.database.models import SmartHomeRelayCommand, SmartHomeAllCommand

router = APIRouter()


@router.get("/simulasi-smarthome-virtual", response_class=HTMLResponse)
async def smarthome_page(request: Request):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    role = str(user.get("role") or "user").lower()
    if role != "admin" and not is_mcp_connected(user["id"]):
        return redirect_with_message("/login", "Endpoint WebSocket MCP wajib dihubungkan sebelum mengakses Smart Home.")
    store = get_store()
    token_info = store.get_xiaozhi_token_info(user["id"])
    token_saved = bool(token_info)
    feature_settings = store.get_feature_settings(user["id"])
    virtual_enabled = bool(feature_settings.get("virtual_smarthome_enabled", True))
    mcp_connected = is_mcp_connected(user["id"])
    payload = smart_home_payload(
        user["id"],
        token_saved=token_saved,
        mcp_connected=mcp_connected,
        virtual_enabled=virtual_enabled,
    )
    mcp_status = mcp_status_payload(
        user["id"],
        token_saved=token_saved,
        token_preview=token_info.get("preview", "") if token_info else "",
        token_hash=token_info.get("token_hash", "") if token_info else "",
    )
    initial_state = {
        "relays": payload["relays"],
        "activeCount": payload["activeCount"],
        "totalCount": payload["totalCount"],
        "mcpConnected": payload["mcpConnected"],
        "tokenSaved": payload["tokenSaved"],
        "virtualEnabled": payload["virtualEnabled"],
        "updatedAt": payload["updatedAt"],
    }
    return render(
        request,
        "smarthome.html",
        {
            "user": user,
            "relays": payload["relays"],
            "active_count": payload["activeCount"],
            "total_count": payload["totalCount"],
            "mcp_connected": payload["mcpConnected"],
            "token_saved": payload["tokenSaved"],
            "virtual_enabled": payload["virtualEnabled"],
            "updated_at": payload["updatedAt"],
            "mcp_token_saved": token_saved,
            "mcp_token_preview": token_info.get("preview", "") if token_info else "",
            "mcp_status": mcp_status,
            "initial_state": initial_state,
            "message": request.query_params.get("message", ""),
            "active_page": "smarthome",
        },
    )


@router.get("/api/smarthome/state")
async def smarthome_state(request: Request):
    user = require_user(request)
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
    return payload


@router.post("/api/smarthome/relay")
async def smarthome_relay(request: Request, cmd: SmartHomeRelayCommand):
    user = require_user(request)
    store = get_store()
    feature_settings = store.get_feature_settings(user["id"])
    if not bool(feature_settings.get("virtual_smarthome_enabled", True)):
        return JSONResponse({"success": False, "message": "Simulasi Smart Home Virtual sedang nonaktif."}, status_code=400)
    try:
        state = set_smart_home_relay(user["id"], cmd.channel, cmd.state)
        return {"success": True, "state": state}
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)


@router.post("/api/smarthome/all")
async def smarthome_all(request: Request, cmd: SmartHomeAllCommand):
    user = require_user(request)
    store = get_store()
    feature_settings = store.get_feature_settings(user["id"])
    if not bool(feature_settings.get("virtual_smarthome_enabled", True)):
        return JSONResponse({"success": False, "message": "Simulasi Smart Home Virtual sedang nonaktif."}, status_code=400)
    state = set_all_smart_home_relays(user["id"], cmd.state)
    return {"success": True, "state": state}
