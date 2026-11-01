import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("xiaozhi.mcp.server")

# MCP server instance - will be initialized if mcp package is available
mcp_server = None
VIRTUAL_SMARTHOME_MCP_TOOLS = {
    "control_relay",
    "control_smart_home_room",
    "get_relay_status",
    "all_relays_on",
    "all_relays_off",
}
REAL_RELAY_MCP_TOOLS = {
    "control_real_relay_by_voice",
    "get_real_relay_status",
    "all_real_relays_on",
    "all_real_relays_off",
}


def init_mcp_server(store, youtube_search_fn=None):
    """Initialize the MCP server and register tools."""
    global mcp_server

    try:
        from mcp.server.fastmcp import FastMCP
        from xiaozhi.mcp.context import mcp_active_owner_ctx
        from xiaozhi.mcp.tools import register_tools
        from xiaozhi.services.mcp_service import record_mcp_tool_history_to_store

        mcp_server = FastMCP("XiaozhiIndonesia")

        # Register all tools
        register_tools(mcp_server, store, record_mcp_tool_history_to_store, youtube_search_fn)

        # Setup mode filtering
        setup_mode_filtering(store)

        logger.info("MCP server initialized with tools")
        return mcp_server
    except ImportError:
        logger.warning("MCP package not available. MCP server disabled.")
        return None


def handle_virtual_tool_with_real_relay(owner_id: int, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from xiaozhi.routers.relay_nyata import control_real_relay, control_all_real_relays, real_relay_status_payload, match_real_relay_for_target
    from xiaozhi.services.mcp_service import record_mcp_tool_history_to_store as record_mcp_tool_history
    from xiaozhi.config import SMART_HOME_RELAYS
    from xiaozhi.services.smarthome_service import smart_home_room_name

    if name == "get_relay_status":
        response = {"success": True, "message": "Status Relay Nyata berhasil dibaca. Tool simulasi diabaikan karena mode Relay Nyata aktif.", **real_relay_status_payload(owner_id)}
        record_mcp_tool_history(owner_id, name, "status relay", arguments, response)
        return response
    if name == "all_relays_on":
        try:
            response = control_all_real_relays(owner_id, "ON", source="mcp_voice")
        except ValueError as exc:
            response = {"success": False, "message": str(exc)}
        record_mcp_tool_history(owner_id, name, "nyalakan semua relay", arguments, response)
        return response
    if name == "all_relays_off":
        try:
            response = control_all_real_relays(owner_id, "OFF", source="mcp_voice")
        except ValueError as exc:
            response = {"success": False, "message": str(exc)}
        record_mcp_tool_history(owner_id, name, "matikan semua relay", arguments, response)
        return response

    target = arguments.get("target", "")
    action = arguments.get("action", "")
    channel = arguments.get("channel")
    if name == "control_relay":
        if not target and channel:
            channel_text = str(channel or "").strip()
            if channel_text in SMART_HOME_RELAYS:
                target = smart_home_room_name(channel_text)
    matched = match_real_relay_for_target(owner_id, target=target, action=action, channel=channel)
    if not matched:
        response = {"success": False, "message": "Perintah suara tidak cocok dengan daftar Relay Nyata yang tersimpan."}
        record_mcp_tool_history(owner_id, name, f"{target} {action}".strip(), arguments, response)
        return response
    if matched.get("is_all"):
        target_room_id = matched["room"]["id"] if matched.get("room") else None
        response = control_all_real_relays(owner_id, matched["command"], room_id=target_room_id, source="mcp_voice")
    else:
        response = control_real_relay(owner_id, matched["room"], matched["relay"], matched["command"], source="mcp_voice")
    response["routed_from"] = name
    record_mcp_tool_history(owner_id, name, f"{target} {action}".strip(), arguments, response)
    return response


def handle_real_tool_with_virtual_relay(owner_id: int, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from xiaozhi.services.mcp_service import record_mcp_tool_history_to_store as record_mcp_tool_history
    from xiaozhi.services.smarthome_service import (
        smart_home_status,
        set_all_smart_home_relays,
        set_smart_home_relay,
        resolve_smart_home_target,
        parse_smart_home_action,
        smart_home_room_name,
    )

    if name == "get_real_relay_status":
        response = {"success": True, "message": "Status Simulasi Smart Home Virtual berhasil dibaca.", "relays": smart_home_status(owner_id)}
        record_mcp_tool_history(owner_id, name, "status relay virtual", arguments, response)
        return response
    if name == "all_real_relays_on":
        set_all_smart_home_relays(owner_id, True)
        response = {"success": True, "message": "Semua perangkat smarthome virtual berhasil dinyalakan."}
        record_mcp_tool_history(owner_id, name, "nyalakan semua relay", arguments, response)
        return response
    if name == "all_real_relays_off":
        set_all_smart_home_relays(owner_id, False)
        response = {"success": True, "message": "Semua perangkat smarthome virtual berhasil dimatikan."}
        record_mcp_tool_history(owner_id, name, "matikan semua relay", arguments, response)
        return response

    if name == "control_real_relay_by_voice":
        user_msg = str(arguments.get("user_message", "")).strip()
        norm_msg = user_msg.lower()
        if "semua" in norm_msg or "all" in norm_msg or "seluruh" in norm_msg:
            try:
                action_bool = parse_smart_home_action(norm_msg)
            except ValueError:
                action_bool = not any(w in norm_msg for w in ["mati", "off", "padam", "nonaktif", "stop"])
            set_all_smart_home_relays(owner_id, action_bool)
            status_txt = "dinyalakan" if action_bool else "dimatikan"
            response = {
                "success": True,
                "message": f"Semua perangkat smarthome virtual berhasil {status_txt}.",
                "routed_from": name,
            }
            record_mcp_tool_history(owner_id, name, user_msg, arguments, response)
            return response

        channel, error = resolve_smart_home_target(user_msg)
        if error or channel is None:
            response = {"success": False, "message": error or "Target ruangan di simulator tidak dikenali."}
            record_mcp_tool_history(owner_id, name, user_msg, arguments, response)
            return response
        try:
            enabled = parse_smart_home_action(user_msg)
            set_smart_home_relay(owner_id, channel, enabled)
            room_name = smart_home_room_name(channel)
            status = "ON" if enabled else "OFF"
            extra = " Alarm aktif." if channel == 6 and enabled else ""
            response = {
                "success": True,
                "message": f"{room_name} berhasil diubah menjadi {status} di simulator virtual.{extra}",
                "channel": channel,
                "room": room_name,
                "state": enabled,
                "routed_from": name,
            }
            record_mcp_tool_history(owner_id, name, user_msg, arguments, response)
            return response
        except ValueError as exc:
            response = {"success": False, "message": str(exc)}
            record_mcp_tool_history(owner_id, name, user_msg, arguments, response)
            return response

    return {"success": False, "message": f"Tool '{name}' dialihkan namun tidak didukung di simulator."}


def setup_mode_filtering(store):
    """Setup tool filtering based on virtual/real relay mode."""
    if mcp_server is None:
        return

    from xiaozhi.mcp.context import mcp_active_owner_ctx

    original_mcp_list_tools = mcp_server.list_tools
    original_mcp_call_tool = mcp_server.call_tool

    def active_virtual_smarthome_enabled() -> Optional[bool]:
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return None
        return bool(store.get_feature_settings(owner_id).get("virtual_smarthome_enabled", True))

    def tool_allowed_for_active_mode(tool_name: str) -> bool:
        virtual_enabled = active_virtual_smarthome_enabled()
        if virtual_enabled is None:
            virtual_enabled = True
        if virtual_enabled:
            return tool_name not in REAL_RELAY_MCP_TOOLS
        return tool_name not in VIRTUAL_SMARTHOME_MCP_TOOLS

    def is_tool_allowed_for_user(owner_id: Optional[int], tool_name: str) -> bool:
        if not tool_allowed_for_active_mode(tool_name):
            return False
        if owner_id is not None:
            # Check user features (e.g. youtube_music)
            features = store.get_user_features(owner_id)
            if tool_name in {"play_youtube_song", "play_playlist_song", "list_user_playlist", "get_playlist_top_played", "get_playlist_cache_status"} and not features.get("youtube_music", True):
                return False
            # Check tool toggles set by admin
            if hasattr(store, "get_mcp_tool_toggles"):
                toggles = store.get_mcp_tool_toggles(owner_id)
                if not toggles.get(tool_name, True):
                    return False
        return True

    async def list_mode_filtered_tools():
        tools = await original_mcp_list_tools()
        owner_id = mcp_active_owner_ctx.get()
        return [tool for tool in tools if is_tool_allowed_for_user(owner_id, tool.name)]

    async def call_mode_filtered_tool(name: str, arguments: Dict[str, Any]):
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is not None:
            features = store.get_user_features(owner_id)
            if name in {"play_youtube_song", "play_playlist_song", "list_user_playlist", "get_playlist_top_played", "get_playlist_cache_status"} and not features.get("youtube_music", True):
                return {"success": False, "message": "Anda tidak diizinkan putar lagu atau cek playlist. Fitur YouTube Music telah dinonaktifkan oleh administrator."}
            if hasattr(store, "get_mcp_tool_toggles"):
                toggles = store.get_mcp_tool_toggles(owner_id)
                if not toggles.get(name, True):
                    return {"success": False, "message": f"Tool '{name}' telah dinonaktifkan oleh administrator untuk akun Anda."}

        if not tool_allowed_for_active_mode(name):
            virtual_enabled = active_virtual_smarthome_enabled()
            if virtual_enabled or virtual_enabled is None:
                if owner_id is not None and name in REAL_RELAY_MCP_TOOLS:
                    return handle_real_tool_with_virtual_relay(int(owner_id), name, arguments or {})
                return {"success": False, "message": "Relay Nyata sedang nonaktif karena Simulasi Smart Home Virtual aktif."}
            if owner_id is not None and name in VIRTUAL_SMARTHOME_MCP_TOOLS:
                return handle_virtual_tool_with_real_relay(int(owner_id), name, arguments or {})
            return {"success": False, "message": "Tool Simulasi Smart Home Virtual sedang nonaktif agar tidak mengganggu Relay Nyata."}
        res = await original_mcp_call_tool(name, arguments)
        try:
            from xiaozhi.services.sse_service import log_admin_event
            log_admin_event("mcp", f"Tool '{name}' dipanggil oleh User {owner_id}", {
                "user_id": owner_id,
                "tool": name,
                "arguments": arguments or {},
            })
        except Exception:
            pass
        return res

    mcp_server._mcp_server.list_tools()(list_mode_filtered_tools)
    mcp_server._mcp_server.call_tool(validate_input=False)(call_mode_filtered_tool)

