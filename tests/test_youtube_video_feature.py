"""
Unit & Integration Tests for YouTube Video Scraping, Genre Classification,
Confirmation Management with Request ID, and Material Saving.
"""

import tempfile
import os
import pytest
from unittest.mock import MagicMock, patch

from xiaozhi.database.sqlite_store import SQLiteStore
from xiaozhi.mcp.context import mcp_active_owner_ctx
from xiaozhi.mcp.tools import register_tools
from xiaozhi.services.scraper_service import (
    classify_video_genre,
    format_duration_id,
    format_view_count_id,
    cache_youtube_video_request,
    get_cached_youtube_video_request,
    scrape_youtube_video_details,
)


class MockFastMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


@pytest.fixture
def test_setup(tmp_path):
    db_file = str(tmp_path / "test_yt_video.db")
    store = SQLiteStore(db_path=db_file)
    user = store.create_user("videouser", "password123")
    owner_id = user["id"]

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


def test_format_duration_and_views():
    """Test Indonesian duration and view count formatters."""
    assert format_duration_id(45) == "45 detik"
    assert format_duration_id(125) == "2 menit 5 detik"
    assert format_duration_id(3665) == "1 jam 1 menit 5 detik"

    assert "ribu penonton" in format_view_count_id(15400)
    assert "juta penonton" in format_view_count_id(2500000)
    assert "miliar penonton" in format_view_count_id(1200000000)


def test_genre_classification():
    """Test video genre taxonomy and topic extraction."""
    genre1, topic1 = classify_video_genre(
        ["Education"],
        "Tutorial Pemrograman Python Dasar untuk Mahasiswa",
        "Dalam video ini kita akan belajar dasar sintaks bahasa python.",
        ["python", "koding"]
    )
    assert genre1 in ("Pendidikan (Edukasi)", "Sains & Teknologi")
    assert "python" in topic1.lower()

    genre2, _ = classify_video_genre(
        ["Entertainment"],
        "Sketsa Lucu Komedi Stand Up",
        "Video hiburan komedi akhir pekan.",
        ["komedi", "lucu"]
    )
    assert genre2 == "Hiburan & Komedi"

    # Test Rust vs Golang by Programmer Zaman Now (even if category is Education)
    genre3, topic3 = classify_video_genre(
        ["Education"],
        "Rust vs Golang | PZN Reaction",
        "JOIN PREMIUM : https://www.youtube.com/ProgrammerZamanNow/join DISCORD PREMIUM",
        ["rust", "golang", "pzn", "reaction"]
    )
    assert genre3 == "Sains & Teknologi"
    assert "rust vs golang" in topic3.lower()


def test_request_id_caching():
    """Test that video metadata is stored and retrievable by request_id."""
    req_id = "ytr_test_999"
    data = {
        "title": "ESP32 C3 Mini Setup",
        "video_id": "vid12345678",
        "channel_name": "Tech Channel",
        "duration_formatted": "10 menit",
        "view_count_formatted": "50 ribu penonton",
        "genre": "Sains & Teknologi",
        "topic": "Microcontroller",
        "youtube_url": "https://www.youtube.com/watch?v=vid12345678",
        "success": True,
    }
    cache_youtube_video_request(req_id, data)
    cached = get_cached_youtube_video_request(req_id)
    assert cached is not None
    assert cached["video_id"] == "vid12345678"
    assert cached["channel_name"] == "Tech Channel"


def test_inspect_youtube_video_with_auto_save(test_setup):
    """Test inspect_youtube_video automatically saves to Knowledge Base if auto-save is detected."""
    owner_id, store, mcp = test_setup
    inspect_fn = mcp.tools["inspect_youtube_video"]

    mock_details = {
        "success": True,
        "request_id": "ytr_req_101",
        "video_id": "testvid1011",
        "title": "Belajar Fisika Kuantum Dasar",
        "channel_name": "Sains Asik",
        "duration_formatted": "15 menit 30 detik",
        "view_count_formatted": "120 ribu penonton",
        "genre": "Pendidikan (Edukasi)",
        "topic": "Mekanika gelombang dan foton",
        "description": "Pengantar fisika kuantum untuk pemula.",
        "thumbnail": "https://example.com/thumb.jpg",
        "youtube_url": "https://www.youtube.com/watch?v=testvid1011",
        "has_transcript": True,
        "transcript": "[00:00] Halo selamat datang di fisika kuantum [00:30] Kita bahas foton",
        "transcript_chars": 62,
    }

    with patch("xiaozhi.services.scraper_service.scrape_youtube_video_details", return_value=mock_details):
        # User explicitly requested to save in prompt
        res = inspect_fn(query="video fisika kuantum tolong simpan ke materi", auto_save_requested=False)

        assert res["success"] is True
        assert res["request_id"] == "ytr_req_101"
        assert res["already_saved"] is True
        assert "SUDAH otomatis saya simpan" in res["message"]

        # Verify it was persisted to Database Materi Belajar with category "Video YouTube"
        materials = store.list_materials(owner_id)
        assert len(materials) == 1
        assert materials[0]["category"] == "Video YouTube"
        assert "Fisika Kuantum" in materials[0]["title"]
        assert "Transkrip & Pembicaraan Subtitle Video" in materials[0]["content"]
        assert "Kita bahas foton" in materials[0]["content"]


def test_inspect_and_confirm_workflow_without_auto_save(test_setup):
    """Test full multi-turn workflow: inspect -> confirm play and save."""
    owner_id, store, mcp = test_setup
    inspect_fn = mcp.tools["inspect_youtube_video"]
    confirm_fn = mcp.tools["confirm_play_and_save_video"]

    mock_details = {
        "success": True,
        "request_id": "ytr_req_202",
        "video_id": "testvid2022",
        "title": "Tutorial ESP32 S3 Cam",
        "channel_name": "Maker Indo",
        "duration_formatted": "8 menit 20 detik",
        "view_count_formatted": "45 ribu penonton",
        "genre": "Sains & Teknologi",
        "topic": "Streaming kamera ESP32",
        "description": "Langkah menggunakan modul camera OV2640.",
        "thumbnail": "https://example.com/thumb2.jpg",
        "youtube_url": "https://www.youtube.com/watch?v=testvid2022",
        "has_transcript": True,
        "transcript": "[00:00] Halo maker hari ini kita pasang modul camera [00:30] Konfigurasi pin D0 sampai D7",
        "transcript_chars": 90,
    }

    with patch("xiaozhi.services.scraper_service.scrape_youtube_video_details", return_value=mock_details):
        # Step 1: User asks to inspect video without auto-save
        inspect_res = inspect_fn(query="putar video esp32 s3 cam")
        assert inspect_res["success"] is True
        assert inspect_res["already_saved"] is False
        assert "Apakah benar video ini yang ingin Anda putar?" in inspect_res["message"]
        assert "Maker Indo" in inspect_res["message"]
        req_id = inspect_res["request_id"]

        # Cache is populated
        cached = get_cached_youtube_video_request(req_id)
        assert cached is not None
        assert cached["has_transcript"] is True

        # Step 2: User confirms playing (play_now=True) -> automatically saves to memory database!
        confirm_res = confirm_fn(request_id=req_id, play_now=True)
        assert confirm_res["success"] is True
        assert confirm_res["played"] is True
        assert confirm_res["saved_to_materials"] is True
        assert "Memutar video" in confirm_res["message"]
        assert "database memori" in confirm_res["message"]

        # Verify saved in Database Materi (materials)
        mats = store.list_materials(owner_id)
        assert len(mats) == 1
        assert mats[0]["category"] == "Video YouTube"
        assert "ESP32 S3 Cam" in mats[0]["title"]
        assert "Konfigurasi pin D0 sampai D7" in mats[0]["content"]

        # Verify saved in User Playlist memory
        playlist_tracks = store.get_user_playlist(owner_id)
        assert len(playlist_tracks) >= 1
        assert any(t["video_id"] == "testvid2022" for t in playlist_tracks)


def test_play_youtube_song_intercepts_video_query(test_setup):
    """Test play_youtube_song intercepts video queries and delegates to inspect_youtube_video."""
    owner_id, store, mcp = test_setup
    play_fn = mcp.tools["play_youtube_song"]

    mock_details = {
        "success": True,
        "request_id": "ytr_req_303",
        "video_id": "vid_lofi_99",
        "title": "Lofi Chill Beats to Relax",
        "channel_name": "Chill Vibe",
        "duration_formatted": "1 jam 20 menit",
        "view_count_formatted": "500 ribu penonton",
        "genre": "Musik & Seni",
        "topic": "Musik santai instrumental",
        "description": "Koleksi musik santai untuk fokus.",
        "thumbnail": "https://example.com/lofi.jpg",
        "youtube_url": "https://www.youtube.com/watch?v=vid_lofi_99",
        "has_transcript": False,
        "transcript": "",
    }

    with patch("xiaozhi.services.scraper_service.scrape_youtube_video_details", return_value=mock_details):
        # User says "putar video lofi" -> should NOT directly play, must inspect and ask confirmation!
        res = play_fn(query="putar video lofi chill")
        assert res["success"] is True
        assert res.get("needs_confirmation") is True
        assert "Apakah benar video ini yang ingin Anda putar?" in res["message"]
        assert res.get("request_id") == "ytr_req_303"


def test_confirm_workflow_cancelled_by_user(test_setup):
    """Test user cancels playback (play_now=False) -> no audio queued, not saved to materials."""
    owner_id, store, mcp = test_setup
    inspect_fn = mcp.tools["inspect_youtube_video"]
    confirm_fn = mcp.tools["confirm_play_and_save_video"]

    mock_details = {
        "success": True,
        "request_id": "ytr_req_404",
        "video_id": "vid_cancel_00",
        "title": "Video Salah Sasaran",
        "channel_name": "Random Channel",
        "duration_formatted": "5 menit",
        "view_count_formatted": "1 ribu penonton",
        "genre": "Hiburan & Komedi",
        "topic": "Sketsa",
        "description": "Video random.",
        "thumbnail": "https://example.com/rand.jpg",
        "youtube_url": "https://www.youtube.com/watch?v=vid_cancel_00",
        "has_transcript": False,
        "transcript": "",
    }

    with patch("xiaozhi.services.scraper_service.scrape_youtube_video_details", return_value=mock_details):
        inspect_res = inspect_fn(query="video random")
        req_id = inspect_res["request_id"]

        # User says "bukan, jangan diputar" -> play_now=False
        confirm_res = confirm_fn(request_id=req_id, play_now=False)
        assert confirm_res["success"] is True
        assert confirm_res["played"] is False
        assert confirm_res["saved_to_materials"] is False
        assert "Baik, pemutaran video dibatalkan." in confirm_res["message"]

        # Ensure nothing was saved in Database Materi
        mats = store.list_materials(owner_id)
        assert len(mats) == 0


def test_option_c_material_consolidation(test_setup):
    """Test Opsi C: Consolidating web research and video on same topic into 1 unified material."""
    owner_id, store, mcp = test_setup
    confirm_fn = mcp.tools["confirm_play_and_save_video"]

    # 1. Existing research material
    mat_id = store.add_material(
        owner_id=owner_id,
        title="Riset: Golang Tutorial Kelebihan",
        category="Riset & Pengetahuan",
        content="Golang memiliki kelebihan konkurensi goroutine yang ringan dan kompilasi cepat.",
        keywords="golang tutorial kelebihan"
    )

    mock_details = {
        "success": True,
        "request_id": "ytr_req_505",
        "video_id": "vid_golang_01",
        "title": "Tutorial Golang Dasar untuk Pemula",
        "channel_name": "Koding ID",
        "duration_formatted": "15 menit",
        "view_count_formatted": "25 ribu penonton",
        "genre": "Sains & Teknologi",
        "topic": "Golang tutorial dasar",
        "description": "Video belajar Golang lengkap.",
        "thumbnail": "https://example.com/go.jpg",
        "youtube_url": "https://www.youtube.com/watch?v=vid_golang_01",
        "has_transcript": True,
        "transcript": "Halo semuanya, hari ini kita belajar Golang dasar.",
    }
    cache_youtube_video_request("ytr_req_505", mock_details)

    # Confirm and save video -> should merge into existing material #mat_id
    res = confirm_fn(request_id="ytr_req_505", play_now=True, save_to_materials=True)
    assert res["success"] is True
    assert res["saved_to_materials"] is True
    assert res.get("consolidated") is True

    # Check total materials: must remain 1 (consolidated), NOT 2!
    mats = store.list_materials(owner_id)
    assert len(mats) == 1
    consolidated_mat = mats[0]
    assert consolidated_mat["category"] == "Riset & Video YouTube"
    assert "Golang" in consolidated_mat["title"]
    assert "Video YouTube Terkait" in consolidated_mat["content"]
    assert "goroutine" in consolidated_mat["content"]


def test_inquiry_checks_not_playing_audio(test_setup):
    """Test user inquiry (checking last video/song/search) redirects to get_playback_status and does NOT play audio."""
    owner_id, store, mcp = test_setup
    play_fn = mcp.tools["play_youtube_song"]
    pl_play_fn = mcp.tools["play_playlist_song"]
    inspect_fn = mcp.tools["inspect_youtube_video"]
    status_fn = mcp.tools["get_playback_status"]

    # 1. User asks "cek lagu terakhir tadi apa" via play_youtube_song
    res_song = play_fn(query="cek lagu terakhir tadi apa")
    assert res_song["success"] is True
    # Must NOT contain instructions to call play_youtube!
    assert "JANGAN memutar lagu" in res_song["instructions"]
    assert "self.audio.play_youtube" not in res_song.get("message", "")

    # 2. User asks "tadi video apa" via inspect_youtube_video
    res_vid = inspect_fn(query="tadi video apa yang kita tonton")
    assert res_vid["success"] is True
    assert "JANGAN memutar lagu" in res_vid["instructions"]

    # 3. User asks "cek pemutaran terakhir" via play_playlist_song
    res_pl = pl_play_fn(query="cek pemutaran terakhir")
    assert res_pl["success"] is True
    assert "JANGAN memutar lagu" in res_pl["instructions"]

    # 4. Direct call to get_playback_status with inquiry query
    status_res = status_fn(query="cek video terakhir kita")
    assert status_res["success"] is True
    assert "instructions" in status_res
    assert "JANGAN memutar lagu atau audio!" in status_res["instructions"]

