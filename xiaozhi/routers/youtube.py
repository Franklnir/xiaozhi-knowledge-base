from xiaozhi.services.playback_tracker import playback_tracker
from xiaozhi.services.youtube_streamer import stream_video_to_websocket, extract_audio_url, get_cached_video_meta
import asyncio
import base64
import collections
import io
import logging
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, AsyncGenerator

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse

from xiaozhi.dependencies import get_current_user, get_store, require_user
from xiaozhi.services.mcp_service import is_mcp_connected

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


def _can_bind_device_to_user(user_id: Optional[int]) -> bool:
    """
    Validasi apakah user memiliki koneksi MCP aktif sebelum menautkan/mengikat Board ID.
    Jika MCP tidak terhubung (is_mcp_connected == False), penautan board DITOLAK
    sehingga board tidak sembarangan diikat ke akun yang tidak aktif/terputus MCP-nya.
    """
    if not user_id:
        return False
    try:
        return bool(is_mcp_connected(int(user_id)))
    except Exception as exc:
        logger.warning("Error checking MCP connection for user %s: %s", user_id, exc)
        return False


def _resolve_owner_for_device(store, device_id: str) -> Optional[int]:
    """Resolve user owner ID based on ESP32 device_id or MAC address."""
    device_id = str(device_id or "").strip()
    if not device_id:
        return None

    raw_mac = device_id[6:] if device_id.lower().startswith("esp32-") else device_id
    mac_with_colons = _normalize_mac(raw_mac)
    raw_mac_clean = raw_mac.replace(":", "").replace("-", "").lower()

    # 1. PRIMARY AUTHORITY: Check registered_devices by exact device_id / MAC via store
    try:
        if hasattr(store, "find_device_by_mac"):
            dev = (
                store.find_device_by_mac(device_id) or
                store.find_device_by_mac(mac_with_colons) or
                store.find_device_by_mac(raw_mac) or
                store.find_device_by_mac(raw_mac_clean)
            )
            if dev and dev.get("owner_id"):
                return int(dev["owner_id"])
        if hasattr(store, "find_device_by_id"):
            dev = (
                store.find_device_by_id(device_id) or
                store.find_device_by_id(mac_with_colons) or
                store.find_device_by_id(raw_mac)
            )
            if dev and dev.get("owner_id"):
                return int(dev["owner_id"])
    except Exception as e:
        logger.debug("Device lookup by MAC failed: %s", e)

    # 2. MATCH AUDIO QUEUE ONLY IF THE URL/COMMAND EXPLICITLY CONTAINS THIS MAC
    try:
        if hasattr(store, "find_recent_audio_command_by_mac"):
            cmd = store.find_recent_audio_command_by_mac(mac_with_colons) or store.find_recent_audio_command_by_mac(raw_mac)
            if cmd and cmd.get("owner_id"):
                return int(cmd["owner_id"])
    except Exception as e:
        logger.debug("Audio queue lookup by MAC failed: %s", e)

    return None


def sanitize_youtube_query(raw_q: str) -> str:
    """Membersihkan kata percakapan, imbuhan ASR terpotong, dan tanda baca dari query pencarian YouTube."""
    if not raw_q:
        return ""
    q = raw_q.strip()
    # 1. Bersihkan tanda kutip dan tanda baca di ujung awal/akhir
    q = re.sub(r'^["\'\s.,!?;:-]+|["\'\s.,!?;:-]+$', '', q).strip()

    # 2. Bersihkan awalan percakapan (termasuk token ASR terpotong seperti 'kan lagu', 'terin lagu')
    prefix_pattern = (
        r"(?i)^(?:(?:halo\s+|hi\s+|hai\s+)?(?:xiaozhi|asisten)\s*,?\s*)?"
        r"(?:tolong\s+|coba\s+|bisa\s+|mohon\s+)?"
        r"(?:(?:putar(?:kan)?|puter(?:in)?|setel(?:kan)?|main(?:kan)?|cari(?:kan)?|dengar(?:kan)?|play|nyala(?:kan)?|hidup(?:kan)?)\s+)?"
        r"(?:kan\s+)?"
        r"(?:lagu|musik|music|video|song|track)?\s*"
        r"(?:yang\s+)?(?:judul(?:nya)?\s+|berjudul\s+|dari\s+)?"
    )
    q_stripped = re.sub(prefix_pattern, "", q).strip()
    if q_stripped:
        q = q_stripped

    # 3. Bersihkan akhiran percakapan (misal 'di youtube music', 'di youtube', 'youtube', 'dari youtube')
    suffix_pattern = r"(?i)\s*(?:di|dari|pada|lewat|via|on|from)?\s*youtube(?:\s+music)?[\s.,!?;:-]*$"
    q = re.sub(suffix_pattern, "", q).strip()

    # 4. Bersihkan sisa tanda baca di tepi
    q = re.sub(r'^["\'\s.,!?;:-]+|["\'\s.,!?;:-]+$', '', q).strip()
    return q or raw_q.strip()


def youtube_search(query: str, max_results: int = 5) -> list:
    if not yt_dlp:
        raise ValueError("yt_dlp tidak tersedia.")
    raw_q = (query or "").strip()
    if not raw_q:
        return []

    from xiaozhi.core.utils import extract_youtube_video_id

    # 1. Direct YouTube Video URL or 11-character Video ID
    direct_vid = extract_youtube_video_id(raw_q)
    if direct_vid:
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={direct_vid}", download=False)
                if info:
                    v_title = info.get("title", "") or f"YouTube Video ({direct_vid})"
                    v_dur = info.get("duration_string", "")
                    thumbs = info.get("thumbnails", [])
                    v_thumb = thumbs[-1].get("url", "") if thumbs else ""
                    return [{
                        "title": v_title,
                        "video_id": direct_vid,
                        "video_url": f"https://www.youtube.com/watch?v={direct_vid}",
                        "duration": v_dur,
                        "thumbnail": v_thumb,
                        "stream_url": f"/api/audio/stream/{direct_vid}",
                    }]
        except Exception as exc:
            logger.warning("Direct video extraction for %s failed (%s), fallback to search", direct_vid, exc)

    # 2. YouTube Playlist URL
    m_list = re.search(r"[?&]list=([a-zA-Z0-9_-]+)", raw_q)
    if m_list:
        playlist_id = m_list.group(1)
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "max_downloads": max_results,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                res = ydl.extract_info(f"https://www.youtube.com/playlist?list={playlist_id}", download=False)
                entries = res.get("entries", [])
                items = []
                for entry in entries[:max_results]:
                    entry_id = entry.get("id", "")
                    if entry_id:
                        thumbs = entry.get("thumbnails", [])
                        items.append({
                            "title": entry.get("title", ""),
                            "video_id": entry_id,
                            "video_url": f"https://www.youtube.com/watch?v={entry_id}",
                            "duration": entry.get("duration_string", ""),
                            "thumbnail": thumbs[-1].get("url", "") if thumbs else "",
                            "stream_url": f"/api/audio/stream/{entry_id}",
                        })
                if items:
                    return items
        except Exception as exc:
            logger.warning("Playlist extraction for %s failed (%s), fallback to search", playlist_id, exc)

    # 3. Clean search keywords: strip conversational prefixes & suffixes
    clean_q = sanitize_youtube_query(raw_q)
    target_q = clean_q if clean_q else raw_q

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "default_search": "ytsearch",
        "max_downloads": max_results,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch{max_results}:{target_q}", download=False)
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


def _fetch_user(store, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not user_id:
        return None
    try:
        uid = int(user_id)
        if hasattr(store, "get_user"):
            return store.get_user(uid)
        if hasattr(store, "get_user_by_id"):
            return store.get_user_by_id(uid)
    except Exception:
        pass
    return None


def _resolve_stream_user_and_info(store, video_id: str, request: Request, owner_id: Optional[int] = None, mac: Optional[str] = None, token: Optional[str] = None):
    user = None
    title = ""
    device_mac = (mac or "").strip()
    if not device_mac and hasattr(request, "headers"):
        device_mac = (
            request.headers.get("Device-Id", "") or
            request.headers.get("X-Device-Mac", "") or
            request.headers.get("X-MAC-Address", "") or
            request.headers.get("X-Device-Id", "") or
            request.headers.get("device_id", "") or
            request.headers.get("mac", "")
        ).strip()
    if not device_mac and hasattr(request, "query_params"):
        device_mac = (
            request.query_params.get("mac", "") or
            request.query_params.get("device_id", "")
        ).strip()

    # 1. Highest priority: explicit owner_id from query parameter
    if owner_id:
        user = _fetch_user(store, owner_id)

    # 2. Check recent audio_queue for this video_id (created in last 20 minutes)
    # This directly identifies the user who just asked XiaoZhi to play this song!
    if not user and hasattr(store, "find_recent_audio_command_by_video_id"):
        try:
            cmd = store.find_recent_audio_command_by_video_id(video_id, minutes=20)
            if cmd and cmd.get("owner_id"):
                user = _fetch_user(store, cmd["owner_id"])
                if cmd.get("title") and not title:
                    title = cmd["title"]
        except Exception as e:
            logger.debug("Error resolving user from audio_queue: %s", e)

    # 3. If still not resolved, check MCP token
    if not user and token:
        try:
            owner = store.find_user_by_mcp_token(token)
            if owner and owner.get("user_id"):
                user = _fetch_user(store, owner["user_id"])
        except Exception:
            pass

    # 4. If still not resolved, lookup user by device_mac in registered_devices
    if not user and device_mac:
        try:
            resolved_id = _resolve_owner_for_device(store, device_mac)
            if resolved_id:
                user = _fetch_user(store, resolved_id)
        except Exception:
            pass

    # 5. Check active session user (web browser session)
    if not user:
        try:
            session_user = get_current_user(request)
            if session_user:
                user = session_user
        except Exception:
            pass

    # 6. Fallback: recent audio_queue record within 45 minutes
    if not user and hasattr(store, "find_recent_audio_command_by_video_id"):
        try:
            cmd = store.find_recent_audio_command_by_video_id(video_id, minutes=45)
            if cmd and cmd.get("owner_id"):
                user = _fetch_user(store, cmd["owner_id"])
                if cmd.get("title") and not title:
                    title = cmd["title"]
        except Exception:
            pass

    # If user known but device_mac not provided in request, check if user already has registered MAC
    if user and not device_mac and hasattr(store, "get_user_mac_address"):
        try:
            device_mac = store.get_user_mac_address(user["id"]) or ""
        except Exception:
            pass

    # Extract detected_chip
    detected_chip = ""
    if hasattr(request, "query_params") and request.query_params.get("chip"):
        detected_chip = request.query_params.get("chip", "").strip().lower()
    if not detected_chip and hasattr(request, "headers"):
        detected_chip = (
            request.headers.get("X-Device-Chip", "") or
            request.headers.get("Device-Chip", "") or
            request.headers.get("X-Chip", "") or
            request.headers.get("X-Chip-Type", "")
        ).strip().lower()

    # Auto-register / rebind device MAC to this user in registered_devices
    # Validasi dulu apakah MCP terhubung sebelum mengikat user ke board!
    if user and device_mac and hasattr(store, "register_device"):
        clean_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
        clean_mac = clean_mac.strip().upper()
        if len(clean_mac) >= 11:
            try:
                user_id = user["id"]
                dev_name, dev_type = _format_chip_name_and_type(detected_chip, clean_mac)
                if _can_bind_device_to_user(user_id):
                    res = store.register_device(
                        user_id,
                        device_id=clean_mac,
                        name=dev_name,
                        device_type=dev_type,
                        notes="Tertaut saat stream lagu ESP32 (MCP Terhubung)"
                    )
                    logger.info(f"[STREAM MAC] Device MAC {clean_mac} ({dev_type}) berhasil diikat ke user {user_id} ({user.get('username')}) [MCP AKTIF] -> {res}")
                    
                    # Auto-lock board MAC ke slot MCP pengguna (Slot 1..3)
                    if hasattr(store, "list_user_xiaozhi_tokens") and hasattr(store, "bind_board_to_slot"):
                        try:
                            user_tokens = store.list_user_xiaozhi_tokens(user_id)
                            target_slot = None
                            for t in user_tokens:
                                if t.get("board_mac") == clean_mac:
                                    target_slot = t["slot_number"]
                                    break
                            if not target_slot:
                                for t in user_tokens:
                                    if not t.get("board_mac"):
                                        target_slot = t["slot_number"]
                                        break
                            if target_slot:
                                store.bind_board_to_slot(user_id, slot=target_slot, device_mac=clean_mac, request_id="stream_audio")
                        except Exception as b_exc:
                            logger.warning(f"Gagal auto-lock slot MAC stream: {b_exc}")
                else:
                    if hasattr(store, "record_device_activity"):
                        store.record_device_activity(clean_mac, user_id)
                    logger.warning(f"[STREAM MAC] Board MAC {clean_mac} TIDAK ditautkan ke user {user_id} karena MCP tidak terhubung.")
            except Exception as exc:
                logger.error(f"[STREAM MAC ERROR] Gagal memproses MAC {clean_mac}: {exc}")

    return user, title, device_mac



def _format_chip_name_and_type(chip: Optional[str], clean_mac: str):
    c = (chip or "").lower().replace("-", "").strip()
    if "s3" in c:
        return f"ESP32-S3 ({clean_mac[-5:]})", "esp32-s3"
    elif "c3" in c:
        return f"ESP32-C3 ({clean_mac[-5:]})", "esp32-c3"
    elif "p4" in c:
        return f"ESP32-P4 ({clean_mac[-5:]})", "esp32-p4"
    elif "s2" in c:
        return f"ESP32-S2 ({clean_mac[-5:]})", "esp32-s2"
    elif c:
        return f"ESP32 ({clean_mac[-5:]})", c
    return f"ESP32 ({clean_mac[-5:]})", "esp32"


def resolve_chip_audio_profile(chip: Optional[str] = None, requested_sr: Optional[int] = None) -> int:
    """
    Menentukan sample_rate transcode Ogg/Opus berdasarkan tipe chip ESP32:
    - esp32-c3 / esp32c3:
      Codec hardware native 16kHz, single-core 160MHz tanpa PSRAM.
      Menggunakan sample_rate=16000 (16k) agar tidak ada overhead resampler di CPU C3,
      mencegah suara down/patah-patah dan audio jernih optimal.
    - esp32-s3 / esp32s3 / esp32-p4:
      Dual-core 240MHz + PSRAM, codec hi-fi.
      Menggunakan sample_rate=24000 (24k) untuk fidelity suara maksimal.
    - default / lain-lain: 24000 (atau sesuai requested_sr jika ada)
    """
    if requested_sr in (16000, 24000, 48000):
        return requested_sr

    chip_str = (chip or "").lower().strip()
    if "c3" in chip_str:
        return 16000
    if "s3" in chip_str or "p4" in chip_str:
        return 24000
    return 24000


def resolve_adaptive_bitrate(requested_br: str, rssi: Optional[int] = None, chip: Optional[str] = None) -> str:
    """
    Intelligently select the optimal Opus bitrate based on requested value, ESP32 Wi-Fi RSSI, and chip profile.
    ESP32-S3 (Dual Core 240MHz, 8MB PSRAM):
      - Maksimal dibatasi pada 30k (stabil, anti putus-putus, hemat bandwidth ~40-50%, audio tetap jernih mono)
      - RSSI >= -65 dBm / Default: 30k
      - -75 to -65 dBm: 24k
      - -82 to -75 dBm: 20k
      - -88 to -82 dBm: 16k
      - < -88 dBm: 12k
    ESP32-C3 (Single Core 160MHz, No PSRAM):
      - Ladder: paling kecil 6k -> 8k -> 10k -> 14k (Maksimal untuk C3 mini: 14k)
      - RSSI >= -65 dBm: 14k (Maksimal untuk C3 mini)
      - -75 to -65 dBm: 10k
      - -85 to -75 dBm: 8k
      - < -85 dBm: 6k
      - Default jika tanpa RSSI: 10k
    """
    br_str = (requested_br or "").lower().strip()
    valid_bitrates = {"6k", "8k", "9k", "10k", "11k", "12k", "14k", "16k", "20k", "24k", "28k", "30k", "32k", "40k", "48k"}

    is_s3 = bool(chip and "s3" in chip.lower())
    is_c3 = bool(chip and "c3" in chip.lower())

    # Jika client meminta bitrate spesifik:
    if br_str and br_str != "auto":
        num_match = re.match(r"^(\d+)k?$", br_str)
        if num_match:
            val = int(num_match.group(1))
            if is_c3:
                # Maksimal untuk C3 mini adalah 14k
                if val > 14:
                    return "14k"
                elif val in (11, 12, 13):
                    return "10k"
                elif f"{val}k" in valid_bitrates:
                    return f"{val}k"
            else:
                # ESP32-S3 & chip default: batasi maksimal 30k
                if val > 30:
                    return "30k"
                elif f"{val}k" in valid_bitrates:
                    return f"{val}k"

    if is_s3:
        if rssi is not None and rssi < 0:
            if rssi >= -65:
                return "30k"
            elif rssi >= -75:
                return "24k"
            elif rssi >= -82:
                return "20k"
            elif rssi >= -88:
                return "16k"
            else:
                return "12k"
        return "30k"

    if is_c3:
        if rssi is not None and rssi < 0:
            if rssi >= -65:
                return "14k"
            elif rssi >= -75:
                return "10k"
            elif rssi >= -85:
                return "8k"
            else:
                return "6k"
        return "10k"

    if rssi is not None and rssi < 0:
        if rssi >= -65:
            return "24k"
        elif rssi >= -75:
            return "20k"
        elif rssi >= -85:
            return "16k"
        else:
            return "12k"
    return "24k"


async def _stream_opus_audio(
    video_id: str,
    source_url: str,
    bitrate: str = "11k",
    sample_rate: int = 24000,
    rssi: Optional[int] = None,
    start_sec: float = 0.0,
    user_id: Optional[int] = None,
    username: str = "",
    title: str = "",
    device_mac: str = "",
    chip: str = "",
    board: str = "",
    duration: str = "",
    duration_seconds: int = 0,
) -> AsyncGenerator[bytes, None]:
    br = resolve_adaptive_bitrate(bitrate, rssi, chip=chip)
    ffmpeg_bin = _FFMPEG_PATH or shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("FFmpeg tidak terpasang di server.")

    session = playback_tracker.start_session(
        user_id=user_id if user_id is not None else 0,
        username=username or (f"user-{user_id}" if user_id else "ESP32 Board"),
        video_id=video_id,
        title=title or f"Video {video_id}",
        stream_type="HTTP Stream",
        device_mac=device_mac or "ESP32 Board",
        bitrate=f"{br}@{sample_rate//1000}kHz",
        duration=duration,
        duration_seconds=duration_seconds,
        chip=chip or "",
        board=board or "",
        rssi=rssi,
    )

    # Immediately broadcast start of playback to all connected admin websockets
    try:
        from xiaozhi.routers.admin import broadcast_admin_users_update
        asyncio.create_task(broadcast_admin_users_update())
    except Exception:
        pass

    cmd = [
        ffmpeg_bin,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    ]
    if start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.2f}"])
    cmd.extend([
        "-i", source_url,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "libopus",
        "-b:a", br,
        "-vbr", "on",
        "-compression_level", "5",
        "-application", "audio",
        "-flush_packets", "1",
        "-frame_duration", "60",
        "-page_duration", "200000",
        "-f", "ogg",
        "pipe:1"
    ])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    if session:
        session.proc = proc

    # Safe stderr draining to prevent pipe buffer deadlock and capture error details
    stderr_lines = collections.deque(maxlen=30)

    async def _drain_stderr(pipe):
        try:
            while True:
                line = await pipe.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="ignore").strip()
                if decoded:
                    stderr_lines.append(decoded)
        except Exception:
            pass

    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))

    total_bytes = 0
    last_logged_bytes = 0
    end_reason = "finished"
    loop_count = 0
    logger.info(f"Starting YouTube stream for {video_id}, FFmpeg PID={proc.pid} (user={user_id})")

    is_c3 = bool(chip and "c3" in chip.lower())
    # Nominal bytes/sec from bitrate (e.g. 10k -> 1250 B/s, 14k -> 1750 B/s)
    br_num_match = re.match(r"^(\d+)", str(br).strip().lower())
    bitrate_kbps = int(br_num_match.group(1)) if br_num_match else (10 if is_c3 else 24)
    nominal_bps = (bitrate_kbps * 1000) // 8
    # Give 45% delivery headroom above nominal bitrate to account for Ogg page headers,
    # network jitter, and keep ESP32 ring buffer filled (anti-stutter / anti putus-putus)
    bytes_per_sec = max(1800, int(nominal_bps * 1.45))
    target_send_time = time.monotonic()

    try:
        while True:
            loop_count += 1
            if session and session.abort_event.is_set():
                logger.info(f"Stream aborted by admin for video {video_id}")
                end_reason = "aborted"
                break

            # Periodically re-check user permission (every ~25 chunks, approx every 1.5-2 seconds)
            if user_id and loop_count % 25 == 0:
                try:
                    store = get_store()
                    if hasattr(store, "get_user_features"):
                        features = store.get_user_features(user_id)
                        if not features.get("youtube_music", True):
                            logger.warning(
                                f"YouTube stream aborted for user {user_id} ({username}): feature youtube_music disabled mid-stream."
                            )
                            end_reason = "aborted"
                            if session:
                                session.trigger_abort()
                            break
                except Exception as exc:
                    logger.debug("Error checking user feature in stream loop: %s", exc)

            chunk = await proc.stdout.read(1536)
            if not chunk:
                logger.info(f"YouTube stream for {video_id} reached EOF, total={total_bytes} bytes")
                end_reason = "finished"
                break
            total_bytes += len(chunk)
            if session:
                session.record_chunk(len(chunk))
            if total_bytes - last_logged_bytes >= 76800:
                logger.info(f"YouTube stream for {video_id}: sent {total_bytes // 1024} KB")
                last_logged_bytes = total_bytes

            # Adaptive Pacing for ESP32-C3 / low RAM chips:
            # Allows initial 10 chunks pre-buffer (~15KB / ~10 detik audio) agar buffer ESP32 terisi mantap,
            # kemudian mengalirkan chunk dengan 45% delivery headroom agar buffer tidak pernah kering (anti putus-putus)
            # tanpa membanjiri RAM (anti-OOM reset).
            if is_c3 and loop_count > 10:
                chunk_duration = len(chunk) / bytes_per_sec
                target_send_time += chunk_duration
                sleep_sec = target_send_time - time.monotonic()
                if sleep_sec > 0:
                    await asyncio.sleep(min(sleep_sec, 0.4))
                elif sleep_sec < -1.5:
                    target_send_time = time.monotonic()

            yield chunk
    except (asyncio.CancelledError, GeneratorExit) as exc:
        end_reason = "cancelled"
        logger.warning(f"YouTube stream for {video_id} client disconnected or cancelled after {total_bytes} bytes")
    except Exception as exc:
        end_reason = "error"
        logger.error(f"YouTube stream for {video_id} error after {total_bytes} bytes: {exc}")
    finally:
        if session:
            playback_tracker.end_session(session.session_id, reason=end_reason, total_bytes=total_bytes)
        try:
            from xiaozhi.routers.admin import broadcast_admin_users_update
            asyncio.create_task(broadcast_admin_users_update())
        except Exception:
            pass
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

        try:
            await asyncio.wait_for(stderr_task, timeout=0.5)
        except Exception:
            stderr_task.cancel()

        # Log detail jika terjadi kendala proses transcoding FFmpeg
        if proc.returncode is not None and proc.returncode not in (0, -9, -15, 137) and end_reason != "aborted":
            err_details = "\n  ".join(stderr_lines) if stderr_lines else "(Tidak ada output stderr dari FFmpeg)"
            logger.error(
                f"Kendala transcoding FFmpeg untuk video '{video_id}' (User: {username or user_id}, PID: {proc.pid}, Exit Code: {proc.returncode}):\n  {err_details}"
            )
        elif total_bytes == 0 and end_reason == "error":
            err_details = "\n  ".join(stderr_lines) if stderr_lines else "(Tidak ada output stderr dari FFmpeg)"
            logger.error(
                f"Kendala transcoding FFmpeg sebelum streaming dimulai untuk video '{video_id}' (User: {username or user_id}):\n  {err_details}"
            )

        logger.info(f"YouTube stream for {video_id} closed, proc_returncode={proc.returncode}")


@router.get("/api/audio/stream/{video_id}")
async def audio_stream_ogg_opus(
    video_id: str,
    request: Request,
    br: str = "auto",
    chip: Optional[str] = Query(None),
    board: Optional[str] = Query(None),
    board_type: Optional[str] = Query(None),
    rssi: Optional[int] = Query(None),
    start: float = 0.0,
    owner_id: Optional[int] = Query(None),
    mac: Optional[str] = Query(None),
    token: Optional[str] = Query(None),
):
    """Real-time Ogg/Opus transcoding stream for ESP32 hardware decoder with adaptive bitrate, chip profiling, and seek resume support."""
    store = get_store()
    user, title, device_mac = _resolve_stream_user_and_info(store, video_id, request, owner_id=owner_id, mac=mac, token=token)
    user_id = user["id"] if user else None
    username = user["username"] if user else ""

    # Resolve chip from query or header
    detected_chip = (
        chip or
        request.headers.get("X-Device-Chip", "") or
        request.headers.get("Device-Chip", "") or
        request.headers.get("X-Chip", "")
    ).strip().lower()

    # Resolve board from query or header
    detected_board = (
        board or
        board_type or
        request.headers.get("X-Device-Board", "") or
        request.headers.get("Device-Board", "") or
        request.headers.get("X-Board-Type", "") or
        request.headers.get("X-Board", "") or
        request.headers.get("Board-Type", "") or
        request.headers.get("Board", "") or
        request.headers.get("X-Device-Type", "") or
        request.headers.get("Device-Type", "")
    ).strip()

    # Auto-lookup hardware profile (chip & board) from store BEFORE computing sample rate and bitrate
    if device_mac and hasattr(store, "find_device_by_mac"):
        try:
            clean_lookup_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
            clean_lookup_mac = clean_lookup_mac.strip().upper()
            dev = store.find_device_by_mac(clean_lookup_mac)
            if dev:
                d_type = (dev.get("device_type") or "").lower()
                d_name = (dev.get("device_name") or "").lower()
                if not detected_chip:
                    if "c3" in d_type or "c3" in d_name:
                        detected_chip = "esp32c3"
                    elif "s3" in d_type or "s3" in d_name:
                        detected_chip = "esp32s3"
                    elif "p4" in d_type or "p4" in d_name:
                        detected_chip = "esp32p4"
                    elif d_type:
                        detected_chip = d_type
                if not detected_board:
                    detected_board = dev.get("device_name") or dev.get("device_type") or ""
        except Exception:
            pass

    # If chip still not identified, inspect user's registered devices list
    if not detected_chip and user_id and hasattr(store, "get_user_devices"):
        try:
            for d in (store.get_user_devices(user_id) or []):
                d_type = (d.get("device_type") or "").lower()
                d_name = (d.get("device_name") or "").lower()
                if "c3" in d_type or "c3" in d_name:
                    detected_chip = "esp32c3"
                    break
                elif "s3" in d_type or "s3" in d_name:
                    detected_chip = "esp32s3"
                    break
        except Exception:
            pass

    # Read RSSI from query param or header
    if rssi is None:
        header_rssi = request.headers.get("X-WiFi-RSSI", "")
        if header_rssi:
            try:
                rssi = int(header_rssi.strip())
            except ValueError:
                rssi = None

    selected_br = resolve_adaptive_bitrate(br, rssi, chip=detected_chip)
    sample_rate = resolve_chip_audio_profile(detected_chip)

    # Check YouTube Music permission for this user
    if user_id:
        features = store.get_user_features(user_id) if hasattr(store, "get_user_features") else {}
        if not features.get("youtube_music", True):
            logger.warning(f"YouTube stream rejected for user {user_id} ({username}): feature youtube_music disabled.")
            raise HTTPException(
                status_code=403,
                detail="Anda tidak diizinkan putar lagu YouTube. Fitur YouTube Music telah dinonaktifkan oleh administrator."
            )

    logger.info(
        f"Stream request for {video_id}: chip={detected_chip or 'default'}, board={detected_board or 'default'} -> sample_rate={sample_rate}Hz, "
        f"requested_br={br}, rssi={rssi} dBm -> selected_br={selected_br}"
    )

    try:
        source_url, extracted_title = await extract_audio_url(video_id)
        if not source_url:
            raise ValueError("Direct audio stream tidak ditemukan.")
        if not title:
            title = extracted_title
        meta = get_cached_video_meta(video_id)
        dur_sec = meta.get("duration", 0)
        dur_fmt = meta.get("duration_formatted", "")
    except Exception as exc:
        logger.warning("Extraction failed for video %s: %s", video_id, exc)
        raise HTTPException(status_code=404, detail=f"Gagal mengekstrak audio YouTube: {exc}")

    return StreamingResponse(
        _stream_opus_audio(
            video_id,
            source_url=source_url,
            bitrate=selected_br,
            sample_rate=sample_rate,
            rssi=rssi,
            start_sec=start,
            user_id=user_id,
            username=username,
            title=title,
            device_mac=device_mac,
            chip=detected_chip,
            board=detected_board,
            duration=dur_fmt,
            duration_seconds=dur_sec,
        ),
        media_type="audio/ogg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Accept-Ranges": "none",
            "X-Adaptive-Bitrate": selected_br,
            "X-Adaptive-RSSI": str(rssi if rssi is not None else "N/A"),
            "X-Device-MAC": device_mac or "none",
            "X-Device-Chip": detected_chip or "unknown",
            "X-Device-Board": detected_board or detected_chip or "ESP32",
            "X-Audio-Sample-Rate": str(sample_rate),
            "X-MAC-Status": "Tersimpan OK" if device_mac else "none",
        }
    )


@router.get("/api/audio/commands/{device_id}")
async def audio_commands_for_device(device_id: str, request: Request):
    """ESP32 firmware command poll endpoint."""
    store = get_store()
    owner_id = _resolve_owner_for_device(store, device_id)
    if not owner_id:
        return {"commands": []}

    # Auto-register device MAC for owner_id only if MCP is connected
    if device_id and hasattr(store, "register_device"):
        clean_mac = device_id[6:] if device_id.lower().startswith("esp32-") else device_id
        clean_mac = clean_mac.strip().upper()
        if len(clean_mac) >= 11:
            try:
                if _can_bind_device_to_user(owner_id):
                    detected_chip = (
                        request.query_params.get("chip", "") or
                        request.headers.get("X-Device-Chip", "") or
                        request.headers.get("Device-Chip", "") or
                        request.headers.get("X-Chip", "") or
                        request.headers.get("X-Chip-Type", "")
                    ).strip().lower()
                    dev_name, dev_type = _format_chip_name_and_type(detected_chip, clean_mac)
                    store.register_device(
                        owner_id,
                        device_id=clean_mac,
                        name=dev_name,
                        device_type=dev_type,
                        notes="Tertaut saat poll audio command (MCP Terhubung)"
                    )
                elif hasattr(store, "record_device_activity"):
                    store.record_device_activity(clean_mac, owner_id)
            except Exception:
                pass

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
        device_id = request.headers.get("Device-Id", "").strip() or request.headers.get("X-Device-Mac", "").strip()

    owner_id = _resolve_owner_for_device(store, device_id)
    if owner_id:
        store.ack_audio_command(owner_id, command_id)
        if device_id and hasattr(store, "register_device"):
            clean_mac = device_id[6:] if device_id.lower().startswith("esp32-") else device_id
            clean_mac = clean_mac.strip().upper()
            if len(clean_mac) >= 11:
                try:
                    if _can_bind_device_to_user(owner_id):
                        detected_chip = ""
                        try:
                            if isinstance(body, dict):
                                detected_chip = str(body.get("chip", "")).strip().lower()
                        except Exception:
                            pass
                        if not detected_chip:
                            detected_chip = (
                                request.headers.get("X-Device-Chip", "") or
                                request.headers.get("Device-Chip", "") or
                                request.headers.get("X-Chip", "") or
                                request.headers.get("X-Chip-Type", "")
                            ).strip().lower()
                        dev_name, dev_type = _format_chip_name_and_type(detected_chip, clean_mac)
                        store.register_device(
                            owner_id,
                            device_id=clean_mac,
                            name=dev_name,
                            device_type=dev_type,
                            notes="Tertaut saat ACK audio playback (MCP Terhubung)"
                        )
                    elif hasattr(store, "record_device_activity"):
                        store.record_device_activity(clean_mac, owner_id)
                except Exception:
                    pass
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
async def audio_play_direct(
    request: Request,
    q: str = Query(""),
    mac: str = Query(""),
    token: str = Query(""),
    owner_id: Optional[int] = Query(None),
):
    """Direct search and stream for ESP32 instant on-demand playback with auto MAC binding."""
    q = (q or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query required")

    try:
        # Extract device MAC
        device_mac = (mac or "").strip()
        if not device_mac and hasattr(request, "headers"):
            device_mac = (
                request.headers.get("Device-Id", "") or
                request.headers.get("X-Device-Mac", "") or
                request.headers.get("X-MAC-Address", "") or
                request.headers.get("X-Device-Id", "") or
                request.headers.get("device_id", "") or
                request.headers.get("mac", "")
            ).strip()

        # Resolve owner first so playlist intents can be handled
        store = get_store()
        resolved_owner_id = owner_id
        if not resolved_owner_id and token:
            owner = store.find_user_by_mcp_token(token)
            if owner and owner.get("user_id"):
                resolved_owner_id = owner["user_id"]
        if not resolved_owner_id and device_mac:
            resolved_owner_id = _resolve_owner_for_device(store, device_mac)
        if not resolved_owner_id:
            try:
                su = get_current_user(request)
                if su and su.get("id"):
                    resolved_owner_id = su["id"]
            except Exception:
                pass

        vid = None
        title = None

        # Check if user requested playlist navigation (e.g. 'berikutnya di playlist', 'lagu selanjutnya', 'putar playlist')
        if resolved_owner_id and (
            re.search(r"(?i)\b(playlist|daftar\s*putar)\b", q) or
            re.search(r"(?i)\b(lagu\s+)?(berikutnya|selanjutnya|next)\b", q)
        ):
            try:
                user_tracks = store.get_user_playlist(resolved_owner_id) if hasattr(store, "get_user_playlist") else []
                if user_tracks:
                    picked_track = user_tracks[0]
                    curr = store.get_current_audio(resolved_owner_id) if hasattr(store, "get_current_audio") else None
                    if curr and curr.get("video_id"):
                        curr_vid = curr["video_id"]
                        for idx, trk in enumerate(user_tracks):
                            if trk.get("video_id") == curr_vid:
                                next_idx = (idx + 1) % len(user_tracks)
                                picked_track = user_tracks[next_idx]
                                break
                    vid = picked_track.get("video_id")
                    title = picked_track.get("title")
                    if hasattr(store, "increment_playlist_play_count") and picked_track.get("id"):
                        try:
                            store.increment_playlist_play_count(resolved_owner_id, picked_track["id"])
                        except Exception:
                            pass
                    logger.info(f"[PLAY DIRECT PLAYLIST] User {resolved_owner_id} requested playlist track: {title} ({vid})")
            except Exception as e:
                logger.warning("Failed to resolve playlist track: %s", e)

        # Standard YouTube search if not resolved from playlist
        if not vid:
            results = youtube_search(q, max_results=1)
            if not results:
                raise HTTPException(status_code=404, detail="Song not found")
            vid = results[0]["video_id"]
            title = results[0]["title"]

        clean_mac = ""
        if device_mac:
            clean_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
            clean_mac = clean_mac.strip().upper()

        base_url = str(request.base_url).rstrip("/")
        if resolved_owner_id:
            # Auto-register device MAC only if MCP is connected
            if clean_mac and len(clean_mac) >= 11 and hasattr(store, "register_device"):
                try:
                    if _can_bind_device_to_user(resolved_owner_id):
                        detected_chip = (
                            request.query_params.get("chip", "") or
                            request.headers.get("X-Device-Chip", "") or
                            request.headers.get("Device-Chip", "") or
                            request.headers.get("X-Chip", "") or
                            request.headers.get("X-Chip-Type", "")
                        ).strip().lower()
                        dev_name, dev_type = _format_chip_name_and_type(detected_chip, clean_mac)
                        store.register_device(
                            resolved_owner_id,
                            device_id=clean_mac,
                            name=dev_name,
                            device_type=dev_type,
                            notes="Tertaut saat play direct audio (MCP Terhubung)"
                        )
                        logger.info(f"[PLAY DIRECT MAC] Device {clean_mac} ({dev_type}) diikat ke user {resolved_owner_id} [MCP AKTIF]")
                    elif hasattr(store, "record_device_activity"):
                        store.record_device_activity(clean_mac, resolved_owner_id)
                        logger.warning(f"[PLAY DIRECT MAC] Board {clean_mac} TIDAK diikat ke user {resolved_owner_id} karena MCP tidak terhubung")
                except Exception as exc:
                    logger.error(f"[PLAY DIRECT MAC ERROR] {exc}")

            # Queue command so streaming and tracker recognize the user
            mac_param = f"&mac={clean_mac}" if clean_mac else ""
            stream_url = f"/api/audio/stream/{vid}?owner_id={resolved_owner_id}{mac_param}"
            full_stream = f"{base_url}{stream_url}"
            try:
                store.queue_audio_command(
                    resolved_owner_id,
                    title=title,
                    stream_url=full_stream,
                    video_url=f"https://www.youtube.com/watch?v={vid}",
                    video_id=vid
                )
            except Exception:
                pass
        else:
            mac_param = f"&mac={clean_mac}" if clean_mac else ""
            stream_url = f"/api/audio/stream/{vid}?{mac_param.lstrip('&')}" if mac_param else f"/api/audio/stream/{vid}"

        return {
            "success": True,
            "video_id": vid,
            "title": title,
            "stream_url": stream_url,
            "owner_id": resolved_owner_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/device/audio/commands")
async def device_audio_commands(
    request: Request,
    token: str = Query(""),
    mac: str = Query(""),
    chip: str = Query("")
):
    store = get_store()
    detected_chip = (
        chip or
        request.headers.get("X-Device-Chip", "") or
        request.headers.get("Device-Chip", "") or
        request.headers.get("X-Chip", "")
    ).strip().lower()

    if hasattr(store, "expire_audio_commands"):
        try:
            store.expire_audio_commands(minutes=30)
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
        device_hdr = request.headers.get("Device-Id", "") or request.headers.get("X-Device-Mac", "")
        if device_hdr:
            owner_id = _resolve_owner_for_device(store, device_hdr)

    # If owner_id is still None, check if there's a recent pending command in audio_queue created in last 2 mins
    if not owner_id and (mac or request.headers.get("Device-Id", "")):
        if hasattr(store, "find_recent_pending_audio_command"):
            try:
                recent = store.find_recent_pending_audio_command(minutes=2)
                if recent and recent.get("owner_id"):
                    owner_id = int(recent["owner_id"])
            except Exception:
                pass

    if not owner_id:
        return {"success": True, "commands": []}

    # Auto-register / update device MAC for this user only if MCP is connected
    device_mac = mac or request.headers.get("Device-Id", "") or request.headers.get("X-Device-Mac", "") or request.headers.get("X-MAC-Address", "")
    mac_saved_ok = False
    if owner_id and device_mac and hasattr(store, "register_device"):
        clean_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
        clean_mac = clean_mac.strip().upper()
        if len(clean_mac) >= 11:
            try:
                if _can_bind_device_to_user(owner_id):
                    dev_name, dev_type = _format_chip_name_and_type(detected_chip, clean_mac)
                    store.register_device(
                        owner_id,
                        device_id=clean_mac,
                        name=dev_name,
                        device_type=dev_type,
                        notes="Tertaut saat polling audio command (MCP Terhubung)"
                    )
                    mac_saved_ok = True
                    logger.info(f"[COMMAND POLL] Device MAC {clean_mac} ({dev_type}) berhasil diikat ke owner {owner_id} [MCP AKTIF]")
                else:
                    if hasattr(store, "record_device_activity"):
                        store.record_device_activity(clean_mac, owner_id)
                    logger.warning(f"[COMMAND POLL] Board MAC {clean_mac} TIDAK diikat ke owner {owner_id} karena MCP tidak terhubung")
            except Exception as exc:
                logger.error(f"[COMMAND POLL ERROR] Gagal memproses MAC {clean_mac}: {exc}")

    commands = store.get_pending_audio_commands(owner_id) if hasattr(store, "get_pending_audio_commands") else store.get_audio_commands(owner_id)
    if commands:
        latest = commands[-1]
        for old in commands[:-1]:
            try:
                store.ack_audio_command(owner_id, old["id"])
            except Exception:
                pass
        commands = [latest]

    formatted_commands = []
    base_url = str(request.base_url).rstrip("/")
    for cmd in commands:
        c = dict(cmd)
        surl = c.get("stream_url", "")
        if surl.startswith("/"):
            surl = f"{base_url}{surl}"
        sep = "&" if "?" in surl else "?"
        if "owner_id=" not in surl and owner_id:
            surl = f"{surl}{sep}owner_id={owner_id}"
            sep = "&"
        if "mac=" not in surl and device_mac:
            surl = f"{surl}{sep}mac={device_mac}"
            sep = "&"
        if "chip=" not in surl and detected_chip:
            surl = f"{surl}{sep}chip={detected_chip}"
            sep = "&"
        c["stream_url"] = surl
        formatted_commands.append(c)

    return {
        "success": True,
        "commands": formatted_commands,
        "chip": detected_chip or "unknown",
        "mac_status": "Tersimpan OK" if mac_saved_ok else None
    }


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
        device_hdr = request.headers.get("Device-Id", "") or request.headers.get("X-Device-Mac", "")
        if device_hdr:
            owner_id = _resolve_owner_for_device(store, device_hdr)

    device_mac = mac or request.headers.get("Device-Id", "") or request.headers.get("X-Device-Mac", "")
    mac_saved_ok = False
    if owner_id and device_mac and hasattr(store, "register_device"):
        clean_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
        clean_mac = clean_mac.strip().upper()
        if len(clean_mac) >= 11:
            try:
                if _can_bind_device_to_user(owner_id):
                    detected_chip = (
                        request.headers.get("X-Device-Chip", "") or
                        request.headers.get("Device-Chip", "") or
                        request.headers.get("X-Chip", "") or
                        request.headers.get("X-Chip-Type", "")
                    ).strip().lower()
                    dev_name, dev_type = _format_chip_name_and_type(detected_chip, clean_mac)
                    store.register_device(
                        owner_id,
                        device_id=clean_mac,
                        name=dev_name,
                        device_type=dev_type,
                        notes="Tertaut saat ACK audio command (MCP Terhubung)"
                    )
                    mac_saved_ok = True
                    logger.info(f"[ACK] Device MAC {clean_mac} ({dev_type}) berhasil diikat ke owner {owner_id} [MCP AKTIF]")
                else:
                    if hasattr(store, "record_device_activity"):
                        store.record_device_activity(clean_mac, owner_id)
            except Exception as exc:
                logger.error(f"[ACK ERROR] Gagal memproses MAC {clean_mac}: {exc}")

    if owner_id and command_id:
        store.ack_audio_command(owner_id, command_id)
        return {"success": True, "message": "Command acknowledged", "mac_status": "Tersimpan OK" if mac_saved_ok else None}
    return {"success": True, "mac_status": "Tersimpan OK" if mac_saved_ok else None}


@router.post("/api/device/audio/status")
async def device_audio_status(request: Request):
    """
    Handle real-time playback state events reported from ESP32 board:
    - 'playing': audio streaming/decoding active
    - 'finished': audio reached natural EOF
    - 'stopped' / 'aborted': playback interrupted by user or stop command
    - 'error': connection interrupted
    """
    store = get_store()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if not body:
        try:
            form = await request.form()
            body = dict(form)
        except Exception:
            pass

    status = str(body.get("status", "")).strip().lower()
    mac = str(body.get("mac", "") or body.get("device_id", "")).strip()
    video_id = str(body.get("video_id", "")).strip()
    token = str(body.get("token", "")).strip()

    device_mac = mac or request.headers.get("Device-Id", "") or request.headers.get("X-Device-Mac", "") or request.headers.get("X-MAC-Address", "") or request.headers.get("device_id", "")
    device_mac = device_mac.strip()

    if not status:
        return {"success": False, "error": "status parameter is required"}

    owner_id = None
    if token:
        owner = store.find_user_by_mcp_token(token)
        if owner and owner.get("user_id"):
            owner_id = owner["user_id"]

    # 1. Check audio_queue for this video_id in the last 30 minutes
    if not owner_id and video_id and hasattr(store, "find_recent_audio_command_by_video_id"):
        try:
            recent = store.find_recent_audio_command_by_video_id(video_id, minutes=30)
            if recent and recent.get("owner_id"):
                owner_id = int(recent["owner_id"])
        except Exception:
            pass

    # 2. Fallback: find active session by video_id or device_mac in playback_tracker
    if not owner_id:
        for s in playback_tracker.get_active_sessions():
            if video_id and s.get("video_id") == video_id:
                owner_id = s.get("user_id")
                break
            if device_mac and s.get("device_mac", "").upper() == device_mac.upper():
                owner_id = s.get("user_id")
                break

    # 3. Fallback: resolve from registered_devices
    if not owner_id and device_mac:
        owner_id = _resolve_owner_for_device(store, device_mac)

    # CRITICAL: Auto-register / rebind device MAC to this user in registered_devices only if MCP is connected
    mac_saved_ok = False
    clean_mac = ""
    if device_mac:
        clean_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
        clean_mac = clean_mac.strip().upper()
        if owner_id and len(clean_mac) >= 11 and hasattr(store, "register_device"):
            try:
                if _can_bind_device_to_user(owner_id):
                    detected_chip = (
                        request.headers.get("X-Device-Chip", "") or
                        request.headers.get("Device-Chip", "") or
                        request.headers.get("X-Chip", "") or
                        request.headers.get("X-Chip-Type", "")
                    ).strip().lower()
                    dev_name, dev_type = _format_chip_name_and_type(detected_chip, clean_mac)
                    store.register_device(
                        owner_id,
                        device_id=clean_mac,
                        name=dev_name,
                        device_type=dev_type,
                        notes="Tertaut saat update status perangkat (MCP Terhubung)"
                    )
                    mac_saved_ok = True
                    logger.info(f"[STATUS MAC] Device MAC {clean_mac} ({dev_type}) diikat ke user {owner_id} [MCP AKTIF]")

                    # Auto-lock board MAC ke slot MCP pengguna (Slot 1..3)
                    if hasattr(store, "list_user_xiaozhi_tokens") and hasattr(store, "bind_board_to_slot"):
                        try:
                            user_tokens = store.list_user_xiaozhi_tokens(owner_id)
                            target_slot = None
                            for t in user_tokens:
                                if t.get("board_mac") == clean_mac:
                                    target_slot = t["slot_number"]
                                    break
                            if not target_slot:
                                for t in user_tokens:
                                    if not t.get("board_mac"):
                                        target_slot = t["slot_number"]
                                        break
                            if target_slot:
                                store.bind_board_to_slot(owner_id, slot=target_slot, device_mac=clean_mac, request_id="device_status")
                        except Exception as b_exc:
                            logger.warning(f"Gagal auto-lock slot MAC status: {b_exc}")
                else:
                    if hasattr(store, "record_device_activity"):
                        store.record_device_activity(clean_mac, owner_id)
                    logger.warning(f"[STATUS MAC] Board MAC {clean_mac} TIDAK diikat ke user {owner_id} karena MCP tidak terhubung")
            except Exception as exc:
                logger.error(f"[STATUS MAC ERROR] Failed to save MAC {clean_mac}: {exc}")

    if owner_id:
        playback_tracker.handle_device_status(owner_id, status, video_id=video_id, device_mac=clean_mac)
        from xiaozhi.routers.admin import broadcast_admin_users_update
        try:
            await broadcast_admin_users_update()
        except Exception:
            pass
        return {"success": True, "owner_id": owner_id, "status": status, "device_mac": clean_mac, "mac_saved": mac_saved_ok}

    return {"success": True, "status": status, "device_mac": clean_mac, "warning": "Unmapped device"}


@router.websocket("/ws/audio/stream/{video_id}")
async def ws_audio_stream_endpoint(
    websocket: WebSocket,
    video_id: str,
    sample_rate: int = 24000,
    title: str = "",
    owner_id: Optional[int] = Query(None),
    mac: Optional[str] = Query(None),
    token: Optional[str] = Query(None)
):
    """WebSocket Opus 24kHz stream for ESP32 hardware decoder."""
    await websocket.accept()
    store = get_store()
    user, extracted_title, device_mac = _resolve_stream_user_and_info(store, video_id, websocket, owner_id=owner_id, mac=mac, token=token)
    user_id = user["id"] if user else None
    username = user["username"] if user else ""
    if not title and extracted_title:
        title = extracted_title
    try:
        await stream_video_to_websocket(
            websocket,
            video_id,
            title=title,
            sample_rate=sample_rate,
            user_id=user_id,
            username=username,
            device_mac=device_mac
        )
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
    if owner_id and device_id and hasattr(store, "register_device"):
        clean_mac = device_id[6:] if device_id.lower().startswith("esp32-") else device_id
        clean_mac = clean_mac.strip().upper()
        if len(clean_mac) >= 11:
            try:
                if _can_bind_device_to_user(owner_id):
                    detected_chip = (
                        websocket.query_params.get("chip", "") or
                        websocket.headers.get("X-Device-Chip", "") or
                        websocket.headers.get("Device-Chip", "") or
                        websocket.headers.get("X-Chip", "") or
                        websocket.headers.get("X-Chip-Type", "")
                    ).strip().lower()
                    dev_name, dev_type = _format_chip_name_and_type(detected_chip, clean_mac)
                    store.register_device(
                        owner_id,
                        device_id=clean_mac,
                        name=dev_name,
                        device_type=dev_type,
                        notes="Tertaut saat WebSocket audio channel aktif (MCP Terhubung)"
                    )
                elif hasattr(store, "record_device_activity"):
                    store.record_device_activity(clean_mac, owner_id)
            except Exception:
                pass
    user = _fetch_user(store, owner_id)
    username = user["username"] if user else f"user-{owner_id}"
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
                        await stream_video_to_websocket(
                            websocket,
                            video_id,
                            title=title,
                            sample_rate=24000,
                            user_id=owner_id,
                            username=username,
                            device_mac=device_id
                        )
            
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
