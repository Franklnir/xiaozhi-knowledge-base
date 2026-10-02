"""
Active YouTube Music Playback Tracker.
Tracks which users and devices are currently streaming/playing audio in real-time.
Thread-safe and async-safe.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xiaozhi.playback_tracker")


@dataclass
class PlaybackSession:
    session_id: str
    user_id: int
    username: str
    video_id: str
    title: str
    stream_type: str = "HTTP Stream"  # "HTTP Stream" or "WebSocket"
    device_mac: str = ""
    bitrate: str = "11k"
    started_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    bytes_streamed: int = 0
    duration: str = ""
    duration_seconds: int = 0
    rssi: Optional[int] = None
    chip: str = ""
    board: str = ""
    status: str = "streaming"  # "streaming", "buffering", "interrupted", "gap", "aborted"
    status_label: str = "Sedang Streaming"
    status_detail: str = "Mengalirkan audio real-time"
    ended_at: Optional[float] = None
    grace_until: Optional[float] = None
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    proc: Optional[Any] = None
    cancel_fn: Optional[Any] = None

    def trigger_abort(self) -> None:
        """Immediately trigger abort event, kill subprocess, and call cancel callback."""
        self.abort_event.set()
        if self.proc is not None:
            try:
                if hasattr(self.proc, "returncode") and self.proc.returncode is None:
                    self.proc.kill()
            except Exception as e:
                logger.debug("Error killing session proc %s: %s", self.session_id, e)
        if callable(self.cancel_fn):
            try:
                self.cancel_fn()
            except Exception as e:
                logger.debug("Error executing session cancel_fn %s: %s", self.session_id, e)

    def record_chunk(self, byte_count: int) -> None:
        self.bytes_streamed += byte_count
        self.last_active_at = time.time()

    @property
    def elapsed_seconds(self) -> int:
        if self.status in {"aborted", "stopped"} and self.ended_at:
            return max(0, int(self.ended_at - self.started_at))
        return max(0, int(time.time() - self.started_at))

    @property
    def elapsed_formatted(self) -> str:
        s = self.elapsed_seconds
        mins, secs = divmod(s, 60)
        return f"{mins:02d}:{secs:02d}"

    @property
    def duration_formatted(self) -> str:
        if not self.duration and self.duration_seconds <= 0:
            return ""
        if self.duration_seconds > 0:
            dm, ds = divmod(self.duration_seconds, 60)
            return f"{dm:02d}:{ds:02d}"
        d_str = str(self.duration).strip()
        if ":" in d_str:
            return d_str
        if d_str.isdigit():
            dm, ds = divmod(int(d_str), 60)
            return f"{dm:02d}:{ds:02d}"
        return d_str

    @property
    def progress_percent(self) -> int:
        if self.duration_seconds > 0:
            pct = int((self.elapsed_seconds / self.duration_seconds) * 100)
            return max(0, min(100, pct))
        return 0

    @property
    def rssi_label(self) -> str:
        if self.rssi is None or self.rssi == 0:
            return "Tidak Ada Info Sinyal"
        r = self.rssi
        if r >= -60:
            return f"{r} dBm (Sangat Kuat 🟢)"
        elif r >= -75:
            return f"{r} dBm (Bagus 🟡)"
        elif r >= -85:
            return f"{r} dBm (Lemah 🟠)"
        else:
            return f"{r} dBm (Sangat Jelek 🔴)"

    @property
    def chip_display(self) -> str:
        c = (self.chip or "").strip().lower()
        if "s3" in c:
            return "ESP32-S3"
        elif "c3" in c:
            return "ESP32-C3"
        elif "p4" in c:
            return "ESP32-P4"
        elif "esp32" in c:
            return "ESP32 Standard"
        elif c:
            return c.upper()
        return "ESP32"

    @property
    def board_display(self) -> str:
        b = (self.board or "").strip()
        c = (self.chip_display or "").strip()
        if b:
            b_norm = b.replace("_", "-").strip()
            b_low = b_norm.lower()
            if b_low in ("esp32-s3", "esp32s3", "esp-s3", "s3"):
                return "ESP32-S3"
            elif b_low in ("esp32-c3", "esp32c3", "esp-c3", "c3"):
                return "ESP32-C3"
            elif b_low in ("esp32-p4", "esp32p4", "esp-p4", "p4"):
                return "ESP32-P4"
            elif b_low in ("esp32-s2", "esp32s2", "esp-s2", "s2"):
                return "ESP32-S2"
            elif b_low in ("esp32", "esp-32"):
                return "ESP32 Standard"
            if c and c.lower() not in b_low:
                return f"{c} ({b_norm})"
            return b_norm
        return c or "ESP32"

    @property
    def bytes_formatted(self) -> str:
        if self.bytes_streamed < 1048576:
            return f"{self.bytes_streamed // 1024} KB"
        return f"{self.bytes_streamed / 1048576:.1f} MB"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "username": self.username,
            "video_id": self.video_id,
            "title": self.title,
            "stream_type": self.stream_type,
            "device_mac": self.device_mac,
            "bitrate": self.bitrate,
            "started_at": self.started_at,
            "elapsed_seconds": self.elapsed_seconds,
            "elapsed_formatted": self.elapsed_formatted,
            "duration": self.duration_formatted,
            "duration_seconds": self.duration_seconds,
            "progress_percent": self.progress_percent,
            "bytes_streamed": self.bytes_streamed,
            "bytes_formatted": f"{self.bytes_streamed // 1024} KB" if self.bytes_streamed < 1048576 else f"{self.bytes_streamed / 1048576:.1f} MB",
            "thumbnail_url": f"https://img.youtube.com/vi/{self.video_id}/mqdefault.jpg" if self.video_id else "",
            "video_url": f"https://www.youtube.com/watch?v={self.video_id}" if self.video_id else "",
            "rssi": self.rssi,
            "rssi_label": self.rssi_label,
            "chip": self.chip_display,
            "board": self.board or self.chip_display,
            "board_display": self.board_display,
            "status": self.status,
            "status_label": self.status_label,
            "status_detail": self.status_detail,
            "grace_until": self.grace_until,
        }


class PlaybackTracker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, PlaybackSession] = {}
        # Mapping user_id -> active session_id (browser / single-board fallback)
        self._user_sessions: Dict[int, str] = {}
        # Mapping clean_mac -> active session_id (allows multi-board concurrent playback)
        self._device_sessions: Dict[str, str] = {}
        # Mapping user_id -> last played session info
        self._last_played: Dict[int, Dict[str, Any]] = {}

    def start_session(
        self,
        user_id: int,
        username: str,
        video_id: str,
        title: str,
        stream_type: str = "HTTP Stream",
        device_mac: str = "",
        bitrate: str = "11k",
        duration: str = "",
        duration_seconds: int = 0,
        chip: str = "",
        board: str = "",
        rssi: Optional[int] = None,
    ) -> PlaybackSession:
        with self._lock:
            clean_mac = (device_mac or "").strip().upper()
            if clean_mac.startswith("ESP32-"):
                clean_mac = clean_mac[6:]

            # Multi-device concurrent playback support:
            # 1. If device_mac is provided, replace only the previous session for THIS specific device
            #    (so same user can play different songs on Board 1, Board 2, etc. simultaneously)
            # 2. If no device_mac (e.g. browser test), replace previous session for this user
            old_sid = None
            if clean_mac and len(clean_mac) >= 11:
                old_sid = self._device_sessions.get(clean_mac)
            elif user_id and user_id > 0:
                old_sid = self._user_sessions.get(user_id)

            if old_sid and old_sid in self._sessions:
                old_session = self._sessions.pop(old_sid, None)
                if old_session:
                    old_session.trigger_abort()
                    self._last_played[user_id] = {
                        **old_session.to_dict(),
                        "ended_at": time.time(),
                        "end_reason": "replaced"
                    }

            # Parse duration_seconds if not provided
            if not duration_seconds and duration:
                if ":" in duration:
                    parts = duration.split(":")
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        duration_seconds = int(parts[0]) * 60 + int(parts[1])
                elif str(duration).isdigit():
                    duration_seconds = int(duration)

            # Auto-lookup board if not explicitly passed
            if not board and device_mac:
                try:
                    from xiaozhi.dependencies import get_store
                    st = get_store()
                    clean_lookup_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
                    clean_lookup_mac = clean_lookup_mac.strip().upper()
                    if hasattr(st, "find_device_by_mac"):
                        dev = st.find_device_by_mac(clean_lookup_mac)
                        if dev:
                            board = dev.get("device_name") or dev.get("device_type") or ""
                except Exception:
                    pass

            session_id = f"play_{user_id}_{int(time.time())}_{video_id[:8]}"
            session = PlaybackSession(
                session_id=session_id,
                user_id=user_id,
                username=username,
                video_id=video_id,
                title=title or f"Video {video_id}",
                stream_type=stream_type,
                device_mac=device_mac,
                bitrate=bitrate,
                duration=duration,
                duration_seconds=duration_seconds,
                chip=chip or "",
                board=board or "",
                rssi=rssi,
                status="streaming",
                status_label="Sedang Streaming",
                status_detail=f"Mengirim audio real-time ({bitrate})",
            )
            self._sessions[session_id] = session
            if clean_mac and len(clean_mac) >= 11:
                self._device_sessions[clean_mac] = session_id
            if user_id and user_id > 0:
                self._user_sessions[user_id] = session_id
            logger.info("Playback session started: %s for user %s (%s, device: %s)", session_id, user_id, title, clean_mac or "none")

            # Permanently persist device_mac to database so it never disappears after stream ends (validasi MCP aktif)
            if device_mac and user_id:
                try:
                    from xiaozhi.dependencies import get_store
                    from xiaozhi.services.mcp_service import is_mcp_connected
                    st = get_store()
                    clean_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
                    clean_mac = clean_mac.strip().upper()
                    if len(clean_mac) >= 11:
                        if hasattr(st, "register_device") and is_mcp_connected(int(user_id)):
                            c = (chip or "").lower().replace("-", "").strip()
                            if "s3" in c:
                                dev_name, dev_type = f"ESP32-S3 ({clean_mac[-5:]})", "esp32-s3"
                            elif "c3" in c:
                                dev_name, dev_type = f"ESP32-C3 ({clean_mac[-5:]})", "esp32-c3"
                            elif "p4" in c:
                                dev_name, dev_type = f"ESP32-P4 ({clean_mac[-5:]})", "esp32-p4"
                            else:
                                dev_name, dev_type = f"ESP32 ({clean_mac[-5:]})", "esp32"
                            st.register_device(
                                user_id,
                                device_id=clean_mac,
                                name=dev_name,
                                device_type=dev_type,
                                notes="Tertaut saat sesi playback lagu (MCP Terhubung)"
                            )
                        elif hasattr(st, "record_device_activity"):
                            st.record_device_activity(clean_mac, int(user_id))
                except Exception as e:
                    logger.debug("Could not auto-register device_mac in tracker: %s", e)

            try:
                from xiaozhi.services.sse_service import log_admin_event
                log_admin_event("audio", f"Mulai audio stream: '{title[:40]}' (User: {username or user_id})", {
                    "session_id": session_id,
                    "user_id": user_id,
                    "title": title,
                    "device_mac": device_mac,
                })
            except Exception:
                pass

            return session

    def end_session(self, session_id: str, reason: str = "finished", total_bytes: int = 0) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return

            now = time.time()
            session.ended_at = now
            if total_bytes > 0:
                session.bytes_streamed = max(session.bytes_streamed, total_bytes)

            reason_lower = str(reason).strip().lower()

            if reason_lower in {"aborted", "stopped"}:
                # Forcefully stopped by user or admin -> drop immediately
                self._sessions.pop(session_id, None)
                if self._user_sessions.get(session.user_id) == session_id:
                    self._user_sessions.pop(session.user_id, None)
                if session.device_mac:
                    c_mac = session.device_mac[6:] if session.device_mac.startswith("ESP32-") else session.device_mac
                    c_mac = c_mac.strip().upper()
                    if self._device_sessions.get(c_mac) == session_id:
                        self._device_sessions.pop(c_mac, None)
                self._last_played[session.user_id] = {
                    **session.to_dict(),
                    "ended_at": now,
                    "end_reason": "aborted"
                }
                logger.info("Playback session aborted: %s for user %s", session_id, session.user_id)
                try:
                    from xiaozhi.services.sse_service import log_admin_event
                    log_admin_event("audio", f"Audio stream dihentikan: '{session.title[:40]}' (User: {session.username or session.user_id})", {
                        "session_id": session_id,
                        "user_id": session.user_id,
                        "title": session.title,
                    })
                except Exception:
                    pass
                return

            elif reason_lower in {"stream_eof", "finished"}:
                # Server sent 100% of audio stream to ESP32!
                # ESP32 is still playing audio out of its internal ring buffer.
                session.status = "buffering"
                session.status_label = "Memutar Buffer (100% Terkirim)"
                session.status_detail = f"Download 100% selesai ({session.bytes_formatted}). Speaker ESP32 memutar sisa buffer."
                grace_dur = 45.0
                if session.duration_seconds > 0:
                    remaining = session.duration_seconds - session.elapsed_seconds
                    if remaining > 0:
                        grace_dur = max(30.0, min(remaining + 10.0, 90.0))
                session.grace_until = now + grace_dur
                logger.info("Playback session download finished, entering buffering grace: %s (grace: %.1fs)", session_id, grace_dur)

            elif reason_lower in {"cancelled", "disconnect", "reset"}:
                # Wi-Fi dropped momentarily or reconnecting
                session.status = "interrupted"
                session.status_label = "Sinyal Terputus (Reconnecting)"
                session.status_detail = f"⚠️ Sinyal Wi-Fi ESP32 terputus ({session.rssi_label}). Menunggu auto-reconnect..."
                session.grace_until = now + 35.0
                logger.warning("Playback session interrupted by Wi-Fi drop: %s (grace: 35s)", session_id)

            elif reason_lower == "gap":
                # Inter-track gap in playlist
                session.status = "gap"
                session.status_label = "Jeda Transisi Antar Lagu"
                session.status_detail = "Lagu selesai, jeda transisi ke track berikutnya..."
                session.grace_until = now + 30.0
                logger.info("Playback session entering track gap: %s", session_id)

            elif reason_lower in {"idle", "device_finished"}:
                # ESP32 hardware itself reported that playback has fully stopped
                self._sessions.pop(session_id, None)
                if self._user_sessions.get(session.user_id) == session_id:
                    self._user_sessions.pop(session.user_id, None)
                self._last_played[session.user_id] = {
                    **session.to_dict(),
                    "ended_at": now,
                    "end_reason": reason_lower
                }
                logger.info("Playback session ended by device status report: %s for user %s", session_id, session.user_id)
                return

            else:
                session.status = "gap"
                session.status_label = "Selesai / Transisi"
                session.status_detail = f"Aliran data selesai ({reason})."
                session.grace_until = now + 25.0

    def stop_session(self, session_id: str) -> bool:
        """Force stop a session by triggering abort, killing FFmpeg proc, and clearing session."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.trigger_abort()
                self.end_session(session_id, reason="aborted")
                logger.info("Playback session abort requested by admin: %s", session_id)
                return True
            return False

    def stop_device_playback(self, device_mac: str) -> bool:
        """Stop any active playback session matching a device MAC address."""
        if not device_mac:
            return False
        clean_mac = str(device_mac).replace(":", "").replace("-", "").strip().upper()
        if clean_mac.startswith("ESP32"):
            clean_mac = clean_mac[5:]
        stopped = False
        with self._lock:
            # 1. Direct device index lookup
            sid = self._device_sessions.get(clean_mac)
            if sid and sid in self._sessions:
                s = self._sessions[sid]
                s.trigger_abort()
                self.end_session(sid, reason="aborted")
                stopped = True
            # 2. Iterate all active sessions to catch partial or formatted MAC matches
            for s in list(self._sessions.values()):
                s_mac = (s.device_mac or "").replace(":", "").replace("-", "").strip().upper()
                if s_mac.startswith("ESP32"):
                    s_mac = s_mac[5:]
                if s_mac and (s_mac == clean_mac or clean_mac.endswith(s_mac) or s_mac.endswith(clean_mac)):
                    s.trigger_abort()
                    self.end_session(s.session_id, reason="aborted")
                    stopped = True
        return stopped

    def stop_user_playback(self, user_id: int) -> bool:
        """Stop playback for a user by user_id and all associated device MAC addresses."""
        stopped = False
        with self._lock:
            sid = self._user_sessions.get(user_id)
            if sid and sid in self._sessions:
                session = self._sessions[sid]
                session.trigger_abort()
                self.end_session(sid, reason="aborted")
                logger.info("Playback session abort requested by user_id %s: %s", user_id, sid)
                stopped = True
            # Also check if any session has this user_id
            for s in list(self._sessions.values()):
                if s.user_id == user_id:
                    s.trigger_abort()
                    self.end_session(s.session_id, reason="aborted")
                    logger.info("Playback session abort requested by user_id %s: %s", user_id, s.session_id)
                    stopped = True

        # Also find and abort sessions for devices belonging to this user (handles user_id=0 sessions on user hardware)
        try:
            from xiaozhi.dependencies import get_store
            store = get_store()
            user_macs = set()
            if hasattr(store, "get_user_mac_address"):
                m = store.get_user_mac_address(user_id)
                if m:
                    user_macs.add(m)
            if hasattr(store, "get_user_mac_addresses"):
                for m in (store.get_user_mac_addresses(user_id) or []):
                    if m:
                        user_macs.add(m)
            if hasattr(store, "get_user_devices"):
                for d in (store.get_user_devices(user_id) or []):
                    mac = d.get("device_id") or d.get("mac")
                    if mac:
                        user_macs.add(mac)

            for mac in user_macs:
                if self.stop_device_playback(mac):
                    stopped = True
                    logger.info("Playback session on user device %s aborted for user_id %s", mac, user_id)
        except Exception as exc:
            logger.debug("Could not lookup user devices for playback abort: %s", exc)

        return stopped

    def get_active_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            now = time.time()
            to_remove = []
            for sid, s in self._sessions.items():
                if s.status != "streaming" and s.grace_until and now >= s.grace_until:
                    to_remove.append(sid)
                elif s.status == "streaming" and now - s.last_active_at > 600:
                    to_remove.append(sid)

            for sid in to_remove:
                s = self._sessions.pop(sid, None)
                if s:
                    if self._user_sessions.get(s.user_id) == sid:
                        self._user_sessions.pop(s.user_id, None)
                    if s.device_mac:
                        c_mac = s.device_mac[6:] if s.device_mac.startswith("ESP32-") else s.device_mac
                        c_mac = c_mac.strip().upper()
                        if self._device_sessions.get(c_mac) == sid:
                            self._device_sessions.pop(c_mac, None)
                    self._last_played[s.user_id] = {
                        **s.to_dict(),
                        "ended_at": s.ended_at or now,
                        "end_reason": s.status
                    }

            status_order = {"streaming": 0, "buffering": 1, "interrupted": 2, "gap": 3}
            sessions_list = list(self._sessions.values())
            sessions_list.sort(key=lambda s: (status_order.get(s.status, 9), -s.started_at))
            return [s.to_dict() for s in sessions_list]

    def get_user_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            sid = self._user_sessions.get(user_id)
            if sid and sid in self._sessions:
                return self._sessions[sid].to_dict()
            return None

    def is_user_playing(self, user_id: int) -> bool:
        with self._lock:
            sid = self._user_sessions.get(user_id)
            return bool(sid and sid in self._sessions)

    def get_session_by_user(self, user_id: int) -> Optional[PlaybackSession]:
        with self._lock:
            sid = self._user_sessions.get(user_id)
            if sid and sid in self._sessions:
                return self._sessions[sid]
            return None

    def get_session_by_mac(self, mac: str) -> Optional[PlaybackSession]:
        if not mac:
            return None
        target = str(mac).replace(":", "").replace("-", "").strip().lower()
        if not target or target.startswith("esp32board"):
            return None
        with self._lock:
            for s in self._sessions.values():
                if s.device_mac:
                    s_clean = str(s.device_mac).replace(":", "").replace("-", "").strip().lower()
                    if s_clean == target or s_clean.endswith(target) or target.endswith(s_clean):
                        return s
            return None

    def get_playback_status(self, user_id: int) -> Dict[str, Any]:
        """Get comprehensive playback status for AI tools and user queries."""
        with self._lock:
            active = self.get_user_session(user_id)
            if active:
                return {
                    "is_playing": True,
                    "status": "playing",
                    "title": active.get("title", ""),
                    "video_id": active.get("video_id", ""),
                    "elapsed_seconds": active.get("elapsed_seconds", 0),
                    "elapsed_formatted": active.get("elapsed_formatted", "00:00"),
                    "duration": active.get("duration", ""),
                    "bitrate": active.get("bitrate", "11k"),
                    "device_mac": active.get("device_mac", ""),
                    "message": f"Saat ini sedang memutar lagu '{active.get('title', '')}' (berjalan selama {active.get('elapsed_formatted', '00:00')}).",
                }

            last = self._last_played.get(user_id)
            if last:
                ended_at = last.get("ended_at", time.time())
                elapsed_mins = max(0, int((time.time() - ended_at) // 60))
                ago_str = "baru saja" if elapsed_mins < 1 else f"{elapsed_mins} menit yang lalu"
                end_reason = last.get("end_reason", "finished")
                reason_str = "dihentikan pengguna" if end_reason in {"stopped", "aborted"} else "selesai diputar"
                return {
                    "is_playing": False,
                    "status": end_reason,
                    "title": last.get("title", ""),
                    "video_id": last.get("video_id", ""),
                    "last_played_at": ended_at,
                    "end_reason": end_reason,
                    "message": f"Saat ini tidak ada lagu yang diputar. Lagu terakhir '{last.get('title', '')}' telah {reason_str} ({ago_str}).",
                }

            return {
                "is_playing": False,
                "status": "idle",
                "title": "",
                "video_id": "",
                "message": "Saat ini tidak ada lagu yang sedang atau baru saja diputar di perangkat Anda.",
            }

    def handle_device_status(self, user_id: int, status: str, video_id: str = "", device_mac: str = "") -> bool:
        """Handle playback status reported directly from ESP32 board."""
        with self._lock:
            status_lower = status.strip().lower()
            sid = self._user_sessions.get(user_id)
            clean_mac = ""
            if device_mac:
                clean_mac = device_mac[6:] if device_mac.lower().startswith("esp32-") else device_mac
                clean_mac = clean_mac.strip().upper()
                if len(clean_mac) >= 11:
                    try:
                        from xiaozhi.dependencies import get_store
                        from xiaozhi.services.mcp_service import is_mcp_connected
                        st = get_store()
                        if hasattr(st, "register_device") and is_mcp_connected(int(user_id)):
                            st.register_device(
                                user_id,
                                device_id=clean_mac,
                                name=f"ESP32 ({clean_mac[-5:]})",
                                device_type="esp32",
                                notes="Tertaut saat update status playback (MCP Terhubung)"
                            )
                        elif hasattr(st, "record_device_activity"):
                            st.record_device_activity(clean_mac, int(user_id))
                    except Exception:
                        pass

            if status_lower in {"finished", "stopped", "aborted", "idle"}:
                if sid and sid in self._sessions:
                    if clean_mac:
                        self._sessions[sid].device_mac = clean_mac
                    self.end_session(sid, reason="device_finished" if status_lower == "finished" else status_lower)
                    logger.info("Device reported %s for user %s (mac: %s), session %s ended", status_lower, user_id, clean_mac, sid)
                    return True
                elif user_id in self._last_played:
                    self._last_played[user_id]["end_reason"] = status_lower
                    if clean_mac:
                        self._last_played[user_id]["device_mac"] = clean_mac
                    return True
            elif status_lower == "playing":
                if sid and sid in self._sessions:
                    self._sessions[sid].last_active_at = time.time()
                    if clean_mac:
                        self._sessions[sid].device_mac = clean_mac
                    return True
            return False


# Global singleton instance
playback_tracker = PlaybackTracker()
