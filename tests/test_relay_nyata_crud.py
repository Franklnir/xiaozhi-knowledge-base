import os
import tempfile
import pytest

# Configure SQLite before importing any xiaozhi modules
temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
temp_db.close()
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SQLITE_DB_PATH"] = temp_db.name

from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.routers.relay_nyata import generate_real_relay_api_config, generate_relay_voice_commands

def test_generate_api_config():
    config = generate_real_relay_api_config("Kamar Tidur Utama")
    assert config["api_slug"].startswith("kamar-tidur-utama")
    assert config["api_client_id"].startswith("device-kamar-tidur-utama")
    assert len(config["api_token"]) >= 32

def test_generate_relay_voice_commands():
    # 1. Combining room and relay name
    cmd_on, cmd_off = generate_relay_voice_commands("Kamar Tidur Utama", "Lampu Utama", 1)
    assert cmd_on == "nyalakan lampu utama kamar tidur utama"
    assert cmd_off == "matikan lampu utama kamar tidur utama"

    # 2. If room name is already included in relay name, don't repeat room name
    cmd_on, cmd_off = generate_relay_voice_commands("Kamar", "Lampu Kamar", 1)
    assert cmd_on == "nyalakan lampu kamar"
    assert cmd_off == "matikan lampu kamar"

    # 3. Default fallback when empty
    cmd_on, cmd_off = generate_relay_voice_commands("Dapur", "", 2)
    assert cmd_on == "nyalakan relay 2 dapur"
    assert cmd_off == "matikan relay 2 dapur"

def test_sqlite_relay_crud_cycle(tmp_path):
    db_file = str(tmp_path / "test_relay.db")
    store = SQLiteStore(db_path=db_file)
    user = store.create_user("testuser", "password123")
    owner_id = user["id"]

    # 1. CREATE
    config = generate_real_relay_api_config("Ruang Tamu")
    relays = [
        {"relay_number": 1, "nama_relay": "Lampu Utama", "voice_command_on": "nyalakan lampu ruang tamu", "voice_command_off": "matikan lampu ruang tamu", "status": "OFF"},
        {"relay_number": 2, "nama_relay": "Kipas Plafon", "voice_command_on": "nyalakan kipas", "voice_command_off": "matikan kipas", "status": "OFF"},
    ]
    room_id = store.add_relay_room(owner_id, "Ruang Tamu", config["api_slug"], config["api_token"], config["api_client_id"], relays)
    assert room_id > 0

    # 2. READ & LIST
    rooms = store.list_relay_rooms(owner_id)
    assert len(rooms) == 1
    r = rooms[0]
    assert r["nama_tempat"] == "Ruang Tamu"
    assert r["api_slug"] == config["api_slug"]
    assert r["jumlah_relay"] == 2
    assert r["api_token"] == config["api_token"]
    assert r["api_commands_path"] == f"/api/device/relay/{config['api_slug']}/commands"
    assert r["api_status_path"] == f"/api/device/relay/{config['api_slug']}/status"

    # 3. GET SINGLE ROOM CONFIG
    room_detail = store.get_relay_room(owner_id, room_id)
    assert room_detail is not None
    assert room_detail["api_token"] == config["api_token"]
    assert len(room_detail["relays"]) == 2

    # 4. FIND BY SLUG & ACK
    found = store.find_relay_room_by_slug(config["api_slug"])
    assert found is not None
    assert found["room_id"] == room_id
    assert found["user_id"] == owner_id
    assert found["api_token"] == config["api_token"]

    store.ack_relay_command(owner_id, room_id, "cmd_123")
    r_after_ack = store.get_relay_room(owner_id, room_id)
    assert r_after_ack["device_connected"] is True

    # 5. UPDATE (Edit Ruangan & Relays)
    new_relays = [
        {"relay_number": 1, "nama_relay": "Lampu Gantung", "voice_command_on": "nyalakan lampu gantung", "voice_command_off": "matikan lampu gantung", "status": "ON"}
    ]
    store.update_relay_room(owner_id, room_id, "Ruang Keluarga", new_relays)
    r_updated = store.get_relay_room(owner_id, room_id)
    assert r_updated["nama_tempat"] == "Ruang Keluarga"
    assert len(r_updated["relays"]) == 1
    assert r_updated["relays"][0]["nama_relay"] == "Lampu Gantung"
    assert r_updated["relays"][0]["status"] == "ON"

    # 6. DELETE
    deleted = store.delete_relay_room(owner_id, room_id)
    assert deleted is True
    assert store.get_relay_room(owner_id, room_id) is None
    assert len(store.list_relay_rooms(owner_id)) == 0


def test_relay_voice_matching(tmp_path):
    db_file = str(tmp_path / "test_relay_voice.db")
    store = SQLiteStore(db_path=db_file)
    user = store.create_user("voicetester", "secret123")
    owner_id = user["id"]

    config = generate_real_relay_api_config("Dapur")
    relays = [
        {"relay_number": 1, "nama_relay": "Lampu Dapur", "voice_command_on": "nyalakan lampu dapur", "voice_command_off": "matikan lampu dapur", "status": "OFF"},
        {"relay_number": 2, "nama_relay": "Kulkas", "voice_command_on": "nyalakan kulkas", "voice_command_off": "matikan kulkas", "status": "ON"},
    ]
    room_id = store.add_relay_room(owner_id, "Dapur", config["api_slug"], config["api_token"], config["api_client_id"], relays)

    # Test voice matching
    matched_on = store.match_real_relay_command(owner_id, "tolong nyalakan lampu dapur sekarang")
    assert matched_on is not None
    assert matched_on["command"] == "ON"
    assert matched_on["relay"]["relay_number"] == 1

    matched_off = store.match_real_relay_command(owner_id, "xiaozhi matikan kulkas dong")
    assert matched_off is not None
    assert matched_off["command"] == "OFF"
    assert matched_off["relay"]["relay_number"] == 2

    # Test status update
    store.update_relay_status(owner_id, room_id, 1, "ON")
    room = store.get_relay_room(owner_id, room_id)
    assert room["relays"][0]["status"] == "ON"


def test_real_relay_device_status_command_model():
    from xiaozhi.database.models import RealRelayDeviceStatusCommand
    cmd = RealRelayDeviceStatusCommand.model_validate({
        "token": "tok_123",
        "command_id": None,
        "relays": [{"relay": 1, "status": "ON"}]
    })
    assert cmd.command_id is None
    assert cmd.token == "tok_123"
    assert cmd.relays[0]["status"] == "ON"


def test_normalize_relay_state_safety():
    from xiaozhi.routers.relay_nyata import normalize_relay_state
    import pytest

    # Safe normalization: any recognized ON pattern maps to ON
    assert normalize_relay_state("ON") == "ON"
    assert normalize_relay_state("on") == "ON"
    assert normalize_relay_state("1") == "ON"
    assert normalize_relay_state("true") == "ON"
    assert normalize_relay_state("TRUE") == "ON"
    assert normalize_relay_state("NYALA") == "ON"
    assert normalize_relay_state("HIDUP") == "ON"

    # Any recognized OFF pattern maps to OFF
    assert normalize_relay_state("OFF") == "OFF"
    assert normalize_relay_state("off") == "OFF"
    assert normalize_relay_state("0") == "OFF"
    assert normalize_relay_state("false") == "OFF"
    assert normalize_relay_state("MATI") == "OFF"

    # Strict safety protection: any unknown or invalid state raises ValueError to prevent unpredictable hardware actuation
    with pytest.raises(ValueError):
        normalize_relay_state(None)
    with pytest.raises(ValueError):
        normalize_relay_state("UNKNOWN_MALICIOUS_STATE")
    with pytest.raises(ValueError):
        normalize_relay_state("")


def test_relay_device_endpoint_security_and_null_command_id(tmp_path):
    import os
    db_file = str(tmp_path / "test_api_relay.db")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = db_file

    from fastapi.testclient import TestClient
    from xiaozhi.main import app
    from xiaozhi.dependencies import get_store

    store = get_store()
    user = store.create_user("device_sec_tester", "pwd12345")
    owner_id = user["id"]

    config = generate_real_relay_api_config("ESP32 Test Room")
    relays = [
        {"relay_number": 1, "nama_relay": "Relay 1", "voice_command_on": "nyalakan relay satu", "voice_command_off": "matikan relay satu", "status": "OFF"}
    ]
    store.add_relay_room(owner_id, "ESP32 Test Room", config["api_slug"], config["api_token"], config["api_client_id"], relays)

    client = TestClient(app)
    slug = config["api_slug"]
    valid_token = config["api_token"]

    # 1. Security: Reject requests without token (400)
    resp = client.get(f"/api/device/relay/{slug}/commands")
    assert resp.status_code == 400

    # 2. Security: Reject requests with invalid token (401)
    resp = client.get(f"/api/device/relay/{slug}/commands?token=wrong_token")
    assert resp.status_code == 401

    # 3. Security: Allow requests with valid token via Query
    resp = client.get(f"/api/device/relay/{slug}/commands?token={valid_token}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert len(data["relays"]) == 1

    # 4. Security: Allow requests with valid token via X-Device-Token Header
    resp = client.get(f"/api/device/relay/{slug}/commands", headers={"X-Device-Token": valid_token})
    assert resp.status_code == 200

    # 5. Safety: Test device posting status with command_id: null (the exact ESP32 payload that caused HTTP 422)
    post_payload = {
        "token": valid_token,
        "command_id": None,
        "relays": [{"relay": 1, "status": "ON"}]
    }
    resp = client.post(f"/api/device/relay/{slug}/status", json=post_payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json()["success"] is True

    # Verify relay state was updated to ON
    rooms = store.list_relay_rooms(owner_id)
    room = next(r for r in rooms if r["api_slug"] == slug)
    assert room["relays"][0]["status"] == "ON"


def test_relay_virtual_smarthome_guard_and_16_channel_security(tmp_path):
    import os
    db_file = str(tmp_path / "test_relay_virtual_guard.db")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = db_file

    from fastapi.testclient import TestClient
    from xiaozhi.main import app
    from xiaozhi.dependencies import get_store, session_serializer, SESSION_COOKIE, generate_csrf

    store = get_store()
    user = store.create_user("guard_tester", "pwd123456")
    user_id = user["id"]

    token = session_serializer.dumps({
        "id": user_id,
        "username": "guard_tester",
        "session_version": 1,
    })
    csrf = generate_csrf(user)
    client = TestClient(app, follow_redirects=False)
    cookies = {SESSION_COOKIE: token}

    # 1. Pastikan default: virtual_smarthome_enabled adalah True
    store.set_feature_setting(user_id, "virtual_smarthome_enabled", True)

    # A. Coba tambah relay saat virtual aktif -> Otomatis dinonaktifkan & notif diberikan!
    add_data = {
        "csrf_token": csrf,
        "nama_tempat": "Ruang Tamu Virtual",
        "jumlah_relay": "2",
        "relay_1_nama": "Lampu 1",
        "relay_2_nama": "Lampu 2",
    }
    resp = client.post("/relay-nyata/add", data=add_data, cookies=cookies)
    assert resp.status_code == 303
    assert "Simulasi+Virtual+otomatis+dinonaktifkan" in resp.headers["location"] or "Simulasi Virtual otomatis dinonaktifkan" in resp.headers["location"]
    assert store.get_feature_settings(user_id).get("virtual_smarthome_enabled") is False
    assert len(store.list_relay_rooms(user_id)) == 1

    # Bersihkan room untuk tes berikutnya
    for r in store.list_relay_rooms(user_id):
        store.delete_relay_room(user_id, r["id"])

    # C. Coba tambah relay dengan 17 channel (> 16 limit) -> Wajib ditolak oleh sistem!
    add_invalid_17 = {
        "csrf_token": csrf,
        "nama_tempat": "Ruang Over Limit",
        "jumlah_relay": "17",
    }
    resp = client.post("/relay-nyata/add", data=add_invalid_17, cookies=cookies)
    assert resp.status_code == 303
    assert "harus+antara+1+dan+16" in resp.headers["location"] or "harus antara 1 dan 16" in resp.headers["location"]
    assert len(store.list_relay_rooms(user_id)) == 0

    # D. Tambah relay fisik hingga 16 relay -> Wajib berhasil!
    add_valid_16 = {
        "csrf_token": csrf,
        "nama_tempat": "Ruang Server 16 Relay",
        "jumlah_relay": "16",
    }
    for i in range(1, 17):
        add_valid_16[f"relay_{i}_nama"] = f"Server Rack {i}"
        add_valid_16[f"relay_{i}_status"] = "OFF"

    resp = client.post("/relay-nyata/add", data=add_valid_16, cookies=cookies)
    assert resp.status_code == 303
    rooms = store.list_relay_rooms(user_id)
    assert len(rooms) == 1
    assert rooms[0]["nama_tempat"] == "Ruang Server 16 Relay"
    assert len(rooms[0]["relays"]) == 16
    assert rooms[0]["relays"][15]["relay_number"] == 16
    room_id = rooms[0]["id"]

    # E. Buat ruangan kedua dengan hanya 2 channel relay untuk tes otorisasi channel
    config_2ch = generate_real_relay_api_config("Kamar Anak 2CH")
    relays_2ch = [
        {"relay_number": 1, "nama_relay": "Lampu Meja", "voice_command_on": "nyalakan meja", "voice_command_off": "matikan meja", "status": "OFF"},
        {"relay_number": 2, "nama_relay": "Lampu Tidur", "voice_command_on": "nyalakan tidur", "voice_command_off": "matikan tidur", "status": "OFF"},
    ]
    room_2ch_id = store.add_relay_room(user_id, "Kamar Anak 2CH", config_2ch["api_slug"], config_2ch["api_token"], config_2ch["api_client_id"], relays_2ch)

    # F. Keamanan Kontrol: Coba aktifkan relay 5 pada ruangan yang hanya punya 2 relay -> Wajib 403 Forbidden!
    resp = client.post("/api/relay-nyata/control", json={
        "room_id": room_2ch_id,
        "relay_number": 5,
        "command": "ON"
    }, cookies=cookies)
    assert resp.status_code == 403
    assert "tidak terdaftar" in resp.json()["message"]

    # G. Keamanan Kontrol: Coba aktifkan relay 20 (> 16 batas sistem) -> Wajib 400 Bad Request!
    resp = client.post("/api/relay-nyata/control", json={
        "room_id": room_id,
        "relay_number": 20,
        "command": "ON"
    }, cookies=cookies)
    assert resp.status_code == 400
    assert "di luar batas" in resp.json()["message"]

    # H. Keamanan Kontrol: Aktifkan relay 16 pada ruangan yang punya 16 relay -> Berhasil!
    resp = client.post("/api/relay-nyata/control", json={
        "room_id": room_id,
        "relay_number": 16,
        "command": "ON"
    }, cookies=cookies)
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # I. Keamanan Mode: Nyalakan kembali Simulasi Virtual -> Kontrol relay nyata harus diblokir!
    store.set_feature_setting(user_id, "virtual_smarthome_enabled", True)
    resp = client.post("/api/relay-nyata/control", json={
        "room_id": room_id,
        "relay_number": 1,
        "command": "ON"
    }, cookies=cookies)
    assert resp.status_code == 400
    assert "Simulasi Smart Home Virtual sedang aktif" in resp.json()["message"]





def test_multi_room_voice_disambiguation_and_graceful_ack(tmp_path):
    """
    Test real user issue resolution:
    1. Multi-room collision: When commanding Relay X in Room 1 (8CH), it must NOT trigger Room 2 (6CH).
    2. Number normalization: "no 2", "nomor dua", "d ruang 1" must correctly match.
    3. Batch control per room: "matikan semua lampu di ruang 1" must target only Room 1.
    4. Graceful ACK: Sent MQTT commands return success=True even if hardware ACK has network delay.
    """
    db_file = str(tmp_path / "test_multi_room.db")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = db_file
    from xiaozhi import dependencies
    store = SQLiteStore(db_path=db_file)
    dependencies._store = store
    user = store.create_user("multiroom_tester", "pwd12345678")
    owner_id = user["id"]

    # Room 1: Ruang 1 (8 relays)
    cfg1 = generate_real_relay_api_config("Ruang 1")
    relays1 = [
        {"relay_number": i, "nama_relay": f"Lampu {i}", "voice_command_on": f"nyalakan lampu {i} ruang 1", "voice_command_off": f"matikan lampu {i} ruang 1", "status": "OFF"}
        for i in range(1, 9)
    ]
    r1_id = store.add_relay_room(owner_id, "Ruang 1", cfg1["api_slug"], cfg1["api_token"], cfg1["api_client_id"], relays1)

    # Room 2: Ruang 2 (6 relays) - created after Room 1
    cfg2 = generate_real_relay_api_config("Ruang 2")
    relays2 = [
        {"relay_number": i, "nama_relay": f"Lampu {i}", "voice_command_on": f"nyalakan lampu {i}", "voice_command_off": f"matikan lampu {i}", "status": "OFF"}
        for i in range(1, 7)
    ]
    r2_id = store.add_relay_room(owner_id, "Ruang 2", cfg2["api_slug"], cfg2["api_token"], cfg2["api_client_id"], relays2)

    # 1. Disambiguation Test: "nyalakan lampu no 2 d ruang 1" MUST match Ruang 1, NOT Ruang 2!
    m1 = store.match_real_relay_command(owner_id, "nyalakan lampu no 2 d ruang 1")
    assert m1 is not None
    assert m1["room"]["id"] == r1_id
    assert m1["relay"]["relay_number"] == 2
    assert m1["command"] == "ON"

    # 2. Spelled-out number test: "matikan lampu nomor dua di ruang 1"
    m2 = store.match_real_relay_command(owner_id, "tolong matikan lampu nomor dua di ruang 1")
    assert m2 is not None
    assert m2["room"]["id"] == r1_id
    assert m2["relay"]["relay_number"] == 2
    assert m2["command"] == "OFF"

    # 3. Room 2 command: "nyalakan lampu 3 ruang 2"
    m3 = store.match_real_relay_command(owner_id, "nyalakan lampu 3 ruang 2")
    assert m3 is not None
    assert m3["room"]["id"] == r2_id
    assert m3["relay"]["relay_number"] == 3

    # 4. Batch room command: "matikan semua lampu di ruang 1"
    m4 = store.match_real_relay_command(owner_id, "matikan semua lampu di ruang 1")
    assert m4 is not None
    assert m4.get("is_all") is True
    assert m4["room"]["id"] == r1_id
    assert m4["command"] == "OFF"

    # 5. Target & Channel matching: match_real_relay_for_target
    from xiaozhi.routers.relay_nyata import match_real_relay_for_target, control_real_relay, control_all_real_relays
    m5 = match_real_relay_for_target(owner_id, target="Ruang 1", action="nyalakan", channel=4)
    assert m5 is not None
    assert m5["room"]["id"] == r1_id
    assert m5["relay"]["relay_number"] == 4
    assert m5["command"] == "ON"

    # 6. Graceful ACK: Sent MQTT command returns success=True with pending=True
    r1 = store.get_relay_room(owner_id, r1_id)
    resp = control_real_relay(owner_id, r1, r1["relays"][1], "ON", source="mcp_voice")
    assert resp["success"] is True
    assert resp["pending"] is True
    assert "telah diproses" in resp["message"]

    # 7. Batch room control execution
    batch_resp = control_all_real_relays(owner_id, "OFF", room_id=r1_id, source="mcp_voice")
    assert batch_resp["success"] is True
    assert batch_resp["count"] == 8


def test_real_relay_voice_identification_multi_room_and_numbered_names(tmp_path):
    """
    Regression suite matching real multi-room user configurations where:
    - Relay names contain trailing digits (Garasi 1/2, Sanggar 1/2, Balkon 1/2)
      that differ from their channel index.
    - Another room name collides with part of a relay name (room 'SANGGAR' vs
      relay 'Sanggar 1' in 'Rumah') or has generic numbered names ('Lampu 1..8'
      in 'Papan').
    - Single-word relay names ('Taman', 'Ontel') and short names ('A'..'H', 'AC')
      are commanded with optional generic fillers ('lampu') or omitted room names.
    """
    from xiaozhi.routers.relay_nyata import (
        generate_relay_voice_commands,
        smart_match_real_relay,
        smart_match_real_relay_detailed,
        smart_match_real_relay_commands,
        smart_match_real_relay_for_target,
    )
    from xiaozhi.mcp.tools import register_tools
    from xiaozhi.mcp.context import mcp_active_owner_ctx
    from xiaozhi import dependencies

    class _DummyMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, *args, **kwargs):
            def _dec(fn):
                self.tools[fn.__name__] = fn
                return fn
            return _dec

    db_file = str(tmp_path / "test_sanggar_regression.db")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["SQLITE_DB_PATH"] = db_file
    store = SQLiteStore(db_path=db_file)
    dependencies._store = store
    user = store.create_user("sanggar_tester", "secret12345")
    owner_id = user["id"]
    store.set_feature_setting(owner_id, "virtual_smarthome_enabled", False)

    def _build_relays(room_name: str, names: list[str]) -> list[dict]:
        items = []
        for idx, name in enumerate(names, start=1):
            v_on, v_off = generate_relay_voice_commands(room_name, name, idx)
            items.append({
                "relay_number": idx,
                "nama_relay": name,
                "voice_command_on": v_on,
                "voice_command_off": v_off,
                "status": "OFF",
            })
        return items

    rumah_names = ["Taman", "Garasi 1", "Garasi 2", "Ontel", "Sanggar 1", "Sanggar 2", "Balkon 1", "Balkon 2"]
    sanggar_names = ["A", "B", "C", "D", "E", "F", "G", "H"]
    papan_names = [f"Lampu {i}" for i in range(1, 9)]

    cfg_s = generate_real_relay_api_config("SANGGAR")
    sanggar_id = store.add_relay_room(
        owner_id, "SANGGAR", cfg_s["api_slug"], cfg_s["api_token"], cfg_s["api_client_id"],
        _build_relays("SANGGAR", sanggar_names),
    )
    cfg_r = generate_real_relay_api_config("Rumah")
    rumah_id = store.add_relay_room(
        owner_id, "Rumah", cfg_r["api_slug"], cfg_r["api_token"], cfg_r["api_client_id"],
        _build_relays("Rumah", rumah_names),
    )

    rooms_a = store.list_relay_rooms(owner_id)

    # Config A cases: SANGGAR (A..H) + Rumah (Taman..Balkon 2)
    m = smart_match_real_relay(rooms_a, "nyalakan lampu Sanggar 1 di rumah")
    assert m and m["room"]["id"] == rumah_id and m["relay"]["relay_number"] == 5 and m["command"] == "ON"

    m = smart_match_real_relay(rooms_a, "Nyalakan lampu sanggar satu rumah")
    assert m and m["room"]["id"] == rumah_id and m["relay"]["relay_number"] == 5 and m["command"] == "ON"

    m = smart_match_real_relay(rooms_a, "nyalakan Balkon 1")
    assert m and m["room"]["id"] == rumah_id and m["relay"]["relay_number"] == 7 and m["command"] == "ON"

    for phrase, exp_cmd in [("matikan lampu taman", "OFF"), ("matikan lampu Taman", "OFF"), ("Nyalakan lampu taman", "ON")]:
        m = smart_match_real_relay(rooms_a, phrase)
        assert m and m["room"]["id"] == rumah_id and m["relay"]["relay_number"] == 1 and m["command"] == exp_cmd

    m = smart_match_real_relay(rooms_a, "nyalakan lampu ontel")
    assert m and m["room"]["id"] == rumah_id and m["relay"]["relay_number"] == 4 and m["command"] == "ON"

    # Short 1-char relays including 'D' in SANGGAR must not collide with shorthand 'd' preposition
    m = smart_match_real_relay(rooms_a, "nyalakan a di sanggar")
    assert m and m["room"]["id"] == sanggar_id and m["relay"]["relay_number"] == 1 and m["command"] == "ON"

    m = smart_match_real_relay(rooms_a, "matikan lampu d di sanggar")
    assert m and m["room"]["id"] == sanggar_id and m["relay"]["relay_number"] == 4 and m["command"] == "OFF"

    m = smart_match_real_relay(rooms_a, "matikan d sanggar")
    assert m and m["room"]["id"] == sanggar_id and m["relay"]["relay_number"] == 4 and m["command"] == "OFF"

    # Config B cases: replace SANGGAR with Papan (Lampu 1..8) + Rumah (Taman..Balkon 2)
    store.delete_relay_room(owner_id, sanggar_id)
    cfg_p = generate_real_relay_api_config("Papan")
    papan_id = store.add_relay_room(
        owner_id, "Papan", cfg_p["api_slug"], cfg_p["api_token"], cfg_p["api_client_id"],
        _build_relays("Papan", papan_names),
    )
    rooms_b = store.list_relay_rooms(owner_id)

    expected_b = [
        ("nyalakan lampu sanggar 2", rumah_id, 6, "ON"),
        ("nyalakan lampu balkon 1", rumah_id, 7, "ON"),
        ("nyalakan lampu balkon dua", rumah_id, 8, "ON"),
        ("nyalakan balkon 1", rumah_id, 7, "ON"),
        ("nyalakan lampu balkon 2 di rumah", rumah_id, 8, "ON"),
        ("matikan garasi 1", rumah_id, 2, "OFF"),
        ("matikan lampu garasi satu di rumah", rumah_id, 2, "OFF"),
        ("nyalakan lampu 1", papan_id, 1, "ON"),
        ("matikan lampu 2 di papan", papan_id, 2, "OFF"),
        ("nyalakan relay 3 di papan", papan_id, 3, "ON"),
    ]
    for phrase, exp_room, exp_num, exp_cmd in expected_b:
        m = smart_match_real_relay(rooms_b, phrase)
        assert m is not None, f"Expected match for '{phrase}'"
        assert m["room"]["id"] == exp_room, f"Wrong room for '{phrase}': {m['room']['nama_tempat']}"
        assert m["relay"]["relay_number"] == exp_num, f"Wrong relay #{m['relay']['relay_number']} ({m['relay']['nama_relay']}) for '{phrase}'"
        assert m["command"] == exp_cmd

    # Unmatched & wrong-digit safety: never trigger adjacent or wrong relay
    assert smart_match_real_relay(rooms_b, "nyalakan lampu garasi 3") is None
    assert smart_match_real_relay(rooms_b, "nyalakan kamar 1") is None
    assert smart_match_real_relay(rooms_b, "nyalakan lampu xyz di rumah") is None

    # Ambiguous request ("Nyalakan lampu balkon" when Balkon 1 & Balkon 2 both exist):
    # Must NOT blindly pick one, and MCP tool must report ambiguity cleanly
    assert smart_match_real_relay(rooms_b, "Nyalakan lampu balkon") is None
    amb = smart_match_real_relay_detailed(rooms_b, "Nyalakan lampu balkon")
    assert amb is not None and amb.get("ambiguous") is True

    mock_mcp = _DummyMCP()
    register_tools(mock_mcp, store, lambda *a, **k: None)
    token = mcp_active_owner_ctx.set(owner_id)
    try:
        res_amb = mock_mcp.tools["control_real_relay_by_voice"]("Nyalakan lampu balkon")
        assert res_amb["success"] is False
        assert res_amb.get("ambiguous") is True
        assert "Balkon 1" in res_amb["message"] and "Balkon 2" in res_amb["message"]

        # Compound voice commands separated by "dan"
        for multi_phrase, exp_nums in [
            ("nyalakan lampu sanggar 1 dan sanggar 2", [5, 6]),
            ("nyalakan lampu balkon 1 dan balkon 2", [7, 8]),
        ]:
            resolved = smart_match_real_relay_commands(rooms_b, multi_phrase)
            assert [item["relay"]["relay_number"] for item in resolved] == exp_nums
            res_multi = mock_mcp.tools["control_real_relay_by_voice"](multi_phrase)
            assert res_multi["success"] is True
            assert res_multi.get("multi") is True
            assert res_multi.get("count") == 2
    finally:
        mcp_active_owner_ctx.reset(token)

    # Extra edge cases: teens (12-16), short 2-char names (AC/TV), custom voice phrase, longest room target
    cfg_e = generate_real_relay_api_config("Ruang 10")
    relays_16 = []
    for idx in range(1, 17):
        name = "AC" if idx == 2 else ("Kipas dan Exhaust" if idx == 3 else f"Lampu {idx}")
        v_on, v_off = generate_relay_voice_commands("Ruang 10", name, idx)
        if idx == 12:
            v_on = "buka tirai barat"
        relays_16.append({
            "relay_number": idx,
            "nama_relay": name,
            "voice_command_on": v_on,
            "voice_command_off": v_off,
            "status": "OFF",
        })
    r10_id = store.add_relay_room(
        owner_id, "Ruang 10", cfg_e["api_slug"], cfg_e["api_token"], cfg_e["api_client_id"], relays_16
    )
    cfg_r1 = generate_real_relay_api_config("Ruang 1")
    store.add_relay_room(
        owner_id, "Ruang 1", cfg_r1["api_slug"], cfg_r1["api_token"], cfg_r1["api_client_id"],
        _build_relays("Ruang 1", ["Lampu 1"]),
    )
    all_rooms = store.list_relay_rooms(owner_id)

    m_ac = smart_match_real_relay(all_rooms, "nyalakan ac di ruang 10")
    assert m_ac and m_ac["room"]["id"] == r10_id and m_ac["relay"]["relay_number"] == 2

    m_12 = smart_match_real_relay(all_rooms, "nyalakan lampu dua belas di ruang 10")
    assert m_12 and m_12["room"]["id"] == r10_id and m_12["relay"]["relay_number"] == 12

    m_16 = smart_match_real_relay(all_rooms, "matikan lampu enam belas di ruang 10")
    assert m_16 and m_16["room"]["id"] == r10_id and m_16["relay"]["relay_number"] == 16 and m_16["command"] == "OFF"

    m_custom = smart_match_real_relay(all_rooms, "tolong buka tirai barat sekarang")
    assert m_custom and m_custom["room"]["id"] == r10_id and m_custom["relay"]["relay_number"] == 12

    m_dan = smart_match_real_relay_commands(all_rooms, "nyalakan kipas dan exhaust di ruang 10")
    assert len(m_dan) == 1 and m_dan[0]["relay"]["relay_number"] == 3

    m_target_10 = smart_match_real_relay_for_target(all_rooms, target="Ruang 10", action="nyalakan", channel=1)
    assert m_target_10 and m_target_10["room"]["id"] == r10_id and m_target_10["relay"]["relay_number"] == 1
