import pytest
from xiaozhi.core.utils import detect_media_url_source, extract_tiktok_media_info, extract_youtube_video_id
from xiaozhi.database.sqlite_store import SQLiteStore


def test_detect_media_url_source_youtube():
    # 1. Standard YouTube URL
    res = detect_media_url_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert res is not None
    assert res["platform"] == "youtube"
    assert res["video_id"] == "dQw4w9WgXcQ"
    assert "youtube.com" in res["canonical_url"]

    # 2. Short youtu.be URL
    res2 = detect_media_url_source("https://youtu.be/dQw4w9WgXcQ?t=10")
    assert res2 is not None
    assert res2["platform"] == "youtube"
    assert res2["video_id"] == "dQw4w9WgXcQ"

    # 3. YouTube shorts
    res3 = detect_media_url_source("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert res3 is not None
    assert res3["platform"] == "youtube"
    assert res3["video_id"] == "dQw4w9WgXcQ"

    # 4. Raw 11-character ID
    res4 = detect_media_url_source("dQw4w9WgXcQ")
    assert res4 is not None
    assert res4["platform"] == "youtube"
    assert res4["video_id"] == "dQw4w9WgXcQ"


def test_detect_media_url_source_tiktok():
    # 1. Full TikTok video link
    res = detect_media_url_source("https://www.tiktok.com/@username/video/7106594312292453675")
    assert res is not None
    assert res["platform"] == "tiktok"
    assert res["video_id"] == "tt_7106594312292453675"
    assert res["raw_id"] == "7106594312292453675"

    # 2. TikTok vt shortlink
    res2 = detect_media_url_source("https://vt.tiktok.com/ZS2rQ6tAB/")
    assert res2 is not None
    assert res2["platform"] == "tiktok"
    assert res2["video_id"] == "tt_ZS2rQ6tAB"
    assert res2["raw_id"] == "ZS2rQ6tAB"

    # 3. TikTok vm shortlink
    res3 = detect_media_url_source("https://vm.tiktok.com/ZM8abcdEF/")
    assert res3 is not None
    assert res3["platform"] == "tiktok"
    assert res3["video_id"] == "tt_ZM8abcdEF"

    # 4. TikTok music/sound link
    res4 = detect_media_url_source("https://www.tiktok.com/music/Original-Sound-7106594312292453675")
    assert res4 is not None
    assert res4["platform"] == "tiktok"
    assert res4["video_id"] == "tt_7106594312292453675"


def test_detect_media_url_source_invalid():
    assert detect_media_url_source("") is None
    assert detect_media_url_source("https://google.com") is None
    assert detect_media_url_source("https://facebook.com/photo.php") is None


def test_store_playlist_tiktok_support(tmp_path):
    # Use isolated SQLite store in tmp_path
    db_file = str(tmp_path / "test_store.db")
    store = SQLiteStore(db_file)

    conn = store._get_conn()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
        ("testuser", "dummyhash", "user", "2026-10-04T00:00:00Z"),
    )
    user_id = cur.lastrowid

    # Add a YouTube track
    yt_track = store.add_playlist_track(

        owner_id=user_id,
        title="Rick Astley - Never Gonna Give You Up",
        youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_id="dQw4w9WgXcQ",
        artist="Rick Astley",
    )
    assert yt_track["track_number"] == 1
    assert yt_track["video_id"] == "dQw4w9WgXcQ"

    # Add a TikTok track
    tt_track = store.add_playlist_track(
        owner_id=user_id,
        title="Viral TikTok Sound Remix",
        youtube_url="https://vt.tiktok.com/ZS2rQ6tAB/",
        video_id="tt_ZS2rQ6tAB",
        artist="DJ TikTok",
    )
    assert tt_track["track_number"] == 2
    assert tt_track["video_id"] == "tt_ZS2rQ6tAB"

    # Test find_playlist_track_by_video_id
    found_yt = store.find_playlist_track_by_video_id("dQw4w9WgXcQ", owner_id=user_id)
    assert found_yt is not None
    assert found_yt["title"] == "Rick Astley - Never Gonna Give You Up"

    found_tt = store.find_playlist_track_by_video_id("tt_ZS2rQ6tAB", owner_id=user_id)
    assert found_tt is not None
    assert found_tt["title"] == "Viral TikTok Sound Remix"

    # Test find_playlist_track_by_query
    # 1. By track number
    q1 = store.find_playlist_track_by_query(user_id, "2")
    assert q1 is not None
    assert q1["id"] == tt_track["id"]

    # 2. By voice phrase "putar lagu nomor 2"
    q2 = store.find_playlist_track_by_query(user_id, "putar lagu nomor 2")
    assert q2 is not None
    assert q2["id"] == tt_track["id"]

    # 3. By TikTok URL
    q3 = store.find_playlist_track_by_query(user_id, "https://vt.tiktok.com/ZS2rQ6tAB/")
    assert q3 is not None
    assert q3["id"] == tt_track["id"]

    # 4. By artist / title keyword
    q4 = store.find_playlist_track_by_query(user_id, "DJ TikTok")
    assert q4 is not None
    assert q4["id"] == tt_track["id"]
