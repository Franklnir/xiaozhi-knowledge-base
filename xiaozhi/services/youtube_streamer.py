"""
Server-Side Audio Injection Streamer for ESP32-C3.
Extracts YouTube audio, transcodes to Opus Mono 24kHz (60ms frames),
demuxes Ogg container on the server, and streams raw Opus frames via WebSocket.
"""

import asyncio
import logging
import os
import shutil
import time
from typing import AsyncGenerator, Optional

logger = logging.getLogger("xiaozhi.youtube_streamer")

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


def get_ffmpeg_binary() -> Optional[str]:
    return _FFMPEG_PATH or shutil.which("ffmpeg")


async def extract_audio_url(video_id: str) -> tuple[Optional[str], str]:
    if not yt_dlp:
        raise RuntimeError("yt_dlp tidak tersedia di server.")

    loop = asyncio.get_running_loop()

    def _extract():
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "extractaudio": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            return info.get("url"), info.get("title", "")

    return await loop.run_in_executor(None, _extract)


async def demux_ogg_opus(proc_stdout) -> AsyncGenerator[bytes, None]:
    """
    Demux raw Opus packets from an FFmpeg Ogg bitstream.
    Discards Ogg header pages (OpusHead, OpusTags) and yields pure raw Opus frames.
    """
    buffer = bytearray()
    header_count = 0
    curr_packet = bytearray()

    while True:
        chunk = await proc_stdout.read(4096)
        if not chunk:
            break
        buffer.extend(chunk)

        while len(buffer) >= 27:
            pos = buffer.find(b"OggS")
            if pos < 0:
                buffer.clear()
                break
            if pos > 0:
                del buffer[:pos]
                if len(buffer) < 27:
                    break

            num_segs = buffer[26]
            hdr_size = 27 + num_segs
            if len(buffer) < hdr_size:
                break

            seg_table = buffer[27:hdr_size]
            body_size = sum(seg_table)
            page_total_size = hdr_size + body_size
            if len(buffer) < page_total_size:
                break

            page_body = buffer[hdr_size:page_total_size]
            del buffer[:page_total_size]

            offset = 0
            for seg_len in seg_table:
                curr_packet.extend(page_body[offset:offset + seg_len])
                offset += seg_len
                if seg_len < 255:
                    if header_count < 2:
                        header_count += 1
                    else:
                        yield bytes(curr_packet)
                    curr_packet.clear()


async def stream_video_to_websocket(websocket, video_id: str, title: str = "", sample_rate: int = 24000):
    """
    Streams a YouTube video as paced Opus frames over a WebSocket connection.
    Formatted to XiaoZhi BinaryProtocol3 / standard audio frames.
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        await websocket.send_json({"type": "error", "message": "FFmpeg tidak terpasang di server."})
        return

    try:
        source_url, extracted_title = await extract_audio_url(video_id)
        if not title:
            title = extracted_title
    except Exception as exc:
        logger.error("Gagal ekstrak direct audio URL: %s", exc)
        await websocket.send_json({"type": "error", "message": f"Gagal ekstrak audio: {exc}"})
        return

    cmd = [
        ffmpeg_bin,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", source_url,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "libopus",
        "-b:a", "12k",
        "-vbr", "on",
        "-compression_level", "5",
        "-application", "voip",
        "-flush_packets", "1",
        "-frame_duration", "60",
        "-f", "ogg",
        "pipe:1"
    ]

    logger.info("Mulai FFmpeg Opus stream untuk %s (%s) @ %d Hz", video_id, title, sample_rate)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )

    abort_event = asyncio.Event()

    async def _listen_client():
        try:
            while not abort_event.is_set():
                data = await websocket.receive_text()
                if "abort" in data.lower() or "stop" in data.lower():
                    logger.info("Client meminta abort playback")
                    abort_event.set()
                    break
        except Exception:
            abort_event.set()

    listen_task = asyncio.create_task(_listen_client())

    try:
        # 1. Kirim state start
        await websocket.send_json({"type": "tts", "state": "start"})
        if title:
            await websocket.send_json({"type": "tts", "state": "sentence_start", "text": f"🎵 {title}"})

        # 2. Pacing loop (60ms frame = 0.06s)
        frame_idx = 0
        target_time = time.monotonic()

        async for packet in demux_ogg_opus(proc.stdout):
            if abort_event.is_set():
                logger.info("Stream dihentikan oleh user abort.")
                break

            # Pack ke XiaoZhi BinaryProtocol3:
            # byte 0: type = 0 (audio)
            # byte 1: reserved = 0
            # byte 2-3: payload size (uint16 BE)
            # bytes 4+: raw opus payload
            payload_len = len(packet)
            binary_frame = bytearray(4 + payload_len)
            binary_frame[0] = 0x00
            binary_frame[1] = 0x00
            binary_frame[2] = (payload_len >> 8) & 0xFF
            binary_frame[3] = payload_len & 0xFF
            binary_frame[4:] = packet

            await websocket.send_bytes(bytes(binary_frame))
            frame_idx += 1

            # Pacing: Pre-buffer 8 frames (480ms), lalu kirim tepat setiap 60ms
            if frame_idx > 8:
                target_time += 0.060
                sleep_duration = target_time - time.monotonic()
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)
                elif sleep_duration < -0.300:
                    # Reset clock jika terjadi lag besar
                    target_time = time.monotonic()

        # 3. Kirim state stop saat selesai
        if not abort_event.is_set():
            await websocket.send_json({"type": "tts", "state": "stop"})

    except (asyncio.CancelledError, GeneratorExit):
        pass
    except Exception as exc:
        logger.error("Error saat streaming audio: %s", exc)
    finally:
        listen_task.cancel()
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        logger.info("FFmpeg stream selesai untuk %s (%d frame)", video_id, frame_idx)
