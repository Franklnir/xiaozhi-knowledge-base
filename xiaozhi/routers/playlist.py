import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from xiaozhi.core.utils import (
    detect_media_url_source,
    extract_youtube_video_id,
    parse_duration_to_seconds,
)
from xiaozhi.dependencies import get_current_user, get_store, render, require_user
from xiaozhi.services.audio_cache_worker import (
    check_media_duration_limit,
    delete_track_cache,
    enqueue_precache,
    get_all_queue_status,
    get_track_cache_status,
)

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


def _fetch_tiktok_metadata(video_url: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch video title and artist/creator from TikTok URL using yt-dlp."""
    try:
        from xiaozhi.core.utils import normalize_tiktok_url_for_ytdlp
        normalized_url = normalize_tiktok_url_for_ytdlp(video_url)
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 8,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=False)
            if info:
                title = info.get("title") or info.get("description")
                uploader = info.get("uploader") or info.get("creator") or info.get("channel") or ""
                return title, uploader
    except Exception as exc:
        logger.debug("Failed to fetch TikTok metadata via yt-dlp: %s", exc)
    return None, None



@router.get("/playlist", response_class=HTMLResponse)
async def playlist_page(request: Request):
    user = require_user(request)
    store = get_store()
    tracks = store.get_user_playlist(user["id"])
    top_tracks = store.get_top_played_playlist(user["id"], limit=5)
    total_plays = sum(int(t.get("play_count", 0)) for t in tracks)

    # Annotate tracks with audio cache status
    for t in tracks:
        c_info = get_track_cache_status(t.get("video_id", ""))
        t["cache_status"] = c_info.get("status", "uncached")
        t["is_cached"] = c_info.get("is_cached", False)
        t["cache_size"] = c_info.get("size_bytes", 0)
        t["cache_percent"] = c_info.get("percent", 100 if c_info.get("is_cached") else 0)

    plus_cfg = store.get_user_access_plus(user["id"]) if hasattr(store, "get_user_access_plus") else {}
    quota_enabled = plus_cfg.get("playlist_quota_enabled", False)
    max_tracks = plus_cfg.get("max_playlist_tracks", 15)
    active_tracks_count = sum(1 for t in tracks if t.get("is_active", True))
    disabled_tracks_count = len(tracks) - active_tracks_count

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
            "plus_cfg": plus_cfg,
            "quota_enabled": quota_enabled,
            "max_tracks": max_tracks,
            "active_tracks_count": active_tracks_count,
            "disabled_tracks_count": disabled_tracks_count,
        },
    )


@router.get("/api/v1/playlist")
async def api_get_playlist(
    request: Request,
    mac: str = Query("", description="ESP32 device MAC address"),
):
    user = get_current_user(request)
    store = get_store()
    owner_id = user["id"] if user else None

    if not owner_id:
        device_mac = mac or request.headers.get("X-Device-Mac", "") or request.headers.get("Device-Id", "")
        if device_mac:
            from xiaozhi.routers.youtube import _resolve_owner_for_device
            owner_id = _resolve_owner_for_device(store, device_mac)

    if not owner_id:
        raise HTTPException(status_code=401, detail="Login diperlukan atau perangkat tidak terdaftar.")

    tracks = store.get_user_playlist(owner_id)
    for t in tracks:
        c_info = get_track_cache_status(t.get("video_id", ""))
        t["cache_status"] = c_info.get("status", "uncached")
        t["is_cached"] = c_info.get("is_cached", False)
        t["cache_size"] = c_info.get("size_bytes", 0)
        t["cache_percent"] = c_info.get("percent", 100 if c_info.get("is_cached") else 0)

    top_tracks = store.get_top_played_playlist(owner_id, limit=5) if hasattr(store, "get_top_played_playlist") else []
    plus_cfg = store.get_user_access_plus(owner_id) if hasattr(store, "get_user_access_plus") else {}
    quota_enabled = plus_cfg.get("playlist_quota_enabled", False)
    max_tracks = plus_cfg.get("max_playlist_tracks", 15)
    active_tracks_count = sum(1 for t in tracks if t.get("is_active", True))
    return {
        "success": True,
        "owner_id": owner_id,
        "total": len(tracks),
        "total_plays": sum(int(t.get("play_count", 0)) for t in tracks),
        "active_tracks_count": active_tracks_count,
        "disabled_tracks_count": len(tracks) - active_tracks_count,
        "quota_enabled": quota_enabled,
        "max_tracks": max_tracks,
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

    plus_cfg = store.get_user_access_plus(user["id"]) if hasattr(store, "get_user_access_plus") else {}
    if plus_cfg.get("playlist_quota_enabled", False):
        max_limit = int(plus_cfg.get("max_playlist_tracks", 15))
        active_count = store.count_user_playlist_tracks(user["id"], active_only=True)
        if active_count >= max_limit:
            raise HTTPException(
                status_code=400,
                detail=f"Batas penambahan playlist telah tercapai (maksimal {max_limit} lagu). Silakan hubungi admin untuk membuka paket langganan Akses Plus."
            )

    raw_url = str(body.get("youtube_url") or "").strip()
    title = str(body.get("title") or "").strip()
    artist = str(body.get("artist") or "").strip()

    if not raw_url:
        raise HTTPException(status_code=400, detail="Link YouTube atau TikTok wajib diisi.")

    media_info = detect_media_url_source(raw_url)
    if not media_info:
        raise HTTPException(
            status_code=400,
            detail="Format link tidak valid. Masukkan link YouTube (youtube.com, youtu.be) atau TikTok (tiktok.com, vt.tiktok.com).",
        )

    platform = media_info["platform"]
    video_id = media_info["video_id"]
    full_url = media_info["canonical_url"]

    # ── Duration Limit Check (Max 1 hour 10 min = 4200s absolute, max 10 min if user already has 1-hour version) ──
    dur_info = check_media_duration_limit(full_url, max_seconds=4200, platform=platform)
    if not dur_info.get("valid", True):
        raise HTTPException(
            status_code=400,
            detail=dur_info.get("error") or "Durasi lagu melebihi batas maksimal 1 jam 10 menit.",
        )

    cand_sec = dur_info.get("duration_sec")
    if cand_sec is not None and cand_sec > 600:
        # Check if user already has a 1-hour version track (> 10 minutes) in playlist
        user_tracks = store.get_user_playlist(user["id"])
        existing_long_track = None
        for t in user_tracks:
            t_sec = parse_duration_to_seconds(t.get("duration"))
            if t_sec and t_sec > 600:
                existing_long_track = t
                break

        if existing_long_track:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Akun Anda sudah memiliki 1 lagu berdurasi panjang versi 1 jam "
                    f"('#{existing_long_track.get('track_number')} {existing_long_track.get('title')}' - {existing_long_track.get('duration')}). "
                    f"Tiap akun dibatasi maksimal 1 lagu versi 1 jam (maksimal 1 jam 10 menit). "
                    f"Lagu berikutnya maksimal berdurasi 10 menit."
                ),
            )

    if not title and dur_info.get("title"):
        title = dur_info["title"]
    if not artist and dur_info.get("artist"):
        artist = dur_info["artist"]
    track_duration = dur_info.get("formatted_duration") or ""

    if platform == "youtube":
        if not title:
            fetched_title = _fetch_youtube_title_oembed(full_url)
            title = fetched_title or f"YouTube Video ({video_id})"
    elif platform == "tiktok":
        if not title or not artist:
            fetched_title, fetched_artist = _fetch_tiktok_metadata(full_url)
            if not title:
                title = fetched_title or f"TikTok Video ({video_id[3:] if video_id.startswith('tt_') else video_id})"
            if not artist and fetched_artist:
                artist = fetched_artist

    try:
        track = store.add_playlist_track(
            owner_id=user["id"],
            title=title,
            youtube_url=full_url,
            video_id=video_id,
            artist=artist,
            duration=track_duration,
        )

        # Trigger asynchronous background pre-caching
        try:
            asyncio.create_task(enqueue_precache(video_id, full_url, duration_sec=cand_sec))
        except Exception as q_exc:
            logger.warning("Failed triggering background precache: %s", q_exc)

        c_info = get_track_cache_status(video_id)
        track["cache_status"] = c_info.get("status", "downloading")
        track["is_cached"] = c_info.get("is_cached", False)
        track["cache_percent"] = c_info.get("percent", 0)

        return {
            "success": True,
            "message": f"Lagu '{title}' ({platform.capitalize()}) berhasil ditambahkan ke Playlist (Nomor #{track.get('track_number')}). Audio otomatis di-cache ke disk server.",
            "track": track,
            "platform": platform,
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
    duration_str = None

    if raw_url is not None:
        raw_url = str(raw_url).strip()
        media_info = detect_media_url_source(raw_url)
        if not media_info:
            raise HTTPException(
                status_code=400,
                detail="Format link tidak valid. Masukkan link YouTube atau TikTok yang valid.",
            )
        video_id = media_info["video_id"]
        raw_url = media_info["canonical_url"]

        dur_info = check_media_duration_limit(raw_url, max_seconds=4200, platform=media_info["platform"])
        if not dur_info.get("valid", True):
            raise HTTPException(
                status_code=400,
                detail=dur_info.get("error") or "Durasi lagu melebihi batas maksimal 1 jam 10 menit.",
            )

        cand_sec = dur_info.get("duration_sec")
        if cand_sec is not None and cand_sec > 600:
            user_tracks = store.get_user_playlist(user["id"])
            existing_long_track = None
            for t in user_tracks:
                if t.get("id") == track_id:
                    continue
                t_sec = parse_duration_to_seconds(t.get("duration"))
                if t_sec and t_sec > 600:
                    existing_long_track = t
                    break

            if existing_long_track:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Akun Anda sudah memiliki 1 lagu berdurasi panjang versi 1 jam "
                        f"('#{existing_long_track.get('track_number')} {existing_long_track.get('title')}' - {existing_long_track.get('duration')}). "
                        f"Tiap akun dibatasi maksimal 1 lagu versi 1 jam (maksimal 1 jam 10 menit). "
                        f"Lagu berikutnya maksimal berdurasi 10 menit."
                    ),
                )

        duration_str = dur_info.get("formatted_duration") or None
        if not title and dur_info.get("title"):
            title = dur_info["title"]
        if not artist and dur_info.get("artist"):
            artist = dur_info["artist"]

        try:
            asyncio.create_task(enqueue_precache(video_id, raw_url, duration_sec=cand_sec))
        except Exception:
            pass

    updated = store.update_playlist_track(
        owner_id=user["id"],
        track_id=track_id,
        title=str(title).strip() if title is not None else None,
        youtube_url=raw_url,
        video_id=video_id,
        artist=str(artist).strip() if artist is not None else None,
        duration=duration_str,
    )
    if updated and updated.get("video_id"):
        c_info = get_track_cache_status(updated["video_id"])
        updated["cache_status"] = c_info.get("status", "uncached")
        updated["is_cached"] = c_info.get("is_cached", False)
        updated["cache_percent"] = c_info.get("percent", 100 if c_info.get("is_cached") else 0)

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
    cache_cleaned = 0
    if deleted and existing.get("video_id"):
        vid = existing["video_id"]
        # If no other user still has this video in their playlist, delete the disk cache
        other_exists = False
        try:
            if hasattr(store, "is_video_id_in_any_playlist"):
                other_exists = store.is_video_id_in_any_playlist(vid)
        except Exception as exc:
            logger.warning("Error checking if video_id exists in other playlists: %s", exc)

        if not other_exists:
            cache_cleaned = delete_track_cache(vid)
            logger.info("Playlist track #%s deleted by user %s: cleaned %d cache files for video_id %s", track_id, user["id"], cache_cleaned, vid)

    return {
        "success": deleted,
        "cache_deleted": cache_cleaned > 0,
        "message": f"Lagu '{existing.get('title')}' berhasil dihapus dari playlist dan file audio cache dibersihkan.",
    }


@router.post("/api/v1/playlist/{track_id}/play")
async def api_play_playlist_track(track_id: int, request: Request):
    user = require_user(request)
    store = get_store()
    track = store.get_playlist_track(user["id"], track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Lagu tidak ditemukan di playlist Anda.")

    if not track.get("is_active", True):
        raise HTTPException(
            status_code=403,
            detail=f"Lagu '{track.get('title')}' telah dinonaktifkan oleh admin. Silakan hubungi admin untuk membuka paket langganan Akses Plus."
        )

    # Increment play count
    updated_track = store.increment_playlist_play_count(user["id"], track_id=track_id) or track

    # Queue to ESP32 audio command
    owner_id = user["id"]
    vid = track.get("video_id", "")
    title = track.get("title", "")
    user_mac = store.get_user_mac_address(owner_id) if hasattr(store, "get_user_mac_address") else ""
    mac_param = f"&mac={user_mac}" if user_mac else ""
    stream_url = f"/api/audio/stream/{vid}?owner_id={owner_id}{mac_param}"
    base = os.getenv("SERVER_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
    device_base = base.replace("https://", "http://") if user_mac else base
    full_stream = f"{device_base}{stream_url}" if stream_url.startswith("/") else stream_url

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


@router.get("/api/v1/playlist/cache-status")
async def api_playlist_cache_status(request: Request):
    """Returns real-time audio disk cache status for all user playlist tracks."""
    user = require_user(request)
    store = get_store()
    tracks = store.get_user_playlist(user["id"])
    statuses = {}
    for t in tracks:
        vid = t.get("video_id", "")
        if vid:
            statuses[str(t["id"])] = {
                "video_id": vid,
                **get_track_cache_status(vid),
            }
    return {
        "success": True,
        "tracks": statuses,
        "queue": get_all_queue_status(),
    }


@router.post("/api/v1/playlist/{track_id}/cache")
async def api_cache_single_track(track_id: int, request: Request):
    """Enqueues an individual playlist track for server disk pre-caching."""
    user = require_user(request)
    store = get_store()
    track = store.get_playlist_track(user["id"], track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Lagu tidak ditemukan di playlist Anda.")
    vid = track.get("video_id", "")
    url = track.get("youtube_url", "")
    if not vid or not url:
        raise HTTPException(status_code=400, detail="Data lagu tidak valid.")

    status_before = get_track_cache_status(vid)
    if status_before["is_cached"]:
        return {
            "success": True,
            "queued": False,
            "status": status_before,
            "message": f"Lagu '{track.get('title')}' sudah 100% tersimpan di disk cache server.",
        }

    dur_sec = parse_duration_to_seconds(track.get("duration"))
    queued = await enqueue_precache(vid, url, duration_sec=dur_sec)
    status_after = get_track_cache_status(vid)
    return {
        "success": True,
        "queued": queued,
        "status": status_after,
        "message": f"Pengunduhan cache untuk '{track.get('title')}' telah ditambahkan ke antrean latar belakang server.",
    }


@router.post("/api/v1/playlist/cache-all")
async def api_cache_all_tracks(request: Request):
    """Enqueues all uncached playlist tracks for background server disk caching."""
    user = require_user(request)
    store = get_store()
    tracks = store.get_user_playlist(user["id"])
    queued_count = 0
    for t in tracks:
        vid = t.get("video_id", "")
        url = t.get("youtube_url", "")
        if vid and url:
            st = get_track_cache_status(vid)
            if not st["is_cached"]:
                dur_sec = parse_duration_to_seconds(t.get("duration"))
                if await enqueue_precache(vid, url, duration_sec=dur_sec):
                    queued_count += 1
    return {
        "success": True,
        "queued_count": queued_count,
        "message": (
            f"{queued_count} lagu berhasil ditambahkan ke antrean download cache server."
            if queued_count > 0
            else "Semua lagu di playlist Anda sudah ter-cache 100% di server!"
        ),
    }

