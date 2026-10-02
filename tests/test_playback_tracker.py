import time
import pytest
from xiaozhi.services.playback_tracker import PlaybackTracker, PlaybackSession


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
    assert "Bagus" in session.rssi_label or "🟡" in session.rssi_label
    assert session.progress_percent > 30

    d = session.to_dict()
    assert d["chip"] == "ESP32-S3"
    assert d["status"] == "streaming"
    assert d["status_label"] == "Sedang Streaming"
    assert d["duration"] == "03:32"


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
