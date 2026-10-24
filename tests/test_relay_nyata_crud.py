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

