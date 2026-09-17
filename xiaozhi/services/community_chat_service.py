"""
Community Chat Hub and Voice Note Service.
Handles WebSocket broadcasting and voice note storage for interactive community chat.
"""
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set
from fastapi import WebSocket

logger = logging.getLogger("xiaozhi.community_chat")

# Storage directory for voice notes
VOICE_NOTES_DIR = Path(os.getenv("VOICE_NOTES_DIR", "data/voice_notes"))
VOICE_NOTES_DIR.mkdir(parents=True, exist_ok=True)


class CommunityChatHub:
    """Manages active WebSocket connections and broadcasts real-time chat events."""

    def __init__(self) -> None:
        self._active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Register a new active WebSocket connection."""
        await websocket.accept()
        self._active_connections.add(websocket)
        logger.info("Community chat WebSocket client connected. Active: %d", len(self._active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a disconnected WebSocket."""
        self._active_connections.discard(websocket)
        logger.info("Community chat WebSocket client disconnected. Active: %d", len(self._active_connections))

    async def broadcast_new_message(self, message: Dict[str, Any]) -> None:
        """Broadcast new message payload to all connected clients."""
        if not self._active_connections:
            return

        payload = {
            "type": "new_message",
            "message": message,
        }
        text_data = json.dumps(payload, ensure_ascii=False)
        stale_connections = []

        for ws in list(self._active_connections):
            try:
                await ws.send_text(text_data)
            except Exception as e:
                logger.debug("Error broadcasting to community chat client: %s", e)
                stale_connections.append(ws)

        for ws in stale_connections:
            self._active_connections.discard(ws)

    async def broadcast_delete_message(self, message_id: int) -> None:
        """Broadcast delete event to all connected clients."""
        if not self._active_connections:
            return

        payload = {
            "type": "delete_message",
            "message_id": message_id,
        }
        text_data = json.dumps(payload, ensure_ascii=False)
        stale_connections = []

        for ws in list(self._active_connections):
            try:
                await ws.send_text(text_data)
            except Exception as e:
                logger.debug("Error broadcasting delete to client: %s", e)
                stale_connections.append(ws)

        for ws in stale_connections:
            self._active_connections.discard(ws)

    @property
    def online_count(self) -> int:
        return len(self._active_connections)


# Global singleton hub
chat_hub = CommunityChatHub()


def save_voice_note(user_id: int, file_bytes: bytes, extension: str = "webm") -> str:
    """
    Save voice note binary data into VOICE_NOTES_DIR with a randomized safe filename.
    Returns the saved filename.
    """
    safe_ext = extension.lstrip(".").lower()
    if safe_ext not in {"webm", "ogg", "mp3", "wav", "m4a", "aac"}:
        safe_ext = "webm"

    rand_token = secrets.token_hex(6)
    filename = f"vn_{user_id}_{int(time.time())}_{rand_token}.{safe_ext}"
    target_path = VOICE_NOTES_DIR / filename
    with open(target_path, "wb") as f:
        f.write(file_bytes)

    logger.info("Saved voice note for user %s to %s (%d bytes)", user_id, filename, len(file_bytes))
    return filename


def get_voice_note_path(filename: str) -> Optional[Path]:
    """Resolve and validate voice note file path safely (preventing path traversal)."""
    clean_name = os.path.basename(filename)
    target = VOICE_NOTES_DIR / clean_name
    if target.is_file():
        return target
    return None
