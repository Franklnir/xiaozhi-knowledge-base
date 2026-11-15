import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from websockets.exceptions import InvalidStatus
from websockets.datastructures import Headers
from websockets.http11 import Response

from xiaozhi.mcp import bridge
from xiaozhi.mcp.bridge import (
    validate_mcp_ws_url,
    record_bridge_failure,
    reset_bridge_failure,
    is_bridge_in_cooldown,
    mcp_bridge_failure_counts,
    mcp_bridge_failure_cooldowns,
)
import xiaozhi.config as config

def test_validate_mcp_ws_url(monkeypatch):
    monkeypatch.setattr(config, "IS_PRODUCTION", True)

    # Valid Xiaozhi endpoint
    valid_url = "wss://api.xiaozhi.me/mcp/?token=test_token_123"
    is_valid, msg = validate_mcp_ws_url(valid_url)
    assert is_valid is True
    assert msg == ""

    # Invalid scheme
    http_url = "http://api.xiaozhi.me/mcp"
    is_valid, msg = validate_mcp_ws_url(http_url)
    assert is_valid is False
    assert "wss://" in msg

    # Web console URL (mistakenly copied by user)
    console_url = "wss://xiaozhi.me/console/agents"
    is_valid, msg = validate_mcp_ws_url(console_url)
    assert is_valid is False
    assert "web console" in msg.lower()

    # Private IP / LAN URL in production
    lan_url = "wss://172.16.23.28:8015/mcp"
    is_valid, msg = validate_mcp_ws_url(lan_url)
    assert is_valid is False
    assert "privat lokal" in msg.lower()

    lan_url_192 = "wss://192.168.1.100/mcp"
    is_valid, msg = validate_mcp_ws_url(lan_url_192)
    assert is_valid is False

    lan_url_10 = "wss://10.0.0.1:8000/mcp"
    is_valid, msg = validate_mcp_ws_url(lan_url_10)
    assert is_valid is False

def test_bridge_cooldown_and_backoff():
    task_key = "99999_1"
    reset_bridge_failure(task_key)

    assert not is_bridge_in_cooldown(task_key)

    # 1st failure (count=1 -> 30s)
    cd1 = record_bridge_failure(task_key, max_cooldown=300)
    assert cd1 == 30
    assert is_bridge_in_cooldown(task_key)
    assert mcp_bridge_failure_counts[task_key] == 1

    # 2nd failure (count=2 -> 60s)
    cd2 = record_bridge_failure(task_key, max_cooldown=300)
    assert cd2 == 60

    # 3rd failure (count=3 -> 120s)
    cd3 = record_bridge_failure(task_key, max_cooldown=300)
    assert cd3 == 120

    # Reset
    reset_bridge_failure(task_key)
    assert not is_bridge_in_cooldown(task_key)
    assert task_key not in mcp_bridge_failure_counts


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_bridge_hides_token_from_logs_and_status(monkeypatch, caplog, error_type):
    secret = "synthetic-test-credential"
    url = f"wss://example.test/mcp?token={secret}"
    status = Mock()

    @asynccontextmanager
    async def failed_connection(*args, **kwargs):
        raise error_type(f"Connection failed for {url}")
        yield

    monkeypatch.setattr(bridge, "websocket_client_server", failed_connection)
    monkeypatch.setattr(bridge, "set_mcp_connection_state", status)
    caplog.set_level("INFO", logger=bridge.logger.name)
    task_key = "99999:2"
    try:
        asyncio.run(bridge.run_mcp_bridge(None, None, 99999, url, "", slot=2))
        assert is_bridge_in_cooldown(task_key)
        assert "coba lagi" in status.call_args.kwargs["message"]
        assert "example.test" not in caplog.text
        assert secret not in caplog.text
        assert secret not in status.call_args.kwargs["message"]
        assert not any(record.exc_info for record in caplog.records)
    finally:
        reset_bridge_failure(task_key)


@pytest.mark.parametrize(("code", "expected", "delay"), [
    (401, "Token MCP ditolak", 900),
    (200, "tanpa upgrade WebSocket", 900),
    (503, "HTTP 503", 30),
])
def test_bridge_handshake_status_and_retry(monkeypatch, code, expected, delay):
    @asynccontextmanager
    async def failed_connection(*args, **kwargs):
        raise InvalidStatus(Response(code, "synthetic", Headers()))
        yield

    status = Mock()
    monkeypatch.setattr(bridge, "websocket_client_server", failed_connection)
    monkeypatch.setattr(bridge, "set_mcp_connection_state", status)
    monkeypatch.setattr(bridge.time, "time", lambda: 1000)
    task_key = "99999:2"
    try:
        asyncio.run(bridge.run_mcp_bridge(None, None, 99999, "wss://example.test/mcp", "", slot=2))
        assert expected in status.call_args.kwargs["message"]
        assert status.call_args.kwargs["connected"] is False
        assert mcp_bridge_failure_cooldowns[task_key] == 1000 + delay
    finally:
        reset_bridge_failure(task_key)


@pytest.mark.parametrize("url", [
    "wss://",
    "wss://api.xiaozhi.me/mcp/",
    "wss://api.xiaozhi.me/mcp/?token=...",
    "wss://example.test/console",
])
def test_incomplete_endpoint_is_rejected_before_connecting(monkeypatch, url):
    connect = Mock()
    status = Mock()
    monkeypatch.setattr(bridge, "websocket_client_server", connect)
    monkeypatch.setattr(bridge, "set_mcp_connection_state", status)
    try:
        asyncio.run(bridge.run_mcp_bridge(None, None, 99999, url, "", slot=2))
        connect.assert_not_called()
        assert status.call_args.kwargs["connected"] is False
    finally:
        reset_bridge_failure("99999:2")


def test_bridge_rechecks_empty_store_when_signaled(monkeypatch):
    async def exercise():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(bridge.mcp_service, "mcp_reload_event", asyncio.Event())
        monkeypatch.setattr(bridge.mcp_service, "_mcp_reload_loop", loop)
        monkeypatch.setattr(bridge, "_RELOAD_COALESCE_SECONDS", 0.02)
        first_poll = asyncio.Event()
        next_poll = asyncio.Event()
        polls = []

        def list_tokens():
            polls.append(None)
            loop.call_soon_threadsafe(first_poll.set if len(polls) == 1 else next_poll.set)
            return []

        task = asyncio.create_task(bridge.mcp_background_task(Mock(list_xiaozhi_tokens=list_tokens), None))
        try:
            await asyncio.wait_for(first_poll.wait(), timeout=1)
            await asyncio.gather(*(asyncio.to_thread(bridge.signal_mcp_reload) for _ in range(10)))
            await asyncio.wait_for(next_poll.wait(), timeout=1)
            assert len(polls) == 2
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(exercise())


def test_repeated_clean_disconnects_back_off_and_a_stable_session_resets_counter(monkeypatch):
    task_key = "99999:2"
    clock = Mock(monotonic=Mock(return_value=100), time=Mock(return_value=1000))
    reload = Mock()
    status = Mock()

    @asynccontextmanager
    async def connected(*args, **kwargs):
        yield None, None

    monkeypatch.setattr(bridge, "websocket_client_server", connected)
    monkeypatch.setattr(bridge, "set_mcp_connection_state", status)
    monkeypatch.setattr(bridge, "signal_mcp_reload", reload)
    monkeypatch.setattr(bridge, "time", clock)
    server = SimpleNamespace(_mcp_server=Mock(run=AsyncMock()))

    async def exercise():
        async def run_bridge():
            await bridge.run_mcp_bridge(None, server, 99999, "wss://example.test/mcp", "", slot=2)

        await run_bridge()
        first_delay = mcp_bridge_failure_cooldowns[task_key] - 1000
        assert 30 <= first_delay < 45
        assert status.call_args.kwargs["failure_code"] == "closed"
        await run_bridge()
        assert mcp_bridge_failure_cooldowns[task_key] - 1000 == first_delay + 30
        assert mcp_bridge_failure_counts[task_key] == 2

        async def stable_session(*args):
            clock.monotonic.return_value += 65

        server._mcp_server.run.side_effect = stable_session
        await run_bridge()
        assert mcp_bridge_failure_counts[task_key] == 1
        assert mcp_bridge_failure_cooldowns[task_key] - 1000 == first_delay
        reload.assert_not_called()

    try:
        asyncio.run(exercise())
    finally:
        reset_bridge_failure(task_key)


def test_cancelled_bridge_does_not_overwrite_status_or_set_cooldown(monkeypatch):
    status = Mock()
    monkeypatch.setattr(bridge, "set_mcp_connection_state", status)

    async def exercise():
        started = asyncio.Event()

        @asynccontextmanager
        async def connected(*args, **kwargs):
            started.set()
            yield None, None

        monkeypatch.setattr(bridge, "websocket_client_server", connected)
        async def wait_for_cancel(*args):
            await asyncio.Future()

        server = SimpleNamespace(_mcp_server=Mock(run=AsyncMock(side_effect=wait_for_cancel)))
        task = asyncio.create_task(bridge.run_mcp_bridge(None, server, 99999, "wss://example.test/mcp", "", slot=2))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert [call.kwargs["connected"] for call in status.call_args_list] == [True]
        assert not is_bridge_in_cooldown("99999:2")

    asyncio.run(exercise())


@pytest.mark.parametrize("close_code", [1000, 1011])
def test_websocket_protocol_and_bridge_shutdown(monkeypatch, caplog, close_code):
    from mcp import ClientSession, types
    from mcp.server.fastmcp import FastMCP
    from mcp.shared.message import SessionMessage
    import anyio
    import websockets

    status = Mock()
    capture = Mock()
    monkeypatch.setattr(config, "IS_PRODUCTION", False)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setattr(bridge, "_handshake_limit", asyncio.Semaphore(1))
    monkeypatch.setattr(bridge, "set_mcp_connection_state", status)
    monkeypatch.setattr(bridge, "capture_xiaozhi_ws_chat", capture)
    caplog.set_level("INFO", logger=bridge.logger.name)
    server = FastMCP("Bridge test")

    @server.tool()
    def echo_text(text: str) -> str:
        return text

    async def exercise():
        loop = asyncio.get_running_loop()
        captures_done = asyncio.Event()
        directions = set()

        def record_direction(direction):
            directions.add(direction)
            if directions == {"inbound", "outbound"}:
                captures_done.set()

        capture.side_effect = lambda _owner, direction, *args: loop.call_soon_threadsafe(record_direction, direction)

        async def peer(ws):
            to_client, from_socket = anyio.create_memory_object_stream(0)
            to_socket, from_client = anyio.create_memory_object_stream(0)

            async def read_messages():
                async for message in ws:
                    await to_client.send(SessionMessage(types.JSONRPCMessage.model_validate_json(message)))

            async def write_messages():
                async for message in from_client:
                    await ws.send(message.message.model_dump_json(by_alias=True, exclude_none=True))

            async with anyio.create_task_group() as group:
                group.start_soon(read_messages)
                group.start_soon(write_messages)
                try:
                    async with ClientSession(from_socket, to_socket) as client:
                        await client.initialize()
                        assert [tool.name for tool in (await client.list_tools()).tools] == ["echo_text"]
                        result = await client.call_tool("echo_text", {"text": "coba bridge"})
                        assert result.content[0].text == "coba bridge"
                        await asyncio.wait_for(captures_done.wait(), timeout=2)
                finally:
                    group.cancel_scope.cancel()
            await ws.close(code=close_code)

        async with websockets.serve(peer, "127.0.0.1", 0) as local:
            port = local.sockets[0].getsockname()[1]
            await asyncio.wait_for(
                bridge.run_mcp_bridge(None, server, 99999, f"ws://127.0.0.1:{port}/mcp", "", slot=2),
                timeout=3,
            )
        assert [call.kwargs["connected"] for call in status.call_args_list] == [True, False]
        assert status.call_args.kwargs["failure_code"] == "closed"
        assert len(capture.call_args_list) == 2
        assert all("coba bridge" in call.args[2] for call in capture.call_args_list)

    try:
        asyncio.run(exercise())
        assert f"close_code={close_code}" in caplog.text
    finally:
        reset_bridge_failure("99999:2")


def test_tool_list_loads_permissions_once_per_account(monkeypatch):
    from mcp.server.fastmcp import FastMCP
    from xiaozhi.mcp import server as mcp
    from xiaozhi.mcp.context import mcp_active_owner_ctx

    server = FastMCP("Permissions test")
    for name in ("get_weather_bmkg", "search_news", "list_user_playlist", "get_real_relay_status"):
        server.add_tool(lambda: "ok", name=name)
    store = Mock()
    store.get_user_features.return_value = {"youtube_music": False}
    store.get_feature_settings.return_value = {"virtual_smarthome_enabled": True}
    store.get_mcp_tool_toggles.return_value = {"search_news": False}
    monkeypatch.setattr(mcp, "mcp_server", server)
    mcp.setup_mode_filtering(store)

    async def exercise():
        from mcp import types

        scope = mcp_active_owner_ctx.set(99)
        try:
            list_tools = server._mcp_server.request_handlers[types.ListToolsRequest]
            result = await list_tools(types.ListToolsRequest())
            assert [tool.name for tool in result.root.tools] == ["get_weather_bmkg"]
        finally:
            mcp_active_owner_ctx.reset(scope)

    asyncio.run(exercise())
    store.get_user_features.assert_called_once_with(99)
    store.get_feature_settings.assert_called_once_with(99)
    store.get_mcp_tool_toggles.assert_called_once_with(99)


def test_handshakes_are_limited_even_when_many_slots_start_at_once(monkeypatch):
    import websockets

    async def exercise():
        active = peak = attempted = 0
        limit = bridge._MAX_CONCURRENT_HANDSHAKES
        at_capacity, release = asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def handshake(*args, **kwargs):
            nonlocal active, peak, attempted
            attempted += 1
            active += 1
            peak = max(peak, active)
            if active == limit:
                at_capacity.set()
            try:
                await release.wait()
            finally:
                active -= 1
            raise OSError("simulated failure")
            yield

        async def connect():
            with pytest.raises(OSError):
                async with bridge.websocket_client_server(99999, "wss://example.test/mcp", ""):
                    pass

        monkeypatch.setattr(websockets, "connect", handshake)
        monkeypatch.setattr(bridge, "_handshake_limit", asyncio.Semaphore(limit))
        monkeypatch.setattr(bridge, "_handshake_start_lock", asyncio.Lock())
        monkeypatch.setattr(bridge, "_HANDSHAKE_START_INTERVAL", 0)
        tasks = [asyncio.create_task(connect()) for _ in range(24)]
        try:
            await asyncio.wait_for(at_capacity.wait(), timeout=2)
            assert attempted == limit
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        assert peak == limit and attempted == 24

    asyncio.run(exercise())


def test_full_websocket_catalog_fits_in_a_single_message(monkeypatch):
    from mcp import types
    from xiaozhi.mcp import server as mcp

    monkeypatch.setattr(mcp, "mcp_server", None)
    server = mcp.init_mcp_server(SimpleNamespace())
    tools = asyncio.run(server.list_tools())
    catalog = types.ListToolsResult(tools=tools).model_dump(by_alias=True, exclude_none=True, mode="json")
    message = types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc="2.0", id=3, result=catalog))

    data = bridge._serialize_websocket_tool_list(message)
    assert len(data.encode("utf-8")) < 60 * 1024
    encoded = json.loads(data)
    assert encoded["id"] == 3
    compact_tools = encoded["result"]["tools"]
    assert [tool["name"] for tool in compact_tools] == [tool.name for tool in tools]
    for original, compact in zip(tools, compact_tools):
        assert compact["description"].split() == original.description.split()
        schema = compact["inputSchema"]
        assert schema.get("required") == original.inputSchema.get("required")
        assert set(schema["properties"]) == set(original.inputSchema["properties"])
        for key, props in schema["properties"].items():
            assert props == {name: value for name, value in original.inputSchema["properties"][key].items() if name != "title"}
