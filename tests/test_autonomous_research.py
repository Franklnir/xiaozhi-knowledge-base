from threading import Barrier, Event
from unittest.mock import Mock

from xiaozhi.services import scraper_service as scraper
from xiaozhi.services import youtube_research_service as youtube
from xiaozhi.services.research_audit import opened_research_urls


def test_auto_research_searches_all_sources_concurrently(monkeypatch):
    started = Barrier(3)
    article = {"title": "Panduan ESP32", "url": "https://example.com/guide"}
    paper = {"title": "Studi ESP32", "url": "https://example.com/paper", "abstract": "Abstrak riset"}
    video = {"id": "aaaaaaaaaaa", "title": "Tutorial ESP32", "channel": "Edukasi"}

    def result(value):
        def search(*args, **kwargs):
            started.wait(timeout=2)
            return value
        return search

    monkeypatch.setattr(scraper, "search_web_sources", result([article]))
    monkeypatch.setattr(scraper, "search_academic_papers_unified", result({"papers": [paper]}))
    monkeypatch.setattr(youtube, "search_youtube_candidates", result(([video], "youtube_data_api")))
    monkeypatch.setattr(scraper, "scrape_url", lambda *args, **kwargs: {"success": True, "content": "Isi panduan"})
    monkeypatch.setattr(youtube, "read_research_video_captions", lambda source, **kwargs: (
        {**source, "transcript_excerpt": "[00:00] Pembahasan ESP32"}, None,
    ))

    research = scraper.conduct_autonomous_research("ESP32")

    assert research["sources"]["web"][0]["excerpt"] == "Isi panduan"
    assert research["sources"]["academic"] == [paper]
    assert research["sources"]["youtube"][0]["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    assert "[00:00] Pembahasan ESP32" in research["summary"]
    assert research["needs_user_confirmation"]
    assert not research["saved_to_db"]


def test_auto_research_keeps_video_link_when_other_sources_timeout(monkeypatch):
    release = Event()
    video = {"video_id": "aaaaaaaaaaa", "title": "Tutorial ESP32", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}

    def slow(*args, **kwargs):
        release.wait(timeout=3)
        return {}

    def unavailable(*args, **kwargs):
        raise OSError("Source unavailable")

    monkeypatch.setattr(scraper, "_RESEARCH_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(scraper, "search_web_sources", unavailable)
    monkeypatch.setattr(scraper, "search_academic_papers_unified", slow)
    monkeypatch.setattr(scraper, "search_research_alternative", lambda group, query: [])
    monkeypatch.setattr(youtube, "find_research_videos", lambda query: [video])
    monkeypatch.setattr(youtube, "read_research_video_captions", slow)

    try:
        research = scraper.conduct_autonomous_research("ESP32")
        assert research["success"]
        assert research["sources"]["youtube"] == [video]
        assert research["source_status"]["youtube_captions"] == "timeout"
        assert research["source_status"]["academic"] == "timeout"
        assert research["source_status"]["web"] == "error"
    finally:
        release.set()


def test_continuation_opens_new_articles_and_does_not_fetch_existing_captions(monkeypatch):
    known_page = {"title": "Panduan lama", "url": "https://example.com/old"}
    new_page = {"title": "Panduan baru", "url": "https://example.com/new"}
    known_video = {"title": "Video lama", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}
    monkeypatch.setattr(scraper, "search_web_sources", Mock(return_value=[known_page, new_page]))
    monkeypatch.setattr(scraper, "search_academic_papers_unified", Mock(return_value={"papers": []}))
    monkeypatch.setattr(scraper, "search_research_alternative", lambda group, query: {"papers": []})
    monkeypatch.setattr(youtube, "find_research_videos", Mock(return_value=[known_video]))
    web_read = Mock(return_value={"success": True, "content": "Cuplikan dari panduan yang baru."})
    captions = Mock()
    monkeypatch.setattr(scraper, "scrape_url", web_read)
    monkeypatch.setattr(youtube, "read_research_video_captions", captions)
    token = opened_research_urls.set(frozenset({known_page["url"], known_video["url"]}))
    try:
        result = scraper.conduct_autonomous_research("Panel surya")
    finally:
        opened_research_urls.reset(token)
    web_read.assert_called_once_with(new_page["url"], max_length=scraper.RESEARCH_READ_CHARS)
    captions.assert_not_called()
    assert result["sources"]["web"][0]["url"] == new_page["url"]


def test_auto_research_recovers_empty_lane_and_cites_the_article_that_was_read(monkeypatch):
    blocked = {"title": "Halaman dibatasi", "url": "https://example.com/blocked"}
    readable = {"title": "Panduan resmi ESP32", "url": "https://example.com/guide"}
    paper = {"title": "Pengujian ESP32", "url": "https://example.com/study"}
    alternate = Mock(return_value={"papers": [paper]})
    def read(url, **kwargs):
        return {"success": True, "content": "Isi panduan ESP32."} if url == readable["url"] else {"success": False}
    monkeypatch.setattr(scraper, "search_web_sources", lambda *args, **kwargs: [blocked, readable])
    monkeypatch.setattr(scraper, "search_academic_papers_unified", lambda *args, **kwargs: {"papers": []})
    monkeypatch.setattr(scraper, "search_research_alternative", alternate)
    monkeypatch.setattr(scraper, "scrape_url", read)
    monkeypatch.setattr(youtube, "find_research_videos", lambda query: [
        {"title": "Video lama", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
    ])

    token = opened_research_urls.set(frozenset({"https://www.youtube.com/watch?v=aaaaaaaaaaa"}))
    try:
        research = scraper.conduct_autonomous_research("ESP32")
    finally:
        opened_research_urls.reset(token)
    assert research["sources"]["web"] == [blocked, {**readable, "excerpt": "Isi panduan ESP32."}]
    assert research["sources"]["academic"] == [paper]
    assert research["source_status"]["academic_alternative"] == "found"
    alternate.assert_called_once_with("academic", "ESP32")


def test_auto_research_reads_article_pdf_and_multiple_videos_as_source_content(monkeypatch):
    articles = [
        {"title": f"Artikel {index}", "url": f"https://example.com/article-{index}"}
        for index in (1, 2)
    ]
    paper = {"title": "Jurnal pengukuran", "url": "https://example.com/journal",
             "pdf_url": "https://example.com/full.pdf", "abstract": "Teks abstrak awal."}
    videos = [
        {"title": f"Video {index}", "url": f"https://www.youtube.com/watch?v={'a' * 10}{index}"}
        for index in (1, 2)
    ]
    monkeypatch.setattr(scraper, "search_web_sources", lambda *args, **kwargs: articles)
    monkeypatch.setattr(scraper, "search_academic_papers_unified", lambda *args, **kwargs: {"papers": [paper]})
    monkeypatch.setattr(youtube, "find_research_videos", lambda query: videos)
    monkeypatch.setattr(youtube, "read_research_video_captions", lambda item, **kwargs: (
        {**item, "transcript_excerpt": "[01:00] Video menjelaskan batas pengukuran."}, None,
    ))

    def read(url, **kwargs):
        if url == paper["pdf_url"]:
            return {"success": True, "source_type": "pdf_document",
                    "content": "### Halaman 5\n\nHasil pengukuran menunjukkan kenaikan lima volt."}
        return {"success": True, "content": f"Teks dari {url}."}

    monkeypatch.setattr(scraper, "scrape_url", read)
    groups = scraper.conduct_autonomous_research("Pengukuran panel surya")["sources"]
    assert all(item["excerpt"] == f"Teks dari {item['url']}." for item in groups["web"])
    assert groups["academic"][0]["url"] == paper["url"]
    assert groups["academic"][0]["excerpt_source_type"] == "pdf_document"
    assert "Halaman 5" in groups["academic"][0]["excerpt"]
    assert all(item["transcript_excerpt"] and item["subtitle_status"] == "available" for item in groups["youtube"])
