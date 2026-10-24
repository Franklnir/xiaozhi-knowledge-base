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


