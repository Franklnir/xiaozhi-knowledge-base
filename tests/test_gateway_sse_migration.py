import os
import time
import pytest
from xiaozhi.services.sse_service import (
    generate_sse_stream_token,
    emit_user_event,
    broadcast_unread_badge,
    broadcast_material_event,
    broadcast_relay_update,
    broadcast_relay_device_status,
    _user_emit_queue,
)


def test_generate_sse_stream_token():
    """Verify stream token generation with HMAC signature."""
    uid = 42
    token = generate_sse_stream_token(uid)
    assert token, "Token should not be empty"

    parts = token.split(".")
    assert len(parts) == 3, "Token must be user_id.timestamp.signature"
    assert parts[0] == str(uid), "user_id in token must match"
    
    ts = int(parts[1])
    assert abs(time.time() - ts) < 5, "Timestamp must be current"
    assert len(parts[2]) == 64, "Signature must be 64-char hex string (SHA-256)"


def test_emit_user_event_queue():
    """Verify emit_user_event enqueues correctly for background dispatcher."""
    # Drain any existing items
    while not _user_emit_queue.empty():
        try:
            _user_emit_queue.get_nowait()
        except Exception:
            break

    emit_user_event(100, "test_event", {"hello": "world"})
    assert not _user_emit_queue.empty(), "Queue should contain emitted event"

    item = _user_emit_queue.get(timeout=1.0)
    assert item["user_id"] == 100
    assert item["event"] == "test_event"
    assert item["data"]["hello"] == "world"


def test_broadcast_unread_badge():
    """Verify broadcast_unread_badge formats unread badge payload."""
    while not _user_emit_queue.empty():
        try:
            _user_emit_queue.get_nowait()
        except Exception:
            break

    broadcast_unread_badge(101, community_count=5, marketplace_count=3)
    item = _user_emit_queue.get(timeout=1.0)
    assert item["user_id"] == 101
    assert item["event"] == "unread_badge"
    assert item["data"]["community_unread"] == 5
    assert item["data"]["marketplace_unread"] == 3


def test_broadcast_material_and_relay_hooks():
    """Verify existing broadcast helpers trigger user event dispatching."""
    while not _user_emit_queue.empty():
        try:
            _user_emit_queue.get_nowait()
        except Exception:
            break

    broadcast_material_event(200, "material_created", {"title": "Materi Baru"})
    item = _user_emit_queue.get(timeout=1.0)
    assert item["user_id"] == 200
    assert item["event"] == "material_created"
    assert item["data"]["title"] == "Materi Baru"

    broadcast_relay_update(200, room_id=1, relay_number=2, status="ON")
    item2 = _user_emit_queue.get(timeout=1.0)
    assert item2["user_id"] == 200
    assert item2["event"] == "relay_update"
    assert item2["data"]["status"] == "ON"
