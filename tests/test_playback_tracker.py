import time
import pytest
from xiaozhi.services.playback_tracker import PlaybackTracker, PlaybackSession


@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    import xiaozhi.dependencies
    monkeypatch.setattr(xiaozhi.dependencies, "get_store", lambda: None)


def test_playback_session_attributes_and_duration():
    session = PlaybackSession(
        session_id="test_sess_1",
        user_id=1,
        username="frank",
        video_id="dQw4w9WgXcQ",
        title="Never Gonna Give You Up",
        stream_type="HTTP Stream",
        device_mac="10:06:1C:82:70:C8",
        bitrate="24k@24kHz",
        started_at=time.time() - 75,  # 1m 15s ago
        duration="03:32",
        duration_seconds=212,
        rssi=-72,
        chip="esp32-s3",
    )

    assert session.elapsed_seconds >= 74
    assert session.elapsed_formatted == "01:15"
    assert session.duration_formatted == "03:32"
    assert session.chip_display == "ESP32-S3"
    assert session.board_display == "ESP32-S3"
    assert "Bagus" in session.rssi_label or "🟡" in session.rssi_label
    assert session.progress_percent > 30

    d = session.to_dict()
    assert d["chip"] == "ESP32-S3"
    assert d["board"] == "ESP32-S3"
    assert d["board_display"] == "ESP32-S3"
    assert d["status"] == "streaming"
    assert d["status_label"] == "Sedang Streaming"
    assert d["duration"] == "03:32"

    # Test custom board type
    s_box = PlaybackSession("s_box", 2, "user", "vid", "title", chip="esp32-s3", board="ESP32-S3-BOX")
    assert s_box.board_display == "ESP32-S3-BOX"
    assert s_box.to_dict()["board_display"] == "ESP32-S3-BOX"


def test_playback_rssi_levels():
    s_good = PlaybackSession("s1", 1, "u", "v", "t", rssi=-55)
    assert "Sangat Kuat" in s_good.rssi_label

    s_med = PlaybackSession("s2", 1, "u", "v", "t", rssi=-70)
    assert "Bagus" in s_med.rssi_label

    s_weak = PlaybackSession("s3", 1, "u", "v", "t", rssi=-80)
    assert "Lemah" in s_weak.rssi_label

    s_bad = PlaybackSession("s4", 1, "u", "v", "t", rssi=-90)
    assert "Sangat Jelek" in s_bad.rssi_label


def test_playback_tracker_grace_period_and_transitions():
    tracker = PlaybackTracker()

    # 1. Start streaming session
    s = tracker.start_session(
        user_id=10,
        username="alex",
        video_id="abc12345",
        title="Test Track",
        chip="esp32-s3",
        rssi=-82,
        duration="04:00",
        duration_seconds=240,
    )
    assert s.status == "streaming"
    assert len(tracker.get_active_sessions()) == 1

    # 2. Download finishes (stream_eof / finished) -> transitions to buffering with grace period
    tracker.end_session(s.session_id, reason="finished", total_bytes=65536)
    active = tracker.get_active_sessions()
    assert len(active) == 1
    assert active[0]["status"] == "buffering"
    assert "Memutar Buffer" in active[0]["status_label"]
    assert "64 KB" in active[0]["status_detail"]
    assert active[0]["grace_until"] is not None

    # 3. If Wi-Fi cancels / drops sejenak -> transitions to interrupted with grace period
    s2 = tracker.start_session(
        user_id=11,
        username="budi",
        video_id="xyz98765",
        title="Another Song",
        chip="esp32-c3",
        rssi=-88,
    )
    tracker.end_session(s2.session_id, reason="cancelled")
    active_map = {sess["user_id"]: sess for sess in tracker.get_active_sessions()}
    assert 11 in active_map
    assert active_map[11]["status"] == "interrupted"
    assert "Sinyal Terputus" in active_map[11]["status_label"]
    assert "auto-reconnect" in active_map[11]["status_detail"]

    # 4. If admin stops playback -> dropped immediately
    tracker.stop_session(s.session_id)
    active_now = tracker.get_active_sessions()
    assert not any(x["session_id"] == s.session_id for x in active_now)

    # 5. If device reports finished / idle -> dropped immediately
    tracker.handle_device_status(11, "finished")
    active_final = tracker.get_active_sessions()
    assert not any(x["user_id"] == 11 for x in active_final)


def test_concurrent_multi_device_playback():
    tracker = PlaybackTracker()

    # User 1 has Board A and Board B playing concurrently
    sess_a = tracker.start_session(
        user_id=1,
        username="frank",
        video_id="song_a",
        title="Lagu Ruang Tamu",
        device_mac="10:06:1C:82:70:C8",
    )
    sess_b = tracker.start_session(
        user_id=1,
        username="frank",
        video_id="song_b",
        title="Lagu Kamar Tidur",
        device_mac="E8:3D:C1:9B:B5:14",
    )

    # Different user (User 2) plays on Board C concurrently
    sess_c = tracker.start_session(
        user_id=2,
        username="budi",
        video_id="song_c",
        title="Lagu Kantor",
        device_mac="24:DC:C3:99:11:22",
    )

    active = tracker.get_active_sessions()
    active_sids = [s["session_id"] for s in active]
    assert sess_a.session_id in active_sids
    assert sess_b.session_id in active_sids
    assert sess_c.session_id in active_sids
    assert len(active) == 3

    # If Board A skips to next track, only Board A is replaced, Board B and C keep playing!
    sess_a2 = tracker.start_session(
        user_id=1,
        username="frank",
        video_id="song_a_next",
        title="Lagu Ruang Tamu Track 2",
        device_mac="10:06:1C:82:70:C8",
    )

    active_after = tracker.get_active_sessions()
    active_sids_after = [s["session_id"] for s in active_after]
    assert sess_a.session_id not in active_sids_after
    assert sess_a2.session_id in active_sids_after
    assert sess_b.session_id in active_sids_after
    assert sess_c.session_id in active_sids_after
    assert len(active_after) == 3


def test_playback_abort_kills_subprocess():
    """Verify that triggering abort immediately kills the underlying proc."""
    class DummyProc:
        def __init__(self):
            self.killed = False
            self.returncode = None

        def kill(self):
            self.killed = True
            self.returncode = -9

    dummy = DummyProc()
    session = PlaybackSession(
        session_id="proc_test",
        user_id=5,
        username="tester",
        video_id="vid123",
        title="Test Song",
        proc=dummy
    )

    assert not session.abort_event.is_set()
    assert not dummy.killed

    session.trigger_abort()
    assert session.abort_event.is_set()
    assert dummy.killed
    assert dummy.returncode == -9


def test_stop_device_playback_and_user_mac_lookup(monkeypatch):
    """Verify that stop_device_playback aborts session matching device MAC, and stop_user_playback cascades to user MACs."""
    class MockStore:
        def get_user_mac_address(self, uid):
            return "AA:BB:CC:DD:EE:FF" if uid == 7 else None

        def get_user_mac_addresses(self, uid):
            return ["AA:BB:CC:DD:EE:FF"] if uid == 7 else []

        def get_user_devices(self, uid):
            return [{"device_id": "AA:BB:CC:DD:EE:FF"}] if uid == 7 else []

    import xiaozhi.dependencies
    monkeypatch.setattr(xiaozhi.dependencies, "get_store", lambda: MockStore())

    tracker = PlaybackTracker()
    # Session started with user_id = 0 (e.g. unknown hardware request), but with device_mac
    sess = tracker.start_session(
        user_id=0,
        username="ESP32 Board",
        video_id="hw_song",
        title="Hardware Stream",
        device_mac="AA:BB:CC:DD:EE:FF"
    )

    assert sess.session_id in [s["session_id"] for s in tracker.get_active_sessions()]

    # Calling stop_user_playback(7) will look up MAC AA:BB:CC:DD:EE:FF and abort this session!
    stopped = tracker.stop_user_playback(7)
    assert stopped is True
    assert sess.session_id not in [s["session_id"] for s in tracker.get_active_sessions()]

