from types import SimpleNamespace
from unittest.mock import Mock

from xiaozhi.services import youtube_research_service as research


def test_youtube_research_returns_multiple_recent_unique_sources(monkeypatch):
    entries = [
        {"id": "aaaaaaaaaaa", "title": "Older", "upload_date": "20261108"},
        {"id": "bbbbbbbbbbb", "title": "Newer", "upload_date": "20261111"},
        {"id": "aaaaaaaaaaa", "title": "Duplicate", "upload_date": "20261108"},
        {"id": "ccccccccccc", "title": "Outside", "upload_date": "20261112"},
        {"id": "not a video"},
    ]
    search = Mock(return_value=(entries, "youtube_search_sample"))
    monkeypatch.setattr(research, "search_youtube_candidates", search)

    class Captions(list):
        language_code = "id"

    captions = Captions([
        SimpleNamespace(text="Judul\n dan isi", start=0),
        SimpleNamespace(text="Judul dan isi", start=1),
        SimpleNamespace(text="Bagian berikutnya", start=32),
    ])
    api = Mock()
    api.list.return_value.find_transcript.return_value.fetch.return_value = captions
    monkeypatch.setattr(research, "YouTubeTranscriptApi", Mock(return_value=api))
    result = research.research_youtube("topik", end_date="2026-11-11", limit=2)

    assert result["multiple_sources"]
    assert [source["title"] for source in result["sources"]] == ["Newer", "Older"]
    assert result["sources"][0]["transcript_excerpt"] == "[00:00] Judul dan isi\n\n[00:32] Bagian berikutnya"
    assert result["sources"][0]["url"] == "https://www.youtube.com/watch?v=bbbbbbbbbbb"
    assert result["skipped"] == ["di_luar_periode"]
    assert api.list.call_count == 2


def test_youtube_research_reports_unavailable_captions(monkeypatch):
    monkeypatch.setattr(research, "search_youtube_candidates", lambda *_: (
        [{"id": "aaaaaaaaaaa", "upload_date": "20261108"}], "youtube_search_sample"
    ))
    monkeypatch.setattr(research, "YouTubeTranscriptApi", Mock(side_effect=RuntimeError))
    result = research.research_youtube("topik")
    assert result["success"] is False
    assert result["sources"] == []
    assert result["skipped"] == ["subtitle_gagal_diambil"]
    assert not research.research_youtube("topik", start_date="kemarin")["success"]


def test_youtube_api_search_uses_date_bounds(monkeypatch):
    monkeypatch.setenv("YOUTUBE_DATA_API_KEY", "synthetic-key")
    response = Mock()
    response.json.return_value = {"items": []}
    request = Mock(return_value=response)
    monkeypatch.setattr(research.requests, "get", request)
    from datetime import date
    entries, method = research.search_youtube_candidates("topik", True, date(2026, 11, 1), date(2026, 11, 2), 4)
    assert not entries and method == "youtube_data_api"
    params = request.call_args.kwargs["params"]
    assert params["order"] == "date"
    assert params["publishedAfter"] == "2026-11-01T00:00:00Z"
    assert params["publishedBefore"] == "2026-11-02T23:59:59.999Z"
