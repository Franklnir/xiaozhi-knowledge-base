import hashlib
import hmac
import io
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
import zipfile
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from xiaozhi.config import (
    REAL_RELAY_MAX_RELAYS,
    REAL_RELAY_POLL_INTERVAL_MS,
    REAL_RELAY_DEVICE_ONLINE_SECONDS,
)
from xiaozhi.core.utils import (
    clean_text,
    normalize_relay_client_id,
    normalize_relay_state,
    normalize_voice_command,
    parse_int_range,
    slugify_topic_part,
    utc_now,
)
from xiaozhi.dependencies import (
    get_current_user,
    get_store,
    render,
    require_user,
    validate_csrf,
    redirect_with_message,
)
from xiaozhi.services.mcp_service import is_mcp_connected
from xiaozhi.database.models import RealRelayControlCommand, RealRelayDeviceStatusCommand

router = APIRouter()
_relay_device_last_broadcast: Dict[int, float] = {}


def _device_token_matches(room: dict, supplied_token: str) -> bool:
    """Fail closed unless the room has a configured per-device credential."""
    supplied_token = str(supplied_token or "")
    if not supplied_token:
        return False
    expected = str(room.get("api_token") or "")
    if expected and hmac.compare_digest(supplied_token, expected):
        return True
    expected_hash = str(room.get("api_token_hash") or "")
    supplied_hash = hashlib.sha256(supplied_token.encode("utf-8")).hexdigest()
    return bool(expected_hash) and hmac.compare_digest(supplied_hash, expected_hash)


def _gateway_internal_key() -> str:
    from xiaozhi.config import APP_SECRET_KEY
    return os.getenv("GATEWAY_INTERNAL_KEY", "").strip() or str(APP_SECRET_KEY or "")


@router.get("/relay-nyata", response_class=HTMLResponse)
async def relay_nyata_page(request: Request, mode: str = Query(default=None)):
    if mode in ("virtual", "simulasi"):
        return RedirectResponse("/simulasi-smarthome-virtual", status_code=303)
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    payload = real_relay_page_payload(user["id"])
    feature_settings = store.get_feature_settings(user["id"])
    virtual_enabled = bool(feature_settings.get("virtual_smarthome_enabled", True))
    quota = store.user_quota(user["id"])
    api_defaults = {
        "max_relays": REAL_RELAY_MAX_RELAYS,
        "poll_interval_ms": REAL_RELAY_POLL_INTERVAL_MS,
        "device_online_window_seconds": REAL_RELAY_DEVICE_ONLINE_SECONDS,
    }
    return render(
        request,
        "relay_nyata.html",
        {
            "user": user,
            "rooms": payload["rooms"],
            "quota": quota,
            "api_defaults": api_defaults,
            "virtual_smarthome_enabled": virtual_enabled,
            "mcp_connected": is_mcp_connected(user["id"]),
            "message": request.query_params.get("message", ""),
            "active_page": "relay_nyata",
            "page": "relay_nyata",
            "active_mode": "real",
        },
    )


def real_relay_page_payload(owner_id: int) -> Dict[str, Any]:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    return {"rooms": rooms}


def _forward_mqtt_command(slug: str, relay_number: int, command: str, cmd_id: str, device_token: str) -> tuple[bool, str]:
    import json
    import os
    import urllib.error
    import urllib.request

    if os.getenv("TESTING") or os.getenv("PYTEST_CURRENT_TEST"):
        return True, ""
    
    clean_slug = (slug or "").strip().lower()
    if not clean_slug:
        return False, "Slug perangkat tidak tersedia."
    if not device_token:
        return False, "Token perangkat tidak tersedia."

    last_error = "Gateway tidak merespons. Periksa koneksi gateway MQTT."
        
    base_urls = [
        os.getenv("GATEWAY_URL", "").rstrip("/"),
        "http://xiaozhi-gateway:8080",
        "http://127.0.0.1:8085",
        "http://gateway:8080",
        "http://localhost:8085",
    ]
    
    payload = json.dumps({
        "relay": int(relay_number),
        "command": str(command).upper(),
        "cmd_id": str(cmd_id),
        "token": device_token,
    }).encode("utf-8")
    
    for base in base_urls:
        if not base:
            continue
        try:
            target = f"{base}/api/device/relay/{clean_slug}/control"
            req = urllib.request.Request(
                target,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if 200 <= resp.status < 300:
                    return True, ""
                last_error = f"Gateway menolak perintah (HTTP {resp.status})."
        except urllib.error.HTTPError as exc:
            try:
                response_data = json.loads(exc.read().decode("utf-8", errors="replace"))
                last_error = str(response_data.get("detail") or f"Gateway menolak perintah (HTTP {exc.code}).")
            except Exception:
                last_error = f"Gateway menolak perintah (HTTP {exc.code})."
        except urllib.error.URLError as exc:
            last_error = f"Gateway tidak dapat dijangkau: {exc.reason}"
        except Exception as exc:
            last_error = f"Gagal menghubungi gateway ({type(exc).__name__})."
    return False, last_error


def control_real_relay(owner_id: int, room: dict, relay: dict, command: str, source: str = "web_ui", wait_for_ack: bool = None) -> dict:
    target_status = normalize_relay_state(command)
    current_status = str(relay.get("status", "OFF")).upper()
    relay_num = int(relay.get("relay_number", 1))
    if relay_num < 1 or relay_num > REAL_RELAY_MAX_RELAYS:
        raise ValueError(f"Nomor channel relay {relay_num} di luar batas yang diizinkan (1 - {REAL_RELAY_MAX_RELAYS}).")
    slug = room.get("slug") or room.get("api_slug") or ""
    
    import time
    cmd_id = f"cmd_{int(time.time() * 1000)}_{relay_num}"

    # 1. Forward instant command to Go Gateway -> Mosquitto MQTT: xiaozhi/relay/{slug}/set
    sent, send_error = _forward_mqtt_command(slug, relay_num, target_status, cmd_id, str(room.get("api_token") or ""))
    if not sent:
        return {
            "success": False,
            "pending": False,
            "validated": False,
            "message": send_error or "Gateway belum berhasil mengirim perintah lewat MQTT.",
            "room": room.get("nama_tempat", ""),
            "relay": relay.get("nama_relay", ""),
            "current_status": current_status,
            "desired_status": target_status,
            "cmd_id": cmd_id,
            "source": source,
        }

    # 2. Broadcast SSE update with status = current_status, pending = target_status, desired = target_status
    try:
        from xiaozhi.services.sse_service import broadcast_relay_update
        broadcast_relay_update(
            user_id=owner_id,
            room_id=room["id"],
            relay_number=relay_num,
            status=current_status,  # Still previous confirmed state!
            source=source,
            room_name=room.get("nama_tempat", ""),
            relay_name=relay.get("nama_relay", ""),
            pending=target_status,  # Waiting for ESP32 hardware ACK!
            desired=target_status,
        )
    except Exception:
        pass

    if wait_for_ack is None:
        wait_for_ack = (source == "mcp_voice")

    store = get_store()

    # 3. Jika dipanggil oleh XiaoZhi Voice (MCP), tunggu konfirmasi fisik hardware ESP32 secara aktif (hingga 2.5 detik)
    if wait_for_ack:
        deadline = time.time() + 2.5
        confirmed = False
        while time.time() < deadline:
            time.sleep(0.08)
            try:
                refreshed_room = store.get_relay_room(owner_id, room["id"])
                if refreshed_room:
                    for r in refreshed_room.get("relays", []):
                        if int(r.get("relay_number", 0)) == relay_num:
                            if str(r.get("status", "")).upper() == target_status:
                                confirmed = True
                                break
            except Exception:
                pass
            if confirmed:
                break

        action_word = "dinyalakan" if target_status == "ON" else "dimatikan"
        status_word = "aktif (ON)" if target_status == "ON" else "mati (OFF)"
        if confirmed:
            return {
                "success": True,
                "pending": False,
                "validated": True,
                "message": f"Berhasil! {relay.get('nama_relay', 'Relay')} di {room.get('nama_tempat', '')} telah {action_word} dan terkonfirmasi fisik {status_word} oleh perangkat ESP32.",
                "room": room.get("nama_tempat", ""),
                "relay": relay.get("nama_relay", ""),
                "current_status": target_status,
                "desired_status": target_status,
                "cmd_id": cmd_id,
                "source": source,
            }
        else:
            # Graceful fallback: Command was successfully dispatched via MQTT.
            # Do NOT report False to voice AI (which would cause "perintah tidak dikenali gateway")
            return {
                "success": True,
                "pending": True,
                "validated": False,
                "message": f"Baik, {relay.get('nama_relay', 'relay')} di {room.get('nama_tempat', '')} telah diproses untuk {action_word}.",
                "room": room.get("nama_tempat", ""),
                "relay": relay.get("nama_relay", ""),
                "current_status": current_status,
                "desired_status": target_status,
                "cmd_id": cmd_id,
                "source": source,
            }

    return {
        "success": True,
        "pending": True,
        "message": f"Perintah {target_status} dikirim ke ESP32 ({room.get('nama_tempat', '')}). Menunggu validasi fisik...",
        "room": room.get("nama_tempat", ""),
        "relay": relay.get("nama_relay", ""),
        "current_status": current_status,
        "desired_status": target_status,
        "cmd_id": cmd_id,
        "source": source,
    }


def control_all_real_relays(owner_id: int, command: str, room_id: int = None, source: str = "web_ui", wait_for_ack: bool = None) -> dict:
    target_status = normalize_relay_state(command)
    store = get_store()
    all_rooms = store.list_relay_rooms(owner_id)
    if room_id is not None:
        rooms = [r for r in all_rooms if int(r.get("id", 0)) == int(room_id)]
    else:
        rooms = all_rooms

    count = 0
    failed_count = 0
    first_failure = ""
    expected_relays = []
    import time
    for room in rooms:
        slug = room.get("slug") or room.get("api_slug") or ""
        for relay in room.get("relays", []):
            relay_num = int(relay.get("relay_number", 1))
            current_status = str(relay.get("status", "OFF")).upper()
            cmd_id = f"cmd_{int(time.time() * 1000)}_{relay_num}"
            sent, send_error = _forward_mqtt_command(slug, relay_num, target_status, cmd_id, str(room.get("api_token") or ""))
            if not sent:
                failed_count += 1
                if not first_failure:
                    first_failure = send_error
                continue
            count += 1
            expected_relays.append((int(room["id"]), relay_num))
            try:
                from xiaozhi.services.sse_service import broadcast_relay_update
                broadcast_relay_update(
                    user_id=owner_id,
                    room_id=room["id"],
                    relay_number=relay_num,
                    status=current_status,
                    source=source,
                    room_name=room.get("nama_tempat", ""),
                    relay_name=relay.get("nama_relay", ""),
                    pending=target_status,
                    desired=target_status,
                )
            except Exception:
                pass

    if wait_for_ack is None:
        wait_for_ack = (source == "mcp_voice")

    target_name = rooms[0].get("nama_tempat", "") if len(rooms) == 1 else "seluruh ruangan"
    action_word = "dinyalakan" if target_status == "ON" else "dimatikan"
    status_word = "aktif (ON)" if target_status == "ON" else "mati (OFF)"

    if wait_for_ack and count > 0:
        deadline = time.time() + 2.5
        all_confirmed = False
        while time.time() < deadline:
            time.sleep(0.1)
            try:
                refreshed_rooms = store.list_relay_rooms(owner_id)
                statuses = {
                    (int(r_room["id"]), int(r_relay.get("relay_number", 0))): str(r_relay.get("status", "")).upper()
                    for r_room in refreshed_rooms
                    for r_relay in r_room.get("relays", [])
                }
                all_match = all(statuses.get(key) == target_status for key in expected_relays)
                if all_match:
                    all_confirmed = True
                    break
            except Exception:
                pass

        if all_confirmed:
            return {
                "success": failed_count == 0,
                "pending": False,
                "validated": failed_count == 0,
                "message": f"{count} relay di {target_name} terkonfirmasi fisik {status_word}. " + (f"{failed_count} perintah gagal dikirim melalui MQTT. {first_failure}" if failed_count else f"Seluruh relay berhasil {action_word}."),
                "count": count,
                "failed_count": failed_count,
                "desired_status": target_status,
                "source": source,
            }
        else:
            return {
                "success": failed_count == 0 and count > 0,
                "pending": True,
                "validated": False,
                "message": f"Baik, perintah {action_word} telah dikirim ke {count} relay di {target_name}." + (f" ({failed_count} gagal dikirim)" if failed_count else ""),
                "count": count,
                "failed_count": failed_count,
                "desired_status": target_status,
                "source": source,
            }

    return {
        "success": failed_count == 0,
        "pending": count > 0,
        "message": f"Perintah {target_status} terkirim ke {count} relay di {target_name}; {failed_count} gagal dikirim melalui MQTT. {first_failure}".strip(),
        "count": count,
        "failed_count": failed_count,
        "desired_status": target_status,
        "source": source,
    }


NUMBER_WORDS_MAP = {
    "nol": "0", "satu": "1", "dua": "2", "tiga": "3", "empat": "4",
    "lima": "5", "enam": "6", "tujuh": "7", "delapan": "8", "sembilan": "9",
    "sepuluh": "10", "sebelas": "11", "dua belas": "12", "tiga belas": "13",
    "empat belas": "14", "lima belas": "15", "enam belas": "16",
}

_NUMBER_WORDS_DESC = sorted(NUMBER_WORDS_MAP.items(), key=lambda item: len(item[0]), reverse=True)
_ON_ACTION_WORDS = {
    "nyalakan", "nyala", "nyalain", "hidupkan", "hidup", "hidupin",
    "aktifkan", "aktifin", "buka", "fungsikan", "operasikan", "bunyikan",
    "dinginkan", "on",
}
_OFF_ACTION_WORDS = {
    "matikan", "mati", "matiin", "padamkan", "padam", "padamin",
    "nonaktifkan", "nonaktifin", "tutup", "hentikan", "senyapkan", "diamkan", "off",
}
_FILLER_WORDS = {"tolong", "dong", "xiaozhi", "coba", "mohon", "silakan", "sekarang", "ya", "yah", "turn"}
_ACTION_AND_FILLER_WORDS = _ON_ACTION_WORDS | _OFF_ACTION_WORDS | _FILLER_WORDS
_GENERIC_DEVICE_WORDS = {
    "lampu", "lamp", "saklar", "switch", "relay", "channel", "ch",
    "saluran", "nomor", "stopkontak", "kontak", "perangkat", "alat",
}
_CHANNEL_SPECIFIER_WORDS = {"relay", "channel", "ch", "saluran", "nomor", "saklar", "pin", "tombol", "port"}
_CONNECTOR_WORDS = {"di", "pada", "ke", "dalam"}
_COMPOUND_SPLIT_RE = re.compile(r"\s*(?:,|\b(?:dan|serta|lalu|kemudian|terus)\b)\s*")


def normalize_smart_text(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\b(?:no|nomer)\b", "nomor", t)
    t = re.sub(r"\btdk\b", "tidak", t)
    t = re.sub(r"\bsdh\b", "sudah", t)
    t = re.sub(r"\bsy\b", "saya", t)
    t = re.sub(r"\bdong\b", "", t)
    t = re.sub(r"\btolong\b", "", t)

    # Replace longer spelled-out numbers (12-16) before base units (1-9).
    for word, digit in _NUMBER_WORDS_DESC:
        t = re.sub(r"\b" + re.escape(word) + r"\b", digit, t)

    return re.sub(r"\s+", " ", t).strip()


def detect_relay_action(text: str) -> str:
    norm = normalize_smart_text(text)
    words = norm.split()
    for w in words:
        if w in _OFF_ACTION_WORDS:
            return "OFF"
        if w in _ON_ACTION_WORDS:
            return "ON"
    return "ON"


def _has_explicit_action(text: str) -> bool:
    words = normalize_smart_text(text).split()
    return any(w in _ON_ACTION_WORDS or w in _OFF_ACTION_WORDS for w in words)


def _has_phrase(haystack: str, needle: str) -> bool:
    if not haystack or not needle:
        return False
    return f" {needle} " in f" {haystack} "


def _room_has_standalone_d_relay(room: dict) -> bool:
    for relay in room.get("relays", []):
        r_tokens = normalize_smart_text(relay.get("nama_relay", "")).split()
        if "d" in r_tokens:
            return True
    return False


def _strip_optional_nya(tokens: list[str], known_tokens: set[str]) -> list[str]:
    result = []
    for tok in tokens:
        if tok.endswith("nya") and len(tok) > 4 and tok[:-3] in known_tokens:
            result.append(tok[:-3])
        else:
            result.append(tok)
    return result


def _extract_room_mentions(rooms: list, norm_msg: str) -> tuple[list, list, list[str]]:
    """
    Identify rooms mentioned in norm_msg, separating explicit prepositional
    references ('di rumah', 'd ruang 1', 'pada papan') from unprefixed mentions.
    Matches longest room names first without overlapping spans.
    """
    explicit_rooms = []
    unprefixed_rooms = []
    explicit_phrases: list[str] = []
    occupied_spans: list[tuple[int, int]] = []

    def _overlaps(start: int, end: int) -> bool:
        return any(not (end <= s or start >= e) for s, e in occupied_spans)

    sorted_rooms = sorted(
        rooms,
        key=lambda r: (len(normalize_smart_text(r.get("nama_tempat", ""))), r.get("id", 0)),
        reverse=True,
    )

    for room in sorted_rooms:
        r_name = normalize_smart_text(room.get("nama_tempat", ""))
        r_slug = normalize_smart_text(room.get("api_slug", ""))
        matched_explicit = False
        matched_unprefixed = False

        if r_name:
            # Allow 'd <room>' as shorthand for 'di <room>' unless the room has a relay literally named 'D'.
            preps = ["di", "pada", "ke", "dalam"]
            if not _room_has_standalone_d_relay(room):
                preps.append("d")
            prep_group = "|".join(preps)
            prep_re = re.compile(
                rf"(?<!\w)(?:{prep_group})\s+(?:ruangan?\s+)?{re.escape(r_name)}(?!\w)"
            )
            for m in prep_re.finditer(norm_msg):
                if not _overlaps(m.start(), m.end()):
                    occupied_spans.append((m.start(), m.end()))
                    explicit_phrases.append(m.group(0))
                    matched_explicit = True
                    break

            if not matched_explicit:
                bare_re = re.compile(rf"(?<!\w){re.escape(r_name)}(?!\w)")
                for m in bare_re.finditer(norm_msg):
                    if not _overlaps(m.start(), m.end()):
                        occupied_spans.append((m.start(), m.end()))
                        matched_unprefixed = True
                        break

        if not matched_explicit and not matched_unprefixed and r_slug:
            if _has_phrase(norm_msg, r_slug):
                matched_unprefixed = True

        if matched_explicit:
            explicit_rooms.append(room)
        elif matched_unprefixed:
            unprefixed_rooms.append(room)

    return explicit_rooms, unprefixed_rooms, explicit_phrases


def _replace_phrases_with_sentinel(text: str, phrases: list[str], sentinel: str = " __room__ ") -> str:
    updated = f" {text} "
    for phrase in sorted(set(phrases), key=len, reverse=True):
        if phrase:
            updated = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", sentinel, updated)
    return re.sub(r"\s+", " ", updated).strip()


def _score_relay_candidate(
    *,
    norm_msg: str,
    room: dict,
    relay: dict,
    action: str,
    has_explicit_rooms: bool,
    is_explicit_room: bool,
    is_unprefixed_room: bool,
    explicit_phrases: list[str],
    known_tokens: set[str],
) -> int:
    """
    Score a single (room, relay) candidate for a voice utterance.
    Full relay-name phrases and explicit channel specifiers strictly outrank
    incidental digits inside unrelated relay names or cross-room mentions.
    """
    r_name = normalize_smart_text(room.get("nama_tempat", ""))
    r_slug = normalize_smart_text(room.get("api_slug", ""))
    room_tokens = set(r_name.split())

    relay_num = str(relay.get("relay_number", "")).strip()
    relay_name = normalize_smart_text(relay.get("nama_relay", ""))
    voice_on = normalize_smart_text(relay.get("voice_command_on", ""))
    voice_off = normalize_smart_text(relay.get("voice_command_off", ""))
    target_voice = voice_on if action == "ON" else voice_off

    if has_explicit_rooms and not is_explicit_room:
        return -9999

    room_score = 200 if is_explicit_room else (80 if is_unprefixed_room else 0)

    # Normalize optional possessive '-nya' only when the stem is a known room/relay token.
    norm_tokens = _strip_optional_nya(norm_msg.split(), known_tokens)
    norm_clean = " ".join(norm_tokens)

    # Isolate digits that aren't part of room names when matching channel numbers.
    room_phrases_to_mask = list(explicit_phrases)
    if r_name:
        room_phrases_to_mask.append(r_name)
    if r_slug:
        room_phrases_to_mask.append(r_slug)
    rem_msg = _replace_phrases_with_sentinel(norm_clean, room_phrases_to_mask)
    rem_tokens = rem_msg.split()

    # Keep the room name when the relay's own name contains it (e.g. "Lampu Dapur" in "Dapur").
    name_mask = list(explicit_phrases)
    if r_name and not _has_phrase(relay_name, r_name):
        name_mask.append(r_name)
    msg_for_name = _replace_phrases_with_sentinel(norm_clean, name_mask)
    msg_no_nomor = re.sub(r"\bnomor\s+(?=\d+\b)", "", msg_for_name)
    msg_no_nomor = re.sub(r"\s+", " ", msg_no_nomor).strip()

    # 1. Relay name scoring
    name_score = 0
    relay_tokens = relay_name.split() if relay_name else []
    non_generic_relay = [t for t in relay_tokens if t not in _GENERIC_DEVICE_WORDS]
    generic_count = len(relay_tokens) - len(non_generic_relay)

    if relay_name:
        if _has_phrase(msg_for_name, relay_name) or _has_phrase(msg_no_nomor, relay_name):
            if non_generic_relay:
                name_score = 220 + (25 * len(non_generic_relay)) + (10 * generic_count)
            else:
                name_score = 100
        elif non_generic_relay:
            name_tokens_pool = set(rem_tokens) | set(msg_for_name.split())
            matched_ng = [t for t in non_generic_relay if t in name_tokens_pool]
            # Require at least one alphabetic identifier so a bare digit never poses as a name match.
            if matched_ng and any(not t.isdigit() for t in matched_ng):
                if len(matched_ng) == len(non_generic_relay):
                    name_score = 170 + (20 * len(non_generic_relay))
                else:
                    name_score = 90 + (15 * (len(matched_ng) - 1))

    # 2. Voice command phrase scoring
    voice_score = 0
    if target_voice:
        target_tokens = target_voice.split()
        # Strip optional connectors/generic words only when they are absent from the saved command.
        optional_skip = (_CONNECTOR_WORDS | (_GENERIC_DEVICE_WORDS - set(target_tokens)))
        filtered_msg_for_voice = " ".join(
            t for t in norm_clean.split() if t not in optional_skip and t != "__room__"
        )
        filtered_voice = " ".join(t for t in target_tokens if t not in _CONNECTOR_WORDS)

        if target_voice == norm_clean:
            voice_score = 350
        elif filtered_voice and filtered_msg_for_voice == filtered_voice:
            voice_score = 260
        elif _has_phrase(norm_clean, target_voice):
            voice_score = 240
        elif filtered_voice and _has_phrase(filtered_msg_for_voice, filtered_voice):
            voice_score = 220
        else:
            core_voice_tokens = [
                t for t in target_tokens
                if t not in _ACTION_AND_FILLER_WORDS
                and t not in room_tokens
                and t not in _CONNECTOR_WORDS
            ]
            has_substance = any(t not in _GENERIC_DEVICE_WORDS for t in core_voice_tokens)
            core_voice = " ".join(core_voice_tokens)
            if core_voice and has_substance:
                if _has_phrase(msg_for_name, core_voice) or _has_phrase(msg_no_nomor, core_voice):
                    voice_score = 210

    # 3. Channel number scoring (strictly distinguish explicit cues vs bare vs noun-bound digits)
    number_score = 0
    if relay_num:
        for idx, tok in enumerate(rem_tokens):
            if tok != relay_num:
                continue
            prev_tok = rem_tokens[idx - 1] if idx > 0 else ""
            if prev_tok in _CHANNEL_SPECIFIER_WORDS:
                number_score = max(number_score, 150)
            elif prev_tok == "" or prev_tok in _ACTION_AND_FILLER_WORDS or prev_tok in _GENERIC_DEVICE_WORDS:
                number_score = max(number_score, 65)

    # 4. Prevent digit conflict (e.g., "nyalakan garasi 2" must not hit "Garasi 1")
    conflict_penalty = 0
    relay_digits = {t for t in relay_tokens if t.isdigit()}
    rem_digits = {t for t in rem_tokens if t.isdigit()}
    if number_score < 150 and relay_digits and rem_digits and not (relay_digits & rem_digits):
        conflict_penalty = 200

    relay_signal_score = name_score + voice_score + number_score - conflict_penalty
    if relay_signal_score < 60:
        return -9999

    return relay_signal_score + room_score


def smart_match_real_relay_detailed(rooms: list, user_message: str) -> dict:
    """
    Match user_message to a real relay room/channel, returning either:
    - single match dict {'room', 'relay', 'command', 'score'}
    - all-room batch dict {'is_all': True, 'room', 'relay': None, 'command'}
    - ambiguous tie dict {'ambiguous': True, 'command', 'candidates', 'message'}
    - None when no candidate matches confidently.
    """
    norm_msg = normalize_smart_text(user_message)
    if not norm_msg or not rooms:
        return None

    action = detect_relay_action(norm_msg)
    is_all = any(w in norm_msg.split() for w in ("semua", "seluruh", "all"))

    explicit_rooms, unprefixed_rooms, explicit_phrases = _extract_room_mentions(rooms, norm_msg)

    if is_all:
        target_room = explicit_rooms[0] if explicit_rooms else (unprefixed_rooms[0] if unprefixed_rooms else None)
        return {
            "is_all": True,
            "room": target_room,
            "relay": None,
            "command": action,
        }

    known_tokens: set[str] = set()
    for room in rooms:
        known_tokens.update(normalize_smart_text(room.get("nama_tempat", "")).split())
        for relay in room.get("relays", []):
            known_tokens.update(normalize_smart_text(relay.get("nama_relay", "")).split())

    has_explicit_rooms = bool(explicit_rooms)
    explicit_ids = {id(r) for r in explicit_rooms}
    unprefixed_ids = {id(r) for r in unprefixed_rooms}

    scored_candidates = []
    seen_keys = set()
    for room in rooms:
        is_exp = id(room) in explicit_ids
        is_unp = id(room) in unprefixed_ids
        if has_explicit_rooms and not is_exp:
            continue
        for relay in room.get("relays", []):
            key = (room.get("id", id(room)), relay.get("relay_number"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            score = _score_relay_candidate(
                norm_msg=norm_msg,
                room=room,
                relay=relay,
                action=action,
                has_explicit_rooms=has_explicit_rooms,
                is_explicit_room=is_exp,
                is_unprefixed_room=is_unp,
                explicit_phrases=explicit_phrases,
                known_tokens=known_tokens,
            )
            if score >= 60:
                scored_candidates.append({
                    "room": room,
                    "relay": relay,
                    "command": action,
                    "score": score,
                })

    if not scored_candidates:
        return None

    scored_candidates.sort(key=lambda item: item["score"], reverse=True)
    top_score = scored_candidates[0]["score"]
    tied_top = [item for item in scored_candidates if item["score"] == top_score]
    if len(tied_top) > 1:
        options_text = ", ".join(
            f"{item['relay'].get('nama_relay', 'Relay')} ({item['room'].get('nama_tempat', '')})"
            for item in tied_top[:4]
        )
        return {
            "ambiguous": True,
            "command": action,
            "candidates": tied_top,
            "message": f"Perintah suara masih ambigu antara: {options_text}. Sebutkan nama lengkap atau nomornya.",
        }

    return scored_candidates[0]


def smart_match_real_relay(rooms: list, user_message: str) -> dict:
    result = smart_match_real_relay_detailed(rooms, user_message)
    if result and result.get("ambiguous"):
        return None
    return result


def smart_match_real_relay_commands(rooms: list, user_message: str) -> list[dict]:
    """
    Resolve either a single relay utterance or a compound command joined by
    connectors like 'dan'/'lalu' (e.g. 'nyalakan lampu sanggar 1 dan sanggar 2').
    """
    norm_msg = normalize_smart_text(user_message)
    if not norm_msg or not rooms:
        return []

    has_literal_connector_name = False
    for room in rooms:
        r_name = normalize_smart_text(room.get("nama_tempat", ""))
        if _COMPOUND_SPLIT_RE.search(r_name) and _has_phrase(norm_msg, r_name):
            has_literal_connector_name = True
            break
        for relay in room.get("relays", []):
            r_relay = normalize_smart_text(relay.get("nama_relay", ""))
            if _COMPOUND_SPLIT_RE.search(r_relay) and _has_phrase(norm_msg, r_relay):
                has_literal_connector_name = True
                break

    if not has_literal_connector_name and _COMPOUND_SPLIT_RE.search(user_message or ""):
        raw_parts = [p.strip() for p in _COMPOUND_SPLIT_RE.split(user_message) if p and p.strip()]
        if len(raw_parts) >= 2:
            clause_rooms = [_extract_room_mentions(rooms, normalize_smart_text(p))[0] for p in raw_parts]
            unique_exp_rooms = {r.get("id", id(r)): r for rs in clause_rooms for r in rs}
            shared_room_suffix = ""
            if len(unique_exp_rooms) == 1:
                only_room = next(iter(unique_exp_rooms.values()))
                shared_room_suffix = f" di {only_room.get('nama_tempat', '')}"

            resolved_parts = []
            seen_targets = set()
            last_action = detect_relay_action(norm_msg)
            all_valid = True
            for idx, part in enumerate(raw_parts):
                clause = part
                if _has_explicit_action(clause):
                    last_action = detect_relay_action(clause)
                else:
                    verb = "nyalakan" if last_action == "ON" else "matikan"
                    clause = f"{verb} {clause}"
                if shared_room_suffix and not clause_rooms[idx]:
                    clause = f"{clause}{shared_room_suffix}"

                res = smart_match_real_relay_detailed(rooms, clause)
                if not res or res.get("ambiguous") or res.get("is_all"):
                    all_valid = False
                    break
                key = (res["room"].get("id", id(res["room"])), res["relay"].get("relay_number"))
                if key in seen_targets:
                    all_valid = False
                    break
                seen_targets.add(key)
                resolved_parts.append(res)

            if all_valid and len(resolved_parts) >= 2:
                return resolved_parts

    single = smart_match_real_relay_detailed(rooms, user_message)
    return [single] if single else []


def smart_match_real_relay_for_target(rooms: list, *, target: str = "", action: str = "", channel: int = None) -> dict:
    cmd = detect_relay_action(action or target)
    norm_target = normalize_smart_text(target)

    if channel is not None:
        try:
            ch_num = int(channel)
        except (ValueError, TypeError):
            ch_num = None

        if ch_num is not None:
            target_room = None
            if norm_target:
                sorted_rooms = sorted(
                    rooms,
                    key=lambda r: len(normalize_smart_text(r.get("nama_tempat", ""))),
                    reverse=True,
                )
                for r in sorted_rooms:
                    r_name = normalize_smart_text(r.get("nama_tempat", ""))
                    if r_name and (_has_phrase(norm_target, r_name) or _has_phrase(r_name, norm_target)):
                        target_room = r
                        break

            search_rooms = [target_room] if target_room else rooms
            for r in search_rooms:
                for relay in r.get("relays", []):
                    if int(relay.get("relay_number", 0)) == ch_num:
                        return {"room": r, "relay": relay, "command": cmd}

    return smart_match_real_relay(rooms, f"{action} {target}".strip())


def match_real_relay_command(owner_id: int, user_message: str) -> dict:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    return smart_match_real_relay(rooms, user_message)


def match_real_relay_actions(owner_id: int, user_message: str) -> list[dict]:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    return smart_match_real_relay_commands(rooms, user_message)


def match_real_relay_for_target(owner_id: int, *, target: str = "", action: str = "", channel: int = None) -> dict:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    return smart_match_real_relay_for_target(rooms, target=target, action=action, channel=channel)


def match_real_relay_for_target_detailed(owner_id: int, *, target: str = "", action: str = "", channel: int = None) -> dict:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    if channel is not None:
        direct = smart_match_real_relay_for_target(rooms, target=target, action=action, channel=channel)
        if direct:
            return direct
    return smart_match_real_relay_detailed(rooms, f"{action} {target}".strip())


def real_relay_status_payload(owner_id: int) -> dict:
    store = get_store()
    rooms = store.list_relay_rooms(owner_id)
    relays = []
    for room in rooms:
        for relay in room.get("relays", []):
            relays.append({
                "room": room["nama_tempat"],
                "relay": relay["nama_relay"],
                "status": relay.get("status", "OFF"),
                "relay_number": relay["relay_number"],
            })
    return {"relays": relays}


def generate_real_relay_api_config(nama_tempat: str) -> Dict[str, Any]:
    nama_tempat = clean_text(nama_tempat, max_len=80, min_len=2, field="Nama tempat")
    slug = slugify_topic_part(nama_tempat) if nama_tempat else "relay"
    suffix = secrets.token_hex(3)
    unique_slug = f"{slug}-{suffix}"
    return {
        "api_slug": unique_slug,
        "api_client_id": f"device-{unique_slug}",
        "api_token": secrets.token_urlsafe(32),
    }


def generate_relay_voice_commands(nama_tempat: str, nama_relay: str, relay_number: int) -> tuple[str, str]:
    """
    Generate natural, unique Indonesian voice commands for turning ON and OFF a relay device.
    Combines device/load name and room name to guarantee uniqueness across different rooms and devices.
    Example:
      nama_tempat='Kamar Utama', nama_relay='Lampu', relay_number=1
      -> ('nyalakan lampu kamar utama', 'matikan lampu kamar utama')
    """
    room_clean = clean_text(nama_tempat or "ruangan", max_len=80, min_len=1, field="Nama tempat").lower().strip()
    relay_clean = clean_text(nama_relay or f"relay {relay_number}", max_len=80, min_len=1, field="Nama relay").lower().strip()

    # Normalize punctuation and special symbols
    room_norm = re.sub(r"[^a-zA-Z0-9\s]+", " ", room_clean).strip()
    relay_norm = re.sub(r"[^a-zA-Z0-9\s]+", " ", relay_clean).strip()

    # If the relay name already contains the room name, avoid repeating it
    if room_norm and room_norm in relay_norm:
        target = relay_norm
    elif room_norm:
        target = f"{relay_norm} {room_norm}"
    else:
        target = relay_norm or f"relay {relay_number}"

    target = re.sub(r"\s+", " ", target).strip()
    return f"nyalakan {target}", f"matikan {target}"


@router.post("/relay-nyata/toggle-simulasi")
async def toggle_simulasi(request: Request, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    feature_settings = store.get_feature_settings(user["id"])
    current = bool(feature_settings.get("virtual_smarthome_enabled", True))
    store.set_feature_setting(user["id"], "virtual_smarthome_enabled", not current)
    status = "dinonaktifkan" if current else "diaktifkan"
    return redirect_with_message("/relay-nyata", f"Simulasi Smart Home Virtual {status}.")


@router.post("/api/relay-nyata/generate-api")
async def generate_api(request: Request):
    user = require_user(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    nama_tempat = str(body.get("nama_tempat", "")).strip()
    if not nama_tempat or len(nama_tempat) < 2:
        nama_tempat = "Ruangan Relay"
    try:
        config = generate_real_relay_api_config(nama_tempat)
    except Exception:
        slug = slugify_topic_part(nama_tempat) if nama_tempat else "relay"
        suffix = secrets.token_hex(3)
        config = {
            "api_slug": f"{slug}-{suffix}",
            "api_client_id": f"device-{slug}-{suffix}",
            "api_token": secrets.token_urlsafe(32),
        }
    return {"success": True, "config": config, **config}


@router.post("/relay-nyata/add")
async def add_relay_room(
    request: Request,
    csrf_token: str = Form(...),
    nama_tempat: str = Form(...),
    api_slug: str = Form(""),
    api_token: str = Form(""),
    api_client_id: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)

    # Check the account's current admin-configured limit before any side effects.
    quota = store.user_quota(user["id"])
    if quota.get("relay_rooms", {}).get("reached"):
        return redirect_with_message(
            "/relay-nyata",
            f"Batas perangkat Relay Nyata akun Anda ({quota.get('relay_rooms', {}).get('limit', 7)} ruangan) telah tercapai. "
            "Hubungi admin jika ingin menambah kuota."
        )

    try:
        form = await request.form()
        jumlah_relay = parse_int_range(form.get("jumlah_relay"), field="Jumlah relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        relays = []
        for index in range(1, jumlah_relay + 1):
            nama_relay = str(form.get(f"relay_{index}_nama", "")).strip() or f"Relay {index}"
            voice_on = str(form.get(f"relay_{index}_on", "")).strip()
            voice_off = str(form.get(f"relay_{index}_off", "")).strip()
            if not voice_on or not voice_off:
                auto_on, auto_off = generate_relay_voice_commands(nama_tempat, nama_relay, index)
                voice_on = voice_on or auto_on
                voice_off = voice_off or auto_off
            relays.append({
                "relay_number": index,
                "nama_relay": nama_relay,
                "voice_command_on": voice_on,
                "voice_command_off": voice_off,
                "status": str(form.get(f"relay_{index}_status", "OFF")),
            })
        # Pastikan api_slug selalu unik di database
        clean_slug = slugify_topic_part(api_slug) if api_slug else ""
        if not clean_slug or store.find_relay_room_by_slug(clean_slug):
            base_slug = slugify_topic_part(nama_tempat) if nama_tempat else "relay"
            clean_slug = f"{base_slug}-{secrets.token_hex(3)}"
            while store.find_relay_room_by_slug(clean_slug):
                clean_slug = f"{base_slug}-{secrets.token_hex(3)}"
        
        api_slug = clean_slug
        if not api_token:
            api_token = secrets.token_urlsafe(32)
        if not api_client_id:
            api_client_id = f"device-{api_slug}"

        store.add_relay_room(user["id"], nama_tempat, api_slug, api_token, api_client_id, relays)
        feature_settings = store.get_feature_settings(user["id"])
        was_virtual_active = bool(feature_settings.get("virtual_smarthome_enabled", False))
        if was_virtual_active:
            store.set_feature_setting(user["id"], "virtual_smarthome_enabled", False)
        success_msg = f"Ruangan '{nama_tempat}' berhasil ditambahkan."
        if was_virtual_active:
            success_msg += " Simulasi Virtual otomatis dinonaktifkan agar perintah suara langsung mengontrol Relay Fisik."
        return redirect_with_message("/relay-nyata", success_msg)
    except Exception as exc:
        return redirect_with_message("/relay-nyata", f"Gagal: {exc}")


@router.post("/relay-nyata/update/{room_id}")
async def update_relay_room(
    request: Request,
    room_id: int,
    csrf_token: str = Form(...),
):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)

    # 1. Pastikan simulasi virtual otomatis dinonaktifkan saat edit relay fisik
    feature_settings = store.get_feature_settings(user["id"])
    was_virtual_active = bool(feature_settings.get("virtual_smarthome_enabled", False))
    if was_virtual_active:
        store.set_feature_setting(user["id"], "virtual_smarthome_enabled", False)

    # 2. Pastikan ruangan milik user yang login
    existing_room = store.get_relay_room(user["id"], room_id)
    if not existing_room:
        return redirect_with_message("/relay-nyata", "Gagal: Ruangan tidak ditemukan atau Anda tidak memiliki akses.")

    try:
        form = await request.form()
        nama_tempat = str(form.get("nama_tempat", "")).strip()
        jumlah_relay = parse_int_range(form.get("jumlah_relay"), field="Jumlah relay", minimum=1, maximum=REAL_RELAY_MAX_RELAYS)
        relays = []
        for index in range(1, jumlah_relay + 1):
            nama_relay = str(form.get(f"relay_{index}_nama", "")).strip() or f"Relay {index}"
            voice_on = str(form.get(f"relay_{index}_on", "")).strip()
            voice_off = str(form.get(f"relay_{index}_off", "")).strip()
            if not voice_on or not voice_off:
                auto_on, auto_off = generate_relay_voice_commands(nama_tempat, nama_relay, index)
                voice_on = voice_on or auto_on
                voice_off = voice_off or auto_off
            relays.append({
                "relay_number": index,
                "nama_relay": nama_relay,
                "voice_command_on": voice_on,
                "voice_command_off": voice_off,
                "status": str(form.get(f"relay_{index}_status", "OFF")),
            })
        store.update_relay_room(user["id"], room_id, nama_tempat, relays)
        success_msg = f"Ruangan '{nama_tempat}' berhasil diperbarui."
        if was_virtual_active:
            success_msg += " Simulasi Virtual otomatis dinonaktifkan agar perintah suara langsung mengontrol Relay Fisik."
        return redirect_with_message("/relay-nyata", success_msg)
    except Exception as exc:
        return redirect_with_message("/relay-nyata", f"Gagal: {exc}")


@router.post("/relay-nyata/delete/{room_id}")
async def delete_relay_room(request: Request, room_id: int, csrf_token: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return redirect_with_message("/login", "Silakan masuk terlebih dahulu.")
    store = get_store()
    validate_csrf(request, csrf_token, user)
    deleted = store.delete_relay_room(user["id"], room_id)
    return redirect_with_message("/relay-nyata", "Ruangan dihapus." if deleted else "Ruangan tidak ditemukan.")


@router.get("/api/relay-nyata/state")
async def relay_nyata_state(request: Request):
    user = require_user(request)
    payload = real_relay_status_payload(user["id"])
    return {"success": True, **payload}


@router.get("/api/relay-nyata/{room_id}/config")
async def relay_room_config(request: Request, room_id: int):
    user = require_user(request)
    store = get_store()
    room = store.get_relay_room(user["id"], room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan.")
    base_url = str(request.base_url).rstrip("/")
    room["api_commands_url"] = f"{base_url}{room.get('api_commands_path', '')}"
    room["api_status_url"] = f"{base_url}{room.get('api_status_path', '')}"
    room["api_token_header"] = "X-Device-Token"
    return {"success": True, "room": room}


@router.post("/api/relay-nyata/control")
async def relay_nyata_control(request: Request, cmd: RealRelayControlCommand):
    user = require_user(request)
    store = get_store()

    # 1. Keamanan Mode: Tolak jika simulasi virtual sedang aktif
    feature_settings = store.get_feature_settings(user["id"])
    if bool(feature_settings.get("virtual_smarthome_enabled", True)):
        return JSONResponse({
            "success": False,
            "message": "Simulasi Smart Home Virtual sedang aktif. Nonaktifkan simulasi virtual terlebih dahulu untuk mengontrol relay fisik.",
        }, status_code=400)

    # 2. Keamanan Batas Sistem Relay
    if cmd.relay_number < 1 or cmd.relay_number > REAL_RELAY_MAX_RELAYS:
        return JSONResponse({
            "success": False,
            "message": f"Akses ditolak: Channel relay {cmd.relay_number} di luar batas yang diizinkan (1 s/d {REAL_RELAY_MAX_RELAYS}).",
        }, status_code=400)

    # 3. Keamanan Otorisasi Ruangan
    room = store.get_relay_room(user["id"], cmd.room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan atau bukan milik akun Anda.")

    # 4. Keamanan Batas Ruangan: Pastikan relay terdaftar pada ruangan tersebut
    relay = next((r for r in room.get("relays", []) if r["relay_number"] == cmd.relay_number), None)
    if not relay:
        registered_numbers = [r["relay_number"] for r in room.get("relays", [])]
        return JSONResponse({
            "success": False,
            "message": f"Akses ditolak: Channel relay {cmd.relay_number} tidak terdaftar pada ruangan '{room.get('nama_tempat')}'. Ruangan ini hanya memiliki {len(registered_numbers)} relay terdaftar.",
        }, status_code=403)

    try:
        response = control_real_relay(user["id"], room, relay, cmd.command, source="web_ui")
        response["updated_room"] = store.get_relay_room(user["id"], cmd.room_id)
        return response
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)


@router.post("/api/relay-nyata/room-control")
async def relay_nyata_room_control(request: Request):
    """Batch control all relays in a specific room (All ON / All OFF)."""
    user = require_user(request)
    store = get_store()

    # 1. Keamanan Mode: Tolak jika simulasi virtual sedang aktif
    feature_settings = store.get_feature_settings(user["id"])
    if bool(feature_settings.get("virtual_smarthome_enabled", True)):
        return JSONResponse({
            "success": False,
            "message": "Simulasi Smart Home Virtual sedang aktif. Nonaktifkan simulasi virtual terlebih dahulu untuk mengontrol relay fisik.",
        }, status_code=400)

    body = await request.json()
    room_id = body.get("room_id")
    command = str(body.get("command", "OFF")).upper()
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id diperlukan.")

    room = store.get_relay_room(user["id"], int(room_id))
    if not room:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan atau bukan milik akun Anda.")

    results = [
        control_real_relay(user["id"], room, relay, command, source="web_ui")
        for relay in room.get("relays", [])
    ]
    failed = [item for item in results if not item.get("success")]
    updated_room = store.get_relay_room(user["id"], int(room_id))
    return {
        "success": not failed,
        "command": command,
        "failed_count": len(failed),
        "message": failed[0].get("message") if failed else f"Perintah {command} terkirim ke semua channel.",
        "updated_room": updated_room,
    }


@router.get("/api/relay-nyata/stream")
async def relay_nyata_stream(request: Request):
    """
    Real-time Server-Sent Events stream for 2-way live relay synchronization.
    Instantly pushes state updates triggered by XiaoZhi Voice Assistant (MCP),
    Web UI switches, or ESP32 hardware ACKs without polling.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sesi tidak valid.")
    from xiaozhi.services.sse_service import stream_relay_events
    return StreamingResponse(
        stream_relay_events(user["id"], request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/device/relay/{api_slug}/commands")
async def device_relay_commands(request: Request, api_slug: str):
    store = get_store()
    auth_token = request.headers.get("X-Device-Token", "") or request.query_params.get("token", "")
    if not auth_token:
        raise HTTPException(status_code=400, detail="Token diperlukan.")
    owner = store.find_relay_room_by_slug(api_slug)
    if not owner:
        raise HTTPException(status_code=404, detail="API slug tidak ditemukan.")
    if not _device_token_matches(owner, auth_token):
        raise HTTPException(status_code=401, detail="Token perangkat tidak valid.")

    try:
        store.ack_relay_command(owner["user_id"], owner["room_id"])
    except Exception:
        pass

    # Broadcast status online ke SSE browser (dibatasi 5 detik per ruangan agar tidak banjir event)
    now_ts = time.time()
    room_id = owner["room_id"]
    last_broadcast = _relay_device_last_broadcast.get(room_id, 0.0)
    if (now_ts - last_broadcast) >= 5.0:
        _relay_device_last_broadcast[room_id] = now_ts
        try:
            from xiaozhi.services.sse_service import broadcast_relay_device_status
            broadcast_relay_device_status(
                user_id=owner["user_id"],
                room_id=room_id,
                connected=True,
                last_seen_at=utc_now(),
                device_label="ESP32 Terhubung",
            )
        except Exception:
            pass

    room = store.get_relay_room(owner["user_id"], owner["room_id"])
    commands = store.get_pending_relay_commands(owner["user_id"], owner["room_id"])
    relays = [
        {
            "relay": r["relay_number"],
            "name": r.get("nama_relay", f"Relay {r['relay_number']}"),
            "status": r.get("status", "OFF"),
            "desired_status": r.get("desired_status", r.get("status", "OFF")),
        }
        for r in (room.get("relays", []) if room else [])
    ]
    return {"success": True, "commands": commands, "relays": relays}


@router.post("/api/device/relay/{api_slug}/status")
async def device_relay_status(request: Request, api_slug: str, cmd: RealRelayDeviceStatusCommand):
    store = get_store()
    auth_token = cmd.token or request.headers.get("X-Device-Token", "")
    if not auth_token:
        raise HTTPException(status_code=400, detail="Token diperlukan.")
    owner = store.find_relay_room_by_slug(api_slug)
    if not owner:
        raise HTTPException(status_code=404, detail="API slug tidak ditemukan.")
    if not _device_token_matches(owner, auth_token):
        raise HTTPException(status_code=401, detail="Token perangkat tidak valid.")

    store.ack_relay_command(owner["user_id"], owner["room_id"], cmd.command_id)

    # If single relay was reported
    relay_num = cmd.relay or cmd.relay_number
    relay_st = cmd.status or cmd.command or cmd.state
    if relay_num and relay_st:
        try:
            st_clean = normalize_relay_state(str(relay_st))
            store.update_relay_status(owner["user_id"], owner["room_id"], int(relay_num), st_clean)
        except Exception:
            pass

    # If batch relays array was reported
    if cmd.relays:
        for r in cmd.relays:
            if isinstance(r, dict) and "relay" in r and "status" in r:
                try:
                    st_clean = normalize_relay_state(str(r["status"]))
                    store.update_relay_status(owner["user_id"], owner["room_id"], int(r["relay"]), st_clean)
                except Exception:
                    pass

    # 2-way broadcast: device is online and command acknowledged
    try:
        from xiaozhi.services.sse_service import broadcast_relay_device_status, broadcast_relay_update
        broadcast_relay_device_status(
            user_id=owner["user_id"],
            room_id=owner["room_id"],
            connected=True,
            last_seen_at=utc_now(),
            device_label="ESP32/8266 terhubung",
        )
        updated_room = store.get_relay_room(owner["user_id"], owner["room_id"])
        if updated_room:
            for r in updated_room.get("relays", []):
                broadcast_relay_update(
                    user_id=owner["user_id"],
                    room_id=owner["room_id"],
                    relay_number=r["relay_number"],
                    status=r.get("status", "OFF"),
                    source="device_ack",
                    room_name=updated_room.get("nama_tempat", ""),
                    relay_name=r.get("nama_relay", ""),
                    pending="",
                    desired=r.get("status", "OFF"),
                )
    except Exception:
        pass

    return {"success": True}


@router.post("/api/internal/relay/broadcast")
async def internal_relay_broadcast(request: Request):
    """
    Internal webhook called by gateway-go to notify Python's SSE stream
    whenever ESP32 polls commands or confirms hardware status.
    """
    internal_key = _gateway_internal_key()
    supplied_key = request.headers.get("X-Gateway-Internal-Key", "")
    if not internal_key or not supplied_key or not hmac.compare_digest(supplied_key, internal_key):
        raise HTTPException(status_code=401, detail="Akses internal ditolak.")

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "detail": "JSON tidak valid"}, status_code=400)

    user_id = data.get("user_id")
    room_id = data.get("room_id")
    room_name = str(data.get("room_name", ""))
    relays = data.get("relays", [])
    connected = bool(data.get("connected", True))

    if not user_id or not room_id:
        return JSONResponse({"success": False, "detail": "user_id dan room_id diperlukan."}, status_code=400)

    try:
        from xiaozhi.services.sse_service import broadcast_relay_device_status, broadcast_relay_update
        last_seen_at = utc_now() if connected else ""
        device_label = "ESP32 Terhubung" if connected else "ESP32 Terputus"
        if not connected:
            last_room = get_store().get_relay_room(int(user_id), int(room_id))
            if last_room:
                last_seen_at = str(last_room.get("device_last_seen_at") or "")
        broadcast_relay_device_status(
            user_id=int(user_id),
            room_id=int(room_id),
            connected=connected,
            last_seen_at=last_seen_at,
            device_label=device_label,
        )

        for r in (relays if connected else []):
            if isinstance(r, dict) and "relay" in r:
                r_num = int(r["relay"])
                r_status = str(r.get("status", "OFF")).upper()
                r_name = str(r.get("name", f"Relay {r_num}"))
                r_desired = str(r.get("desired_status", r_status)).upper()
                broadcast_relay_update(
                    user_id=int(user_id),
                    room_id=int(room_id),
                    relay_number=r_num,
                    status=r_status,
                    source="device_ack",
                    room_name=room_name,
                    relay_name=r_name,
                    pending="",
                    desired=r_desired,
                )
    except Exception as exc:
        return JSONResponse({"success": False, "detail": str(exc)}, status_code=500)

    return {"success": True}


@router.post("/api/internal/mqtt/event")
async def internal_mqtt_gateway_event(request: Request):
    """Persist gateway MQTT connection and device diagnostic events."""
    internal_key = _gateway_internal_key()
    supplied_key = request.headers.get("X-Gateway-Internal-Key", "")
    if not internal_key or not supplied_key or not hmac.compare_digest(supplied_key, internal_key):
        raise HTTPException(status_code=401, detail="Akses internal ditolak.")

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "detail": "JSON tidak valid"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"success": False, "detail": "Payload harus berupa objek JSON."}, status_code=400)

    event_type = str(data.get("event_type") or "").strip().lower()
    allowed_events = {
        "broker_connecting",
        "broker_connected",
        "broker_connection_lost",
        "broker_connect_timeout",
        "broker_connect_failed",
        "broker_publish_failed",
        "device_online",
        "device_offline",
        "device_error",
    }
    if event_type not in allowed_events:
        return JSONResponse({"success": False, "detail": "Jenis event MQTT tidak dikenal."}, status_code=400)

    raw_broker = str(data.get("broker_url") or "")
    broker_url = ""
    try:
        parsed_broker = urlsplit(raw_broker)
        if parsed_broker.hostname:
            host = parsed_broker.hostname
            if ":" in host:
                host = f"[{host}]"
            if parsed_broker.port:
                host = f"{host}:{parsed_broker.port}"
            broker_url = f"{parsed_broker.scheme}://{host}"[:255]
    except ValueError:
        pass

    error_events = {
        "broker_connection_lost",
        "broker_connect_timeout",
        "broker_connect_failed",
        "broker_publish_failed",
        "device_error",
    }
    severity = "error" if event_type in error_events else ("warning" if event_type == "device_offline" else "info")
    event = {
        "event_type": event_type,
        "severity": severity,
        "connected": bool(data.get("connected", False)),
        "broker_url": broker_url,
        "detail": str(data.get("detail") or "")[:2000],
        "slug": str(data.get("slug") or "")[:100],
        "topic": str(data.get("topic") or "")[:255],
        "cmd_id": str(data.get("cmd_id") or "")[:128],
    }
    try:
        get_store().record_mqtt_gateway_event(event)
    except Exception:
        return JSONResponse({"success": False, "detail": "Gagal menyimpan event MQTT."}, status_code=500)

    return {"success": True}


@router.api_route("/api/relay-nyata/{room_id}/download-project-zip", methods=["GET", "POST"])
async def download_arduino_project_zip(room_id: int, request: Request):
    """
    Generate fully self-contained Plug-and-Play Arduino project .ZIP
    including the custom .ino sketch and pre-bundled libraries (ArduinoJson & PubSubClient).
    Supports both POST (with custom edited code from web UI) and GET.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Silakan login terlebih dahulu.")

    store = get_store()
    room = store.get_relay_room(user["id"], room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Ruangan tidak ditemukan.")

    body = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}

    code = body.get("code", "")
    board_name = body.get("board", "ESP32-C3 (RISC-V)")
    room_name = room.get("nama_tempat", "Smart_Home")
    folder_slug = re.sub(r'[^a-zA-Z0-9_]+', '_', room_name).strip('_') or f"Room_{room_id}"
    topic_slug = slugify_topic_part(str(room.get("api_slug") or room_name))
    folder_name = f"{folder_slug}_ESP32_Relay"

    if not code:
        board_lower = board_name.lower()
        if "esp01" in board_lower or "esp-01" in board_lower or "01s" in board_lower:
            default_pins = [0]  # ESP-01 / ESP-01S WAJIB HANYA 1 RELAY (GPIO 0)
            led_pin = 2
            led_active_low = "true"
        elif "n16r8" in board_lower or ("s3" in board_lower and "16" in board_lower):
            default_pins = [1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 21]
            led_pin = 21
            led_active_low = "false"
        elif "s2" in board_lower:
            default_pins = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
            led_pin = 15
            led_active_low = "false"
        elif "s3" in board_lower:
            default_pins = [1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
            led_pin = 21
            led_active_low = "false"
        elif "c3" in board_lower:
            default_pins = [4, 5, 6, 7, 10, 3, 1, 0]
            led_pin = 8
            led_active_low = "true"
        elif "8266" in board_lower:
            default_pins = [5, 4, 14, 12, 13]  # Pin aman ESP8266 (bebas strapping 0, 2, 15)
            led_pin = 2
            led_active_low = "true"
        else:
            default_pins = [4, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33, 5, 13, 14]
            led_pin = 2
            led_active_low = "false"

        wifi_include = "#include <ESP8266WiFi.h>" if ("8266" in board_lower or "esp01" in board_lower or "esp-01" in board_lower or "01s" in board_lower) else "#include <WiFi.h>"
        num_relays = max(1, min(len(room.get("relays", [])) or int(room.get("jumlah_relay", 2) or 2), len(default_pins)))
        pins_str = ", ".join(str(p) for p in default_pins[:num_relays])
        states_str = ", ".join(["false"] * num_relays)

        code = f"""/*
 * ============================================================
 * FIRMWARE RELAY NYATA XIAOZHI AI (HYBRID GO + MQTT)
 * ============================================================
 * Mikrokontroler: {board_name}
 * Fitur:
 * 1. Hybrid Environment: Auto-detect Wokwi Simulator vs Hardware Fisik
 * 2. Event-Driven MQTT ke Mosquitto (Xiaozhi Gateway)
 * 3. 2-Way Hardware Feedback & LWT (Last Will & Testament)
 * 4. Deduplikasi Command ID (Anti retransmisi)
 * 5. Serial CLI Control: 'status', 'all on', 'all off', '1 on', dll.
 * ============================================================
 */

{wifi_include}
#include <PubSubClient.h>
#include <ArduinoJson.h>
#if defined(ESP32)
#include <esp_wifi.h>
#endif

// ============================================================
// 1. KONFIGURASI WIFI RUMAH
// ============================================================
const char* WIFI_SSID     = "WIFI_RUMAH_ANDA";
const char* WIFI_PASSWORD = "PASSWORD_WIFI";

// ============================================================
// 2. KONFIGURASI MQTT BROKER MOSQUITTO (XIAOZHI GATEWAY)
// ============================================================
const char* MQTT_BROKER   = "mqtt.xiaozhiscig.biz.id";
const int   MQTT_PORT     = 1883;
const char* CLIENT_ID     = "{room.get('api_client_id', 'esp32_relay')}";
const char* DEVICE_TOKEN  = "{room.get('api_token', '')}";

const char* TOPIC_COMMAND      = "xiaozhi/relay/{topic_slug}/set";
const char* TOPIC_STATE        = "xiaozhi/relay/{topic_slug}/state";
const char* TOPIC_AVAILABILITY = "xiaozhi/relay/{topic_slug}/availability";
const char* TOPIC_ERROR        = "xiaozhi/relay/{topic_slug}/err";

// ============================================================
// 3. KONFIGURASI PIN RELAY {board_name}
// ============================================================
const int NUM_RELAYS = {num_relays};
const int RELAY_PINS[NUM_RELAYS] = {{ {pins_str} }};

// ACTIVE LOW: Relay menyala saat pin bernilai LOW (GND)
const bool RELAY_ACTIVE_LOW = true;

bool relayStates[NUM_RELAYS] = {{ {states_str} }};

// Ring buffer untuk deduplikasi command ID
const int DEDUP_SLOTS = 10;
String processedCmds[DEDUP_SLOTS];
int dedupHead = 0;

WiFiClient espClient;
PubSubClient mqttClient(espClient);

unsigned long lastMqttRetry = 0;
unsigned long lastHeartbeat = 0;
unsigned long lastStatusPrint = 0;
unsigned long wifiDisconnectSince = 0;

// ============================================================
// 4. INDIKATOR STATUS & AKTIVITAS LED ONBOARD
// ============================================================
const int STATUS_LED_PIN = {led_pin};
const bool STATUS_LED_ACTIVE_LOW = {led_active_low};

unsigned long ledActivityUntil = 0;
int ledActivityBlinks = 0;

void setStatusLed(bool on) {{
    if (STATUS_LED_PIN < 0) return;
    digitalWrite(STATUS_LED_PIN, STATUS_LED_ACTIVE_LOW ? (on ? LOW : HIGH) : (on ? HIGH : LOW));
}}

void triggerActivityLed(int numBlinks = 2) {{
    ledActivityBlinks = numBlinks * 2;
    ledActivityUntil = millis();
}}

void updateStatusLed() {{
    if (STATUS_LED_PIN < 0) return;
    unsigned long now = millis();

    // 1. Jika OFFLINE (WiFi atau MQTT terputus): LED mati total (tidak berkedip)
    if (WiFi.status() != WL_CONNECTED || !mqttClient.connected()) {{
        setStatusLed(false);
        return;
    }}

    // 2. Jika ada aktivitas data masuk / keluar: Kedip cepat (60ms ON / 60ms OFF)
    if (ledActivityBlinks > 0) {{
        if (now >= ledActivityUntil) {{
            bool turnOn = (ledActivityBlinks % 2 != 0);
            setStatusLed(turnOn);
            ledActivityBlinks--;
            ledActivityUntil = now + 60;
        }}
        return;
    }}

    // 3. Jika ONLINE normal (Standby): Denyut halus (Heartbeat: 80ms ON tiap 1.5 detik)
    unsigned long cycle = now % 1500;
    if (cycle < 80) {{
        setStatusLed(true);
    }} else {{
        setStatusLed(false);
    }}
}}

bool isDuplicate(const String& id) {{
    if (id.length() == 0) return false;
    for (int i = 0; i < DEDUP_SLOTS; i++) {{
        if (processedCmds[i] == id) return true;
    }}
    return false;
}}

void markProcessed(const String& id) {{
    if (id.length() == 0) return;
    processedCmds[dedupHead] = id;
    dedupHead = (dedupHead + 1) % DEDUP_SLOTS;
}}

void applyRelay(int index, bool turnOn) {{
    if (index < 0 || index >= NUM_RELAYS) return;
    uint8_t level = RELAY_ACTIVE_LOW ? (turnOn ? LOW : HIGH) : (turnOn ? HIGH : LOW);
    digitalWrite(RELAY_PINS[index], level);
    relayStates[index] = turnOn;
    Serial.printf("[STATUS RELAY] ⚡ CH %d (GPIO %d) -> %s (Pin Level: %s)\\n",
                  index + 1, RELAY_PINS[index], turnOn ? "🟢 ON (Menyala)" : "⚪ OFF (Padam)", level == HIGH ? "HIGH" : "LOW");
}}

void printSystemStatus() {{
    Serial.println("\\n============================================================");
    Serial.println("   📊 STATUS REAL-TIME: XIAOZHI ESP32 SMART RELAY (24/7)");
    Serial.println("============================================================");
    unsigned long upSec = millis() / 1000;
    unsigned long upDays = upSec / 86400;
    unsigned long upHours = (upSec % 86400) / 3600;
    unsigned long upMins = (upSec % 3600) / 60;
    unsigned long upSecs = upSec % 60;
    if (upDays > 0) {{
        Serial.printf("⏱️  Uptime        : %lu hari, %lu jam, %lu menit, %lu detik\\n", upDays, upHours, upMins, upSecs);
    }} else {{
        Serial.printf("⏱️  Uptime        : %lu jam, %lu menit, %lu detik\\n", upHours, upMins, upSecs);
    }}
    #if defined(ESP32) || defined(ESP8266)
    Serial.printf("💾 RAM Bebas (Heap): %u bytes (Stabil)\\n", ESP.getFreeHeap());
    #endif
    Serial.printf("📶 WiFi Status   : %s\\n", (WiFi.status() == WL_CONNECTED) ? "TERHUBUNG (Online)" : "TERPUTUS (Offline)");
    if (WiFi.status() == WL_CONNECTED) {{
        Serial.printf("🌐 IP Address    : %s\\n", WiFi.localIP().toString().c_str());
        Serial.printf("📡 Sinyal (RSSI) : %d dBm\\n", WiFi.RSSI());
    }}
    Serial.printf("🔌 MQTT Broker   : %s (%s:%d)\\n", mqttClient.connected() ? "TERHUBUNG (Online)" : "TERPUTUS (Offline)", MQTT_BROKER, MQTT_PORT);
    Serial.printf("🏠 Ruangan       : %s\\n", "{room_name}");
    Serial.printf("🆔 Client ID     : %s\\n", CLIENT_ID);
    Serial.println("------------------------------------------------------------");
    Serial.println("⚡ STATUS CHANNEL RELAY SAAT INI:");
    for (int i = 0; i < NUM_RELAYS; i++) {{
        Serial.printf("   • Channel %d (GPIO %2d) : %s\\n", 
                      i + 1, RELAY_PINS[i], relayStates[i] ? "🟢 [ON  / MENYALA]" : "⚪ [OFF / PADAM]");
    }}
    Serial.println("============================================================");
    Serial.println("💡 Bantuan: Ketik 'status' di Serial Monitor untuk cek status kapan saja.");
    Serial.println("💡 Kontrol: Ketik '1 on', '1 off', 'all on', atau 'all off' untuk uji coba.");
    Serial.println("============================================================\\n");
}}

void publishState(int relayIndex, const String& cmdId) {{
    if (!mqttClient.connected()) return;

    StaticJsonDocument<256> doc;
    doc["cmd_id"] = cmdId;
    doc["relay"] = relayIndex + 1;
    doc["status"] = relayStates[relayIndex] ? "ON" : "OFF";
    doc["uptime_sec"] = millis() / 1000;
    doc["rssi"] = WiFi.RSSI();

    char buffer[256];
    size_t len = serializeJson(doc, buffer);
    mqttClient.publish(TOPIC_STATE, (uint8_t*)buffer, len, false);
    triggerActivityLed(2);
    Serial.printf("[STATUS MQTT] 📤 State terkirim ke Broker: CH %d = %s (Cmd: %s)\\n", 
                  relayIndex + 1, relayStates[relayIndex] ? "ON" : "OFF", cmdId.c_str());
}}

void mqttCallback(char* topic, byte* payload, unsigned int length) {{
    triggerActivityLed(2);
    Serial.printf("[MQTT RECV] Pesan diterima di topic [%s]\\n", topic);

    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {{
        Serial.printf("[MQTT ERR] Gagal parse JSON payload: %s\\n", err.c_str());
        return;
    }}

    String cmdId = doc["cmd_id"] | "";
    int relayNum = doc["relay"] | 0;
    String action = doc["action"] | "";
    action.toUpperCase();

    int idx = relayNum - 1;
    if (idx < 0 || idx >= NUM_RELAYS) {{
        Serial.printf("[WARN] Channel %d di luar batas konfigurasi (Max: %d)\\n", relayNum, NUM_RELAYS);
        return;
    }}

    if (isDuplicate(cmdId)) {{
        Serial.printf("[DEDUP] Perintah %s sudah pernah diproses. Mengirim state ulang.\\n", cmdId.c_str());
        publishState(idx, cmdId);
        return;
    }}

    if (action == "ON") {{
        applyRelay(idx, true);
    }} else if (action == "OFF") {{
        applyRelay(idx, false);
    }} else if (action == "TOGGLE") {{
        applyRelay(idx, !relayStates[idx]);
    }} else {{
        return;
    }}

    markProcessed(cmdId);
    publishState(idx, cmdId);
}}

void connectWiFi() {{
    if (WiFi.status() == WL_CONNECTED) return;

    Serial.println("\\n[WIFI] Memulai pemindaian access point...");

    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    #if defined(ESP32)
    // Keep Wi-Fi power save disabled in both simulator and hardware modes to protect MQTT keepalive.
    WiFi.setSleep(false);
    #endif
    WiFi.disconnect(true, true);
    delay(100);

    #if defined(ESP32)
    // Buka frekuensi region Indonesia (Channel 1-13)
    wifi_country_t country = {{
        .cc = "ID",
        .schan = 1,
        .nchan = 13,
        .max_tx_power = 78,
        .policy = WIFI_COUNTRY_POLICY_AUTO
    }};
    esp_wifi_set_country(&country);

    WiFi.setTxPower(WIFI_POWER_18_5dBm);
    #endif

    int n = WiFi.scanNetworks(false, true);
    bool isWokwi = false;
    int targetIndex = -1;

    for (int i = 0; i < n; ++i) {{
        String ssidFound = WiFi.SSID(i);
        if (ssidFound == "Wokwi-GUEST") {{
            isWokwi = true;
        }}
        if (ssidFound == WIFI_SSID) {{
            targetIndex = i;
        }}
    }}

    if (isWokwi && targetIndex == -1) {{
        // Lingkungan Simulator Wokwi
        Serial.println("[WIFI] Lingkungan terdeteksi: SIMULATOR WOKWI");
        Serial.println("[WIFI] Menghubungkan ke access point virtual 'Wokwi-GUEST'...");
        WiFi.scanDelete();
        WiFi.begin("Wokwi-GUEST", "", 6);
    }} else if (targetIndex != -1) {{
        // Lingkungan Hardware Nyata
        int chan = WiFi.channel(targetIndex);
        uint8_t bssid[6] = {{0}};
        memcpy(bssid, WiFi.BSSID(targetIndex), sizeof(bssid));
        Serial.printf("[WIFI] Lingkungan terdeteksi: HARDWARE FISIK NYATA\\n");
        Serial.printf("[WIFI] Mengunci ke '%s' (Kanal: %d, RSSI: %d dBm)...\\n", WIFI_SSID, chan, WiFi.RSSI(targetIndex));
        WiFi.scanDelete();
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD, chan, bssid);
    }} else {{
        // Fallback tangguh: jika SSID belum muncul di scan (sinyal lambat/hidden/cold-boot), tetap hubungkan langsung
        Serial.printf("[WIFI] ⚠️ SSID '%s' belum muncul di scan; mencoba koneksi langsung...\\n", WIFI_SSID);
        WiFi.scanDelete();
        WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    }}

    int timeout = 0;
    while (WiFi.status() != WL_CONNECTED && timeout < 35) {{
        delay(500);
        Serial.print(".");
        timeout++;
    }}
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {{
        Serial.println("[WIFI] ✅ BERHASIL TERHUBUNG!");
        Serial.printf("[WIFI] IP Address  : %s\\n", WiFi.localIP().toString().c_str());
        Serial.printf("[WIFI] Gateway     : %s\\n", WiFi.gatewayIP().toString().c_str());
        Serial.printf("[WIFI] Signal RSSI : %d dBm\\n\\n", WiFi.RSSI());
    }} else {{
        Serial.printf("[WIFI] ❌ Gagal terhubung (Status: %d)\\n", WiFi.status());
    }}
}}

void reconnectMQTT() {{
    if (mqttClient.connected()) return;
    if (WiFi.status() != WL_CONNECTED) return;

    Serial.print("[MQTT] Menghubungkan ke broker Mosquitto...");
    espClient.stop();
    delay(50);

    // Dynamic Client ID: sertakan ID chip hardware unik agar jika 2 board memakai slug sama tidak saling kick
    char dynamicClientId[64];
    #if defined(ESP32)
    uint32_t chipId = (uint32_t)ESP.getEfuseMac();
    snprintf(dynamicClientId, sizeof(dynamicClientId), "%s-%06X", CLIENT_ID, chipId & 0xFFFFFF);
    #elif defined(ESP8266)
    snprintf(dynamicClientId, sizeof(dynamicClientId), "%s-%06X", CLIENT_ID, ESP.getChipId() & 0xFFFFFF);
    #else
    snprintf(dynamicClientId, sizeof(dynamicClientId), "%s", CLIENT_ID);
    #endif

    // LWT: Jika koneksi putus mendadak, Mosquitto otomatis mempublikasikan "offline"
    if (mqttClient.connect(dynamicClientId, TOPIC_AVAILABILITY, 1, true, "offline")) {{
        triggerActivityLed(3);
        Serial.printf(" BERHASIL! (ID: %s)\\n", dynamicClientId);
        mqttClient.publish(TOPIC_AVAILABILITY, "online", true);
        mqttClient.subscribe(TOPIC_COMMAND, 1);
        Serial.printf("[MQTT] Subscribed ke topic: %s\\n", TOPIC_COMMAND);

        for (int i = 0; i < NUM_RELAYS; i++) {{
            publishState(i, "init_boot");
        }}
        Serial.println("[MQTT] ✅ Status awal seluruh relay berhasil dipublikasikan.");
    }} else {{
        Serial.printf(" GAGAL (rc=%d). Coba lagi dalam 5 detik.\\n", mqttClient.state());
    }}
}}

void handleSerialInput() {{
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\\n');
    cmd.trim();
    if (cmd.length() == 0) return;

    if (cmd.equalsIgnoreCase("status") || cmd.equalsIgnoreCase("s")) {{
        printSystemStatus();
    }} else if (cmd.equalsIgnoreCase("help") || cmd.equalsIgnoreCase("?")) {{
        Serial.println("\\n--- DAFTAR PERINTAH SERIAL MONITOR ---");
        Serial.println("status          : Tampilkan ringkasan status sistem & relay lengkap");
        Serial.println("all on          : Nyalakan semua channel relay");
        Serial.println("all off         : Matikan semua channel relay");
        Serial.println("<no> on         : Contoh '1 on' untuk menyalakan Channel 1");
        Serial.println("<no> off        : Contoh '1 off' untuk mematikan Channel 1");
        Serial.println("<no> toggle     : Contoh '1 toggle' untuk toggle Channel 1");
        Serial.println("---------------------------------------\\n");
    }} else if (cmd.equalsIgnoreCase("all on")) {{
        Serial.println("[SERIAL] ⚡ Menyalakan SEMUA channel relay...");
        for (int i = 0; i < NUM_RELAYS; i++) {{ 
            applyRelay(i, true); 
            publishState(i, "serial_all_on"); 
        }}
        printSystemStatus();
    }} else if (cmd.equalsIgnoreCase("all off")) {{
        Serial.println("[SERIAL] ⚡ Mematikan SEMUA channel relay...");
        for (int i = 0; i < NUM_RELAYS; i++) {{ 
            applyRelay(i, false); 
            publishState(i, "serial_all_off"); 
        }}
        printSystemStatus();
    }} else {{
        int spaceIdx = cmd.indexOf(' ');
        int ch = 0;
        String act = "";
        if (spaceIdx > 0) {{
            ch = cmd.substring(0, spaceIdx).toInt();
            act = cmd.substring(spaceIdx + 1);
            act.trim();
            act.toUpperCase();
        }} else {{
            // Tanpa aksi eksplisit (misal hanya angka 1 akibat noise/glitch serial RX),
            // abaikan demi keamanan anti-glitch agar relay tidak ter-toggle sendiri.
            ch = cmd.toInt();
            act = "";
        }}

        int idx = ch - 1;
        if (idx >= 0 && idx < NUM_RELAYS) {{
            if (act == "ON") {{
                applyRelay(idx, true);
                publishState(idx, "serial_cmd");
            }} else if (act == "OFF") {{
                applyRelay(idx, false);
                publishState(idx, "serial_cmd");
            }} else if (act == "TOGGLE") {{
                applyRelay(idx, !relayStates[idx]);
                publishState(idx, "serial_toggle");
            }} else {{
                Serial.printf("[SERIAL WARN] Perintah tidak lengkap untuk channel %d. Gunakan '%d on', '%d off', atau '%d toggle'. Diabaikan demi keamanan.\\n", ch, ch, ch, ch);
            }}
        }} else {{
            Serial.printf("[SERIAL] Perintah '%s' tidak dikenal. Ketik 'help' atau 'status'.\\n", cmd.c_str());
        }}
    }}
}}

void setup() {{
    Serial.begin(115200);
#if defined(ESP32)
    Serial.setTxTimeoutMs(0); // Non-blocking jika dihidupkan dengan charger tanpa PC
#endif
    Serial.setTimeout(50);

    // Sinkronisasi port USB CDC Serial
    unsigned long startSerial = millis();
    while (!Serial && (millis() - startSerial < 3000)) {{
        delay(10);
    }}
    delay(400);

    // Inisialisasi pin relay dengan proteksi anti-chattering saat startup
    for (int i = 0; i < NUM_RELAYS; i++) {{
        digitalWrite(RELAY_PINS[i], RELAY_ACTIVE_LOW ? HIGH : LOW);
        pinMode(RELAY_PINS[i], OUTPUT);
        relayStates[i] = false;
    }}

    // Inisialisasi pin status LED bawaan board
    if (STATUS_LED_PIN >= 0) {{
        pinMode(STATUS_LED_PIN, OUTPUT);
        setStatusLed(false);
    }}

    Serial.println("\\n==========================================");
    Serial.printf(" XIAOZHI SMART RELAY (%s) BOOTED\\n", "{board_name}");
    Serial.printf(" Client ID      : %s\\n", CLIENT_ID);
    Serial.printf(" Active Channels: %d\\n", NUM_RELAYS);
    Serial.println("==========================================");

    connectWiFi();

    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(512);
    mqttClient.setKeepAlive(30);

    if (WiFi.status() == WL_CONNECTED) {{
        reconnectMQTT();
    }}

    printSystemStatus();
}}

void loop() {{
    updateStatusLed();
    unsigned long now = millis();

    // Service MQTT first so serial input or other work cannot starve keepalive packets.
    if (mqttClient.connected() && !mqttClient.loop()) {{
        Serial.printf("[MQTT] Loop terputus (state=%d); reconnect akan dicoba.\\n", mqttClient.state());
    }}

    handleSerialInput();

    // Reconnect Wi-Fi berkala jika koneksi terputus (Failsafe 24/7)
    if (WiFi.status() != WL_CONNECTED) {{
        if (wifiDisconnectSince == 0) {{
            wifiDisconnectSince = now;
        }} else if (now - wifiDisconnectSince >= 300000) {{ // 5 menit terputus total
            Serial.println("\\n[FAILSAFE 24/7] Wi-Fi terputus > 5 menit. Merestart mikrokontroler demi stabilitas...");
            delay(500);
            ESP.restart();
        }}
        if (now - lastMqttRetry >= 10000) {{
            lastMqttRetry = now;
            connectWiFi();
        }}
        return;
    }} else {{
        wifiDisconnectSince = 0;
    }}

    // Reconnect MQTT berkala
    if (!mqttClient.connected()) {{
        if (now - lastMqttRetry >= 5000) {{
            lastMqttRetry = now;
            reconnectMQTT();
        }}
    }} else {{
        // Heartbeat berkala tiap 15 detik (menjaga NAT router firewall tetap terbuka 24/7)
        if (now - lastHeartbeat >= 15000) {{
            lastHeartbeat = now;
            mqttClient.publish(TOPIC_AVAILABILITY, "online", true);
        }}
    }}

    // Tampilkan ringkasan status di Serial Monitor tiap 30 detik
    if (now - lastStatusPrint >= 30000) {{
        lastStatusPrint = now;
        #if defined(ESP32) || defined(ESP8266)
        uint32_t freeRam = ESP.getFreeHeap();
        #else
        uint32_t freeRam = 0;
        #endif
        Serial.printf("[STATUS LIVE] ⏱️ %lus | RAM: %uB | RSSI: %d dBm | MQTT: %s | Relay: [", 
                      now / 1000, freeRam, WiFi.RSSI(), 
                      mqttClient.connected() ? "ONLINE" : "OFFLINE");
        for (int i = 0; i < NUM_RELAYS; i++) {{
            Serial.printf("CH%d:%s%s", i + 1, relayStates[i] ? "ON" : "OFF", (i < NUM_RELAYS - 1) ? " " : "");
        }}
        Serial.println("]");
    }}
}}
"""

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Custom Sketch .ino
        ino_filename = f"{folder_name}/{folder_name}.ino"
        zf.writestr(ino_filename, code.encode("utf-8"))

        # 2. README Plug & Play
        readme_content = f"""====================================================================
  PANDUAN LENGKAP PLUG & PLAY: ESP32 SMART RELAY XIAOZHI
  Ruangan : {room_name} ({board_name})
  Broker  : 163.61.58.235:1883 (Mosquitto)
====================================================================

Langkah 1: Pasang Library ke Arduino IDE (Hanya 1x Saja)
-------------------------------------------------------
Di dalam folder 'libraries/' sudah tersedia 2 file library:
1. 'ArduinoJson.zip'
2. 'PubSubClient.zip'

Cara Pasang:
a. Buka aplikasi Arduino IDE di komputer / laptop Anda.
b. Klik Menu: Sketch -> Include Library -> Add .ZIP Library...
c. Pilih file 'ArduinoJson.zip' dari folder libraries ini.
d. Ulangi untuk file 'PubSubClient.zip'.

Langkah 2: Buka dan Upload Kodingan
-------------------------------------------------------
a. Buka file '{folder_name}.ino' di Arduino IDE.
b. Pada menu 'Tools' -> 'Board', pilih board ESP32 Anda ({board_name}).
c. Hubungkan kabel USB ESP32 ke laptop, lalu pilih Port COM yang muncul.
d. Klik tombol panah kanan (Upload).

SELESAI!
ESP32 Anda otomatis menyambung ke Wi-Fi dan langsung sinkron
dengan website https://xiaozhiscig.biz.id/relay-nyata
====================================================================
"""
        zf.writestr(f"{folder_name}/README_PANDUAN_PLUG_PLAY.txt", readme_content.encode("utf-8"))

        # 3. Include official library zips
        static_libs_dir = Path(__file__).resolve().parent.parent / "static" / "download" / "arduino_libs"
        lib_arduinojson = static_libs_dir / "ArduinoJson.zip"
        lib_pubsub = static_libs_dir / "PubSubClient.zip"

        if lib_arduinojson.exists():
            zf.write(lib_arduinojson, f"{folder_name}/libraries/ArduinoJson.zip")
        if lib_pubsub.exists():
            zf.write(lib_pubsub, f"{folder_name}/libraries/PubSubClient.zip")

    zip_buffer.seek(0)
    zip_bytes = zip_buffer.getvalue()

    filename = f"{folder_slug}_esp32_relay_complete.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        }
    )
