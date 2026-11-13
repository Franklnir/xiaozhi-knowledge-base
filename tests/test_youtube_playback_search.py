from datetime import datetime
from unittest.mock import Mock

from xiaozhi.services import youtube_playback_service as video


def test_channel_filter_and_latest_order_keep_relevant_titles(monkeypatch):
    entries = [
        {"id": "aaaaaaaaaaa", "title": "Tutorial ESP32", "channel": "Other", "upload_date": "20261112"},
        {"id": "bbbbbbbbbbb", "title": "Tutorial ESP32", "channel": "Maker Indo", "upload_date": "20261101"},
        {"id": "ccccccccccc", "title": "Tutorial ESP32 S3", "channel": "Maker Indo", "upload_date": "20261111"},
        {"id": "ddddddddddd", "title": "Resep ayam", "channel": "Maker Indo", "upload_date": "20261113"},
    ]
    search = Mock(return_value=(entries, "youtube_search_sample"))
    monkeypatch.setattr(video, "search_youtube_candidates", search)
    monkeypatch.setattr(video, "_video_metadata", lambda _: None)
    result = video.find_playback_choices(1, 2, "Tutorial ESP32 terbaru dari channel Maker Indo")
    assert [item["video_id"] for item in result["candidates"]] == ["ccccccccccc", "bbbbbbbbbbb"]
    assert result["candidates"][0]["published_date_utc"] == "2026-11-11"
    assert search.call_args.args[0:2] == ("Tutorial ESP32 Maker Indo", True)
    assert result["needs_confirmation"]


def test_exact_title_beats_partial_and_wrong_channel_is_not_substituted(monkeypatch):
    entries = [
        {"id": "aaaaaaaaaaa", "title": "Tutorial ESP32 S3", "channel": "Maker Indo"},
        {"id": "bbbbbbbbbbb", "title": "Tutorial ESP32", "channel": "Maker Indo"},
    ]
    monkeypatch.setattr(video, "search_youtube_candidates", lambda *_: (entries, "youtube_search_sample"))
    monkeypatch.setattr(video, "_video_metadata", lambda _: None)
    assert video.find_playback_choices(1, 2, "Tutorial ESP32")["candidates"][0]["video_id"] == "bbbbbbbbbbb"
    assert not video.find_playback_choices(1, 2, "Tutorial ESP32", channel="Other")["success"]


def test_today_uses_verified_wib_date_and_orders_same_day_by_time(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return cls(2026, 11, 13, 16, tzinfo=tz)

    monkeypatch.setattr(video, "datetime", Clock)
    entries = [
        {"id": "aaaaaaaaaaa", "title": "Update ESP32", "published_at": "2026-11-12T17:10:00Z"},
        {"id": "bbbbbbbbbbb", "title": "Update ESP32", "published_at": "2026-11-12T16:50:00Z"},
        {"id": "ccccccccccc", "title": "Update ESP32", "published_at": "2026-11-12T18:00:00Z"},
        {"id": "ddddddddddd", "title": "Update ESP32", "upload_date": "20261113"},
    ]
    monkeypatch.setattr(video, "search_youtube_candidates", lambda *_: (entries, "youtube_data_api"))
    monkeypatch.setattr(video, "_video_metadata", lambda _: None)
    result = video.find_playback_choices(1, 2, "Update ESP32 hari ini")
    assert [item["video_id"] for item in result["candidates"]] == ["ccccccccccc", "aaaaaaaaaaa"]
    assert result["candidates"][0]["published_at_wib"].startswith("2026-11-13T01:00")
