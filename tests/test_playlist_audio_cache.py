import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request

from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.routers.youtube import (
    AUDIO_CACHE_DIR,
    _get_playlist_cache_path,
    _stream_cached_opus_audio,
    audio_cache_status,
    audio_play_direct,
    audio_stream_ogg_opus,
    audio_track_cache_status,
)


def test_get_playlist_cache_path():
    p1 = _get_playlist_cache_path("CEEK4B3FSIA", 24000, "24k")
    assert p1.name == "CEEK4B3FSIA_24000_24k.opus"
    assert p1.parent == AUDIO_CACHE_DIR

    # Test sanitization
    p2 = _get_playlist_cache_path("tt_ZSb9BSQeT/evil?param=1", 16000, "16k")
    assert "/" not in p2.name
    assert "?" not in p2.name
    assert p2.name.endswith("_16000_16k.opus")


def test_stream_cached_opus_audio(tmp_path):
    async def _run():
        dummy_file = tmp_path / "test_track_24000_24k.opus"
        dummy_data = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00" + (b"X" * 15000)
        dummy_file.write_bytes(dummy_data)

        chunks = []
        async for chunk in _stream_cached_opus_audio(
            video_id="test_track",
            cache_path=dummy_file,
            bitrate="24k",
            sample_rate=24000,
            user_id=1,
            username="test_user",
            title="Test Track Title",
        ):
            chunks.append(chunk)

        received_data = b"".join(chunks)
        assert len(received_data) == len(dummy_data)
        assert received_data == dummy_data

    asyncio.run(_run())


def test_audio_stream_cache_hit_for_playlist_track(tmp_path):
    async def _run():
        db_file = tmp_path / "test_cache.db"
        store = SQLiteStore(str(db_file))
        user = store.create_user("cache_tester", "password123")
        uid = user["id"]

        vid = "PLAYLIST_VID_001"
        store.add_playlist_track(
            uid,
            title="Cached Song",
            artist="Test Artist",
            youtube_url=f"https://www.youtube.com/watch?v={vid}",
            video_id=vid,
        )

        # Buat file cache tiruan (>10KB)
        cache_path = _get_playlist_cache_path(vid, 24000, "24k")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        fake_opus = b"OggS" + (b"\xaa" * 20000)
        cache_path.write_bytes(fake_opus)

        try:
            scope = {
                "type": "http",
                "method": "GET",
                "path": f"/api/audio/stream/{vid}",
                "headers": [(b"host", b"testserver")],
                "query_string": b"",
            }
            req = Request(scope)

            with patch("xiaozhi.routers.youtube.get_store", return_value=store):
                resp = await audio_stream_ogg_opus(
                    video_id=vid,
                    request=req,
                    br="24k",
                    chip="esp32s3",
                    start=0.0,
                    owner_id=uid,
                )

                # Verifikasi header respon mengindikasikan cache HIT
                assert resp.headers.get("X-Audio-Cache") == "HIT"
                assert resp.media_type == "audio/ogg"

                # Verifikasi payload stream langsung mengalirkan data file cache
                body = []
                async for chunk in resp.body_iterator:
                    body.append(chunk)
                assert b"".join(body) == fake_opus

        finally:
            if cache_path.exists():
                cache_path.unlink()

    asyncio.run(_run())


def test_audio_stream_cache_bypass_for_non_playlist_track(tmp_path):
    async def _run():
        db_file = tmp_path / "test_non_cache.db"
        store = SQLiteStore(str(db_file))
        user = store.create_user("normal_user", "password123")
        uid = user["id"]

        non_playlist_vid = "RANDOM_SEARCH_999"

        scope = {
            "type": "http",
            "method": "GET",
            "path": f"/api/audio/stream/{non_playlist_vid}",
            "headers": [(b"host", b"testserver")],
            "query_string": b"",
        }
        req = Request(scope)

        with patch("xiaozhi.routers.youtube.get_store", return_value=store), \
             patch("xiaozhi.routers.youtube.extract_audio_url", new=AsyncMock(return_value=("http://example.com/audio.m4a", "Random Song"))), \
             patch("xiaozhi.routers.youtube._stream_opus_audio", return_value=AsyncMock()):

            resp = await audio_stream_ogg_opus(
                video_id=non_playlist_vid,
                request=req,
                br="24k",
                chip="esp32s3",
                start=0.0,
                owner_id=uid,
            )

            # Untuk track non-playlist, X-Audio-Cache harus bernilai BYPASS
            assert resp.headers.get("X-Audio-Cache") == "BYPASS"

    asyncio.run(_run())


def test_audio_cache_status_endpoints(tmp_path):
    async def _run():
        test_vid = "TEST_STATUS_VID"
        cache_path = _get_playlist_cache_path(test_vid, 24000, "24k")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(b"dummy_data_1234567890")

        try:
            # Status specific track
            track_stat = await audio_track_cache_status(test_vid)
            assert track_stat["video_id"] == test_vid
            assert track_stat["is_cached"] is True
            assert len(track_stat["cached_files"]) >= 1

            # Status global cache
            global_stat = await audio_cache_status()
            assert global_stat["success"] is True
            assert global_stat["total_cached_tracks"] >= 1
            assert global_stat["total_size_bytes"] > 0
        finally:
            if cache_path.exists():
                cache_path.unlink()

    asyncio.run(_run())


def test_audio_play_direct_playlist_track_without_duration(tmp_path):
    async def _run():
        db_file = tmp_path / "test_play_direct.db"
        store = SQLiteStore(str(db_file))
        user = store.create_user("testuser_direct", "password123")
        uid = user["id"]

        # Register device MAC
        clean_mac = "84:C7:BB:78:BE:54"
        store.register_device(uid, clean_mac, "Test Device")

        # Add playlist track with empty duration
        track = store.add_playlist_track(
            owner_id=uid,
            title="intrument tidur",
            youtube_url="https://www.youtube.com/watch?v=loO-KqvSZ6U",
            video_id="loO-KqvSZ6U",
            artist="Relaxing Music",
        )
        assert track["duration"] == ""

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/audio/play_direct",
            "headers": [(b"host", b"localhost")],
            "query_string": b"q=intrument%20tidur&mac=84:c7:bb:78:be:54",
        }
        req = Request(scope)

        with patch("xiaozhi.routers.youtube.get_store", return_value=store), \
             patch("xiaozhi.routers.youtube._can_bind_device_to_user", return_value=True):
            res = await audio_play_direct(
                request=req,
                q="intrument tidur",
                mac=clean_mac,
                owner_id=uid,
            )
            assert res["success"] is True
            assert res["video_id"] == "loO-KqvSZ6U"
            assert res["title"] == "intrument tidur"
            assert "/api/audio/stream/loO-KqvSZ6U" in res["stream_url"]

    asyncio.run(_run())


def test_check_media_duration_limit():
    import yt_dlp
    from xiaozhi.services.audio_cache_worker import check_media_duration_limit

    # 1. Video > 1 hour 10 minutes (e.g. 3 hours / 10838 seconds)
    fake_info_long = {
        "title": "Sleep Music 3 Hours",
        "duration": 10838,
        "is_live": False,
        "uploader": "Relax Piano",
    }
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=fake_info_long):
        res = check_media_duration_limit("https://www.youtube.com/watch?v=loO-KqvSZ6U", max_seconds=4200)
        assert res["valid"] is False
        assert "melebihi batas maksimal 1 jam 10 menit" in res["error"]
        assert res["duration_sec"] == 10838

    # 2. 1-Hour Version Video (e.g. 55 minutes / 3300 seconds)
    fake_info_hour = {
        "title": "Relaxing Piano 1 Hour",
        "duration": 3300,
        "is_live": False,
        "uploader": "Relax Piano",
    }
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=fake_info_hour):
        res = check_media_duration_limit("https://www.youtube.com/watch?v=piano1hour", max_seconds=4200)
        assert res["valid"] is True
        assert res["error"] is None
        assert res["duration_sec"] == 3300
        assert res["formatted_duration"] == "55:00"

    # 3. Video <= 10 minutes (e.g. 4 minutes / 240 seconds)
    fake_info_ok = {
        "title": "Normal Song",
        "duration": 240,
        "is_live": False,
        "uploader": "Artist Band",
    }
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=fake_info_ok):
        res = check_media_duration_limit("https://www.youtube.com/watch?v=normal123", max_seconds=4200)
        assert res["valid"] is True
        assert res["error"] is None
        assert res["duration_sec"] == 240
        assert res["formatted_duration"] == "04:00"

    # 4. Live stream video
    fake_info_live = {
        "title": "24/7 Live Lofi Radio",
        "duration": None,
        "is_live": True,
        "uploader": "Lofi Girl",
    }
    with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value=fake_info_live):
        res = check_media_duration_limit("https://www.youtube.com/watch?v=live123", max_seconds=4200)
        assert res["valid"] is False
        assert "live stream" in res["error"]


def test_get_track_cache_status_states(tmp_path):
    from xiaozhi.services import audio_cache_worker
    from xiaozhi.services.audio_cache_worker import get_track_cache_status

    vid = "TEST_STATUS_VID"
    # Uncached state
    with patch.object(audio_cache_worker, "AUDIO_CACHE_DIR", tmp_path):
        st = get_track_cache_status(vid)
        assert st["status"] == "uncached"
        assert st["is_cached"] is False

        # Downloading state (in queue or active)
        audio_cache_worker._queue_set.add(vid)
        try:
            st2 = get_track_cache_status(vid)
            assert st2["status"] == "downloading"
            assert st2["is_cached"] is False
        finally:
            audio_cache_worker._queue_set.discard(vid)

        # Ready state (cached file exists)
        cache_file = tmp_path / f"{vid}_16000_16k.opus"
        cache_file.write_bytes(b"OggS" + (b"0" * 15000))

        st3 = get_track_cache_status(vid)
        assert st3["status"] == "ready"
        assert st3["is_cached"] is True
        assert st3["size_bytes"] > 10240


def test_audio_stream_fallback_to_other_cached_bitrate(tmp_path):
    """If exact sr/br is not cached, but another sample rate exists for the playlist track, it hits disk cache."""
    async def _run():
        db_file = tmp_path / "test_fallback.db"
        store = SQLiteStore(str(db_file))
        user = store.create_user("cache_fallback_user", "password123")
        uid = user["id"]

        vid = "FALLBACK_VID_999"
        store.add_playlist_track(
            uid,
            title="Pre-cached Song",
            artist="Artist",
            youtube_url=f"https://www.youtube.com/watch?v={vid}",
            video_id=vid,
        )

        # Suppose background worker pre-cached at 16000_16k
        cached_file = AUDIO_CACHE_DIR / f"{vid}_16000_16k.opus"
        cached_file.parent.mkdir(parents=True, exist_ok=True)
        cached_file.write_bytes(b"OggS" + (b"\x99" * 18000))

        try:
            scope = {
                "type": "http",
                "method": "GET",
                "path": f"/api/audio/stream/{vid}",
                "headers": [(b"host", b"testserver")],
                "query_string": b"",
            }
            req = Request(scope)

            with patch("xiaozhi.routers.youtube.get_store", return_value=store):
                # Request with 24000Hz (e.g. ESP32-S3 default), but file exists at 16000Hz
                resp = await audio_stream_ogg_opus(
                    video_id=vid,
                    request=req,
                    br="24k",
                    chip="esp32s3",
                    start=0.0,
                    owner_id=uid,
                )
                assert resp.headers.get("X-Audio-Cache") == "HIT"
        finally:
            if cached_file.exists():
                cached_file.unlink()

    asyncio.run(_run())


def test_add_playlist_track_1_hour_policy(tmp_path):
    async def _run():
        from fastapi import HTTPException
        from xiaozhi.routers.playlist import api_add_playlist_track

        db_file = tmp_path / "test_1hour.db"
        store = SQLiteStore(str(db_file))
        user = store.create_user("user_policy", "password123")
        uid = user["id"]

        # 1. Add first 55-minute track (Allowed as the 1 long track)
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/playlist",
            "headers": [(b"host", b"testserver")],
        }
        req1 = Request(scope)
        req1._json = {
            "youtube_url": "https://www.youtube.com/watch?v=long1111111",
            "title": "Long Track 1",
            "artist": "Artist",
        }

        fake_dur_55m = {
            "valid": True,
            "duration_sec": 3300,
            "formatted_duration": "55:00",
            "title": "Long Track 1",
            "artist": "Artist",
        }
        with patch("xiaozhi.routers.playlist.require_user", return_value=user), \
             patch("xiaozhi.routers.playlist.get_store", return_value=store), \
             patch("xiaozhi.routers.playlist.check_media_duration_limit", return_value=fake_dur_55m):
            res1 = await api_add_playlist_track(req1)
            assert res1["success"] is True
            assert res1["track"]["duration"] == "55:00"

        # 2. Add second 45-minute track (Must be REJECTED with 400 because 1 long track already exists)
        req2 = Request(scope)
        req2._json = {
            "youtube_url": "https://www.youtube.com/watch?v=long2222222",
            "title": "Long Track 2",
            "artist": "Artist",
        }
        fake_dur_45m = {
            "valid": True,
            "duration_sec": 2700,
            "formatted_duration": "45:00",
            "title": "Long Track 2",
            "artist": "Artist",
        }
        with patch("xiaozhi.routers.playlist.require_user", return_value=user), \
             patch("xiaozhi.routers.playlist.get_store", return_value=store), \
             patch("xiaozhi.routers.playlist.check_media_duration_limit", return_value=fake_dur_45m):
            with pytest.raises(HTTPException) as exc_info:
                await api_add_playlist_track(req2)
            assert exc_info.value.status_code == 400
            assert "sudah memiliki 1 lagu berdurasi panjang versi 1 jam" in exc_info.value.detail

        # 3. Add 4-minute track (Must be ALLOWED)
        req3 = Request(scope)
        req3._json = {
            "youtube_url": "https://www.youtube.com/watch?v=short333333",
            "title": "Short Track",
            "artist": "Artist",
        }
        fake_dur_4m = {
            "valid": True,
            "duration_sec": 240,
            "formatted_duration": "04:00",
            "title": "Short Track",
            "artist": "Artist",
        }
        with patch("xiaozhi.routers.playlist.require_user", return_value=user), \
             patch("xiaozhi.routers.playlist.get_store", return_value=store), \
             patch("xiaozhi.routers.playlist.check_media_duration_limit", return_value=fake_dur_4m):
            res3 = await api_add_playlist_track(req3)
            assert res3["success"] is True
            assert res3["track"]["duration"] == "04:00"

    asyncio.run(_run())



