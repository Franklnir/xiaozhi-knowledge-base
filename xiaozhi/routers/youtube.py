from xiaozhi.services.youtube_streamer import stream_video_to_websocket
import asyncio
import base64
import io
import logging
import re
import shutil
import subprocess
from typing import Optional, AsyncGenerator

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from xiaozhi.dependencies import get_current_user, get_store, require_user

logger = logging.getLogger("xiaozhi.youtube")
router = APIRouter()

# YouTube imports (optional)
try:
    import yt_dlp
except ImportError:
    yt_dlp = None

_FFMPEG_PATH = None
if yt_dlp:
    try:
        import imageio_ffmpeg
        _FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, Exception):
        pass
    if not _FFMPEG_PATH:
        _FFMPEG_PATH = shutil.which("ffmpeg")


def _normalize_mac(mac: str) -> str:
    cleaned = re.sub(r"[^0-9a-fA-F]", "", mac or "").lower()
    if len(cleaned) == 12:
        return ":".join(cleaned[i:i+2] for i in range(0, 12, 2))
    return mac.strip().lower()


def _resolve_owner_for_device(store, device_id: str) -> Optional[int]:
    """Resolve user owner ID based on ESP32 device_id or MAC address."""
    device_id = str(device_id or "").strip()
    if not device_id:
        return None

    raw_mac = device_id[6:] if device_id.lower().startswith("esp32-") else device_id
    mac_with_colons = _normalize_mac(raw_mac)

    conn = getattr(store, "_get_conn", lambda: None)()
    if conn is not None:
        try:
            pending_row = conn.execute(
                "SELECT owner_id FROM audio_queue WHERE status = 'pending' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if pending_row and pending_row["owner_id"]:
                return int(pending_row["owner_id"])
        except Exception:
            pass

        try:
            row = conn.execute(
                "SELECT owner_id FROM registered_devices WHERE LOWER(device_id) = ? OR LOWER(device_id) = ?",
                (device_id.lower(), raw_mac.lower())
            ).fetchone()
            if row and row["owner_id"]:
                return int(row["owner_id"])
        except Exception:
            pass

    if hasattr(store, "find_device_by_mac"):
        try:
            dev = store.find_device_by_mac(mac_with_colons)
            if dev and dev.get("owner_id"):
                return int(dev["owner_id"])
        except Exception:
            pass

    if conn is not None:
        try:
            token_row = conn.execute("SELECT user_id FROM xiaozhi_tokens ORDER BY updated_at DESC LIMIT 1").fetchone()
            if token_row and token_row["user_id"]:
                return int(token_row["user_id"])
        except Exception:
            pass

        try:
            admin_row = conn.execute("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
            if admin_row and admin_row["id"]:
                return int(admin_row["id"])
        except Exception:
            pass

        try:
            first_user = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
            if first_user and first_user["id"]:
                return int(first_user["id"])
        except Exception:
            pass

    return 1


def youtube_search(query: str, max_results: int = 5) -> list:
    if not yt_dlp:
        raise ValueError("yt_dlp tidak tersedia.")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "default_search": "ytsearch",
        "max_downloads": max_results,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        entries = result.get("entries", [])
        items = []
        for entry in entries[:max_results]:
            video_id = entry.get("id", "")
            items.append({
                "title": entry.get("title", ""),
                "video_id": video_id,
                "video_url": f"https://www.youtube.com/watch?v={video_id}",
                "duration": entry.get("duration_string", ""),
                "thumbnail": entry.get("thumbnails", [{}])[-1].get("url", "") if entry.get("thumbnails") else "",
                "stream_url": f"/api/audio/stream/{video_id}",
            })
        return items


async def _stream_opus_audio(video_id: str, bitrate: str = "12k", start_sec: float = 0.0) -> AsyncGenerator[bytes, None]:
    valid_bitrates = {"6k", "8k", "9k", "10k", "11k", "12k", "16k", "20k", "24k", "32k"}
    br = bitrate.lower().strip() if bitrate and bitrate.lower().strip() in valid_bitrates else "11k"
    if not yt_dlp:
        raise HTTPException(status_code=503, detail="yt_dlp tidak tersedia.")

    ffmpeg_bin = _FFMPEG_PATH or shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise HTTPException(status_code=500, detail="FFmpeg tidak terpasang di server.")

    loop = asyncio.get_running_loop()

    def _extract_url():
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "extractaudio": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            return info.get("url")

    try:
        source_url = await loop.run_in_executor(None, _extract_url)
    except Exception as exc:
        logger.error("Failed to extract audio URL for video %s: %s", video_id, exc)
        raise HTTPException(status_code=500, detail=f"Gagal mengekstrak audio: {exc}")

    if not source_url:
        raise HTTPException(status_code=404, detail="Audio stream tidak ditemukan.")

    cmd = [
        ffmpeg_bin,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
    ]
    if start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.2f}"])
    cmd.extend([
        "-re",
        "-i", source_url,
        "-vn",
        "-ac", "1",
        "-ar", "24000",
        "-c:a", "libopus",
        "-b:a", br,
        "-vbr", "on",
        "-compression_level", "5",
        "-application", "voip",
        "-flush_packets", "1",
        "-frame_duration", "60",
        "-page_duration", "60000",
        "-f", "ogg",
        "pipe:1"
    ])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    total_bytes = 0
    logger.info(f"Starting YouTube stream for {video_id}, FFmpeg PID={proc.pid}")
    try:
        while True:
            chunk = await proc.stdout.read(1536)
            if not chunk:
                logger.info(f"YouTube stream for {video_id} reached EOF, total={total_bytes} bytes")
                break
            total_bytes += len(chunk)
            if total_bytes % (1536 * 50) == 0:
                logger.info(f"YouTube stream for {video_id}: sent {total_bytes // 1024} KB")
            yield chunk
    except (asyncio.CancelledError, GeneratorExit) as exc:
        logger.warning(f"YouTube stream for {video_id} client disconnected or cancelled after {total_bytes} bytes")
    except Exception as exc:
        logger.error(f"YouTube stream for {video_id} error after {total_bytes} bytes: {exc}")
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        logger.info(f"YouTube stream for {video_id} closed, proc_returncode={proc.returncode}")


@router.get("/api/audio/stream/{video_id}")
async def audio_stream_ogg_opus(video_id: str, request: Request, br: str = "12k", start: float = 0.0):
    """Real-time Ogg/Opus mono 24kHz transcoding stream for ESP32 hardware decoder with adaptive bitrate and seek resume support."""
    return StreamingResponse(
        _stream_opus_audio(video_id, bitrate=br, start_sec=start),
        media_type="audio/ogg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Accept-Ranges": "none",
        }
    )


@router.get("/api/audio/commands/{device_id}")
async def audio_commands_for_device(device_id: str, request: Request):
    """ESP32 firmware command poll endpoint."""
    store = get_store()
    owner_id = _resolve_owner_for_device(store, device_id)
    if not owner_id:
        return {"commands": []}

    commands = store.get_pending_audio_commands(owner_id) if hasattr(store, "get_pending_audio_commands") else store.get_audio_commands(owner_id)

    formatted_commands = []
    base_url = str(request.base_url).rstrip("/")

    for cmd in commands:
        stream_url = cmd.get("stream_url", "")
        if stream_url.startswith("/"):
            stream_url = f"{base_url}{stream_url}"

        formatted_commands.append({
            "id": str(cmd.get("id", "")),
            "title": cmd.get("title", ""),
            "stream_url": stream_url,
            "video_id": cmd.get("video_id", ""),
        })

    return {"commands": formatted_commands}


@router.post("/api/audio/ack/{command_id}")
async def audio_ack_for_device(command_id: str, request: Request):
    """ESP32 firmware playback ACK endpoint."""
    store = get_store()
    device_id = ""
    try:
        body = await request.json()
        device_id = str(body.get("device_id", "")).strip()
    except Exception:
        pass

    if not device_id:
        device_id = request.headers.get("Device-Id", "").strip()

    owner_id = _resolve_owner_for_device(store, device_id)
    if owner_id:
        store.ack_audio_command(owner_id, command_id)
    return {"success": True}


@router.get("/api/youtube-audio/{video_id}")
async def youtube_audio_stream(video_id: str, request: Request):
    if not yt_dlp:
        raise HTTPException(status_code=503, detail="yt_dlp tidak tersedia.")
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login diperlukan.")
    store = get_store()
    features = store.get_user_features(user["id"])
    if not features.get("youtube_music", True):
        raise HTTPException(status_code=403, detail="Fitur YouTube Music dinonaktifkan.")

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "extractaudio": True,
            "audioformat": "opus",
            "audio_quality": 0,
        }
        if _FFMPEG_PATH:
            ydl_opts["ffmpeg_location"] = _FFMPEG_PATH

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            stream_url = info.get("url")
            if not stream_url:
                raise HTTPException(status_code=404, detail="Audio stream tidak ditemukan.")
            return JSONResponse({
                "success": True,
                "video_id": video_id,
                "title": info.get("title", ""),
                "stream_url": stream_url,
                "duration": info.get("duration_string", ""),
            })
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil audio: {str(exc)[:100]}")


@router.post("/api/audio/queue")
async def queue_audio(request: Request):
    user = require_user(request)
    store = get_store()
    body = await request.json()
    title = str(body.get("title", "")).strip()
    stream_url = str(body.get("stream_url", "")).strip()
    video_url = str(body.get("video_url", "")).strip()
    duration = str(body.get("duration", "")).strip()
    video_id = str(body.get("video_id", "")).strip()
    if not stream_url:
        if video_id:
            stream_url = f"/api/audio/stream/{video_id}"
        else:
            raise HTTPException(status_code=400, detail="stream_url diperlukan.")
    store.queue_audio_command(user["id"], title=title, stream_url=stream_url, video_url=video_url, duration=duration, video_id=video_id)
    return {"success": True, "message": f"Lagu '{title}' berhasil di-queue."}


@router.get("/api/audio/now-playing")
async def now_playing(request: Request):
    user = get_current_user(request)
    if not user:
        return {"success": False, "message": "Login diperlukan."}
    store = get_store()
    current = store.get_current_audio(user["id"])
    return {"success": True, "current": current}


@router.get("/api/audio/play_direct")
async def audio_play_direct(q: str):
    """Direct search and stream for ESP32 instant on-demand playback."""
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query required")
    try:
        results = youtube_search(q, max_results=1)
        if not results:
            raise HTTPException(status_code=404, detail="Song not found")
        return {
            "success": True,
            "video_id": results[0]["video_id"],
            "title": results[0]["title"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/device/audio/commands")
async def device_audio_commands(request: Request, token: str = Query(""), mac: str = Query("")):
    store = get_store()
    conn = getattr(store, "_get_conn", lambda: None)()
    if conn is not None:
        try:
            conn.execute("UPDATE audio_queue SET status='expired' WHERE status='pending' AND datetime(created_at) < datetime('now', '-30 minutes')")
            conn.commit()
        except Exception:
            pass
    owner_id = None
    if token:
        owner = store.find_user_by_mcp_token(token)
        if owner:
            owner_id = owner["user_id"]
    if not owner_id and mac:
        owner_id = _resolve_owner_for_device(store, mac)

    if not owner_id:
        device_hdr = request.headers.get("Device-Id", "")
        if device_hdr:
            owner_id = _resolve_owner_for_device(store, device_hdr)

    if not owner_id:
        return {"success": True, "commands": []}

    commands = store.get_pending_audio_commands(owner_id) if hasattr(store, "get_pending_audio_commands") else store.get_audio_commands(owner_id)
    if commands:
        latest = commands[-1]
        for old in commands[:-1]:
            try:
                store.ack_audio_command(owner_id, old["id"])
            except Exception:
                pass
        commands = [latest]
    return {"success": True, "commands": commands}


@router.post("/api/device/audio/ack")
async def device_audio_ack(request: Request):
    store = get_store()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if not body:
        form = await request.form()
        body = dict(form)

    token = str(body.get("token", "")).strip()
    command_id = str(body.get("command_id", "")).strip()
    mac = str(body.get("mac", "")).strip()

    owner_id = None
    if token:
        owner = store.find_user_by_mcp_token(token)
        if owner:
            owner_id = owner["user_id"]
    if not owner_id and mac:
        owner_id = _resolve_owner_for_device(store, mac)
    if not owner_id:
        device_hdr = request.headers.get("Device-Id", "")
        if device_hdr:
            owner_id = _resolve_owner_for_device(store, device_hdr)

    if owner_id and command_id:
        store.ack_audio_command(owner_id, command_id)
    return {"success": True}


@router.websocket("/ws/audio/stream/{video_id}")
async def ws_audio_stream_endpoint(
    websocket: WebSocket,
    video_id: str,
    sample_rate: int = 24000,
    title: str = ""
):
    """WebSocket Opus 24kHz stream for ESP32 hardware decoder."""
    await websocket.accept()
    try:
        await stream_video_to_websocket(websocket, video_id, title=title, sample_rate=sample_rate)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("WebSocket stream error: %s", exc)


@router.websocket("/ws/device/audio/{device_id}")
async def ws_device_audio_channel(websocket: WebSocket, device_id: str):
    """Persistent audio streaming channel for ESP32 device."""
    await websocket.accept()
    store = get_store()
    owner_id = _resolve_owner_for_device(store, device_id)
    logger.info("Device connected to persistent audio channel: %s (owner=%s)", device_id, owner_id)
    
    try:
        while True:
            # Poll for pending audio commands for this device
            if owner_id:
                commands = store.get_pending_audio_commands(owner_id) if hasattr(store, "get_pending_audio_commands") else store.get_audio_commands(owner_id)
                if commands:
                    cmd = commands[0]
                    cmd_id = str(cmd.get("id", ""))
                    video_id = cmd.get("video_id", "")
                    title = cmd.get("title", "")
                    if video_id:
                        store.ack_audio_command(owner_id, cmd_id)
                        await stream_video_to_websocket(websocket, video_id, title=title, sample_rate=24000)
            
            # Non-blocking check or wait
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                if "ping" in msg.lower():
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        logger.info("Device disconnected from audio channel: %s", device_id)
    except Exception as exc:
        logger.error("Device audio channel error: %s", exc)
