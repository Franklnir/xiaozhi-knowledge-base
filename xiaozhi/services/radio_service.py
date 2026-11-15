"""Resolve configured radio links and issue playback URLs for saved stations."""

import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from itsdangerous import BadData, URLSafeTimedSerializer

from xiaozhi.config import APP_SECRET_KEY
from xiaozhi.services.playback_tracker import playback_tracker

logger = logging.getLogger("xiaozhi.radio")
STATIONS_FILE = Path(__file__).resolve().parents[2] / "data" / "radio_stations.json"
RADIO_ID = re.compile(r"radio_live_[a-z0-9][a-z0-9-]{0,47}\Z")
PLAYBACK_TICKET_MAX_AGE = 15 * 60
_tickets = URLSafeTimedSerializer(APP_SECRET_KEY, salt="xiaozhi-radio-playback")
STATUS_LABELS = {
    "registered": "Terdaftar",
    "connecting": "Menghubungkan…",
    "streaming": "Sedang streaming",
    "waiting": "Menunggu audio…",
    "interrupted": "Gangguan terakhir",
    "unavailable": "Sumber belum tersedia",
    "disabled": "Nonaktif",
}


@dataclass(frozen=True)
class RadioStation:
    video_id: str
    title: str
    page_url: str
    stream_url: str


def is_radio_id(video_id: str) -> bool:
    return isinstance(video_id, str) and RADIO_ID.fullmatch(video_id) is not None


def radio_playlist_tracks(tracks: list[dict]) -> list[dict]:
    radio_tracks = (track for track in tracks if is_radio_id(track.get("video_id")))
    return [{**track, "radio_number": number} for number, track in enumerate(radio_tracks, start=1)]


def explicit_radio_query(query: str) -> str | None:
    match = re.fullmatch(
        r"(?:(?:tolong|coba)\s+)?(?:putar(?:kan)?|mainkan|setel|dengarkan)\s+(?:stream\s+)?radio(?:\s+(.*))?",
        query.strip().rstrip("?!."), re.IGNORECASE,
    )
    return (match[1] or "").strip() if match else None


def match_radio_tracks(tracks: list[dict], query: str) -> list[dict]:
    key = " ".join(query.casefold().split())
    number = re.fullmatch(r"(?:(?:nomor|no\.?|ke)[\s-]*)?#?\s*([0-9]{1,9})", key)
    if number:
        return [track for track in tracks if track["radio_number"] == int(number[1])]
    titles = [(track, " ".join(track["title"].casefold().split())) for track in tracks]
    exact = [track for track, title in titles if title == key]
    return exact or [track for track, title in titles if key in title]


def _https_url(value: str):
    if not isinstance(value, str) or any(char.isspace() for char in value) or "\\" in value:
        return None
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if url.scheme != "https" or url.username is not None or url.port not in (None, 443):
            return None
        if not re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z0-9-]+", host) or host.endswith(".localhost"):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return url if not host.rsplit(".", 1)[-1].isdigit() else None
    except ValueError:
        pass
    return None


def _page_key(value: str):
    url = _https_url(value)
    if url is None:
        return None
    query = tuple(sorted(
        (key, val) for key, val in parse_qsl(url.query)
        if not key.startswith("utm_") and key != "referrer_id"
    ))
    return url.hostname, url.path.rstrip("/"), query


def radio_stations():
    try:
        data = json.loads(STATIONS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        logger.warning("Radio station configuration could not be read")
        return
    if not isinstance(data, list):
        logger.warning("Radio station configuration must be a list")
        return
    for entry in data:
        if not isinstance(entry, dict):
            continue
        video_id = f"radio_live_{entry.get('id', '')}"
        title = entry.get("title")
        stream_url = entry.get("stream_url")
        page_url = entry.get("page_url") or stream_url
        if is_radio_id(video_id) and isinstance(title, str) and title.strip() and _https_url(page_url) and _https_url(stream_url):
            yield RadioStation(video_id, title.strip(), page_url, stream_url)


def find_radio_station(url: str) -> RadioStation | None:
    key = _page_key(url)
    if key is None:
        return None
    return next(
        (station for station in radio_stations() if url == station.stream_url or key == _page_key(station.page_url)),
        None,
    )


def get_radio_station(video_id: str) -> RadioStation | None:
    return next((station for station in radio_stations() if station.video_id == video_id), None) if is_radio_id(video_id) else None


def radio_statuses(owner_id: int, tracks: list[dict], stations: list[RadioStation]) -> dict:
    active = playback_tracker.get_session_by_user(owner_id)
    latest = playback_tracker.get_latest_session(owner_id) if active is None else None
    configured_ids = {station.video_id for station in stations}
    now = time.time()
    statuses = {}
    for track in tracks:
        video_id = track["video_id"]
        state = "registered"
        if not track.get("is_active", True):
            state = "disabled"
        elif video_id not in configured_ids:
            state = "unavailable"
        elif active and active.video_id == video_id:
            if not active.bytes_streamed:
                state = "connecting"
            else:
                state = "waiting" if now - active.last_active_at > 10 else "streaming"
        elif latest and latest["video_id"] == video_id and latest.get("end_reason") == "error":
            state = "interrupted"
        statuses[track["id"]] = {"state": state, "label": STATUS_LABELS[state]}
    return statuses


def queue_radio_track(store, owner_id: int, track: dict, base_url: str, mac: str = "") -> str:
    video_id = track["video_id"]
    if not get_radio_station(video_id):
        raise ValueError("Stream stasiun ini belum tersedia.")
    params = {"radio_ticket": _tickets.dumps([int(owner_id), int(track["id"]), video_id])}
    if mac:
        params["mac"] = mac
        base_url = base_url.replace("https://", "http://", 1)
    stream_url = f"{base_url.rstrip('/')}/api/audio/stream/{video_id}?{urlencode(params)}"
    store.queue_audio_command(
        owner_id, title=track["title"], stream_url=stream_url,
        video_url=track["youtube_url"], duration="LIVE", video_id=track["video_id"],
    )
    store.increment_playlist_play_count(owner_id, track_id=track["id"])
    return stream_url


def resolve_radio_playback(store, video_id: str, ticket: str):
    try:
        owner_id, track_id, signed_id = _tickets.loads(ticket, max_age=PLAYBACK_TICKET_MAX_AGE)
    except (BadData, TypeError, ValueError):
        return None
    if signed_id != video_id:
        return None
    station = get_radio_station(video_id)
    if station is None:
        return None
    track = store.get_playlist_track(owner_id, track_id)
    if not track or track.get("video_id") != video_id or not track.get("is_active", True):
        return None
    return owner_id, track, station
