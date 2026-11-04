# -*- coding: utf-8 -*-
"""Small, safe helpers for preparing and compacting stored chat history for MCP responses and persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

logger = logging.getLogger("xiaozhi.chat_memory")

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|credential)",
    re.IGNORECASE,
)
_IMPORTANT_KEYS = (
    "success",
    "status",
    "message",
    "title",
    "name",
    "query",
    "topic",
    "date",
    "time",
    "created_at",
    "id",
    "total",
    "count",
    "error",
    "result",
    "data",
    "items",
    "results",
)

ARCHIVE_BASE_DIR = os.path.join("data", "chat_payload_archives")


def _compact_value(value: Any, *, depth: int = 0, max_depth: int = 3) -> Any:
    if isinstance(value, dict):
        keys = list(value)
        ordered = [key for key in _IMPORTANT_KEYS if key in value]
        ordered.extend(key for key in keys if key not in ordered)
        selected = ordered[:12]
        compacted = {}
        for key in selected:
            label = str(key)
            if _SENSITIVE_KEY.search(label):
                compacted[label] = "[disamarkan]"
            elif depth >= max_depth:
                compacted[label] = "[objek bertingkat disimpan di riwayat]"
            else:
                compacted[label] = _compact_value(value[key], depth=depth + 1, max_depth=max_depth)
        if len(keys) > len(selected):
            compacted["_field_lainnya"] = len(keys) - len(selected)
        return compacted

    if isinstance(value, (list, tuple)):
        selected = list(value[:4])
        result = [_compact_value(item, depth=depth + 1, max_depth=max_depth) for item in selected]
        if len(value) > len(selected):
            result.append({"_item_lainnya": len(value) - len(selected)})
        return result

    if isinstance(value, str):
        text = value.strip()
        if len(text) > 240:
            return text[:237].rstrip() + "..."
        return text

    if value is None or isinstance(value, (bool, int, float)):
        return value

    text = str(value)
    return text[:240] + ("..." if len(text) > 240 else "")


def summarize_history_payload(value: Any, *, max_chars: int = 1400) -> Any:
    """Return a bounded, redacted preview while leaving the stored payload unchanged."""
    if value is None:
        return None

    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            text = value.strip()
            return text[:max_chars] + ("... [dipotong untuk konteks; payload asli tetap tersimpan]" if len(text) > max_chars else "")

    compacted = _compact_value(parsed)
    rendered = json.dumps(compacted, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(rendered) <= max_chars:
        return compacted
    return {
        "ringkasan": rendered[: max_chars - 48].rstrip() + "...",
        "catatan": "Pratinjau dibatasi; payload asli tetap tersimpan di riwayat.",
    }


def format_bytes_human(num_bytes: int) -> str:
    """Format bytes to human readable string (B, KB, MB, GB)."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    elif num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.2f} KB"
    elif num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def archive_payload_to_disk(
    raw_payload: Any,
    owner_id: int,
    history_id: int,
    field_name: str,
    base_dir: str = ARCHIVE_BASE_DIR,
) -> Dict[str, Any]:
    """
    Simpan salinan asli payload yang persis ke arsip operasional terkompresi gzip di penyimpanan persisten.
    Menghitung checksum SHA-256 untuk verifikasi integritas data.
    """
    if isinstance(raw_payload, bytes):
        raw_bytes = raw_payload
    elif isinstance(raw_payload, str):
        raw_bytes = raw_payload.encode("utf-8")
    else:
        raw_bytes = json.dumps(raw_payload, ensure_ascii=False, default=str).encode("utf-8")

    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
    short_hash = sha256_hash[:12]
    original_size = len(raw_bytes)

    owner_folder = os.path.join(base_dir, str(owner_id))
    os.makedirs(owner_folder, exist_ok=True)

    filename = f"{history_id}_{field_name}_{short_hash}.json.gz"
    target_path = os.path.join(owner_folder, filename)
    relative_pointer = f"{owner_id}/{filename}"

    # Compress with gzip
    compressed_data = gzip.compress(raw_bytes)
    compressed_size = len(compressed_data)

    # Atomic write to temporary file then replace
    temp_path = target_path + f".tmp_{os.getpid()}"
    with open(temp_path, "wb") as f:
        f.write(compressed_data)
    os.replace(temp_path, target_path)

    # Verification: Ensure file exists and reads back correctly
    if not os.path.exists(target_path) or os.path.getsize(target_path) != compressed_size:
        raise IOError(f"Verifikasi arsip gagal untuk {target_path}")

    return {
        "pointer": relative_pointer,
        "sha256": sha256_hash,
        "original_bytes": original_size,
        "compressed_bytes": compressed_size,
    }


def read_archived_payload_from_disk(
    pointer: str,
    base_dir: str = ARCHIVE_BASE_DIR,
) -> Tuple[bool, Any, str]:
    """
    Membaca dan mendekompresi payload asli dari arsip persisten dengan validasi path traversal yang aman.
    """
    clean_pointer = str(pointer or "").strip().replace("\\", "/")
    if not clean_pointer or ".." in clean_pointer or clean_pointer.startswith("/"):
        return False, None, "Pointer arsip tidak valid atau melanggar batasan direktori."

    full_path = os.path.normpath(os.path.join(base_dir, clean_pointer))
    norm_base = os.path.normpath(base_dir)

    try:
        common = os.path.commonpath([norm_base, full_path])
        if common != norm_base:
            return False, None, "Akses direktori di luar arsip ditolak."
    except Exception:
        return False, None, "Validasi path arsip gagal."

    if not os.path.exists(full_path):
        return False, None, f"File arsip tidak ditemukan: {clean_pointer}"

    try:
        with gzip.open(full_path, "rb") as gz:
            raw_bytes = gz.read()
        raw_text = raw_bytes.decode("utf-8")
        try:
            parsed = json.loads(raw_text)
            return True, parsed, ""
        except (TypeError, json.JSONDecodeError):
            return True, raw_text, ""
    except Exception as e:
        logger.exception("Gagal membaca arsip payload: %s", clean_pointer)
        return False, None, f"Gagal mendekompresi arsip: {str(e)}"


def compact_single_history_record(
    store: Any,
    record: Dict[str, Any],
    min_size_bytes: int = 2048,
    base_dir: str = ARCHIVE_BASE_DIR,
) -> Dict[str, Any]:
    """
    Periksa dan lakukan kompresi/arsip terhadap request_payload dan response_payload dari sebuah record riwayat.
    Hanya mengganti payload inline dengan ringkasan terstruktur dan pointer arsip persisten jika ukuran >= min_size_bytes.
    """
    history_id = int(record["id"])
    owner_id = int(record["owner_id"])

    req_payload = record.get("request_payload")
    res_payload = record.get("response_payload")

    new_req = None
    new_res = None
    saved_bytes = 0
    compacted_fields = []

    # Helper to check if payload is already archived
    def is_already_archived(val: Any) -> bool:
        if isinstance(val, dict):
            return bool(val.get("_archived"))
        if isinstance(val, str) and '"_archived": true' in val:
            return True
        return False

    # Process Request Payload
    if req_payload and not is_already_archived(req_payload):
        req_str = req_payload if isinstance(req_payload, str) else json.dumps(req_payload, default=str)
        if len(req_str.encode("utf-8")) >= min_size_bytes:
            meta = archive_payload_to_disk(req_payload, owner_id, history_id, "req", base_dir)
            summary = summarize_history_payload(req_payload, max_chars=600)
            compacted_obj = {
                "_compacted": True,
                "_archived": True,
                "_archive_pointer": meta["pointer"],
                "_original_bytes": meta["original_bytes"],
                "_compressed_bytes": meta["compressed_bytes"],
                "_sha256": meta["sha256"],
                "_compacted_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
            }
            new_req = json.dumps(compacted_obj, ensure_ascii=False)
            saved_bytes += (meta["original_bytes"] - len(new_req.encode("utf-8")))
            compacted_fields.append("request_payload")

    # Process Response Payload
    if res_payload and not is_already_archived(res_payload):
        res_str = res_payload if isinstance(res_payload, str) else json.dumps(res_payload, default=str)
        if len(res_str.encode("utf-8")) >= min_size_bytes:
            meta = archive_payload_to_disk(res_payload, owner_id, history_id, "res", base_dir)
            summary = summarize_history_payload(res_payload, max_chars=800)
            compacted_obj = {
                "_compacted": True,
                "_archived": True,
                "_archive_pointer": meta["pointer"],
                "_original_bytes": meta["original_bytes"],
                "_compressed_bytes": meta["compressed_bytes"],
                "_sha256": meta["sha256"],
                "_compacted_at": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
            }
            new_res = json.dumps(compacted_obj, ensure_ascii=False)
            saved_bytes += (meta["original_bytes"] - len(new_res.encode("utf-8")))
            compacted_fields.append("response_payload")

    if new_req is not None or new_res is not None:
        store.update_chat_payloads(
            chat_id=history_id,
            owner_id=owner_id,
            request_payload=new_req,
            response_payload=new_res,
        )
        return {
            "compacted": True,
            "fields": compacted_fields,
            "saved_bytes": max(0, saved_bytes),
        }

    return {"compacted": False, "fields": [], "saved_bytes": 0}


def run_chat_memory_compactor_batch(
    store: Any,
    *,
    older_than_days: int = 7,
    min_size_bytes: int = 2048,
    batch_size: int = 50,
    owner_id: Optional[int] = None,
    max_seconds: float = 4.0,
    base_dir: str = ARCHIVE_BASE_DIR,
) -> Dict[str, Any]:
    """
    Jalankan compactor secara bertahap (batch kecil) dengan batasan waktu/CPU agar ringan di VPS.
    Menjaga identitas percakapan, user_message, xiaozhi_answer, dan metadata tetap utuh.
    """
    start_time = time.time()
    candidates = store.find_chat_compaction_candidates(
        older_than_days=older_than_days,
        min_size_bytes=min_size_bytes,
        limit=batch_size,
        owner_id=owner_id,
    )

    processed_count = 0
    compacted_count = 0
    total_saved_bytes = 0
    errors: List[Dict[str, Any]] = []

    for item in candidates:
        # Check time budget to protect VPS CPU
        if (time.time() - start_time) > max_seconds:
            logger.info("Compaction batch dihentikan sementara (mencapai batas anggaran waktu %.1fs)", max_seconds)
            break

        processed_count += 1
        try:
            res = compact_single_history_record(
                store,
                item,
                min_size_bytes=min_size_bytes,
                base_dir=base_dir,
            )
            if res.get("compacted"):
                compacted_count += 1
                total_saved_bytes += res.get("saved_bytes", 0)
        except Exception as exc:
            logger.exception("Error compacting chat record %s: %s", item.get("id"), exc)
            errors.append({"history_id": item.get("id"), "error": str(exc)})

    duration_ms = round((time.time() - start_time) * 1000, 2)
    has_more = len(candidates) >= batch_size and (time.time() - start_time) <= max_seconds

    return {
        "success": True,
        "processed_count": processed_count,
        "compacted_count": compacted_count,
        "saved_bytes": total_saved_bytes,
        "saved_bytes_human": format_bytes_human(total_saved_bytes),
        "duration_ms": duration_ms,
        "has_more": has_more,
        "errors": errors,
    }


def get_chat_memory_archive_stats(
    store: Any,
    owner_id: Optional[int] = None,
    base_dir: str = ARCHIVE_BASE_DIR,
) -> Dict[str, Any]:
    """
    Hitung ringkasan statistik penggunaan disk arsip payload dan antrean kompresi riwayat chat.
    """
    total_files = 0
    total_disk_bytes = 0

    if os.path.exists(base_dir):
        for root, _, files in os.walk(base_dir):
            if owner_id is not None:
                # Filter to specific owner folder
                rel = os.path.relpath(root, base_dir)
                if rel != "." and rel.split(os.sep)[0] != str(owner_id):
                    continue
            for f in files:
                if f.endswith(".json.gz"):
                    total_files += 1
                    try:
                        total_disk_bytes += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass

    # Count candidates in DB older than 7 days (> 2KB)
    try:
        candidates_7d = len(store.find_chat_compaction_candidates(
            older_than_days=7,
            min_size_bytes=2048,
            limit=200,
            owner_id=owner_id,
        ))
    except Exception:
        candidates_7d = 0

    return {
        "archive_files_count": total_files,
        "archive_disk_usage_bytes": total_disk_bytes,
        "archive_disk_usage_human": format_bytes_human(total_disk_bytes),
        "candidates_older_than_7d": candidates_7d,
    }


def resolve_original_history_payload(
    record: Dict[str, Any],
    field_name: str = "response_payload",
    base_dir: str = ARCHIVE_BASE_DIR,
) -> Tuple[bool, Any, str]:
    """
    Mengambil payload asli: jika masih inline, kembalikan langsung; jika sudah diarsipkan, baca dari file gzip.
    """
    raw_val = record.get(field_name)
    if raw_val is None:
        return True, None, "Payload kosong."

    parsed = raw_val
    if isinstance(raw_val, str):
        try:
            parsed = json.loads(raw_val)
        except Exception:
            return True, raw_val, ""

    if isinstance(parsed, dict) and parsed.get("_archived") and parsed.get("_archive_pointer"):
        pointer = parsed["_archive_pointer"]
        return read_archived_payload_from_disk(pointer, base_dir=base_dir)

    return True, parsed, ""


def resolve_target_date(date_str: str = "today") -> str:
    """Resolve natural Indonesian/English date relative keyword to YYYY-MM-DD."""
    now_utc = datetime.now(timezone.utc)
    s = str(date_str or "today").strip().lower()
    if s in ("today", "hari_ini", "hari ini", ""):
        return now_utc.strftime("%Y-%m-%d")
    elif s in ("yesterday", "kemarin"):
        return (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
    elif re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return now_utc.strftime("%Y-%m-%d")


def build_daily_chat_recap(
    store: Any,
    owner_id: int,
    slot_number: Optional[int] = None,
    target_date: Optional[str] = "today",
) -> Dict[str, Any]:
    """
    Menyusun rekap aktivitas harian percakapan, musik, dan IoT terisolasi per user dan slot.
    Menghasilkan req_id unik untuk pelacakan.
    """
    req_id = f"recap_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    clean_date = resolve_target_date(target_date or "today")
    clean_slot = int(slot_number) if slot_number and str(slot_number).isdigit() and 1 <= int(slot_number) <= 3 else None

    records = store.list_chat_history(
        owner_id=int(owner_id),
        limit=300,
        date=clean_date,
        slot_number=clean_slot,
        strict_slot=bool(clean_slot),
    )

    songs_played: List[Dict[str, Any]] = []
    seen_songs = set()
    iot_commands: List[Dict[str, Any]] = []
    topics: List[str] = []
    seen_topics = set()
    preview_items: List[Dict[str, Any]] = []

    for r in records:
        tool_name = str(r.get("tool_name") or "").strip()
        user_msg = str(r.get("user_message") or "").strip()
        ai_ans = str(r.get("xiaozhi_answer") or "").strip()
        created_at = str(r.get("created_at") or "")
        item_slot = r.get("slot_number") or 1

        # Collect preview
        preview_items.append({
            "id": r.get("id"),
            "tool_name": tool_name,
            "user_message": user_msg,
            "xiaozhi_answer": ai_ans[:140] + ("..." if len(ai_ans) > 140 else ""),
            "created_at": created_at,
            "slot_number": item_slot,
        })

        # Track songs played
        if tool_name in ("play_youtube_song", "play_playlist_song"):
            title = user_msg
            if ai_ans and ("Memutar lagu" in ai_ans or "Ditemukan lagu" in ai_ans):
                match = re.search(r"'(.*?)'", ai_ans)
                if match:
                    title = match.group(1)
            song_key = title.lower()
            if song_key and song_key not in seen_songs:
                seen_songs.add(song_key)
                songs_played.append({
                    "title": title,
                    "tool": tool_name,
                    "time": created_at[11:16] if len(created_at) >= 16 else "",
                })

        # Track IoT / Relay commands
        elif "relay" in tool_name or tool_name in ("control_relay", "set_relay_state", "toggle_relay", "smarthome"):
            cmd_desc = user_msg or tool_name
            iot_commands.append({
                "command": cmd_desc,
                "tool": tool_name,
                "time": created_at[11:16] if len(created_at) >= 16 else "",
            })

        # Extract topics from user queries
        if user_msg and not tool_name.startswith("xiaozhi_ws") and len(user_msg) >= 4:
            clean_q = user_msg.strip()
            if not any(clean_q.lower().startswith(x) for x in ["panggil tool", "play", "putar", "1", "2", "3"]):
                topic_snippet = clean_q[:80]
                if topic_snippet.lower() not in seen_topics:
                    seen_topics.add(topic_snippet.lower())
                    topics.append(topic_snippet)

    total_chats = len(records)
    slot_label = f"Slot {clean_slot}" if clean_slot else "Semua Slot"

    # Generate friendly Indonesian summary for voice / TTS
    parts = []
    parts.append(f"Rekap aktivitas untuk {slot_label} pada tanggal {clean_date}: Terjadi total {total_chats} percakapan.")
    if songs_played:
        song_names = ", ".join(f"'{s['title']}'" for s in songs_played[:4])
        parts.append(f"Memutar {len(songs_played)} lagu, di antaranya {song_names}.")
    if iot_commands:
        parts.append(f"Mengeksekusi {len(iot_commands)} perintah perangkat pintar atau saklar relay.")
    if topics:
        top_list = "; ".join(topics[:3])
        parts.append(f"Topik obrolan meliputi: {top_list}.")
    if not songs_played and not iot_commands and not topics and total_chats == 0:
        summary_text = f"Belum ada aktivitas percakapan tercatat untuk {slot_label} pada tanggal {clean_date}."
    else:
        summary_text = " ".join(parts)

    return {
        "success": True,
        "req_id": req_id,
        "date": clean_date,
        "slot_number": clean_slot,
        "slot_label": slot_label,
        "stats": {
            "total_chats": total_chats,
            "songs_count": len(songs_played),
            "iot_count": len(iot_commands),
            "topics_count": len(topics),
        },
        "summary_text": summary_text,
        "songs_played": songs_played,
        "iot_commands": iot_commands,
        "key_topics": topics[:10],
        "items_preview": preview_items[:15],
    }


def extract_action_items_from_history(
    store: Any,
    owner_id: int,
    slot_number: Optional[int] = None,
    target_date: Optional[str] = "today",
) -> Dict[str, Any]:
    """
    Mendeteksi tugas, janji temu, dan to-do list dari riwayat percakapan pengguna secara otomatis.
    Menghasilkan req_id unik untuk pelacakan.
    """
    req_id = f"todo_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    clean_date = resolve_target_date(target_date or "today")
    clean_slot = int(slot_number) if slot_number and str(slot_number).isdigit() and 1 <= int(slot_number) <= 3 else None

    records = store.list_chat_history(
        owner_id=int(owner_id),
        limit=300,
        date=clean_date,
        slot_number=clean_slot,
        strict_slot=bool(clean_slot),
    )

    task_pattern = re.compile(
        r"\b(ingatkan|ingat|jangan lupa|catat|harus|perlu|jadwal|tugas|deadline|meeting|rapat|kerjakan|selesaikan|hubungi|telpon|telepon|wa|whatsapp|chat|kirim|beli|bayar|pesan|siapkan)\b",
        re.IGNORECASE,
    )
    skip_pattern = re.compile(
        r"\b(play|putar lagu|nyalakan|matikan|cuaca|bmkg|buka|tutup|list playlist)\b",
        re.IGNORECASE,
    )

    tasks: List[Dict[str, Any]] = []
    seen_texts = set()

    for idx, r in enumerate(reversed(records), 1):
        user_msg = str(r.get("user_message") or "").strip()
        if not user_msg or len(user_msg) < 5:
            continue

        # Skip hardware commands unless they explicitly contain reminder/task keywords
        if skip_pattern.search(user_msg) and not any(k in user_msg.lower() for k in ["ingatkan", "jangan lupa", "catat", "harus", "jadwal"]):
            continue

        if task_pattern.search(user_msg):
            low = user_msg.lower()
            category = "Tugas"
            if any(k in low for k in ["meeting", "rapat", "jadwal", "janji"]):
                category = "Janji Temu"
            elif any(k in low for k in ["ingatkan", "jangan lupa", "catat"]):
                category = "Pengingat"
            elif any(k in low for k in ["beli", "bayar", "pesan"]):
                category = "Belanja & Finansial"
            elif any(k in low for k in ["hubungi", "telpon", "telepon", "wa", "whatsapp", "kirim"]):
                category = "Komunikasi"

            clean_task = re.sub(r"^(tolong\s+)?(ingatkan\s+(saya\s+)?|catat\s+(bahwa\s+)?|jangan\s+lupa\s+)", "", user_msg, flags=re.IGNORECASE).strip()
            clean_task = clean_task[0].upper() + clean_task[1:] if clean_task else user_msg

            norm_key = clean_task.lower()
            if norm_key in seen_texts:
                continue
            seen_texts.add(norm_key)

            created_at = str(r.get("created_at") or "")
            time_str = created_at[11:16] if len(created_at) >= 16 else ""

            tasks.append({
                "id": len(tasks) + 1,
                "task": clean_task,
                "category": category,
                "raw_message": user_msg,
                "source_chat_id": r.get("id"),
                "time": time_str,
                "created_at": created_at,
                "slot_number": r.get("slot_number") or 1,
            })

    slot_label = f"Slot {clean_slot}" if clean_slot else "Semua Slot"
    if tasks:
        task_items_str = ", ".join(f"{t['id']}. {t['task']}" for t in tasks[:4])
        summary_text = f"Ditemukan {len(tasks)} tugas atau janji dari percakapan {slot_label} pada tanggal {clean_date}: {task_items_str}."
    else:
        summary_text = f"Tidak ada komitmen tugas atau janji temu yang terdeteksi dari percakapan {slot_label} pada tanggal {clean_date}."

    return {
        "success": True,
        "req_id": req_id,
        "date": clean_date,
        "slot_number": clean_slot,
        "slot_label": slot_label,
        "total_tasks": len(tasks),
        "summary_text": summary_text,
        "tasks": tasks,
    }
