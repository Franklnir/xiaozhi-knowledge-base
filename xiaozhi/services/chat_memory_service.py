# -*- coding: utf-8 -*-
"""Small, safe helpers for preparing and compacting stored chat history for MCP responses and persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

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
