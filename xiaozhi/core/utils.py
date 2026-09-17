import hashlib
import ipaddress
import json
import re
import secrets
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request as UrlRequest, build_opener

from xiaozhi.config import (
    API_IMPORT_MAX_BYTES,
    API_IMPORT_MAX_CONTENT_BYTES,
    API_IMPORT_MAX_ITEMS,
    API_IMPORT_TIMEOUT,
    API_SEARCH_EXCERPT_BYTES,
    CHAT_HISTORY_MAX_TEXT_BYTES,
    EMOTE_ALIASES,
    LIVE_API_CATEGORY,
    LIVE_API_SOURCE_TYPE,
    SEARCH_ALIASES,
    SEARCH_STOPWORDS,
)


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


safe_url_opener = build_opener(NoRedirectHandler)


# ── Basic Utilities ────────────────────────────────────────────────────────

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seconds_since_iso(value: str) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds()
    except (TypeError, ValueError):
        return None


def utf8_size(value: str) -> int:
    return len((value or "").encode("utf-8"))


def format_size_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.2f} MB"


def content_size_metadata(content: str) -> Dict[str, Any]:
    size_bytes = utf8_size(content)
    return {
        "content_size_bytes": size_bytes,
        "content_size_mb": round(size_bytes / (1024 * 1024), 2),
        "content_size_label": format_size_mb(size_bytes),
    }


def clamp_text_bytes(value: str, max_bytes: int = CHAT_HISTORY_MAX_TEXT_BYTES) -> str:
    value = (value or "").replace("\x00", "").strip()
    if utf8_size(value) <= max_bytes:
        return value
    notice = f"\n\n[Dipangkas karena melebihi {format_size_mb(max_bytes)}.]"
    available = max_bytes - utf8_size(notice)
    if available <= 0:
        return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore").strip()
    clipped = value.encode("utf-8")[:available].decode("utf-8", errors="ignore").strip()
    return f"{clipped}{notice}"


def serialize_history_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return clamp_text_bytes(value)
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        text = str(value)
    return clamp_text_bytes(text)


# ── Text Extraction ────────────────────────────────────────────────────────

def extract_text_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (extract_text_value(item) for item in value))).strip()
    if isinstance(value, dict):
        for key in ("content", "text", "message", "transcript", "answer", "response"):
            text = extract_text_value(value.get(key))
            if text:
                return text
    return ""


def extract_emote_value(value: Any) -> str:
    if isinstance(value, str):
        emote = value.strip()
        if utf8_size(emote) > 32:
            return ""
        return EMOTE_ALIASES.get(emote.lower(), emote)
    if isinstance(value, list):
        for item in value:
            emote = extract_emote_value(item)
            if emote:
                return emote
        return ""
    if isinstance(value, dict):
        for key in ("emoji", "emote", "emotion", "expression", "face"):
            emote = extract_emote_value(value.get(key))
            if emote:
                return emote
        for key in ("metadata", "meta", "extra"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                emote = extract_emote_value(nested)
                if emote:
                    return emote
    return ""


def text_with_emote(content: str, emote: str) -> str:
    content = (content or "").strip()
    emote = (emote or "").strip()
    if not emote:
        return content
    if not content:
        return emote
    if emote in content:
        return content
    return f"{emote} {content}"


def collect_chat_messages(value: Any) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            messages.extend(collect_chat_messages(item))
        return messages
    if not isinstance(value, dict):
        return messages

    role_value = str(
        value.get("role")
        or value.get("sender")
        or value.get("speaker")
        or value.get("type")
        or value.get("author")
        or ""
    ).lower()
    content = extract_text_value(value)
    emote = extract_emote_value(value)
    if content:
        content = text_with_emote(content, emote)
        if role_value in {"user", "human", "client", "request"} or "user" in role_value:
            messages.append({"role": "user", "content": content})
        elif role_value in {"assistant", "ai", "bot", "xiaozhi", "response"} or "assistant" in role_value or "ai" == role_value:
            messages.append({"role": "assistant", "content": content})

    for key in ("messages", "chat", "conversation", "history", "data", "payload", "result"):
        nested = value.get(key)
        if isinstance(nested, (dict, list)):
            messages.extend(collect_chat_messages(nested))
    return messages


# ── Search ─────────────────────────────────────────────────────────────────

def tokenize_search_text(value: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", (value or "").lower())


def expanded_search_terms(value: str) -> List[str]:
    terms: List[str] = []
    seen = set()
    for token in tokenize_search_text(value):
        if len(token) < 3 or token in SEARCH_STOPWORDS:
            continue
        candidates = [token, *SEARCH_ALIASES.get(token, [])]
        for candidate in candidates:
            candidate = candidate.strip().lower()
            if candidate and candidate not in seen:
                seen.add(candidate)
                terms.append(candidate)
    return terms


def material_search_score(item: Dict[str, Any], keyword: str) -> int:
    phrase = " ".join((keyword or "").lower().split())
    terms = expanded_search_terms(keyword)
    identity = " ".join(
        [
            item.get("title", ""),
            item.get("category", ""),
            item.get("keywords", ""),
            item.get("api_label", ""),
            item.get("api_url", ""),
        ]
    ).lower()
    content = item.get("content", "").lower()
    score = 0
    if phrase and phrase in identity:
        score += 100
    if phrase and phrase in content:
        score += 60
    for term in terms:
        if term in identity:
            score += 24
        if term in content:
            score += 6 if len(term) > 3 else 3
    if item.get("source_type") == LIVE_API_SOURCE_TYPE and score:
        score += 10
    return score


def rank_materials(rows: List[Dict[str, Any]], keyword: str, *, include_zero: bool = False) -> List[Dict[str, Any]]:
    scored = []
    for item in rows:
        score = material_search_score(item, keyword)
        if score or include_zero:
            enriched = dict(item)
            enriched["match_score"] = score
            scored.append(enriched)
    return sorted(scored, key=lambda item: (int(item.get("match_score", 0)), int(item.get("id", 0))), reverse=True)


def focused_answer_context(content: str, keyword: str, max_bytes: int = API_SEARCH_EXCERPT_BYTES) -> str:
    content = (content or "").strip()
    if not content or utf8_size(content) <= max_bytes:
        return content
    terms = expanded_search_terms(keyword)
    if not terms:
        return latest_text_within_bytes(content, max_bytes)
    lines = content.splitlines()
    selected = set()
    for index, line in enumerate(lines):
        lower = line.lower()
        if any(term in lower for term in terms):
            selected.update(range(max(0, index - 2), min(len(lines), index + 3)))
    if not selected:
        return latest_text_within_bytes(content, max_bytes)
    excerpt = "\n".join(lines[index] for index in sorted(selected)).strip()
    return truncate_material_content(excerpt, max_bytes)


# ── Text Cleaning ──────────────────────────────────────────────────────────

def clean_text(value: str, *, max_len: int, field: str, min_len: int = 0) -> str:
    value = " ".join((value or "").replace("\x00", "").split())
    if len(value) < min_len:
        raise ValueError(f"{field} wajib diisi.")
    if len(value) > max_len:
        raise ValueError(f"{field} terlalu panjang. Maksimal {max_len} karakter.")
    return value


def slugify_topic_part(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or secrets.token_hex(4)


def normalize_voice_command(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_relay_client_id(value: str, api_slug: str) -> str:
    client_id = str(value or "").strip()
    if not client_id:
        return f"device-{api_slug}"
    for legacy_prefix in ("esp32-8266-", "esp8266-"):
        if client_id.startswith(legacy_prefix):
            return f"device-{client_id[len(legacy_prefix):]}"
    return client_id


def parse_int_range(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} wajib berupa angka.") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{field} harus antara {minimum} dan {maximum}.")
    return number


def parse_limit_value(value: Any, *, field: str) -> int:
    if value is None or str(value).strip() == "":
        return 0
    return parse_int_range(value, field=field, minimum=0, maximum=1_000_000)


def count_text_words(value: str) -> int:
    return len(re.findall(r"\S+", value or ""))


def normalize_relay_state(value: Any, *, field: str = "Status relay") -> str:
    status = str(value or "").strip().upper()
    if status in {"TRUE", "1", "ON", "NYALA", "HIDUP"}:
        return "ON"
    if status in {"FALSE", "0", "OFF", "MATI"}:
        return "OFF"
    raise ValueError(f"{field} harus ON atau OFF.")


def clean_multiline(
    value: str,
    *,
    max_len: Optional[int] = None,
    max_bytes: Optional[int] = None,
    field: str,
    min_len: int = 0,
) -> str:
    value = (value or "").replace("\x00", "").strip()
    if len(value) < min_len:
        raise ValueError(f"{field} wajib diisi.")
    if max_len is not None and len(value) > max_len:
        raise ValueError(f"{field} terlalu panjang. Maksimal {max_len} karakter.")
    if max_bytes is not None and utf8_size(value) > max_bytes:
        raise ValueError(f"{field} terlalu besar. Maksimal {format_size_mb(max_bytes)}.")
    return value


def compact_text(value: Any, *, max_len: Optional[int] = None) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    if max_len is not None and len(text) > max_len:
        return text[: max_len - 3].rstrip() + "..."
    return text


# ── API Helpers ────────────────────────────────────────────────────────────

def safe_api_label(api_url: str) -> str:
    parsed = urlparse(api_url)
    path = parsed.path or "/"
    host = parsed.hostname or parsed.netloc
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}{path}"


def validate_external_api_url(api_url: str) -> str:
    api_url = clean_multiline(api_url, max_len=2000, min_len=8, field="URL API")
    parsed = urlparse(api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("URL API harus berupa endpoint http:// atau https:// yang valid.")
    host = parsed.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("URL API lokal tidak diizinkan.")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Host API tidak bisa di-resolve.") from exc
    for address in addresses:
        ip_text = address[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ValueError("Alamat IP API tidak valid.") from exc
        if not ip.is_global:
            raise ValueError("URL API harus mengarah ke host publik.")
    return api_url


def fetch_api_json(api_url: str) -> Any:
    api_url = validate_external_api_url(api_url)
    request = UrlRequest(
        api_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "XiaozhiIndonesia/1.0",
        },
        method="GET",
    )
    try:
        with safe_url_opener.open(request, timeout=API_IMPORT_TIMEOUT) as response:
            payload = response.read(API_IMPORT_MAX_BYTES + 1)
    except HTTPError as exc:
        raise ValueError(f"API mengembalikan status {exc.code}.") from exc
    except URLError as exc:
        raise ValueError("API tidak bisa dihubungi.") from exc
    except TimeoutError as exc:
        raise ValueError("API terlalu lama merespons.") from exc
    if len(payload) > API_IMPORT_MAX_BYTES:
        raise ValueError("Response API terlalu besar. Batasi data atau naikkan API_IMPORT_MAX_BYTES.")
    try:
        return json.loads(payload.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError("Response API harus berupa JSON valid.") from exc


# ── Data Processing ────────────────────────────────────────────────────────

def scalar_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return compact_text(value)
    if isinstance(value, list):
        return compact_text(", ".join(scalar_to_text(item) for item in value if scalar_to_text(item)))
    if isinstance(value, dict):
        return compact_text(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return compact_text(value)


def pick_field(item: Dict[str, Any], names: List[str]) -> str:
    lowered = {str(key).lower(): key for key in item.keys()}
    for name in names:
        key = lowered.get(name.lower())
        if key is not None:
            text = scalar_to_text(item.get(key))
            if text:
                return text
    return ""


def flatten_api_fields(value: Any, prefix: str = "", depth: int = 0) -> List[Tuple[str, str]]:
    if depth > 3:
        return [(prefix or "data", scalar_to_text(value))]
    if isinstance(value, dict):
        rows: List[Tuple[str, str]] = []
        for key, nested in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(nested, (dict, list)):
                rows.extend(flatten_api_fields(nested, name, depth + 1))
            else:
                text = scalar_to_text(nested)
                if text:
                    rows.append((name, text))
        return rows
    if isinstance(value, list):
        rows = []
        for index, nested in enumerate(value[:20], start=1):
            name = f"{prefix}[{index}]" if prefix else f"data[{index}]"
            rows.extend(flatten_api_fields(nested, name, depth + 1))
        return rows
    text = scalar_to_text(value)
    return [(prefix or "data", text)] if text else []


def find_api_lists(value: Any, path: str = "root", depth: int = 0) -> List[Tuple[str, List[Any]]]:
    if depth > 5:
        return []
    found: List[Tuple[str, List[Any]]] = []
    if isinstance(value, list):
        found.append((path, value))
        for index, item in enumerate(value[:5]):
            found.extend(find_api_lists(item, f"{path}[{index}]", depth + 1))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(find_api_lists(item, f"{path}.{key}", depth + 1))
    return found


def extract_api_records(payload: Any, max_items: Optional[int] = None) -> List[Dict[str, Any]]:
    candidates = find_api_lists(payload)
    if not candidates:
        if isinstance(payload, dict):
            return [payload]
        return [{"value": payload}]

    def score(candidate: Tuple[str, List[Any]]) -> int:
        path, rows = candidate
        dict_rows = [row for row in rows if isinstance(row, dict)]
        scalar_rows = [row for row in rows if not isinstance(row, (dict, list))]
        bonus = 25 if any(token in path.lower() for token in ("data", "items", "results", "records")) else 0
        return len(dict_rows) * 10 + len(scalar_rows) + bonus

    _, best_rows = max(candidates, key=score)
    selected_rows = best_rows if max_items is None else best_rows[:max_items]
    normalized: List[Dict[str, Any]] = []
    for index, row in enumerate(selected_rows, start=1):
        if isinstance(row, dict):
            normalized.append(row)
        else:
            normalized.append({"value": row, "index": index})
    return normalized


def category_from_text(text: str, existing_categories: List[str]) -> str:
    lower = text.lower()
    rules = [
        ("Tugas Mahasiswa", ("tugas", "assignment", "deadline", "submission", "pekerjaan rumah")),
        ("Pengumuman & Info", ("pengumuman", "informasi", "info", "announcement", "berita", "news")),
        ("Jadwal Kuliah", ("jadwal", "schedule", "tanggal", "waktu", "kelas", "pertemuan")),
        ("Catatan Dosen", ("catatan", "note", "dosen", "instruktur", "lecturer")),
        ("Materi Perkuliahan", ("materi", "modul", "lesson", "course", "pembelajaran", "kuliah")),
    ]
    existing_lookup = {name.lower(): name for name in existing_categories}
    for suggested, keywords in rules:
        if any(keyword in lower for keyword in keywords):
            return existing_lookup.get(suggested.lower(), suggested)
    return existing_lookup.get("materi perkuliahan", "Materi Perkuliahan")


def normalize_api_category(value: str, existing_categories: List[str], fallback_text: str) -> str:
    value = compact_text(value, max_len=80)
    existing_lookup = {name.lower(): name for name in existing_categories}
    if value:
        matched = existing_lookup.get(value.lower())
        if matched:
            return matched
        if re.search(r"[A-Za-z0-9]", value) and len(value) >= 2:
            return value[:80]
    return category_from_text(fallback_text, existing_categories)


def build_api_materials(api_url: str, payload: Any, existing_categories: List[str]) -> List[Dict[str, str]]:
    records = extract_api_records(payload, max_items=API_IMPORT_MAX_ITEMS)
    source_label = safe_api_label(api_url)
    source_hash = hashlib.sha256(api_url.encode("utf-8")).hexdigest()
    materials: List[Dict[str, str]] = []
    for index, item in enumerate(records, start=1):
        title = pick_field(item, ["title", "judul", "name", "nama", "subject", "topic", "question", "pertanyaan"])
        if not title:
            title = pick_field(item, ["id", "uuid", "slug", "kode", "code", "key"])
        if not title:
            title = f"Data API {index}"
        title = compact_text(title, max_len=160)
        if len(title) < 3:
            title = f"Data API {index}"

        content = pick_field(
            item,
            ["content", "isi", "body", "description", "deskripsi", "text", "summary", "ringkasan", "answer", "jawaban", "value"],
        )
        fields = flatten_api_fields(item)
        field_lines = [f"{name}: {text}" for name, text in fields if text]
        if not content or len(content) < 5:
            content = "\n".join(field_lines)
        if not content or len(content) < 5:
            content = json.dumps(item, ensure_ascii=False, indent=2)
        content = truncate_material_content(f"Sumber API: {source_label}\n\n{content}")

        category_value = pick_field(item, ["category", "kategori", "type", "jenis", "group", "kelompok"])
        tag_text = pick_field(item, ["tags", "tag", "keywords", "keyword"])
        category = normalize_api_category(category_value, existing_categories, f"{title}\n{tag_text}\n{content}")

        keywords = pick_field(item, ["keywords", "keyword", "tags", "tag", "kategori", "category"])
        if not keywords:
            keywords = ", ".join([category, title])
        keywords = compact_text(keywords, max_len=300)

        source_key = pick_field(item, ["id", "uuid", "slug", "kode", "code", "key"])
        if not source_key:
            stable = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            source_key = hashlib.sha256(stable.encode("utf-8")).hexdigest()
        else:
            source_key = hashlib.sha256(source_key.encode("utf-8")).hexdigest()

        materials.append(
            {
                "title": title,
                "category": category,
                "content": content,
                "keywords": keywords,
                "source_hash": source_hash,
                "source_key": source_key,
            }
        )
    return materials


def summarize_api_materials(materials: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for item in materials:
        group = groups.setdefault(item["category"], {"category": item["category"], "count": 0, "sample_titles": []})
        group["count"] += 1
        if len(group["sample_titles"]) < 3:
            group["sample_titles"].append(item["title"])
    return sorted(groups.values(), key=lambda item: (-int(item["count"]), item["category"].lower()))


def is_live_api_category(category: str) -> bool:
    return (category or "").strip().lower() == LIVE_API_CATEGORY


def dump_api_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def api_payload_hash(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def latest_text_within_bytes(content: str, max_bytes: int) -> str:
    content = content.strip()
    if utf8_size(content) <= max_bytes:
        return content
    if max_bytes <= 0:
        return ""
    lines = content.splitlines()
    kept: List[str] = []
    used = 0
    for line in reversed(lines):
        line_size = utf8_size(line) + (1 if kept else 0)
        if used + line_size > max_bytes:
            break
        kept.append(line)
        used += line_size
    if kept:
        return "\n".join(reversed(kept)).strip()
    truncated = content.encode("utf-8")[-max_bytes:]
    return truncated.decode("utf-8", errors="ignore").strip()


def truncate_material_content(content: str, max_bytes: int = API_IMPORT_MAX_CONTENT_BYTES) -> str:
    content = content.strip()
    if utf8_size(content) <= max_bytes:
        return content
    notice = f"[Data dipangkas karena melewati {format_size_mb(max_bytes)}. Data terbaru diprioritaskan.]"
    remaining = max_bytes - utf8_size(notice) - 1
    if remaining <= 0:
        return latest_text_within_bytes(content, max_bytes)
    latest_content = latest_text_within_bytes(content, remaining)
    return f"{notice}\n{latest_content}".strip()


def combine_live_api_content(stored_content: str, live_content: str) -> str:
    live_section = f"Data API realtime terbaru:\n{live_content or '-'}"
    if utf8_size(live_section) >= API_IMPORT_MAX_CONTENT_BYTES or not stored_content:
        return truncate_material_content(live_section)

    stored_header = "\n\nData tersimpan sebelumnya:\n"
    remaining = API_IMPORT_MAX_CONTENT_BYTES - utf8_size(live_section) - utf8_size(stored_header)
    if remaining <= 0:
        return truncate_material_content(live_section)

    stored_value = stored_content.strip()
    if utf8_size(stored_value) > remaining:
        notice = f"[Sebagian data lama dihapus karena batas {format_size_mb(API_IMPORT_MAX_CONTENT_BYTES)}.]"
        remaining_for_old = remaining - utf8_size(notice) - 1
        if remaining_for_old > 0:
            stored_value = f"{notice}\n{latest_text_within_bytes(stored_value, remaining_for_old)}"
        else:
            stored_value = ""

    content = live_section if not stored_value else f"{live_section}{stored_header}{stored_value}"
    return truncate_material_content(content)


def format_api_record_window(api_url: str, records: List[Dict[str, Any]], total_records: int) -> Optional[str]:
    if len(records) <= 1:
        return None
    label = safe_api_label(api_url)
    fetched_at = utc_now()

    def build_window(start_index: int) -> str:
        visible_records = records[start_index:]
        dropped = total_records - len(visible_records)
        content = "\n".join(
            [
                f"Data realtime dari API: {label}",
                f"Diambil pada: {fetched_at}",
                f"Total record terbaca: {total_records}",
                f"Record ditampilkan: {len(visible_records)}",
                f"Record paling awal dipangkas: {dropped}",
                "Format: raw JSON records",
                "",
                dump_api_json(visible_records),
            ]
        )
        return content

    low = 0
    high = len(records) - 1
    best: Optional[str] = None
    while low <= high:
        mid = (low + high) // 2
        candidate = build_window(mid)
        if utf8_size(candidate) <= API_IMPORT_MAX_CONTENT_BYTES:
            best = candidate
            high = mid - 1
        else:
            low = mid + 1
    return best


def format_api_payload_for_material(api_url: str, payload: Any) -> str:
    label = safe_api_label(api_url)
    records = extract_api_records(payload)
    raw_json = dump_api_json(payload)
    content = "\n".join(
        [
            f"Data realtime dari API: {label}",
            f"Diambil pada: {utc_now()}",
            f"Total record terbaca: {len(records)}",
            "Format: raw JSON",
            "",
            raw_json,
        ]
    )
    if utf8_size(content) <= API_IMPORT_MAX_CONTENT_BYTES:
        return content
    window_content = format_api_record_window(api_url, records, len(records))
    if window_content:
        return window_content
    return truncate_material_content(content)


def preview_live_api_content(api_url: str, previous_hash: str = "") -> Dict[str, Any]:
    api_url = validate_external_api_url(api_url)
    payload = fetch_api_json(api_url)
    records = extract_api_records(payload)
    content_hash = api_payload_hash(payload)
    changed = not previous_hash or previous_hash != content_hash
    content = format_api_payload_for_material(api_url, payload) if changed else ""
    size_metadata = content_size_metadata(content) if changed else {
        "content_size_bytes": 0,
        "content_size_mb": 0,
        "content_size_label": "",
    }
    return {
        "api_label": safe_api_label(api_url),
        "total_items": len(records),
        "content": content,
        "content_hash": content_hash,
        "changed": changed,
        **size_metadata,
        "updated_at": utc_now(),
    }


def normalize_mac_address(raw_mac: str) -> str:
    """Normalize ESP32 MAC address to standard AA:BB:CC:DD:EE:FF format."""
    raw = str(raw_mac or "").strip()
    if not raw:
        return ""
    clean_prefix = re.sub(r"^(esp32[-_]|board[-_])", "", raw, flags=re.IGNORECASE)
    parts = re.split(r"[:-]", clean_prefix)
    if len(parts) == 6 and all(len(p) in (1, 2) and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts):
        return ":".join(p.zfill(2).upper() for p in parts)
    hex_only = re.sub(r"[^0-9A-Fa-f]", "", clean_prefix)
    if len(hex_only) == 12:
        return ":".join(hex_only[i:i+2].upper() for i in range(0, 12, 2))
    return raw.strip().upper()

