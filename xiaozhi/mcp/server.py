import asyncio
import functools
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
_BLOCKING_SEARCH_TOOLS = {
    "research_youtube_information", "find_youtube_video_to_play",
    "search_web", "search_web_deep", "search_news", "search_places",
    "search_social_media", "search_course_materials", "read_live_api_data",
    "get_weather_bmkg", "get_earthquake_info", "lookup_kbbi", "search_wikipedia",
    "convert_currency", "search_academic_papers", "autonomous_deep_research", "deep_research_workspace",
}
_MUSIC_TOOLS = {
    "play_youtube_song", "play_playlist_song", "list_user_playlist",
    "get_playlist_top_played", "get_playlist_cache_status",
}


def init_mcp_server(store, youtube_search_fn=None):
    """Initialize the MCP server and register tools."""
    global mcp_server

    try:
        from mcp.server.fastmcp import FastMCP
        from xiaozhi.mcp.tools import register_tools
        from xiaozhi.services.mcp_service import record_mcp_tool_history_to_store

        mcp_server = FastMCP("XiaozhiIndonesia")

        register_tools(_SearchToolRegistrar(mcp_server), store, record_mcp_tool_history_to_store, youtube_search_fn)

        # Setup mode filtering
        setup_mode_filtering(store)

        logger.info("MCP server initialized with tools")
        return mcp_server
    except ImportError:
        logger.warning("MCP package not available. MCP server disabled.")
        return None


class _SearchToolRegistrar:
    """Keep blocking searches off the server loop without changing tool schemas."""

    def __init__(self, server):
        self.server = server
        self.limit = asyncio.Semaphore(2)

    def _search_finished(self, work):
        self.limit.release()
        if not work.cancelled():
            work.exception()  # The client might have disconnected.

    def tool(self, *args, **kwargs):
        register = self.server.tool(*args, **kwargs)

        def decorate(fn):
            if fn.__name__ in _BLOCKING_SEARCH_TOOLS:

                @functools.wraps(fn)
                async def run_search(*call_args, **call_kwargs):
                    await self.limit.acquire()
                    # A disconnected client can exit while its search still occupies capacity.
                    work = asyncio.create_task(asyncio.to_thread(fn, *call_args, **call_kwargs))
                    work.add_done_callback(self._search_finished)
                    return await asyncio.shield(work)

                register(run_search)
                return fn
            return register(fn)

        return decorate


def handle_virtual_tool_with_real_relay(owner_id: int, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    from xiaozhi.routers.relay_nyata import (
        control_all_real_relays,
        control_real_relay,
        match_real_relay_for_target_detailed,
        real_relay_status_payload,
    )
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
    matched = match_real_relay_for_target_detailed(owner_id, target=target, action=action, channel=channel)
    if not matched:
        response = {"success": False, "message": "Perintah suara tidak cocok dengan daftar Relay Nyata yang tersimpan."}
        record_mcp_tool_history(owner_id, name, f"{target} {action}".strip(), arguments, response)
        return response
    if matched.get("ambiguous"):
        response = {"success": False, "ambiguous": True, "message": matched["message"]}
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

    def tool_allowed_for_mode(tool_name: str, virtual_enabled: bool) -> bool:
        if virtual_enabled:
            return tool_name not in REAL_RELAY_MCP_TOOLS
        return tool_name not in VIRTUAL_SMARTHOME_MCP_TOOLS

    def load_tool_policy(owner_id: Optional[int]):
        if owner_id is None:
            return True, True, {}
        features = store.get_user_features(owner_id)
        virtual = store.get_feature_settings(owner_id).get("virtual_smarthome_enabled", True)
        toggles = store.get_mcp_tool_toggles(owner_id) if hasattr(store, "get_mcp_tool_toggles") else {}
        return bool(virtual), features.get("youtube_music", True), toggles

    async def list_mode_filtered_tools():
        tools = await original_mcp_list_tools()
        virtual_enabled, music_enabled, toggles = await asyncio.to_thread(load_tool_policy, mcp_active_owner_ctx.get())
        return [
            tool for tool in tools if tool_allowed_for_mode(tool.name, virtual_enabled)
            and (music_enabled or tool.name not in _MUSIC_TOOLS) and toggles.get(tool.name, True)
        ]

    async def call_mode_filtered_tool(name: str, arguments: Dict[str, Any]):
        # Tool name alias resolution agar LLM fleksibel memanggil fungsi
        tool_aliases = {
            "get_conversation_history": "get_chat_history",
            "riwayat_percakapan": "get_chat_history",
            "baca_riwayat_chat": "get_chat_history",
            "read_chat_history": "get_chat_history",
            "baca_nada_emosi": "read_emotion_tone",
            "analyze_emotion_tone": "read_emotion_tone",
            "detect_emotion": "read_emotion_tone",
            "pesona_karakter": "get_character_persona",
            "character_persona": "get_character_persona",
        }
        if name in tool_aliases:
            name = tool_aliases[name]

        owner_id = mcp_active_owner_ctx.get()
        virtual_enabled, music_enabled, toggles = await asyncio.to_thread(load_tool_policy, owner_id)
        if owner_id is not None:
            if name in _MUSIC_TOOLS and not music_enabled:
                return {"success": False, "message": "Anda tidak diizinkan putar lagu atau cek playlist. Fitur YouTube Music telah dinonaktifkan oleh administrator."}
            if not toggles.get(name, True):
                return {"success": False, "message": f"Tool '{name}' telah dinonaktifkan oleh administrator untuk akun Anda."}

        if not tool_allowed_for_mode(name, virtual_enabled):
            if virtual_enabled:
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

