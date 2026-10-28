import uuid
import pytest
from fastapi.testclient import TestClient
from xiaozhi.main import app
from xiaozhi.dependencies import get_store, session_serializer, SESSION_COOKIE

client = TestClient(app, follow_redirects=False)

def test_mcp_page_requires_auth():
    response = client.get("/mcp")
    assert response.status_code in [302, 303, 307]
    assert "/login" in response.headers.get("location", "")

def test_mcp_page_renders_successfully():
    store = get_store()
    user = store.get_user_by_username("admin") or store.get_user_by_username("mcp_test_user")
    if not user:
        user = store.create_user(f"mcp_{uuid.uuid4().hex[:6]}", "password123")

    user_token = session_serializer.dumps({
        "id": user["id"],
        "username": user["username"],
        "session_version": user.get("session_version", 1),
    })

    client_with_redirect = TestClient(app, follow_redirects=True)
    response = client_with_redirect.get("/mcp", cookies={SESSION_COOKIE: user_token})
    assert response.status_code == 200
    html = response.text
    # Check page titles and section headers
    assert "Perangkat Hardware" in html
    assert "3 Slot MCP XiaoZhi" in html
    assert "Riwayat Pengikatan Hardware ESP32" in html
    assert "Koneksi MCP XiaoZhi" in html
    # Check key components
    assert "boardHistoryModal" in html
    assert "slotTab1" in html
    assert 'id="mcp_token"' in html
    assert 'id="saveMcpBtn"' in html
    # Check navbar active MCP link
    assert 'href="/mcp"' in html
