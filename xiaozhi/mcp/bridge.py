import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Optional

from xiaozhi.mcp.context import mcp_active_owner_ctx, mcp_request_id_ctx
from xiaozhi.services.mcp_service import (
    mcp_bridge_tasks,
    mcp_reload_event,
    set_mcp_connection_state,
    signal_mcp_reload,
)

logger = logging.getLogger("xiaozhi.mcp.bridge")


def capture_xiaozhi_ws_chat(owner_id: int, direction: str, raw_message: str, token_hash: str = ""):
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

    def _capture(owner_id, direction, raw_message, token_hash=""):
        try:
            payload = json.loads(raw_message)
        except (TypeError, json.JSONDecodeError):
            return
        messages = collect_chat_messages(payload)
        if not messages:
            return
        user_message = "\n\n".join(item["content"] for item in messages if item["role"] == "user")
        xiaozhi_answer = "\n\n".join(item["content"] for item in messages if item["role"] == "assistant")
        if not user_message and not xiaozhi_answer:
            return
        store.upsert_chat_transcript(owner_id, tool_name=f"xiaozhi_ws_{direction}", user_message=user_message, xiaozhi_answer=xiaozhi_answer, payload=payload, token_hash=token_hash)

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

            # Launch bridges for users not yet connected
            for token_info in tokens:
                user_id = int(token_info["user_id"])
                if user_id in mcp_bridge_tasks and not mcp_bridge_tasks[user_id].done():
                    continue  # Already running
                url = token_info["token"]
                if not url.startswith("wss://"):
                    logger.warning("Token bukan wss://: user_id=%s", user_id)
                    continue
                from xiaozhi.core.security import normalize_token_hash, xiaozhi_token_hash
                token_hash = normalize_token_hash(token_info.get("token_hash", "")) or xiaozhi_token_hash(url)
                set_mcp_connection_state(user_id, token_hash, connected=False, message="Menghubungkan ke XiaoZhi...")
                logger.info("MCP bridge mencoba: user_id=%s", user_id)
                task = asyncio.create_task(run_mcp_bridge(store, mcp_server, user_id, url, token_hash))
                mcp_bridge_tasks[user_id] = task

            # Wait for reload signal - interruptible sleep
            if mcp_reload_event:
                mcp_reload_event.clear()
                await mcp_reload_event.wait()
                logger.info("MCP reload signal received. Restarting affected bridges.")
            else:
                await asyncio.sleep(30)
        except Exception:
            logger.exception("Gagal menjalankan background MCP.")
            await asyncio.sleep(10)


async def run_mcp_bridge(store, mcp_server, user_id: int, url: str, token_hash: str):
    """Run a single MCP WebSocket bridge for a user."""
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
    request_id = f"mcp-{user_id}-{uuid.uuid4().hex[:8]}"
    masked = url[:50] + "..." if len(url) > 50 else url
    logger.info("[%s] MCP bridge start: user_id=%s url=%s", request_id, user_id, masked)
    ctx_token = mcp_active_owner_ctx.set(user_id)
    req_token = mcp_request_id_ctx.set(request_id)
    try:
        async with websocket_client_server(user_id, url, token_hash) as (read_stream, write_stream):
            set_mcp_connection_state(user_id, token_hash, connected=True, message="MCP terhubung ke XiaoZhi", request_id=request_id)
            logger.info("[%s] MCP bridge CONNECTED: user_id=%s", request_id, user_id)
            await mcp_server._mcp_server.run(
                read_stream, write_stream,
                mcp_server._mcp_server.create_initialization_options(),
            )
    except websockets.exceptions.InvalidStatusCode as exc:
        logger.error("[%s] MCP bridge HTTP error: user_id=%s status=%s", request_id, user_id, exc.status_code)
        set_mcp_connection_state(user_id, token_hash, connected=False, message=f"HTTP {exc.status_code} dari XiaoZhi", request_id=request_id)
    except websockets.exceptions.ConnectionClosed as exc:
        logger.warning("[%s] MCP bridge closed: user_id=%s code=%s reason=%s", request_id, user_id, exc.code, exc.reason)
        set_mcp_connection_state(user_id, token_hash, connected=False, message=f"WebSocket ditutup: {exc.code}", request_id=request_id)
    except OSError as exc:
        logger.error("[%s] MCP bridge network error: user_id=%s err=%s", request_id, user_id, exc)
        set_mcp_connection_state(user_id, token_hash, connected=False, message=f"Network error: {exc}", request_id=request_id)
    except Exception as exc:
        logger.exception("[%s] MCP bridge unexpected error: user_id=%s", request_id, user_id)
        set_mcp_connection_state(user_id, token_hash, connected=False, message=f"Error: {str(exc)[:100]}", request_id=request_id)
    finally:
        mcp_active_owner_ctx.reset(ctx_token)
        mcp_request_id_ctx.reset(req_token)
        mcp_bridge_tasks.pop(user_id, None)
        logger.info("[%s] MCP bridge selesai: user_id=%s", request_id, user_id)


@asynccontextmanager
async def websocket_client_server(user_id: int, url: str, token_hash: str):
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
                        asyncio.create_task(asyncio.to_thread(capture_xiaozhi_ws_chat, user_id, "inbound", message, token_hash))
                        try:
                            msg = types.JSONRPCMessage.model_validate_json(message)
                            await read_stream_writer.send(SessionMessage(msg))
                        except Exception as exc:
                            await read_stream_writer.send(exc)
            except Exception:
                logger.info("WebSocket reader berhenti.")

        async def ws_writer():
            try:
                async with write_stream_reader:
                    async for session_message in write_stream_reader:
                        json_str = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                        asyncio.create_task(asyncio.to_thread(capture_xiaozhi_ws_chat, user_id, "outbound", json_str, token_hash))
                        await ws.send(json_str)
            except Exception:
                logger.info("WebSocket writer berhenti.")

        async with anyio.create_task_group() as tg:
            tg.start_soon(ws_reader)
            tg.start_soon(ws_writer)
            yield read_stream, write_stream
