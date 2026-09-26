import logging
import os
import re
import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional
import requests
from bs4 import BeautifulSoup

from xiaozhi.core.utils import clean_text
from xiaozhi.mcp.context import mcp_active_owner_ctx
from xiaozhi.services.smarthome_service import (
    parse_smart_home_action,
    resolve_smart_home_target,
    set_all_smart_home_relays,
    set_smart_home_relay,
    smart_home_room_name,
    smart_home_status,
)

logger = logging.getLogger("xiaozhi.mcp.tools")


def format_material_for_xiaozhi(item: dict, keyword: str = "") -> dict:
    """Format material for XiaoZhi consumption."""
    return {
        "id": item.get("id"),
        "title": item.get("title", ""),
        "category": item.get("category", ""),
        "keywords": item.get("keywords", ""),
        "content": item.get("content", ""),
        "source_type": item.get("source_type", ""),
    }



def _scrape_page_content(url: str, max_chars: int = 1500) -> str:
    """Scrape clean textual content from a webpage."""
    if not url or not url.startswith("http"):
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup.body
        if not main:
            return ""
        text = main.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception:
        return ""


def _deep_web_search(query: str, max_results: int = 5, read_content: bool = True) -> dict:
    """Multi-source deep web search: Wikipedia + Google News + Web Scraper."""
    results = []
    
    # 1. Wikipedia Search & Full Intro Extract
    try:
        w_url = "https://id.wikipedia.org/w/api.php"
        w_params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "utf8": 1,
            "srlimit": 2
        }
        headers = {"User-Agent": "XiaozhiDeepSearch/1.0"}
        wr = requests.get(w_url, params=w_params, headers=headers, timeout=4)
        if wr.status_code == 200:
            w_data = wr.json()
            for hit in w_data.get("query", {}).get("search", [])[:1]:
                pageid = hit["pageid"]
                ex_params = {
                    "action": "query",
                    "prop": "extracts",
                    "pageids": pageid,
                    "explaintext": 1,
                    "exintro": 1,
                    "format": "json"
                }
                er = requests.get(w_url, params=ex_params, headers=headers, timeout=4)
                if er.status_code == 200:
                    pages = er.json().get("query", {}).get("pages", {})
                    extract = pages.get(str(pageid), {}).get("extract", "")
                    if extract:
                        results.append({
                            "title": hit["title"],
                            "url": f"https://id.wikipedia.org/?curid={pageid}",
                            "snippet": extract[:1000],
                            "source": "Wikipedia Ensiklopedia",
                            "is_deep_content": True
                        })
    except Exception:
        pass

    # 2. Google News & Web RSS
    try:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=id&gl=ID&ceid=ID:id"
        resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            items = re.findall(r'<item>(.*?)</item>', resp.text, re.DOTALL)
            for item in items[:max_results]:
                t = re.search(r'<title>(.*?)</title>', item)
                l = re.search(r'<link>(.*?)</link>', item)
                d = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
                src = re.search(r'<source.*?>(.*?)</source>', item)
                if t:
                    raw_link = l.group(1) if l else ""
                    raw_desc = re.sub(r'<[^>]+>', '', d.group(1)) if d else ""
                    clean_desc = re.sub(r'&[a-zA-Z]+;', ' ', raw_desc).strip()
                    
                    item_data = {
                        "title": t.group(1),
                        "url": raw_link,
                        "snippet": clean_desc[:300],
                        "source": src.group(1) if src else "Web / Berita",
                        "is_deep_content": False
                    }
                    results.append(item_data)
    except Exception:
        pass

    # 3. Deep Page Reader: For top non-wiki result, attempt to scrape deeper text if requested
    if read_content:
        for r in results:
            if r.get("source") != "Wikipedia Ensiklopedia" and r.get("url"):
                deep_text = _scrape_page_content(r["url"], max_chars=1500)
                if deep_text and len(deep_text) > len(r.get("snippet", "")):
                    r["snippet"] = deep_text
                    r["is_deep_content"] = True
                    break

    return {
        "success": bool(results),
        "query": query,
        "message": f"Ditemukan {len(results)} sumber informasi mendalam di internet.",
        "results": results[:max_results]
    }


def _search_social_media(query: str, platform: str = "all", max_results: int = 5) -> dict:
    """Search social media discussions, opinions, and sentiments."""
    platform_map = {
        "reddit": [("Reddit", f"site:reddit.com {query}")],
        "twitter": [("X / Twitter", f"site:x.com OR site:twitter.com {query}")],
        "x": [("X / Twitter", f"site:x.com OR site:twitter.com {query}")],
        "youtube": [("YouTube", f"site:youtube.com {query}")],
        "tiktok": [("TikTok", f"site:tiktok.com {query}")],
        "all": [
            ("Reddit", f"site:reddit.com {query}"),
            ("X / Twitter", f"site:x.com OR site:twitter.com {query}"),
            ("YouTube", f"site:youtube.com {query}")
        ]
    }
    
    target_queries = platform_map.get(platform.lower(), platform_map["all"])
    results = []

    for plat_name, target_q in target_queries:
        try:
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(target_q)}&hl=id&gl=ID&ceid=ID:id"
            resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                items = re.findall(r'<item>(.*?)</item>', resp.text, re.DOTALL)
                for item in items[:2]:
                    t = re.search(r'<title>(.*?)</title>', item)
                    l = re.search(r'<link>(.*?)</link>', item)
                    d = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
                    if t:
                        raw_desc = re.sub(r'<[^>]+>', '', d.group(1)) if d else ""
                        clean_desc = re.sub(r'&[a-zA-Z]+;', ' ', raw_desc).strip()
                        results.append({
                            "platform": plat_name,
                            "title": t.group(1),
                            "url": l.group(1) if l else "",
                            "discussion_snippet": clean_desc[:300]
                        })
        except Exception:
            pass

    return {
        "success": bool(results),
        "platform": platform,
        "query": query,
        "message": f"Ditemukan {len(results)} opini & diskusi dari media sosial terkait '{query}'.",
        "results": results[:max_results]
    }


def _osint_recon(target: str, target_type: str = "auto") -> dict:
    """Passive OSINT reconnaissance for IP, Domain, or Username."""
    target = target.strip()
    is_ip = bool(re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', target))
    is_domain = bool(re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', target)) and not is_ip

    if target_type == "ip" or (target_type == "auto" and is_ip):
        # IP Intelligence
        data = {}
        try:
            r = requests.get(f"http://ip-api.com/json/{target}?fields=status,message,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,as,query", timeout=5)
            if r.status_code == 200:
                data = r.json()
        except Exception as e:
            data["error"] = str(e)

        try:
            hostname, _, _ = socket.gethostbyaddr(target)
            data["reverse_dns"] = hostname
        except Exception:
            data["reverse_dns"] = "Tidak ada PTR record"

        return {
            "success": True,
            "type": "IP Address Intelligence",
            "target": target,
            "ip_intel": data
        }

    elif target_type == "domain" or (target_type == "auto" and is_domain):
        # Domain Intelligence
        data = {}
        try:
            ips = socket.getaddrinfo(target, 80)
            data["resolved_ips"] = list(set([item[4][0] for item in ips]))
        except Exception as e:
            data["resolved_ips"] = [f"Gagal resolve DNS: {str(e)}"]

        # Check Web Headers & Tech Stack
        try:
            resp = requests.get(f"https://{target}", timeout=4, headers={"User-Agent": "Mozilla/5.0"})
            h = resp.headers
            data["tech_headers"] = {
                "server": h.get("Server", "Unknown"),
                "status_code": resp.status_code,
                "x_powered_by": h.get("X-Powered-By", "Hidden"),
                "hsts": "Strict-Transport-Security" in h,
                "waf_or_cdn": "Cloudflare" if "cf-ray" in h or "cloudflare" in h.get("Server", "").lower() else "Standard"
            }
        except Exception as e:
            data["tech_headers"] = {"error": str(e)}

        # Certificate Transparency Subdomain Lookup
        try:
            crt_url = f"https://crt.sh/?q=%.{target}&output=json"
            cr = requests.get(crt_url, timeout=4)
            if cr.status_code == 200:
                entries = cr.json()
                subdomains = list(set([item.get("name_value", "") for item in entries if item.get("name_value")]))
                clean_subs = [s for s in subdomains if "\n" not in s][:8]
                data["discovered_subdomains"] = clean_subs
        except Exception:
            data["discovered_subdomains"] = []

        return {
            "success": True,
            "type": "Domain Reconnaissance",
            "target": target,
            "domain_recon": data
        }

    else:
        # Username Intelligence across platforms
        username = target.lstrip("@")
        profiles_to_check = {
            "GitHub": f"https://api.github.com/users/{username}",
            "Reddit": f"https://www.reddit.com/user/{username}/about.json",
            "Telegram": f"https://t.me/{username}",
            "Pinterest": f"https://www.pinterest.com/{username}/",
            "Dev.to": f"https://dev.to/{username}",
            "GitLab": f"https://gitlab.com/{username}",
            "Medium": f"https://medium.com/@{username}"
        }

        found_profiles = []
        def check_profile(site, url):
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                r = requests.get(url, headers=headers, timeout=3)
                if r.status_code == 200:
                    public_url = url.replace("/about.json", "").replace("api.github.com/users", "github.com")
                    found_profiles.append({"platform": site, "url": public_url})
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=5) as ex:
            for site, url in profiles_to_check.items():
                ex.submit(check_profile, site, url)

        return {
            "success": True,
            "type": "Username Digital Footprint (OSINT)",
            "username": username,
            "profiles_found_count": len(found_profiles),
            "profiles": found_profiles
        }


def register_tools(mcp_server, store, record_mcp_tool_history, youtube_search_fn=None):
    """Register all MCP tools with the server."""

    @mcp_server.tool()
    def search_course_materials(search_keyword: str) -> dict:
        """
        Cari materi perkuliahan, tugas, jadwal, pengumuman, catatan dosen, dan data API realtime.
        Gunakan tool ini untuk pertanyaan umum. Untuk pertanyaan API realtime seperti cuaca, suhu,
        kelembapan, angin, atau data sensor, keyword Indonesia akan dicocokkan ke field JSON API.
        Jawaban harus hanya memakai data yang ditemukan dari knowledge base Xiaozhi Indonesia.
        """
        try:
            owner_id = mcp_active_owner_ctx.get()
            if owner_id is None:
                return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "results": []}
            data = store.search_materials(owner_id, search_keyword, limit=10)
            if not data:
                response = {
                    "success": False,
                    "message": "Data atau materi tersebut tidak ditemukan di sistem Xiaozhi Indonesia.",
                    "results": [],
                }
                record_mcp_tool_history(owner_id, "search_course_materials", search_keyword, {"search_keyword": search_keyword}, response)
                return response
            formatted_results = [format_material_for_xiaozhi(item, search_keyword) for item in data]
            response = {
                "success": True,
                "message": (
                    "Data berhasil ditemukan. Jawab secara akurat hanya berdasarkan Konteks Paling Relevan "
                    "dan Isi Data Lengkap. Untuk raw JSON API, ambil field yang paling tepat dengan pertanyaan."
                ),
                "results": formatted_results,
            }
            record_mcp_tool_history(owner_id, "search_course_materials", search_keyword, {"search_keyword": search_keyword}, response)
            return response
        except Exception:
            logger.exception("Error MCP search")
            return {"success": False, "message": "Terjadi kesalahan saat mencari data.", "results": []}

    @mcp_server.tool()
    def read_live_api_data(search_keyword: str = "") -> dict:
        """
        Baca data API realtime terbaru dari kategori "data dari api".
        Gunakan tool ini saat user bertanya tentang data API, cuaca, suhu, kelembapan, angin,
        sensor, status realtime, atau meminta jawaban yang harus berasal dari endpoint API.
        Jawaban harus hanya berdasarkan raw JSON API yang dikembalikan tool ini.
        """
        try:
            owner_id = mcp_active_owner_ctx.get()
            if owner_id is None:
                return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "results": []}
            data = store.list_live_api_materials(owner_id, search_keyword, limit=5)
            if not data:
                response = {
                    "success": False,
                    "message": "Belum ada materi kategori data dari api yang tersimpan.",
                    "results": [],
                }
                record_mcp_tool_history(owner_id, "read_live_api_data", search_keyword, {"search_keyword": search_keyword}, response)
                return response
            formatted_results = [format_material_for_xiaozhi(item, search_keyword) for item in data]
            response = {
                "success": True,
                "message": (
                    "Data API realtime berhasil dibaca. Jawab hanya dari raw JSON API terbaru. "
                    "Pilih field yang paling relevan dengan pertanyaan pengguna."
                ),
                "results": formatted_results,
            }
            record_mcp_tool_history(owner_id, "read_live_api_data", search_keyword, {"search_keyword": search_keyword}, response)
            return response
        except Exception:
            logger.exception("Error MCP live API read")
            return {"success": False, "message": "Terjadi kesalahan saat membaca data API realtime.", "results": []}

    @mcp_server.tool()
    def read_material_database(search_keyword: str = "", category: str = "", limit: int = 10) -> dict:
        """
        Baca database materi EduSmart secara lengkap/detail sesuai pertanyaan.
        Gunakan tool ini saat user bertanya tentang isi materi, database materi, daftar materi,
        atau meminta jawaban dari catatan/tugas/pengumuman/jadwal/materi yang tersimpan.

        Args:
            search_keyword: Kata kunci pertanyaan user. Kosongkan untuk membaca materi terbaru.
            category: Filter kategori opsional, misalnya "Materi Perkuliahan", "Catatan Dosen", atau "data dari api".
            limit: Jumlah materi maksimal yang dibaca, 1-25.
        """
        try:
            owner_id = mcp_active_owner_ctx.get()
            if owner_id is None:
                return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "results": []}
            data = store.list_material_database(owner_id, search_keyword, category, limit)
            if not data:
                response = {
                    "success": False,
                    "message": "Tidak ada materi yang cocok di database Xiaozhi Indonesia.",
                    "results": [],
                }
                record_mcp_tool_history(owner_id, "read_material_database", search_keyword, {"search_keyword": search_keyword, "category": category, "limit": limit}, response)
                return response
            formatted_results = [format_material_for_xiaozhi(item, search_keyword) for item in data]
            response = {
                "success": True,
                "message": (
                    "Database materi berhasil dibaca. Jawab hanya berdasarkan materi yang dikembalikan. "
                    "Gunakan ID Materi untuk membaca satu materi lebih detail bila perlu."
                ),
                "results": formatted_results,
            }
            record_mcp_tool_history(owner_id, "read_material_database", search_keyword, {"search_keyword": search_keyword, "category": category, "limit": limit}, response)
            return response
        except Exception:
            logger.exception("Error MCP material database read")
            return {"success": False, "message": "Terjadi kesalahan saat membaca database materi.", "results": []}

    @mcp_server.tool()
    def read_material_detail(material_id: int = 0, title: str = "", search_keyword: str = "") -> dict:
        """
        Baca satu materi EduSmart secara lengkap berdasarkan ID materi, judul, atau kata kunci.
        Gunakan tool ini setelah search_course_materials/read_material_database menemukan kandidat,
        atau saat user meminta detail isi satu materi tertentu.
        """
        try:
            owner_id = mcp_active_owner_ctx.get()
            if owner_id is None:
                return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "result": None}
            item = store.find_material_detail(owner_id, material_id, title, search_keyword)
            if not item:
                response = {
                    "success": False,
                    "message": "Materi detail tidak ditemukan di database Xiaozhi Indonesia.",
                    "result": None,
                }
                record_mcp_tool_history(owner_id, "read_material_detail", search_keyword or title or str(material_id), {"material_id": material_id, "title": title, "search_keyword": search_keyword}, response)
                return response
            query = search_keyword or title or item.get("title", "")
            response = {
                "success": True,
                "message": (
                    "Detail materi berhasil dibaca. Jawab hanya berdasarkan Isi Data Lengkap materi ini "
                    "dan jangan menambahkan informasi di luar database."
                ),
                "result": format_material_for_xiaozhi(item, query),
            }
            record_mcp_tool_history(owner_id, "read_material_detail", query, {"material_id": material_id, "title": title, "search_keyword": search_keyword}, response)
            return response
        except Exception:
            logger.exception("Error MCP material detail read")
            return {"success": False, "message": "Terjadi kesalahan saat membaca detail materi.", "result": None}

    @mcp_server.tool()
    def save_chat_history(user_message: str, xiaozhi_answer: str) -> dict:
        """
        Simpan transcript chat lengkap antara user dan Xiaozhi ke halaman Riwayat Chat.
        Gunakan tool ini setelah Xiaozhi menjawab user, terutama jika percakapan tidak memakai tool lain.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        response = {"success": True, "message": "Riwayat chat berhasil disimpan."}
        record_mcp_tool_history(owner_id, "save_chat_history", user_message, {"user_message": user_message}, response, xiaozhi_answer=xiaozhi_answer, source="chat_transcript")
        return response

    @mcp_server.tool()
    def recall_chat_memory(query: str = "", limit: int = 5) -> dict:
        """
        Ingat kembali riwayat percakapan sebelumnya antara user dan Xiaozhi dari memori jangka panjang (Long-Term Semantic Memory).
        Panggil tool ini ketika user:
        - Bertanya tentang obrolan yang lalu ("tadi kita bahas apa", "kemarin saya nanya apa", "ingat nggak...").
        - Mengungkit topik/konsep secara semantik (contoh: user tanya "masalah keuanganku" padahal sebelumnya cuma cerita "biaya skripsi dan uang kos").
        - Meminta melanjutkan pembahasan sebelumnya ("lanjutkan topik kita tadi").

        Args:
            query: Kata kunci topik atau konsep yang ingin dicari (contoh: "keuangan", "tugas fisika", "resep", "koding"). Kosongkan untuk membaca percakapan paling terkini.
            limit: Jumlah riwayat percakapan terakhir yang ingin diambil (default: 5, maksimal: 10).
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "riwayat_percakapan": []}

        limit_val = max(1, min(int(limit or 5), 10))
        query_clean = str(query or "").strip()

        try:
            # Gunakan mode semantik cerdas jika user mencari berdasarkan topik/makna
            use_semantic = bool(query_clean)
            records = store.list_chat_history(owner_id, query=query_clean, limit=limit_val, semantic=use_semantic)
            parsed_history = []
            for r in records:
                user_msg = str(r.get("user_message") or "").strip()
                ai_ans = str(r.get("xiaozhi_answer") or "").strip()
                tool_name = str(r.get("tool_name") or "").strip()
                req_p = r.get("request_payload")
                res_p = r.get("response_payload")
                if not user_msg and not ai_ans and not res_p:
                    continue
                item_dict = {
                    "waktu": str(r.get("created_at") or ""),
                    "pesan_user": user_msg or f"(Pemanggilan {tool_name})" if tool_name else "-",
                    "jawaban_xiaozhi": ai_ans[:800] + ("..." if len(ai_ans) > 800 else ""),
                    "tool_dipakai": tool_name
                }
                # Attach Raw JSON Response & parameters so RAG tool fully understands past context
                if res_p:
                    try:
                        rp = res_p if isinstance(res_p, dict) else json.loads(res_p)
                        item_dict["raw_response"] = rp
                    except Exception:
                        item_dict["raw_response"] = str(res_p)[:400]
                if req_p:
                    try:
                        rq = req_p if isinstance(req_p, dict) else json.loads(req_p)
                        item_dict["parameter_tool"] = rq
                    except Exception:
                        pass
                if "similarity_score" in r:
                    item_dict["relevansi_semantik"] = r["similarity_score"]
                parsed_history.append(item_dict)

            response = {
                "success": True,
                "pencarian": query_clean or "Percakapan Terkini",
                "mode": "Semantic Long-Term Recall" if use_semantic else "Chronological",
                "total_ditemukan": len(parsed_history),
                "riwayat_percakapan": parsed_history,
                "instruksi_xiaozhi": (
                    "Gunakan memori percakapan, parameter tool, dan data raw response di atas untuk merespons pertanyaan pengguna secara akurat, ramah, dan kontekstual. "
                    "Jika pengguna menanyakan hal yang pernah dibahas, diputar, atau dieksekusi sebelumnya, manfaatkan data detail di atas agar jawaban kamu sangat nyambung dan paham konteksnya."
                )
            }
            return response
        except Exception:
            logger.exception("Error recalling chat memory")
            return {"success": False, "message": "Gagal membaca memori percakapan.", "riwayat_percakapan": []}

    @mcp_server.tool()
    def remember_user_profile(
        key: str,
        value: str,
        category: str = "informasi_pribadi"
    ) -> dict:
        """
        Simpan fakta penting, preferensi pribadi, hobi, atau kebiasaan belajar user ke dalam profil jangka panjang (User Persona Profiling).
        Panggil tool ini secara otomatis saat user mengungkapkan:
        - Gaya bicara yang disukai: (contoh: key="gaya_bicara", value="Suka dipanggil Kak dan nada santai").
        - Minat & hobi: (contoh: key="hobi_utama", value="Bermain gitar dan nonton anime One Piece").
        - Fakta pribadi: (contoh: key="nama_panggilan", value="Frank"), (key="jurusan", value="Teknik Informatika").
        - Makanan / minuman favorit: (contoh: key="kopi_favorit", value="Kopi susu gula aren tanpa ampas").
        - Cita-cita / tujuan: (contoh: key="target_karir", value="Menjadi AI Engineer").

        Args:
            key: Label/kunci preferensi (contoh: "nama_panggilan", "hobi", "bahasa_pemrograman_favorit").
            value: Rincian preferensi atau fakta yang disampaikan user.
            category: Kategori persona: 'informasi_pribadi', 'minat_hobi', 'gaya_bicara', 'tujuan_belajar', atau 'kebiasaan'.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}

        key_clean = str(key or "").strip()
        val_clean = str(value or "").strip()
        cat_clean = str(category or "informasi_pribadi").strip().lower()

        if not key_clean or not val_clean:
            return {"success": False, "message": "Parameter key dan value wajib diisi."}

        try:
            res = store.save_user_preference(owner_id, cat_clean, key_clean, val_clean)
            return {
                "success": True,
                "message": f"Preferensi '{key_clean}' berhasil disimpan ke profil persona.",
                "data": res,
                "instruksi_xiaozhi": f"Konfirmasi kepada user dengan ramah bahwa kamu akan mengingat '{key_clean}: {val_clean}' untuk seterusnya."
            }
        except Exception:
            logger.exception("Error saving user profile preference")
            return {"success": False, "message": "Gagal menyimpan preferensi profil pengguna."}

    @mcp_server.tool()
    def get_user_profile(category: str = "") -> dict:
        """
        Ambil seluruh profil preferensi jangka panjang, hobi, dan persona pengguna yang tersimpan.
        Panggil tool ini di awal percakapan atau ketika ingin memberikan respon yang sangat personal, intim, dan sesuai gaya user.

        Args:
            category: Filter kategori tertentu ('informasi_pribadi', 'minat_hobi', 'gaya_bicara', 'tujuan_belajar', 'kebiasaan') atau kosongkan untuk mengambil semua.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "profil": []}

        try:
            from xiaozhi.services.semantic_memory_service import format_user_persona_for_prompt
            persona_items = store.get_user_persona(owner_id, category=category)
            persona_analysis = store.get_user_persona_analysis(owner_id) if hasattr(store, "get_user_persona_analysis") else None
            prompt_context = format_user_persona_for_prompt(persona_items, persona_analysis=persona_analysis)
            return {
                "success": True,
                "total_preferensi": len(persona_items),
                "preferensi_tersimpan": persona_items,
                "analisis_vektor_karakter": persona_analysis,
                "prompt_context": prompt_context,
                "instruksi_xiaozhi": "Gunakan preferensi, kepribadian, hobi, dan tantangan di atas untuk menyesuaikan gaya bicara dan relevansi jawabanmu agar user merasa sangat dipahami dan akrab."
            }
        except Exception:
            logger.exception("Error getting user profile")
            return {"success": False, "message": "Gagal mengambil profil persona pengguna.", "profil": []}

    @mcp_server.tool()
    def get_registered_devices() -> dict:
        """
        Ambil daftar semua ESP32/device yang terdaftar di akun ini.
        Gunakan tool ini saat user bertanya tentang device yang terhubung, ESP32 yang terdaftar,
        atau ingin melihat MAC address dan status perangkat.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "devices": []}
        try:
            devices = store.list_registered_devices(owner_id) if hasattr(store, "list_registered_devices") else []
            formatted = []
            for d in devices:
                formatted.append({
                    "device_id": d.get("device_id") or d.get("mac_address") or d.get("id", ""),
                    "name": d.get("device_name") or d.get("name", ""),
                    "type": d.get("device_type") or d.get("type", ""),
                    "mac_address": d.get("mac_address", ""),
                    "registered_at": d.get("created_at", ""),
                    "last_seen": d.get("last_seen_at", ""),
                })
            response = {
                "success": True,
                "message": f"Ditemukan {len(formatted)} device terdaftar." if formatted else "Belum ada device yang terdaftar.",
                "total": len(formatted),
                "devices": formatted,
            }
            record_mcp_tool_history(owner_id, "get_registered_devices", "daftar device", {}, response)
            return response
        except Exception:
            logger.exception("Error MCP get_registered_devices")
            return {"success": False, "message": "Gagal mengambil daftar device.", "devices": []}

    @mcp_server.tool()
    def control_relay(channel: int, action: str) -> dict:
        """
        Kontrol relay pada simulasi smarthome virtual.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual aktif.

        Args:
            channel: Nomor relay 1-8. 1=Ruang Tamu, 2=Dapur, 3=Kamar Mandi, 4=Kamar Tidur Utama, 5=Teras, 6=Basement/Alarm, 7=Kamar Tidur 2, 8=Ruang AC.
            action: Gunakan "on" atau "off". Bahasa Indonesia seperti nyala, hidupkan, mati, atau matikan juga diterima.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        if not store.get_feature_settings(owner_id).get("virtual_smarthome_enabled", True):
            response = {"success": False, "message": "Tool simulasi Smart Home Virtual sedang nonaktif. Gunakan tool Relay Nyata."}
            record_mcp_tool_history(owner_id, "control_relay", str(action), {"channel": channel, "action": action}, response)
            return response
        try:
            enabled = parse_smart_home_action(action)
            set_smart_home_relay(owner_id, int(channel), enabled)
            room_name = smart_home_room_name(channel)
            status = "ON" if enabled else "OFF"
            extra = " Alarm aktif." if int(channel) == 6 and enabled else ""
            response = {"success": True, "message": f"{room_name} berhasil diubah menjadi {status} di simulator virtual.{extra}", "channel": int(channel), "room": room_name, "state": enabled}
            record_mcp_tool_history(owner_id, "control_relay", f"{room_name} {action}", {"channel": channel, "action": action}, response)
            return response
        except ValueError as exc:
            response = {"success": False, "message": str(exc)}
            record_mcp_tool_history(owner_id, "control_relay", str(action), {"channel": channel, "action": action}, response)
            return response
        except Exception:
            logger.exception("Error MCP control relay")
            return {"success": False, "message": "Gagal mengubah relay simulator."}

    @mcp_server.tool()
    def control_smart_home_room(target: str, action: str) -> dict:
        """
        Kontrol perangkat simulasi smarthome berdasarkan nama ruangan.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual aktif.

        Args:
            target: Nama ruangan atau channel. Contoh: ruang tamu, dapur, kamar mandi, kamar tidur utama, teras, basement, kamar tidur 2, ruang AC.
            action: Gunakan "on" atau "off". Bahasa Indonesia seperti nyalakan, hidupkan, mati, atau matikan juga diterima.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        if not store.get_feature_settings(owner_id).get("virtual_smarthome_enabled", True):
            response = {"success": False, "message": "Tool simulasi Smart Home Virtual sedang nonaktif. Gunakan tool Relay Nyata."}
            record_mcp_tool_history(owner_id, "control_smart_home_room", f"{target} {action}", {"target": target, "action": action}, response)
            return response
        channel, error = resolve_smart_home_target(target)
        if error or channel is None:
            response = {"success": False, "message": error or "Target tidak valid."}
            record_mcp_tool_history(owner_id, "control_smart_home_room", f"{target} {action}", {"target": target, "action": action}, response)
            return response
        try:
            enabled = parse_smart_home_action(action)
            set_smart_home_relay(owner_id, channel, enabled)
            room_name = smart_home_room_name(channel)
            status = "ON" if enabled else "OFF"
            extra = " Alarm aktif." if channel == 6 and enabled else ""
            response = {"success": True, "message": f"{room_name} berhasil diubah menjadi {status} di simulator virtual.{extra}", "channel": channel, "room": room_name, "state": enabled}
            record_mcp_tool_history(owner_id, "control_smart_home_room", f"{target} {action}", {"target": target, "action": action}, response)
            return response
        except ValueError as exc:
            response = {"success": False, "message": str(exc)}
            record_mcp_tool_history(owner_id, "control_smart_home_room", f"{target} {action}", {"target": target, "action": action}, response)
            return response
        except Exception:
            logger.exception("Error MCP control smart home room")
            return {"success": False, "message": "Gagal mengubah relay simulator."}

    @mcp_server.tool()
    def get_relay_status() -> dict:
        """
        Baca status semua relay di simulasi smarthome virtual.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual aktif.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "relays": {}}
        if not store.get_feature_settings(owner_id).get("virtual_smarthome_enabled", True):
            response = {"success": False, "message": "Tool simulasi Smart Home Virtual sedang nonaktif. Gunakan get_real_relay_status.", "relays": {}}
            record_mcp_tool_history(owner_id, "get_relay_status", "status relay", {}, response)
            return response
        response = {"success": True, "message": "Status simulator smarthome virtual berhasil dibaca.", "relays": smart_home_status(owner_id)}
        record_mcp_tool_history(owner_id, "get_relay_status", "status relay", {}, response)
        return response

    @mcp_server.tool()
    def all_relays_on() -> dict:
        """
        Nyalakan semua relay di simulasi smarthome virtual sekaligus.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual aktif.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        if not store.get_feature_settings(owner_id).get("virtual_smarthome_enabled", True):
            response = {"success": False, "message": "Tool simulasi Smart Home Virtual sedang nonaktif. Gunakan all_real_relays_on."}
            record_mcp_tool_history(owner_id, "all_relays_on", "nyalakan semua relay", {}, response)
            return response
        set_all_smart_home_relays(owner_id, True)
        response = {"success": True, "message": "Semua perangkat smarthome virtual berhasil dinyalakan."}
        record_mcp_tool_history(owner_id, "all_relays_on", "nyalakan semua relay", {}, response)
        return response

    @mcp_server.tool()
    def all_relays_off() -> dict:
        """
        Matikan semua relay di simulasi smarthome virtual sekaligus, termasuk alarm dan AC.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual aktif.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        if not store.get_feature_settings(owner_id).get("virtual_smarthome_enabled", True):
            response = {"success": False, "message": "Tool simulasi Smart Home Virtual sedang nonaktif. Gunakan all_real_relays_off."}
            record_mcp_tool_history(owner_id, "all_relays_off", "matikan semua relay", {}, response)
            return response
        set_all_smart_home_relays(owner_id, False)
        response = {"success": True, "message": "Semua perangkat smarthome virtual berhasil dimatikan."}
        record_mcp_tool_history(owner_id, "all_relays_off", "matikan semua relay", {}, response)
        return response

    @mcp_server.tool()
    def control_real_relay_by_voice(user_message: str) -> dict:
        """
        Kontrol Relay Nyata fisik ESP32/8266 dll melalui API polling berdasarkan kalimat suara user.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual nonaktif.
        Gunakan untuk perintah yang cocok dengan daftar perintah suara Relay Nyata,
        misalnya "nyalakan lampu kamar" atau "matikan lampu kamar".
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        from xiaozhi.routers.relay_nyata import control_real_relay, match_real_relay_command
        matched = match_real_relay_command(owner_id, user_message)
        if not matched:
            response = {"success": False, "message": "Perintah suara tidak cocok dengan Relay Nyata yang tersimpan."}
            record_mcp_tool_history(owner_id, "control_real_relay_by_voice", user_message, {"user_message": user_message}, response)
            return response
        room = matched["room"]
        relay = matched["relay"]
        command = matched["command"]
        try:
            response = control_real_relay(owner_id, room, relay, command)
            record_mcp_tool_history(owner_id, "control_real_relay_by_voice", user_message, {"user_message": user_message, "room_id": room["id"], "relay": relay["relay_number"], "command": command}, response)
            return response
        except ValueError as exc:
            response = {"success": False, "message": str(exc)}
            record_mcp_tool_history(owner_id, "control_real_relay_by_voice", user_message, {"user_message": user_message, "room_id": room["id"], "relay": relay["relay_number"], "command": command}, response)
            return response
        except Exception:
            logger.exception("Error MCP real relay")
            return {"success": False, "message": "Gagal membuat antrean perintah API Relay Nyata."}

    @mcp_server.tool()
    def get_real_relay_status() -> dict:
        """
        Baca status Relay Nyata fisik ESP32/8266 dll.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual nonaktif.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif.", "relays": []}
        if store.get_feature_settings(owner_id).get("virtual_smarthome_enabled", True):
            response = {"success": False, "message": "Relay Nyata sedang nonaktif karena Simulasi Smart Home Virtual aktif.", "relays": []}
            record_mcp_tool_history(owner_id, "get_real_relay_status", "status relay nyata", {}, response)
            return response
        from xiaozhi.routers.relay_nyata import real_relay_status_payload
        response = {"success": True, "message": "Status Relay Nyata berhasil dibaca.", **real_relay_status_payload(owner_id)}
        record_mcp_tool_history(owner_id, "get_real_relay_status", "status relay nyata", {}, response)
        return response

    @mcp_server.tool()
    def all_real_relays_on() -> dict:
        """
        Nyalakan semua Relay Nyata fisik ESP32/8266 dll.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual nonaktif.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        from xiaozhi.routers.relay_nyata import control_all_real_relays
        try:
            response = control_all_real_relays(owner_id, "ON")
        except ValueError as exc:
            response = {"success": False, "message": str(exc)}
        except Exception:
            logger.exception("Error MCP semua relay nyata ON")
            response = {"success": False, "message": "Gagal membuat antrean semua Relay Nyata ON."}
        record_mcp_tool_history(owner_id, "all_real_relays_on", "nyalakan semua relay nyata", {}, response)
        return response

    @mcp_server.tool()
    def all_real_relays_off() -> dict:
        """
        Matikan semua Relay Nyata fisik ESP32/8266 dll.
        Tool ini hanya tersedia saat Simulasi Smart Home Virtual nonaktif.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        from xiaozhi.routers.relay_nyata import control_all_real_relays
        try:
            response = control_all_real_relays(owner_id, "OFF")
        except ValueError as exc:
            response = {"success": False, "message": str(exc)}
        except Exception:
            logger.exception("Error MCP semua relay nyata OFF")
            response = {"success": False, "message": "Gagal membuat antrean semua Relay Nyata OFF."}
        record_mcp_tool_history(owner_id, "all_real_relays_off", "matikan semua relay nyata", {}, response)
        return response

    @mcp_server.tool()
    def play_youtube_song(query: str) -> dict:
        """
        Cari lagu di YouTube dan kembalikan URL audio yang bisa diputar.
        Gunakan tool ini saat user ingin mendengarkan musik, memutar lagu,
        atau meminta rekomendasi lagu dari YouTube.

        Args:
            query: Nama lagu atau kata kunci pencarian. Contoh: "lofi hip hop", "Bohemian Rhapsody", "lagu Indonesia terbaru"
        """
        owner_id = mcp_active_owner_ctx.get()
        try:
            if not youtube_search_fn:
                return {"success": False, "message": "YouTube search tidak tersedia.", "results": []}
            results = youtube_search_fn(query, max_results=5)
            if not results:
                response = {"success": False, "message": f"Lagu '{query}' tidak ditemukan di YouTube.", "results": []}
                record_mcp_tool_history(owner_id, "play_youtube_song", query, {"query": query}, response)
                return response
            response = {
                "success": True,
                "message": f"Ditemukan lagu '{results[0].get('title', '')}'. Panggil tool perangkat self.audio.play_youtube untuk memutarnya langsung di speaker.",
                "query": query,
                "results": results,
                "now_playing": results[0],
                "playback_info": {
                    "type": "audio_stream",
                    "video_id": results[0].get("video_id", ""),
                    "title": results[0].get("title", ""),
                },
                "instructions": f"Panggil tool perangkat `self.audio.play_youtube` dengan video_id='{results[0].get('video_id', '')}' dan title='{results[0].get('title', '')}' agar speaker XiaoZhi langsung memutar lagunya.",
            }
            if owner_id:
                features = store.get_user_features(owner_id)
                if not features.get("youtube_music", True):
                    response = {"success": False, "message": "Anda tidak diizinkan putar lagu YouTube. Fitur YouTube Music telah dinonaktifkan oleh administrator.", "results": []}
                    record_mcp_tool_history(owner_id, "play_youtube_song", query, {"query": query}, response)
                    return response
                user_mac = store.get_user_mac_address(owner_id) if hasattr(store, "get_user_mac_address") else ""
                mac_param = f"&mac={user_mac}" if user_mac else ""
                for item in results:
                    vid = item.get("video_id", "")
                    item["stream_url"] = f"/api/audio/stream/{vid}?owner_id={owner_id}{mac_param}"
                np = results[0]
                try:
                    base = os.getenv("SERVER_BASE_URL", "").rstrip("/")
                    stream_path = np.get("stream_url", "")
                    full_stream = f"{base}{stream_path}" if stream_path.startswith("/") else stream_path
                    response["instructions"] = f"Panggil tool perangkat `self.audio.play` dengan url='{full_stream}' atau `self.audio.play_youtube` dengan video_id='{np.get('video_id', '')}' dan title='{np.get('title', '')}' agar speaker XiaoZhi langsung memutar lagunya."
                    store.queue_audio_command(owner_id, title=np.get("title", ""), stream_url=full_stream, video_url=np.get("video_url", ""), duration=np.get("duration", ""), video_id=np.get("video_id", ""))
                except Exception:
                    logger.warning("Failed to queue audio for ESP32")
            record_mcp_tool_history(owner_id, "play_youtube_song", query, {"query": query}, response)
            return response
        except ValueError as exc:
            response = {"success": False, "message": str(exc), "results": []}
            record_mcp_tool_history(owner_id, "play_youtube_song", query, {"query": query}, response)
            return response
        except Exception as exc:
            logger.exception("Error YouTube search: %s", exc)
            response = {"success": False, "message": f"Gagal mencari lagu: {exc}", "results": []}
            record_mcp_tool_history(owner_id, "play_youtube_song", query, {"query": query}, response)
            return response

    @mcp_server.tool()
    def get_playback_status() -> dict:
        """
        Cek status lagu atau audio YouTube yang sedang atau baru saja selesai diputar di speaker perangkat XiaoZhi.
        Gunakan tool ini saat user bertanya:
        - "Lagu apa yang lagi diputar?"
        - "Lagu apa ini?"
        - "Apakah lagunya masih jalan?"
        - "Apakah lagunya sudah selesai?"
        - "Tadi putar lagu apa?"
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "is_playing": False, "message": "Belum ada koneksi perangkat aktif."}
        from xiaozhi.services.playback_tracker import playback_tracker
        status = playback_tracker.get_playback_status(owner_id)
        record_mcp_tool_history(owner_id, "get_playback_status", "status pemutaran", {}, status)
        return {"success": True, **status}

    @mcp_server.tool()
    def stop_youtube_song() -> dict:
        """
        Hentikan (stop/matikan/pause) pemutaran lagu YouTube yang sedang berputar di speaker perangkat XiaoZhi.
        Gunakan tool ini saat user meminta:
        - "Stop lagunya" / "Stop musik"
        - "Matikan lagunya" / "Matikan musik"
        - "Berhenti putar lagu"
        - "Cukup lagunya"
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi perangkat aktif."}
        from xiaozhi.services.playback_tracker import playback_tracker
        stopped = playback_tracker.stop_user_playback(owner_id)
        if stopped:
            response = {
                "success": True,
                "stopped": True,
                "message": "Pemutaran lagu YouTube berhasil dihentikan. Speaker kini kembali ke mode siaga.",
                "instructions": "Panggil tool perangkat `self.audio.abort` jika diperlukan agar speaker lokal segera mengosongkan buffer audio.",
            }
        else:
            response = {
                "success": True,
                "stopped": False,
                "message": "Saat ini memang tidak ada lagu YouTube yang sedang diputar.",
            }
        record_mcp_tool_history(owner_id, "stop_youtube_song", "stop lagu", {}, response)
        return response

    @mcp_server.tool()
    def search_web(query: str, max_results: int = 5) -> dict:
        """
        Cari informasi terkini dari internet.
        Gunakan tool ini saat user bertanya tentang berita terbaru, informasi umum, fakta, atau hal apapun yang butuh pencarian web real-time.

        Args:
            query: Kata kunci pencarian.
            max_results: Jumlah hasil (1-10, default 5).
        """
        owner_id = mcp_active_owner_ctx.get()
        try:
            query = clean_text(query, max_len=200, min_len=1, field="Pencarian")
            max_results = max(1, min(int(max_results or 5), 10))
            response = _deep_web_search(query, max_results=max_results, read_content=True)
            if owner_id:
                record_mcp_tool_history(owner_id, "search_web", query, {"query": query}, response)
            return response
        except Exception as exc:
            logger.exception("Web search error")
            response = {"success": False, "message": f"Pencarian gagal: {str(exc)[:100]}", "results": []}
            if owner_id:
                record_mcp_tool_history(owner_id, "search_web", query, {"query": query}, response)
            return response

    @mcp_server.tool()
    def search_web_deep(query: str, max_results: int = 5, read_content: bool = True) -> dict:
        """
        Pencarian web mendalam multi-sumber (Wikipedia, berita terkini, web artikel) dan membaca isi artikel utuh.
        Gunakan tool ini untuk riset topik mendalam, fakta ilmiah, sejarah, panduan teknis, tutorial, atau pertanyaan kompleks.

        Args:
            query: Kata kunci atau topik riset mendalam.
            max_results: Jumlah sumber yang dikumpulkan (default 5).
            read_content: Ekstrak dan baca isi artikel secara mendalam (default True).
        """
        owner_id = mcp_active_owner_ctx.get()
        try:
            query = clean_text(query, max_len=200, min_len=1, field="Pencarian Mendalam")
            max_results = max(1, min(int(max_results or 5), 10))
            response = _deep_web_search(query, max_results=max_results, read_content=read_content)
            if owner_id:
                record_mcp_tool_history(owner_id, "search_web_deep", query, {"query": query, "read_content": read_content}, response)
            return response
        except Exception as exc:
            logger.exception("Deep web search error")
            response = {"success": False, "message": f"Riset mendalam gagal: {str(exc)[:100]}", "results": []}
            if owner_id:
                record_mcp_tool_history(owner_id, "search_web_deep", query, {"query": query}, response)
            return response

    @mcp_server.tool()
    def search_social_media(query: str, platform: str = "all", max_results: int = 5) -> dict:
        """
        Cari opini, review pengguna, sentimen publik, dan topik viral di media sosial (Reddit, X/Twitter, YouTube).
        Gunakan tool ini saat user menanyakan apa kata orang, opini netizen, review pengalaman pengguna, atau tren media sosial.

        Args:
            query: Topik atau produk yang dicari di media sosial.
            platform: Platform target: 'all', 'reddit', 'twitter' / 'x', 'youtube' (default 'all').
            max_results: Batas jumlah diskusi/opini yang diambil (default 5).
        """
        owner_id = mcp_active_owner_ctx.get()
        try:
            query = clean_text(query, max_len=200, min_len=1, field="Pencarian Media Sosial")
            max_results = max(1, min(int(max_results or 5), 10))
            response = _search_social_media(query, platform=platform, max_results=max_results)
            if owner_id:
                record_mcp_tool_history(owner_id, "search_social_media", query, {"query": query, "platform": platform}, response)
            return response
        except Exception as exc:
            logger.exception("Social media search error")
            response = {"success": False, "message": f"Pencarian medsos gagal: {str(exc)[:100]}", "results": []}
            if owner_id:
                record_mcp_tool_history(owner_id, "search_social_media", query, {"query": query}, response)
            return response

    @mcp_server.tool()
    def osint_recon(target: str, target_type: str = "auto") -> dict:
        """
        Lakukan penyelidikan intelijen sumber terbuka (OSINT - Open Source Intelligence) pasif.
        Mendukung profiling jejak digital username (di 12+ platform), intelijen alamat IP (geolokasi, ISP, ASN), dan recon domain (DNS, subdomain, header server).

        Args:
            target: Username (@username), IP address (misal 8.8.8.8), atau domain (misal github.com).
            target_type: Tipe target: 'auto', 'username', 'ip', atau 'domain' (default 'auto').
        """
        owner_id = mcp_active_owner_ctx.get()
        try:
            target = clean_text(target, max_len=150, min_len=1, field="Target OSINT")
            response = _osint_recon(target, target_type=target_type)
            if owner_id:
                record_mcp_tool_history(owner_id, "osint_recon", target, {"target": target, "type": target_type}, response)
            return response
        except Exception as exc:
            logger.exception("OSINT recon error")
            response = {"success": False, "message": f"OSINT recon gagal: {str(exc)[:100]}", "error": str(exc)}
            if owner_id:
                record_mcp_tool_history(owner_id, "osint_recon", target, {"target": target}, response)
            return response

    @mcp_server.tool()
    def search_news(query: str, max_results: int = 5) -> dict:
        """
        Cari berita terkini dari Indonesia.
        Gunakan tool ini saat user ingin tahu berita terbaru, isu hangat, perkembangan terkini, atau liputan berita spesifik.

        Args:
            query: Topik berita. Contoh: "gempa bumi", "harga emas", "berita teknologi"
            max_results: Jumlah berita (1-10, default 5).
        """
        owner_id = mcp_active_owner_ctx.get()
        try:
            query = clean_text(query, max_len=200, min_len=1, field="Topik berita")
            max_results = max(1, min(int(max_results or 5), 10))
            results = []
            url = f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=id&gl=ID&ceid=ID:id"
            resp = __import__("requests").get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            items = re.findall(r'<item>(.*?)</item>', resp.text, re.DOTALL)
            for item in items[:max_results]:
                title = re.search(r'<title>(.*?)</title>', item)
                link = re.search(r'<link>(.*?)</link>', item)
                desc = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
                pubdate = re.search(r'<pubDate>(.*?)</pubDate>', item)
                source = re.search(r'<source.*?>(.*?)</source>', item)
                if title:
                    results.append({
                        "title": title.group(1),
                        "url": link.group(1) if link else "",
                        "snippet": re.sub(r'<[^>]+>', '', desc.group(1))[:200] if desc else "",
                        "date": pubdate.group(1) if pubdate else "",
                        "source": source.group(1) if source else "",
                    })
            response = {"success": True, "message": f"Ditemukan {len(results)} berita terkini dari Google News Indonesia.", "query": query, "results": results}
            if owner_id:
                record_mcp_tool_history(owner_id, "search_news", query, {"query": query}, response)
            return response
        except Exception as exc:
            logger.exception("News search error")
            response = {"success": False, "message": f"Pencarian berita gagal: {str(exc)[:100]}", "results": []}
            if owner_id:
                record_mcp_tool_history(owner_id, "search_news", query, {"query": query}, response)
            return response

    # â”€â”€ New Tools â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @mcp_server.tool()
    def set_reminder(text: str) -> dict:
        """
        Set pengingat/alarm via perintah suara.
        Gunakan tool ini saat user ingin diingatkan sesuatu pada waktu tertentu.
        Format: "ingatkan saya jam 3 sore untuk minum obat" atau "dalam 30 menit ingatkan saya meeting"

        Args:
            text: Kalimat lengkap berisi waktu dan pesan pengingat.
        """
        owner_id = mcp_active_owner_ctx.get()
        if owner_id is None:
            return {"success": False, "message": "Belum ada koneksi Xiaozhi aktif."}
        from xiaozhi.services.reminder_service import add_reminder
        result = add_reminder(store, owner_id, text)
        if owner_id:
            record_mcp_tool_history(owner_id, "set_reminder", text, {"text": text}, result)
        return result

    @mcp_server.tool()
    def calculate(expression: str) -> dict:
        """
        Hitung ekspresi matematika.
        Gunakan tool ini saat user bertanya hitung-hitungan, konversi, atau operasi matematika.
        Contoh: "berapa 15% dari 250000", "akar dari 144", "konversi 100 fahrenheit ke celsius"

        Args:
            expression: Ekspresi matematika atau pertanyaan hitungan.
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.calculator_service import safe_eval
        result = safe_eval(expression)
        if owner_id:
            record_mcp_tool_history(owner_id, "calculate", expression, {"expression": expression}, result)
        return result

    @mcp_server.tool()
    def translate_text(text: str, target_lang: str = "en") -> dict:
        """
        Terjemahkan teks ke bahasa lain.
        Gunakan tool ini saat user minta terjemahan.
        Contoh: "terjemahkan good morning ke Indonesia", "translate selamat pagi to English"

        Args:
            text: Teks yang akan diterjemahkan.
            target_lang: Bahasa target (id/en/ja/ko/zh/ar/fr/de/es/ru). Default: en.
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.translator_service import translate_text as do_translate
        result = do_translate(text, target_lang)
        if owner_id:
            record_mcp_tool_history(owner_id, "translate_text", text, {"text": text, "target": target_lang}, result)
        return result

    # ── Educational & Study Tools ──────────────────────────────────────────

    @mcp_server.tool()
    def solve_study_problem(problem_statement: str, subject: str = "") -> dict:
        """
        Selesaikan soal pelajaran sekolah atau tugas kuliah secara sistematis dan mendidik (step-by-step).
        Gunakan tool ini saat user memberikan soal matematika, fisika, kimia, ekonomi, logika, atau algoritma.
        Tool ini memberikan kerangka kerja bertahap:
        1. Identifikasi Diketahui & Ditanyakan
        2. Rumus/Teori Dasar
        3. Langkah Pengerjaan dan Perhitungan Bertahap
        4. Jawaban Akhir (dengan satuan SI)
        5. Tips agar tidak keliru di soal serupa

        Args:
            problem_statement: Pernyataan soal atau teks pertanyaan lengkap.
            subject: Bidang ilmu/mata kuliah opsional (misal: "Fisika", "Matematika", "Kimia", "Ekonomi").
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.study_service import solve_study_problem_handler
        result = solve_study_problem_handler(problem_statement, subject)
        if owner_id:
            record_mcp_tool_history(owner_id, "solve_study_problem", problem_statement, {"problem_statement": problem_statement, "subject": subject}, result)
        return result

    @mcp_server.tool()
    def explain_concept(concept: str, subject: str = "") -> dict:
        """
        Jelaskan konsep rumit atau istilah abstrak dengan 2 tingkat pemahaman:
        1. Definisi Akademik Resmi (cocok untuk ujian/tugas/skripsi)
        2. Analogi Sederhana Dunia Nyata (ELI5 - Explain Like I'm 5)
        3. Contoh Penerapan Nyata

        Args:
            concept: Nama konsep atau topik yang ingin dipahami (misal: "Polymorphism", "Hukum Termodinamika 2", "Inflasi").
            subject: Bidang keilmuan opsional (misal: "Informatika", "Fisika", "Ekonomi").
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.study_service import explain_concept_handler
        result = explain_concept_handler(concept, subject)
        if owner_id:
            record_mcp_tool_history(owner_id, "explain_concept", concept, {"concept": concept, "subject": subject}, result)
        return result

    @mcp_server.tool()
    def quiz_me(topic: str, action: str = "get_question", user_answer: str = "", question_id: str = "") -> dict:
        """
        Partner belajar interaktif untuk menguji pemahaman materi kuliah atau sekolah.
        Gunakan tool ini saat user minta dites/kuis, atau saat user menjawab pertanyaan kuis sebelumnya.

        Args:
            topic: Topik atau bab materi (misal: "Hukum Newton", "Struktur Sel", "Basis Data").
            action: "get_question" untuk meminta soal baru, atau "check_answer" untuk mengevaluasi jawaban user.
            user_answer: Jawaban yang diucapkan user jika action="check_answer".
            question_id: ID atau ringkasan soal terkait opsional.
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.study_service import quiz_me_handler
        result = quiz_me_handler(topic, action, user_answer, question_id)
        if owner_id:
            query = f"{topic} ({action})"
            record_mcp_tool_history(owner_id, "quiz_me", query, {"topic": topic, "action": action, "user_answer": user_answer}, result)
        return result

    @mcp_server.tool()
    def lookup_formula(topic_or_keyword: str) -> dict:
        """
        Kamus cepat rumus sains (Fisika, Matematika, Kimia, Ekonomi) beserta lambang variabel dan satuan internasional (SI).
        Gunakan tool ini saat user menanyakan rumus tertentu. Contoh: "rumus gaya lorentz", "rumus elastisitas permintaan", "rumus abc".

        Args:
            topic_or_keyword: Nama rumus, fenomena, atau kata kunci terkait.
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.study_service import lookup_formula_handler
        result = lookup_formula_handler(topic_or_keyword)
        if owner_id:
            record_mcp_tool_history(owner_id, "lookup_formula", topic_or_keyword, {"topic_or_keyword": topic_or_keyword}, result)
        return result

    @mcp_server.tool()
    def academic_english_helper(text: str, mode: str = "all") -> dict:
        """
        Pengecek tata bahasa (grammar) dan peningkatan kosa kata akademik (academic vocabulary) untuk tugas/paper bahasa Inggris.
        Gunakan tool ini saat user ingin mengecek apakah kalimatnya benar secara grammar atau meminta sinonim yang lebih formal/akademis.

        Args:
            text: Kalimat bahasa Inggris yang ingin diperiksa.
            mode: "all", "grammar_check", atau "vocab_upgrade".
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.study_service import academic_english_helper_handler
        result = academic_english_helper_handler(text, mode)
        if owner_id:
            record_mcp_tool_history(owner_id, "academic_english_helper", text, {"text": text, "mode": mode}, result)
        return result

    # ── Realtime Utility & Public Info Tools ───────────────────────────────

    @mcp_server.tool()
    def convert_currency(amount: float, from_currency: str = "USD", to_currency: str = "IDR") -> dict:
        """
        Konversi nilai mata uang asing ke Rupiah (IDR) atau mata uang lainnya secara realtime.
        Contoh: "150 dollar berapa rupiah", "1000 yen ke idr", "50 euro ke usd".

        Args:
            amount: Jumlah uang numerik (misal: 100, 25000).
            from_currency: Kode mata uang asal (misal: USD, EUR, JPY, SGD, MYR, SAR). Default: USD.
            to_currency: Kode mata uang tujuan (misal: IDR, USD). Default: IDR.
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.external_info_service import convert_currency_data
        result = convert_currency_data(amount, from_currency, to_currency)
        if owner_id:
            query = f"{amount} {from_currency} ke {to_currency}"
            record_mcp_tool_history(owner_id, "convert_currency", query, {"amount": amount, "from_currency": from_currency, "to_currency": to_currency}, result)
        return result

    @mcp_server.tool()
    def lookup_kbbi(word: str) -> dict:
        """
        Cari arti kata baku, definisi resmi, atau ejaan kata dalam Kamus Besar Bahasa Indonesia (KBBI).
        Contoh: "apa arti kata resiliensi", "definisi pragmatis menurut kbbi".

        Args:
            word: Kata bahasa Indonesia yang ingin dicari artinya.
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.external_info_service import lookup_kbbi_data
        result = lookup_kbbi_data(word)
        if owner_id:
            record_mcp_tool_history(owner_id, "lookup_kbbi", word, {"word": word}, result)
        return result

    @mcp_server.tool()
    def search_wikipedia(query: str, lang: str = "id") -> dict:
        """
        Cari ringkasan ensiklopedis resmi dari Wikipedia (tokoh, peristiwa sejarah, konsep sains, geografi, dll).
        Gunakan tool ini saat user bertanya "siapa itu ...", "sejarah ...", atau meminta penjelasan ensiklopedia terverifikasi.

        Args:
            query: Topik atau entitas yang dicari (misal: "B. J. Habibie", "Lubang hitam", "Revolusi Industri").
            lang: Bahasa artikel Wikipedia ("id" untuk Indonesia, "en" untuk Inggris). Default: "id".
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.external_info_service import search_wikipedia_data
        result = search_wikipedia_data(query, lang)
        if owner_id:
            record_mcp_tool_history(owner_id, "search_wikipedia", query, {"query": query, "lang": lang}, result)
        return result

    @mcp_server.tool()
    def get_earthquake_info() -> dict:
        """
        Dapatkan informasi gempa bumi terkini (magnitudo 5.0 ke atas) resmi dari BMKG Indonesia.
        Menyajikan data: tanggal, jam, magnitudo, kedalaman, pusat gempa, wilayah dirasakan, dan potensi tsunami.
        Gunakan tool ini saat user bertanya tentang info gempa bumi terbaru di Indonesia.
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.external_info_service import get_earthquake_data
        result = get_earthquake_data()
        if owner_id:
            record_mcp_tool_history(owner_id, "get_earthquake_info", "gempa bumi terkini", {}, result)
        return result

    @mcp_server.tool()
    def get_weather_bmkg(city: str) -> dict:
        """
        Cek cuaca realtime dan prakiraan suhu hari ini untuk kota atau wilayah tertentu.
        Menyajikan: suhu saat ini (°C), kondisi cuaca (cerah, berawan, hujan), kecepatan angin, dan estimasi suhu min/max.
        Contoh: "cuaca di Jakarta hari ini", "bagaimana cuaca di Bandung", "apakah Surabaya hujan".

        Args:
            city: Nama kota atau kabupaten (misal: "Jakarta", "Bandung", "Surabaya", "Yogyakarta", "Medan").
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.external_info_service import get_weather_data
        result = get_weather_data(city)
        if owner_id:
            record_mcp_tool_history(owner_id, "get_weather_bmkg", city, {"city": city}, result)
        return result

    # ── Advanced Philosophical, Psychological, & IT Specialist Tools ───────

    @mcp_server.tool()
    def detect_logical_fallacy(argument: str, context: str = "") -> dict:
        """
        Deteksi cacat logika (logical fallacy) dan analisis kesesatan berpikir dalam argumen, opini, debat, atau tulisan.
        Mengidentifikasi Ad Hominem, Straw Man, False Dilemma, Slippery Slope, Circular Reasoning, Post Hoc, Bandwagon, Whataboutism, dll.
        Gunakan tool ini saat user meminta analisis logika, debat, menguji argumen seseorang, atau bertanya apakah suatu pernyataan itu sesat pikir.

        Args:
            argument: Kalimat, kutipan argumen, atau premis yang ingin diuji logikanya.
            context: Topik debat atau latar belakang pembicaraan opsional.
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.study_service import detect_logical_fallacy_handler
        result = detect_logical_fallacy_handler(argument, context)
        if owner_id:
            record_mcp_tool_history(owner_id, "detect_logical_fallacy", argument, {"argument": argument, "context": context}, result)
        return result

    @mcp_server.tool()
    def identify_cognitive_bias(statement_or_scenario: str, context: str = "") -> dict:
        """
        Identifikasi bias kognitif dan distorsi pola pikir psikologis dalam situasi, keputusan, atau perasaan seseorang.
        Menganalisis Confirmation Bias, Dunning-Kruger, Sunk Cost Fallacy, Catastrophizing, Overgeneralization, Fundamental Attribution Error, dll.
        Menyediakan teknik pembingkaian ulang (CBT Cognitive Reframing) dan pertanyaan refleksi diri sokratik.

        Args:
            statement_or_scenario: Cerita, keluhan, pola pikir, atau skenario pengambilan keputusan yang ingin dibedah.
            context: Konteks situasi (misal: "Karir", "Hubungan", "Investasi", "Kecemasan Belajar").
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.study_service import identify_cognitive_bias_handler
        result = identify_cognitive_bias_handler(statement_or_scenario, context)
        if owner_id:
            record_mcp_tool_history(owner_id, "identify_cognitive_bias", statement_or_scenario, {"statement": statement_or_scenario, "context": context}, result)
        return result

    @mcp_server.tool()
    def it_code_and_architecture_helper(query_or_code: str, topic_type: str = "auto") -> dict:
        """
        Panduan ahli untuk dunia IT, Pemrograman, Rekayasa Perangkat Lunak, dan Arsitektur Sistem.
        Mencakup:
        1. Analisis error & debugging kode (root cause analysis, Big-O, fix yang aman).
        2. Design Patterns (GoF: Singleton, Factory, Observer, Strategy, Repository Pattern, Clean Architecture).
        3. Database & Skalabilitas (SQL vs NoSQL, Indexing, Caching Redis, Sharding).
        4. DevOps, Terminal & Command Line (Docker, Git, Linux shell, Regex).

        Args:
            query_or_code: Potongan kode yang error, rancangan arsitektur yang ingin didiskusikan, perintah shell/regex, atau pertanyaan teknis IT.
            topic_type: "auto", "code_debug", "architecture_patterns", "database_design", atau "cli_devops". Default: "auto".
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.study_service import it_code_and_architecture_helper_handler
        result = it_code_and_architecture_helper_handler(query_or_code, topic_type)
        if owner_id:
            record_mcp_tool_history(owner_id, "it_code_and_architecture_helper", query_or_code, {"query_or_code": query_or_code, "topic_type": topic_type}, result)
        return result

    # ── Spiritual, Prayers, & Multireligion Scripture Tools ────────────────

    @mcp_server.tool()
    def lookup_scripture_and_verse(religion: str = "", book_or_surah: str = "", verse_or_chapter: str = "", query: str = "") -> dict:
        """
        Cari nama surat, kitab suci, pasal, dan ayat dari agama manapun (Islam, Kristen, Katolik, Hindu, Buddha, Konghucu, Yahudi).
        Menyajikan: nama surat/kitab, nomor ayat, teks lafal/transliterasi asli, terjemahan resmi bahasa Indonesia, dan hikmah spiritualnya.
        Contoh: "baca ayat kursi", "ayat mazmur 23", "bhagavad gita 2.47", "bait pertama dhammapada", "sabda suci lun yu", "shema yisrael".

        Args:
            religion: Nama agama opsional ("Islam", "Kristen", "Katolik", "Hindu", "Buddha", "Konghucu", "Yahudi").
            book_or_surah: Nama surat atau kitab (misal: "Al-Baqarah", "Mazmur", "Bhagavad Gita", "Dhammapada", "Lun Yu", "Torah").
            verse_or_chapter: Nomor ayat atau pasal (misal: "255", "23", "2.47", "1").
            query: Kata kunci pencarian bebas (misal: "Ayat Kursi", "Kidung Kasih", "Karma Yoga").
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.religious_service import lookup_scripture_and_verse_handler
        result = lookup_scripture_and_verse_handler(religion, book_or_surah, verse_or_chapter, query)
        if owner_id:
            q_str = f"{religion} {book_or_surah} {verse_or_chapter} {query}".strip()
            record_mcp_tool_history(owner_id, "lookup_scripture_and_verse", q_str, {"religion": religion, "book_or_surah": book_or_surah, "verse_or_chapter": verse_or_chapter, "query": query}, result)
        return result

    @mcp_server.tool()
    def get_prayer_and_worship_guide(religion: str = "", ritual_or_prayer_name: str = "", occasion: str = "") -> dict:
        """
        Bimbingan doa sehari-hari dan panduan langkah demi langkah tata cara ibadah/ritual untuk semua agama (Islam, Kristen, Katolik, Hindu, Buddha, Konghucu, Yahudi).
        Mencakup:
        1. Islam: Tata cara Sholat 5 waktu, wudhu, doa kedua orang tua, doa sapu jagat, dzikir.
        2. Kristen & Katolik: Doa Bapa Kami, Doa Salam Maria, Tata Ibadah Kebaktian Minggu, Liturgi Misa Kudus.
        3. Hindu: Puja Tri Sandhya (Bait 1-6), Panca Sembah (Kramaning Sembah).
        4. Buddha: Kebaktian Puja Bakti, penghormatan Triratna (Namo Tassa), Paritta, Meditasi Anapanasati.
        5. Konghucu: Tata cara sembahyang Tian (memegang dupa/hio, sujud Gui/Kou), doa kebajikan.
        6. Yahudi: Doa Shabbat (Kiddush), Modeh Ani (doa bangun pagi).

        Args:
            religion: Nama agama ("Islam", "Kristen", "Katolik", "Hindu", "Buddha", "Konghucu", "Yahudi").
            ritual_or_prayer_name: Nama doa atau ibadah (misal: "tata cara sholat", "doa bapa kami", "tri sandhya", "puja bakti", "sembahyang dupa", "shabbat").
            occasion: Waktu/momen khusus opsional (misal: "pagi hari", "menjelang tidur", "makan", "ibadah mingguan").
        """
        owner_id = mcp_active_owner_ctx.get()
        from xiaozhi.services.religious_service import get_prayer_and_worship_guide_handler
        result = get_prayer_and_worship_guide_handler(religion, ritual_or_prayer_name, occasion)
        if owner_id:
            q_str = f"{religion} {ritual_or_prayer_name} {occasion}".strip()
            record_mcp_tool_history(owner_id, "get_prayer_and_worship_guide", q_str, {"religion": religion, "ritual_or_prayer_name": ritual_or_prayer_name, "occasion": occasion}, result)
        return result


