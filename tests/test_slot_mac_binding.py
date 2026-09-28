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

