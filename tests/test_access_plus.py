import pytest
from xiaozhi.database.sqlite_store import SQLiteStore

def test_access_plus_store_functionality(tmp_path):
    db_file = tmp_path / "test_access_plus.db"
    store = SQLiteStore(str(db_file))

    user = store.create_user("user_plus_1", "password123")
    user_id = user["id"]
    
    # 1. Check default Akses Plus settings
    settings = store.get_user_access_plus(user_id)
    assert settings["user_id"] == user_id
    assert settings["mcp_multislot_allowed"] is True
    assert settings["playlist_quota_enabled"] is False
    assert settings["max_playlist_tracks"] == 15

    # 2. Add tokens for slot 1, 2, 3
    store.set_xiaozhi_token(user_id, "token_slot_1_secret", slot=1, device_label="Slot Satu")
    store.set_xiaozhi_token(user_id, "token_slot_2_secret", slot=2, device_label="Slot Dua")
    store.set_xiaozhi_token(user_id, "token_slot_3_secret", slot=3, device_label="Slot Tiga")

    tokens = store.list_user_xiaozhi_tokens(user_id)
    assert len(tokens) == 3
    assert all(t.get("is_active", True) is True for t in tokens)

    # 3. Add playlist tracks
    t1 = store.add_playlist_track(user_id, "Song 1", "https://example.com/1", video_id="vid1")
    t2 = store.add_playlist_track(user_id, "Song 2", "https://example.com/2", video_id="vid2")

    assert store.count_user_playlist_tracks(user_id) == 2
    assert store.count_user_playlist_tracks(user_id, active_only=True) == 2

    # 4. Check list_access_plus_overview
    overview = store.list_access_plus_overview()
    user_entry = next((u for u in overview if u["user_id"] == user_id), None)
    assert user_entry is not None
    assert "email" in user_entry
    assert user_entry["total_slots"] == 3
    assert user_entry["has_multislot"] is True
    assert user_entry["playlist"]["total_tracks"] == 2
    assert user_entry["playlist"]["has_playlist"] is True

    # Check list_admin_manageable_users includes email
    manageable = store.list_admin_manageable_users()
    m_user = next((u for u in manageable if u["id"] == user_id), None)
    assert m_user is not None
    assert "email" in m_user

    # 5. Restrict user (mcp_multislot_allowed=False, playlist_quota_enabled=True, max_playlist_tracks=15)
    store.set_user_access_plus(user_id, mcp_multislot_allowed=False, playlist_quota_enabled=True, max_playlist_tracks=15, notes="Dibatasi admin")
    settings = store.get_user_access_plus(user_id)
    assert settings["mcp_multislot_allowed"] is False
    assert settings["playlist_quota_enabled"] is True
    assert settings["max_playlist_tracks"] == 15

    # 6. Deactivate slot 2 and 3 without deleting
    store.set_user_slot_active(user_id, slot=2, is_active=False)
    store.set_user_slot_active(user_id, slot=3, is_active=False)

    tokens = store.list_user_xiaozhi_tokens(user_id)
    assert len(tokens) == 3  # Tokens are NOT deleted!
    token_by_slot = {t["slot_number"]: t for t in tokens}
    assert token_by_slot[1]["is_active"] is True
    assert token_by_slot[2]["is_active"] is False
    assert token_by_slot[3]["is_active"] is False
    # Ensure decryption works and token secret preserved
    assert store.get_xiaozhi_token(user_id, slot=2) == "token_slot_2_secret"

    # 7. Re-activate slot 2 (auto-reconnect flag test)
    store.set_user_slot_active(user_id, slot=2, is_active=True)
    tokens = store.list_user_xiaozhi_tokens(user_id)
    token_by_slot = {t["slot_number"]: t for t in tokens}
    assert token_by_slot[2]["is_active"] is True

    # 8. Deactivate a playlist track without deleting
    track_1_id = t1["id"]
    store.set_user_playlist_track_active(user_id, track_1_id, is_active=False)

    assert store.count_user_playlist_tracks(user_id) == 2
    assert store.count_user_playlist_tracks(user_id, active_only=True) == 1

    tracks = store.get_user_playlist(user_id)
    assert len(tracks) == 2  # Tracks are NOT deleted!
    track_1 = next(t for t in tracks if t["id"] == track_1_id)
    assert bool(track_1["is_active"]) is False


def test_manual_mcp_connections_obey_admin_restrictions(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from xiaozhi import dependencies
    from xiaozhi.config import SESSION_COOKIE, session_serializer
    from xiaozhi.routers import api_v1_mcp, mcp_endpoints

    store = SQLiteStore(str(tmp_path / "manual-mcp.db"))
    user = store.create_user("manual_mcp", "password123")
    endpoint = "wss://example.test/mcp?token=synthetic"
    store.set_xiaozhi_token(user["id"], endpoint)
    monkeypatch.setattr(dependencies, "_store", store)
    app = FastAPI()
    app.include_router(mcp_endpoints.router)
    app.include_router(api_v1_mcp.router)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_serializer.dumps(user))
    csrf = dependencies.make_csrf_token(user)

    store.set_mcp_blocked(user["id"], True, "synthetic")
    assert client.post("/api/v1/mcp/reconnect").status_code == 403
    assert client.post("/api/v1/mcp/save", json={"mcp_token": endpoint}).status_code == 403
    assert client.post("/reconnect_mcp", data={"slot": 1, "csrf_token": csrf}).status_code == 403
    assert client.post("/save_mcp", data={"slot": 1, "mcp_token": endpoint, "csrf_token": csrf}).status_code == 403

    store.set_mcp_blocked(user["id"], False)
    store.set_user_access_plus(user["id"], mcp_multislot_allowed=False)
    assert client.post("/save_mcp", data={"slot": 2, "mcp_token": endpoint, "csrf_token": csrf}).status_code == 403
    store.set_user_slot_active(user["id"], 1, False)
    assert client.post("/reconnect_mcp", data={"slot": 1, "csrf_token": csrf}).status_code == 403
