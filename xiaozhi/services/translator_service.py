"""Translation service using free translation APIs."""
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("xiaozhi.translator")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# Common translations dictionary (fallback when API unavailable)
COMMON_TRANSLATIONS = {
    # Indonesian to English
    "id-en": {
        "selamat pagi": "good morning",
        "selamat siang": "good afternoon",
        "selamat malam": "good night",
        "terima kasih": "thank you",
        "sama-sama": "you're welcome",
        "apa kabar": "how are you",
        "baik-baik saja": "i'm fine",
        "sampai jumpa": "see you",
        "permisi": "excuse me",
        "maaf": "sorry",
        "ya": "yes",
        "tidak": "no",
        "air": "water",
        "makan": "eat",
        "minum": "drink",
        "tidur": "sleep",
        "jalan": "walk",
        "rumah": "house",
        "sekolah": "school",
    },
    # English to Indonesian
    "en-id": {
        "good morning": "selamat pagi",
        "good afternoon": "selamat siang",
        "good night": "selamat malam",
        "thank you": "terima kasih",
        "you're welcome": "sama-sama",
        "how are you": "apa kabar",
        "i'm fine": "baik-baik saja",
        "see you": "sampai jumpa",
        "excuse me": "permisi",
        "sorry": "maaf",
        "yes": "ya",
        "no": "tidak",
        "water": "air",
        "eat": "makan",
        "drink": "minum",
        "sleep": "tidur",
        "walk": "jalan",
        "house": "rumah",
        "school": "sekolah",
    },
}


def detect_language(text: str) -> str:
    """Simple language detection."""
    # Check for Indonesian-specific patterns
    id_patterns = [
        r'\b(aku|saya|kamu|dia|kami|kalian|mereka)\b',
        r'\b(adalah|ialah|merupakan)\b',
        r'\b(sedang|telah|akan|sudah|belum)\b',
        r'\b(dan|atau|tetapi|namun|karena)\b',
    ]
    for pattern in id_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return "id"

    # Default to English if contains mostly ASCII
    if all(ord(c) < 128 for c in text):
        return "en"

    return "id"


def translate_text(text: str, target_lang: str, source_lang: str = "") -> Dict[str, Any]:
    """Translate text to target language."""
    if not text.strip():
        return {"success": False, "error": "Teks kosong"}

    if not source_lang:
        source_lang = detect_language(text)

    if source_lang == target_lang:
        return {
            "success": True,
            "original": text,
            "translated": text,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "message": "Bahasa sumber dan target sama",
        }

    # Check common translations first
    dict_key = f"{source_lang}-{target_lang}"
    common = COMMON_TRANSLATIONS.get(dict_key, {})
    text_lower = text.lower().strip()
    if text_lower in common:
        return {
            "success": True,
            "original": text,
            "translated": common[text_lower],
            "source_lang": source_lang,
            "target_lang": target_lang,
        }

    # Try Google Translate (free tier)
    if HAS_REQUESTS:
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            params = {
                "client": "gtx",
                "sl": source_lang,
                "tl": target_lang,
                "dt": "t",
                "q": text,
            }
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if result and isinstance(result, list) and result[0]:
                    translated = "".join(item[0] for item in result[0] if item[0])
                    return {
                        "success": True,
                        "original": text,
                        "translated": translated,
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                    }
        except Exception as e:
            logger.warning(f"Translation API failed: {e}")

    return {
        "success": False,
        "error": "Terjemahan tidak tersedia. Coba install requests: pip install requests",
    }


def get_supported_languages() -> Dict[str, str]:
    """Get supported languages."""
    return {
        "id": "Indonesia",
        "en": "English",
        "ja": "Japanese",
        "ko": "Korean",
        "zh": "Chinese",
        "ar": "Arabic",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
        "ru": "Russian",
    }
