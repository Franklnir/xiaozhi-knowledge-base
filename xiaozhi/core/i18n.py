"""Internationalization (i18n) support."""
from typing import Dict, Optional

# Supported languages
SUPPORTED_LANGUAGES = {"id": "Indonesia", "en": "English"}
DEFAULT_LANGUAGE = "id"

# Translations
_translations: Dict[str, Dict[str, str]] = {
    "id": {
        # Auth
        "login.title": "Masuk",
        "login.username": "Username",
        "login.password": "Password",
        "login.submit": "Masuk",
        "login.no_account": "Belum punya akun?",
        "login.register": "Daftar",
        "login.error": "Username atau password salah.",
        "register.title": "Daftar",
        "register.submit": "Daftar",
        "register.success": "Akun berhasil dibuat!",
        "register.has_account": "Sudah punya akun?",
        # Dashboard
        "dashboard.title": "Dashboard",
        "dashboard.welcome": "Selamat datang",
        "dashboard.materials": "Materi",
        "dashboard.add_material": "Tambah Materi",
        "dashboard.search": "Cari materi...",
        # Common
        "common.save": "Simpan",
        "common.cancel": "Batal",
        "common.delete": "Hapus",
        "common.edit": "Edit",
        "common.loading": "Memuat...",
        "common.error": "Terjadi kesalahan",
        "common.success": "Berhasil",
        # MCP
        "mcp.status": "Status MCP",
        "mcp.connected": "Terhubung",
        "mcp.disconnected": "Terputus",
        "mcp.save_endpoint": "Simpan Endpoint",
        "mcp.delete_endpoint": "Hapus Endpoint",
        # Smart Home
        "smarthome.title": "Smart Home",
        "smarthome.relay_on": "Nyala",
        "smarthome.relay_off": "Mati",
        # Admin
        "admin.title": "Panel Admin",
        "admin.users": "Manajemen User",
        "admin.mcp_monitor": "Monitor MCP",
    },
    "en": {
        # Auth
        "login.title": "Login",
        "login.username": "Username",
        "login.password": "Password",
        "login.submit": "Login",
        "login.no_account": "Don't have an account?",
        "login.register": "Register",
        "login.error": "Invalid username or password.",
        "register.title": "Register",
        "register.submit": "Register",
        "register.success": "Account created successfully!",
        "register.has_account": "Already have an account?",
        # Dashboard
        "dashboard.title": "Dashboard",
        "dashboard.welcome": "Welcome",
        "dashboard.materials": "Materials",
        "dashboard.add_material": "Add Material",
        "dashboard.search": "Search materials...",
        # Common
        "common.save": "Save",
        "common.cancel": "Cancel",
        "common.delete": "Delete",
        "common.edit": "Edit",
        "common.loading": "Loading...",
        "common.error": "An error occurred",
        "common.success": "Success",
        # MCP
        "mcp.status": "MCP Status",
        "mcp.connected": "Connected",
        "mcp.disconnected": "Disconnected",
        "mcp.save_endpoint": "Save Endpoint",
        "mcp.delete_endpoint": "Delete Endpoint",
        # Smart Home
        "smarthome.title": "Smart Home",
        "smarthome.relay_on": "ON",
        "smarthome.relay_off": "OFF",
        # Admin
        "admin.title": "Admin Panel",
        "admin.users": "User Management",
        "admin.mcp_monitor": "MCP Monitor",
    },
}


def t(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """Translate a key to the specified language."""
    lang = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    text = _translations.get(lang, {}).get(key)
    if text is None:
        # Fallback to default language
        text = _translations.get(DEFAULT_LANGUAGE, {}).get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def get_language(request) -> str:
    """Get language from request (cookie or header)."""
    # Check cookie first
    lang = request.cookies.get("lang", "")
    if lang in SUPPORTED_LANGUAGES:
        return lang
    # Check Accept-Language header
    accept = request.headers.get("accept-language", "")
    if accept.startswith("en"):
        return "en"
    return DEFAULT_LANGUAGE


def get_translations(lang: str = DEFAULT_LANGUAGE) -> Dict[str, str]:
    """Get all translations for a language."""
    return _translations.get(lang, _translations.get(DEFAULT_LANGUAGE, {}))
