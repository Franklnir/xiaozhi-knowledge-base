import os
import tempfile
import pytest

# Configure SQLite backend for testing
temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
temp_db.close()
os.environ["DB_BACKEND"] = "sqlite"
os.environ["SQLITE_DB_PATH"] = temp_db.name

from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.services.smarthome_service import (
    parse_smart_home_action,
    resolve_smart_home_target,
    set_smart_home_relay,
    get_smart_home_state,
    set_all_smart_home_relays,
    smart_home_status,
)
from xiaozhi.mcp.context import mcp_active_owner_ctx
from xiaozhi.mcp.tools import register_tools


class MockFastMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


@pytest.fixture
def test_env(tmp_path):
    db_file = str(tmp_path / "test_smarthome.db")
    store = SQLiteStore(db_path=db_file)
    user = store.create_user("smarthome_user", "password123")
    owner_id = user["id"]
    store.set_feature_setting(owner_id, "virtual_smarthome_enabled", True)
    
    mock_mcp = MockFastMCP()
    history = []
    def record_history(uid, name, query, args, res):
        history.append((uid, name, query, args, res))
        
    register_tools(mock_mcp, store, record_history)
    
    token = mcp_active_owner_ctx.set(owner_id)
    try:
        yield owner_id, store, mock_mcp
    finally:
        mcp_active_owner_ctx.reset(token)


def test_parse_smart_home_action_fungsikan_and_variations():
    # Test ON actions
    for phrase in ["on", "turn on", "nyala", "nyalakan", "hidup", "hidupkan", "aktifkan", "fungsikan", "operasikan", "bunyikan", "dinginkan", "fungsikanlah", "tolong fungsikan"]:
        assert parse_smart_home_action(phrase) is True, f"Failed for {phrase}"

    # Test OFF actions
    for phrase in ["off", "turn off", "mati", "matikan", "padam", "padamkan", "nonaktifkan", "hentikan", "senyapkan", "diamkan", "matikanlah"]:
        assert parse_smart_home_action(phrase) is False, f"Failed for {phrase}"

    # Test invalid action
    with pytest.raises(ValueError):
        parse_smart_home_action("acak adul")


def test_resolve_smart_home_target_comprehensive():
    # Channel 1: Ruang Tamu / Lampu Utama
    assert resolve_smart_home_target("ruang tamu")[0] == 1
    assert resolve_smart_home_target("lampu utama")[0] == 1
    assert resolve_smart_home_target("lampu ruang tamu")[0] == 1

    # Channel 2: Dapur / Lampu Dapur
    assert resolve_smart_home_target("dapur")[0] == 2
    assert resolve_smart_home_target("lampu dapur")[0] == 2

    # Channel 3: Kamar Mandi / Toilet / WC
    assert resolve_smart_home_target("kamar mandi")[0] == 3
    assert resolve_smart_home_target("lampu kamar mandi")[0] == 3
    assert resolve_smart_home_target("toilet")[0] == 3
    assert resolve_smart_home_target("wc")[0] == 3

    # Channel 4: Kamar Tidur Utama / Master Bedroom
    assert resolve_smart_home_target("kamar tidur utama")[0] == 4
    assert resolve_smart_home_target("kamar utama")[0] == 4
    assert resolve_smart_home_target("master bedroom")[0] == 4
    assert resolve_smart_home_target("lampu kamar utama")[0] == 4

    # Channel 5: Teras / Lampu Depan
    assert resolve_smart_home_target("teras")[0] == 5
    assert resolve_smart_home_target("lampu teras")[0] == 5
    assert resolve_smart_home_target("lampu depan")[0] == 5

    # Channel 6: Basement / Alarm / Sirine
    assert resolve_smart_home_target("alarm")[0] == 6
    assert resolve_smart_home_target("sirine")[0] == 6
    assert resolve_smart_home_target("basement")[0] == 6
    assert resolve_smart_home_target("alarm rumah")[0] == 6

    # Channel 7: Kamar Tidur 2
    assert resolve_smart_home_target("kamar tidur 2")[0] == 7
    assert resolve_smart_home_target("kamar 2")[0] == 7
    assert resolve_smart_home_target("lampu kamar kedua")[0] == 7

    # Channel 8: Ruang AC / Pendingin
    assert resolve_smart_home_target("ac")[0] == 8
    assert resolve_smart_home_target("ruang ac")[0] == 8
    assert resolve_smart_home_target("pendingin ruangan")[0] == 8


def test_mcp_control_smart_home_room_with_fungsikan(test_env):
    owner_id, store, mock_mcp = test_env
    tools = mock_mcp.tools
    
    # 1. Fungsikan lampu dapur
    res = tools["control_smart_home_room"](target="lampu dapur", action="fungsikan")
    assert res["success"] is True
    assert res["state"] is True
    assert res["channel"] == 2
    state = get_smart_home_state(owner_id)
    assert state["2"] is True

    # 2. Matikan lampu dapur
    res = tools["control_smart_home_room"](target="lampu dapur", action="matikan")
    assert res["success"] is True
    assert res["state"] is False
    state = get_smart_home_state(owner_id)
    assert state["2"] is False

    # 3. Fungsikan AC
    res = tools["control_smart_home_room"](target="ac", action="fungsikan")
    assert res["success"] is True
    assert res["channel"] == 8
    assert res["state"] is True
    state = get_smart_home_state(owner_id)
    assert state["8"] is True

    # 4. Fungsikan / Bunyikan Alarm
    res = tools["control_smart_home_room"](target="alarm", action="bunyikan")
    assert res["success"] is True
    assert res["channel"] == 6
    assert res["state"] is True
    assert "Alarm aktif" in res["message"]
    state = get_smart_home_state(owner_id)
    assert state["6"] is True

    # 5. Hentikan / Matikan Alarm
    res = tools["control_smart_home_room"](target="alarm", action="hentikan")
    assert res["success"] is True
    assert res["state"] is False
    state = get_smart_home_state(owner_id)
    assert state["6"] is False

    # 6. Fungsikan semua ruangan
    res = tools["control_smart_home_room"](target="semua", action="fungsikan")
    assert res["success"] is True
    state = get_smart_home_state(owner_id)
    for ch in range(1, 9):
        assert state[str(ch)] is True


def test_mcp_control_all_relays_and_status(test_env):
    owner_id, store, mock_mcp = test_env
    tools = mock_mcp.tools

    # Nyalakan semua
    res = tools["all_relays_on"]()
    assert res["success"] is True
    state = get_smart_home_state(owner_id)
    for ch in range(1, 9):
        assert state[str(ch)] is True

    # Status
    status_res = tools["get_relay_status"]()
    assert status_res["success"] is True
    assert status_res["relays"]["1"]["state"] is True

    # Matikan semua
    res = tools["all_relays_off"]()
    assert res["success"] is True
    state = get_smart_home_state(owner_id)
    for ch in range(1, 9):
        assert state[str(ch)] is False


def test_mcp_set_smarthome_simulation_mode(test_env):
    owner_id, store, mock_mcp = test_env
    tools = mock_mcp.tools

    # Matikan mode simulasi
    res = tools["set_smarthome_simulation_mode"](action="matikan")
    assert res["success"] is True
    assert res["virtual_smarthome_enabled"] is False
    assert store.get_feature_settings(owner_id)["virtual_smarthome_enabled"] is False

    # Aktifkan kembali mode simulasi
    res = tools["set_smarthome_simulation_mode"](action="aktifkan")
    assert res["success"] is True
    assert res["virtual_smarthome_enabled"] is True
    assert store.get_feature_settings(owner_id)["virtual_smarthome_enabled"] is True


def test_handle_real_tool_with_virtual_relay(test_env):
    owner_id, store, mock_mcp = test_env
    from xiaozhi.mcp.server import handle_real_tool_with_virtual_relay

    # Test control_real_relay_by_voice routed to virtual
    res = handle_real_tool_with_virtual_relay(owner_id, "control_real_relay_by_voice", {"user_message": "nyalakan lampu teras"})
    assert res["success"] is True
    assert res["channel"] == 5
    assert res["state"] is True
    assert get_smart_home_state(owner_id)["5"] is True

    # Test matikan via real relay voice command
    res = handle_real_tool_with_virtual_relay(owner_id, "control_real_relay_by_voice", {"user_message": "matikan lampu teras"})
    assert res["success"] is True
    assert res["state"] is False
    assert get_smart_home_state(owner_id)["5"] is False

    # Test all_real_relays_on routed to virtual
    res = handle_real_tool_with_virtual_relay(owner_id, "all_real_relays_on", {})
    assert res["success"] is True
    state = get_smart_home_state(owner_id)
    assert all(state[str(ch)] for ch in range(1, 9))

    # Test get_real_relay_status routed to virtual
    res = handle_real_tool_with_virtual_relay(owner_id, "get_real_relay_status", {})
    assert res["success"] is True
    assert "relays" in res
    assert res["relays"]["5"]["state"] is True
