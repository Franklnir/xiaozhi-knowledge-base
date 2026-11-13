"""Select an exact YouTube video before queuing its audio."""

import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from itertools import islice
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import yt_dlp

from xiaozhi.services.youtube_research_service import search_youtube_candidates

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_PENDING = OrderedDict()
_LOCK = Lock()
_RECENT_WORDS = r"\b(?:paling\s+baru|terbaru|terkini|hari\s+ini|barusan|baru\s+rilis)\b"
_RELEASE_PREFIX = re.compile(
    rf"^(?:rilis\s+(?:video\s+(?:youtube\s+)?)?|(?:video(?:\s+youtube)?|upload|unggahan)\s+)"
    rf"(?:{_RECENT_WORDS})(?:\s+(?:tentang\s+)?)?", re.I,
)
_CHANNEL_HANDLE = re.compile(r"@[A-Za-z0-9_.-]{3,30}")
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_METADATA_OPTIONS = {"quiet": True, "no_warnings": True, "noplaylist": True,
                     "socket_timeout": 5, "retries": 0, "extractor_retries": 0,
                     "ignore_no_formats_error": True}


def _direct_video_id(query):
    if _VIDEO_ID.fullmatch(query):
        return query
    if not query.startswith(("http://", "https://")):
        return None
    url = urlsplit(query)
    if url.hostname == "youtu.be":
        candidate = url.path.strip("/")
    elif url.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if url.path == "/watch":
            candidate = parse_qs(url.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([^/]+)/?", url.path)
            candidate = match[1] if match else ""
    else:
        candidate = ""
    if not _VIDEO_ID.fullmatch(candidate):
        raise ValueError("Tautan harus menuju satu video YouTube.")
    return candidate


def _words(value):
    normalized = re.sub(r"\bxiao[\s-]+zhi\b", "xiaozhi", str(value or "").casefold())
    return " ".join(re.findall(r"[^\W_]+", normalized))


def _match_title(query, title):
    target = _words(str(title or "")[:300])
    if not query:
        return 1.0
    if query == target:
        return 3.0
    if f" {query} " in f" {target} ":
        return 2.0
    overlap = len(set(query.split()) & set(target.split())) / len(set(query.split()))
    similarity = SequenceMatcher(None, query, target, autojunk=False).ratio()
    if overlap >= 0.6 or similarity >= 0.78:
        return max(overlap, similarity)
    return 0.0


def _matches_channel(entry, channel):
    if not channel:
        return True
    names = {entry.get("channel"), entry.get("uploader"), entry.get("uploader_id"), entry.get("channel_id")}
    if channel.startswith("@") or _CHANNEL_ID.fullmatch(channel):
        return any(str(name or "").casefold() == channel.casefold() for name in names)
    return _words(channel) in {_words(name) for name in names if name}


def _upload_day(entry):
    try:
        return datetime.strptime(entry.get("upload_date") or "", "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _publication_time(entry):
    try:
        if entry.get("published_at"):
            published = datetime.fromisoformat(entry["published_at"].replace("Z", "+00:00"))
            return published if published.tzinfo else None
        if entry.get("timestamp"):
            return datetime.fromtimestamp(entry["timestamp"], timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return None


def _video_metadata(video_id):
    with yt_dlp.YoutubeDL(_METADATA_OPTIONS.copy()) as ydl:
        entry = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    return entry if entry and entry.get("id") == video_id else None


def _enrich(entry):
    try:
        return {**entry, **{key: value for key, value in (_video_metadata(entry["id"]) or {}).items()
                           if value is not None}}
    except Exception:
        return entry


def _channel_uploads(identity):
    path = identity if _CHANNEL_HANDLE.fullmatch(identity) else f"channel/{identity}"
    options = {**_METADATA_OPTIONS, "noplaylist": False, "extract_flat": True, "playlistend": 12}
    with yt_dlp.YoutubeDL(options) as downloader:
        result = downloader.extract_info(f"https://www.youtube.com/{path}/videos", download=False) or {}
        return [
            {**entry, "channel": entry.get("channel") or result.get("channel") or result.get("uploader"),
             "uploader_id": result.get("uploader_id") or entry.get("uploader_id"),
             "channel_id": result.get("channel_id") or entry.get("channel_id")}
            for entry in islice(result.get("entries") or (), 12)
            if entry
        ]


def find_playback_choices(owner_id, slot, query, *, channel="", newest=False):
    query = " ".join(query.split())
    channel = " ".join(channel.split())
    if not (query or channel) or len(query) > 300 or len(channel) > 120:
        return {"success": False, "message": "Masukkan judul, channel, atau tautan video YouTube."}
    try:
        video_id = _direct_video_id(query)
    except ValueError:
        return {"success": False, "message": "Gunakan tautan ke satu video YouTube yang valid."}

    today_only = False
    if not video_id:
        today_only = bool(re.search(r"\bhari\s+ini\b", query, re.I))
        newest = newest or bool(re.search(_RECENT_WORDS, query, re.I))
        query = _RELEASE_PREFIX.sub("", query)
        query = re.sub(_RECENT_WORDS, " ", query, flags=re.I).strip()
        match = re.search(r"(?:^|\bdari\s+)(?:channel|kanal)\s+(.+)$", query, re.I)
        if match:
            channel = channel or match[1].strip()
            query = query[:match.start()].strip()
        query = re.sub(r"\bxiao[\s-]+zhi\b", "xiaozhi", query, flags=re.I)
    title_query = _words(query)
    search_query = " ".join(part for part in (query, channel) if part)
    if not search_query:
        return {"success": False, "message": "Sebutkan topik video atau channel yang ingin dicari."}

    method = "direct_video"
    wib = timezone(timedelta(hours=7))
    today = datetime.now(wib).date()
    try:
        if video_id:
            entry = _video_metadata(video_id)
            entries = [entry] if entry else []
        else:
            start = today - timedelta(days=1) if today_only else today - timedelta(days=7) if newest and not channel else None
            entries, method = [], "youtube_search_sample"
            if newest and (_CHANNEL_HANDLE.fullmatch(channel) or _CHANNEL_ID.fullmatch(channel)):
                try:
                    entries, method = _channel_uploads(channel), "youtube_channel_uploads"
                except Exception:
                    pass
            if not any(
                entry and _matches_channel(entry, channel) and _match_title(title_query, entry.get("title"))
                for entry in entries
            ):
                entries, method = search_youtube_candidates(search_query, newest, start, None, 12)
            if newest and channel and not title_query and method == "youtube_search_sample":
                identities = {
                    entry["channel_id"] for entry in entries
                    if entry and _matches_channel(entry, channel)
                    and _CHANNEL_ID.fullmatch(entry.get("channel_id") or "")
                }
                if len(identities) == 1:
                    try:
                        uploads = _channel_uploads(identities.pop())
                        if uploads:
                            entries, method = uploads, "youtube_channel_uploads"
                    except Exception:
                        pass
            if start and not today_only and not any(
                entry and _matches_channel(entry, channel) and _match_title(title_query, entry.get("title"))
                for entry in entries
            ):
                entries, method = search_youtube_candidates(search_query, newest, None, None, 12)
    except Exception:
        return {"success": False, "message": "Metadata video YouTube sedang tidak bisa diperiksa."}

    pool = {}
    for entry in entries:
        if not entry or not _VIDEO_ID.fullmatch(str(entry.get("id") or "")) or not entry.get("title"):
            continue
        if video_id or _match_title(title_query, entry["title"]):
            pool.setdefault(entry["id"], entry)
    if not video_id:
        needs_metadata = [entry for entry in pool.values()
                          if (today_only and not _publication_time(entry))
                          or (newest and not _upload_day(entry))
                          or (channel and not _matches_channel(entry, channel))][:8]
        if needs_metadata:
            with ThreadPoolExecutor(max_workers=4) as executor:
                for entry in executor.map(_enrich, needs_metadata):
                    pool[entry["id"]] = entry

    ranked = []
    for entry in pool.values():
        if not video_id and not _matches_channel(entry, channel):
            continue
        uploaded = _upload_day(entry)
        published_at = _publication_time(entry)
        if today_only:
            if not published_at or published_at.astimezone(wib).date() != today:
                continue
        if published_at:
            uploaded = published_at.astimezone(timezone.utc).date()
        score = _match_title(title_query, entry["title"]) if not video_id else 1.0
        if not score:
            continue
        vid = entry["id"]
        date_rank = (uploaded or date.min, published_at.timestamp() if published_at else 0)
        ranked.append((score, date_rank, {
            "video_id": vid,
            "title": str(entry["title"])[:200],
            "channel": str(entry.get("channel") or entry.get("uploader") or "")[:120],
            "duration_seconds": entry.get("duration"),
            "published_date_utc": uploaded.isoformat() if uploaded else None,
            "published_at_wib": published_at.astimezone(wib).isoformat() if published_at else None,
            "url": f"https://www.youtube.com/watch?v={vid}",
        }))
    if not ranked:
        return {"success": False, "message": "Tidak ada video yang cocok dengan judul, channel, atau tanggal yang diminta. Coba sebutkan nama channel persis atau kirim tautannya."}

    ranked.sort(key=lambda item: (item[1], item[0]) if newest else (item[0], item[1]), reverse=True)
    request_id, candidates = remember_playback_choices(owner_id, slot, (item[2] for item in ranked[:3]))
    result = {"success": True, "request_id": request_id, "needs_confirmation": True, "candidates": candidates}
    if newest and not video_id:
        if any(not item["published_date_utc"] for item in candidates):
            result["freshness_note"] = (
                "Diambil dari tab Video channel. Sebagian tanggal terbit belum terverifikasi."
                if method == "youtube_channel_uploads"
                else "Sebagian tanggal terbit belum terverifikasi. Hasil ini berasal dari sampel pencarian."
            )
        else:
            result["freshness_note"] = "Urutan tanggal berlaku untuk kandidat yang ditemukan."
        result["search_method"] = method
    return result


def remember_playback_choices(owner_id, slot, videos):
    """Keep the displayed source order for the next explicit playback request."""
    request_id = uuid4().hex
    candidates = [{"number": i, **video} for i, video in enumerate(videos, 1)]
    with _LOCK:
        _PENDING[owner_id, slot, request_id] = (time.monotonic() + 300, candidates)
        if len(_PENDING) > 256:
            _PENDING.popitem(last=False)
    return request_id, candidates


def take_playback_choice(owner_id, slot, request_id, choice):
    key = (owner_id, slot, request_id)
    with _LOCK:
        pending = _PENDING.get(key)
        if not pending:
            return None
        expires_at, candidates = pending
        if expires_at <= time.monotonic():
            del _PENDING[key]
            return None
        if not 1 <= choice <= len(candidates):
            return None
        del _PENDING[key]
        return candidates[choice - 1]
