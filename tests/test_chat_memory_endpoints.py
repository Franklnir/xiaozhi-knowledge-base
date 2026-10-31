# -*- coding: utf-8 -*-
import json
import pytest
from starlette.testclient import TestClient
from xiaozhi.main import app
from xiaozhi.dependencies import get_store


@pytest.fixture
def test_client(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_api_chat.db")
    from xiaozhi.database.sqlite_store import SQLiteStore
    store = SQLiteStore(db_file)
    monkeypatch.setattr("xiaozhi.dependencies.get_store", lambda: store)
    monkeypatch.setattr("xiaozhi.routers.admin.get_store", lambda: store)
    monkeypatch.setattr("xiaozhi.routers.chat.get_store", lambda: store)
    
    # Create real users in DB to satisfy foreign keys
    admin = store.create_user("adminuser", "password123")
    conn = store._get_conn()
    conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (admin["id"],))
    conn.commit()
    admin = store.get_user_by_id(admin["id"])

    normal = store.create_user("normaluser", "password123")
    
    monkeypatch.setattr("xiaozhi.dependencies.require_admin", lambda req: admin)
    monkeypatch.setattr("xiaozhi.routers.admin.require_admin", lambda req: admin)
    monkeypatch.setattr("xiaozhi.dependencies.require_user", lambda req: admin)
    monkeypatch.setattr("xiaozhi.routers.chat.require_user", lambda req: normal)
    
    client = TestClient(app)
    return client, store, admin, normal


def test_admin_chat_memory_stats_endpoint(test_client):
    client, store, admin, normal = test_client
    
    res = client.get("/admin/api/chat-memory/stats")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "archive_files_count" in data
    assert "archive_disk_usage_human" in data
    assert data["status"] == "healthy"


def test_admin_chat_memory_compact_validation(test_client):
    client, store, admin, normal = test_client
    
    # Invalid days
    res = client.post("/admin/api/chat-memory/compact?days=0")
    assert res.status_code == 400
    assert res.json()["error_code"] == "INVALID_DAYS_RANGE"

    # Invalid batch size
    res = client.post("/admin/api/chat-memory/compact?batch_size=500")
    assert res.status_code == 400
    assert res.json()["error_code"] == "INVALID_BATCH_SIZE"

    # Valid execution (no records to compact yet)
    res = client.post("/admin/api/chat-memory/compact?days=7&min_size_kb=2&batch_size=20")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["data"]["processed_count"] == 0


def test_admin_chat_memory_clear_slot(test_client, monkeypatch):
    client, store, admin, normal = test_client
    monkeypatch.setattr("xiaozhi.routers.admin.validate_csrf", lambda *args, **kwargs: True)

    # Add records for normal user
    store.add_chat_history(normal["id"], tool_name="t1", slot_number=1, user_message="Slot 1")
    store.add_chat_history(normal["id"], tool_name="t2", slot_number=2, user_message="Slot 2")

    # Clear slot 2
    res = client.post(
        "/admin/api/chat-memory/clear-slot",
        data={"target_owner_id": normal["id"], "slot_number": 2, "csrf_token": "test"},
    )
    assert res.status_code == 200
    assert res.json()["removed_records"] == 1
    assert res.json()["slot_number"] == 2

    # Check slot 1 still intact
    left = store.list_chat_history(normal["id"], slot_number=1, strict_slot=True)
    assert len(left) == 1


def test_admin_chat_memory_payload_endpoint(test_client, tmp_path, monkeypatch):
    client, store, admin, normal = test_client
    archive_dir = str(tmp_path / "archives")
    monkeypatch.setattr("xiaozhi.services.chat_memory_service.ARCHIVE_BASE_DIR", archive_dir)

    # 404 test
    res = client.get("/admin/api/chat-memory/payload/99999")
    assert res.status_code == 404
    assert res.json()["error_code"] == "CHAT_RECORD_NOT_FOUND"

    # Insert record and read it
    rec = store.add_chat_history(
        normal["id"],
        tool_name="test_tool",
        slot_number=1,
        user_message="Tolong tes",
        xiaozhi_answer="Siap",
        response_payload={"foo": "bar", "number": 42},
    )
    res = client.get(f"/admin/api/chat-memory/payload/{rec['id']}")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["history_id"] == rec["id"]
    assert data["response_payload"]["foo"] == "bar"
