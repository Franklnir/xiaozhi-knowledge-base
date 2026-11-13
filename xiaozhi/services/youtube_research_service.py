"""Read-only YouTube search and public captions for source-based answers."""

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from itertools import islice

import requests
import yt_dlp
from youtube_transcript_api import NoTranscriptFound, YouTubeTranscriptApi

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_CAPTION_BUDGET = 3200


class _CaptionSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (4, 6))
        return super().request(method, url, **kwargs)


def _upload_date(entry):
    raw = entry.get("upload_date")
    if raw:
        try:
            return datetime.strptime(raw, "%Y%m%d").date()
        except (TypeError, ValueError):
            pass
    return None


def search_youtube_candidates(query, newest, start, end, count):
    api_key = os.getenv("YOUTUBE_DATA_API_KEY", "").strip()
    if api_key:
        params = {"part": "snippet", "type": "video", "q": query, "maxResults": count,
                  "order": "date" if newest else "relevance"}
        if start:
            params["publishedAfter"] = f"{start}T00:00:00Z"
        if end:
            params["publishedBefore"] = f"{end}T23:59:59.999Z"
        try:
            response = requests.get("https://www.googleapis.com/youtube/v3/search", params=params,
                                    headers={"X-Goog-Api-Key": api_key}, timeout=(4, 6))
            response.raise_for_status()
            return [
                {
                    "id": item["id"]["videoId"],
                    "title": item["snippet"].get("title"),
                    "channel": item["snippet"].get("channelTitle"),
                    "published_at": item["snippet"]["publishedAt"],
                    "upload_date": item["snippet"]["publishedAt"][:10].replace("-", ""),
                }
                for item in response.json().get("items", [])
            ], "youtube_data_api"
        except (requests.RequestException, KeyError, ValueError, TypeError):
            pass
    terms = [query]
    if start and start > date.min:
        terms.append(f"after:{(start - timedelta(days=1)).isoformat()}")
    if end and end < date.max:
        terms.append(f"before:{(end + timedelta(days=1)).isoformat()}")
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "socket_timeout": 6,
        "retries": 0,
        "extractor_retries": 0,
        "playlistend": count,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        result = downloader.extract_info(f"ytsearch{count}:{' '.join(terms)}", download=False)
        return list(islice((result or {}).get("entries") or (), count)), "youtube_search_sample"


def _source(entry, start, end, newest):
    video_id = entry["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    uploaded = _upload_date(entry)
    if (newest or start or end) and not uploaded:
        options = {"quiet": True, "no_warnings": True, "socket_timeout": 5, "retries": 0,
                   "extractor_retries": 0, "ignore_no_formats_error": True}
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                metadata = downloader.extract_info(url, download=False)
            uploaded = _upload_date(metadata or {})
        except Exception:
            pass
    if (start or end) and not uploaded:
        return None, "tanggal_tidak_terverifikasi"
    if uploaded and ((start and uploaded < start) or (end and uploaded > end)):
        return None, "di_luar_periode"

    try:
        with _CaptionSession() as session:
            api = YouTubeTranscriptApi(http_client=session)
            tracks = api.list(video_id)
            try:
                track = tracks.find_transcript(["id", "en"])
            except NoTranscriptFound:
                track = next(iter(tracks), None)
            if track is None:
                return None, "subtitle_tidak_tersedia"
            captions = track.fetch()
            language = captions.language_code
            paragraphs = []
            last_mark = -30
            used = 0
            previous_text = ""
            truncated = False
            for segment in captions:
                text = " ".join(segment.text.split())
                if not text or text == previous_text:
                    continue
                mark = int(segment.start)
                if mark >= last_mark + 30:
                    minutes, seconds = divmod(mark, 60)
                    piece = f"\n\n[{minutes:02d}:{seconds:02d}] {text}"
                    last_mark = mark
                else:
                    piece = f" {text}"
                if used + len(piece) > _CAPTION_BUDGET:
                    truncated = True
                    break
                paragraphs.append(piece)
                used += len(piece)
                previous_text = text
    except Exception:
        return None, "subtitle_gagal_diambil"

    if not paragraphs:
        return None, "subtitle_tidak_tersedia"
    return {
        "video_id": video_id,
        "title": str(entry.get("title") or f"YouTube {video_id}")[:200],
        "channel": str(entry.get("channel") or entry.get("uploader") or "")[:120],
        "url": url,
        "published_date_utc": uploaded.isoformat() if uploaded else None,
        "subtitle_language": language,
        "transcript_excerpt": "".join(paragraphs).strip(),
        "transcript_truncated": truncated,
    }, None


def research_youtube(query: str, newest: bool = True, start_date: str = "", end_date: str = "", limit: int = 3) -> dict:
    query = " ".join(query.split())
    if not query or len(query) > 300:
        return {"success": False, "message": "Masukkan topik YouTube sepanjang 1–300 karakter.", "sources": []}
    try:
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        return {"success": False, "message": "Gunakan tanggal YYYY-MM-DD.", "sources": []}
    if start and end and start > end:
        return {"success": False, "message": "Tanggal mulai harus sebelum atau sama dengan tanggal akhir.", "sources": []}

    limit = max(2, min(limit, 4))
    count = limit + 5 if newest else limit + 2
    prefer_recent = newest and start is None and end is None
    recent_start = datetime.now(timezone.utc).date() - timedelta(days=7) if prefer_recent else start
    try:
        entries, search_method = search_youtube_candidates(query, newest, recent_start, end, count)
        if prefer_recent and not entries:
            entries, search_method = search_youtube_candidates(query, newest, None, None, count)
    except Exception:
        return {"success": False, "message": "Pencarian YouTube sedang tidak tersedia.", "sources": []}
    candidates = {}
    for entry in entries:
        if entry and _VIDEO_ID.fullmatch(str(entry.get("id") or "")):
            candidates.setdefault(entry["id"], entry)

    sources, skipped = [], []
    with ThreadPoolExecutor(max_workers=3) as executor:
        for source, reason in executor.map(lambda entry: _source(entry, start, end, newest), candidates.values()):
            if source is not None:
                sources.append(source)
            else:
                skipped.append(reason)

    if newest:
        sources.sort(key=lambda source: source["published_date_utc"] or "", reverse=True)
    recent_found = sum(
        source["published_date_utc"] is not None and source["published_date_utc"] >= recent_start.isoformat()
        for source in sources
    ) if prefer_recent else None
    sources = sources[:limit]
    return {
        "success": bool(sources),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "search_method": search_method,
        "coverage_note": (
            "Hasil mengikuti indeks pencarian YouTube."
            if search_method == "youtube_data_api"
            else "Urutan terbaru dihitung dari kandidat yang ditemukan dan tanggal yang bisa dibaca."
        ),
        "freshness_note": (
            "Tidak ditemukan sumber bersubtitle dengan tanggal terverifikasi dalam tujuh hari terakhir. "
            "Jika ada hasil yang lebih lama atau tanpa tanggal, sebutkan keterbatasan itu."
            if recent_found == 0 else None
        ),
        "sources": sources,
        "skipped": skipped,
        "multiple_sources": len(sources) >= 2,
        "message": (
            "Cuplikan subtitle tersedia." if len(sources) >= 2
            else "Hanya satu video memiliki cuplikan yang dapat digunakan." if sources
            else "Tidak ada cuplikan subtitle yang bisa digunakan dalam hasil pencarian ini."
        ),
    }
