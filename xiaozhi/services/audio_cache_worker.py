import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from xiaozhi.core.utils import format_duration, normalize_tiktok_url_for_ytdlp

logger = logging.getLogger("xiaozhi.audio_cache_worker")

AUDIO_CACHE_DIR = Path(
    os.getenv(
        "AUDIO_CACHE_DIR",
        str(Path(__file__).resolve().parent.parent.parent / "data" / "audio_cache"),
    )
)
try:
    AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

_queue: asyncio.Queue = asyncio.Queue()
_queue_set: Set[str] = set()
_active_downloads: Set[str] = set()
_worker_task: Optional[asyncio.Task] = None
_cache_semaphore = asyncio.Semaphore(1)


def check_media_duration_limit(
    media_url: str,
    max_seconds: int = 600,
    platform: str = "youtube",
) -> Dict[str, Any]:
    """
    Validates that a YouTube or TikTok audio does not exceed max_seconds (default 10 minutes / 600s).
    Also checks if the media is a live stream.
    Returns:
        {
            "valid": bool,
            "duration_sec": Optional[int],
            "formatted_duration": str,
            "title": Optional[str],
            "artist": Optional[str],
            "error": Optional[str],
        }
    """
    try:
        import yt_dlp
    except ImportError:
        logger.warning("yt_dlp not installed, duration check skipped.")
        return {
            "valid": True,
            "duration_sec": None,
            "formatted_duration": "",
            "title": None,
            "artist": None,
            "error": None,
        }

    target_url = media_url.strip()
    if platform == "tiktok" or "tiktok.com" in target_url:
        target_url = normalize_tiktok_url_for_ytdlp(target_url)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "socket_timeout": 8,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)
            if not info:
                return {
                    "valid": True,
                    "duration_sec": None,
                    "formatted_duration": "",
                    "title": None,
                    "artist": None,
                    "error": None,
                }

            # Check if live stream
            if info.get("is_live") or info.get("live_status") == "is_live":
                return {
                    "valid": False,
                    "duration_sec": None,
                    "formatted_duration": "Live Stream",
                    "title": info.get("title"),
                    "artist": info.get("uploader") or info.get("creator"),
                    "error": "Video siaran langsung (live stream) tidak dapat disimpan ke playlist.",
                }

            raw_duration = info.get("duration") or info.get("duration_string")
            duration_sec = None
            if isinstance(raw_duration, (int, float)):
                duration_sec = int(raw_duration)
            elif isinstance(raw_duration, str):
                parts = raw_duration.strip().split(":")
                try:
                    if len(parts) == 2:
                        duration_sec = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        duration_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                    else:
                        duration_sec = int(float(raw_duration))
                except Exception:
                    duration_sec = None

            fmt_dur = format_duration(duration_sec or raw_duration)
            title = info.get("title")
            artist = info.get("uploader") or info.get("creator") or info.get("channel") or ""

            if duration_sec is not None and duration_sec > max_seconds:
                m = duration_sec // 60
                s = duration_sec % 60
                dur_text = f"{m} menit {s} detik" if m > 0 else f"{s} detik"
                max_m = max_seconds // 60
                return {
                    "valid": False,
                    "duration_sec": duration_sec,
                    "formatted_duration": fmt_dur,
                    "title": title,
                    "artist": artist,
                    "error": f"Durasi audio ({dur_text}) melebihi batas maksimal {max_m} menit. Video di atas {max_m} menit tidak dapat disimpan ke playlist.",
                }

            return {
                "valid": True,
                "duration_sec": duration_sec,
                "formatted_duration": fmt_dur,
                "title": title,
                "artist": artist,
                "error": None,
            }

    except Exception as exc:
        logger.warning("Duration check error for %s: %s", media_url, exc)
        return {
            "valid": True,
            "duration_sec": None,
            "formatted_duration": "",
            "title": None,
            "artist": None,
            "error": None,
        }


def get_track_cache_status(video_id: str) -> Dict[str, Any]:
    """
    Returns the real-time cache state of an audio track:
    - 'ready': Audio file exists on VPS local disk (>10KB) -> 100% ready to play
    - 'downloading': Currently in the download queue or actively transcoding
    - 'uncached': Not yet in disk cache
    """
    safe_vid = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(video_id or "").strip())
    if not safe_vid:
        return {"status": "uncached", "is_cached": False, "size_bytes": 0, "filename": ""}

    if safe_vid in _active_downloads or safe_vid in _queue_set:
        return {"status": "downloading", "is_cached": False, "size_bytes": 0, "filename": ""}

    matches = list(AUDIO_CACHE_DIR.glob(f"{safe_vid}_*.opus")) if AUDIO_CACHE_DIR.exists() else []
    valid = [f for f in matches if f.is_file() and f.stat().st_size > 10240]
    if valid:
        return {
            "status": "ready",
            "is_cached": True,
            "size_bytes": valid[0].stat().st_size,
            "filename": valid[0].name,
        }

    return {"status": "uncached", "is_cached": False, "size_bytes": 0, "filename": ""}


def get_all_queue_status() -> Dict[str, Any]:
    """Summary of active and queued background cache tasks."""
    return {
        "active": list(_active_downloads),
        "queued": list(_queue_set),
        "total_queued": len(_queue_set) + len(_active_downloads),
    }


def _ensure_worker_running():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        try:
            loop = asyncio.get_running_loop()
            _worker_task = loop.create_task(_worker_loop(), name="audio_precache_worker")
        except RuntimeError:
            pass


async def _worker_loop():
    logger.info("Audio cache worker loop started.")
    while True:
        try:
            item = await _queue.get()
        except asyncio.CancelledError:
            break

        vid = item["video_id"]
        youtube_url = item["youtube_url"]
        sample_rate = item.get("sample_rate", 16000)
        bitrate = item.get("bitrate", "16k")

        _queue_set.discard(vid)
        _active_downloads.add(vid)

        try:
            async with _cache_semaphore:
                await _execute_download_and_cache(vid, youtube_url, sample_rate, bitrate)
        except Exception as exc:
            logger.exception("Precache task error for %s: %s", vid, exc)
        finally:
            _active_downloads.discard(vid)
            _queue.task_done()


async def _execute_download_and_cache(
    video_id: str,
    youtube_url: str,
    sample_rate: int = 16000,
    bitrate: str = "16k",
):
    safe_vid = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(video_id or "").strip())
    safe_br = re.sub(r"[^a-zA-Z0-9]", "", str(bitrate or "").strip().lower())
    target_cache_path = AUDIO_CACHE_DIR / f"{safe_vid}_{sample_rate}_{safe_br}.opus"

    if target_cache_path.is_file() and target_cache_path.stat().st_size > 10240:
        logger.info("Precache: %s already cached, skipping.", target_cache_path.name)
        return

    from xiaozhi.services.youtube_streamer import extract_audio_url

    source_url, extracted_title = await extract_audio_url(video_id, target_url=youtube_url)
    if not source_url:
        logger.warning("Precache: failed to extract audio stream for %s (%s)", video_id, youtube_url)
        return

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        logger.warning("Precache: ffmpeg not found on system, cannot transcode.")
        return

    tmp_cache_path = AUDIO_CACHE_DIR / f"{safe_vid}_{sample_rate}_{safe_br}.tmp.{os.getpid()}.opus"

    cmd = [
        ffmpeg_bin,
        "-y",
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64)\r\n",
        "-i", source_url,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "libopus",
        "-b:a", bitrate,
        "-vbr", "on",
        "-compression_level", "5",
        "-application", "audio",
        "-threads", "1",
        str(tmp_cache_path),
    ]

    logger.info("Precache transcoding started for %s -> %s", video_id, target_cache_path.name)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode == 0 and tmp_cache_path.exists() and tmp_cache_path.stat().st_size > 10240:
            tmp_cache_path.replace(target_cache_path)
            logger.info(
                "Precache SUCCESS: %s cached to disk (%d bytes, %s, %dHz)",
                video_id,
                target_cache_path.stat().st_size,
                bitrate,
                sample_rate,
            )
        else:
            if tmp_cache_path.exists():
                tmp_cache_path.unlink(missing_ok=True)
            logger.warning(
                "Precache FAILED for %s (exit code %s, size=%s)",
                video_id,
                proc.returncode,
                tmp_cache_path.stat().st_size if tmp_cache_path.exists() else 0,
            )
    except Exception as exc:
        if tmp_cache_path.exists():
            tmp_cache_path.unlink(missing_ok=True)
        logger.error("Precache subprocess error for %s: %s", video_id, exc)


async def enqueue_precache(
    video_id: str,
    youtube_url: str,
    sample_rate: int = 16000,
    bitrate: str = "16k",
) -> bool:
    """
    Submits an audio track to the background download and transcoding queue.
    Returns True if scheduled, False if already cached or already in queue.
    """
    safe_vid = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(video_id or "").strip())
    if not safe_vid:
        return False

    status = get_track_cache_status(safe_vid)
    if status["is_cached"]:
        return False

    if safe_vid in _queue_set or safe_vid in _active_downloads:
        return False

    _queue_set.add(safe_vid)
    await _queue.put({
        "video_id": safe_vid,
        "youtube_url": youtube_url,
        "sample_rate": sample_rate,
        "bitrate": bitrate,
    })
    _ensure_worker_running()
    return True
