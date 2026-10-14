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


def test_mcp_recall_chat_memory_slot_isolation(tmp_path):
    from unittest.mock import MagicMock
    from xiaozhi.mcp.context import mcp_active_owner_ctx, mcp_active_slot_ctx
    from xiaozhi.mcp.tools import register_tools
    from xiaozhi.database.sqlite_store import SQLiteStore

    db_file = tmp_path / "test_mcp_chat_isolation.db"
    store = SQLiteStore(str(db_file))

    user = store.create_user("mcpuser", "mcppassword123")
    owner_id = user["id"]

    # Add chats for Slot 1 and Slot 3
    store.add_chat_history(
        owner_id=owner_id,
        tool_name="tool_s1",
        user_message="Saya sedang belajar Python di slot satu",
        xiaozhi_answer="Semangat belajar Python di slot 1!",
        slot_number=1,
    )
    store.add_chat_history(
        owner_id=owner_id,
        tool_name="tool_s3",
        user_message="Saya sedang bermain game Mobile Legends di slot tiga",
        xiaozhi_answer="Ayo push rank di slot 3!",
        slot_number=3,
    )

    registered_tools = {}
    mock_mcp = MagicMock()
    def mock_tool():
        def decorator(fn):
            registered_tools[fn.__name__] = fn
            return fn
        return decorator
    mock_mcp.tool = mock_tool

    register_tools(mock_mcp, store, MagicMock())

    recall_fn = registered_tools["recall_chat_memory"]
    profile_fn = registered_tools["get_user_profile"]

    # When active slot is 1
    t1_owner = mcp_active_owner_ctx.set(owner_id)
    t1_slot = mcp_active_slot_ctx.set(1)
    res_s1 = recall_fn(query="")
    assert res_s1["success"] is True
    assert res_s1["slot_aktif"] == 1
    assert len(res_s1["riwayat_percakapan"]) == 1
    assert "slot satu" in res_s1["riwayat_percakapan"][0]["pesan_user"]
    # Check Active Session detection (< 5 min)
    assert res_s1["active_session_terdeteksi"] is True
    assert res_s1["riwayat_percakapan"][0]["is_active_session"] is True
    assert "Active Session Context" in res_s1["riwayat_percakapan"][0]["prioritas"]
    assert "[PERHATIAN ACTIVE SESSION]" in res_s1["instruksi_xiaozhi"]

    # Check get_user_profile 3_percakapan_terakhir injection for Slot 1
    prof_s1 = profile_fn()
    assert prof_s1["success"] is True
    assert prof_s1["slot_aktif"] == 1
    assert len(prof_s1["3_percakapan_terakhir"]) == 1
    assert "slot satu" in prof_s1["3_percakapan_terakhir"][0]["pesan_user"]
    assert "3 Percakapan Terakhir" in prof_s1["prompt_context"]

    # When active slot is 3
    t3_slot = mcp_active_slot_ctx.set(3)
    res_s3 = recall_fn(query="")
    assert res_s3["success"] is True
    assert res_s3["slot_aktif"] == 3
    assert len(res_s3["riwayat_percakapan"]) == 1
    assert "slot tiga" in res_s3["riwayat_percakapan"][0]["pesan_user"]
    assert res_s3["active_session_terdeteksi"] is True

    # Check get_user_profile 3_percakapan_terakhir injection for Slot 3
    prof_s3 = profile_fn()
    assert prof_s3["success"] is True
    assert prof_s3["slot_aktif"] == 3
    assert len(prof_s3["3_percakapan_terakhir"]) == 1
    assert "slot tiga" in prof_s3["3_percakapan_terakhir"][0]["pesan_user"]

    # Persona analysis per slot
    persona_s1 = store.get_user_persona_analysis(owner_id, slot_number=1)
    assert persona_s1["total_chats_analyzed"] == 1

    persona_s3 = store.get_user_persona_analysis(owner_id, slot_number=3)
    assert persona_s3["total_chats_analyzed"] == 1

    # Clean up context
    mcp_active_owner_ctx.reset(t1_owner)
    mcp_active_slot_ctx.reset(t1_slot)


