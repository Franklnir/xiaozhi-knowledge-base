import asyncio
import logging
import re
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Any, Dict, Optional, Tuple

from xiaozhi.mcp.context import (
    mcp_active_owner_ctx,
    mcp_request_id_ctx,
    mcp_active_slot_ctx,
    mcp_active_token_hash_ctx,
    mcp_active_mac_ctx,
)
from xiaozhi.services.mcp_service import (
    mcp_bridge_tasks,
    mcp_reload_event,
    set_mcp_connection_state,
    signal_mcp_reload,
)

logger = logging.getLogger("xiaozhi.mcp.bridge")

# ── Failure Backoff & Cooldown Tracking ────────────────────────────────────
mcp_bridge_failure_counts: Dict[str, int] = {}
mcp_bridge_failure_cooldowns: Dict[str, float] = {}


def is_bridge_in_cooldown(task_key: str) -> bool:
    cooldown_until = mcp_bridge_failure_cooldowns.get(task_key, 0)
    return time.time() < cooldown_until


def record_bridge_failure(task_key: str, max_cooldown: int = 300) -> float:
    count = mcp_bridge_failure_counts.get(task_key, 0) + 1
    mcp_bridge_failure_counts[task_key] = count
    # Exponential backoff: 30s, 60s, 120s, max 300s
    cooldown = min(max_cooldown, 30 * (2 ** (min(count, 4) - 1)))
    mcp_bridge_failure_cooldowns[task_key] = time.time() + cooldown
    return cooldown


def reset_bridge_failure(task_key: str):
    mcp_bridge_failure_counts.pop(task_key, None)
    mcp_bridge_failure_cooldowns.pop(task_key, None)


def validate_mcp_ws_url(url: str) -> Tuple[bool, str]:
    cleaned = (url or "").strip()
    if not cleaned.startswith("wss://") and not cleaned.startswith("ws://"):
        return False, "Token bukan wss:// atau ws://"

    try:
        parsed = urllib.parse.urlparse(cleaned)
        hostname = (parsed.hostname or "").lower()

        # Deteksi URL web console yang sering salah dimasukkan user
        if "/console/" in parsed.path or parsed.path.rstrip("/") == "/console/agents":
            return False, "Format URL salah: Mengarah ke halaman web console, bukan WebSocket endpoint MCP (seharusnya wss://api.xiaozhi.me/mcp/?token=...)"

        # Deteksi IP privat lokal di environment VPS / Production
        import os
        from xiaozhi.config import IS_PRODUCTION
        is_private = (
            hostname in ("localhost", "127.0.0.1") or
            hostname.startswith("192.168.") or
            hostname.startswith("10.") or
            bool(re.match(r"^172\.(1[6-9]|2[0-9]|3[0-1])\.", hostname))
        )
        if is_private and (IS_PRODUCTION or os.getenv("ENVIRONMENT") == "production"):
            return False, f"IP privat lokal ({hostname}) tidak dapat dijangkau dari server cloud VPS. Gunakan domain publik."

        return True, ""
    except Exception as exc:
        return False, f"URL tidak valid: {exc}"


def capture_xiaozhi_ws_chat(owner_id: int, direction: str, raw_message: str, token_hash: str = "", slot: int = 1, device_mac: str = "", request_id: str = ""):
    """Capture WebSocket chat messages for history. Will be injected with store."""
    pass


def set_capture_function(fn):
    global capture_xiaozhi_ws_chat
    capture_xiaozhi_ws_chat = fn


async def start_mcp_background(store):
    """Start the MCP background task for managing WebSocket bridges."""
    global mcp_reload_event

    from xiaozhi.mcp.server import mcp_server

    if mcp_server is None:
        logger.warning("MCP server not available. Background task not started.")
        return

    mcp_reload_event = asyncio.Event()

    # Set capture function
    from xiaozhi.core.utils import collect_chat_messages
    import json
    import time

    pending_tool_calls: Dict[str, Dict[str, Any]] = {}

    def _capture(owner_id, direction, raw_message, token_hash="", slot: int = 1, device_mac: str = "", request_id: str = ""):
        try:
            payload = json.loads(raw_message)
        except (TypeError, json.JSONDecodeError):
            return

        tool_name = f"xiaozhi_ws_{direction}"
        user_message = ""
        xiaozhi_answer = ""
        req_payload = None
        resp_payload = None

        msg_id = str(payload.get("id")) if payload.get("id") is not None else None

        # Clean old pending tool calls (> 120s)
        now_ts = time.time()
        for k in list(pending_tool_calls.keys()):
            if now_ts - pending_tool_calls[k].get("time", 0) > 120:
                pending_tool_calls.pop(k, None)

        # 1. Inbound tool call from Xiaozhi
        if direction == "inbound" and payload.get("method") == "tools/call":
            params = payload.get("params") or {}
            called_tool = params.get("name", "tool")
            tool_name = called_tool
            args = params.get("arguments") or {}
            req_payload = args
            user_message = str(args.get("user_message") or args.get("query") or args.get("search_keyword") or args.get("text") or args.get("expression") or args.get("topic") or "")
            if not user_message and called_tool:
                user_message = f"Panggil tool {called_tool}"
            xiaozhi_answer = str(args.get("xiaozhi_answer") or "")

            if msg_id:
                pending_tool_calls[msg_id] = {
                    "owner_id": owner_id,
                    "tool_name": called_tool,
                    "user_message": user_message,
                    "request_payload": req_payload,
                    "token_hash": token_hash,
                    "slot": slot,
                    "device_mac": device_mac,
                    "request_id": request_id,
                    "time": now_ts,
                }

        # 2. Outbound tool response from server
        elif direction == "outbound" and msg_id and msg_id in pending_tool_calls:
            pending = pending_tool_calls.pop(msg_id)
            tool_name = pending["tool_name"]
            user_message = pending["user_message"]
            req_payload = pending["request_payload"]
            slot = pending.get("slot", slot)
            device_mac = pending.get("device_mac", device_mac)
            request_id = pending.get("request_id", request_id)
            resp_payload = payload.get("result") or payload

            res = payload.get("result")
            if isinstance(res, dict) and "content" in res:
                c_list = res.get("content", [])
                xiaozhi_answer = "\n".join(str(c.get("text", "")) for c in c_list if isinstance(c, dict) and c.get("text"))
            elif isinstance(res, (dict, list, str)):
                xiaozhi_answer = str(res)

        # 3. Fallback to generic message collection (STT, TTS, LLM)
        if not user_message and not xiaozhi_answer:
            messages = collect_chat_messages(payload)
            if messages:
                user_message = "\n\n".join(item["content"] for item in messages if item["role"] == "user")
                xiaozhi_answer = "\n\n".join(item["content"] for item in messages if item["role"] == "assistant")
                if direction == "inbound":
                    req_payload = payload
                else:
                    resp_payload = payload

        if not user_message and not xiaozhi_answer and not resp_payload:
            return

        store.upsert_chat_transcript(
            owner_id,
            tool_name=tool_name,
            user_message=user_message,
            xiaozhi_answer=xiaozhi_answer,
            payload=req_payload or (payload if direction == "inbound" else None),
            response_payload=resp_payload or (payload if direction == "outbound" else None),
            token_hash=token_hash,
            slot_number=slot,
            device_mac=device_mac,
            request_id=request_id,
        )

    set_capture_function(_capture)

    asyncio.create_task(mcp_background_task(store, mcp_server))


async def mcp_background_task(store, mcp_server):
    """Background task that manages MCP WebSocket bridges for all users."""
    while True:
        try:
            tokens = store.list_xiaozhi_tokens()
            if not tokens:
                await asyncio.sleep(30)
                continue

            # Launch bridges for users not yet connected (mendukung hingga 3 slot per akun)
            for token_info in tokens:
                try:
                    user_id = int(token_info["user_id"])
                    slot = int(token_info.get("slot_number", 1) or 1)
                    task_key = f"{user_id}:{slot}"
                    if task_key in mcp_bridge_tasks and not mcp_bridge_tasks[task_key].done():
                        continue  # Already running

                    # Check failure cooldown (hindari reconnect loop terus menerus)
                    if is_bridge_in_cooldown(task_key):
                        continue

                    # Check if user MCP is blocked by admin
                    if store.is_mcp_blocked(user_id):
                        logger.info("MCP blocked for user_id=%s, skipping", user_id)
                        continue

                    # Check if token slot is active (Akses Plus)
                    if not token_info.get("is_active", True):
                        logger.info("Slot %s user %s dinonaktifkan admin (Akses Plus), skipping", slot, user_id)
                        continue

                    url = token_info["token"]
                    from xiaozhi.core.security import normalize_token_hash, xiaozhi_token_hash
                    token_hash = normalize_token_hash(token_info.get("token_hash", "")) or xiaozhi_token_hash(url)
                    label = token_info.get("device_label", f"XiaoZhi {slot}") or f"XiaoZhi {slot}"
                    board_mac = token_info.get("board_mac", "") or ""

                    # Validasi URL token sebelum mencoba koneksi
                    is_valid, err_msg = validate_mcp_ws_url(url)
                    if not is_valid:
                        set_mcp_connection_state(user_id, token_hash, connected=False, message=err_msg, slot=slot, device_label=label, board_mac=board_mac)
                        record_bridge_failure(task_key, max_cooldown=300)
                        logger.warning("Token tidak valid user_id=%s slot=%s (%s): %s", user_id, slot, label, err_msg)
                        continue

                    set_mcp_connection_state(user_id, token_hash, connected=False, message=f"Menghubungkan {label} (Slot {slot})...", slot=slot, device_label=label, board_mac=board_mac)
                    logger.info("MCP bridge mencoba: user_id=%s slot=%s label=%s mac=%s", user_id, slot, label, board_mac)
                    task = asyncio.create_task(run_mcp_bridge(store, mcp_server, user_id, url, token_hash, slot=slot, device_label=label, board_mac=board_mac))
                    mcp_bridge_tasks[task_key] = task
                except Exception as item_err:
                    logger.exception("Gagal memproses token MCP per-user: user_id=%s slot=%s err=%s", token_info.get("user_id"), token_info.get("slot_number"), item_err)

            # Wait for reload signal OR timeout (check for new users every 30s)
            if mcp_reload_event:
                mcp_reload_event.clear()
                try:
                    await asyncio.wait_for(mcp_reload_event.wait(), timeout=30)
                    logger.info("MCP reload signal received. Checking for new bridges.")
                except asyncio.TimeoutError:
                    pass  # Timeout is normal - just loop to check for new users
            else:
                await asyncio.sleep(30)
        except Exception:
            logger.exception("Gagal menjalankan background MCP.")
            await asyncio.sleep(10)


async def run_mcp_bridge(store, mcp_server, user_id: int, url: str, token_hash: str, slot: int = 1, device_label: str = "XiaoZhi", board_mac: str = ""):
    """Run a single MCP WebSocket bridge for a user's slot."""
    try:
        import anyio
        import websockets
        from mcp import types
        from mcp.server.session import SessionMessage
    except ImportError:
        logger.error("MCP/WebSocket packages not available.")
        return

    from xiaozhi.core.security import normalize_token_hash, xiaozhi_token_hash

    token_hash = normalize_token_hash(token_hash) or xiaozhi_token_hash(url)
    task_key = f"{user_id}:{slot}"
    request_id = f"mcp-{user_id}-s{slot}-{uuid.uuid4().hex[:6]}"
    masked = url[:50] + "..." if len(url) > 50 else url
    logger.info("[%s] MCP bridge start: user_id=%s slot=%s mac=%s url=%s", request_id, user_id, slot, board_mac, masked)
    ctx_token = mcp_active_owner_ctx.set(user_id)
    req_token = mcp_request_id_ctx.set(request_id)
    slot_token = mcp_active_slot_ctx.set(int(slot or 1))
    hash_token = mcp_active_token_hash_ctx.set(token_hash or "")
    mac_token = mcp_active_mac_ctx.set(board_mac or "")
    was_connected = False
    try:
        async with websocket_client_server(user_id, url, token_hash, slot=slot, device_mac=board_mac, request_id=request_id) as (read_stream, write_stream):
            was_connected = True
            reset_bridge_failure(task_key)
            set_mcp_connection_state(user_id, token_hash, connected=True, message=f"{device_label} terhubung", request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac)
            logger.info("[%s] MCP bridge CONNECTED: user_id=%s slot=%s mac=%s", request_id, user_id, slot, board_mac)
            try:
                from xiaozhi.services.sse_service import log_admin_event
                log_admin_event("mcp", f"WebSocket MCP Terhubung (User ID: {user_id}, Slot: {slot}, {device_label})", {"user_id": user_id, "slot": slot, "device_mac": board_mac, "request_id": request_id, "status": "connected"})
            except Exception:
                pass
            await mcp_server._mcp_server.run(
                read_stream, write_stream,
                mcp_server._mcp_server.create_initialization_options(),
            )
    except websockets.exceptions.InvalidStatusCode as exc:
        cd = record_bridge_failure(task_key)
        logger.error("[%s] MCP bridge HTTP error: user_id=%s slot=%s status=%s (cooldown %ss)", request_id, user_id, slot, exc.status_code, int(cd))
        set_mcp_connection_state(user_id, token_hash, connected=False, message=f"HTTP {exc.status_code} dari XiaoZhi (coba lagi dlm {int(cd)}s)", request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac)
    except websockets.exceptions.ConnectionClosed as exc:
        cd = record_bridge_failure(task_key)
        logger.warning("[%s] MCP bridge closed: user_id=%s slot=%s code=%s reason=%s (cooldown %ss)", request_id, user_id, slot, exc.code, exc.reason, int(cd))
        set_mcp_connection_state(user_id, token_hash, connected=False, message=f"WebSocket ditutup: {exc.code} (coba lagi dlm {int(cd)}s)", request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac)
    except OSError as exc:
        cd = record_bridge_failure(task_key)
        logger.error("[%s] MCP bridge network error: user_id=%s slot=%s err=%s (cooldown %ss)", request_id, user_id, slot, exc, int(cd))
        set_mcp_connection_state(user_id, token_hash, connected=False, message=f"Network error: {exc} (coba lagi dlm {int(cd)}s)", request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac)
    except Exception as exc:
        cd = record_bridge_failure(task_key)
        logger.exception("[%s] MCP bridge unexpected error: user_id=%s slot=%s (cooldown %ss)", request_id, user_id, slot, int(cd))
        set_mcp_connection_state(user_id, token_hash, connected=False, message=f"Error: {str(exc)[:100]}", request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac)
    finally:
        mcp_active_owner_ctx.reset(ctx_token)
        mcp_request_id_ctx.reset(req_token)
        mcp_active_slot_ctx.reset(slot_token)
        mcp_active_token_hash_ctx.reset(hash_token)
        mcp_active_mac_ctx.reset(mac_token)
        mcp_bridge_tasks.pop(task_key, None)

        if was_connected:
            set_mcp_connection_state(user_id, token_hash, connected=False, message=f"{device_label} terputus (mencoba hubungkan kembali...)", request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac)
            try:
                from xiaozhi.services.sse_service import log_admin_event
                log_admin_event("mcp", f"WebSocket MCP Terputus (User ID: {user_id}, Slot: {slot})", {"user_id": user_id, "slot": slot, "device_mac": board_mac, "request_id": request_id, "status": "disconnected"})
            except Exception:
                pass
            logger.info("[%s] MCP bridge selesai: user_id=%s slot=%s (was_connected=True, reconnecting)", request_id, user_id, slot)

            # Trigger background task to reconnect after 3s delay ONLY if it was previously connected
            async def _quick_reload():
                try:
                    await asyncio.sleep(3)
                    signal_mcp_reload()
                except Exception:
                    pass
            asyncio.create_task(_quick_reload())
        else:
            logger.info("[%s] MCP bridge selesai: user_id=%s slot=%s (was_connected=False, cooling down)", request_id, user_id, slot)


@asynccontextmanager
async def websocket_client_server(user_id: int, url: str, token_hash: str, slot: int = 1, device_mac: str = "", request_id: str = ""):
    """Create a WebSocket connection for MCP bridge."""
    try:
        import anyio
        import websockets
        from mcp import types
        from mcp.server.session import SessionMessage
    except ImportError:
        raise RuntimeError("MCP/WebSocket packages not available.")

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async with websockets.connect(
        url, open_timeout=30, ping_interval=25, ping_timeout=60,
        close_timeout=5, max_size=2**20,
        additional_headers={"User-Agent": "XiaozhiIndonesia-MCP/1.0"},
    ) as ws:
        async def ws_reader():
            try:
                async with read_stream_writer:
                    async for message in ws:
                        asyncio.create_task(asyncio.to_thread(capture_xiaozhi_ws_chat, user_id, "inbound", message, token_hash, slot, device_mac, request_id))
                        try:
                            msg = types.JSONRPCMessage.model_validate_json(message)
                            await read_stream_writer.send(SessionMessage(msg))
                        except Exception as exc:
                            await read_stream_writer.send(exc)
            except Exception as exc:
                logger.info("WebSocket reader berhenti: %s (%s)", type(exc).__name__, exc)

        async def ws_writer():
            try:
                async with write_stream_reader:
                    async for session_message in write_stream_reader:
                        json_str = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                        asyncio.create_task(asyncio.to_thread(capture_xiaozhi_ws_chat, user_id, "outbound", json_str, token_hash, slot, device_mac, request_id))
                        await ws.send(json_str)
            except Exception as exc:
                logger.info("WebSocket writer berhenti: %s (%s)", type(exc).__name__, exc)

        async with anyio.create_task_group() as tg:
            tg.start_soon(ws_reader)
            tg.start_soon(ws_writer)
            yield read_stream, write_stream
