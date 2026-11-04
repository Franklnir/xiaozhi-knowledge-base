import pytest
from datetime import datetime, timezone
from xiaozhi.services.chat_memory_service import (
    resolve_target_date,
    build_daily_chat_recap,
    extract_action_items_from_history,
)

class MockStore:
    def __init__(self, records):
        self.records = records

    def list_chat_history(self, owner_id, query="", limit=5, token_hash=None, date=None, slot_number=None, strict_slot=False, semantic=False):
        res = [r for r in self.records if r.get("owner_id") == owner_id]
        if date:
            res = [r for r in res if (r.get("created_at") or "").startswith(date)]
        if slot_number:
            res = [r for r in res if r.get("slot_number") == slot_number]
        return res[:limit]

def test_resolve_target_date():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert resolve_target_date("today") == today
    assert resolve_target_date("hari_ini") == today
    assert resolve_target_date("2026-11-04") == "2026-11-04"

def test_daily_chat_recap_and_slot_isolation():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    records = [
        # Slot 1 records
        {
            "id": 1,
            "owner_id": 10,
            "slot_number": 1,
            "tool_name": "play_youtube_song",
            "user_message": "Putar lagu Laskar Pelangi",
            "xiaozhi_answer": "Memutar lagu 'Laskar Pelangi' dari YouTube.",
            "created_at": f"{today}T10:00:00Z",
        },
        {
            "id": 2,
            "owner_id": 10,
            "slot_number": 1,
            "tool_name": "control_relay",
            "user_message": "Nyalakan lampu ruang tamu",
            "xiaozhi_answer": "Lampu ruang tamu dinyalakan.",
            "created_at": f"{today}T10:05:00Z",
        },
        {
            "id": 3,
            "owner_id": 10,
            "slot_number": 1,
            "tool_name": None,
            "user_message": "Bagaimana cara kerja panel surya?",
            "xiaozhi_answer": "Panel surya bekerja dengan mengubah foton cahaya...",
            "created_at": f"{today}T10:10:00Z",
        },
        # Slot 2 records (different slot, same owner)
        {
            "id": 4,
            "owner_id": 10,
            "slot_number": 2,
            "tool_name": "play_youtube_song",
            "user_message": "Putar Bohemian Rhapsody",
            "xiaozhi_answer": "Memutar lagu 'Bohemian Rhapsody'.",
            "created_at": f"{today}T11:00:00Z",
        },
    ]

    store = MockStore(records)

    # Test Slot 1
    recap1 = build_daily_chat_recap(store, owner_id=10, slot_number=1, target_date="today")
    assert recap1["success"] is True
    assert recap1["req_id"].startswith("recap_")
    assert recap1["slot_number"] == 1
    assert recap1["stats"]["total_chats"] == 3
    assert recap1["stats"]["songs_count"] == 1
    assert recap1["songs_played"][0]["title"] == "Laskar Pelangi"
    assert recap1["stats"]["iot_count"] == 1
    assert "Slot 1" in recap1["slot_label"]
    assert "Laskar Pelangi" in recap1["summary_text"]

    # Test Slot 2 isolation: should NOT contain Laskar Pelangi
    recap2 = build_daily_chat_recap(store, owner_id=10, slot_number=2, target_date="today")
    assert recap2["success"] is True
    assert recap2["slot_number"] == 2
    assert recap2["stats"]["total_chats"] == 1
    assert recap2["stats"]["songs_count"] == 1
    assert recap2["songs_played"][0]["title"] == "Bohemian Rhapsody"

def test_extract_action_items():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    records = [
        {
            "id": 101,
            "owner_id": 20,
            "slot_number": 1,
            "user_message": "Tolong ingatkan saya beli susu dan telur nanti sore",
            "created_at": f"{today}T08:00:00Z",
        },
        {
            "id": 102,
            "owner_id": 20,
            "slot_number": 1,
            "user_message": "Jadwal rapat project dengan tim marketing jam 2 siang",
            "created_at": f"{today}T08:30:00Z",
        },
        {
            "id": 103,
            "owner_id": 20,
            "slot_number": 1,
            "user_message": "Halo selamat pagi Xiaozhi apa kabar?",
            "created_at": f"{today}T09:00:00Z",
        },
    ]

    store = MockStore(records)
    todos = extract_action_items_from_history(store, owner_id=20, slot_number=1, target_date="today")

    assert todos["success"] is True
    assert todos["req_id"].startswith("todo_")
    assert todos["total_tasks"] == 2
    categories = [t["category"] for t in todos["tasks"]]
    assert "Pengingat" in categories or "Belanja & Finansial" in categories
    assert "Janji Temu" in categories

