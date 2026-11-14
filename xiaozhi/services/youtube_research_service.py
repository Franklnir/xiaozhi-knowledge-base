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
    raw = entry.get("upload_date") or str(entry.get("published_at") or "")[:10].replace("-", "")
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
                    "channel_id": item["snippet"].get("channelId"),
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


def _source(entry, start, end, newest, *, today_wib=None):
    from xiaozhi.services.youtube_playback_service import _enrich, _publication_time

    video_id = entry["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    uploaded = _upload_date(entry)
    published_at = _publication_time(entry)
    if ((newest or start or end) and not (uploaded or published_at)) or (today_wib and not published_at):
        entry = _enrich(entry)
        published_at = _publication_time(entry)
        uploaded = _upload_date(entry)
    if published_at:
        uploaded = published_at.astimezone(timezone.utc).date()
    if today_wib and not published_at:
        return None, "tanggal_tidak_terverifikasi"
    if today_wib and published_at.astimezone(timezone(timedelta(hours=7))).date() != today_wib:
        return None, "di_luar_periode"
    if (start or end) and not uploaded:
        return None, "tanggal_tidak_terverifikasi"
    if uploaded and ((start and uploaded < start) or (end and uploaded > end)):
        return None, "di_luar_periode"

    metadata = {
        "video_id": video_id,
        "title": str(entry.get("title") or f"YouTube {video_id}")[:200],
        "channel": str(entry.get("channel") or entry.get("uploader") or "")[:120],
        "url": url,
        "published_date_utc": uploaded.isoformat() if uploaded else None,
        "published_at_wib": published_at.astimezone(timezone(timedelta(hours=7))).isoformat() if published_at else None,
    }
    try:
        with _CaptionSession() as session:
            api = YouTubeTranscriptApi(http_client=session)
            tracks = api.list(video_id)
            try:
                track = tracks.find_transcript(["id", "en"])
            except NoTranscriptFound:
                track = next(iter(tracks), None)
            if track is None:
                return metadata, "subtitle_tidak_tersedia"
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
        return metadata, "subtitle_gagal_diambil"

    if not paragraphs:
        return metadata, "subtitle_tidak_tersedia"
    return {
        **metadata,
        "subtitle_language": language,
        "transcript_excerpt": "".join(paragraphs).strip(),
        "transcript_truncated": truncated,
    }, None


def research_youtube(query: str, newest: bool = True, start_date: str = "", end_date: str = "", limit: int = 3,
                     *, channel: str = "") -> dict:
    query = " ".join(query.split())
    channel = " ".join(channel.split())
    if not (query or channel) or len(query) > 300 or len(channel) > 120:
        return {"success": False, "message": "Masukkan topik, channel, atau tautan video YouTube.", "sources": []}
    try:
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        return {"success": False, "message": "Gunakan tanggal YYYY-MM-DD.", "sources": []}
    if start and end and start > end:
        return {"success": False, "message": "Tanggal mulai harus sebelum atau sama dengan tanggal akhir.", "sources": []}

    from xiaozhi.services.youtube_playback_service import (
        _direct_video_id, _match_title, _matches_channel, _video_metadata, _words,
        channel_confirmation, parse_video_search, search_channel_videos,
    )
    try:
        direct_id = _direct_video_id(query)
    except ValueError:
        return {"success": False, "message": "Gunakan tautan ke satu video YouTube yang valid.", "sources": []}
    if not direct_id:
        today_wib = (
            datetime.now(timezone(timedelta(hours=7))).date()
            if not (start or end) and re.search(r"\bhari\s+ini\b", query, re.I) else None
        )
        query, channel = parse_video_search(query, channel)
        if not (query or channel):
            return {"success": False, "message": "Sebutkan topik video atau channel yang ingin dicari.", "sources": []}
    else:
        today_wib = None
    limit = max(2, min(limit, 4))
    count = limit + 5 if newest else limit + 2
    prefer_recent = newest and start is None and end is None and not today_wib and not direct_id
    recent_start = datetime.now(timezone.utc).date() - timedelta(days=7) if prefer_recent else start
    if today_wib:
        recent_start = today_wib - timedelta(days=1)
    try:
        if direct_id:
            entries, search_method = [_video_metadata(direct_id)], "direct_video"
        elif channel:
            entries, search_method, channel, suggestions = search_channel_videos(
                query, channel, newest, recent_start if today_wib else start, end, count, search=search_youtube_candidates,
                use_uploads=not (start or end),
            )
            if suggestions:
                return {**channel_confirmation(suggestions), "sources": []}
            topic = _words(query)
            entries = [entry for entry in entries if entry and _matches_channel(entry, channel)
                       and _match_title(topic, entry.get("title"))]
            if not entries:
                return {"success": False, "sources": [], "channel": channel,
                        "message": "Belum ditemukan video yang cocok dari channel ini. Tanyakan sebelum mencari di channel lain."}
        else:
            entries, search_method = search_youtube_candidates(query, newest, recent_start, end, count)
            if prefer_recent and not entries:
                entries, search_method = search_youtube_candidates(query, newest, None, None, count)
    except Exception:
        return {"success": False, "message": "Pencarian YouTube sedang tidak tersedia.", "sources": []}
    candidates = {}
    for entry in entries:
        if entry and _VIDEO_ID.fullmatch(str(entry.get("id") or "")):
            candidates.setdefault(entry["id"], entry)

    sources, videos_without_subtitles, skipped = [], [], []
    source_options = {"today_wib": today_wib} if today_wib else {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        for source, reason in executor.map(
            lambda entry: _source(entry, start, end, newest, **source_options), candidates.values()
        ):
            if reason:
                skipped.append(reason)
                if source is not None:
                    videos_without_subtitles.append({**source, "subtitle_status": reason})
            elif source is not None:
                sources.append(source)

    if newest:
        for group in (sources, videos_without_subtitles):
            group.sort(key=lambda source: (source["published_date_utc"] or "", source.get("published_at_wib") or ""),
                       reverse=True)
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
        "videos_without_subtitles": videos_without_subtitles[:limit],
        "skipped": skipped,
        "multiple_sources": len(sources) >= 2,
        "message": (
            "Cuplikan subtitle tersedia." if len(sources) >= 2
            else "Hanya satu video memiliki cuplikan yang dapat digunakan." if sources
            else "Video ditemukan. Cuplikan subtitle tidak tersedia untuk merangkum isinya." if videos_without_subtitles
            else "Tidak ada cuplikan subtitle yang bisa digunakan dalam hasil pencarian ini."
        ),
    }
