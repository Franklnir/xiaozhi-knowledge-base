"""Match confirmed YouTube video requests to observed audio streaming."""

import json
import re
from datetime import datetime, timezone

from xiaozhi.core.utils import normalize_mac_address
from xiaozhi.services.chat_memory_service import resolve_original_history_payload


def _video_response(payload):
    if not isinstance(payload, dict) or payload.get("isError"):
        return {}
    if isinstance(payload.get("structuredContent"), dict):
        return payload["structuredContent"]
    content = payload.get("content")
    if isinstance(content, list) and content:
        try:
            parsed = json.loads(content[0]["text"])
        except (KeyError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return payload


def get_last_requested_video(store, tracker, owner_id, slot, device_mac=""):
    session = tracker.get_latest_session(owner_id) or {}
    expected_mac = normalize_mac_address(device_mac or "")
    if not expected_mac:
        slot_info = store.get_xiaozhi_token_info(owner_id, slot=slot) or {}
        expected_mac = normalize_mac_address(slot_info.get("board_mac") or "")
    actual_mac = normalize_mac_address(session.get("device_mac") or "")
    if not expected_mac or expected_mac != actual_mac:
        session = {}

    records = []
    for tool in ("play_confirmed_youtube_video", "confirm_play_and_save_video"):
        records.extend(store.list_chat_history(owner_id, tool_name=tool, limit=20,
                                               slot_number=slot, strict_slot=True))
    records.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
    latest_request = None
    for row in records:
        _, payload, _ = resolve_original_history_payload(row)
        result = _video_response(payload)
        video_id = result.get("video_id")
        if (result.get("success") is not True or result.get("playback_queued") is not True
                or not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)):
            continue
        video = {
            "video_id": video_id,
            "title": result.get("title") or f"Video YouTube {video_id}",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "requested_at": str(row.get("created_at") or ""),
            "status": "belum_terverifikasi",
        }
        if latest_request is None:
            latest_request = video
        if session.get("video_id") != video_id or not session.get("bytes_streamed"):
            continue
        try:
            requested = datetime.fromisoformat(video["requested_at"])
            if requested.tzinfo is None:
                requested = requested.replace(tzinfo=timezone.utc)
            if float(session["started_at"]) < requested.timestamp() - 5:
                continue
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        video["status"] = "stream_berakhir" if session.get("ended_at") else "stream_berjalan"
        return video, latest_request
    return None, latest_request
