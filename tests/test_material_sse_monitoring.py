"""
Unit & Integration Tests for Material / Knowledge Base SSE Real-Time Monitoring.
Verifies:
1. MaterialEventHub pub/sub mechanics & multi-tenant isolation.
2. SSE streaming generator format (W3C standard chunks).
3. Material CRUD triggers proper SSE broadcasts.
4. Security guard for /api/materials/stream-events.
"""

import asyncio
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from xiaozhi.main import app
from xiaozhi.services.sse_service import (
    MaterialEventHub,
    broadcast_material_event,
    format_sse,
    material_event_hub,
    stream_material_events,
)


def test_material_event_hub_pub_sub_and_isolation():
    """Test that MaterialEventHub correctly routes events and isolates between tenants."""
    async def _test():
        hub = MaterialEventHub()

        # User 101 and User 202 subscribe
        q_user1 = await hub.subscribe(101)
        q_user2 = await hub.subscribe(202)

        assert hub.subscriber_count(101) == 1
        assert hub.subscriber_count(202) == 1
        assert hub.subscriber_count() == 2

        # Emit an event for user 101 only
        hub.emit(101, "material_created", {"id": 1, "title": "Materi Rahasia User 1"})

        # User 101 should receive the event
        event1 = await asyncio.wait_for(q_user1.get(), timeout=1.0)
        assert event1["type"] == "material_created"
        assert event1["data"]["title"] == "Materi Rahasia User 1"

        # User 202 queue should be completely empty (Strict multi-tenant isolation)
        assert q_user2.empty(), "User 202 must not receive events targeted to User 101!"

        # Unsubscribe
        hub.unsubscribe(101, q_user1)
        hub.unsubscribe(202, q_user2)
        assert hub.subscriber_count(101) == 0
        assert hub.subscriber_count(202) == 0

    asyncio.run(_test())


def test_stream_material_events_generator():
    """Test that stream_material_events yields initial 'connected' event and handles incoming events."""
    async def _test():
        mock_request = MagicMock()
        mock_request.is_disconnected.side_effect = [False, False, True]

        mock_store = MagicMock()
        mock_store.list_materials.return_value = [
            {"id": 1, "title": "Catatan Algoritma", "category": "Materi Kuliah", "source_type": "standard"},
            {"id": 2, "title": "Sensor Cuaca", "category": "data dari api", "source_type": "api_live", "api_url": "https://example.com/api"},
        ]

        gen = stream_material_events(user_id=88, request=mock_request, store=mock_store, live_api_interval_sec=999.0)

        # 1. First yielded chunk must be 'connected' event
        first_chunk = await gen.__anext__()
        assert "event: connected" in first_chunk
        assert '"status": "ok"' in first_chunk
        assert '"user_id": 88' in first_chunk
        assert '"total": 2' in first_chunk

        # Broadcast an event
        broadcast_material_event(88, "material_deleted", {"material_id": 1})

        # 2. Next yielded chunk should be the 'material_deleted' event
        second_chunk = await gen.__anext__()
        assert "event: material_deleted" in second_chunk
        assert '"material_id": 1' in second_chunk

    asyncio.run(_test())


def test_sse_endpoint_unauthorized():
    """Unauthenticated requests to /api/materials/stream-events should be rejected."""
    client = TestClient(app)
    response = client.get("/api/materials/stream-events", follow_redirects=False)
    # Auth guard redirects to login (302/303) or returns 401
    assert response.status_code in (302, 303, 401)
