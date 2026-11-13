from datetime import datetime
from unittest.mock import Mock

from xiaozhi.services import news_service as news


def test_today_filters_in_wib_and_counts_distinct_articles(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return cls(2026, 11, 13, 8, tzinfo=tz)

    def item(title, source, domain, date):
        return {
            "title": title, "source": source, "publisher_domain": domain,
            "url": f"https://{domain}/article", "published_at": news._published(date),
            "snippet": "",
        }

    today_a = item("Berita A", "ANTARA", "antaranews.com", "Thu, 12 Nov 2026 17:30:00 GMT")
    today_b = item("Berita B", "CNBC Indonesia", "cnbcindonesia.com", "Thu, 12 Nov 2026 19:30:00 GMT")
    yesterday = item("Berita C", "ANTARA", "antaranews.com", "Thu, 12 Nov 2026 16:30:00 GMT")
    feeds = {"ANTARA": [yesterday, today_a], "CNBC Indonesia": [today_b]}
    monkeypatch.setattr(news, "datetime", Clock)
    monkeypatch.setattr(news, "_feed_items", lambda url, publisher="", domain="", params=None: feeds.get(publisher, []))
    monkeypatch.setattr(news, "_read_article", lambda entry: {"article_excerpt": "Cuplikan " + entry["title"]})
    result = news.search_latest_news("berita terbaru hari ini")
    assert result["period"] == "hari_ini"
    assert result["articles_read"] == 2
    assert [entry["title"] for entry in result["results"]] == ["Berita B", "Berita A"]
    assert all(entry["date"].startswith("2026-11-13") for entry in result["results"])


def test_empty_news_and_redirect_away_from_publisher(monkeypatch):
    monkeypatch.setattr(news, "_feed_items", lambda *args, **kwargs: [])
    assert not news.search_latest_news("topik yang tidak ada")["success"]
    fetch = Mock(return_value=(None, "https://example.test/article"))
    monkeypatch.setattr(news, "_fetch", fetch)
    assert news._read_article({"url": "https://www.antaranews.com/article", "publisher_domain": "antaranews.com"}) == {}
    fetch.assert_called_once()
