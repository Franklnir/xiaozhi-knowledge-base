import asyncio
import logging
import re
import time
import urllib.parse
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, Dict, Optional, Tuple

from xiaozhi.mcp.context import (
    mcp_active_owner_ctx,
    mcp_request_id_ctx,
    mcp_active_slot_ctx,
    mcp_active_token_hash_ctx,
    mcp_active_mac_ctx,
)
from xiaozhi.services import mcp_service
from xiaozhi.services.mcp_service import (
    mcp_bridge_tasks,
    set_mcp_connection_state,
    signal_mcp_reload,
)

logger = logging.getLogger("xiaozhi.mcp.bridge")

# ── Failure Backoff & Cooldown Tracking ────────────────────────────────────
mcp_bridge_failure_counts: Dict[str, int] = {}
mcp_bridge_failure_cooldowns: Dict[str, float] = {}
_RECONCILE_INTERVAL = 30
_RELOAD_COALESCE_SECONDS = 1
_STABLE_CONNECTION_SECONDS = 60
_MAX_CONCURRENT_HANDSHAKES = 8
_HANDSHAKE_START_INTERVAL = 0.15
_handshake_limit: Optional[asyncio.Semaphore] = None
_handshake_start_lock: Optional[asyncio.Lock] = None
_MCP_PROTOCOL_METHODS = {"initialize", "notifications/initialized", "ping", "tools/list"}


def is_bridge_in_cooldown(task_key: str) -> bool:
    cooldown_until = mcp_bridge_failure_cooldowns.get(task_key, 0)
    return time.time() < cooldown_until


def record_bridge_failure(task_key: str, max_cooldown: int = 300, *, stagger: bool = False) -> float:
    count = min(mcp_bridge_failure_counts.get(task_key, 0) + 1, 5)
    mcp_bridge_failure_counts[task_key] = count
    # Exponential backoff: 30s, 60s, 120s, max 300s
    cooldown = min(max_cooldown, 30 * (2 ** (count - 1)))
    if stagger:
        cooldown = min(max_cooldown, cooldown + hash(task_key) % 15)
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
        if not hostname or parsed.username is not None or parsed.fragment:
            return False, "Format URL endpoint MCP tidak valid."
        if parsed.port == 0:
            return False, "Port endpoint MCP tidak valid."

        # Deteksi URL web console yang sering salah dimasukkan user
        path = parsed.path.rstrip("/")
        if path == "/console" or path.startswith("/console/"):
            return False, "Format URL salah: Mengarah ke halaman web console, bukan WebSocket endpoint MCP (seharusnya wss://api.xiaozhi.me/mcp/?token=...)"
        if hostname == "api.xiaozhi.me" and (
            parsed.scheme != "wss" or not path or not any(
                token.strip() and "..." not in token
                for token in urllib.parse.parse_qs(parsed.query).get("token", [])
            )
        ):
            return False, "Salin URL WebSocket MCP lengkap dari konsol XiaoZhi, termasuk token."

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
            return False, "IP privat lokal tidak dapat dijangkau dari server cloud VPS. Gunakan domain publik."

        return True, ""
    except ValueError:
        return False, "Format URL endpoint MCP tidak valid."


def capture_xiaozhi_ws_chat(owner_id: int, direction: str, raw_message: str, token_hash: str = "", slot: int = 1, device_mac: str = "", request_id: str = ""):
    """Capture WebSocket chat messages for history. Will be injected with store."""
    pass


def set_capture_function(fn):
    global capture_xiaozhi_ws_chat
    capture_xiaozhi_ws_chat = fn


async def start_mcp_background(store):
    """Start the MCP background task for managing WebSocket bridges."""

    from xiaozhi.mcp.server import mcp_server

    if mcp_server is None:
        logger.warning("MCP server not available. Background task not started.")
        return

    global _handshake_limit, _handshake_start_lock
    mcp_service.init_mcp_reload()
    _handshake_limit = asyncio.Semaphore(_MAX_CONCURRENT_HANDSHAKES)
    _handshake_start_lock = asyncio.Lock()

    # Set capture function
    from xiaozhi.core.utils import collect_chat_messages
    import json
    import time

    pending_tool_calls: Dict[tuple, Dict[str, Any]] = {}

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
        call_key = (owner_id, slot, request_id, msg_id)
        turn_request_id = (
            f"{request_id}:{msg_id[:32]}"[:64]
            if msg_id
            else str(request_id or "")[:64]
        )

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
                pending_tool_calls[call_key] = {
                    "owner_id": owner_id,
                    "tool_name": called_tool,
                    "user_message": user_message,
                    "request_payload": req_payload,
                    "token_hash": token_hash,
                    "slot": slot,
                    "device_mac": device_mac,
                    "request_id": turn_request_id,
                    "time": now_ts,
                }

        # 2. Outbound tool response from server
        elif direction == "outbound" and msg_id and call_key in pending_tool_calls:
            pending = pending_tool_calls.pop(call_key)
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
                request_id = turn_request_id
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

    return asyncio.create_task(mcp_background_task(store, mcp_server), name="mcp-bridge-supervisor")


async def mcp_background_task(store, mcp_server):
    """Background task that manages MCP WebSocket bridges for all users."""
    while True:
        try:
            reload_event = mcp_service.mcp_reload_event
            tokens = await asyncio.to_thread(store.list_xiaozhi_tokens)
            blocked_users = {}
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

                    # Check if token slot is active (Akses Plus)
                    if not token_info.get("is_active", True):
                        continue
                    if user_id not in blocked_users:
                        blocked_users[user_id] = await asyncio.to_thread(store.is_mcp_blocked, user_id)
                    if reload_event is not None and reload_event.is_set():
                        break  # Re-read credentials and permissions after a change.
                    if blocked_users[user_id]:
                        continue
                    # A manual connect can start while the database check is in flight.
                    current = mcp_bridge_tasks.get(task_key)
                    if current is not None and not current.done():
                        continue

                    url = token_info["token"]
                    from xiaozhi.core.security import normalize_token_hash, xiaozhi_token_hash
                    token_hash = normalize_token_hash(token_info.get("token_hash", "")) or xiaozhi_token_hash(url)
                    label = token_info.get("device_label", f"XiaoZhi {slot}") or f"XiaoZhi {slot}"
                    board_mac = token_info.get("board_mac", "") or ""

                    # Validasi URL token sebelum mencoba koneksi
                    is_valid, err_msg = validate_mcp_ws_url(url)
                    if not is_valid:
                        set_mcp_connection_state(user_id, token_hash, connected=False, failure_code="invalid_endpoint", message=err_msg, slot=slot, device_label=label, board_mac=board_mac)
                        record_bridge_failure(task_key, max_cooldown=300)
                        logger.warning("Token tidak valid user_id=%s slot=%s (%s): %s", user_id, slot, label, err_msg)
                        continue

                    set_mcp_connection_state(user_id, token_hash, connected=False, message=f"Menghubungkan {label} (Slot {slot})...", slot=slot, device_label=label, board_mac=board_mac)
                    task = asyncio.create_task(run_mcp_bridge(store, mcp_server, user_id, url, token_hash, slot=slot, device_label=label, board_mac=board_mac))
                    mcp_bridge_tasks[task_key] = task
                except Exception as item_err:
                    logger.error("Gagal memproses token MCP per-user: user_id=%s slot=%s error_type=%s", token_info.get("user_id"), token_info.get("slot_number"), type(item_err).__name__)

            # Wait for reload signal OR timeout (check for new users every 30s)
            if reload_event is not None:
                try:
                    await asyncio.wait_for(reload_event.wait(), timeout=_RECONCILE_INTERVAL)
                    await asyncio.sleep(_RELOAD_COALESCE_SECONDS)
                    reload_event.clear()
                except asyncio.TimeoutError:
                    pass  # Timeout is normal - just loop to check for new users
            else:
                await asyncio.sleep(_RECONCILE_INTERVAL)
        except Exception as exc:
            logger.error("Gagal menjalankan background MCP: error_type=%s", type(exc).__name__)
            await asyncio.sleep(10)


async def stop_mcp_background(supervisor):
    if supervisor is None:
        return
    supervisor.cancel()
    await asyncio.gather(supervisor, return_exceptions=True)
    bridges = tuple(mcp_bridge_tasks.values())
    for task in bridges:
        task.cancel()
    await asyncio.gather(*bridges, return_exceptions=True)
    mcp_bridge_tasks.clear()
    mcp_service.clear_mcp_reload()


async def run_mcp_bridge(store, mcp_server, user_id: int, url: str, token_hash: str, slot: int = 1, device_label: str = "XiaoZhi", board_mac: str = ""):
    """Run a single MCP WebSocket bridge for a user's slot."""
    try:
        from websockets import exceptions as ws_exceptions
    except ImportError:
        logger.error("MCP/WebSocket packages not available.")
        return

    from xiaozhi.core.security import normalize_token_hash, xiaozhi_token_hash

    token_hash = normalize_token_hash(token_hash) or xiaozhi_token_hash(url)
    task_key = f"{user_id}:{slot}"
    request_id = f"mcp-{user_id}-s{slot}-{uuid.uuid4().hex[:6]}"
    valid, message = validate_mcp_ws_url(url)
    if not valid:
        record_bridge_failure(task_key)
        set_mcp_connection_state(user_id, token_hash, connected=False, failure_code="invalid_endpoint", message=message, request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac)
        if mcp_bridge_tasks.get(task_key) is asyncio.current_task():
            mcp_bridge_tasks.pop(task_key, None)
        return
    logger.info("[%s] MCP bridge start: user_id=%s slot=%s", request_id, user_id, slot)
    ctx_token = mcp_active_owner_ctx.set(user_id)
    req_token = mcp_request_id_ctx.set(request_id)
    slot_token = mcp_active_slot_ctx.set(int(slot or 1))
    hash_token = mcp_active_token_hash_ctx.set(token_hash or "")
    mac_token = mcp_active_mac_ctx.set(board_mac or "")
    connected_at = None
    failure_code = None
    close_code = None
    message = ""
    cooldown = None
    cancelled = False
    try:
        async with websocket_client_server(user_id, url, token_hash, slot=slot, device_mac=board_mac, request_id=request_id) as (read_stream, write_stream):
            connected_at = time.monotonic()
            set_mcp_connection_state(user_id, token_hash, connected=True, message=f"{device_label} terhubung", request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac)
            logger.info("[%s] MCP bridge CONNECTED: user_id=%s slot=%s", request_id, user_id, slot)
            try:
                from xiaozhi.services.sse_service import log_admin_event
                log_admin_event("mcp", f"WebSocket MCP Terhubung (User ID: {user_id}, Slot: {slot}, {device_label})", {"user_id": user_id, "slot": slot, "device_mac": board_mac, "request_id": request_id, "status": "connected"})
            except Exception:
                pass
            await mcp_server._mcp_server.run(
                read_stream, write_stream,
                mcp_server._mcp_server.create_initialization_options(),
            )
            failure_code = "closed"
    except asyncio.CancelledError:
        cancelled = True
        raise
    except ws_exceptions.InvalidStatus as exc:
        status_code = exc.response.status_code
        message = {
            401: "Token MCP ditolak (HTTP 401). Salin ulang endpoint dari konsol XiaoZhi.",
            200: "Endpoint mengembalikan HTTP 200 tanpa upgrade WebSocket. Periksa URL endpoint MCP.",
        }.get(status_code, "")
        failure_code = {401: "unauthorized", 200: "no_upgrade"}.get(status_code, "handshake")
        if message:
            cooldown = 900
        else:
            message = f"HTTP {status_code} dari XiaoZhi"
        logger.warning("[%s] MCP bridge HTTP error: user_id=%s slot=%s status=%s", request_id, user_id, slot, status_code)
    except ws_exceptions.ConnectionClosed as exc:
        failure_code = "closed"
        close_code = exc.rcvd.code if exc.rcvd else 1006
        message = f"WebSocket ditutup: {close_code}"
    except OSError:
        failure_code = "network"
        message = "Koneksi jaringan MCP gagal"
    except Exception as exc:
        failure_code = "connection"
        message = "Koneksi MCP gagal"
        logger.warning("[%s] MCP bridge unexpected error: user_id=%s slot=%s error_type=%s", request_id, user_id, slot, type(exc).__name__)
    finally:
        mcp_active_owner_ctx.reset(ctx_token)
        mcp_request_id_ctx.reset(req_token)
        mcp_active_slot_ctx.reset(slot_token)
        mcp_active_token_hash_ctx.reset(hash_token)
        mcp_active_mac_ctx.reset(mac_token)
        registered_task = mcp_bridge_tasks.get(task_key)
        if registered_task is asyncio.current_task():
            mcp_bridge_tasks.pop(task_key, None)

        if not cancelled and failure_code and (registered_task is None or registered_task is asyncio.current_task()):
            connected_seconds = 0 if connected_at is None else time.monotonic() - connected_at
            if connected_seconds >= _STABLE_CONNECTION_SECONDS:
                reset_bridge_failure(task_key)
            if cooldown is None:
                cooldown = record_bridge_failure(task_key, stagger=connected_at is not None)
                message = f"{message or 'Koneksi MCP terputus'} (coba lagi paling cepat {int(cooldown)} detik)"
            else:
                mcp_bridge_failure_cooldowns[task_key] = time.time() + cooldown
            set_mcp_connection_state(
                user_id, token_hash, connected=False, failure_code=failure_code, message=message,
                request_id=request_id, slot=slot, device_label=device_label, board_mac=board_mac,
            )
            logger.info(
                "[%s] MCP bridge end: user_id=%s slot=%s failure=%s close_code=%s connected_s=%.1f cooldown_s=%s",
                request_id, user_id, slot, failure_code, close_code, connected_seconds, int(cooldown),
            )


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

    async with AsyncExitStack() as stack:
        connection = websockets.connect(
            url, open_timeout=30, ping_interval=25, ping_timeout=60,
            close_timeout=5, max_size=2**20,
            additional_headers={"User-Agent": "XiaozhiIndonesia-MCP/1.0"},
        )
        if _handshake_limit is None:
            ws = await stack.enter_async_context(connection)
        else:
            async with _handshake_limit:
                if _handshake_start_lock is not None:
                    async with _handshake_start_lock:
                        await asyncio.sleep(_HANDSHAKE_START_INTERVAL)
                ws = await stack.enter_async_context(connection)

        transport_error = None

        def capture(direction, raw_message):
            asyncio.create_task(asyncio.to_thread(
                capture_xiaozhi_ws_chat, user_id, direction, raw_message, token_hash, slot, device_mac, request_id,
            ))

        async def ws_reader():
            nonlocal transport_error
            try:
                async with read_stream_writer:
                    while True:
                        message = await ws.recv()
                        try:
                            msg = types.JSONRPCMessage.model_validate_json(message)
                        except ValueError:
                            capture("inbound", message)
                            await read_stream_writer.send(ValueError("Pesan JSON-RPC MCP tidak valid."))
                            continue
                        if getattr(msg.root, "method", None) not in _MCP_PROTOCOL_METHODS:
                            capture("inbound", message)
                        await read_stream_writer.send(SessionMessage(msg))
            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                transport_error = exc
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                pass  # The writer or MCP session already closed the stream.

        async def ws_writer():
            nonlocal transport_error
            try:
                async with write_stream_reader:
                    async for session_message in write_stream_reader:
                        message = session_message.message
                        json_str = message.model_dump_json(by_alias=True, exclude_none=True)
                        result = getattr(message.root, "result", None)
                        if result is None or not (
                            result.keys() <= {"tools", "nextCursor", "_meta"} or
                            result.keys() <= {"protocolVersion", "capabilities", "serverInfo", "instructions", "_meta"}
                        ):
                            capture("outbound", json_str)
                        await ws.send(json_str)
            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                transport_error = exc
                await read_stream_writer.aclose()

        async with anyio.create_task_group() as tg:
            tg.start_soon(ws_reader)
            tg.start_soon(ws_writer)
            try:
                yield read_stream, write_stream
            finally:
                tg.cancel_scope.cancel()
        if transport_error is not None:
            raise transport_error
