import base64
import hashlib
import logging
import os
import secrets

from cryptography.fernet import Fernet
from itsdangerous import URLSafeTimedSerializer

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
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "irsyad03")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "irsyad031226")
RESTORED_USER_USERNAME = os.getenv("RESTORED_USER_USERNAME", "irsyad26")
RESTORED_USER_PASSWORD = os.getenv("RESTORED_USER_PASSWORD", "irsyad261203")

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
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")

# ── JWT Secret ─────────────────────────────────────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError("JWT_SECRET wajib diset untuk production.")
    JWT_SECRET = secrets.token_urlsafe(64)
    logger.warning(
        "JWT_SECRET belum diset. Token JWT hanya stabil sampai proses restart."
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
