from contextvars import Context
from datetime import datetime, timezone

import pytest

from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.database import sqlite_store
from xiaozhi.mcp.context import mcp_active_owner_ctx
from xiaozhi.mcp.tools import register_tools
from xiaozhi.services import mcp_service, reminder_service


@pytest.fixture
def tools_and_accounts(tmp_path, monkeypatch):
    class Server:
        def tool(self):
            def register(fn):
                tools[fn.__name__] = fn
                return fn
            return register

    store = SQLiteStore(str(tmp_path / "tools.db"))
    owner = store.create_user("tool_owner", "test-password")["id"]
    other = store.create_user("tool_other", "test-password")["id"]
    tools = {}
    register_tools(Server(), store, lambda *args, **kwargs: None)
    monkeypatch.setattr(mcp_service, "mcp_connection_states", {})
    return tools, store, owner, other


def test_mcp_status_is_scoped_and_omits_credentials(tools_and_accounts):
    tools, store, owner, other = tools_and_accounts
    status = tools["get_mcp_connection_status"]
    assert Context().run(status)["success"] is False
    token = "wss://example.test/mcp?token=synthetic-secret"
    store.set_xiaozhi_token(owner, token, slot=1)
    token_hash = store.get_xiaozhi_token_info(owner, slot=1)["token_hash"]
    mcp_service.set_mcp_connection_state(owner, token_hash, connected=True)
    mcp_service.set_mcp_connection_state(
        owner, token_hash, connected=False, failure_code="unauthorized", message=token
    )
    context = Context()
    context.run(mcp_active_owner_ctx.set, owner)
    result = context.run(status)
    assert len(result["slots"]) == 3
    first = result["slots"][0]
    assert first["status"] == "disconnected"
    assert first["last_connected_at"]
    assert "401" in first["failure_reason"]
    assert "synthetic-secret" not in str(result) and token_hash not in str(result)
    assert context.run(status, 1)["slots"] == [first]
    assert context.run(status, 4)["success"] is False

    context.run(mcp_active_owner_ctx.set, other)
    assert context.run(status, 1)["slots"] == [{
        "slot": 1, "status": "not_configured", "last_connected_at": None, "failure_reason": None,
    }]
    store.set_xiaozhi_token(owner, token + "-new", slot=1)
    context.run(mcp_active_owner_ctx.set, owner)
    assert context.run(status, 1)["slots"][0]["last_connected_at"] is None


def test_reminders_use_wib_and_cancel_only_owned_pending_records(tools_and_accounts, monkeypatch):
    tools, store, owner, other = tools_and_accounts
    list_pending, cancel = tools["list_reminders"], tools["cancel_reminder"]
    assert Context().run(list_pending)["success"] is False
    assert Context().run(cancel, "today")["success"] is False

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 13, 18, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(reminder_service, "datetime", FixedDateTime)
    monkeypatch.setattr(sqlite_store, "utc_now", lambda: "2026-11-13T18:00:00+00:00")
    store.add_reminder(owner, "today", "Belajar", "2026-11-13T19:00:00+00:00")
    store.add_reminder(owner, "tomorrow", "Rapat", "2026-11-14T19:00:00+00:00")
    store.add_reminder(other, "private", "Data akun lain", "2026-11-13T19:00:00+00:00")
    store.add_reminder(owner, "claimed", "Sudah diproses", "2026-11-13T17:00:00+00:00")
    assert [row["id"] for row in store.get_due_reminders()] == ["claimed"]

    context = Context()
    context.run(mcp_active_owner_ctx.set, owner)
    assert context.run(list_pending, "hari ini")["reminders"] == [{
        "id": "today", "message": "Belajar", "scheduled_at": "2026-11-14T02:00:00+07:00",
    }]
    assert [row["id"] for row in context.run(list_pending, "besok")["reminders"]] == ["tomorrow"]
    assert context.run(cancel, "private")["success"] is False
    assert context.run(cancel, "claimed")["success"] is False
    assert context.run(cancel, "today")["success"] is True
    assert context.run(cancel, "today")["success"] is False
    assert context.run(list_pending, "hari_ini")["reminders"] == []
