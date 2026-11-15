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
