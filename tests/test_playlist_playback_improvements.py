import pytest
from xiaozhi.core.utils import detect_media_url_source, extract_youtube_video_id, is_generic_playlist_query
from xiaozhi.database.sqlite_store import SQLiteStore


def test_stream_url_detection():
    url1 = "https://xiaozhiscig.biz.id/api/audio/stream/CEEK4B3FSIA?owner_id=1&mac=7C:E8:B1:A4:D6:E4"
    assert extract_youtube_video_id(url1) == "CEEK4B3FSIA"
    media1 = detect_media_url_source(url1)
    assert media1 is not None
    assert media1["video_id"] == "CEEK4B3FSIA"
    assert media1["platform"] == "youtube"

    url2 = "/api/audio/stream/tt_ZSb9BSQeT?owner_id=1"
    media2 = detect_media_url_source(url2)
    assert media2 is not None
    assert media2["video_id"] == "tt_ZSb9BSQeT"
    assert media2["platform"] == "tiktok"


def test_is_generic_playlist_query():
    assert is_generic_playlist_query("putar playlist") is True
    assert is_generic_playlist_query("putar lagu di playlist") is True
    assert is_generic_playlist_query("putar lagu di playlist saya") is True
    assert is_generic_playlist_query("playlist") is True
    assert is_generic_playlist_query("putar musik playlist") is True
    assert is_generic_playlist_query("") is True

    # Not generic (specified song/number/title)
    assert is_generic_playlist_query("1") is False
    assert is_generic_playlist_query("putar lagu 1") is False
    assert is_generic_playlist_query("nomor 2") is False
    assert is_generic_playlist_query("wednesday") is False
    assert is_generic_playlist_query("dj haning dayak") is False


def test_find_playlist_track_with_variations(tmp_path):
    db_file = tmp_path / "test.db"
    store = SQLiteStore(str(db_file))
    user = store.create_user("playlist_test_user", "password123")
    uid = user["id"]

    store.add_playlist_track(uid, title="Wednesday Theme", artist="Wednesday", youtube_url="https://www.youtube.com/watch?v=CEEK4B3FSIA", video_id="CEEK4B3FSIA")
    store.add_playlist_track(uid, title="DJ Hati Yang Dulu", artist="DJ", youtube_url="https://vt.tiktok.com/ZSb9BSQeT/", video_id="tt_ZSb9BSQeT")
    store.add_playlist_track(uid, title="DJ Haning Dayak", artist="DJ Haning", youtube_url="https://www.youtube.com/watch?v=ykVw5QYNRgo", video_id="ykVw5QYNRgo")

    # Track 1
    t1 = store.find_playlist_track_by_query(uid, "1")
    assert t1 is not None and t1["track_number"] == 1
    t1_word = store.find_playlist_track_by_query(uid, "putar lagu 1")
    assert t1_word is not None and t1_word["track_number"] == 1
    t1_lagu = store.find_playlist_track_by_query(uid, "lagu 1")
    assert t1_lagu is not None and t1_lagu["track_number"] == 1

    # Track 2
    t2 = store.find_playlist_track_by_query(uid, "mainkan lagu 2")
    assert t2 is not None and t2["track_number"] == 2

    # Track 3
    t3 = store.find_playlist_track_by_query(uid, "putar lagu ke-3")
    assert t3 is not None and t3["track_number"] == 3

    # Stream URL lookup
    stream_url = "https://xiaozhiscig.biz.id/api/audio/stream/CEEK4B3FSIA?owner_id=99"
    t_stream = store.find_playlist_track_by_query(uid, stream_url)
    assert t_stream is not None and t_stream["video_id"] == "CEEK4B3FSIA"

    # Generic query returns None (allowing prompt to ask user)
    assert store.find_playlist_track_by_query(uid, "putar playlist") is None
    assert store.find_playlist_track_by_query(uid, "putar lagu di playlist saya") is None
