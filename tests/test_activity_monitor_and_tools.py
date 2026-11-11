import json
import pytest
from unittest.mock import MagicMock, patch
from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.mcp.tools import register_tools
from xiaozhi.mcp.context import mcp_active_owner_ctx, mcp_active_slot_ctx


class DummyServer:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


def test_activity_monitor_ranking_and_status_parsing(tmp_path):
    db_file = tmp_path / "test_monitor.db"
    store = SQLiteStore(db_path=str(db_file))

    # Create dummy user
    user = store.create_user("testuser", "password123")
    user_id = user["id"]
    device_mac = "AA:BB:CC:DD:EE:01"
    store.register_device(owner_id=user_id, mac_address=device_mac, device_name="Xiaozhi Smart Speaker")

    # 1. Add successful tool call
    store.add_chat_history(
        owner_id=user_id,
        slot_number=1,
        user_message="Berapa cuaca di Jakarta?",
        xiaozhi_answer="Cuaca di Jakarta cerah 29 derajat Celsius.",
        device_mac=device_mac,
        tool_name="get_weather",
        request_payload={"city": "Jakarta"},
        response_payload={"temp": 29, "condition": "Sunny", "success": True},
    )

    # 2. Add failed tool call (with error response payload)
    store.add_chat_history(
        owner_id=user_id,
        slot_number=1,
        user_message="Buka pintu gerbang",
        xiaozhi_answer="Maaf, perangkat IoT gerbang sedang offline.",
        device_mac=device_mac,
        tool_name="control_gate",
        request_payload={"action": "open"},
        response_payload={"is_error": True, "error": "Koneksi gateway IoT terputus (timeout 5000ms)"},
    )

    # 3. Add second call for get_weather (success)
    store.add_chat_history(
        owner_id=user_id,
        slot_number=1,
        user_message="Cuaca di Bandung?",
        xiaozhi_answer="Bandung mendung 22 derajat.",
        device_mac=device_mac,
        tool_name="get_weather",
        request_payload={"city": "Bandung"},
        response_payload={"temp": 22, "condition": "Cloudy"},
    )

    # Test daily activity monitor with 1d range
    monitor_all = store.get_daily_activity_monitor(range_filter="1d")
    assert monitor_all["totals"]["total_tools"] == 3
    assert monitor_all["totals"]["total_success"] == 2
    assert monitor_all["totals"]["total_failed"] == 1
    assert len(monitor_all["timeline"]) == 3

    # Check status and reason parsing
    failed_items = [item for item in monitor_all["timeline"] if item["status"] == "failed"]
    assert len(failed_items) == 1
    assert failed_items[0]["tool_name"] == "control_gate"
    assert failed_items[0]["status_label"] == "GAGAL"
    assert "terputus" in failed_items[0]["status_reason"].lower()

    success_items = [item for item in monitor_all["timeline"] if item["status"] in ("success", "completed")]
    assert len(success_items) == 2
    assert success_items[0]["status_label"] == "BERHASIL"

    # Test filtering by status = 'failed'
    monitor_failed = store.get_daily_activity_monitor(status_filter="failed")
    assert len(monitor_failed["timeline"]) == 1
    assert monitor_failed["timeline"][0]["tool_name"] == "control_gate"

    # Test filtering by status = 'success'
    monitor_success = store.get_daily_activity_monitor(status_filter="success")
    assert len(monitor_success["timeline"]) == 2
    assert all(i["tool_name"] == "get_weather" for i in monitor_success["timeline"])

    # Test weekly tools ranking leaderboard (Urutan tools terbanyak dipanggil)
    ranking = monitor_all["weekly_tools_ranking"]
    assert len(ranking) == 2
    # Rank #1: get_weather (2 calls, 2 success, 0 failed)
    assert ranking[0]["tool_name"] == "get_weather"
    assert ranking[0]["total_calls"] == 2
    assert ranking[0]["success_count"] == 2
    assert ranking[0]["failed_count"] == 0
    assert ranking[0]["percentage"] == 100.0

    # Rank #2: control_gate (1 call, 0 success, 1 failed)
    assert ranking[1]["tool_name"] == "control_gate"
    assert ranking[1]["total_calls"] == 1
    assert ranking[1]["success_count"] == 0
    assert ranking[1]["failed_count"] == 1
    assert ranking[1]["percentage"] == 50.0

    # Test filtering by tool
    monitor_tool_gate = store.get_daily_activity_monitor(tool_filter="control_gate")
    assert len(monitor_tool_gate["timeline"]) == 1
    assert monitor_tool_gate["timeline"][0]["tool_name"] == "control_gate"


def test_xiaozhi_persona_and_emotion_tools(tmp_path):
    server = DummyServer()
    db_file = tmp_path / "test_tools.db"
    store = SQLiteStore(str(db_file))
    user = store.create_user("persona_user", "password123")
    user_id = user["id"]

    register_tools(server, store, lambda *a, **k: None)

    # Set context
    token_owner = mcp_active_owner_ctx.set(user_id)
    token_slot = mcp_active_slot_ctx.set(1)

    try:
        # 1. read_emotion_tone tool
        emotion_res = server.tools["read_emotion_tone"](
            user_speech="Aku sedih banget hari ini kucingku hilang",
            context="user curhat di kamar",
        )
        assert emotion_res["success"] is True
        assert emotion_res["detected_emotion"] == "sedih"
        assert "recommended_vocal_cue" in emotion_res
        assert emotion_res["recommended_persona_response"] == "hangat_penyayang"

        # 2. get_character_persona tool
        persona_res = server.tools["get_character_persona"](
            user_speech="Hahaha lucu banget cerita kamu!",
            include_emotion_reading=True,
        )
        assert persona_res["success"] is True
        assert "karakter_persona" in persona_res
        assert "analisis_nada_emosi_user" in persona_res
        assert persona_res["analisis_nada_emosi_user"]["detected_emotion"] in ("gembira", "ketawa", "senang")

    finally:
        mcp_active_owner_ctx.reset(token_owner)
        mcp_active_slot_ctx.reset(token_slot)


def test_chat_history_tool_retrieval(tmp_path):
    server = DummyServer()
    db_file = tmp_path / "test_history.db"
    store = SQLiteStore(db_path=str(db_file))

    user = store.create_user("chattester", "password123")
    user_id = user["id"]
    store.add_chat_history(
        owner_id=user_id,
        slot_number=1,
        user_message="Ingatkan saya beli susu formula besok jam 8 pagi",
        xiaozhi_answer="Baik, saya catat pengingat beli susu formula besok jam 8 pagi.",
        tool_name="set_reminder",
    )

    register_tools(server, store, lambda *a, **k: None)

    token_owner = mcp_active_owner_ctx.set(user_id)
    token_slot = mcp_active_slot_ctx.set(1)

    try:
        # Test query for "susu formula" via get_chat_history
        res = server.tools["get_chat_history"](query="susu formula", limit=5)
        assert res["success"] is True
        assert res["total_ditemukan"] == 1
        assert "susu formula" in res["riwayat_percakapan"][0]["pesan_user"]

        # Test query via recall_chat_memory with conversational query
        res_conversational = server.tools["recall_chat_memory"](query="tadi kita ngobrol apa ya", limit=5)
        assert res_conversational["success"] is True
        assert res_conversational["total_ditemukan"] == 1
        assert "susu formula" in res_conversational["riwayat_percakapan"][0]["pesan_user"]

    finally:
        mcp_active_owner_ctx.reset(token_owner)
        mcp_active_slot_ctx.reset(token_slot)
