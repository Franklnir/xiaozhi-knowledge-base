"""Select an exact YouTube video before sending it to the browser player."""

import re
import time
from collections import OrderedDict
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import yt_dlp

from xiaozhi.services.youtube_research_service import search_youtube_candidates

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_PENDING = OrderedDict()
_LOCK = Lock()


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


def find_playback_choices(owner_id, slot, query):
    query = " ".join(query.split())
    if not query or len(query) > 300:
        return {"success": False, "message": "Masukkan judul, channel, atau tautan video YouTube."}
    try:
        video_id = _direct_video_id(query)
    except ValueError:
        return {"success": False, "message": "Gunakan tautan ke satu video YouTube yang valid."}

    try:
        if video_id:
            options = {"quiet": True, "no_warnings": True, "noplaylist": True,
                       "socket_timeout": 6, "retries": 0, "ignore_no_formats_error": True}
            with yt_dlp.YoutubeDL(options) as ydl:
                entry = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            entries = [entry] if entry and entry.get("id") == video_id else []
        else:
            entries, _ = search_youtube_candidates(query, False, None, None, 6)
    except Exception:
        return {"success": False, "message": "Metadata video YouTube sedang tidak bisa diperiksa."}

    choices = {}
    for entry in entries:
        if not entry or not _VIDEO_ID.fullmatch(str(entry.get("id") or "")) or not entry.get("title"):
            continue
        vid = entry["id"]
        choices.setdefault(vid, {
            "video_id": vid,
            "title": str(entry["title"])[:200],
            "channel": str(entry.get("channel") or entry.get("uploader") or "")[:120],
            "duration_seconds": entry.get("duration"),
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
        if len(choices) == 3:
            break
    if not choices:
        return {"success": False, "message": "Tidak ada video yang cocok. Coba sertakan judul dan channel."}

    request_id, candidates = remember_playback_choices(owner_id, slot, choices.values())
    return {"success": True, "request_id": request_id, "needs_confirmation": True, "candidates": candidates}


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
