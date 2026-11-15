import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from xiaozhi.database.postgres_store import PostgresStore


@pytest.fixture
def accounts():
    dsn = os.getenv("XIAOZHI_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set XIAOZHI_TEST_POSTGRES_DSN to a separate migrated test database.")
    store = PostgresStore(dsn=dsn, min_pool_size=1, max_pool_size=5)
    suffix = uuid4().hex[:10]
    users = []
    try:
        for name in ("owner", "other"):
            users.append(store.create_user(f"{name}_{suffix}", "test-password-123")["id"])
        admin = store.ensure_admin_user(f"quota_admin_{suffix}", "test-password-123")
        users.append(admin["id"])
        yield store, users[0], users[1], admin
    finally:
        if users:
            with store._get_conn() as conn:
                conn.execute("DELETE FROM users WHERE id = ANY(%s)", (users,))
        store.close()


def add_material(store, owner, title, content="satu dua", live=False):
    return store.add_material(
        owner, title, "Data dari API" if live else "Materi", content, "",
        "https://example.test/data" if live else "",
    )


def add_room(store, owner, name):
    slug = uuid4().hex
    return store.add_relay_room(
        owner, name, slug, f"test-{slug}", slug,
        [{"relay_number": 1, "nama_relay": "Uji", "status": "OFF"}],
    )


def test_real_role_enforces_resource_ownership(accounts):
    store, owner, other, _ = accounts
    material = add_material(store, owner, "Materi akun A")
    room = add_room(store, owner, "Ruangan akun A")
    store.set_user_limits(owner, max_materials=3)
    with store._get_resource_conn(other) as conn:
        assert conn.execute("SELECT current_user AS role").fetchone()["role"] == "app_tenant_role"
        tables = ("materials", "relay_rooms", "relay_devices", "user_limits")
        for table in tables:
            assert conn.execute("SELECT row_security_active(%s::regclass) AS active", (table,)).fetchone()["active"]
        assert not conn.execute("SELECT id FROM materials WHERE id = %s", (material,)).fetchone()
        assert not conn.execute("SELECT id FROM relay_rooms WHERE id = %s", (room,)).fetchone()
        assert not conn.execute("SELECT room_id FROM relay_devices WHERE room_id = %s", (room,)).fetchone()
        assert not conn.execute("SELECT user_id FROM user_limits WHERE user_id = %s", (owner,)).fetchone()
        assert conn.execute("UPDATE materials SET title = 'Tidak boleh' WHERE id = %s", (material,)).rowcount == 0
        assert conn.execute("UPDATE relay_devices SET status = 'ON' WHERE room_id = %s", (room,)).rowcount == 0

    with store._get_resource_conn(owner) as conn:
        assert conn.execute("SELECT max_materials FROM user_limits").fetchone()["max_materials"] == 3
        assert conn.execute("UPDATE user_limits SET max_materials = 0").rowcount == 0
        assert conn.execute("SELECT id FROM materials WHERE id = %s", (material,)).fetchone()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store._get_resource_conn(other) as conn:
            conn.execute("INSERT INTO user_limits (user_id) VALUES (%s)", (other,))

    assert store.get_material(owner, material)["title"] == "Materi akun A"
    assert store.get_relay_room(owner, room)["relays"][0]["status"] == "OFF"


def test_admin_changes_take_effect_for_existing_accounts(accounts, monkeypatch):
    from fastapi.testclient import TestClient
    from xiaozhi import dependencies
    from xiaozhi.config import SESSION_COOKIE, session_serializer

    store, owner, other, admin = accounts
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", store.dsn)
    from xiaozhi.main import app
    monkeypatch.setattr(dependencies, "_store", store)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, session_serializer.dumps({
        "id": admin["id"], "username": admin["username"], "session_version": admin["session_version"],
    }))

    def apply_limits(materials, words, apis, rooms):
        response = client.post(f"/admin/users/{owner}/limits", data={
            "max_materials": materials,
            "max_words_per_material": words,
            "max_live_apis": apis,
            "max_relay_rooms": rooms,
            "csrf_token": dependencies.make_csrf_token(admin),
        }, follow_redirects=False)
        assert response.status_code == 303
        assert store._get_user_limits_dict(owner) == {
            "max_materials": materials, "max_words_per_material": words, "max_live_apis": apis, "max_relay_rooms": rooms,
        }

    assert store._get_user_limits_dict(owner) == {
        "max_materials": 3, "max_words_per_material": 6000, "max_live_apis": 2, "max_relay_rooms": 7,
    }
    ordinary_material = add_material(store, owner, "Bahan awal")
    live_material = add_material(store, owner, "API pertama", live=True)
    room = add_room(store, owner, "Ruangan pertama")
    apply_limits(2, 2, 1, 1)

    with pytest.raises(ValueError, match="2 materi"):
        add_material(store, owner, "Materi tambahan")
    with pytest.raises(ValueError, match="1 API"):
        store.update_material(owner, ordinary_material, "Bahan awal", "Data dari API", "dua kata", "", "https://example.test/data")
    with pytest.raises(ValueError, match="2 kata"):
        store.update_material(owner, ordinary_material, "Bahan awal", "Materi", "tiga\u00a0kata\u2003berbeda", "")
    with pytest.raises(ValueError, match="1 ruangan"):
        add_room(store, owner, "Ruangan tambahan")

    # Editing existing data without consuming a new quota unit is still permitted.
    assert store.update_material(owner, live_material, "API baru", "Data dari API", "dua kata", "", "https://example.test/data")
    store.update_relay_room(owner, room, "Renamed", [{"relay_number": 1}])
    assert store.get_relay_room(owner, room)["nama_tempat"] == "Renamed"
    add_material(store, other, "Materi akun lain", "empat kata masih boleh")
    add_room(store, other, "Kuota independen")

    apply_limits(3, 3, 2, 2)
    assert store.update_material(owner, ordinary_material, "Bahan awal", "Data dari API", "tiga kata sekarang", "", "https://example.test/data")
    add_material(store, owner, "Materi tambahan")
    add_room(store, owner, "Ruangan tambahan")
    with pytest.raises(ValueError, match="3 materi"):
        add_material(store, owner, "Kuota baru penuh")
    with pytest.raises(ValueError, match="2 ruangan"):
        add_room(store, owner, "Kuota baru penuh")


def test_concurrent_material_creates_respect_single_remaining_place(accounts):
    store, owner, _, _ = accounts
    store.set_user_limits(owner, max_materials=1)
    barrier = Barrier(2, timeout=5)

    def attempt(title):
        barrier.wait()
        try:
            add_material(store, owner, title)
        except ValueError as exc:
            assert "1 materi" in str(exc)
            return "blocked"
        return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("Materi pertama", "Materi kedua")))
    assert sorted(results) == ["blocked", "saved"]
    assert len(store.list_materials(owner)) == 1


def test_mcp_slots_and_boards_stay_with_their_owner(accounts):
    store, owner, other, _ = accounts
    mac_hex = "02" + uuid4().hex[:10].upper()
    mac = ":".join(mac_hex[i:i+2] for i in range(0, 12, 2))
    endpoint = "wss://example.test/mcp?token=" + uuid4().hex
    store.set_xiaozhi_token(owner, endpoint, slot=1)
    store.set_xiaozhi_token(other, endpoint + "-other", slot=1)
    store.set_xiaozhi_token(owner, endpoint + "-updated", slot=1)
    assert store.get_xiaozhi_token(owner, slot=1) == endpoint + "-updated"
    assert store.get_xiaozhi_token(other, slot=1) == endpoint + "-other"
    assert store.bind_board_to_slot(owner, slot=1, device_mac=mac)["success"]
    assert store.list_registered_devices(owner)
    assert not store.bind_board_to_slot(other, slot=1, device_mac=mac)["success"]
    assert not store.register_device(other, device_id=mac).get("success", True)
    assert not store.detach_device(mac, owner_id=other)
    assert not store.delete_device(other, mac)
    with store._get_resource_conn(other) as conn:
        for table, column in (("xiaozhi_tokens", "user_id"), ("registered_devices", "owner_id"), ("board_binding_history", "user_id")):
            query = psycopg.sql.SQL("SELECT 1 FROM {} WHERE {} = %s").format(
                psycopg.sql.Identifier(table), psycopg.sql.Identifier(column)
            )
            assert conn.execute(query, (owner,)).fetchone() is None

    assert store.get_board_binding_history(user_id=owner)[0]["status"] == "ACTIVE"
    assert store.detach_board_from_slot(owner, slot=1)["success"]
    assert store.bind_board_to_slot(other, slot=1, device_mac=mac)["success"]
    assert store.get_board_binding_history(user_id=owner)[0]["status"] == "DETACHED"
    assert store.get_board_binding_history(user_id=other)[0]["status"] == "ACTIVE"
    assert store.delete_xiaozhi_token(other, slot=1)
    assert not store.list_registered_devices(other)


def test_admin_settings_are_readable_only_by_owner_and_writable_by_admin(accounts):
    from xiaozhi.services.mcp_service import get_mcp_connection_restriction

    store, owner, other, _ = accounts
    endpoint = "wss://example.test/mcp?token=" + uuid4().hex
    store.set_xiaozhi_token(owner, endpoint, slot=1)
    store.set_xiaozhi_token(owner, endpoint + "-two", slot=2)
    store.set_user_access_plus(owner, mcp_multislot_allowed=False)
    store.set_mcp_tool_toggle(owner, "search_news", False)
    store.set_user_feature(owner, "youtube_music", False)
    store.set_feature_setting(owner, "virtual_smarthome_enabled", False)
    assert store.get_feature_settings(owner) == {
        "virtual_smarthome_enabled": False, "youtube_music_enabled": False,
    }
    assert store.get_feature_settings(other)["youtube_music_enabled"]
    assert get_mcp_connection_restriction(store, owner, 2)
    assert not get_mcp_connection_restriction(store, owner, 1)
    assert [s["slot_number"] for s in store.list_active_mcp_tokens() if s["user_id"] == owner] == [1]

    store.set_mcp_blocked(owner, True, "synthetic")
    assert get_mcp_connection_restriction(store, owner, 1)
    assert not [s for s in store.list_active_mcp_tokens() if s["user_id"] == owner]
    with store._get_resource_conn(other) as conn:
        assert not conn.execute("SELECT user_id FROM feature_settings WHERE user_id = %s", (owner,)).fetchone()
        assert not conn.execute("SELECT user_id FROM user_access_plus_settings WHERE user_id = %s", (owner,)).fetchone()
    with store._get_resource_conn(owner) as conn:
        assert conn.execute("UPDATE user_access_plus_settings SET mcp_multislot_allowed = TRUE").rowcount == 0
        assert conn.execute("UPDATE mcp_tool_toggles SET enabled = TRUE").rowcount == 0
        assert conn.execute("UPDATE mcp_user_settings SET mcp_blocked = FALSE").rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.set_feature_setting(owner, "youtube_music_enabled", True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store._get_resource_conn(owner) as conn:
            conn.execute("UPDATE xiaozhi_tokens SET is_active = TRUE WHERE slot_number = 2")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.set_xiaozhi_token(owner, endpoint + "-three", slot=3)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store._get_resource_conn(db_role="app_mcp_connector") as conn:
            conn.execute("SELECT id FROM chat_history")

    store.set_mcp_blocked(owner, False)
    store.set_user_access_plus(owner, mcp_multislot_allowed=True)
    assert not get_mcp_connection_restriction(store, owner, 2)
    assert sorted(s["slot_number"] for s in store.list_active_mcp_tokens() if s["user_id"] == owner) == [1, 2]
    assert store.set_user_slot_active(owner, 2, False)
    assert store.delete_xiaozhi_token(owner, slot=2)
    assert get_mcp_connection_restriction(store, owner, 2)
    overview = next(account for account in store.list_access_plus_overview() if account["user_id"] == owner)
    assert not overview["slots"][2]["is_active"] and not overview["slots"][2]["preview"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.set_xiaozhi_token(owner, endpoint + "-two", slot=2)
    assert store.set_user_slot_active(owner, 2, True)
    store.set_xiaozhi_token(owner, endpoint + "-two", slot=2)
    assert not get_mcp_connection_restriction(store, owner, 2)


def test_chat_persona_and_reminders_use_separate_account_and_worker_roles(accounts):
    from datetime import datetime, timedelta, timezone

    store, owner, other, _ = accounts
    store.add_chat_history(owner, source="test", tool_name="test", user_message="visible slot one", slot_number=1)
    store.add_chat_history(owner, source="test", tool_name="test", user_message="visible slot two", slot_number=2)
    slot_history = store.list_chat_history(owner, slot_number=1)
    assert [entry["user_message"] for entry in slot_history] == ["visible slot one"]
    chat_id = slot_history[0]["id"]
    assert store.get_chat_history_item(chat_id, owner)["user_message"] == "visible slot one"
    assert store.get_chat_history_item(chat_id, other) is None
    assert not store.delete_chat_history_item(other, chat_id)
    assert store.delete_chat_history_item(owner, chat_id)
    assert not store.list_chat_history(owner, slot_number=1)
    assert not store.list_chat_history(other)
    store.save_user_preference(owner, "test", "setting", "private value")
    assert store.get_user_persona(owner)
    assert not store.get_user_persona(other)

    due, future = uuid4().hex, uuid4().hex
    now = datetime.now(timezone.utc)
    store.add_reminder(owner, due, "Due test", (now - timedelta(minutes=2)).isoformat())
    store.add_reminder(other, future, "Future test", (now + timedelta(days=2)).isoformat())
    with store._get_resource_conn(other) as conn:
        assert conn.execute("UPDATE reminders SET message = 'Denied' WHERE id = %s", (due,)).rowcount == 0
        assert conn.execute("SELECT id FROM chat_history WHERE owner_id = %s", (owner,)).fetchone() is None
        assert conn.execute("SELECT id FROM user_persona WHERE owner_id = %s", (owner,)).fetchone() is None
    claimed = {row["id"] for row in store.get_due_reminders()}
    assert due in claimed and future not in claimed
    store.mark_reminder_sent(due)
    assert store.list_reminders(owner)[0]["status"] == "sent"
    assert store.list_reminders(other)[0]["status"] == "pending"
    assert not store.delete_reminder(other, due)
    assert not store.delete_reminder(owner, future, pending_only=True)
    with store._get_resource_conn(db_role="app_reminder_worker") as conn:
        assert not conn.execute("SELECT id FROM reminders WHERE id IN (%s, %s)", (due, future)).fetchall()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with store._get_resource_conn(db_role="app_reminder_worker") as conn:
            conn.execute("UPDATE reminders SET message = 'Denied'")
    assert store.delete_reminder(other, future, pending_only=True)
