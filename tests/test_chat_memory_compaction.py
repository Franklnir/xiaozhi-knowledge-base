# -*- coding: utf-8 -*-
import json
import os
import pytest
from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.services.chat_memory_service import (
    archive_payload_to_disk,
    read_archived_payload_from_disk,
    compact_single_history_record,
    run_chat_memory_compactor_batch,
    resolve_original_history_payload,
    get_chat_memory_archive_stats,
    summarize_history_payload,
)


def test_payload_disk_archive_and_read(tmp_path):
    archive_dir = str(tmp_path / "archives")
    raw_data = {"tool": "youtube_search", "results": [{"title": "Lagu Pop", "id": "xyz123" * 50}]}
    
    meta = archive_payload_to_disk(raw_data, owner_id=1, history_id=101, field_name="res", base_dir=archive_dir)
    assert meta["pointer"].startswith("1/")
    assert meta["original_bytes"] > 0
    assert meta["compressed_bytes"] < meta["original_bytes"]

    ok, decompressed, err = read_archived_payload_from_disk(meta["pointer"], base_dir=archive_dir)
    assert ok is True
    assert err == ""
    assert decompressed["tool"] == "youtube_search"
    assert len(decompressed["results"]) == 1


def test_chat_memory_compaction_flow(tmp_path):
    db_file = str(tmp_path / "test_chat.db")
    archive_dir = str(tmp_path / "archives")
    store = SQLiteStore(db_file)

    user = store.create_user("memoryuser", "password123")
    owner_id = user["id"]

    # 1. Create a large response payload (> 2KB)
    large_payload = {
        "items": [{"idx": i, "data": "dummy text content " * 20} for i in range(30)],
        "secret_token": "super_secret_token_123",
    }
    
    # Insert old record (using direct query to simulate 10 days ago)
    import datetime
    ten_days_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)).isoformat()
    
    conn = store._get_conn()
    conn.execute(
        """INSERT INTO chat_history (owner_id, source, tool_name, slot_number, user_message, xiaozhi_answer, response_payload, created_at)
           VALUES (?, 'mcp_tool', 'web_search', 1, 'Cari data besar', 'Berikut datanya...', ?, ?)""",
        (owner_id, json.dumps(large_payload), ten_days_ago)
    )
    conn.commit()

    # Verify candidate detection
    candidates = store.find_chat_compaction_candidates(older_than_days=7, min_size_bytes=1000)
    assert len(candidates) == 1
    record = candidates[0]

    # Run compaction batch
    res = run_chat_memory_compactor_batch(
        store,
        older_than_days=7,
        min_size_bytes=1000,
        batch_size=10,
        base_dir=archive_dir,
    )
    assert res["success"] is True
    assert res["compacted_count"] == 1
    assert res["saved_bytes"] > 0

    # Verify updated row in DB
    updated = store.get_chat_history_item(record["id"], owner_id)
    assert updated is not None
    res_obj = json.loads(updated["response_payload"])
    assert res_obj["_archived"] is True
    assert "_archive_pointer" in res_obj
    assert "summary" in res_obj

    # Verify secret is redacted in summary
    summary_str = json.dumps(res_obj["summary"])
    assert "super_secret_token_123" not in summary_str

    # Resolve original payload
    ok, original_recovered, _ = resolve_original_history_payload(updated, "response_payload", base_dir=archive_dir)
    assert ok is True
    assert original_recovered["secret_token"] == "super_secret_token_123"
    assert len(original_recovered["items"]) == 30

    # Verify stats
    stats = get_chat_memory_archive_stats(store, owner_id=owner_id, base_dir=archive_dir)
    assert stats["archive_files_count"] == 1
    assert stats["archive_disk_usage_bytes"] > 0


def test_clear_chat_history_slot_isolation(tmp_path):
    db_file = str(tmp_path / "test_chat_slots.db")
    store = SQLiteStore(db_file)

    user = store.create_user("slotuser", "password123")
    owner_id = user["id"]

    # Add messages across slots 1, 2, 3
    store.add_chat_history(owner_id, tool_name="t1", slot_number=1, user_message="Slot 1 msg")
    store.add_chat_history(owner_id, tool_name="t2", slot_number=2, user_message="Slot 2 msg")
    store.add_chat_history(owner_id, tool_name="t3", slot_number=3, user_message="Slot 3 msg")

    assert len(store.list_chat_history(owner_id, slot_number=None)) == 3

    # Clear only Slot 2
    deleted = store.clear_chat_history(owner_id, slot_number=2)
    assert deleted == 1

    # Verify Slot 1 & 3 still exist!
    assert len(store.list_chat_history(owner_id, slot_number=1, strict_slot=True)) == 1
    assert len(store.list_chat_history(owner_id, slot_number=2, strict_slot=True)) == 0
    assert len(store.list_chat_history(owner_id, slot_number=3, strict_slot=True)) == 1
