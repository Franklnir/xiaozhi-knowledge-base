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
    abort_event: asyncio.Event = field(default_factory=asyncio.Event)

    def record_chunk(self, byte_count: int) -> None:
        self.bytes_streamed += byte_count
        self.last_active_at = time.time()

    @property
    def elapsed_seconds(self) -> int:
        return max(0, int(time.time() - self.started_at))

    @property
    def elapsed_formatted(self) -> str:
        s = self.elapsed_seconds
        mins, secs = divmod(s, 60)
        return f"{mins:02d}:{secs:02d}"

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
            "duration": self.duration,
            "bytes_streamed": self.bytes_streamed,
            "bytes_formatted": f"{self.bytes_streamed // 1024} KB" if self.bytes_streamed < 1048576 else f"{self.bytes_streamed / 1048576:.1f} MB",
            "thumbnail_url": f"https://img.youtube.com/vi/{self.video_id}/mqdefault.jpg" if self.video_id else "",
            "video_url": f"https://www.youtube.com/watch?v={self.video_id}" if self.video_id else "",
            "status": "Memutar",
        }


class PlaybackTracker:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, PlaybackSession] = {}
        # Mapping user_id -> active session_id
        self._user_sessions: Dict[int, str] = {}
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
    ) -> PlaybackSession:
        with self._lock:
            # End any existing session for this user if active
            old_sid = self._user_sessions.get(user_id)
            if old_sid and old_sid in self._sessions:
                old_session = self._sessions.pop(old_sid, None)
                if old_session:
                    old_session.abort_event.set()
                    self._last_played[user_id] = {
                        **old_session.to_dict(),
                        "ended_at": time.time(),
                        "end_reason": "replaced"
                    }

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
            )
            self._sessions[session_id] = session
            self._user_sessions[user_id] = session_id
            logger.info("Playback session started: %s for user %s (%s)", session_id, user_id, title)
            return session

    def end_session(self, session_id: str, reason: str = "finished") -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                if self._user_sessions.get(session.user_id) == session_id:
                    self._user_sessions.pop(session.user_id, None)
                self._last_played[session.user_id] = {
                    **session.to_dict(),
                    "ended_at": time.time(),
                    "end_reason": reason
                }
                logger.info("Playback session ended (%s): %s for user %s", reason, session_id, session.user_id)

    def stop_session(self, session_id: str) -> bool:
        """Force stop a session by setting its abort event."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.abort_event.set()
                logger.info("Playback session abort requested by admin: %s", session_id)
                return True
            return False

    def stop_user_playback(self, user_id: int) -> bool:
        with self._lock:
            sid = self._user_sessions.get(user_id)
            if sid and sid in self._sessions:
                session = self._sessions[sid]
                session.abort_event.set()
                logger.info("Playback session abort requested by user_id %s: %s", user_id, sid)
                return True
            # Also check if any session has this user_id
            for s in list(self._sessions.values()):
                if s.user_id == user_id:
                    s.abort_event.set()
                    logger.info("Playback session abort requested by user_id %s: %s", user_id, s.session_id)
                    return True
            return False

    def get_active_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            # Prune any sessions inactive for > 10 minutes without update
            now = time.time()
            stale = [sid for sid, s in self._sessions.items() if now - s.last_active_at > 600]
            for sid in stale:
                s = self._sessions.pop(sid, None)
                if s and self._user_sessions.get(s.user_id) == sid:
                    self._user_sessions.pop(s.user_id, None)

            return [s.to_dict() for s in self._sessions.values()]

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

    def handle_device_status(self, user_id: int, status: str, video_id: str = "") -> bool:
        """Handle playback status reported directly from ESP32 board."""
        with self._lock:
            status_lower = status.strip().lower()
            sid = self._user_sessions.get(user_id)
            if status_lower in {"finished", "stopped", "aborted", "idle"}:
                if sid and sid in self._sessions:
                    self.end_session(sid, reason=status_lower)
                    logger.info("Device reported %s for user %s, session %s ended", status_lower, user_id, sid)
                    return True
                elif user_id in self._last_played:
                    self._last_played[user_id]["end_reason"] = status_lower
                    return True
            elif status_lower == "playing":
                if sid and sid in self._sessions:
                    self._sessions[sid].last_active_at = time.time()
                    return True
            return False


# Global singleton instance
playback_tracker = PlaybackTracker()
