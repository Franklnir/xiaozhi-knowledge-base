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
