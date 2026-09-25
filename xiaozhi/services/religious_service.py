"""
Religious and Spiritual Service for Xiaozhi:
Comprehensive, accurate, respectful, and reverent guides for:
- Islam, Christianity & Catholicism, Hinduism, Buddhism, Confucianism, and Judaism.
Covers:
1. Holy Scriptures (Kitab Suci): 114 Surahs of Al-Qur'an (Online Live API + Offline Canon), Hadiths, Bible (Old & New Testament), Bhagavad Gita, Dhammapada, Lun Yu, Tanakh.
2. Daily Prayers (Doa Sehari-hari): Makan, tidur, perjalanan/kendaraan, keluar/masuk rumah, wc, orang tua, sapu jagat, kesembuhan, sayyidul istighfar, cemas/hutang, Doa Bapa Kami, Salam Maria, Angelus, Tri Sandhya, dsb.
3. Worship & Ritual Guides (Tata Cara Ibadah): Sholat 5 waktu, wudhu, misa ekaristi, panca sembah, puja bakti, sembahyang dupa.
"""
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("xiaozhi.services.religious_service")

# ═════════════════════════════════════════════════════════════════════════════
# 1. KATALOG 114 SURAT AL-QUR'AN & PARSER REFERENSI AYAT
# ═════════════════════════════════════════════════════════════════════════════

SURAH_CATALOG: Dict[int, Dict[str, Any]] = {
    1: {"name": "Al-Fatihah", "latin": "Al-Fatihah", "arti": "Pembukaan", "ayat": 7, "type": "Makkiyah", "aliases": ["fatihah", "al fatihah", "alfatihah", "surat 1", "ummul quran", "al-fatihah"]},
    2: {"name": "Al-Baqarah", "latin": "Al-Baqarah", "arti": "Sapi Betina", "ayat": 286, "type": "Madaniyah", "aliases": ["baqarah", "al baqarah", "albaqarah", "surat 2", "ayat kursi", "al-baqarah"]},
    3: {"name": "Ali 'Imran", "latin": "Ali 'Imran", "arti": "Keluarga Imran", "ayat": 200, "type": "Madaniyah", "aliases": ["ali imran", "al imran", "keluarga imran", "surat 3", "ali-imran"]},
    4: {"name": "An-Nisa'", "latin": "An-Nisa'", "arti": "Wanita", "ayat": 176, "type": "Madaniyah", "aliases": ["an nisa", "annisa", "nisa", "surat 4", "an-nisa"]},
    5: {"name": "Al-Ma'idah", "latin": "Al-Ma'idah", "arti": "Jamuan", "ayat": 120, "type": "Madaniyah", "aliases": ["al maidah", "almaidah", "maidah", "surat 5", "al-maidah"]},
    6: {"name": "Al-An'am", "latin": "Al-An'am", "arti": "Binatang Ternak", "ayat": 165, "type": "Makkiyah", "aliases": ["al anam", "alanam", "surat 6", "al-anam"]},
    7: {"name": "Al-A'raf", "latin": "Al-A'raf", "arti": "Tempat Tertinggi", "ayat": 206, "type": "Makkiyah", "aliases": ["al araf", "alaraf", "surat 7", "al-araf"]},
    8: {"name": "Al-Anfal", "latin": "Al-Anfal", "arti": "Rampasan Perang", "ayat": 75, "type": "Madaniyah", "aliases": ["al anfal", "alanfal", "surat 8", "al-anfal"]},
    9: {"name": "At-Taubah", "latin": "At-Taubah", "arti": "Pengampunan", "ayat": 129, "type": "Madaniyah", "aliases": ["at taubah", "attaubah", "taubah", "surat 9", "at-taubah", "bara'ah"]},
    10: {"name": "Yunus", "latin": "Yunus", "arti": "Nabi Yunus", "ayat": 109, "type": "Makkiyah", "aliases": ["yunus", "surat 10"]},
    11: {"name": "Hud", "latin": "Hud", "arti": "Nabi Hud", "ayat": 123, "type": "Makkiyah", "aliases": ["hud", "surat 11"]},
    12: {"name": "Yusuf", "latin": "Yusuf", "arti": "Nabi Yusuf", "ayat": 111, "type": "Makkiyah", "aliases": ["yusuf", "surat 12"]},
    13: {"name": "Ar-Ra'd", "latin": "Ar-Ra'd", "arti": "Guruh", "ayat": 43, "type": "Madaniyah", "aliases": ["ar rad", "arrad", "surat 13", "ar-rad"]},
    14: {"name": "Ibrahim", "latin": "Ibrahim", "arti": "Nabi Ibrahim", "ayat": 52, "type": "Makkiyah", "aliases": ["ibrahim", "surat 14"]},
    15: {"name": "Al-Hijr", "latin": "Al-Hijr", "arti": "Gunung Al-Hijr", "ayat": 99, "type": "Makkiyah", "aliases": ["al hijr", "alhijr", "surat 15", "al-hijr"]},
    16: {"name": "An-Nahl", "latin": "An-Nahl", "arti": "Lebah", "ayat": 128, "type": "Makkiyah", "aliases": ["an nahl", "annahl", "nahl", "surat 16", "an-nahl"]},
    17: {"name": "Al-Isra'", "latin": "Al-Isra'", "arti": "Memperjalankan Malam Hari", "ayat": 111, "type": "Makkiyah", "aliases": ["al isra", "alisra", "isra", "surat 17", "bani israil", "al-isra"]},
    18: {"name": "Al-Kahf", "latin": "Al-Kahf", "arti": "Gua", "ayat": 110, "type": "Makkiyah", "aliases": ["al kahf", "al kahfi", "alkahfi", "kahfi", "surat 18", "al-kahf", "al-kahfi"]},
    19: {"name": "Maryam", "latin": "Maryam", "arti": "Maryam", "ayat": 98, "type": "Makkiyah", "aliases": ["maryam", "surat 19"]},
    20: {"name": "Ta-Ha", "latin": "Ta-Ha", "arti": "Ta Ha", "ayat": 135, "type": "Makkiyah", "aliases": ["taha", "ta ha", "surat 20", "ta-ha"]},
    21: {"name": "Al-Anbiya'", "latin": "Al-Anbiya'", "arti": "Para Nabi", "ayat": 112, "type": "Makkiyah", "aliases": ["al anbiya", "alanbiya", "surat 21", "al-anbiya"]},
    22: {"name": "Al-Hajj", "latin": "Al-Hajj", "arti": "Haji", "ayat": 78, "type": "Madaniyah", "aliases": ["al hajj", "alhajj", "hajj", "surat 22", "al-hajj"]},
    23: {"name": "Al-Mu'minun", "latin": "Al-Mu'minun", "arti": "Orang-orang Mukmin", "ayat": 118, "type": "Makkiyah", "aliases": ["al muminun", "almuminun", "surat 23", "al-muminun"]},
    24: {"name": "An-Nur", "latin": "An-Nur", "arti": "Cahaya", "ayat": 64, "type": "Madaniyah", "aliases": ["an nur", "annur", "nur", "surat 24", "an-nur"]},
    25: {"name": "Al-Furqan", "latin": "Al-Furqan", "arti": "Pembeda", "ayat": 77, "type": "Makkiyah", "aliases": ["al furqan", "alfurqan", "surat 25", "al-furqan"]},
    26: {"name": "Asy-Syu'ara'", "latin": "Asy-Syu'ara'", "arti": "Para Penyair", "ayat": 227, "type": "Makkiyah", "aliases": ["asy syuara", "syuara", "surat 26", "asy-syuara"]},
    27: {"name": "An-Naml", "latin": "An-Naml", "arti": "Semut", "ayat": 93, "type": "Makkiyah", "aliases": ["an naml", "annaml", "surat 27", "an-naml"]},
    28: {"name": "Al-Qashash", "latin": "Al-Qashash", "arti": "Kisah-kisah", "ayat": 88, "type": "Makkiyah", "aliases": ["al qashash", "alqashash", "qashash", "surat 28", "al-qashash"]},
    29: {"name": "Al-'Ankabut", "latin": "Al-'Ankabut", "arti": "Laba-laba", "ayat": 69, "type": "Makkiyah", "aliases": ["al ankabut", "alankabut", "surat 29", "al-ankabut"]},
    30: {"name": "Ar-Rum", "latin": "Ar-Rum", "arti": "Bangsa Romawi", "ayat": 60, "type": "Makkiyah", "aliases": ["ar rum", "arrum", "surat 30", "ar-rum"]},
    31: {"name": "Luqman", "latin": "Luqman", "arti": "Luqman", "ayat": 34, "type": "Makkiyah", "aliases": ["luqman", "surat 31"]},
    32: {"name": "As-Sajdah", "latin": "As-Sajdah", "arti": "Sujud", "ayat": 30, "type": "Makkiyah", "aliases": ["as sajdah", "assajdah", "sajdah", "surat 32", "as-sajdah"]},
    33: {"name": "Al-Ahzab", "latin": "Al-Ahzab", "arti": "Golongan yang Bersekutu", "ayat": 73, "type": "Madaniyah", "aliases": ["al ahzab", "alahzab", "ahzab", "surat 33", "al-ahzab"]},
    34: {"name": "Saba'", "latin": "Saba'", "arti": "Kaum Saba'", "ayat": 54, "type": "Makkiyah", "aliases": ["saba", "surat 34"]},
    35: {"name": "Fathir", "latin": "Fathir", "arti": "Pencipta", "ayat": 45, "type": "Makkiyah", "aliases": ["fathir", "surat 35"]},
    36: {"name": "Ya-Sin", "latin": "Ya-Sin", "arti": "Ya Sin", "ayat": 83, "type": "Makkiyah", "aliases": ["yasin", "ya sin", "yaseen", "surat 36", "jantung al-quran", "ya-sin"]},
    37: {"name": "Ash-Shaffat", "latin": "Ash-Shaffat", "arti": "Barisan-barisan", "ayat": 182, "type": "Makkiyah", "aliases": ["ash shaffat", "shaffat", "surat 37", "ash-shaffat"]},
    38: {"name": "Shad", "latin": "Shad", "arti": "Shad", "ayat": 88, "type": "Makkiyah", "aliases": ["shad", "sad", "surat 38"]},
    39: {"name": "Az-Zumar", "latin": "Az-Zumar", "arti": "Rombongan", "ayat": 75, "type": "Makkiyah", "aliases": ["az zumar", "azzumar", "zumar", "surat 39", "az-zumar"]},
    40: {"name": "Ghafir", "latin": "Ghafir", "arti": "Maha Pengampun", "ayat": 85, "type": "Makkiyah", "aliases": ["ghafir", "al mumin", "surat 40"]},
    41: {"name": "Fushshilat", "latin": "Fushshilat", "arti": "Dijelaskan", "ayat": 54, "type": "Makkiyah", "aliases": ["fushshilat", "fussilat", "surat 41"]},
    42: {"name": "Asy-Syura", "latin": "Asy-Syura", "arti": "Musyawarah", "ayat": 53, "type": "Makkiyah", "aliases": ["asy syura", "syura", "surat 42", "asy-syura"]},
    43: {"name": "Az-Zukhruf", "latin": "Az-Zukhruf", "arti": "Perhiasan", "ayat": 89, "type": "Makkiyah", "aliases": ["az zukhruf", "zukhruf", "surat 43", "az-zukhruf"]},
    44: {"name": "Ad-Dukhan", "latin": "Ad-Dukhan", "arti": "Kabut Asap", "ayat": 59, "type": "Makkiyah", "aliases": ["ad dukhan", "addukhan", "dukhan", "surat 44", "ad-dukhan"]},
    45: {"name": "Al-Jatsiyah", "latin": "Al-Jatsiyah", "arti": "Berlutut", "ayat": 37, "type": "Makkiyah", "aliases": ["al jatsiyah", "jatsiyah", "surat 45", "al-jatsiyah"]},
    46: {"name": "Al-Ahqaf", "latin": "Al-Ahqaf", "arti": "Bukit-bukit Pasir", "ayat": 35, "type": "Makkiyah", "aliases": ["al ahqaf", "ahqaf", "surat 46", "al-ahqaf"]},
    47: {"name": "Muhammad", "latin": "Muhammad", "arti": "Nabi Muhammad", "ayat": 38, "type": "Madaniyah", "aliases": ["muhammad", "surat 47"]},
    48: {"name": "Al-Fath", "latin": "Al-Fath", "arti": "Kemenangan", "ayat": 29, "type": "Madaniyah", "aliases": ["al fath", "alfath", "fath", "surat 48", "al-fath"]},
    49: {"name": "Al-Hujurat", "latin": "Al-Hujurat", "arti": "Kamar-kamar", "ayat": 18, "type": "Madaniyah", "aliases": ["al hujurat", "hujurat", "surat 49", "al-hujurat"]},
    50: {"name": "Qaf", "latin": "Qaf", "arti": "Qaf", "ayat": 45, "type": "Makkiyah", "aliases": ["qaf", "surat 50"]},
    51: {"name": "Adz-Dzariyat", "latin": "Adz-Dzariyat", "arti": "Angin yang Menerbangkan", "ayat": 60, "type": "Makkiyah", "aliases": ["adz dzariyat", "dzariyat", "surat 51", "adz-dzariyat"]},
    52: {"name": "Ath-Thur", "latin": "Ath-Thur", "arti": "Bukit Tursina", "ayat": 49, "type": "Makkiyah", "aliases": ["ath thur", "thur", "surat 52", "ath-thur"]},
    53: {"name": "An-Najm", "latin": "An-Najm", "arti": "Bintang", "ayat": 62, "type": "Makkiyah", "aliases": ["an najm", "najm", "surat 53", "an-najm"]},
    54: {"name": "Al-Qamar", "latin": "Al-Qamar", "arti": "Bulan", "ayat": 55, "type": "Makkiyah", "aliases": ["al qamar", "qamar", "surat 54", "al-qamar"]},
    55: {"name": "Ar-Rahman", "latin": "Ar-Rahman", "arti": "Maha Pemurah", "ayat": 78, "type": "Madaniyah", "aliases": ["ar rahman", "arrahman", "rahman", "surat 55", "ar-rahman"]},
    56: {"name": "Al-Waqi'ah", "latin": "Al-Waqi'ah", "arti": "Hari Kiamat", "ayat": 96, "type": "Makkiyah", "aliases": ["al waqiah", "alwaqiah", "waqiah", "surat 56", "al-waqiah"]},
    57: {"name": "Al-Hadid", "latin": "Al-Hadid", "arti": "Besi", "ayat": 29, "type": "Madaniyah", "aliases": ["al hadid", "hadid", "surat 57", "al-hadid"]},
    58: {"name": "Al-Mujadilah", "latin": "Al-Mujadilah", "arti": "Gugatan", "ayat": 22, "type": "Madaniyah", "aliases": ["al mujadilah", "mujadilah", "surat 58", "al-mujadilah"]},
    59: {"name": "Al-Hasyr", "latin": "Al-Hasyr", "arti": "Pengusiran", "ayat": 24, "type": "Madaniyah", "aliases": ["al hasyr", "hasyr", "surat 59", "al-hasyr"]},
    60: {"name": "Al-Mumtahanah", "latin": "Al-Mumtahanah", "arti": "Wanita yang Diuji", "ayat": 13, "type": "Madaniyah", "aliases": ["al mumtahanah", "mumtahanah", "surat 60", "al-mumtahanah"]},
    61: {"name": "Ash-Shaff", "latin": "Ash-Shaff", "arti": "Barisan", "ayat": 14, "type": "Madaniyah", "aliases": ["ash shaff", "shaff", "surat 61", "ash-shaff"]},
    62: {"name": "Al-Jumu'ah", "latin": "Al-Jumu'ah", "arti": "Hari Jumat", "ayat": 11, "type": "Madaniyah", "aliases": ["al jumuah", "jumuah", "jumat", "surat 62", "al-jumuah"]},
    63: {"name": "Al-Munafiqun", "latin": "Al-Munafiqun", "arti": "Orang Munafik", "ayat": 11, "type": "Madaniyah", "aliases": ["al munafiqun", "munafiqun", "surat 63", "al-munafiqun"]},
    64: {"name": "At-Taghabun", "latin": "At-Taghabun", "arti": "Hari Ditampakkan Kesalahan", "ayat": 18, "type": "Madaniyah", "aliases": ["at taghabun", "taghabun", "surat 64", "at-taghabun"]},
    65: {"name": "Ath-Thalaq", "latin": "Ath-Thalaq", "arti": "Perceraian", "ayat": 12, "type": "Madaniyah", "aliases": ["ath thalaq", "thalaq", "surat 65", "ayat seribu dinar", "ath-thalaq"]},
    66: {"name": "At-Tahrim", "latin": "At-Tahrim", "arti": "Pengharaman", "ayat": 12, "type": "Madaniyah", "aliases": ["at tahrim", "tahrim", "surat 66", "at-tahrim"]},
    67: {"name": "Al-Mulk", "latin": "Al-Mulk", "arti": "Kerajaan", "ayat": 30, "type": "Makkiyah", "aliases": ["al mulk", "almulk", "mulk", "tabarak", "surat 67", "al-mulk"]},
    68: {"name": "Al-Qalam", "latin": "Al-Qalam", "arti": "Pena", "ayat": 52, "type": "Makkiyah", "aliases": ["al qalam", "qalam", "surat 68", "nun", "al-qalam"]},
    69: {"name": "Al-Haqqah", "latin": "Al-Haqqah", "arti": "Hari Kiamat yang Pasti", "ayat": 52, "type": "Makkiyah", "aliases": ["al haqqah", "haqqah", "surat 69", "al-haqqah"]},
    70: {"name": "Al-Ma'arij", "latin": "Al-Ma'arij", "arti": "Tempat-tempat Naik", "ayat": 44, "type": "Makkiyah", "aliases": ["al maarij", "maarij", "surat 70", "al-maarij"]},
    71: {"name": "Nuh", "latin": "Nuh", "arti": "Nabi Nuh", "ayat": 28, "type": "Makkiyah", "aliases": ["nuh", "surat 71"]},
    72: {"name": "Al-Jinn", "latin": "Al-Jinn", "arti": "Jin", "ayat": 28, "type": "Makkiyah", "aliases": ["al jinn", "jinn", "jin", "surat 72", "al-jinn"]},
    73: {"name": "Al-Muzzammil", "latin": "Al-Muzzammil", "arti": "Orang yang Berselimut", "ayat": 20, "type": "Makkiyah", "aliases": ["al muzzammil", "muzzammil", "surat 73", "al-muzzammil"]},
    74: {"name": "Al-Muddatstsir", "latin": "Al-Muddatstsir", "arti": "Orang yang Berkemul", "ayat": 56, "type": "Makkiyah", "aliases": ["al muddatstsir", "muddatstsir", "surat 74", "al-muddatstsir"]},
    75: {"name": "Al-Qiyamah", "latin": "Al-Qiyamah", "arti": "Hari Kiamat", "ayat": 40, "type": "Makkiyah", "aliases": ["al qiyamah", "qiyamah", "surat 75", "al-qiyamah"]},
    76: {"name": "Al-Insan", "latin": "Al-Insan", "arti": "Manusia", "ayat": 31, "type": "Madaniyah", "aliases": ["al insan", "insan", "ad dahr", "surat 76", "al-insan"]},
    77: {"name": "Al-Mursalat", "latin": "Al-Mursalat", "arti": "Malaikat yang Diutus", "ayat": 50, "type": "Makkiyah", "aliases": ["al mursalat", "mursalat", "surat 77", "al-mursalat"]},
    78: {"name": "An-Naba'", "latin": "An-Naba'", "arti": "Berita Besar", "ayat": 40, "type": "Makkiyah", "aliases": ["an naba", "naba", "amma yatasa'alun", "surat 78", "an-naba"]},
    79: {"name": "An-Nazi'at", "latin": "An-Nazi'at", "arti": "Malaikat yang Mencabut", "ayat": 46, "type": "Makkiyah", "aliases": ["an naziat", "naziat", "surat 79", "an-naziat"]},
    80: {"name": "'Abasa", "latin": "'Abasa", "arti": "Bermuka Masam", "ayat": 42, "type": "Makkiyah", "aliases": ["abasa", "surat 80"]},
    81: {"name": "At-Takwir", "latin": "At-Takwir", "arti": "Menggulung", "ayat": 29, "type": "Makkiyah", "aliases": ["at takwir", "takwir", "surat 81", "at-takwir"]},
    82: {"name": "Al-Infithar", "latin": "Al-Infithar", "arti": "Terbelah", "ayat": 19, "type": "Makkiyah", "aliases": ["al infithar", "infithar", "surat 82", "al-infithar"]},
    83: {"name": "Al-Muthaffifin", "latin": "Al-Muthaffifin", "arti": "Orang yang Curang", "ayat": 36, "type": "Makkiyah", "aliases": ["al muthaffifin", "muthaffifin", "surat 83", "al-muthaffifin"]},
    84: {"name": "Al-Insyiqaq", "latin": "Al-Insyiqaq", "arti": "Terbelah", "ayat": 25, "type": "Makkiyah", "aliases": ["al insyiqaq", "insyiqaq", "surat 84", "al-insyiqaq"]},
    85: {"name": "Al-Buruj", "latin": "Al-Buruj", "arti": "Gugusan Bintang", "ayat": 22, "type": "Makkiyah", "aliases": ["al buruj", "buruj", "surat 85", "al-buruj"]},
    86: {"name": "Ath-Thariq", "latin": "Ath-Thariq", "arti": "Bintang yang Bersinar", "ayat": 17, "type": "Makkiyah", "aliases": ["ath thariq", "thariq", "surat 86", "ath-thariq"]},
    87: {"name": "Al-A'la", "latin": "Al-A'la", "arti": "Yang Paling Tinggi", "ayat": 19, "type": "Makkiyah", "aliases": ["al ala", "alala", "sabbihisma", "surat 87", "al-ala"]},
    88: {"name": "Al-Ghasyiyah", "latin": "Al-Ghasyiyah", "arti": "Hari Pembalasan", "ayat": 26, "type": "Makkiyah", "aliases": ["al ghasyiyah", "ghasyiyah", "surat 88", "al-ghasyiyah"]},
    89: {"name": "Al-Fajr", "latin": "Al-Fajr", "arti": "Fajar", "ayat": 30, "type": "Makkiyah", "aliases": ["al fajr", "fajr", "surat 89", "al-fajr"]},
    90: {"name": "Al-Balad", "latin": "Al-Balad", "arti": "Negeri", "ayat": 20, "type": "Makkiyah", "aliases": ["al balad", "balad", "surat 90", "al-balad"]},
    91: {"name": "Asy-Syams", "latin": "Asy-Syams", "arti": "Matahari", "ayat": 15, "type": "Makkiyah", "aliases": ["asy syams", "syams", "surat 91", "asy-syams"]},
    92: {"name": "Al-Lail", "latin": "Al-Lail", "arti": "Malam", "ayat": 21, "type": "Makkiyah", "aliases": ["al lail", "lail", "surat 92", "al-lail"]},
    93: {"name": "Adh-Dhuha", "latin": "Adh-Dhuha", "arti": "Waktu Dhuha", "ayat": 11, "type": "Makkiyah", "aliases": ["ad dhuha", "ad-dhuha", "dhuha", "waddhuha", "surat 93"]},
    94: {"name": "Asy-Syarh", "latin": "Asy-Syarh", "arti": "Melapangkan", "ayat": 8, "type": "Makkiyah", "aliases": ["asy syarh", "alam nasyrah", "al insyirah", "insyirah", "surat 94", "al-insyirah"]},
    95: {"name": "At-Tin", "latin": "At-Tin", "arti": "Buah Tin", "ayat": 8, "type": "Makkiyah", "aliases": ["at tin", "tin", "surat 95", "at-tin", "wattini"]},
    96: {"name": "Al-'Alaq", "latin": "Al-'Alaq", "arti": "Segumpal Darah", "ayat": 19, "type": "Makkiyah", "aliases": ["al alaq", "iqra", "surat 96", "al-alaq"]},
    97: {"name": "Al-Qadr", "latin": "Al-Qadr", "arti": "Kemuliaan", "ayat": 5, "type": "Makkiyah", "aliases": ["al qadr", "qadr", "lailatul qadr", "inna anzalnahu", "surat 97", "al-qadr"]},
    98: {"name": "Al-Bayyinah", "latin": "Al-Bayyinah", "arti": "Bukti Nyata", "ayat": 8, "type": "Madaniyah", "aliases": ["al bayyinah", "bayyinah", "surat 98", "al-bayyinah"]},
    99: {"name": "Az-Zalzalah", "latin": "Az-Zalzalah", "arti": "Keguncangan", "ayat": 8, "type": "Madaniyah", "aliases": ["az zalzalah", "zalzalah", "idza zulzilat", "surat 99", "az-zalzalah"]},
    100: {"name": "Al-'Adiyat", "latin": "Al-'Adiyat", "arti": "Kuda Perang", "ayat": 11, "type": "Makkiyah", "aliases": ["al adiyat", "adiyat", "wal adiyat", "surat 100", "al-adiyat"]},
    101: {"name": "Al-Qari'ah", "latin": "Al-Qari'ah", "arti": "Hari Kiamat", "ayat": 11, "type": "Makkiyah", "aliases": ["al qariah", "qariah", "surat 101", "al-qariah"]},
    102: {"name": "At-Takatsur", "latin": "At-Takatsur", "arti": "Bermegah-megahan", "ayat": 8, "type": "Makkiyah", "aliases": ["at takatsur", "takatsur", "alhakumut takatsur", "surat 102", "at-takatsur"]},
    103: {"name": "Al-'Asr", "latin": "Al-'Asr", "arti": "Masa / Waktu", "ayat": 3, "type": "Makkiyah", "aliases": ["al asr", "al-asr", "wal asr", "asr", "surat 103"]},
    104: {"name": "Al-Humazah", "latin": "Al-Humazah", "arti": "Pengumpat", "ayat": 9, "type": "Makkiyah", "aliases": ["al humazah", "humazah", "wailul likulli humazah", "surat 104", "al-humazah"]},
    105: {"name": "Al-Fil", "latin": "Al-Fil", "arti": "Gajah", "ayat": 5, "type": "Makkiyah", "aliases": ["al fil", "alfil", "fil", "alam tara kaifa", "surat 105", "al-fil"]},
    106: {"name": "Quraisy", "latin": "Quraisy", "arti": "Suku Quraisy", "ayat": 4, "type": "Makkiyah", "aliases": ["quraisy", "li ilafi quraisy", "surat 106"]},
    107: {"name": "Al-Ma'un", "latin": "Al-Ma'un", "arti": "Barang Berguna", "ayat": 7, "type": "Makkiyah", "aliases": ["al maun", "almaun", "maun", "ara aital ladzi", "surat 107", "al-maun"]},
    108: {"name": "Al-Kautsar", "latin": "Al-Kautsar", "arti": "Nikmat yang Berlimpah", "ayat": 3, "type": "Makkiyah", "aliases": ["al kautsar", "alkautsar", "kautsar", "inna a'thaina", "surat 108", "al-kautsar"]},
    109: {"name": "Al-Kafirun", "latin": "Al-Kafirun", "arti": "Orang-orang Kafir", "ayat": 6, "type": "Makkiyah", "aliases": ["al kafirun", "alkafirun", "kafirun", "qul ya ayyuhal kafirun", "surat 109", "al-kafirun"]},
    110: {"name": "An-Nasr", "latin": "An-Nasr", "arti": "Pertolongan", "ayat": 3, "type": "Madaniyah", "aliases": ["an nasr", "annasr", "nasr", "idza ja'a nashrullah", "surat 110", "an-nasr"]},
    111: {"name": "Al-Lahab", "latin": "Al-Lahab", "arti": "Gejolak Api", "ayat": 5, "type": "Makkiyah", "aliases": ["al lahab", "lahab", "al masad", "tsabbat yada", "surat 111", "al-lahab"]},
    112: {"name": "Al-Ikhlas", "latin": "Al-Ikhlas", "arti": "Memurnikan Keesaan Allah", "ayat": 4, "type": "Makkiyah", "aliases": ["al ikhlas", "alikhlas", "ikhlas", "qul huwallahu ahad", "surat 112", "al-ikhlas"]},
    113: {"name": "Al-Falaq", "latin": "Al-Falaq", "arti": "Waktu Subuh", "ayat": 5, "type": "Makkiyah", "aliases": ["al falaq", "alfalaq", "falaq", "qul a'udzu birabbil falaq", "surat 113", "al-falaq"]},
    114: {"name": "An-Nas", "latin": "An-Nas", "arti": "Manusia", "ayat": 6, "type": "Makkiyah", "aliases": ["an nas", "annas", "nas", "qul a'udzu birabbin nas", "surat 114", "an-nas"]},
}

# In-memory cache for online fetched surahs
_QURAN_CACHE: Dict[int, Dict[str, Any]] = {}


def resolve_surah_reference(query_text: str, book_or_surah: str = "") -> Optional[int]:
    """Find matching surah number from search term or surah name."""
    combined = f"{book_or_surah} {query_text}".lower().strip()
    
    # 1. Check direct number pattern: "surat 112" or "surah 36"
    num_match = re.search(r"\b(?:surat|surah|ke-)\s*(\d{1,3})\b", combined)
    if num_match:
        val = int(num_match.group(1))
        if 1 <= val <= 114:
            return val

    # 2. Check aliases matching in catalog
    for num, meta in SURAH_CATALOG.items():
        if meta["latin"].lower() in combined or meta["name"].lower() in combined:
            return num
        for alias in meta["aliases"]:
            if alias in combined:
                return num

    return None


def parse_verse_range(verse_str: str, max_verses: int) -> Tuple[int, int]:
    """Extract start and end verse from string (e.g., '1-5', '255', 'ayat 3')."""
    if not verse_str:
        return 1, min(max_verses, 10)
    
    clean = re.sub(r"[^\d\-]", "", verse_str.strip())
    if "-" in clean:
        parts = clean.split("-")
        try:
            start = max(1, int(parts[0]))
            end = min(max_verses, int(parts[1]))
            if start <= end:
                return start, end
        except Exception:
            pass
    elif clean.isdigit():
        val = int(clean)
        if 1 <= val <= max_verses:
            return val, val

    return 1, min(max_verses, 10)


def fetch_quran_surah_online(surah_num: int) -> Optional[Dict[str, Any]]:
    """Fetch full surah with Arabic, phonetic Latin, and Indonesian translation from official Kemenag equran.id API."""
    if surah_num in _QURAN_CACHE:
        return _QURAN_CACHE[surah_num]

    url = f"https://equran.id/api/v2/surat/{surah_num}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "XiaozhiReligiousService/2.0"})
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            data = payload.get("data")
            if data:
                _QURAN_CACHE[surah_num] = data
                return data
    except Exception as e:
        logger.warning("Online Quran fetch failed for surah %d: %s. Using local fallback.", surah_num, e)
    
    return None


# ═════════════════════════════════════════════════════════════════════════════
# 2. DATABASE OFFLINE KITAB SUCI (KANONIKAL LINTAS AGAMA)
# ═════════════════════════════════════════════════════════════════════════════

OFFLINE_QURAN_VERSES: Dict[str, Dict[str, Any]] = {
    "al_fatihah": {
        "surah_name": "Al-Fatihah (Surat ke-1)",
        "arti": "Pembukaan",
        "verses": [
            {"verse": 1, "arabic": "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ", "latin": "Bismillāhir-raḥmānir-raḥīm", "translation": "Dengan nama Allah Yang Maha Pengasih, Maha Penyayang."},
            {"verse": 2, "arabic": "الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ", "latin": "Al-ḥamdu lillāhi rabbil-'ālamīn", "translation": "Segala puji bagi Allah, Tuhan seluruh alam."},
            {"verse": 3, "arabic": "الرَّحْمَٰنِ الرَّحِيمِ", "latin": "Ar-raḥmānir-raḥīm", "translation": "Yang Maha Pengasih, Maha Penyayang."},
            {"verse": 4, "arabic": "مَالِكِ يَوْمِ الدِّينِ", "latin": "Māliki yaumid-dīn", "translation": "Pemilik hari pembalasan."},
            {"verse": 5, "arabic": "إِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ", "latin": "Iyyāka na'budu wa iyyāka nasta'īn", "translation": "Hanya kepada Engkaulah kami menyembah dan hanya kepada Engkaulah kami memohon pertolongan."},
            {"verse": 6, "arabic": "اهْدِنَا الصِّرَاطَ الْمُسْتَقِيمَ", "latin": "Ihdinaṣ-ṣirāṭal-mustaqīm", "translation": "Tunjukilah kami jalan yang lurus,"},
            {"verse": 7, "arabic": "صِرَاطَ الَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ الْمَغْضُوبِ عَلَيْهِمْ وَلَا الضَّالِّينَ", "latin": "Ṣirāṭallażīna an'amta 'alaihim gairil-magḍūbi 'alaihim wa laḍ-ḍāllīn", "translation": "(yaitu) jalan orang-orang yang telah Engkau beri nikmat kepadanya; bukan (jalan) mereka yang dimurkai, dan bukan (pula jalan) mereka yang sesat."}
        ]
    },
    "ayat_kursi": {
        "surah_name": "Ayat Kursi (Al-Baqarah: 255)",
        "arti": "Ayat Perlindungan Tertinggi",
        "verses": [
            {
                "verse": 255,
                "arabic": "اللَّهُ لَا إِلَٰهَ إِلَّا هُوَ الْحَيُّ الْقَيُّومُ ۚ لَا تَأْخُذُهُ سِنَةٌ وَلَا نَوْمٌ ۚ لَّهُ مَا فِي السَّمَاوَاتِ وَمَا فِي الْأَرْضِ ۗ مَن ذَا الَّذِي يَشْفَعُ عِندَهُ إِلَّا بِإِذْنِهِ ۚ يَعْلَمُ مَا بَيْنَ أَيْدِيهِمْ وَمَا خَلْفَهُمْ ۖ وَلَا يُحِيطُونَ بِشَيْءٍ مِّنْ عِلْمِهِ إِلَّا بِمَا شَاءَ ۚ وَسِعَ كُرْسِيُّهُ السَّمَاوَاتِ وَالْأَرْضَ ۖ وَلَا يَئُودُهُ حِفْظُهُمَا ۚ وَهُوَ الْعَلِيُّ الْعَظِيمُ",
                "latin": "Allāhu lā ilāha illā huwal-ḥayyul-qayyūm, lā ta'khużuhū sinatuw wa lā na'ūm, lahū mā fis-samāwāti wa mā fil-arḍ, man żal-lażī yasyfa'u 'indahū illā bi'iżnih, ya'lamu mā baina aidīhim wa mā khalfahum, wa lā yuḥīṭūna bisyai'im min 'ilmihī illā bimā syā', wasi'a kursiyyuhus-samāwāti wal-arḍ, wa lā ya'ūduhū ḥifẓuhumā, wa huwal-'aliyyul-'aẓīm.",
                "translation": "Allah, tidak ada tuhan selain Dia. Yang Mahahidup, Yang terus-menerus mengurus makhluk-Nya. Tidak mengantuk dan tidak tidur. Milik-Nya apa yang ada di langit dan apa yang ada di bumi. Tidak ada yang dapat memberi syafaat di sisi-Nya tanpa izin-Nya. Dia mengetahui apa yang ada di hadapan mereka dan apa yang ada di belakang mereka, dan mereka tidak mengetahui sesuatu pun dari ilmu-Nya melainkan apa yang Dia kehendaki. Kursi-Nya (ilmu dan kekuasaan-Nya) meliputi langit dan bumi. Dan Dia tidak merasa berat memelihara keduanya, dan Dia Mahatinggi, Mahabesar."
            }
        ]
    },
    "al_ikhlas": {
        "surah_name": "Al-Ikhlas (Surat ke-112)",
        "arti": "Memurnikan Keesaan Allah",
        "verses": [
            {"verse": 1, "arabic": "قُلْ هُوَ اللَّهُ أَحَدٌ", "latin": "Qul huwallāhu aḥad", "translation": "Katakanlah (Muhammad), 'Dialah Allah, Yang Maha Esa.'"},
            {"verse": 2, "arabic": "اللَّهُ الصَّمَدُ", "latin": "Allāhuṣ-ṣamad", "translation": "Allah tempat meminta segala sesuatu."},
            {"verse": 3, "arabic": "لَمْ يَلِدْ وَلَمْ يُولَدْ", "latin": "Lam yalid wa lam yūlad", "translation": "(Allah) tidak beranak dan tidak pula diperanakkan,"},
            {"verse": 4, "arabic": "وَلَمْ يَكُن لَّهُ كُفُوًا أَحَدٌ", "latin": "Wa lam yakul lahū kufuwan aḥad", "translation": "Dan tidak ada sesuatu pun yang setara dengan Dia."}
        ]
    },
    "al_falaq": {
        "surah_name": "Al-Falaq (Surat ke-113)",
        "arti": "Waktu Fajar / Subuh",
        "verses": [
            {"verse": 1, "arabic": "قُلْ أَعُوذُ بِرَبِّ الْفَلَقِ", "latin": "Qul a'ūżu birabbil-falaq", "translation": "Katakanlah, 'Aku berlindung kepada Tuhan yang menguasai subuh (fajar),'"},
            {"verse": 2, "arabic": "مِن شَرِّ مَا خَلَقَ", "latin": "Min syarri mā khalaq", "translation": "dari kejahatan (makhluk yang) Dia ciptakan,"},
            {"verse": 3, "arabic": "وَمِن شَرِّ غَاسِقٍ إِذَا وَقَبَ", "latin": "Wa min syarri gāsiqin iżā waqab", "translation": "dan dari kejahatan malam apabila telah gelap gulita,"},
            {"verse": 4, "arabic": "وَمِن شَرِّ النَّفَّاثَاتِ فِي الْعُقَدِ", "latin": "Wa min syarrin-naffāṡāti fil-'uqad", "translation": "dan dari kejahatan perempuan-perempuan (penyihir) yang meniup pada buhul-buhul (talinya),"},
            {"verse": 5, "arabic": "وَمِن شَرِّ حَاسِدٍ إِذَا حَسَدَ", "latin": "Wa min syarri ḥāsidin iżā ḥasad", "translation": "dan dari kejahatan orang yang dengki apabila dia dengki."}
        ]
    },
    "an_nas": {
        "surah_name": "An-Nas (Surat ke-114)",
        "arti": "Manusia",
        "verses": [
            {"verse": 1, "arabic": "قُلْ أَعُوذُ بِرَبِّ النَّاسِ", "latin": "Qul a'ūżu birabbin-nās", "translation": "Katakanlah, 'Aku berlindung kepada Tuhannya manusia,'"},
            {"verse": 2, "arabic": "مَلِكِ النَّاسِ", "latin": "Malikin-nās", "translation": "Raja manusia,"},
            {"verse": 3, "arabic": "إِلَٰهِ النَّاسِ", "latin": "Ilāhin-nās", "translation": "Sembahan manusia,"},
            {"verse": 4, "arabic": "مِن شَرِّ الْوَسْوَاسِ الْخَنَّاسِ", "latin": "Min syarril-waswāsil-khannās", "translation": "dari kejahatan (bisikan) setan yang bersembunyi,"},
            {"verse": 5, "arabic": "الَّذِي يُوَسْوِسُ فِي صُدُورِ النَّاسِ", "latin": "Allażī yuwaswisu fī ṣudūrin-nās", "translation": "yang membisikkan (kejahatan) ke dalam dada manusia,"},
            {"verse": 6, "arabic": "مِنَ الْجِنَّةِ وَالنَّاسِ", "latin": "Minal-jinnati wan-nās", "translation": "dari (golongan) jin dan manusia."}
        ]
    },
    "al_kautsar": {
        "surah_name": "Al-Kautsar (Surat ke-108)",
        "arti": "Nikmat yang Berlimpah",
        "verses": [
            {"verse": 1, "arabic": "إِنَّا أَعْطَيْنَاكَ الْكَوْثَرَ", "latin": "Innā a'ṭainākal-kauṡar", "translation": "Sungguh, Kami telah memberimu (Muhammad) nikmat yang banyak."},
            {"verse": 2, "arabic": "فَصَلِّ لِرَبِّكَ وَانْحَرْ", "latin": "Faṣalli lirabbika wan-ḥar", "translation": "Maka laksanakanlah sholat karena Tuhanmu, dan berkurbanlah."},
            {"verse": 3, "arabic": "إِنَّ شَانِئَكَ هُوَ الْأَبْتَرُ", "latin": "Inna syāni'aka huwal-abtar", "translation": "Sungguh, orang-orang yang membencimu dialah yang terputus (dari rahmat Allah)."}
        ]
    },
    "ayat_seribu_dinar": {
        "surah_name": "Ayat Seribu Dinar (Ath-Thalaq: 2-3)",
        "arti": "Kunci Pembuka Rezeki & Jalan Keluar",
        "verses": [
            {
                "verse": "2-3",
                "arabic": "وَمَن يَتَّقِ اللَّهَ يَجْعَل لَّهُ مَخْرَجًا ۝ وَيَرْزُقْهُ مِنْ حَيْثُ لَا يَحْتَسِبُ ۚ وَمَن يَتَوَكَّلْ عَلَى اللَّهِ فَهُوَ حَسْبُهُ ۚ إِنَّ اللَّهَ بَالِغُ أَمْرِهِ ۚ قَدْ جَعَلَ اللَّهُ لِكُلِّ شَيْءٍ قَدْرًا",
                "latin": "Wa may yattaqillāha yaj'al lahū makhrajā, wa yarzuq-hu min ḥaiṡu lā yaḥtasib, wa may yatawakkal 'alallāhi fahuwa ḥasbuh, innallāha bāligu amrih, qad ja'alallāhu likulli syai'in qadrā.",
                "translation": "Barangsiapa bertakwa kepada Allah niscaya Dia akan membukakan jalan keluar baginya, dan Dia memberinya rezeki dari arah yang tidak disangka-sangkanya. Dan barangsiapa bertawakal kepada Allah, niscaya Allah akan mencukupkan (keperluan)nya. Sesungguhnya Allah melaksanakan urusan-Nya. Sungguh, Allah telah mengadakan ketentuan bagi setiap sesuatu."
            }
        ]
    }
}

# ═════════════════════════════════════════════════════════════════════════════
# 3. DATABASE HADITS SHAHIH & ALKITAB & KITAB KANONIKAL
# ═════════════════════════════════════════════════════════════════════════════

CANONICAL_SCRIPTURES: List[Dict[str, Any]] = [
    # ── HADITS SHAHIH ISLAM ────────────────────────────────────────────────
    {
        "religion": "Islam",
        "category": "Hadits Shahih",
        "title": "Hadits Tentang Niat (Innamal A'malu bin Niyyat)",
        "aliases": ["hadits niat", "niat", "innamal a'malu", "amal tergantung niat"],
        "narrator": "HR. Bukhari (No. 1) & Muslim (No. 1907) dari Umar bin Khattab r.a.",
        "arabic": "إِنَّمَا الأَعْمَالُ بِالنِّيَّاتِ ، وَإِنَّمَا لِكُلِّ امْرِئٍ مَا نَوَى",
        "latin": "Innamal-a'mālu bin-niyyāt, wa innamā likullimri'in mā nawā.",
        "translation": "Sesungguhnya setiap amalan tergantung pada niatnya, dan setiap orang akan mendapatkan balasan sesuai dengan apa yang dia niatkan.",
        "hikmah": "Pondasi utama dalam ajaran Islam bahwa keikhlasan niat semata-mata karena Allah SWT adalah penentu diterima atau ditolaknya setiap perbuatan."
    },
    {
        "religion": "Islam",
        "category": "Hadits Shahih",
        "title": "Hadits Senyum adalah Sedekah",
        "aliases": ["senyum adalah sedekah", "hadits senyum", "tabassumuka"],
        "narrator": "HR. Tirmidzi (No. 1956) dari Abu Dzar r.a.",
        "arabic": "تَبَسُّمُكَ فِي وَجْهِ أَخِيكَ لَكَ صَدَقَةٌ",
        "latin": "Tabassumuka fī wajhi akhīka laka ṣadaqah.",
        "translation": "Senyummu di hadapan saudaramu adalah bernilai sedekah bagimu.",
        "hikmah": "Kebaikan dan keramahan wajah adalah sedekah termudah yang menyejukkan hati sesama manusia."
    },
    {
        "religion": "Islam",
        "category": "Hadits Shahih",
        "title": "Hadits Kewajiban Menuntut Ilmu",
        "aliases": ["menuntut ilmu", "hadits ilmu", "thalabul ilmi"],
        "narrator": "HR. Ibnu Majah (No. 224) dari Anas bin Malik r.a. (Shahih)",
        "arabic": "طَلَبُ الْعِلْمِ فَرِيضَةٌ عَلَى كُلِّ مُسْلِمٍ",
        "latin": "Ṭalabul-'ilmi farīḍatun 'alā kulli muslim.",
        "translation": "Menuntut ilmu itu wajib bagi setiap muslim (dan muslimah).",
        "hikmah": "Pentingnya menuntut ilmu pengetahuan duniawi dan ukhrawi untuk mengangkat derajat peradaban."
    },
    {
        "religion": "Islam",
        "category": "Hadits Shahih",
        "title": "Hadits Kasih Sayang Kepada Sesama Ciptaan",
        "aliases": ["kasih sayang", "sayangilah yang di bumi", "irham man fil ardhi"],
        "narrator": "HR. Abu Dawud (No. 4941) & Tirmidzi (No. 1924)",
        "arabic": "ارْحَمُوا مَنْ فِي الأَرْضِ يَرْحَمْكُمْ مَنْ فِي السَّمَاءِ",
        "latin": "Irḥamū man fil-arḍi yarḥamkum man fis-samā'.",
        "translation": "Sayangilah siapa pun yang ada di muka bumi, niscaya Yang di langit (Allah SWT) akan menyayangimu.",
        "hikmah": "Seruan rahmatan lil 'alamin untuk menebarkan cinta kasih kepada seluruh makhluk hidup."
    },
    {
        "religion": "Islam",
        "category": "Hadits Shahih",
        "title": "Hadits Menahan Amarah (Jangan Marah)",
        "aliases": ["jangan marah", "hadits marah", "la taghdhab"],
        "narrator": "HR. Bukhari (No. 6116) dari Abu Hurairah r.a.",
        "arabic": "لاَ تَغْضَبْ وَلَكَ الْجَنَّةُ",
        "latin": "Lā tagḍab wa lakal-jannah.",
        "translation": "Janganlah engkau marah, maka bagimu surga.",
        "hikmah": "Pengendalian diri dan kesabaran emosional adalah ciri keimanan yang tinggi."
    },

    # ── ALKITAB (KRISTEN & KATOLIK) ────────────────────────────────────────
    {
        "religion": "Kristen & Katolik",
        "category": "Alkitab - Perjanjian Lama",
        "title": "Mazmur 23 (Tuhan adalah Gembalaku)",
        "aliases": ["mazmur 23", "psalm 23", "tuhan adalah gembalaku", "gembala yang baik"],
        "text": (
            "1. TUHAN adalah gembalaku, takkan kekurangan aku.\n"
            "2. Ia membaringkan aku di padang yang berumput hijau, Ia membimbing aku ke air yang tenang;\n"
            "3. Ia menyegarkan jiwaku. Ia menuntun aku di jalan yang benar oleh karena nama-Nya.\n"
            "4. Sekalipun aku berjalan dalam lembah kekelaman, aku tidak takut bahaya, sebab Engkau besertaku; gada-Mu dan tongkat-Mu, itulah yang menghibur aku.\n"
            "5. Engkau menyediakan hidangan bagiku, di hadapan lawanku; Engkau mengurapi kepalaku dengan minyak; pialaku penuh melimpah.\n"
            "6. Kebajikan dan kemurahan belaka akan mengikuti aku, seumur hidupku; dan aku akan diam dalam rumah TUHAN sepanjang masa."
        ),
        "hikmah": "Penyerahan diri penuh iman kepada pemeliharaan Tuhan yang tidak pernah meninggalkan umat-Nya."
    },
    {
        "religion": "Kristen & Katolik",
        "category": "Alkitab - Perjanjian Baru",
        "title": "Yohanes 3:16 (Kasih Allah yang Menyelamatkan)",
        "aliases": ["yohanes 3:16", "john 3:16", "karena begitu besar kasih allah"],
        "text": "Karena begitu besar kasih Allah akan dunia ini, sehingga Ia telah mengaruniakan Anak-Nya yang tunggal, supaya setiap orang yang percaya kepada-Nya tidak binasa, melainkan beroleh hidup yang kekal.",
        "hikmah": "Intisari Injil mengenai kasih anugerah keselamatan cuma-cuma dari Allah."
    },
    {
        "religion": "Kristen & Katolik",
        "category": "Alkitab - Perjanjian Baru",
        "title": "1 Korintus 13:4-8 (Hakekat Kasih Sejati / Agape)",
        "aliases": ["1 korintus 13", "korintus 13", "kasih itu sabar", "kidung kasih"],
        "text": (
            "Kasih itu sabar; kasih itu murah hati; ia tidak cemburu. Ia tidak memegahkan diri dan tidak sombong. "
            "Ia tidak melakukan yang tidak sopan dan tidak mencari keuntungan diri sendiri. Ia tidak pemarah dan tidak menyimpan kesalahan orang lain. "
            "Ia tidak bersukacita karena ketidakadilan, tetapi karena kebenaran. Ia menutupi segala sesuatu, percaya segala sesuatu, mengharapkan segala sesuatu, sabar menanggung segala sesuatu. Kasih tidak berkesudahan."
        ),
        "hikmah": "Standar moral kasih tanpa pamrih yang menjadi tiang tertinggi hubungan antarmanusia."
    },
    {
        "religion": "Kristen & Katolik",
        "category": "Alkitab - Perjanjian Lama",
        "title": "Mazmur 91 (Dalam Lindungan Yang Mahatinggi)",
        "aliases": ["mazmur 91", "psalm 91", "dalam lindungan yang mahatinggi", "doa perlindungan kristen"],
        "text": (
            "Orang yang duduk dalam lindungan Yang Mahatinggi dan bermalam dalam naungan Yang Mahakuasa akan berkata kepada TUHAN: "
            "'Tempat perlindunganku dan kubu pertahananku, Allahku, pada-Nya aku percaya.' Sungguh, Dialah yang akan melepaskan engkau dari jerat penangkap burung, dari penyakit sampar yang busuk. "
            "Dengan kepak-Nya Ia akan menudungi engkau, di bawah sayap-Nya engkau akan berlindung."
        ),
        "hikmah": "Janji perlindungan ilahi dari ketakutan malam hari dan marabahaya."
    },
    {
        "religion": "Kristen & Katolik",
        "category": "Alkitab - Perjanjian Baru",
        "title": "Filipi 4:6-7 (Ketenangan Batin & Jangan Kuatir)",
        "aliases": ["filipi 4:6-7", "filipi 4", "jangan kuatir", "damai sejahtera allah"],
        "text": "Janganlah hendaknya kamu kuatir tentang apa pun juga, tetapi nyatakanlah dalam segala hal keinginanmu kepada Allah dalam doa dan permohonan dengan ucapan syukur. Damai sejahtera Allah, yang melampaui segala akal, akan memelihara hati dan pikiranmu dalam Kristus Yesus.",
        "hikmah": "Ketenangan hati yang mengatasi segala kecemasan duniawi lewat doa dan ucapan syukur."
    },

    # ── HINDU, BUDDHA, KONGHUCU, YAHUDI ────────────────────────────────────
    {
        "religion": "Hindu",
        "category": "Wedha & Bhagavad Gita",
        "title": "Bhagavad Gita 2.47 (Karmanye Vadhikaraste - Karma Yoga)",
        "aliases": ["bhagavad gita 2.47", "karma yoga", "karmanye vadhikaraste"],
        "sanskrit": "कर्मण्येवाधिकारस्ते मा फलेषु कदाचन। मा कर्मफलहेतुर्भूर्मा ते सङ्गोऽस्त्वकर्मणि॥",
        "latin": "Karmaṇy evādhikāras te mā phaleṣu kadācana, mā karma-phala-hetur bhūr mā te saṅgo 'stv akarmaṇi.",
        "translation": "Kewajibanmu adalah bertindak dan bekerja sebaik mungkin, jangan pernah terikat atau mengeluhkan hasilnya. Jangan jadikan pahala sebagai motif kerjamu, dan jangan pula engkau terjerumus ke dalam kemalasan.",
        "hikmah": "Pelajaran ikhlas beramal dan bekerja dengan dedikasi tinggi tanpa keterikatan nafsu hasil."
    },
    {
        "religion": "Buddha",
        "category": "Tipitaka - Dhammapada",
        "title": "Dhammapada Bait 1 & 5 (Pikiran Pelopor Kebahagiaan & Cinta Menaklukkan Kebencian)",
        "aliases": ["dhammapada 1", "dhammapada 5", "pikiran adalah pelopor", "kebencian tidak berakhir dengan kebencian"],
        "pali": "Manopubbaṅgamā dhammā manosēṭṭhā manomayā... Na hi verena verāni sammantīdha kudācanaṁ, averena ca sammanti esa dhammo sanantano.",
        "translation": "Pikiran adalah pelopor dari segala sesuatu, pikiran adalah pemimpin, pikiran adalah pembentuk. Kebencian tidak akan pernah berakhir bila dibalas dengan kebencian; hanya dengan cinta kasih kebencian dapat dipadamkan. Inilah hukum abadi.",
        "hikmah": "Hukum universal mengenai kekuatan pikiran positif dan welas asih tanpa syarat."
    },
    {
        "religion": "Konghucu",
        "category": "Kitab Si Shu - Lun Yu (Sabda Suci)",
        "title": "Lun Yu: Ajaran Cinta Kasih (Ren) & Aturan Kencana (Golden Rule)",
        "aliases": ["lun yu", "sabda suci konghucu", "golden rule konghucu"],
        "chinese": "己所不欲，勿施于人 (Jǐ suǒ bù yù, wù shī yú rén)",
        "translation": "Apa yang engkau sendiri tidak inginkan orang lain perbuat terhadapmu, janganlah engkau perbuat kepada orang lain.",
        "hikmah": "Prinsip moralitas timbal balik dan empati universal yang memandu adab manusia."
    },
    {
        "religion": "Yahudi",
        "category": "Tanakh - Torah",
        "title": "Shema Yisrael (Ulangan / Devarim 6:4)",
        "aliases": ["shema yisrael", "shema", "devarim 6:4"],
        "hebrew": "שְׁמַע יִשְׂרָאֵל יְהוָה אֱלֹהֵינוּ יְהוָה אֶחָד",
        "latin": "Sh'ma Yisra'el, Adonai Eloheinu, Adonai Eḥad.",
        "translation": "Dengarlah, hai orang Israel: TUHAN itu Allah kita, TUHAN itu Esa!",
        "hikmah": "Landasan iman monoteisme dan kepatuhan penuh hati kepada Sang Pencipta."
    }
]

# ═════════════════════════════════════════════════════════════════════════════
# 4. DATABASE DOA SEHARI-HARI LENGKAP & TATA CARA IBADAH
# ═════════════════════════════════════════════════════════════════════════════

COMPREHENSIVE_PRAYERS_DATABASE: List[Dict[str, Any]] = [
    # ── ISLAM: DOA SEHARI-HARI ─────────────────────────────────────────────
    {
        "religion": "Islam",
        "category": "Doa Makan",
        "title": "Doa Sebelum Makan",
        "aliases": ["doa sebelum makan", "doa mau makan", "makan", "bacaan makan"],
        "arabic": "اللَّهُمَّ بَارِكْ لَنَا فِيمَا رَزَقْتَنَا وَقِنَا عَذَابَ النَّارِ",
        "latin": "Allāhumma bārik lanā fīmā razaqtanā wa qinā ‘ażāban-nār.",
        "translation": "Ya Allah, berkahilah rezeki yang telah Engkau berikan kepada kami dan peliharalah kami dari siksa api neraka.",
        "adab": "Membaca Basmalah, menggunakan tangan kanan, dan makan dari bagian yang terdekat."
    },
    {
        "religion": "Islam",
        "category": "Doa Makan",
        "title": "Doa Sesudah Makan",
        "aliases": ["doa sesudah makan", "doa selesai makan", "habis makan"],
        "arabic": "الْحَمْدُ لِلَّهِ الَّذِي أَطْعَمَنَا وَسَقَانَا وَجَعَلَنَا مِنَ الْمُسْلِمِينَ",
        "latin": "Al-ḥamdu lillāhillażī aṭ‘amanā wa saqānā wa ja‘alanā minal-muslimīn.",
        "translation": "Segala puji bagi Allah yang telah memberi makan dan minum kepada kami, serta menjadikan kami termasuk orang-orang yang berserah diri (muslim)."
    },
    {
        "religion": "Islam",
        "category": "Doa Tidur",
        "title": "Doa Sebelum Tidur",
        "aliases": ["doa sebelum tidur", "doa mau tidur", "bismillahika allahumma ahya"],
        "arabic": "بِاسْمِكَ اللَّهُمَّ أَحْيَا وَبِاسْمِكَ أَمُوتُ",
        "latin": "Bismika Allāhumma aḥyā wa bismika amūt.",
        "translation": "Dengan nama-Mu ya Allah aku hidup, dan dengan nama-Mu aku mati.",
        "adab": "Berwudhu terlebih dahulu, mengibaskan tempat tidur, dan menghadap ke sebelah kanan."
    },
    {
        "religion": "Islam",
        "category": "Doa Tidur",
        "title": "Doa Bangun Tidur",
        "aliases": ["doa bangun tidur", "doa setelah tidur", "alhamdulillahilladzi ahyana"],
        "arabic": "الْحَمْدُ لِلَّهِ الَّذِي أَحْيَانَا بَعْدَ مَا أَمَاتَنَا وَإِلَيْهِ النُّشُورُ",
        "latin": "Al-ḥamdu lillāhillażī aḥyānā ba‘da mā amātanā wa ilaihin-nusyūr.",
        "translation": "Segala puji bagi Allah yang telah menghidupkan kami setelah mematikan kami (tidur), dan hanya kepada-Nya kami akan dibangkitkan."
    },
    {
        "religion": "Islam",
        "category": "Doa Rumah",
        "title": "Doa Keluar Rumah",
        "aliases": ["doa keluar rumah", "bismillahi tawakkaltu", "keluar rumah"],
        "arabic": "بِسْمِ اللَّهِ تَوَكَّلْتُ عَلَى اللَّهِ لَا حَوْلَ وَلَا قُوَّةَ إِلَّا بِاللَّهِ",
        "latin": "Bismillāhi tawakkaltu ‘alallāh, lā ḥawla wa lā quwwata illā billāh.",
        "translation": "Dengan nama Allah, aku bertawakal kepada Allah. Tiada daya dan tiada kekuatan melainkan dengan pertolongan Allah semata.",
        "keutamaan": "Malaikat akan menyeru: 'Engkau telah dicukupi, dilindungi, dan diberi petunjuk', serta setan akan menjauh."
    },
    {
        "religion": "Islam",
        "category": "Doa Rumah",
        "title": "Doa Masuk Rumah",
        "aliases": ["doa masuk rumah", "masuk rumah"],
        "arabic": "بِسْمِ اللَّهِ وَلَجْنَا وَبِسْمِ اللَّهِ خَرَجْنَا وَعَلَى اللَّهِ رَبِّنَا تَوَكَّلْنَا",
        "latin": "Bismillāhi walajnā wa bismillāhi kharajnā wa ‘alallāhi rabbinā tawakkalnā.",
        "translation": "Dengan nama Allah kami masuk, dan dengan nama Allah kami keluar, dan kepada Allah Tuhan kami, kami bertawakal.",
        "adab": "Mengucapkan salam kepada keluarga di dalam rumah: 'Assalamu'alaikum warahmatullahi wabarakatuh'."
    },
    {
        "religion": "Islam",
        "category": "Doa Bersuci",
        "title": "Doa Masuk Kamar Mandi / Toilet",
        "aliases": ["doa masuk kamar mandi", "doa masuk wc", "masuk toilet", "allahumma inni audzubika minal khubutsi"],
        "arabic": "اللَّهُمَّ إِنِّي أَعُوذُ بِكَ مِنَ الْخُبُثِ وَالْخَبَائِثِ",
        "latin": "Allāhumma innī a‘ūżu bika minal-khubutsi wal-khabā’its.",
        "translation": "Ya Allah, sesungguhnya aku berlindung kepada-Mu dari godaan setan laki-laki dan setan perempuan.",
        "adab": "Melangkah dengan kaki kiri terlebih dahulu."
    },
    {
        "religion": "Islam",
        "category": "Doa Bersuci",
        "title": "Doa Keluar Kamar Mandi / Toilet",
        "aliases": ["doa keluar kamar mandi", "doa keluar wc", "keluar toilet", "ghufranaka"],
        "arabic": "غُفْرَانَكَ ، الْحَمْدُ لِلَّهِ الَّذِي أَذْهَبَ عَنِّي الأَذَى وَعَافَانِي",
        "latin": "Ghufrānaka, al-ḥamdu lillāhillażī ażhaba ‘annil-ażā wa ‘āfānī.",
        "translation": "Aku memohon ampunan-Mu. Segala puji bagi Allah yang telah menghilangkan kotoran/penyakit dariku dan menyehatkanku.",
        "adab": "Melangkah dengan kaki kanan terlebih dahulu."
    },
    {
        "religion": "Islam",
        "category": "Doa Perjalanan",
        "title": "Doa Naik Kendaraan (Darat / Laut / Udara)",
        "aliases": ["doa naik kendaraan", "doa perjalanan", "doa safar", "subhanalladzi sakhkhara lana hadza"],
        "arabic": "سُبْحَانَ الَّذِي سَخَّرَ لَنَا هَٰذَا وَمَا كُنَّا لَهُ مُقْرِنِينَ وَإِنَّا إِلَىٰ رَبِّنَا لَمُنقَلِبُونَ",
        "latin": "Subḥānallażī sakhkhara lanā hāżā wa mā kunnā lahū muqrinīn, wa innā ilā rabbinā lamunqalibūn.",
        "translation": "Mahasuci Allah yang telah menundukkan kendaraan ini untuk kami, padahal kami sebelumnya tidak mampu menguasainya. Dan sesungguhnya hanya kepada Tuhan kamilah kami akan kembali."
    },
    {
        "religion": "Islam",
        "category": "Doa Belajar",
        "title": "Doa Menuntut Ilmu & Belajar",
        "aliases": ["doa belajar", "doa menuntut ilmu", "doa mohon kepintaran", "rabbi zidni ilma"],
        "arabic": "رَبِّ زِدْنِي عِلْمًا وَارْزُقْنِي فَهْمًا وَاجْعَلْنِي مِنَ الصَّالِحِينَ",
        "latin": "Rabbī zidnī ‘ilmā, warzuqnī fahmā, waj‘alnī minaṣ-ṣāliḥīn.",
        "translation": "Ya Tuhanku, tambahkanlah kepadaku ilmu pengetahuan, dan berilah aku karunia kepahaman yang luas, serta masukkanlah aku ke dalam golongan orang-orang yang saleh."
    },
    {
        "religion": "Islam",
        "category": "Doa Keluarga",
        "title": "Doa Untuk Kedua Orang Tua",
        "aliases": ["doa orang tua", "doa ibu bapak", "rabbighfirli waliwalidayya"],
        "arabic": "رَبِّ اغْفِرْ لِي وَلِوَالِدَيَّ وَارْحَمْهُمَا كَمَا رَبَّيَانِي صَغِيرًا",
        "latin": "Rabbighfir lī wa liwālidayya warḥamhumā kamā rabbayānī ṣaghīrā.",
        "translation": "Wahai Tuhanku, ampunilah aku dan kedua orang tuaku, dan sayangilah mereka berdua sebagaimana mereka telah mengasuh dan mendidikku di waktu kecil."
    },
    {
        "religion": "Islam",
        "category": "Doa Kebaikan",
        "title": "Doa Sapu Jagat (Kebaikan Dunia & Akhirat)",
        "aliases": ["doa sapu jagat", "rabbana atina", "kebaikan dunia akhirat"],
        "arabic": "رَبَّنَا آتِنَا فِي الدُّنْيَا حَسَنَةً وَفِي الْآخِرَةِ حَسَنَةً وَقِنَا عَذَابَ النَّارِ",
        "latin": "Rabbanā ātinā fid-dunyā ḥasanataw wa fil-ākhirati ḥasanataw wa qinā ‘ażāban-nār.",
        "translation": "Ya Tuhan kami, berilah kami kebaikan di dunia dan kebaikan di akhirat, dan peliharalah kami dari siksaan api neraka."
    },
    {
        "religion": "Islam",
        "category": "Doa Tobat & Istighfar",
        "title": "Sayyidul Istighfar (Rajanya Istighfar)",
        "aliases": ["sayyidul istighfar", "raja istighfar", "doa tobat", "istighfar terbaik"],
        "arabic": "اللَّهُمَّ أَنْتَ رَبِّي لَا إِلَٰهَ إِلَّا أَنْتَ خَلَقْتَنِي وَأَنَا عَبْدُكَ وَأَنَا عَلَىٰ عَهْدِكَ وَوَعْدِكَ مَا اسْتَطَعْتُ ، أَعُوذُ بِكَ مِنْ شَرِّ مَا صَنَعْتُ ، أَبُوءُ لَكَ بِنِعْمَتِكَ عَلَيَّ وَأَبُوءُ بِذَنْبِي فَاغْفِرْ لِي فَإِنَّهُ لَا يَغْفِرُ الذُّنُوبَ إِلَّا أَنْتَ",
        "latin": "Allāhumma anta rabbī lā ilāha illā anta khalaqtanī wa anā ‘abduka wa anā ‘alā ‘ahdika wa wa‘dika mastata‘tu, a‘ūżu bika min syarri mā ṣana‘tu, abū’u laka bini‘matika ‘alayya wa abū’u biżambī fagfir lī fa’innahū lā yagfiruż-żunūba illā anta.",
        "translation": "Ya Allah, Engkaulah Tuhanku, tidak ada tuhan selain Engkau. Engkaulah yang menciptakanku dan aku adalah hamba-Mu. Aku senantiasa setia pada perjanjian dan janji-Mu semampuku. Aku berlindung kepada-Mu dari keburukan apa yang telah kuperbuat. Aku mengakui segala nikmat-Mu atasku dan aku mengakui dosaku kepada-Mu, maka ampunilah aku, sesungguhnya tidak ada yang dapat mengampuni dosa selain Engkau.",
        "keutamaan": "Siapa yang membacanya dengan yakin di pagi hari lalu meninggal sebelum sore, atau di petang hari lalu meninggal sebelum pagi, dijamin masuk surga (HR. Bukhari)."
    },
    {
        "religion": "Islam",
        "category": "Doa Kesehatan",
        "title": "Doa Memohon Kesembuhan Orang Sakit",
        "aliases": ["doa sakit", "doa kesembuhan", "doa menjenguk orang sakit", "allahumma rabbannas"],
        "arabic": "اللَّهُمَّ رَبَّ النَّاسِ أَذْهِبِ الْبَأْسَ اشْفِ أَنْتَ الشَّافِي لَا شِفَاءَ إِلَّا شِفَاؤُكَ شِفَاءً لَا يُغَادِرُ سَقَمًا",
        "latin": "Allāhumma rabbin-nās, ażhibil-ba’s, isyfi antasy-syāfī, lā syifā’a illā syifā’uka syifā’al lā yugādiru saqamā.",
        "translation": "Ya Allah, Tuhan seluruh manusia, hilangkanlah penyakit ini dan sembuhkanlah. Engkaulah Maha Penyembuh, tiada kesembuhan melainkan kesembuhan dari-Mu, kesembuhan yang tidak meninggalkan rasa sakit sedikit pun."
    },
    {
        "religion": "Islam",
        "category": "Doa Ketenangan Jiwa",
        "title": "Doa Menghilangkan Rasa Cemas, Sedih, Gelisah & Lilitan Hutang",
        "aliases": ["doa cemas", "doa gelisah", "doa sedih", "doa hutang", "minal hammi wal hazan"],
        "arabic": "اللَّهُمَّ إِنِّي أَعُوذُ بِكَ مِنَ الْهَمِّ وَالْحَزَنِ وَالْعَجْزِ وَالْكَسَلِ وَالْجُبْنِ وَالْبُخْلِ وَضَلَعِ الدَّيْنِ وَغَلَبَةِ الرِّجَالِ",
        "latin": "Allāhumma innī a‘ūżu bika minal-hammi wal-ḥazan, wal-‘ajzi wal-kasal, wal-jubni wal-bukhl, wa ḍala‘id-daini wa galabatir-rijāl.",
        "translation": "Ya Allah, aku berlindung kepada-Mu dari rasa gelisah dan kesedihan, dari kelemahan dan kemalasan, dari sifat pengecut dan kikir, serta dari lilitan hutang dan penindasan orang lain."
    },
    {
        "religion": "Islam",
        "category": "Doa Iman",
        "title": "Doa Keteguhan Hati & Iman",
        "aliases": ["doa keteguhan hati", "doa teguh iman", "ya muqallibal qulub"],
        "arabic": "يَا مُقَلِّبَ الْقُلُوبِ ثَبِّتْ قَلْبِي عَلَى دِينِكَ",
        "latin": "Yā Muqallibal-qulūb, tsabbit qalbī ‘alā dīnik.",
        "translation": "Wahai Dzat Yang Maha Membolak-balikkan hati, teguhkanlah hatiku di atas agama-Mu."
    },

    # ── KRISTEN & KATOLIK: DOA HARIAN ──────────────────────────────────────
    {
        "religion": "Kristen & Katolik",
        "category": "Doa Pokok",
        "title": "Doa Bapa Kami (Our Father / Pater Noster)",
        "aliases": ["doa bapa kami", "bapa kami", "our father", "doa tuhan yesus"],
        "text": (
            "Bapa kami yang di sorga, Dikuduskanlah nama-Mu,\n"
            "datanglah Kerajaan-Mu, jadilah kehendak-Mu di bumi seperti di sorga.\n"
            "Berikanlah kami pada hari ini makanan kami yang secukupnya\n"
            "dan ampunilah kami akan kesalahan kami, seperti kami juga mengampuni orang yang bersalah kepada kami;\n"
            "dan janganlah membawa kami ke dalam pencobaan, tetapi lepaskanlah kami dari pada yang jahat.\n"
            "[Karena Engkaulah yang empunya Kerajaan dan kuasa dan kemuliaan sampai selama-lamanya. Amin.]"
        ),
        "penjelasan": "Doa sempurna yang diajarkan langsung oleh Tuhan Yesus Kristus kepada para murid-Nya (Matius 6:9-13)."
    },
    {
        "religion": "Katolik",
        "category": "Doa Maria",
        "title": "Doa Salam Maria (Ave Maria)",
        "aliases": ["salam maria", "ave maria", "doa maria"],
        "text": (
            "Salam Maria, penuh rahmat, Tuhan sertamu;\n"
            "terpujilah engkau di antara wanita, dan terpujilah buah tubuhmu, Yesus.\n"
            "Santa Maria, bunda Allah, doakanlah kami yang berdosa ini,\n"
            "sekarang dan waktu kami mati. Amin."
        ),
        "penjelasan": "Penghormatan kepada Santa Perawan Maria, Bunda Penebus, yang bersumber dari salam Malaikat Gabriel (Lukas 1:28)."
    },
    {
        "religion": "Katolik & Kristen",
        "category": "Doa Pujian Trinitas",
        "title": "Doa Kemuliaan (Gloria Patri)",
        "aliases": ["doa kemuliaan", "kemuliaan kepada bapa", "gloria patri"],
        "text": "Kemuliaan kepada Bapa dan Putra dan Roh Kudus, seperti pada permulaan, sekarang, selalu, dan sepanjang segala abad. Amin."
    },
    {
        "religion": "Katolik",
        "category": "Doa Jam Kerahiman",
        "title": "Doa Malaikat Tuhan (Angelus)",
        "aliases": ["doa angelus", "malaikat tuhan", "doa jam 6 dan 12"],
        "text": (
            "Maria diberi kabar oleh malaikat Tuhan, bahwa ia akan mengandung dari Roh Kudus (Salam Maria...)\n"
            "Aku ini hamba Tuhan, terjadilah padaku menurut perkataan-Mu (Salam Maria...)\n"
            "Sabda sudah menjadi daging, dan tinggal di antara kita (Salam Maria...)\n"
            "Doakanlah kami ya Santa Bunda Allah, supaya kami dapat menikmati janji Kristus. Amin."
        )
    },
    {
        "religion": "Kristen & Katolik",
        "category": "Doa Harian",
        "title": "Doa Pagi Hari Kristen",
        "aliases": ["doa pagi kristen", "doa bangun pagi kristen", "doa fajar kristen"],
        "text": (
            "Bapa Surgawi yang baik, terima kasih atas nafas kehidupan dan perlindungan-Mu sepanjang malam.\n"
            "Saat fajar merekah, bimbinglah langkah kami hari ini agar perkataan, pikiran, dan perbuatan kami memuliakan nama-Mu.\n"
            "Berkatilah pekerjaan, keluarga, dan sesama yang kami jumpai. Dalam nama Tuhan Yesus kami berdoa dan bersyukur. Amin."
        )
    },
    {
        "religion": "Kristen & Katolik",
        "category": "Doa Harian",
        "title": "Doa Malam Hari Sebelum Tidur Kristen",
        "aliases": ["doa malam kristen", "doa sebelum tidur kristen"],
        "text": (
            "Tuhan Yesus yang penuh kasih, kami bersyukur atas segala penyertaan dan berkat-Mu sepanjang hari yang telah lalu.\n"
            "Ampunilah setiap dosa dan kesalahan yang kami perbuat dalam pikiran, perkataan, maupun tindakan kami.\n"
            "Kini kami menyerahkan tubuh dan jiwa kami ke dalam tangan kasih-Mu. Berikanlah kami istirahat malam yang damai dan lindungilah seisi rumah kami. Dalam nama Yesus Kristus. Amin."
        )
    },

    # ── HINDU: TRI SANDHYA & MANTRAM ───────────────────────────────────────
    {
        "religion": "Hindu",
        "category": "Puja Tri Sandhya",
        "title": "Puja Tri Sandhya (Lengkap 6 Bait)",
        "aliases": ["tri sandhya", "puja tri sandhya", "mantra tri sandhya", "trisandya"],
        "bait": [
            {
                "bait": 1,
                "sanskrit": "Om Bhur Bhuwah Swah, Tat Sawitur Warenyam, Bhargo Dewasya Dhimahi, Dhiyo Yo Nah Pracodayat.",
                "arti": "Ya Tuhan, Penguasa ketiga loka (Bhur, Bhuwah, Swah), Pencipta segala yang ada, kita memusatkan pikiran pada kecemerlangan cahaya Ilahi-Mu; semoga Engkau menerangi dan membimbing kecerdasan budi kami."
            },
            {
                "bait": 2,
                "sanskrit": "Om Narayana Ewedam Sarwam, Yad Bhutam Yac Ca Bhawyam, Niskalanko Niranjano Nirwikalpo, Nirakhyatah Suddho Dewo Eko, Narayana Na Dwitiyo 'Sti Kascit.",
                "arti": "Ya Tuhan, Narayana adalah segala yang ada di alam semesta ini, apa yang telah ada dan apa yang akan ada. Ia tanpa noda, suci tanpa cela, abadi melampaui perubahan, dan tidak ada yang kedua selain Dia."
            },
            {
                "bait": 3,
                "sanskrit": "Om Twam Siwah Twam Mahadewah, Iswarah Parameswarah, Brahma Wisnusca Rudrasca, Purusah Parikirtitah.",
                "arti": "Engkau adalah Siwa, Engkau Mahadewa, Iswara, Parameswara, Brahma, Wisnu, dan Rudra. Engkaulah Purusa Asal Segala Ciptaan."
            },
            {
                "bait": 6,
                "sanskrit": "Om Ksamaswa Mam Mahadewa, Sarwaprani Hitankara, Mam Moca Sarwa Papebhyah, Palayaswa Sadasywa. Om Santih, Santih, Santih, Om.",
                "arti": "Ampunilah hamba ya Mahadewa, Pelindung segala makhluk hidup. Bebaskanlah hamba dari segala dosa dan kelemahan. Ya Tuhan, semoga damai di hati, damai di dunia, dan damai selalu."
            }
        ]
    },

    # ── BUDDHA: VANDANA & TISARANA ─────────────────────────────────────────
    {
        "religion": "Buddha",
        "category": "Paritta Pokok",
        "title": "Penghormatan Triratna: Namakkara Gatha & Tisarana",
        "aliases": ["namo tassa", "tisarana", "penghormatan buddha", "paritta pokok"],
        "pali": (
            "Namo Tassa Bhagavato Arahato Sammāsambuddhassa (3x)\n\n"
            "Buddhaṁ saraṇaṁ gacchāmi.\n"
            "Dhammaṁ saraṇaṁ gacchāmi.\n"
            "Saṅghaṁ saraṇaṁ gacchāmi.\n\n"
            "Sabbe sattā bhavantu sukhitattā."
        ),
        "translation": (
            "Terpujilah Sang Bhagavā, Yang Mahasuci, Yang Telah Mencapai Penerangan Sempurna oleh diri-Nya sendiri (3x).\n\n"
            "Aku berlindung kepada Buddha.\n"
            "Aku berlindung kepada Dhamma (Ajaran Kebenaran).\n"
            "Aku berlindung kepada Sangha (Persaudaraan Suci para Bhikkhu).\n\n"
            "Semoga semua makhluk hidup berbahagia, terbebas dari derita, tenteram, dan damai."
        )
    }
]


# ═════════════════════════════════════════════════════════════════════════════
# 5. HANDLER MCP RESMI: LOOKUP AYAT & GUIDELINES DOA
# ═════════════════════════════════════════════════════════════════════════════

def lookup_scripture_and_verse_handler(
    religion: str = "",
    book_or_surah: str = "",
    verse_or_chapter: str = "",
    query: str = ""
) -> Dict[str, Any]:
    """
    Lookup accurate scriptures across religions:
    1. For Islam: Resolves 114 Quranic Surahs and verses (dynamic live fetch + offline fallback) + Sahih Hadiths.
    2. For Christianity & Catholicism: Holy Bible (Psalms, John, Corinthians, Proverbs, etc.).
    3. For Hinduism, Buddhism, Confucianism, Judaism: Bhagavad Gita, Dhammapada, Lun Yu, Tanakh.
    """
    search_term = (query or book_or_surah).lower().strip()
    target_rel = religion.lower().strip()

    # ── 1. CEK APAKAH INI PENCARIAN AL-QUR'AN (ISLAM) ─────────────────────────
    is_islam_context = (
        not target_rel
        or "islam" in target_rel
        or "quran" in search_term
        or "surat" in search_term
        or "surah" in search_term
        or "ayat" in search_term
    )

    if is_islam_context:
        surah_num = resolve_surah_reference(search_term, book_or_surah)
        if surah_num:
            meta = SURAH_CATALOG.get(surah_num, {})
            total_v = meta.get("ayat", 1)
            v_start, v_end = parse_verse_range(verse_or_chapter or search_term, total_v)

            # Coba ambil data lengkap online (equran.id)
            online_data = fetch_quran_surah_online(surah_num)
            if online_data and "ayat" in online_data:
                raw_ayats = online_data.get("ayat", [])
                selected_ayats = []
                for a in raw_ayats:
                    nomor = a.get("nomorAyat", 0)
                    if v_start <= nomor <= v_end:
                        selected_ayats.append({
                            "ayat_ke": nomor,
                            "teks_arab": a.get("teksArab", ""),
                            "transliterasi_latin": a.get("teksLatin", ""),
                            "terjemahan_indonesia": a.get("teksIndonesia", "")
                        })

                if selected_ayats:
                    return {
                        "success": True,
                        "agama": "Islam",
                        "sumber": "Al-Qur'anul Karim (Standar Kemenag RI)",
                        "identitas": {
                            "surat_ke": surah_num,
                            "nama_surat": meta.get("name"),
                            "nama_latin": meta.get("latin"),
                            "arti_nama": meta.get("arti"),
                            "golongan": meta.get("type"),
                            "total_ayat_surat": total_v,
                            "rentang_ayat_tampil": f"Ayat {v_start} - {v_end}"
                        },
                        "ayat_lengkap": selected_ayats,
                        "instruksi_xiaozhi": (
                            f"Bacakan ayat suci Surat {meta.get('latin')} ({meta.get('arti')}) ayat {v_start}-{v_end} "
                            "dengan tartil, fasih, dan takzim. Sebutkan lafal teks asli / transliterasinya, "
                            "bacakan terjemahan resmi bahasa Indonesia di atas, dan jelaskan hikmahnya dengan santun dan menyejukkan hati."
                        )
                    }

            # Offline Fallback untuk surat/ayat populer
            for key, off_data in OFFLINE_QURAN_VERSES.items():
                if meta.get("name", "").lower() in off_data["surah_name"].lower() or key in search_term:
                    return {
                        "success": True,
                        "agama": "Islam",
                        "sumber": "Al-Qur'anul Karim (Pustaka Kanonikal)",
                        "identitas": {
                            "surat_ke": surah_num,
                            "nama_surat": off_data["surah_name"],
                            "arti": off_data.get("arti", ""),
                            "rentang_ayat": f"Ayat {v_start} - {v_end}"
                        },
                        "ayat_lengkap": off_data["verses"],
                        "instruksi_xiaozhi": (
                            f"Bacakan ayat suci Surat {meta.get('latin')} dengan tartil dan takzim. "
                            "Sajikan lafal teks Arab / Latin dan terjemahan bahasa Indonesianya secara jelas dan menenangkan."
                        )
                    }

    # ── 2. CEK SCRIPTURES / HADITS / ALKITAB LAINNYA ─────────────────────────
    matched_scriptures = []
    for sc in CANONICAL_SCRIPTURES:
        rel_match = not target_rel or target_rel in sc["religion"].lower() or sc["religion"].lower() in target_rel
        if not rel_match:
            continue

        aliases = sc.get("aliases", [])
        title = sc.get("title", "").lower()
        if search_term:
            if any(search_term in a for a in aliases) or any(a in search_term for a in aliases) or search_term in title:
                matched_scriptures.append(sc)
        else:
            matched_scriptures.append(sc)

    if matched_scriptures:
        best = matched_scriptures[0]
        return {
            "success": True,
            "agama": best["religion"],
            "kategori": best.get("category", "Kitab Suci"),
            "judul": best["title"],
            "detail": best,
            "instruksi_xiaozhi": (
                f"Bacakan teks suci '{best['title']}' dengan penuh kesantunan dan takzim. "
                "Sampaikan lafal aslinya jika ada, terjemahannya dalam bahasa Indonesia yang menyentuh jiwa, "
                "serta terangkan hikmah kebijaksanaannya secara damai dan sejuk."
            )
        }

    # ── 3. GENERAL GUIDANCE JIKA TIDAK DITEMUKAN SPESIFIK ────────────────────
    return {
        "success": True,
        "agama": religion or "Lintas Agama",
        "pencarian": query or book_or_surah,
        "ayat_pasal": verse_or_chapter,
        "panduan_baca": {
            "1_rujukan": f"Buka kitab suci resmi untuk '{book_or_surah}' pasal/ayat '{verse_or_chapter}'.",
            "2_lafal_resmi": "Kutip teks asli / transliterasi fonetik dengan benar tanpa mengubah substansi arti.",
            "3_terjemahan": "Bacakan terjemahan resmi bahasa Indonesia yang diakui lembaga keagamaan resmi.",
            "4_hikmah": "Uraikan intisari moral dan kedamaian spiritual bagi kehidupan umat."
        },
        "instruksi_xiaozhi": (
            f"Sampaikan ayat kitab suci '{book_or_surah} {verse_or_chapter}' untuk tradisi agama '{religion or 'terkait'}'. "
            "Gunakan referensi teologis sahih yang diakui dan bacakan dengan intonasi santun serta membawa kedamaian hati."
        )
    }


def get_prayer_and_worship_guide_handler(
    religion: str = "",
    ritual_or_prayer_name: str = "",
    occasion: str = ""
) -> Dict[str, Any]:
    """
    Retrieve comprehensive daily prayers and step-by-step worship guides across all religions:
    Includes Arabic/Sanskrit/Pali/Latin texts, phonetic transliterations, Indonesian translations, and etiquette (adab).
    """
    search_term = f"{ritual_or_prayer_name} {occasion}".lower().strip()
    target_rel = religion.lower().strip()

    matched_prayers = []

    for item in COMPREHENSIVE_PRAYERS_DATABASE:
        rel_match = not target_rel or target_rel in item["religion"].lower() or item["religion"].lower() in target_rel
        if not rel_match:
            continue

        aliases = item.get("aliases", [])
        title = item.get("title", "").lower()
        cat = item.get("category", "").lower()

        if search_term:
            # 1. Direct phrase or alias substring
            direct_match = any(search_term in a for a in aliases) or any(a in search_term for a in aliases) or search_term in title or search_term in cat
            # 2. Token keywords overlap (e.g. "kedua orang tua" matches "orang tua")
            stopwords = {"doa", "baca", "bacakan", "minta", "tolong", "untuk", "dan", "yang", "dari"}
            words = [w for w in re.findall(r"\w+", search_term) if len(w) >= 3 and w not in stopwords]
            token_match = any(w in title or any(w in a for a in aliases) for w in words) if words else False

            if direct_match:
                matched_prayers.insert(0, item)  # Prioritize exact/direct match
            elif token_match:
                matched_prayers.append(item)
        else:
            matched_prayers.append(item)

    if matched_prayers:
        best_match = matched_prayers[0]
        return {
            "success": True,
            "agama": best_match["religion"],
            "kategori": best_match.get("category", "Doa Sehari-hari"),
            "nama_doa": best_match["title"],
            "doa": best_match,
            "instruksi_xiaozhi": (
                f"Bimbinglah pengguna dengan membacakan doa '{best_match['title']}' secara jelas, khusyuk, dan tenang. "
                "Bacakan teks asli dan lafal latinnya, sertakan terjemahan bahasa Indonesianya, "
                "serta jelaskan adab atau keutamaannya agar pengguna dapat mengamalkannya dengan mantap."
            )
        }

    # Fallback jika nama doa khusus belum masuk daftar eksak
    return {
        "success": True,
        "agama": religion or "Umum / Lintas Agama",
        "perihal": ritual_or_prayer_name,
        "momen": occasion,
        "sistematika_doa": {
            "1_niat_dan_kebersihan": "Awali dengan niat yang ikhlas, kebersihan diri dan pakaian, serta menghadapkan hati kepada Tuhan Yang Maha Esa.",
            "2_bacaan_lafal": f"Sajikan teks lafal doa '{ritual_or_prayer_name}' yang sahih dan makruf dalam tradisi agama terkait.",
            "3_terjemahan": "Sertakan terjemahan bahasa Indonesia yang jelas dan mudah dipahami maknanya.",
            "4_doa_penutup": "Tutup dengan harapan ampunan, kesehatan, ketenangan jiwa, dan keselamatan bagi keluarga."
        },
        "instruksi_xiaozhi": (
            f"Bacakan doa '{ritual_or_prayer_name}' dalam tradisi keagamaan '{religion or 'terkait'}' "
            "dengan lafal yang sahih, intonasi tenang, penuh penghormatan, dan terjemahan bahasa Indonesia yang menyejukkan hati."
        )
    }
