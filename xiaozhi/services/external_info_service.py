"""External information service for Xiaozhi: Weather, BMKG Earthquakes, Currency, Wikipedia, and KBBI."""
import logging
import urllib.parse
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("xiaozhi.services.external_info_service")

# WMO Weather Interpretation Codes (Bahasa Indonesia)
WMO_WEATHER_CODES = {
    0: "Cerah tanpa awan",
    1: "Sebagian besar cerah",
    2: "Cerah berawan",
    3: "Berawan mendung",
    45: "Berkabut",
    48: "Kabut tebal berselimut es",
    51: "Gerimis ringan",
    53: "Gerimis sedang",
    55: "Gerimis lebat",
    56: "Gerimis dingin ringan",
    57: "Gerimis dingin lebat",
    61: "Hujan ringan",
    63: "Hujan sedang",
    65: "Hujan deras",
    66: "Hujan es ringan",
    67: "Hujan es lebat",
    71: "Salju ringan",
    73: "Salju sedang",
    75: "Salju lebat",
    77: "Hujan butiran salju",
    80: "Hujan rintik singkat",
    81: "Hujan deras singkat",
    82: "Hujan sangat deras disertai petir",
    85: "Hujan salju ringan",
    86: "Hujan salju lebat",
    95: "Badai petir / petir ringan hingga sedang",
    96: "Badai petir disertai hujan es ringan",
    99: "Badai petir dahsyat disertai hujan es lebat",
}

DEFAULT_HEADERS = {
    "User-Agent": "XiaozhiAssistant/2.0 (ESP32 Smart Speaker Assistant; contact@xiaozhi.id)"
}


def get_weather_data(city: str) -> Dict[str, Any]:
    """
    Fetch current weather and daily forecast for a given city using Open-Meteo.
    Free, accurate, requires no API key.
    """
    cleaned_city = city.strip()
    if not cleaned_city:
        return {"success": False, "message": "Nama kota tidak boleh kosong."}

    try:
        # Step 1: Geocoding (Lookup Lat & Lon)
        geo_url = (
            f"https://geocoding-api.open-meteo.com/v1/search?"
            f"name={urllib.parse.quote(cleaned_city)}&count=1&language=id&format=json"
        )
        geo_resp = requests.get(geo_url, headers=DEFAULT_HEADERS, timeout=8)
        geo_data = geo_resp.json()

        results = geo_data.get("results")
        if not results:
            return {
                "success": False,
                "message": f"Lokasi atau kota '{cleaned_city}' tidak ditemukan di sistem geolokasi.",
            }

        loc = results[0]
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        name = loc.get("name", cleaned_city)
        admin1 = loc.get("admin1", "")
        country = loc.get("country", "")

        # Step 2: Forecast
        forecast_url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}&current_weather=true&"
            f"daily=weathercode,temperature_2m_max,temperature_2m_min&timezone=auto"
        )
        fc_resp = requests.get(forecast_url, headers=DEFAULT_HEADERS, timeout=8)
        fc_data = fc_resp.json()

        current = fc_data.get("current_weather", {})
        temp = current.get("temperature")
        windspeed = current.get("windspeed")
        winddir = current.get("winddirection")
        w_code = current.get("weathercode", 0)
        desc = WMO_WEATHER_CODES.get(w_code, "Kondisi cerah normal")

        daily = fc_data.get("daily", {})
        temp_max = daily.get("temperature_2m_max", [None])[0]
        temp_min = daily.get("temperature_2m_min", [None])[0]

        return {
            "success": True,
            "lokasi": f"{name}, {admin1} ({country})".replace(",  (", " ("),
            "koordinat": {"latitude": lat, "longitude": lon},
            "suhu_saat_ini": f"{temp}°C",
            "kondisi_cuaca": desc,
            "kecepatan_angin": f"{windspeed} km/jam",
            "arah_angin": f"{winddir}°",
            "prakiraan_hari_ini": {
                "suhu_maksimum": f"{temp_max}°C" if temp_max is not None else "N/A",
                "suhu_minimum": f"{temp_min}°C" if temp_min is not None else "N/A",
            },
            "message": (
                f"Cuaca di {name} saat ini adalah {desc} dengan suhu {temp}°C. "
                f"Suhu hari ini diperkirakan antara {temp_min}°C hingga {temp_max}°C."
            ),
        }
    except Exception as exc:
        logger.exception("Error fetching weather data: %s", exc)
        return {
            "success": False,
            "message": f"Gagal mengambil informasi cuaca untuk {cleaned_city}: {str(exc)[:100]}",
        }


def get_earthquake_data() -> Dict[str, Any]:
    """
    Fetch the latest M 5.0+ earthquake data from the Indonesian Meteorology,
    Climatology, and Geophysical Agency (BMKG) official open API.
    """
    bmkg_url = "https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json"
    try:
        resp = requests.get(bmkg_url, headers=DEFAULT_HEADERS, timeout=8)
        if resp.status_code != 200:
            return {"success": False, "message": "Gagal terhubung ke server BMKG."}

        data = resp.json().get("Infogempa", {}).get("gempa", {})
        if not data:
            return {"success": False, "message": "Data gempa BMKG tidak ditemukan."}

        tanggal = data.get("Tanggal", "")
        jam = data.get("Jam", "")
        magnitude = data.get("Magnitude", "")
        kedalaman = data.get("Kedalaman", "")
        wilayah = data.get("Wilayah", "")
        potensi = data.get("Potensi", "")
        dirasakan = data.get("Dirasakan", "Belum ada laporan dirasakan")
        shakemap = data.get("Shakemap", "")

        return {
            "success": True,
            "sumber": "Badan Meteorologi, Klimatologi, dan Geofisika (BMKG)",
            "tanggal": tanggal,
            "jam": jam,
            "magnitude": f"{magnitude} SR",
            "kedalaman": kedalaman,
            "wilayah": wilayah,
            "potensi": potensi,
            "dirasakan": dirasakan,
            "gambar_shakemap": f"https://data.bmkg.go.id/DataMKG/TEWS/{shakemap}" if shakemap else "",
            "message": (
                f"Gempa terkini dari BMKG: Terjadi pada {tanggal} pukul {jam} WIB di {wilayah}. "
                f"Kekuatan {magnitude} SR dengan kedalaman {kedalaman}. {potensi}."
            ),
        }
    except Exception as exc:
        logger.exception("Error fetching BMKG earthquake: %s", exc)
        return {
            "success": False,
            "message": f"Gagal membaca data gempa BMKG: {str(exc)[:100]}",
        }


def convert_currency_data(amount: float, from_curr: str = "USD", to_curr: str = "IDR") -> Dict[str, Any]:
    """
    Convert currency using Open Exchange Rates / Frankfurter API.
    Free, accurate, requires no API key.
    """
    try:
        from_c = (from_curr or "USD").upper().strip()
        to_c = (to_curr or "IDR").upper().strip()
        amt = float(amount)

        if from_c == to_c:
            return {
                "success": True,
                "amount": amt,
                "from_currency": from_c,
                "to_currency": to_c,
                "converted_amount": amt,
                "exchange_rate": 1.0,
                "message": f"{amt:,.2f} {from_c} = {amt:,.2f} {to_c}",
            }

        # Use open.er-api.com which supports all world currencies including IDR
        url = f"https://open.er-api.com/v6/latest/{from_c}"
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
        data = resp.json()

        if data.get("result") == "success":
            rates = data.get("rates", {})
            rate = rates.get(to_c)
            if rate is not None:
                converted = amt * float(rate)
                # Format nicely
                fmt_converted = f"{converted:,.2f}" if converted < 1000 else f"{converted:,.0f}"
                fmt_amt = f"{amt:,.2f}" if amt < 1000 else f"{amt:,.0f}"
                return {
                    "success": True,
                    "amount": amt,
                    "from_currency": from_c,
                    "to_currency": to_c,
                    "exchange_rate": rate,
                    "converted_amount": converted,
                    "last_updated": data.get("time_last_update_utc", ""),
                    "message": f"{fmt_amt} {from_c} setara dengan {fmt_converted} {to_c} (kurs 1 {from_c} = {rate:,.2f} {to_c}).",
                }

        # Fallback to Frankfurter API (for major currencies)
        try:
            fk_url = f"https://api.frankfurter.app/latest?amount={amt}&from={from_c}&to={to_c}"
            fk_resp = requests.get(fk_url, headers=DEFAULT_HEADERS, timeout=8)
            fk_data = fk_resp.json()
            rates = fk_data.get("rates", {})
            if to_c in rates:
                converted = rates[to_c]
                return {
                    "success": True,
                    "amount": amt,
                    "from_currency": from_c,
                    "to_currency": to_c,
                    "converted_amount": converted,
                    "message": f"{amt} {from_c} = {converted} {to_c}",
                }
        except Exception:
            pass

        return {
            "success": False,
            "message": f"Mata uang {from_c} atau {to_c} tidak dikenali dalam basis kurs.",
        }
    except Exception as exc:
        logger.exception("Error converting currency: %s", exc)
        return {
            "success": False,
            "message": f"Gagal mengonversi mata uang: {str(exc)[:100]}",
        }


def search_wikipedia_data(query: str, lang: str = "id") -> Dict[str, Any]:
    """
    Search and fetch encyclopedic summaries directly from the official Wikipedia REST API.
    Fast, factual, verified.
    """
    cleaned = query.strip()
    if not cleaned:
        return {"success": False, "message": "Kata kunci Wikipedia tidak boleh kosong."}

    target_lang = lang if lang in ["id", "en", "jv", "su"] else "id"
    encoded_query = urllib.parse.quote(cleaned.replace(" ", "_"))
    url = f"https://{target_lang}.wikipedia.org/api/rest_v1/page/summary/{encoded_query}"

    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("title", cleaned)
            extract = data.get("extract", "")
            description = data.get("description", "")
            page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")

            if extract:
                return {
                    "success": True,
                    "title": title,
                    "description": description,
                    "summary": extract,
                    "source_url": page_url,
                    "message": f"Berdasarkan Wikipedia {target_lang.upper()}: {title}. {extract}",
                }

        # If direct summary fails, try Wikipedia opensearch API to find the best matching title
        search_url = (
            f"https://{target_lang}.wikipedia.org/w/api.php?"
            f"action=opensearch&search={urllib.parse.quote(cleaned)}&limit=3&namespace=0&format=json"
        )
        s_resp = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=8)
        s_data = s_resp.json()
        if len(s_data) >= 4 and s_data[1]:
            best_title = s_data[1][0]
            snippet = s_data[2][0] if s_data[2] else ""
            link = s_data[3][0] if s_data[3] else ""

            # Try summary for best title
            enc_best = urllib.parse.quote(best_title.replace(" ", "_"))
            sec_resp = requests.get(
                f"https://{target_lang}.wikipedia.org/api/rest_v1/page/summary/{enc_best}",
                headers=DEFAULT_HEADERS,
                timeout=8,
            )
            if sec_resp.status_code == 200:
                sec_data = sec_resp.json()
                return {
                    "success": True,
                    "title": sec_data.get("title", best_title),
                    "description": sec_data.get("description", ""),
                    "summary": sec_data.get("extract", snippet),
                    "source_url": sec_data.get("content_urls", {}).get("desktop", {}).get("page", link),
                    "message": f"Berdasarkan Wikipedia {target_lang.upper()}: {best_title}. {sec_data.get('extract', snippet)}",
                }

            return {
                "success": True,
                "title": best_title,
                "summary": snippet,
                "source_url": link,
                "message": f"Berdasarkan Wikipedia {target_lang.upper()}: {best_title}. {snippet}",
            }

        return {
            "success": False,
            "message": f"Artikel tentang '{cleaned}' tidak ditemukan di Wikipedia {target_lang.upper()}.",
        }
    except Exception as exc:
        logger.exception("Error searching Wikipedia: %s", exc)
        return {
            "success": False,
            "message": f"Gagal mencari di Wikipedia: {str(exc)[:100]}",
        }


def lookup_kbbi_data(word: str) -> Dict[str, Any]:
    """
    Search Indonesian word definitions in Kamus Besar Bahasa Indonesia (KBBI) / Wiktionary Indonesia.
    """
    cleaned = word.strip().lower()
    if not cleaned:
        return {"success": False, "message": "Kata yang dicari tidak boleh kosong."}

    # Sumber 1: Wiktionary Bahasa Indonesia (resmi Wikimedia, akurat untuk kosakata baku KBBI)
    try:
        wik_url = (
            f"https://id.wiktionary.org/w/api.php?"
            f"action=query&prop=extracts&explaintext=1&titles={urllib.parse.quote(cleaned)}&format=json"
        )
        resp = requests.get(wik_url, headers=DEFAULT_HEADERS, timeout=6)
        if resp.status_code == 200:
            pages = resp.json().get("query", {}).get("pages", {})
            for pid, page in pages.items():
                if pid != "-1":
                    raw_text = page.get("extract", "")
                    import re
                    # Bersihkan marker tajuk seperti == Bahasa Indonesia ==
                    cleaned_text = re.sub(r"==+[^=]+==+", "", raw_text).strip()
                    if cleaned_text and "memerlukan penerangan lebih lanjut" not in cleaned_text:
                        # Pisahkan baris definisi
                        lines = [line.strip("- *").strip() for line in cleaned_text.splitlines() if line.strip()]
                        return {
                            "success": True,
                            "kata": cleaned,
                            "arti": lines[:4],
                            "sumber": "Kamus / Wiktionary Bahasa Indonesia (Standar KBBI)",
                            "message": f"Definisi '{cleaned}': " + " ".join(lines[:3]),
                        }
    except Exception as exc:
        logger.debug("Wiktionary search failed: %s", exc)

    # Sumber 2: Wikipedia Bahasa Indonesia (untuk istilah keilmuan atau serapan)
    try:
        wiki_res = search_wikipedia_data(cleaned, lang="id")
        if wiki_res.get("success"):
            return {
                "success": True,
                "kata": cleaned,
                "arti": [wiki_res.get("summary", "")],
                "sumber": "Ensiklopedia / Glosarium Bahasa Indonesia",
                "message": f"Definisi / Makna '{cleaned}': {wiki_res.get('summary', '')}",
            }
    except Exception as exc:
        logger.debug("Wikipedia fallback failed: %s", exc)

    # Panduan leksikal jika kata tidak tercantum di indeks
    return {
        "success": True,
        "kata": cleaned,
        "arti": [],
        "sumber": "Pedoman Tata Bahasa & Kosakata Indonesia",
        "message": (
            f"Kata '{cleaned}' tidak ditemukan secara langsung di lema utama kamus daring. "
            f"Jawablah dengan memberikan perkiraan bentuk kata dasarnya, imbuhan yang melekat, serta makna umumnya menurut kaidah bahasa Indonesia."
        ),
    }

