import os
import tempfile
import pytest

# Configure SQLite in-memory before importing app/factory
temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
temp_db.close()
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SQLITE_DB_PATH"] = temp_db.name

from fastapi.testclient import TestClient
from xiaozhi.main import app
from xiaozhi.dependencies import get_store, session_serializer, SESSION_COOKIE
from xiaozhi.services.mcp_service import set_mcp_connection_state, mcp_connection_states

client = TestClient(app, follow_redirects=True)


def test_mcp_overlay_gating_behavior():
    store = get_store()

    # 1. Non-admin user without MCP connection
    non_admin = store.create_user("overlay_user", "password123")
    non_admin_id = non_admin["id"]
    mcp_connection_states.clear()

    user_token = session_serializer.dumps({
        "id": non_admin_id,
        "username": "overlay_user",
        "session_version": 1,
    })

    # Access /dashboard without MCP connection -> should show mcpRequiredOverlay
    res_dash = client.get("/dashboard", cookies={SESSION_COOKIE: user_token})
    assert res_dash.status_code == 200
    assert "id=\"mcpRequiredOverlay\"" in res_dash.text
    assert "Wajib Hubungkan MCP XiaoZhi" in res_dash.text

    # Access /chat without MCP connection -> should show mcpRequiredOverlay
    res_chat = client.get("/chat", cookies={SESSION_COOKIE: user_token})
    assert res_chat.status_code == 200
    assert "id=\"mcpRequiredOverlay\"" in res_chat.text

    # Access /riwayat-chat without MCP connection -> should show mcpRequiredOverlay
    res_hist = client.get("/riwayat-chat", cookies={SESSION_COOKIE: user_token})
    assert res_hist.status_code == 200
    assert "id=\"mcpRequiredOverlay\"" in res_hist.text

    # Access /dokumentasi without MCP connection -> MUST NOT show mcpRequiredOverlay
    res_doc = client.get("/dokumentasi", cookies={SESSION_COOKIE: user_token})
    assert res_doc.status_code == 200
    assert "id=\"mcpRequiredOverlay\"" not in res_doc.text

    # 2. Admin user without MCP connection -> MUST NOT show mcpRequiredOverlay
    admin_user = store.ensure_admin_user("overlay_admin", "password123")
    admin_token = session_serializer.dumps({
        "id": admin_user["id"],
        "username": "overlay_admin",
        "session_version": 1,
    })

    res_admin_dash = client.get("/dashboard", cookies={SESSION_COOKIE: admin_token})
    assert res_admin_dash.status_code == 200
    assert "id=\"mcpRequiredOverlay\"" not in res_admin_dash.text

    # 3. Non-admin user after MCP is connected -> MUST NOT show mcpRequiredOverlay
    set_mcp_connection_state(non_admin_id, "dummy_token_hash", connected=True, slot=1)
    res_connected_dash = client.get("/dashboard", cookies={SESSION_COOKIE: user_token})
    assert res_connected_dash.status_code == 200
    assert "id=\"mcpRequiredOverlay\"" not in res_connected_dash.text

    mcp_connection_states.clear()
    try:
        os.remove(temp_db.name)
    except Exception:
        pass
