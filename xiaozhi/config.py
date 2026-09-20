import base64
import hashlib
import logging
import os
import secrets

from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer

def _load_env_file():
    from pathlib import Path
    for parent in [Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent]:
        env_file = parent / ".env"
        if env_file.exists():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass
            break

_load_env_file()

logger = logging.getLogger("xiaozhi")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# ── Categories ──────────────────────────────────────────────────────────────
DEFAULT_CATEGORIES = [
    "Materi Perkuliahan",
    "Tugas Mahasiswa",
    "Catatan Dosen",
    "Jadwal Kuliah",
    "Pengumuman & Info",
    "data dari api",
]

# ── API Import Limits ──────────────────────────────────────────────────────
API_IMPORT_MAX_BYTES = int(os.getenv("API_IMPORT_MAX_BYTES", str(5 * 1024 * 1024)))
API_IMPORT_MAX_ITEMS = int(os.getenv("API_IMPORT_MAX_ITEMS", "150"))
API_IMPORT_MAX_CONTENT_BYTES = int(os.getenv("API_IMPORT_MAX_CONTENT_BYTES", str(5 * 1024 * 1024)))
API_IMPORT_TIMEOUT = float(os.getenv("API_IMPORT_TIMEOUT", "12"))
LIVE_API_CATEGORY = "data dari api"
LIVE_API_SOURCE_TYPE = "api_live"
API_SEARCH_EXCERPT_BYTES = int(os.getenv("API_SEARCH_EXCERPT_BYTES", str(128 * 1024)))

# ── Chat History ───────────────────────────────────────────────────────────
CHAT_HISTORY_MAX_TEXT_BYTES = int(os.getenv("CHAT_HISTORY_MAX_TEXT_BYTES", str(1024 * 1024)))
CHAT_HISTORY_DEFAULT_LIMIT = int(os.getenv("CHAT_HISTORY_DEFAULT_LIMIT", "100"))

# ── MCP & Relay ────────────────────────────────────────────────────────────
MCP_TOKEN_HASH_LENGTH = 16
REAL_RELAY_MAX_RELAYS = 4
REAL_RELAY_POLL_INTERVAL_MS = int(os.getenv("REAL_RELAY_POLL_INTERVAL_MS", "1000"))
REAL_RELAY_DEVICE_ONLINE_SECONDS = int(os.getenv("REAL_RELAY_DEVICE_ONLINE_SECONDS", "45"))

# ── Auth Defaults ──────────────────────────────────────────────────────────
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip() or "admin"
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
RESTORED_USER_USERNAME = os.getenv("RESTORED_USER_USERNAME", "").strip()
RESTORED_USER_PASSWORD = os.getenv("RESTORED_USER_PASSWORD", "").strip()

# ── User Limits & Features ─────────────────────────────────────────────────
USER_LIMIT_DEFAULTS = {
    "max_materials": 3,
    "max_words_per_material": 6000,
    "max_live_apis": 2,
    "max_relay_rooms": 7,
}

USER_FEATURE_FLAGS = {
    "youtube_music": True,
}

# ── All 39 MCP Tools Catalog ──────────────────────────────────────────────
ALL_MCP_TOOLS_CATALOG = [
    # 1. Knowledge Base & Memori AI
    {
        "name": "search_course_materials",
        "title": "Knowledge Base & Materi",
        "category": "knowledge",
        "category_label": "Knowledge & Memori",
        "icon": "📚",
        "description": "Pencarian materi perkuliahan, tugas, jadwal, pengumuman, dan data API realtime.",
    },
    {
        "name": "read_live_api_data",
        "title": "Live API Sensor & Cuaca",
        "category": "knowledge",
        "category_label": "Knowledge & Memori",
        "icon": "📡",
        "description": "Membaca data API realtime sensor, suhu, cuaca, dan status perangkat.",
    },
    {
        "name": "read_material_database",
        "title": "Database Materi EduSmart",
        "category": "knowledge",
        "category_label": "Knowledge & Memori",
        "icon": "🗃️",
        "description": "Membaca database materi pembelajaran lengkap sesuai pertanyaan.",
    },
    {
        "name": "read_material_detail",
        "title": "Detail Materi Lengkap",
        "category": "knowledge",
        "category_label": "Knowledge & Memori",
        "icon": "📄",
        "description": "Membaca satu materi spesifik secara utuh berdasarkan ID atau kata kunci.",
    },
    {
        "name": "save_chat_history",
        "title": "Simpan Riwayat Percakapan",
        "category": "knowledge",
        "category_label": "Knowledge & Memori",
        "icon": "💾",
        "description": "Mencatat obrolan penting ke database riwayat memori.",
    },
    {
        "name": "recall_chat_memory",
        "title": "Semantic Chat Memory Recall",
        "category": "knowledge",
        "category_label": "Knowledge & Memori",
        "icon": "🧠",
        "description": "Mengingat percakapan lampau menggunakan vektor semantik RAG.",
    },

    # 2. Karakter, Persona & Device
    {
        "name": "remember_user_profile",
        "title": "Profil Preferensi & Persona",
        "category": "persona",
        "category_label": "Karakter & Device",
        "icon": "👤",
        "description": "Menyimpan preferensi, hobi, dan gaya bahasa user jangka panjang.",
    },
    {
        "name": "get_user_profile",
        "title": "Baca Karakter & Persona AI",
        "category": "persona",
        "category_label": "Karakter & Device",
        "icon": "🧬",
        "description": "Membaca kepribadian (Introvert/Extrovert), hobi, dan tantangan hidup pengguna.",
    },
    {
        "name": "get_registered_devices",
        "title": "Daftar Perangkat ESP32",
        "category": "persona",
        "category_label": "Karakter & Device",
        "icon": "📟",
        "description": "Melihat daftar perangkat keras ESP32 dan MAC address yang terhubung.",
    },

    # 3. Smart Home Virtual & Relay Nyata
    {
        "name": "control_relay",
        "title": "Kontrol Relay Virtual",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "🏠",
        "description": "Kontrol relay pada simulasi smart home virtual.",
    },
    {
        "name": "control_smart_home_room",
        "title": "Kontrol Ruangan Virtual",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "🛋️",
        "description": "Kontrol perangkat simulasi smart home berdasarkan nama ruangan.",
    },
    {
        "name": "get_relay_status",
        "title": "Status Relay Virtual",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "📊",
        "description": "Membaca status ON/OFF semua relay di smart home virtual.",
    },
    {
        "name": "all_relays_on",
        "title": "Semua Relay Virtual ON",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "💡",
        "description": "Menyalakan semua perangkat virtual sekaligus.",
    },
    {
        "name": "all_relays_off",
        "title": "Semua Relay Virtual OFF",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "🌑",
        "description": "Mematikan semua perangkat virtual sekaligus termasuk AC dan alarm.",
    },
    {
        "name": "control_real_relay_by_voice",
        "title": "Kontrol Suara Relay Fisik",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "⚡",
        "description": "Kontrol modul relay fisik nyata via polling API suara ESP32.",
    },
    {
        "name": "get_real_relay_status",
        "title": "Status Relay Fisik Nyata",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "🔌",
        "description": "Membaca status realtime modul relay fisik nyata.",
    },
    {
        "name": "all_real_relays_on",
        "title": "Semua Relay Fisik ON",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "🟢",
        "description": "Menyalakan seluruh pin relay fisik ESP32 secara serentak.",
    },
    {
        "name": "all_real_relays_off",
        "title": "Semua Relay Fisik OFF",
        "category": "iot",
        "category_label": "Smart Home & IoT",
        "icon": "🔴",
        "description": "Mematikan seluruh pin relay fisik ESP32 secara serentak.",
    },

    # 4. Multimedia & Web
    {
        "name": "play_youtube_song",
        "title": "YouTube Music Player",
        "category": "media",
        "category_label": "Media & Web",
        "icon": "🎵",
        "description": "Pencarian dan pemutaran audio lagu dari YouTube.",
    },
    {
        "name": "get_playback_status",
        "title": "Status Pemutaran Musik",
        "category": "media",
        "category_label": "Media & Web",
        "icon": "🎧",
        "description": "Mengecek status lagu YouTube yang sedang atau baru saja selesai diputar di speaker.",
    },
    {
        "name": "stop_youtube_song",
        "title": "Stop Musik YouTube",
        "category": "media",
        "category_label": "Media & Web",
        "icon": "⏹️",
        "description": "Menghentikan pemutaran musik/audio YouTube yang sedang berjalan di speaker perangkat.",
    },
    {
        "name": "search_web",
        "title": "Web Search Live",
        "category": "media",
        "category_label": "Media & Web",
        "icon": "🔍",
        "description": "Pencarian informasi terkini dari internet.",
    },
    {
        "name": "search_news",
        "title": "Berita Terkini Indonesia",
        "category": "media",
        "category_label": "Media & Web",
        "icon": "📰",
        "description": "Pencarian berita aktual dari berbagai portal berita Indonesia.",
    },

    # 5. Produktivitas & Utilitas Harian
    {
        "name": "set_reminder",
        "title": "Pengingat & Smart Alarm",
        "category": "productivity",
        "category_label": "Produktivitas",
        "icon": "⏰",
        "description": "Mengatur pengingat dan alarm menggunakan perintah suara.",
    },
    {
        "name": "calculate",
        "title": "Kalkulator & Matematika",
        "category": "productivity",
        "category_label": "Produktivitas",
        "icon": "🧮",
        "description": "Menghitung ekspresi matematika rumit secara instan.",
    },
    {
        "name": "translate_text",
        "title": "Penerjemah Multi-Bahasa",
        "category": "productivity",
        "category_label": "Produktivitas",
        "icon": "🌐",
        "description": "Menerjemahkan teks antar berbagai bahasa di dunia.",
    },
    {
        "name": "convert_currency",
        "title": "Konversi Kurs Mata Uang",
        "category": "productivity",
        "category_label": "Produktivitas",
        "icon": "💱",
        "description": "Konversi nilai tukar valuta asing ke Rupiah (IDR) secara realtime.",
    },
    {
        "name": "lookup_kbbi",
        "title": "Kamus KBBI Resmi",
        "category": "productivity",
        "category_label": "Produktivitas",
        "icon": "📖",
        "description": "Definisi baku dan ejaan resmi Kamus Besar Bahasa Indonesia.",
    },
    {
        "name": "search_wikipedia",
        "title": "Ensiklopedia Wikipedia",
        "category": "productivity",
        "category_label": "Produktivitas",
        "icon": "🌍",
        "description": "Mencari ringkasan ensiklopedis resmi dari Wikipedia.",
    },

    # 6. Edukasi, Akademik & Sains
    {
        "name": "solve_study_problem",
        "title": "Pemecah Soal Pelajaran",
        "category": "education",
        "category_label": "Edukasi & Kuliah",
        "icon": "🎓",
        "description": "Menyelesaikan soal sekolah dan tugas kuliah secara mendidik (step-by-step).",
    },
    {
        "name": "explain_concept",
        "title": "Penjelas Konsep Edukasi",
        "category": "education",
        "category_label": "Edukasi & Kuliah",
        "icon": "💡",
        "description": "Menjelaskan konsep rumit atau abstrak dengan analogi sederhana.",
    },
    {
        "name": "quiz_me",
        "title": "Kuis Interaktif Materi",
        "category": "education",
        "category_label": "Edukasi & Kuliah",
        "icon": "📝",
        "description": "Partner belajar interaktif untuk menguji pemahaman materi kuliah.",
    },
    {
        "name": "lookup_formula",
        "title": "Kamus Rumus Sains",
        "category": "education",
        "category_label": "Edukasi & Kuliah",
        "icon": "📐",
        "description": "Kamus cepat rumus Fisika, Matematika, Kimia, dan Ekonomi lengkap dengan satuan SI.",
    },
    {
        "name": "academic_english_helper",
        "title": "Academic English & Grammar",
        "category": "education",
        "category_label": "Edukasi & Kuliah",
        "icon": "🔤",
        "description": "Pengecekan tata bahasa akademik dan kosakata paper ilmiah bahasa Inggris.",
    },

    # 7. Informasi Cuaca & Bencana
    {
        "name": "get_earthquake_info",
        "title": "Info Gempa Bumi BMKG",
        "category": "info",
        "category_label": "Cuaca & Info Publik",
        "icon": "🌋",
        "description": "Informasi gempa terkini M 5.0+ resmi dari BMKG Indonesia.",
    },
    {
        "name": "get_weather_bmkg",
        "title": "Prakiraan Cuaca BMKG",
        "category": "info",
        "category_label": "Cuaca & Info Publik",
        "icon": "🌤️",
        "description": "Prakiraan cuaca dan suhu realtime per kota di Indonesia dari BMKG.",
    },

    # 8. Logika, Pola Pikir & IT
    {
        "name": "detect_logical_fallacy",
        "title": "Deteksi Cacat Logika (Fallacy)",
        "category": "critical_thinking",
        "category_label": "Logika & IT",
        "icon": "🎯",
        "description": "Menganalisis kesesatan berpikir dan argumen dalam debat atau opini.",
    },
    {
        "name": "identify_cognitive_bias",
        "title": "Identifikasi Bias Kognitif",
        "category": "critical_thinking",
        "category_label": "Logika & IT",
        "icon": "🧩",
        "description": "Mendeteksi distorsi psikologis dan bias kognitif dalam pengambilan keputusan.",
    },
    {
        "name": "it_code_and_architecture_helper",
        "title": "Asisten Koding & Arsitektur IT",
        "category": "critical_thinking",
        "category_label": "Logika & IT",
        "icon": "💻",
        "description": "Panduan rekayasa perangkat lunak, algoritma, koding, dan arsitektur sistem.",
    },

    # 9. Spiritual & Keagamaan
    {
        "name": "lookup_scripture_and_verse",
        "title": "Pencarian Ayat Kitab Suci",
        "category": "spiritual",
        "category_label": "Spiritual & Agama",
        "icon": "📖",
        "description": "Pencarian nama surat, pasal, dan ayat kitab suci berbagai agama resmi.",
    },
    {
        "name": "get_prayer_and_worship_guide",
        "title": "Panduan Ibadah & Doa",
        "category": "spiritual",
        "category_label": "Spiritual & Agama",
        "icon": "🕌",
        "description": "Bimbingan doa harian dan tata cara ibadah lengkap untuk semua agama.",
    },
]

ALL_MCP_TOOL_NAMES = [t["name"] for t in ALL_MCP_TOOLS_CATALOG]


# ── Emote Aliases ──────────────────────────────────────────────────────────
EMOTE_ALIASES = {
    "angry": "\U0001f620",
    "confused": "\U0001f615",
    "cry": "\U0001f622",
    "happy": "\U0001f60a",
    "laugh": "\U0001f604",
    "love": "\U0001f60d",
    "neutral": "\U0001f610",
    "sad": "\U0001f622",
    "smile": "\U0001f60a",
    "surprised": "\U0001f62e",
    "thinking": "\U0001f914",
    "wink": "\U0001f609",
}

# ── Search ─────────────────────────────────────────────────────────────────
SEARCH_STOPWORDS = {
    "ada", "apa", "apakah", "bagaimana", "berapa", "berikan", "bisa",
    "coba", "dalam", "dan", "dari", "data", "dengan", "di", "ini",
    "itu", "jelaskan", "ke", "kok", "mana", "mau", "nya", "pada",
    "saat", "saya", "sebutkan", "untuk", "yang",
}

SEARCH_ALIASES = {
    "ac": ["air_conditioner", "conditioner", "cooling"],
    "angin": ["wind", "speed", "deg", "gust"],
    "awan": ["cloud", "clouds", "all"],
    "barang": ["product", "item", "sku", "inventory", "stock"],
    "bayar": ["payment", "paid", "status", "amount"],
    "cuaca": ["weather", "description", "main", "temp", "humidity", "wind", "rain", "clouds"],
    "derajat": ["temp", "temperature", "celsius", "fahrenheit"],
    "dingin": ["temp", "temperature", "feels_like"],
    "harga": ["price", "amount", "total", "subtotal"],
    "hujan": ["rain", "precipitation"],
    "kelembaban": ["humidity"],
    "kelembapan": ["humidity"],
    "kota": ["city", "name", "country"],
    "lokasi": ["location", "coord", "lat", "lon", "name", "country"],
    "maksimum": ["max", "temp_max"],
    "minimum": ["min", "temp_min"],
    "omzet": ["revenue", "sales", "income", "total_sales", "amount"],
    "pelanggan": ["customer", "buyer", "client", "user"],
    "pembayaran": ["payment", "paid", "status", "amount"],
    "pendapatan": ["revenue", "income", "sales", "total"],
    "penjualan": ["sales", "revenue", "transaction", "order", "total_sales", "amount"],
    "pesanan": ["order", "orders", "order_id", "quantity", "qty"],
    "panas": ["temp", "temperature", "feels_like"],
    "produk": ["product", "item", "sku", "name"],
    "realtime": ["realtime", "updated_at", "diambil", "dt"],
    "sekarang": ["realtime", "updated_at", "diambil", "dt"],
    "stok": ["stock", "inventory", "qty", "quantity"],
    "suhu": ["temp", "temperature", "feels_like", "temp_min", "temp_max"],
    "tekanan": ["pressure"],
    "terasa": ["feels_like"],
    "terbaru": ["realtime", "updated_at", "diambil", "dt"],
    "transaksi": ["transaction", "transactions", "order", "payment", "sales"],
    "update": ["updated_at", "diambil", "dt"],
    "waktu": ["time", "timezone", "dt", "updated_at", "diambil"],
}

# ── Session & CSRF ─────────────────────────────────────────────────────────
SESSION_COOKIE = "edusmart_session"
LEGACY_SESSION_COOKIE = "user_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7
CSRF_MAX_AGE = 60 * 60 * 4

# ── UI ─────────────────────────────────────────────────────────────────────
UI_THEMES = {"default", "dark", "neo"}
DEFAULT_UI_THEME = "neo"

# ── Production Detection ───────────────────────────────────────────────────
IS_PRODUCTION = os.getenv("ENVIRONMENT", "").lower() == "production" or bool(os.getenv("SPACE_ID"))

# ── CORS Origins ───────────────────────────────────────────────────────────
_raw_origins = os.getenv("ALLOWED_ORIGINS", "").strip()
if _raw_origins:
    ALLOWED_ORIGINS = [orig.strip() for orig in _raw_origins.split(",") if orig.strip()]
else:
    ALLOWED_ORIGINS = [
        "https://xiaozhiscig.biz.id",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
    ]
ALLOWED_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|xiaozhiscig\.biz\.id)(:\d+)?$"

# ── Secret Key ─────────────────────────────────────────────────────────────
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY")
if not APP_SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError("APP_SECRET_KEY wajib diset untuk production.")
    APP_SECRET_KEY = secrets.token_urlsafe(32)
    logger.warning(
        "APP_SECRET_KEY belum diset. Session dan enkripsi hanya stabil sampai proses restart."
    )

session_serializer = URLSafeTimedSerializer(APP_SECRET_KEY, salt="edusmart-session")
csrf_serializer = URLSafeTimedSerializer(APP_SECRET_KEY, salt="edusmart-csrf")
google_oauth_serializer = URLSafeTimedSerializer(APP_SECRET_KEY, salt="edusmart-google-oauth")

# ── Google OAuth ───────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "https://xiaozhiscig.biz.id/api/auth/google/callback",
).strip()

# ── JWT Secret ─────────────────────────────────────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    JWT_SECRET = APP_SECRET_KEY
    if not IS_PRODUCTION:
        logger.warning(
            "JWT_SECRET belum diset. Menggunakan APP_SECRET_KEY sebagai fallback."
        )


def _fernet_from_secret() -> Fernet:
    data_secret = os.getenv("DATA_ENCRYPTION_KEY")
    if IS_PRODUCTION and not data_secret:
        raise RuntimeError("DATA_ENCRYPTION_KEY wajib diset untuk production.")
    secret = data_secret or APP_SECRET_KEY
    try:
        return Fernet(secret.encode("utf-8"))
    except Exception:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


fernet = _fernet_from_secret()

# ── Smart Home Virtual ─────────────────────────────────────────────────────
SMART_HOME_RELAYS = {
    "1": {
        "name": "Ruang Tamu",
        "location": "living_room",
        "color": "yellow",
        "device": "Lampu utama",
        "aliases": ("ruang tamu", "tamu", "living room"),
    },
    "2": {
        "name": "Dapur",
        "location": "kitchen",
        "color": "white",
        "device": "Lampu dapur",
        "aliases": ("dapur", "kitchen"),
    },
    "3": {
        "name": "Kamar Mandi",
        "location": "bathroom",
        "color": "white",
        "device": "Lampu kamar mandi",
        "aliases": ("kamar mandi", "mandi", "wc", "toilet", "bathroom", "km"),
    },
    "4": {
        "name": "Kamar Tidur Utama",
        "location": "bedroom_main",
        "color": "white",
        "device": "Lampu kamar utama",
        "aliases": (
            "kamar tidur utama", "kamar utama", "master bedroom",
            "tidur utama", "kamar 1", "kamar satu", "bedroom utama", "bedroom 1",
        ),
    },
    "5": {
        "name": "Teras",
        "location": "terrace",
        "color": "white",
        "device": "Lampu teras",
        "aliases": ("teras", "depan", "teras depan", "terrace"),
    },
    "6": {
        "name": "Basement",
        "location": "basement",
        "color": "red",
        "device": "Alarm",
        "aliases": ("basement", "alarm", "sirine", "siren", "bahaya"),
    },
    "7": {
        "name": "Kamar Tidur 2",
        "location": "bedroom_2",
        "color": "white",
        "device": "Lampu kamar kedua",
        "aliases": (
            "kamar tidur 2", "kamar tidur dua", "kamar 2", "kamar dua",
            "kamar kedua", "bedroom 2", "second bedroom",
        ),
    },
    "8": {
        "name": "Ruang AC",
        "location": "ac_room",
        "color": "blue",
        "device": "AC",
        "aliases": ("ruang ac", "ac", "air conditioner", "kipas", "pendingin"),
    },
}
SMART_HOME_CHANNELS = tuple(SMART_HOME_RELAYS.keys())
SMART_HOME_VALID_ROOM_HINT = ", ".join(meta["name"] for meta in SMART_HOME_RELAYS.values())
SMART_HOME_AMBIGUOUS_TARGETS = {
    "kamar": ("4", "7"),
    "kamar tidur": ("4", "7"),
    "bedroom": ("4", "7"),
}
SMART_HOME_ON_ACTIONS = {
    "on", "turn on", "nyala", "nyalakan", "hidup", "hidupkan",
    "1", "true", "aktif", "aktifkan",
}
SMART_HOME_OFF_ACTIONS = {
    "off", "turn off", "mati", "matikan", "padam", "padamkan",
    "0", "false", "nonaktif", "nonaktifkan",
}

# Build channel alias lookup
SMART_HOME_CHANNEL_ALIASES: dict = {}
for _ch, _meta in SMART_HOME_RELAYS.items():
    _aliases = set(_meta["aliases"])
    _aliases.update({_ch, f"ch {_ch}", f"ch{_ch}", f"channel {_ch}", f"kanal {_ch}"})
    SMART_HOME_CHANNEL_ALIASES[_ch] = _aliases

# ── Firmware Marketplace ───────────────────────────────────────────────────
MARKETPLACE_ADMIN_FEE_FLAT = int(os.getenv("MARKETPLACE_ADMIN_FEE_FLAT", "1500"))
MARKETPLACE_ADMIN_FEE_PERCENT = float(os.getenv("MARKETPLACE_ADMIN_FEE_PERCENT", "0.0"))
MARKETPLACE_PAYMENT_PROVIDER = os.getenv("MARKETPLACE_PAYMENT_PROVIDER", "simulator").strip().lower()
MINIMUM_WITHDRAWAL = int(os.getenv("MINIMUM_WITHDRAWAL", "10000"))
WITHDRAWAL_FEE = int(os.getenv("WITHDRAWAL_FEE", "0"))

# S3 / Object Storage (NepalCloud / AWS / Cloudflare R2 / MinIO)
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "").strip()
S3_REGION = os.getenv("S3_REGION", "us-east-1").strip()
S3_BUCKET_PUBLIC = os.getenv("S3_BUCKET_PUBLIC", "xiaozhi-public").strip()
S3_BUCKET_PRIVATE = os.getenv("S3_BUCKET_PRIVATE", "xiaozhi-private").strip()
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "").strip()
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "").strip()
S3_USE_SSL = os.getenv("S3_USE_SSL", "true").strip().lower() == "true"

# Payment Secrets
XENDIT_SECRET_KEY = os.getenv("XENDIT_SECRET_KEY", "").strip()
XENDIT_WEBHOOK_TOKEN = os.getenv("XENDIT_WEBHOOK_TOKEN", "").strip()
MIDTRANS_SERVER_KEY = os.getenv("MIDTRANS_SERVER_KEY", "").strip()
MIDTRANS_CLIENT_KEY = os.getenv("MIDTRANS_CLIENT_KEY", "").strip()
MIDTRANS_IS_PRODUCTION = os.getenv("MIDTRANS_IS_PRODUCTION", "false").strip().lower() == "true"

