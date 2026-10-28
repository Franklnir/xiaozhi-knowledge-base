import pytest
from fastapi.testclient import TestClient
from xiaozhi.main import app
from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.services.mcp_service import set_mcp_connection_state, is_mcp_connected
from xiaozhi.mcp.bridge import record_bridge_failure, is_bridge_in_cooldown


def test_mcp_status_endpoints_sync():
    """Verify /mcp_status and /api/mcp/status both exist and return compatible payloads."""
    from xiaozhi.dependencies import get_store, session_serializer, SESSION_COOKIE
    import uuid
    store = get_store()
    user = store.get_user_by_username("admin") or store.get_user_by_username("mcp_sync_user")
    if not user:
        user = store.create_user(f"mcp_sync_{uuid.uuid4().hex[:6]}", "password123")

    user_token = session_serializer.dumps({
        "id": user["id"],
        "username": user["username"],
        "session_version": user.get("session_version", 1),
    })

    client = TestClient(app, cookies={SESSION_COOKIE: user_token})

    # 1. Test /api/mcp/status
    res1 = client.get("/api/mcp/status")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["success"] is True
    assert "slots" in data1
    assert "status" in data1
    assert "connected" in data1
    assert "tokenSaved" in data1
    
    # 2. Test /mcp_status alias (previously 404)
    res2 = client.get("/mcp_status")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["success"] is True
    assert data2["slots"] == data1["slots"]
    assert data2["connected"] == data1["connected"]


def test_cooldown_reset_on_save():
    """Verify that saving or reconnecting MCP resets any failure cooldown."""
    task_key = "999:1"
    # Artificially put in cooldown
    record_bridge_failure(task_key)
    assert is_bridge_in_cooldown(task_key) is True
    
    from xiaozhi.mcp.bridge import reset_bridge_failure
    reset_bridge_failure(task_key)
    assert is_bridge_in_cooldown(task_key) is False


@pytest.mark.anyio
async def test_wait_mcp_handshake():
    """Verify _wait_mcp_handshake correctly detects connected state and respects timeout."""
    from xiaozhi.routers.mcp_endpoints import _wait_mcp_handshake
    user_id = 8888
    slot = 1
    token_hash = "testhash123"
    
    # Not connected -> times out safely
    connected = await _wait_mcp_handshake(user_id, token_hash, slot=slot, timeout=0.2)
    assert connected is False
    
    # Set connected -> detects immediately
    set_mcp_connection_state(user_id, token_hash, connected=True, slot=slot)
    connected = await _wait_mcp_handshake(user_id, token_hash, slot=slot, timeout=0.5)
    assert connected is True
    
    # Cleanup
    set_mcp_connection_state(user_id, token_hash, connected=False, slot=slot)

