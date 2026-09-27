import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from xiaozhi.core.utils import extract_youtube_video_id
from xiaozhi.dependencies import get_current_user, get_store, render, require_user

logger = logging.getLogger("xiaozhi.playlist")
router = APIRouter()


def _fetch_youtube_title_oembed(video_url: str) -> Optional[str]:
    """Fetch video title using YouTube public oEmbed endpoint (no API key needed)."""
    try:
        oembed_url = f"https://www.youtube.com/oembed?url={video_url}&format=json"
        req = UrlRequest(oembed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urlopen(req, timeout=4) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return data.get("title")
    except Exception:
        pass
    return None


@router.get("/playlist", response_class=HTMLResponse)
async def playlist_page(request: Request):
    user = require_user(request)
    store = get_store()
    tracks = store.get_user_playlist(user["id"])
    top_tracks = store.get_top_played_playlist(user["id"], limit=5)
    total_plays = sum(int(t.get("play_count", 0)) for t in tracks)

    return render(
        request,
        "playlist.html",
        {
            "user": user,
            "page": "playlist",
            "active_page": "playlist",
            "tracks": tracks,
            "top_tracks": top_tracks,
            "total_tracks": len(tracks),
            "total_plays": total_plays,
        },
    )


@router.get("/api/v1/playlist")
async def api_get_playlist(request: Request):
    user = require_user(request)
    store = get_store()
    tracks = store.get_user_playlist(user["id"])
    top_tracks = store.get_top_played_playlist(user["id"], limit=5)
    return {
        "success": True,
        "total": len(tracks),
        "total_plays": sum(int(t.get("play_count", 0)) for t in tracks),
        "tracks": tracks,
        "top_tracks": top_tracks,
    }


@router.post("/api/v1/playlist")
async def api_add_playlist_track(request: Request):
    user = require_user(request)
    store = get_store()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Data JSON tidak valid.")

    raw_url = str(body.get("youtube_url") or "").strip()
    title = str(body.get("title") or "").strip()
    artist = str(body.get("artist") or "").strip()

    if not raw_url:
        raise HTTPException(status_code=400, detail="Link YouTube atau Video ID wajib diisi.")

    video_id = extract_youtube_video_id(raw_url)
    if not video_id:
        raise HTTPException(
            status_code=400,
            detail="Format link YouTube tidak valid. Gunakan format seperti: https://www.youtube.com/watch?v=... atau https://youtu.be/...",
        )

    # Standardize full YouTube URL
    full_url = f"https://www.youtube.com/watch?v={video_id}"

    # Auto-fetch title if left blank
    if not title:
        fetched_title = _fetch_youtube_title_oembed(full_url)
        title = fetched_title or f"YouTube Video ({video_id})"

    try:
        track = store.add_playlist_track(
            owner_id=user["id"],
            title=title,
            youtube_url=full_url,
            video_id=video_id,
            artist=artist,
        )
        return {
            "success": True,
            "message": f"Lagu '{title}' berhasil ditambahkan ke Playlist (Nomor #{track.get('track_number')}).",
            "track": track,
        }
    except Exception as exc:
        logger.exception("Error adding playlist track: %s", exc)
        raise HTTPException(status_code=500, detail=f"Gagal menambahkan lagu: {exc}")


@router.put("/api/v1/playlist/{track_id}")
async def api_update_playlist_track(track_id: int, request: Request):
    user = require_user(request)
    store = get_store()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Data JSON tidak valid.")

    existing = store.get_playlist_track(user["id"], track_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Lagu tidak ditemukan di playlist Anda.")

    raw_url = body.get("youtube_url")
    title = body.get("title")
    artist = body.get("artist")
    video_id = None

    if raw_url is not None:
        raw_url = str(raw_url).strip()
        vid = extract_youtube_video_id(raw_url)
        if not vid:
            raise HTTPException(status_code=400, detail="Format link YouTube tidak valid.")
        video_id = vid
        raw_url = f"https://www.youtube.com/watch?v={vid}"

    updated = store.update_playlist_track(
        owner_id=user["id"],
        track_id=track_id,
        title=str(title).strip() if title is not None else None,
        youtube_url=raw_url,
        video_id=video_id,
        artist=str(artist).strip() if artist is not None else None,
    )
    return {
        "success": True,
        "message": "Data lagu playlist berhasil diperbarui.",
        "track": updated,
    }


@router.delete("/api/v1/playlist/{track_id}")
async def api_delete_playlist_track(track_id: int, request: Request):
    user = require_user(request)
    store = get_store()
    existing = store.get_playlist_track(user["id"], track_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Lagu tidak ditemukan di playlist Anda.")

    deleted = store.delete_playlist_track(user["id"], track_id)
    return {
        "success": deleted,
        "message": f"Lagu '{existing.get('title')}' berhasil dihapus dari playlist.",
    }


@router.post("/api/v1/playlist/{track_id}/play")
async def api_play_playlist_track(track_id: int, request: Request):
    user = require_user(request)
    store = get_store()
    track = store.get_playlist_track(user["id"], track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Lagu tidak ditemukan di playlist Anda.")

    # Increment play count
    updated_track = store.increment_playlist_play_count(user["id"], track_id=track_id) or track

    # Queue to ESP32 audio command
    owner_id = user["id"]
    vid = track.get("video_id", "")
    title = track.get("title", "")
    user_mac = store.get_user_mac_address(owner_id) if hasattr(store, "get_user_mac_address") else ""
    mac_param = f"&mac={user_mac}" if user_mac else ""
    stream_url = f"/api/audio/stream/{vid}?owner_id={owner_id}{mac_param}"
    base = os.getenv("SERVER_BASE_URL", "").rstrip("/")
    full_stream = f"{base}{stream_url}" if stream_url.startswith("/") else stream_url

    try:
        store.queue_audio_command(
            owner_id,
            title=title,
            stream_url=full_stream,
            video_url=track.get("youtube_url", f"https://www.youtube.com/watch?v={vid}"),
            duration=track.get("duration", ""),
            video_id=vid,
        )
    except Exception as exc:
        logger.warning("Failed to queue audio for ESP32: %s", exc)

    return {
        "success": True,
        "message": f"Perintah memutar #{track.get('track_number')} '{title}' telah dikirim ke perangkat speaker ESP32.",
        "stream_url": full_stream,
        "video_id": vid,
        "track": updated_track,
    }
