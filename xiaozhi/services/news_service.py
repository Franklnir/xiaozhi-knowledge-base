"""Fetch fresh headlines and read articles from supported publisher feeds."""

import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup


WIB = timezone(timedelta(hours=7))
GOOGLE_NEWS = "https://news.google.com/rss/search"
PUBLISHER_FEEDS = {
    "ANTARA": ("https://www.antaranews.com/rss/terkini.xml", "antaranews.com"),
    "CNBC Indonesia": ("https://www.cnbcindonesia.com/rss", "cnbcindonesia.com"),
    "Republika": ("https://www.republika.co.id/rss", "republika.co.id"),
    "BBC News Indonesia": ("https://www.bbc.com/indonesia/index.xml", "bbc.com"),
}
PUBLISHER_SUBDOMAINS = {
    "republika.co.id": {"news.republika.co.id", "ameera.republika.co.id", "ekonomi.republika.co.id"},
}
HEADERS = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
PERIODS = {"hari_ini", "24_jam", "7_hari", "semua"}
_GENERIC_WORDS = {"berita", "terbaru", "terkini", "terupdate", "update", "hari", "ini", "saat",
                  "sekarang", "realtime", "indonesia", "cari", "carikan", "apa", "saja", "di", "dari"}


def _fetch(url, limit, params=None):
    """Read a bounded response without following redirects."""
    deadline = time.monotonic() + 8
    with requests.get(url, params=params, headers=HEADERS, timeout=(3, 4),
                      allow_redirects=False, stream=True) as response:
        if response.is_redirect:
            return None, response.headers.get("Location")
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_content(chunk_size=16384):
            body.extend(chunk)
            if len(body) > limit or time.monotonic() > deadline:
                raise ValueError("Response limit exceeded")
        return bytes(body), None


def _published(value):
    try:
        value = parsedate_to_datetime(value or "")
        return value.astimezone(WIB) if value.tzinfo else value.replace(tzinfo=timezone.utc).astimezone(WIB)
    except (TypeError, ValueError, OverflowError):
        return None


def _publisher_url(url, domain):
    try:
        target = urlsplit(url)
        hosts = {domain, f"www.{domain}"} | PUBLISHER_SUBDOMAINS.get(domain, set())
        return (target.scheme == "https" and target.hostname in hosts
                and target.port in (None, 443) and target.username is None
                and target.path not in ("", "/"))
    except ValueError:
        return False


def _feed_items(url, publisher="", domain="", params=None):
    try:
        body, _ = _fetch(url, 1_500_000, params)
        if not body or b"\0" in body or b"<!DOCTYPE" in body.upper():
            return []
        root = ElementTree.fromstring(body)
    except (requests.RequestException, ValueError, ElementTree.ParseError):
        return []
    items = []
    for node in root.findall("./channel/item")[:100]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if domain and link.startswith("http://"):
            link = "https://" + link[len("http://"):]
        if not title or not link.startswith("https://") or (domain and not _publisher_url(link, domain)):
            continue
        published = _published(node.findtext("pubDate"))
        description = BeautifulSoup(node.findtext("description") or "", "html.parser").get_text(" ", strip=True)
        items.append({
            "title": title[:300], "url": link, "source": publisher or node.findtext("source", ""),
            "published_at": published, "snippet": description[:300], "publisher_domain": domain,
        })
    return items


def _read_article(item):
    url = item["url"]
    domain = item["publisher_domain"]
    try:
        for _ in range(3):
            if not _publisher_url(url, domain):
                return {}
            body, redirect = _fetch(url, 2_000_000)
            if not redirect:
                break
            url = urljoin(url, redirect)
        else:
            return {}
    except (requests.RequestException, ValueError):
        return {}
    if not body:
        return {}

    page = BeautifulSoup(body, "html.parser")
    article = page.select_one(".post-content, .detail-text, article") or page.select_one("main")
    if not article:
        return {}
    for node in article.select("aside, nav, footer, script, style, .related"):
        node.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in article.find_all("p")]
    text = "\n\n".join(p for p in paragraphs if len(p.split()) >= 8)
    if len(text.split()) < 30:
        return {}
    meta = page.find("meta", property="article:published_time")
    return {"url": url, "article_excerpt": text[:2400],
            "article_published_at": meta.get("content", "")[:45] if meta else None}


def search_latest_news(query: str, limit: int = 5, period: str = "otomatis") -> dict:
    now = datetime.now(WIB)
    query = " ".join(query.split())
    if period == "otomatis":
        period = "hari_ini" if re.search(r"\b(hari ini|saat ini|sekarang)\b", query, re.I) else "24_jam"
    if period not in PERIODS:
        return {"success": False, "message": "Pilih hari_ini, 24_jam, 7_hari, atau semua.", "results": []}
    limit = max(1, min(limit, 10))

    start = {
        "hari_ini": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "24_jam": now - timedelta(hours=24), "7_hari": now - timedelta(days=7),
        "semua": datetime.min.replace(tzinfo=WIB),
    }[period]
    terms = set(re.findall(r"\w+", query.lower())) - _GENERIC_WORDS
    topic = re.sub(r"\b(hari ini|saat ini|sekarang|terbaru|terkini|realtime)\b", "", query, flags=re.I).strip()
    google_query = topic if terms else "berita Indonesia"
    if period != "semua":
        google_query += " when:7d" if period == "7_hari" else " when:1d"
    params = {"q": google_query, "hl": "id", "gl": "ID", "ceid": "ID:id"}

    with ThreadPoolExecutor(max_workers=5) as pool:
        google = pool.submit(_feed_items, GOOGLE_NEWS, params=params)
        feeds = [pool.submit(_feed_items, url, publisher, domain)
                 for publisher, (url, domain) in PUBLISHER_FEEDS.items()]
        direct = [item for feed in feeds for item in feed.result()]
        headlines = google.result()

    def within_period(item):
        published = item["published_at"]
        return published is not None and start <= published <= now

    direct = [
        item for item in direct if within_period(item) and terms.issubset(
            set(re.findall(r"\w+", (item["title"] + " " + item["snippet"]).lower()))
        )
    ]
    direct.sort(key=lambda item: item["published_at"], reverse=True)
    # Try one recent article per publisher. Read at most three distinct publishers.
    by_publisher = {}
    for item in direct:
        by_publisher.setdefault(item["publisher_domain"], item)
    to_read = list(by_publisher.values())[:min(3, limit)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for item, article in zip(to_read, pool.map(_read_article, to_read)):
            item.update(article)

    read_count = sum(bool(item.get("article_excerpt")) for item in to_read)
    selected = [item for item in to_read if item.get("article_excerpt")]
    def title_key(item):
        title = item["title"]
        if item["source"] and title.casefold().endswith(f" - {item['source'].casefold()}"):
            title = title[:-(len(item["source"]) + 3)]
        return " ".join(re.findall(r"\w+", title.casefold()))

    seen = {title_key(item) for item in selected}
    ordered = sorted(direct + [item for item in headlines if within_period(item)],
                     key=lambda item: item["published_at"], reverse=True)
    for item in ordered:
        if len(selected) >= limit:
            break
        key = title_key(item)
        if key not in seen:
            selected.append(item)
            seen.add(key)
    results = []
    for item in sorted(selected, key=lambda item: item["published_at"], reverse=True):
        results.append({
            **{k: v for k, v in item.items() if k not in ("publisher_domain", "published_at")},
            "date": item["published_at"].isoformat(),
            "content_level": "article_excerpt" if item.get("article_excerpt") else "headline_only",
        })
    return {
        "success": bool(results),
        "checked_at": now.isoformat(),
        "period": period,
        "articles_read": read_count,
        "results": results,
        "message": (
            "Tidak ditemukan berita bertanggal dalam periode ini." if not results
            else "Hanya judul dan cuplikan feed yang tersedia." if read_count == 0
            else "Hanya satu artikel berhasil dibaca." if read_count == 1
            else f"Cuplikan dari {read_count} artikel penerbit berbeda tersedia."
        ),
    }
