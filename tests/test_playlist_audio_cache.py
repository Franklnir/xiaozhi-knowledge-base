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
        assert st["percent"] == 0

        # Downloading state (in queue or active)
        audio_cache_worker._queue_set.add(vid)
        try:
            st2 = get_track_cache_status(vid)
            assert st2["status"] == "downloading"
            assert st2["is_cached"] is False
            assert st2["percent"] == 0
        finally:
            audio_cache_worker._queue_set.discard(vid)

        # Ready state (cached file exists)
        cache_file = tmp_path / f"{vid}_16000_16k.opus"
        cache_file.write_bytes(b"OggS" + (b"0" * 15000))

        st3 = get_track_cache_status(vid)
        assert st3["status"] == "ready"
        assert st3["is_cached"] is True
        assert st3["size_bytes"] > 10240
        assert st3["percent"] == 100


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


def test_delete_track_cache(tmp_path):
    from xiaozhi.services import audio_cache_worker
    from xiaozhi.services.audio_cache_worker import delete_track_cache, get_track_cache_status

    vid = "DEL_TEST_VID"
    with patch.object(audio_cache_worker, "AUDIO_CACHE_DIR", tmp_path):
        # Create cache files
        file1 = tmp_path / f"{vid}_16000_16k.opus"
        file2 = tmp_path / f"{vid}_24000_24k.opus"
        file_tmp = tmp_path / f"{vid}_16000_16k.tmp.999.opus"
        other_file = tmp_path / "OTHER_VID_16000_16k.opus"

        file1.write_bytes(b"OggS" + (b"1" * 15000))
        file2.write_bytes(b"OggS" + (b"2" * 15000))
        file_tmp.write_bytes(b"OggS" + (b"3" * 5000))
        other_file.write_bytes(b"OggS" + (b"9" * 15000))

        # Add to tracking
        audio_cache_worker._queue_set.add(vid)
        audio_cache_worker._active_downloads.add(vid)
        audio_cache_worker._progress_state[vid] = {"status": "downloading", "percent": 50}

        deleted = delete_track_cache(vid)
        assert deleted == 3
        assert not file1.exists()
        assert not file2.exists()
        assert not file_tmp.exists()
        assert other_file.exists()  # other track file is untouched
        assert vid not in audio_cache_worker._queue_set
        assert vid not in audio_cache_worker._active_downloads
        assert vid not in audio_cache_worker._progress_state


def test_track_cache_percentage_progress(tmp_path):
    from xiaozhi.services import audio_cache_worker
    from xiaozhi.services.audio_cache_worker import get_track_cache_status

    vid = "PROGRESS_TEST_VID"
    with patch.object(audio_cache_worker, "AUDIO_CACHE_DIR", tmp_path):
        # Active downloading with progress state
        audio_cache_worker._active_downloads.add(vid)
        audio_cache_worker._progress_state[vid] = {
            "status": "downloading",
            "percent": 45,
            "current_sec": 45.0,
            "total_sec": 100,
        }

        try:
            st = get_track_cache_status(vid)
            assert st["status"] == "downloading"
            assert st["percent"] == 45
            assert st["current_sec"] == 45.0
            assert st["total_sec"] == 100
        finally:
            audio_cache_worker._active_downloads.discard(vid)
            audio_cache_worker._progress_state.pop(vid, None)


def test_api_delete_playlist_track_cleans_cache(tmp_path):
    async def _run():
        from xiaozhi.routers.playlist import api_delete_playlist_track
        from xiaozhi.services import audio_cache_worker

        db_file = tmp_path / "test_del.db"
        store = SQLiteStore(str(db_file))
        user = store.create_user("user_del", "password123")
        uid = user["id"]

        vid = "DELETE_SYNC_VID"
        t = store.add_playlist_track(
            owner_id=uid,
            title="Track to Delete",
            youtube_url=f"https://www.youtube.com/watch?v={vid}",
            video_id=vid,
            artist="Artist",
        )
        track_id = t["id"]

        with patch.object(audio_cache_worker, "AUDIO_CACHE_DIR", tmp_path):
            cache_file = tmp_path / f"{vid}_16000_16k.opus"
            cache_file.write_bytes(b"OggS" + (b"A" * 15000))
            assert cache_file.exists()

            scope = {
                "type": "http",
                "method": "DELETE",
                "path": f"/api/v1/playlist/{track_id}",
                "headers": [(b"host", b"testserver")],
            }
            req = Request(scope)

            with patch("xiaozhi.routers.playlist.require_user", return_value=user), \
                 patch("xiaozhi.routers.playlist.get_store", return_value=store):
                res = await api_delete_playlist_track(track_id, req)
                assert res["success"] is True
                assert res["cache_deleted"] is True
                # Cache file must be deleted from disk
                assert not cache_file.exists()

    asyncio.run(_run())


def test_get_playlist_cache_status_tool(tmp_path):
    from xiaozhi.mcp.context import mcp_active_owner_ctx
    from xiaozhi.mcp.tools import register_tools
    from xiaozhi.services import audio_cache_worker

    db_file = tmp_path / "test_mcp_cache.db"
    store = SQLiteStore(str(db_file))
    user = store.create_user("cache_mcp_user", "password123")
    uid = user["id"]

    # Add 3 tracks: 1 cached, 1 downloading, 1 uncached
    t1 = store.add_playlist_track(uid, title="Lagu Siap", artist="Artist 1", youtube_url="https://youtube.com/watch?v=VID_READY", video_id="VID_READY")
    t2 = store.add_playlist_track(uid, title="Lagu Proses", artist="Artist 2", youtube_url="https://youtube.com/watch?v=VID_DL", video_id="VID_DL")
    t3 = store.add_playlist_track(uid, title="Lagu Belum", artist="Artist 3", youtube_url="https://youtube.com/watch?v=VID_UNCACHED", video_id="VID_UNCACHED")

    class MockMCPServer:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn
            return decorator

    mock_mcp = MockMCPServer()

    history_records = []
    def record_history(*args):
        history_records.append(args)

    with patch.object(audio_cache_worker, "AUDIO_CACHE_DIR", tmp_path):
        # Create cache file for VID_READY
        ready_file = tmp_path / "VID_READY_16000_16k.opus"
        ready_file.write_bytes(b"OggS" + (b"B" * 15000))

        # Setup downloading state for VID_DL
        audio_cache_worker._active_downloads.add("VID_DL")
        audio_cache_worker._progress_state["VID_DL"] = {"status": "downloading", "percent": 50, "current_sec": 30.0, "total_sec": 60}

        try:
            register_tools(mock_mcp, store, record_history)
            token = mcp_active_owner_ctx.set(uid)

            # 1. Test get_playlist_cache_status overall
            res = mock_mcp.tools["get_playlist_cache_status"]("")
            assert res["success"] is True
            assert res["total_tracks"] == 3
            assert res["ready_count"] == 1
            assert res["downloading_count"] == 1
            assert res["uncached_count"] == 1
            assert "siap diputar lancar" in res["message"].lower()
            assert "proses unduh cache" in res["message"].lower()
            assert "belum diunduh" in res["message"].lower()

            # 2. Test specific track query (track 1 - ready)
            res_t1 = mock_mcp.tools["get_playlist_cache_status"]("1")
            assert res_t1["success"] is True
            assert res_t1["track"]["cache_status"] == "ready"
            assert "100% selesai diunduh" in res_t1["message"]

            # 3. Test specific track query (track 3 - uncached)
            res_t3 = mock_mcp.tools["get_playlist_cache_status"]("Lagu Belum")
            assert res_t3["success"] is True
            assert res_t3["track"]["cache_status"] == "uncached"
            assert "belum tersimpan" in res_t3["message"]

            # 4. Test play_playlist_song with ready track
            play_ready = mock_mcp.tools["play_playlist_song"]("1")
            assert play_ready["success"] is True
            assert play_ready["cache_status"] == "ready"
            assert "siap diputar dengan sangat lancar" in play_ready["message"]

            # 5. Test play_playlist_song with uncached track -> warning given
            play_uncached = mock_mcp.tools["play_playlist_song"]("3")
            assert play_uncached["success"] is True
            assert play_uncached["cache_status"] == "uncached"
            assert "putus-putus" in play_uncached["message"]
            assert "putus-putus" in play_uncached["instructions"]

            # 6. Test list_user_playlist has cache tags
            list_res = mock_mcp.tools["list_user_playlist"]()
            assert list_res["success"] is True
            assert "[Siap Lancar ⚡]" in list_res["message"]
            assert "[Unduh 50% ⏳]" in list_res["message"]
            assert "[Streaming ⚠️]" in list_res["message"]

            mcp_active_owner_ctx.reset(token)
        finally:
            audio_cache_worker._active_downloads.discard("VID_DL")
            audio_cache_worker._progress_state.pop("VID_DL", None)
