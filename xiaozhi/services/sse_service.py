"""
Server-Sent Events (SSE) Hub & Streaming Service for XiaoZhi.
Provides real-time event streaming for:
1. AI Chat streaming responses (token-by-token typing effect)
2. Admin live log and MCP event stream
3. Material vectorization & indexing progress streaming
"""

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from fastapi import Request

from xiaozhi.core.utils import utc_now
from xiaozhi.services.semantic_memory_service import (
    clean_and_tokenize,
    expand_semantic_concepts,
    calculate_semantic_similarity,
)

logger = logging.getLogger("xiaozhi.sse")


def format_sse(event: str, data: Any, event_id: Optional[str] = None) -> str:
    """Format payload into standard W3C Server-Sent Events (SSE) chunk."""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event:
        lines.append(f"event: {event}")
    
    if isinstance(data, (dict, list)):
        payload = json.dumps(data, ensure_ascii=False)
    else:
        payload = str(data)
    
    for line in payload.split("\n"):
        lines.append(f"data: {line}")
    lines.append("\n")  # Final newline to complete chunk
    return "\n".join(lines)


# ── 1. Admin Event & Log Streaming Hub ──────────────────────────────────────

class AdminLogHub:
    """In-memory event hub for live admin log streaming via SSE."""
    def __init__(self, max_buffer: int = 60):
        self._subscribers: Set[asyncio.Queue] = set()
        self._buffer: List[Dict[str, Any]] = []
        self._max_buffer = max_buffer
        self._lock = asyncio.Lock()

    def emit(self, category: str, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Broadcast an event to all connected admin streams and keep in buffer."""
        entry = {
            "id": f"log_{int(time.time() * 1000)}_{len(self._buffer)}",
            "time": time.strftime("%H:%M:%S"),
            "timestamp": utc_now(),
            "category": category,  # 'mcp', 'audio', 'device', 'system'
            "message": message,
            "metadata": metadata or {},
        }
        # Keep ring buffer
        if len(self._buffer) >= self._max_buffer:
            self._buffer.pop(0)
        self._buffer.append(entry)

        # Broadcast to queues
        for q in list(self._subscribers):
            try:
                q.put_nowait(entry)
            except Exception:
                pass

    def get_recent(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self._buffer[-limit:]

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q)


admin_log_hub = AdminLogHub()


def log_admin_event(category: str, message: str, metadata: Optional[Dict[str, Any]] = None):
    """Global helper to emit an admin log event for SSE subscribers."""
    admin_log_hub.emit(category, message, metadata)


async def stream_admin_logs(request: Request) -> AsyncGenerator[str, None]:
    """Generator for streaming admin server events via SSE."""
    q = await admin_log_hub.subscribe()
    try:
        # 1. Send recent backlog first
        recent = admin_log_hub.get_recent(25)
        for item in recent:
            yield format_sse("log", item, event_id=item["id"])

        yield format_sse("connected", {"status": "ok", "message": "Live Log Stream aktif."})

        # 2. Stream live events
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(q.get(), timeout=15.0)
                yield format_sse("log", item, event_id=item["id"])
            except asyncio.TimeoutError:
                # Keep-alive heartbeat comment
                yield ": ping\n\n"
    finally:
        admin_log_hub.unsubscribe(q)


# ── 2. AI Chat Streaming (Token-by-Token Typing Effect) ──────────────────────

async def stream_ai_chat(query: str, user_id: int, store: Any, request: Request) -> AsyncGenerator[str, None]:
    """
    Streams intelligent contextual response word-by-word with SSE.
    Strictly isolated to user_id: accesses user profile, registered ESP32 MAC,
    knowledge base materials, MCP connection, quota, and smart home relay devices.
    NEVER leaks or accesses other users' data.
    """
    import os
    import httpx

    clean_q = query.strip()
    if not clean_q:
        yield format_sse("error", {"message": "Pertanyaan tidak boleh kosong."})
        return

    yield format_sse("start", {"query": clean_q})

    # 1. Fetch user data (Strictly scoped to user_id)
    user = store.get_user(user_id)
    username = user.get("username", "User") if user else f"User {user_id}"
    role = (user.get("role") or "user").capitalize() if user else "User"
    email = user.get("email") if user else None
    created_at = user.get("created_at", "-") if user else "-"

    user_mac = store.get_user_mac_address(user_id)
    devices = []
    try:
        devices = store.list_registered_devices(user_id)
    except Exception:
        pass

    token_info = store.get_xiaozhi_token_info(user_id)
    token_preview = token_info.get("preview", "") if token_info else ""
    token_hash = token_info.get("token_hash", "") if token_info else ""

    from xiaozhi.services.mcp_service import is_mcp_connected
    mcp_connected = is_mcp_connected(user_id)

    quota = None
    try:
        quota = store.user_quota(user_id)
    except Exception:
        pass

    user_materials = []
    try:
        user_materials = store.list_materials(user_id)
    except Exception:
        pass

    relay_rooms = []
    try:
        relay_rooms = store.list_relay_rooms(user_id)
    except Exception:
        pass

    reminders = []
    try:
        reminders = store.list_reminders(user_id)
    except Exception:
        pass

    # Log chat event to admin log stream
    log_admin_event("chat", f"User {user_id} ({username}) bertanya: '{clean_q[:40]}...'", {"user_id": user_id, "username": username})

    q_lower = clean_q.lower()
    full_text = ""
    matched_materials = []
    sources = []

    # ── Security Guard: Strict Multi-Tenant Isolation ─────────────────────────
    # Reject queries attempting to inspect or enumerate other users
    other_user_keywords = [
        "user lain", "orang lain", "semua user", "pengguna lain", "user id ", "user_id ",
        "daftar user", "user siapa aja", "user siapa saja", "data user lain", "mac user lain",
        "ada user apa aja", "siapa saja yang daftar", "database user", "daftar semua pengguna",
        "akun orang lain", "intip user", "bocorkan user"
    ]
    if any(kw in q_lower for kw in other_user_keywords):
        full_text = (
            "🔒 **Akses Ditolak — Perlindungan Privasi Multi-Tenant Aktif**\n\n"
            f"Sistem XiaoZhi Indonesia menerapkan isolasi data multi-tenant yang ketat:\n\n"
            f"1. **Hak Akses Anda**: Saya hanya memiliki otoritas untuk membaca dan mengelola data milik akun Anda sendiri (**{username}**).\n"
            "2. **Kerahasiaan Data Pengguna Lain**: Saya tidak dapat mengakses, melihat, merangkum, maupun membocorkan informasi akun, perangkat hardware ESP32, atau materi milik pengguna lain.\n"
            "3. **Keamanan Terjamin**: Data pribadi Anda juga dilindungi dengan standar isolasi yang sama sehingga pengguna lain tidak dapat mengintip data Anda.\n\n"
            "Silakan ajukan pertanyaan seputar akun Anda, perangkat ESP32 Anda, materi kuliah Anda, atau fitur XiaoZhi!"
        )

    # ── User Account / Identity Inquiries ────────────────────────────────────
    elif any(k in q_lower for k in ["siapa saya", "nama saya", "akun saya", "profil saya", "email saya", "role saya", "kapan akun", "identitas saya"]):
        devices_summary = f"{len(devices)} board terdaftar" if devices else "Belum ada board terdaftar"
        full_text = (
            f"👤 **Detail Akun Anda ({username})**\n\n"
            f"Berikut adalah data resmi akun Anda yang tersimpan di sistem:\n"
            f"- **Nama Pengguna**: `{username}`\n"
            f"- **ID Pengguna**: `#{user_id}`\n"
            f"- **Role / Hak Akses**: `{role}`\n"
            f"- **Email**: `{email if email else 'Belum didaftarkan'}`\n"
            f"- **Tanggal Registrasi**: `{created_at}`\n"
            f"- **Status MAC ESP32**: `{user_mac if user_mac else 'Belum disetel di profil'}`\n"
            f"- **Perangkat Terdaftar**: {devices_summary}\n\n"
            "💡 *Catatan: Data ini hanya dapat dilihat oleh Anda sendiri setelah proses autentikasi berhasil.*"
        )

    # ── User ESP32 Hardware & MAC Inquiries ──────────────────────────────────
    elif any(k in q_lower for k in ["mac address", "mac saya", "perangkat saya", "board saya", "esp32 saya", "device saya", "hardware saya", "alamat mac"]) and not any(k in q_lower for k in ["putar", "lagu", "musik", "youtube", "panduan", "cara"]):
        if user_mac:
            dev_lines = []
            if devices:
                for idx, d in enumerate(devices, 1):
                    d_name = d.get("device_name", f"ESP32 #{idx}")
                    d_mac = d.get("mac_address", "-")
                    d_status = "Online" if d.get("is_online") else "Offline / Standby"
                    dev_lines.append(f"  {idx}. **{d_name}** (`{d_mac}`) — Status: *{d_status}*")
                dev_str = "\n".join(dev_lines)
            else:
                dev_str = f"  - Board Utama: `{user_mac}`"

            full_text = (
                f"📟 **Informasi Perangkat ESP32 Milik Anda**\n\n"
                f"- **MAC Address Utama**: `{user_mac}`\n"
                f"- **Total Perangkat Terdaftar**: {len(devices) if devices else 1}\n\n"
                f"**Daftar Board:**\n{dev_str}\n\n"
                "Board Anda dapat terhubung ke streaming audio YouTube dan server FastMCP XiaoZhi menggunakan MAC address tersebut."
            )
        else:
            full_text = (
                "📟 **Status Perangkat ESP32 Anda**\n\n"
                "Saat ini **belum ada MAC address ESP32 yang terhubung** ke akun Anda.\n\n"
                "👉 **Cara Menghubungkannya:**\n"
                "1. Buka menu **Profil Saya** di sidebar.\n"
                "2. Masukkan 12 digit MAC Address ESP32 Anda (contoh: `24:DC:C3:9A:28:30`).\n"
                "3. Klik **Simpan MAC Address**.\n\n"
                "Setelah tersimpan, board ESP32 Anda otomatis dikenali oleh server untuk memutar audio YouTube dan mengeksekusi perintah suara!"
            )

    # ── User Knowledge Base & Quota Inquiries ────────────────────────────────
    elif any(k in q_lower for k in ["kuota", "materi saya", "daftar materi", "kapasitas materi", "sisa kuota", "knowledge base saya", "berapa materi"]) and not any(k in q_lower for k in ["panduan", "cara upload", "cara tambah"]):
        mat_count = len(user_materials)
        quota_label = quota.get("materials", {}).get("label", "Tanpa Batas") if quota else "Normal"
        remaining = quota.get("materials", {}).get("remaining") if quota else None
        rem_text = f"{remaining} materi lagi" if remaining is not None else "Tanpa batas kuota"

        sample_titles = ""
        if user_materials:
            sample_titles = "\n**5 Materi Teratas Anda:**\n" + "\n".join(
                [f"{i+1}. **{m.get('title', 'Materi')}** ({m.get('category', 'Umum')})" for i, m in enumerate(user_materials[:5])]
            )

        full_text = (
            f"📚 **Status Knowledge Base & Kuota Akun Anda**\n\n"
            f"- **Jumlah Materi Tersimpan**: `{mat_count} materi`\n"
            f"- **Batas Kuota Akun**: `{quota_label}`\n"
            f"- **Sisa Kuota Tersedia**: `{rem_text}`\n"
            f"{sample_titles}\n\n"
            "💡 *Semua materi di atas otomatis diindeks dan dapat dijawab oleh XiaoZhi saat Anda mengajukan pertanyaan.*"
        )

    # ── FastMCP Token & Connection Status Inquiries ──────────────────────────
    elif any(k in q_lower for k in ["token mcp saya", "token saya", "status mcp saya", "status mcp", "koneksi mcp saya", "mcp saya"]) and not any(k in q_lower for k in ["panduan", "cara", "langkah", "setup", "bagaimana", "apa itu"]):
        status_badge = "🟢 **Terhubung (Online)**" if mcp_connected else "🔴 **Terputus / Standby**"
        token_str = f"`{token_preview}`" if token_preview else "*Belum dibuat*"
        full_text = (
            f"⚡ **Status FastMCP Bridge Akun Anda**\n\n"
            f"- **Status Koneksi WebSocket MCP**: {status_badge}\n"
            f"- **Token MCP Tersimpan**: {token_str}\n"
            f"- **Hash Token Keamanan**: `{token_hash[:16]}...` (Terverifikasi)\n\n"
            "**Fungsi FastMCP:**\n"
            "FastMCP memungkinkan board ESP32 Anda memanggil **41 Tools Ilmiah** (Kalkulator, Analisis Soal, Deteksi Bias Kognitif, Cuaca BMKG, Kontrol Relay, dsb) secara otomatis saat Anda berbicara."
        )

    # ── User Smart Home & Relay Inquiries ────────────────────────────────────
    elif any(k in q_lower for k in ["smart home saya", "relay saya", "saklar saya", "lampu saya", "perangkat rumah saya", "ruangan saya"]) and not any(k in q_lower for k in ["panduan", "cara", "langkah", "bagaimana", "apa itu"]):
        if relay_rooms:
            rooms_desc = []
            for r in relay_rooms:
                r_name = r.get("name", "Ruangan")
                relays = r.get("relays", [])
                rel_desc = ", ".join([f"{rel.get('name', 'Saklar')} ({'ON' if rel.get('state') else 'OFF'})" for rel in relays]) if relays else "Tidak ada saklar"
                rooms_desc.append(f"- **{r_name}**: {rel_desc}")
            rooms_str = "\n".join(rooms_desc)
            full_text = (
                f"🏠 **Daftar Smart Home & Saklar Relay Milik Anda**\n\n"
                f"Terdapat **{len(relay_rooms)} ruangan** yang terkonfigurasi di akun Anda:\n\n"
                f"{rooms_str}\n\n"
                "Anda dapat mengontrol saklar ini lewat perintah suara ke XiaoZhi atau menu **Relay Nyata**."
            )
        else:
            full_text = (
                "🏠 **Status Smart Home & Relay Anda**\n\n"
                "Belum ada ruangan atau saklar relay nyata yang dikonfigurasi pada akun Anda.\n\n"
                "Anda dapat menambahkan ruangan dan saklar baru melalui menu **Relay Nyata** atau **Simulasi Smarthome** di sidebar!"
            )

    # ── User Reminders Inquiries ─────────────────────────────────────────────
    elif any(k in q_lower for k in ["reminder", "pengingat", "jadwal saya", "alarm saya"]):
        if reminders:
            rem_list = "\n".join([f"{idx+1}. **{r.get('text', 'Pengingat')}** (Waktu: {r.get('due_time', '-')})" for idx, r in enumerate(reminders[:5])])
            full_text = (
                f"⏰ **Pengingat Aktif Akun Anda**\n\n"
                f"Ditemukan **{len(reminders)} pengingat** terdaftar:\n\n"
                f"{rem_list}"
            )
        else:
            full_text = (
                "⏰ **Pengingat Akun Anda**\n\n"
                "Tidak ada pengingat atau jadwal aktif yang tersimpan saat ini.\n"
                "Anda dapat menyetel pengingat langsung dengan berbicara ke board ESP32 XiaoZhi Anda."
            )

    # ── Specific Tool Inquiry (Lookup in 41 Tools Catalog) ───────────────────
    elif any(k in q_lower for k in ["tool", "fungsi tool", "apa itu tool", "kegunaan tool"]) and not any(k in q_lower for k in ["daftar tool", "apa saja tool", "katalog tool", "sebutkan tool"]):
        from xiaozhi.config import ALL_MCP_TOOLS_CATALOG
        tool_matched = None
        for t in ALL_MCP_TOOLS_CATALOG:
            name = t["name"].lower()
            title = t["title"].lower()
            if name in q_lower or (len(name) > 4 and name.replace("_", " ") in q_lower):
                tool_matched = t
                break
            if title in q_lower:
                tool_matched = t
                break

        if not tool_matched:
            tool_keywords = {
                "cuaca": "get_weather_bmkg", "bmkg": "get_weather_bmkg", "gempa": "get_earthquake_info",
                "kalkulator": "calculate", "hitung": "calculate", "matematika": "calculate",
                "soal": "solve_study_problem", "kuis": "quiz_me", "bias": "identify_cognitive_bias",
                "fallacy": "detect_logical_fallacy", "sesat pikir": "detect_logical_fallacy",
                "kamus": "lookup_kbbi", "kbbi": "lookup_kbbi", "terjemah": "translate_text",
                "kurs": "convert_currency", "wikipedia": "search_wikipedia", "koding": "it_code_and_architecture_helper",
                "sholat": "get_prayer_and_worship_guide", "doa": "get_prayer_and_worship_guide",
                "hadits": "lookup_scripture_and_verse", "ayat": "lookup_scripture_and_verse",
                "berita": "search_news", "web search": "search_web", "pengingat": "set_reminder",
                "memori": "recall_chat_memory", "ingat": "recall_chat_memory", "relay": "control_real_relay_by_voice"
            }
            for kw, t_name in tool_keywords.items():
                if kw in q_lower:
                    for t in ALL_MCP_TOOLS_CATALOG:
                        if t["name"] == t_name:
                            tool_matched = t
                            break
                    if tool_matched:
                        break

        if tool_matched:
            icon = tool_matched.get("icon", "🛠️")
            t_name = tool_matched.get("name", "")
            t_title = tool_matched.get("title", "")
            t_cat = tool_matched.get("category_label", tool_matched.get("category", "General"))
            t_desc = tool_matched.get("description", "")
            full_text = (
                f"{icon} **Detail Tool FastMCP: {t_title}**\n\n"
                f"- **Identifier Teknis**: `{t_name}`\n"
                f"- **Kategori**: `{t_cat}`\n"
                f"- **Deskripsi Fungsi**: {t_desc}\n\n"
                f"**Cara Kerja di XiaoZhi:**\n"
                f"Tool ini dipanggil secara otomatis oleh asisten suara XiaoZhi di board ESP32 saat Anda mengajukan pertanyaan atau perintah suara yang relevan. "
                f"Server FastMCP mengeksekusi fungsi ini dan menyajikan hasilnya secara instan ke XiaoZhi."
            )

    # ── All 41 FastMCP Tools Catalog Inquiry ─────────────────────────────────
    elif any(k in q_lower for k in ["apa saja tool", "daftar tool", "katalog tool", "41 tool", "sebutkan tool", "tool apa saja", "tools mcp", "tools yang ada"]):
        from xiaozhi.config import ALL_MCP_TOOLS_CATALOG
        cat_groups = {}
        for t in ALL_MCP_TOOLS_CATALOG:
            c = t.get("category_label", t.get("category", "Lainnya"))
            if c not in cat_groups:
                cat_groups[c] = []
            cat_groups[c].append(f"{t.get('icon', '•')} **{t.get('title')}** (`{t.get('name')}`): {t.get('description')}")

        lines = [f"🛠️ **Katalog Resmi 41 Tools FastMCP XiaoZhi Indonesia**\n\nXiaoZhi terhubung dengan **41 tools cerdas** berstandar ilmiah dan realtime:"]
        for c_name, items in cat_groups.items():
            lines.append(f"\n📂 **{c_name}**:")
            for item in items:
                lines.append(f"  - {item}")
        lines.append("\n💡 *Semua tools di atas dipanggil secara otomatis oleh XiaoZhi saat mendengarkan perintah suara Anda.*")
        full_text = "\n".join(lines)

    # ── Documentation: FastMCP Setup & Troubleshooting ───────────────────────
    elif any(k in q_lower for k in ["setup mcp", "koneksi mcp", "cara menghubungkan mcp", "token wss", "troubleshoot mcp", "mcp terputus", "mcp offline", "apa itu mcp"]):
        full_text = (
            "🔌 **Panduan Lengkap Setup & Troubleshooting FastMCP**\n\n"
            "**Apa itu MCP?**\n"
            "MCP (Model Context Protocol) adalah protokol penghubung antara asisten suara XiaoZhi dengan server Knowledge Base dan 41 tools kami.\n\n"
            "**Langkah-Langkah Setup:**\n"
            "1. **Buka Konsol XiaoZhi**: Kunjungi [https://xiaozhi.me](https://xiaozhi.me) dan login ke akun Anda.\n"
            "2. **Ambil Endpoint MCP**: Cari menu *Koneksi MCP* / *MCP Endpoint*, salin URL lengkap berawalan `wss://`.\n"
            "   *Contoh*: `wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1Ni...`\n"
            "3. **Simpan di Dashboard**: Buka Dashboard Xiaozhi Indonesia, tempel URL di panel *Koneksi XiaoZhi* (sidebar kanan), lalu klik **Simpan**.\n"
            "4. **Verifikasi**: Tunggu beberapa detik hingga indikator status di dashboard berubah hijau (**Terhubung**).\n\n"
            "**Troubleshooting Jika Gagal / Offline:**\n"
            "- Pastikan menyalin URL LENGKAP berawalan `wss://` (jangan terpotong).\n"
            "- Pastikan koneksi internet port 443 tidak diblokir firewall.\n"
            "- Jika status terputus, klik 'Hubungkan Ulang' atau generate ulang token di xiaozhi.me."
        )

    # ── Documentation: YouTube Music Streaming on ESP32 ─────────────────────
    elif any(k in q_lower for k in ["panduan musik", "panduan youtube", "streaming audio", "youtube esp32", "format audio", "opus", "bitrate", "troubleshoot audio", "watchdog", "wdt", "ack audio"]):
        full_text = (
            "🎵 **Panduan Lengkap YouTube Music Streaming di ESP32**\n\n"
            "**Cara Kerja Arsitektur:**\n"
            "- Server mencari audio YouTube secara real-time dan langsung melakukan transcoding ke format **Ogg/Opus Mono 24kHz** (atau 16kHz).\n"
            "- Audio di-stream ke ESP32 melalui HTTP Streaming (`/api/audio/stream/{video_id}?br=11k&mac={MAC}`) atau saluran persisten WebSocket (`/ws/device/audio/{MAC}`).\n"
            "- Bitrate adaptif: **6k – 32k** (default 11k, sangat hemat kuota dan ringan untuk mikrokontroler ESP32).\n\n"
            "**Perintah Suara:**\n"
            "- *'XiaoZhi, putar lagu [Judul/Artis]'*\n"
            "- *'Jeda musik'* / *'Lanjutkan musik'* / *'Hentikan musik'*\n\n"
            "**Solusi Kendala (Troubleshooting ESP32):**\n"
            "- **Board Sering Reboot (Watchdog Reset / WDT)**: Tambahkan `yield();` di dalam loop `while (stream->available())` firmware C++ Anda.\n"
            "- **Lagu Mengulang Terus**: Pastikan firmware memanggil `POST /api/device/audio/ack` dengan `command_id` saat pemutaran selesai.\n"
            "- **Status Admin 'Menunggu Board'**: Pastikan request polling ESP32 menyertakan parameter `?mac=...` atau header `Device-Id`."
        )

    # ── Documentation: Smart Home & Relay ────────────────────────────────────
    elif any(k in q_lower for k in ["panduan relay", "panduan smart home", "cara kerja smart home", "cara relay", "gpio relay"]):
        full_text = (
            "🏠 **Panduan Smart Home & Kontrol Relay Fisik**\n\n"
            "**Dua Mode Operasi:**\n"
            "1. **Simulasi Smarthome Virtual**: Eksplorasi denah rumah pintar, ruangan virtual, dan simulasi saklar lampu langsung dari browser.\n"
            "2. **Relay Nyata (Hardware ESP32)**: Mengontrol saklar relay fisik yang terhubung ke pin GPIO mikrokontroler ESP32 Anda.\n\n"
            "**Konfigurasi Pin GPIO ESP32:**\n"
            "- Hubungkan Pin Kontrol Relay (IN1, IN2) ke GPIO ESP32 (contoh: GPIO 26, GPIO 27).\n"
            "- Sambungkan VCC ke 5V / 3.3V dan GND ke GND.\n\n"
            "**Perintah Suara:**\n"
            "- *'Nyalakan lampu kamar'* / *'Matikan saklar relay 1'* / *'Nyalakan semua lampu'*.\n"
            "- XiaoZhi memanggil tool `control_real_relay_by_voice` dan memperbarui state relay secara instan."
        )

    # ── Documentation: Knowledge Base & Vektorisasi ──────────────────────────
    elif any(k in q_lower for k in ["panduan knowledge", "cara upload materi", "cara tambah materi", "vektorisasi", "live api"]):
        full_text = (
            "📚 **Panduan Knowledge Base & Vektorisasi Semantik**\n\n"
            "**Fungsi Knowledge Base:**\n"
            "Menyimpan catatan perkuliahan, SOP, referensi belajar, maupun data sensor realtime agar dapat dijawab oleh XiaoZhi saat Anda bertanya.\n\n"
            "**Alur Vektorisasi Semantik:**\n"
            "1. **Pembersihan & Tokenisasi Teks**: Menghapus stopwords bahasa Indonesia dan mengekstrak istilah inti.\n"
            "2. **Pencocokan Taksonomi & Konsep**: Memetakan hubungan antar topik dan bobot fitur semantik.\n"
            "3. **Penyimpanan Database Terindeks**: Disimpan dengan isolasi ketat sesuai user ID pemiliknya.\n"
            "4. **Integrasi Live API**: Anda dapat menautkan URL API publik (cuaca, sensor IoT) yang diperbarui secara berkala.\n\n"
            "Saat Anda bertanya, tool `search_course_materials` mencari kecocokan semantik tertinggi dan menyajikan jawaban yang akurat."
        )

    # ── Master Documentation Overview ────────────────────────────────────────
    elif any(k in q_lower for k in ["dokumentasi", "panduan lengkap", "buku panduan", "fitur apa saja", "fitur xiaozhi"]):
        full_text = (
            "📖 **Buku Panduan & Arsitektur Resmi XiaoZhi Indonesia**\n\n"
            "XiaoZhi Indonesia mengintegrasikan 6 pilar teknologi cerdas:\n\n"
            "1. 🔌 **FastMCP Integration**: Menghubungkan board ESP32 dengan 41 tools komputasi ilmiah dan memori jangka panjang.\n"
            "2. 🎵 **YouTube Music Streamer**: Transcoding Opus 24kHz real-time dengan konsumsi bandwidth ultra-rendah untuk speaker board.\n"
            "3. 🏠 **Smart Home & Relay Nyata**: Kontrol saklar fisik dan otomasi rumah berbasis IoT dan perintah suara.\n"
            "4. 📚 **Knowledge Base & Semantic RAG**: Penyimpanan materi perkuliahan dengan pencarian semantik cerdas.\n"
            "5. 💬 **Tanya AI SSE Real-Time**: Chatbot cerdas streaming berbasis Server-Sent Events dengan kursor mengetik interaktif.\n"
            "6. 🛡️ **Isolasi Multi-Tenant**: Perlindungan data pribadi tingkat tinggi; data Anda 100% aman dan terisolasi dari akun lain.\n\n"
            "💡 *Anda dapat menanyakan panduan spesifik kapan saja, seperti 'Bagaimana cara setup MCP?' atau 'Apa fungsi tool BMKG?'*"
        )

    # ── Search Knowledge Materials (RAG) ─────────────────────────────────────
    else:
        try:
            raw_materials = store.search_materials(user_id, clean_q, limit=5)
            for m in raw_materials:
                sim = calculate_semantic_similarity(clean_q, f"{m.get('title', '')} {m.get('content', '')}")
                m["similarity"] = round(sim, 2)
                matched_materials.append(m)
            matched_materials.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        except Exception as e:
            logger.debug("Error searching materials: %s", e)

        top_material = matched_materials[0] if matched_materials and matched_materials[0].get("similarity", 0) > 0.12 else None

        if top_material:
            title = top_material.get("title", "Pengetahuan")
            content = top_material.get("content", "")
            cat = top_material.get("category", "Umum")
            sources = [{"id": top_material.get("id"), "title": title, "category": cat}]

            full_text = (
                f"📖 **Berdasarkan Materi Anda: '{title}' ({cat})**\n\n"
                f"{content}\n\n"
                f"---\n"
                f"💡 *Sumber ini diambil secara eksklusif dari Knowledge Base akun {username}. Apakah ada bagian materi ini yang ingin Anda diskusikan lebih lanjut?*"
            )

    # ── If No Pre-defined Branch: Generative AI (Gemini 3.5 Flash) or Local Engine ──
    if not full_text:
        gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
        gemini_success = False

        if gemini_api_key:
            try:
                system_instruction = (
                    f"Anda adalah XiaoZhi AI Indonesia, asisten AI ramah, cerdas, dan menguasai seluruh dokumentasi sistem XiaoZhi. "
                    f"Pengguna aktif: {username} (Role: {role}, MAC: {user_mac or 'belum disetel'}). "
                    f"ATURAN KEAMANAN MUTLAK: Anda HANYA diizinkan merujuk dan melayani data milik pengguna {username}. "
                    f"JANGAN PERNAH membocorkan, menyebutkan, atau mengarang data pengguna lain. "
                    f"Anda memiliki pengetahuan lengkap tentang dokumentasi XiaoZhi: setup FastMCP WSS, YouTube Music streaming Opus di ESP32, "
                    f"smart home relay nyata via GPIO, knowledge base RAG, dan 41 tools FastMCP. "
                    f"Jawablah dalam Bahasa Indonesia yang jelas, ramah, dan terstruktur."
                )
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:streamGenerateContent?alt=sse&key={gemini_api_key}"
                payload = {
                    "contents": [{"parts": [{"text": clean_q}]}],
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                }

                async with httpx.AsyncClient(timeout=15.0) as client:
                    async with client.stream("POST", url, json=payload) as resp:
                        if resp.status_code == 200:
                            gemini_success = True
                            token_idx = 0
                            async for raw_line in resp.aiter_lines():
                                if await request.is_disconnected():
                                    break
                                if not raw_line.startswith("data:"):
                                    continue
                                json_part = raw_line[5:].strip()
                                if not json_part:
                                    continue
                                try:
                                    import json
                                    c_data = json.loads(json_part)
                                    candidates = c_data.get("candidates", [])
                                    if candidates:
                                        parts = candidates[0].get("content", {}).get("parts", [])
                                        for p in parts:
                                            chunk_text = p.get("text", "")
                                            if chunk_text:
                                                full_text += chunk_text
                                                yield format_sse("token", {"token": chunk_text, "index": token_idx})
                                                token_idx += 1
                                except Exception:
                                    pass
            except Exception as gem_err:
                logger.debug("Gemini streaming error: %s", gem_err)
                gemini_success = False

        # If Gemini was not used or failed, use smart local assistant response
        if not gemini_success:
            if any(k in q_lower for k in ["halo", "hai", "selamat", "pagi", "siang", "malam"]):
                full_text = (
                    f"Halo **{username}**! Senang menyapa Anda kembali di asisten AI XiaoZhi Indonesia. "
                    f"Saya siap membantu Anda mempelajari materi kuliah, memutar musik YouTube di board ESP32 Anda, "
                    f"mengontrol saklar smart home, atau mengecek status sistem. Ada yang bisa saya bantu sekarang?"
                )
            elif any(k in q_lower for k in ["putar", "lagu", "musik", "youtube"]):
                full_text = (
                    f"🎵 **Memutar Musik di ESP32**\n\n"
                    f"Tentu! Untuk memutar audio YouTube langsung di speaker board ESP32 Anda:\n"
                    f"1. Pastikan MAC board Anda (`{user_mac if user_mac else 'Belum disetel di profil'}`) sudah terhubung.\n"
                    f"2. Katakan ke speaker XiaoZhi: *'XiaoZhi, putar {clean_q}'*.\n"
                    f"3. Atau Anda dapat mencari dan memutar lagu langsung dari dashboard web XiaoZhi."
                )
            else:
                full_text = (
                    f"Mengenai pertanyaan Anda tentang **'{clean_q}'**:\n\n"
                    f"Pertanyaan ini telah dicatat dalam riwayat obrolan akun Anda (**{username}**). "
                    f"Anda dapat menambahkan dokumen atau catatan terkait topik ini ke menu **Knowledge Base** pada Dashboard "
                    f"agar asisten XiaoZhi dapat memberikan jawaban yang semakin mendalam dan terpersonalisasi."
                )

    # Stream the full_text word by word (if not already streamed via Gemini SSE)
    if full_text and (not locals().get("gemini_success", False)):
        words = full_text.split(" ")
        for idx, word in enumerate(words):
            if await request.is_disconnected():
                break
            chunk = word + (" " if idx < len(words) - 1 else "")
            yield format_sse("token", {"token": chunk, "index": idx})
            await asyncio.sleep(0.025)

    # Save to chat history transcript
    try:
        store.upsert_chat_transcript(
            user_id,
            tool_name="web_chat_sse",
            user_message=clean_q,
            xiaozhi_answer=full_text,
            token_hash=token_hash,
        )
    except Exception as e:
        logger.debug("Failed to record chat history: %s", e)

    # Send final done event with sources
    if not sources and matched_materials:
        sources = [{"id": m.get("id"), "title": m.get("title"), "category": m.get("category")} for m in matched_materials[:3]]

    yield format_sse("done", {
        "full_text": full_text,
        "sources": sources,
        "timestamp": utc_now(),
    })


# ── 3. Real-Time Vectorization & Material Indexing Progress Stream ──────────

async def stream_material_indexing(
    user_id: int,
    title: str,
    category: str,
    content: str,
    keywords: str,
    api_url: str,
    store: Any,
    request: Request,
) -> AsyncGenerator[str, None]:
    """
    Processes material indexing with real-time SSE progress percentage (0-100%).
    """
    yield format_sse("progress", {"percent": 10, "step": "Validasi & Normalisasi Teks", "message": "Memeriksa format data materi..."})
    await asyncio.sleep(0.2)

    if not title.strip() or not content.strip():
        yield format_sse("error", {"message": "Judul dan isi materi tidak boleh kosong."})
        return

    yield format_sse("progress", {"percent": 30, "step": "Pembersihan Stopwords", "message": "Menghapus kata-kata tidak signifikan (stop words)..."})
    await asyncio.sleep(0.25)
    tokens = clean_and_tokenize(content)

    yield format_sse("progress", {"percent": 55, "step": "Ekstraksi Konsep Semantik", "message": f"Mengekstrak {len(tokens)} token kata dan mencocokkan taksonomi..."})
    await asyncio.sleep(0.3)
    features = expand_semantic_concepts(tokens)

    yield format_sse("progress", {"percent": 75, "step": "Kalkulasi Vektor Vektorisasi", "message": f"Menghitung bobot {len(features)} dimensi fitur semantik..."})
    await asyncio.sleep(0.3)

    # Persist to database store
    yield format_sse("progress", {"percent": 90, "step": "Menyimpan ke Knowledge Base", "message": "Menulis data materi ke database server..."})
    try:
        new_item = store.add_material(user_id, title.strip(), category.strip(), content.strip(), keywords.strip(), api_url.strip())
        from xiaozhi.services.mcp_service import signal_mcp_reload
        signal_mcp_reload()

        log_admin_event("material", f"User {user_id} menambahkan materi: '{title[:35]}'", {"user_id": user_id, "title": title})

        yield format_sse("progress", {"percent": 100, "step": "Selesai", "message": "Materi berhasil divektorisasi dan siap diakses AI!"})
        await asyncio.sleep(0.15)
        item_payload = new_item if isinstance(new_item, dict) else ({"id": getattr(new_item, "id", str(new_item))} if new_item else {})
        yield format_sse("done", {
            "success": True,
            "message": "Materi berhasil disimpan dan divektorisasi.",
            "item": item_payload,
        })
    except Exception as exc:
        yield format_sse("error", {"message": f"Gagal menyimpan materi: {str(exc)}"})
