import pytest
from xiaozhi.database.sqlite_store import SQLiteStore


def test_sqlite_chat_history_slot_isolation(tmp_path):
    db_file = tmp_path / "test_chat_isolation.db"
    store = SQLiteStore(str(db_file))

    user = store.create_user("chatuser", "chatpass")
    owner_id = user["id"]

    # Add message for Slot 1
    store.add_chat_history(
        owner_id=owner_id,
        tool_name="test_tool_1",
        user_message="Halo slot 1",
        xiaozhi_answer="Jawaban slot 1",
        slot_number=1,
    )

    # Add message for Slot 2
    store.add_chat_history(
        owner_id=owner_id,
        tool_name="test_tool_2",
        user_message="Halo slot 2",
        xiaozhi_answer="Jawaban slot 2",
        slot_number=2,
    )

    # Add message for Slot 3
    store.add_chat_history(
        owner_id=owner_id,
        tool_name="test_tool_3",
        user_message="Halo slot 3",
        xiaozhi_answer="Jawaban slot 3",
        slot_number=3,
    )

    # 1. Test Slot 1 isolation
    s1_history = store.list_chat_history(owner_id=owner_id, slot_number=1)
    assert len(s1_history) == 1
    assert s1_history[0]["user_message"] == "Halo slot 1"

    # 2. Test Slot 2 isolation
    s2_history = store.list_chat_history(owner_id=owner_id, slot_number=2)
    assert len(s2_history) == 1
    assert s2_history[0]["user_message"] == "Halo slot 2"

    # 3. Test Slot 3 isolation
    s3_history = store.list_chat_history(owner_id=owner_id, slot_number=3)
    assert len(s3_history) == 1
    assert s3_history[0]["user_message"] == "Halo slot 3"

    # 4. Test All Slots (slot_number=None)
    all_history = store.list_chat_history(owner_id=owner_id, slot_number=None)
    assert len(all_history) == 3

    # 5. Test stats isolation
    s1_stats = store.chat_history_stats(owner_id=owner_id, slot_number=1)
    assert s1_stats["total"] == 1

    s2_stats = store.chat_history_stats(owner_id=owner_id, slot_number=2)
    assert s2_stats["total"] == 1

    all_stats = store.chat_history_stats(owner_id=owner_id, slot_number=None)
    assert all_stats["total"] == 3


def test_json_chat_history_slot_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_DB_LOCAL_ONLY", "1")
    monkeypatch.setenv("HF_DB_CACHE_DIR", str(tmp_path))
    from xiaozhi.database.store import HFJsonStore
    store = HFJsonStore()
    owner_id = 42

    store.add_chat_history(
        owner_id=owner_id,
        tool_name="test_tool_1",
        user_message="Msg Slot 1",
        slot_number=1,
    )
    store.add_chat_history(
        owner_id=owner_id,
        tool_name="test_tool_2",
        user_message="Msg Slot 2",
        slot_number=2,
    )

    s1 = store.list_chat_history(owner_id=owner_id, slot_number=1)
    assert len(s1) == 1
    assert s1[0]["user_message"] == "Msg Slot 1"

    s2 = store.list_chat_history(owner_id=owner_id, slot_number=2)
    assert len(s2) == 1
    assert s2[0]["user_message"] == "Msg Slot 2"

    s_all = store.list_chat_history(owner_id=owner_id, slot_number=None)
    assert len(s_all) == 2

