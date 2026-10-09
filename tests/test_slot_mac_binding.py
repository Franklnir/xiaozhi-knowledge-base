import pytest
from xiaozhi.database.sqlite_store import SQLiteStore


def test_sqlite_slot_mac_binding():
    store = SQLiteStore(db_path=":memory:")

    user = store.create_user("testuser", "testpass")
    user_id = user["id"]

    # 1. Save token for slot 1
    req1 = "req-test-101"
    store.set_xiaozhi_token(user_id, "wss://api.xiaozhi.me/mcp/?token=slot1_token", slot=1, device_label="Ruang Tamu", request_id=req1)
    tokens = store.list_user_xiaozhi_tokens(user_id)
    assert len(tokens) == 1
    assert tokens[0]["slot_number"] == 1
    assert tokens[0]["device_label"] == "Ruang Tamu"
    assert tokens[0]["board_mac"] == ""
    assert tokens[0]["is_locked"] is False

    # 2. ESP32 connects and binds MAC
    req2 = "req-test-102"
    bound = store.bind_board_to_slot(user_id, slot=1, device_mac="24:DC:C3:9A:28:30", request_id=req2)
    assert bound["success"] is True
    assert bound["board_mac"] == "24:DC:C3:9A:28:30"

    tokens = store.list_user_xiaozhi_tokens(user_id)
    assert tokens[0]["board_mac"] == "24:DC:C3:9A:28:30"
    assert tokens[0]["is_locked"] is True

    # 3. Anti-spoofing: attempt to bind a different MAC without detach
    req3 = "req-test-103"
    bound2 = store.bind_board_to_slot(user_id, slot=1, device_mac="AA:BB:CC:DD:EE:FF", request_id=req3)
    assert bound2["success"] is False  # Cannot overwrite locked MAC
    assert "terkunci" in bound2.get("message", "").lower() or bound2.get("detail")
    tokens = store.list_user_xiaozhi_tokens(user_id)
    assert tokens[0]["board_mac"] == "24:DC:C3:9A:28:30"

    # 4. Update label without losing MAC or resetting token
    req4 = "req-test-104"
    renamed = store.update_slot_label(user_id, slot=1, device_label="Ruang Kerja Baru", request_id=req4)
    assert renamed is True
    tokens = store.list_user_xiaozhi_tokens(user_id)
    assert tokens[0]["device_label"] == "Ruang Kerja Baru"
    assert tokens[0]["board_mac"] == "24:DC:C3:9A:28:30"

    # 5. Check audit history
    history = store.get_board_binding_history(user_id=user_id, slot_number=1)
    assert len(history) >= 1
    assert any(h["action"] == "bind" and h["device_mac"] == "24:DC:C3:9A:28:30" for h in history)

    # 6. Detach board
    req5 = "req-test-105"
    detached = store.detach_board_from_slot(user_id, slot=1, request_id=req5)
    assert detached["success"] is True
    tokens = store.list_user_xiaozhi_tokens(user_id)
    assert tokens[0]["board_mac"] == ""
    assert tokens[0]["is_locked"] is False

    # 7. Check detach in audit history
    history = store.get_board_binding_history(user_id=user_id, slot_number=1)
    assert any(h["action"] == "detach" and h["device_mac"] == "24:DC:C3:9A:28:30" for h in history)

    # 8. Check admin listing includes slot array
    users = store.list_admin_manageable_users()
    test_user_entry = next((u for u in users if u["id"] == user_id), None)
    assert test_user_entry is not None
    assert "slots" in test_user_entry
    assert len(test_user_entry["slots"]) >= 1
    assert test_user_entry["slots"][0]["slot_number"] == 1
    assert test_user_entry["slots"][0]["device_label"] == "Ruang Kerja Baru"


def test_set_mcp_connection_state_with_board_mac():
    from xiaozhi.services.mcp_service import set_mcp_connection_state, is_mcp_connected, mcp_connection_states, _slot_key

    # Test setting connection state with board_mac and arbitrary kwargs
    set_mcp_connection_state(
        owner_id=999,
        token_hash="fakehash123",
        connected=True,
        message="Terhubung",
        request_id="req-999",
        slot=2,
        device_label="Ruang Tidur",
        board_mac="AA:BB:CC:11:22:33",
        extra_arbitrary_param="should_not_crash",
    )

    state = mcp_connection_states.get(_slot_key(999, 2))
    assert state is not None
    assert state["connected"] is True
    assert state["slot"] == 2
    assert state["device_label"] == "Ruang Tidur"
    assert state["board_mac"] == "AA:BB:CC:11:22:33"
    assert is_mcp_connected(999, "fakehash123", slot=2) is True


def test_playlist_matching_accuracy():
    from xiaozhi.database.sqlite_store import SQLiteStore
    store = SQLiteStore(db_path=":memory:")
    user = store.create_user("musicfan", "password123")
    uid = user["id"]

    # Add 5 tracks with different names
    store.add_playlist_track(uid, title="Track One", youtube_url="https://www.youtube.com/watch?v=11111111111", video_id="11111111111")
    store.add_playlist_track(uid, title="Track Two", youtube_url="https://www.youtube.com/watch?v=22222222222", video_id="22222222222")
    store.add_playlist_track(uid, title="Track Three", youtube_url="https://www.youtube.com/watch?v=33333333333", video_id="33333333333")
    store.add_playlist_track(uid, title="Track Four", youtube_url="https://www.youtube.com/watch?v=44444444444", video_id="44444444444")
    store.add_playlist_track(uid, title="Track Five (Didi Kempot)", youtube_url="https://www.youtube.com/watch?v=55555555555", video_id="55555555555")

    # 1. Search for a title containing number: "Maroon 5" should NOT match track #5
    matched = store.find_playlist_track_by_query(uid, "Maroon 5")
    assert matched is None or matched["title"] != "Track Five (Didi Kempot)"

    # 2. Explicit track number: "2" or "nomor 2" MUST match track #2
    t2 = store.find_playlist_track_by_query(uid, "2")
    assert t2 is not None and t2["track_number"] == 2
    t2_explicit = store.find_playlist_track_by_query(uid, "nomor 2")
    assert t2_explicit is not None and t2_explicit["track_number"] == 2

    # 3. Direct URL / ID
    t3 = store.find_playlist_track_by_query(uid, "https://www.youtube.com/watch?v=33333333333")
    assert t3 is not None and t3["video_id"] == "33333333333"


def test_mac_transfer_and_detach_on_slot_deletion():
    from xiaozhi.database.sqlite_store import SQLiteStore
    store = SQLiteStore(db_path=":memory:")

    user1 = store.create_user("irsyad26", "password123")
    user2 = store.create_user("irsyad", "password456")
    u1_id = user1["id"]
    u2_id = user2["id"]

    mac_board = "7C:E8:B1:A4:D6:E4"

    # 1. User 1 configures slot 1, 2, and 3
    store.set_xiaozhi_token(u1_id, "wss://api.xiaozhi.me/mcp/?token=token1", slot=1, device_label="Slot 1")
    store.set_xiaozhi_token(u1_id, "wss://api.xiaozhi.me/mcp/?token=token2", slot=2, device_label="Slot 2")
    store.set_xiaozhi_token(u1_id, "wss://api.xiaozhi.me/mcp/?token=token3", slot=3, device_label="Slot 3")

    # User 1 binds MAC to slot 3
    b1 = store.bind_board_to_slot(u1_id, slot=3, device_mac=mac_board, request_id="req-u1-bind")
    assert b1["success"] is True

    # Check registered_devices & find_user_by_active_token_mac
    dev = store.find_device_by_mac(mac_board)
    assert dev is not None
    assert dev["owner_id"] == u1_id
    assert store.find_user_by_active_token_mac(mac_board) == u1_id

    # 2. User 1 deletes slot 3 (leaving slots 1 and 2 intact)
    del_res = store.delete_xiaozhi_token(u1_id, slot=3, request_id="req-u1-del-s3")
    assert del_res is True

    # Board MAC must be unlinked from registered_devices (owner_id = NULL, status = 'DETACHED')
    dev_after_del = store.find_device_by_mac(mac_board)
    assert dev_after_del is not None
    assert dev_after_del["owner_id"] is None
    assert dev_after_del["status"] == "DETACHED"

    # User 1 active token lookup must return None for this MAC
    assert store.find_user_by_active_token_mac(mac_board) is None

    # History for User 1 must still exist and be marked UNLINKED
    hist_u1 = store.get_board_binding_history(user_id=u1_id, slot_number=3)
    assert len(hist_u1) >= 1
    assert any(h["status"] == "UNLINKED" for h in hist_u1)

    # 3. User 2 registers slot 1
    store.set_xiaozhi_token(u2_id, "wss://api.xiaozhi.me/mcp/?token=u2_token1", slot=1, device_label="irsyad Slot 1")

    # User 2 binds the same board MAC
    b2 = store.bind_board_to_slot(u2_id, slot=1, device_mac=mac_board, request_id="req-u2-bind")
    assert b2["success"] is True

    # Verify registered_devices now belongs to User 2
    dev_u2 = store.find_device_by_mac(mac_board)
    assert dev_u2 is not None
    assert dev_u2["owner_id"] == u2_id
    assert dev_u2["status"] == "ACTIVE"

    # Active token MAC belongs to User 2
    assert store.find_user_by_active_token_mac(mac_board) == u2_id

    # History for User 2 exists as ACTIVE
    hist_u2 = store.get_board_binding_history(user_id=u2_id, slot_number=1)
    assert len(hist_u2) >= 1
    assert any(h["status"] == "ACTIVE" and h["user_id"] == u2_id for h in hist_u2)

    # Old history for User 1 is STILL preserved!
    hist_u1_check = store.get_board_binding_history(user_id=u1_id, slot_number=3)
    assert len(hist_u1_check) >= 1


def test_resolve_owner_for_device_anti_stale_and_transfer():
    from xiaozhi.database.sqlite_store import SQLiteStore
    from xiaozhi.routers.youtube import _resolve_owner_for_device

    store = SQLiteStore(db_path=":memory:")
    u1 = store.create_user("irsyad26", "password123")["id"]
    u2 = store.create_user("irsyad", "password456")["id"]

    mac_a = "90:DA:72:87:D2:68"
    mac_b = "68:EE:8F:4D:50:1C"
    mac_c = "7C:E8:B1:A4:D6:E4"

    # User 1 sets 3 slots
    store.set_xiaozhi_token(u1, "wss://api.xiaozhi.me/mcp/?token=tok1", slot=1, device_label="U1 Slot 1")
    store.set_xiaozhi_token(u1, "wss://api.xiaozhi.me/mcp/?token=tok2", slot=2, device_label="U1 Slot 2")
    store.set_xiaozhi_token(u1, "wss://api.xiaozhi.me/mcp/?token=tok3", slot=3, device_label="U1 Slot 3")

    store.bind_board_to_slot(u1, slot=1, device_mac=mac_a)
    store.bind_board_to_slot(u1, slot=2, device_mac=mac_b)
    store.bind_board_to_slot(u1, slot=3, device_mac=mac_c)

    # Initially, MAC C resolves to User 1
    assert _resolve_owner_for_device(store, mac_c) == u1

    # User 1 deletes slot 3
    store.delete_xiaozhi_token(u1, slot=3, request_id="del-s3")

    # Now MAC C must NOT resolve to User 1!
    assert _resolve_owner_for_device(store, mac_c) is None

    # User 2 configures slot 1 and queues an audio command for "despacito"
    store.set_xiaozhi_token(u2, "wss://api.xiaozhi.me/mcp/?token=u2_tok1", slot=1, device_label="U2 Slot 1")
    store.queue_audio_command(u2, title="Despacito", stream_url="/api/audio/stream/xyz", video_id="vid123")

    # With query="despacito", it resolves to User 2 via recent audio queue correlation!
    assert _resolve_owner_for_device(store, mac_c, query="despacito") == u2

    # User 2 binds MAC C to slot 1
    store.bind_board_to_slot(u2, slot=1, device_mac=mac_c)

    # Now MAC C resolves directly to User 2 as active token owner!
    assert _resolve_owner_for_device(store, mac_c) == u2


def test_board_history_with_current_owner_and_transfer_detection():
    from xiaozhi.database.sqlite_store import SQLiteStore

    store = SQLiteStore(db_path=":memory:")
    u1 = store.create_user("irsyad26", "password123")["id"]
    u2 = store.create_user("irsyad", "password456")["id"]

    mac_test = "7C:E8:B1:A4:D6:E4"

    # 1. User 1 binds MAC to slot 3
    store.set_xiaozhi_token(u1, "wss://api.xiaozhi.me/mcp/?token=u1_tok3", slot=3, device_label="U1 Slot 3")
    store.bind_board_to_slot(u1, slot=3, device_mac=mac_test)

    # Check history for User 1: currently self-owned
    hist_u1 = store.get_board_binding_history(user_id=u1)
    assert len(hist_u1) >= 1
    target = next((h for h in hist_u1 if h["device_mac"] == mac_test), None)
    assert target is not None
    assert target["is_current_owner"] is True
    assert target["is_transferred"] is False
    assert target["current_owner_username"] == "irsyad26"

    # 2. User 1 deletes slot 3 (detaches board)
    store.delete_xiaozhi_token(u1, slot=3, request_id="del-u1-s3")

    # 3. User 2 configures slot 1 and binds this MAC
    store.set_xiaozhi_token(u2, "wss://api.xiaozhi.me/mcp/?token=u2_tok1", slot=1, device_label="U2 Slot 1")
    store.bind_board_to_slot(u2, slot=1, device_mac=mac_test)

    # 4. Old user (User 1 - irsyad26) views history
    hist_u1_after = store.get_board_binding_history(user_id=u1)
    target_u1 = next((h for h in hist_u1_after if h["device_mac"] == mac_test), None)
    assert target_u1 is not None

    # CRITICAL: Old user knows board MAC is now connected to new user @irsyad!
    assert target_u1["is_transferred"] is True
    assert target_u1["is_current_owner"] is False
    assert target_u1["current_owner_username"] == "irsyad"
    assert target_u1["current_owner_id"] == u2
    assert target_u1["current_slot_number"] == 1
    assert "irsyad" in target_u1["current_connection_text"]
    assert "Slot 1" in target_u1["current_connection_text"]

    # 5. New user (User 2 - irsyad) views history
    hist_u2 = store.get_board_binding_history(user_id=u2)
    target_u2 = next((h for h in hist_u2 if h["device_mac"] == mac_test), None)
    assert target_u2 is not None
    assert target_u2["is_current_owner"] is True
    assert target_u2["is_transferred"] is False
    assert target_u2["current_owner_username"] == "irsyad"




