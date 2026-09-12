import logging
import re
from typing import Any, Dict, Optional

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
                "message": f"Ditemukan {len(results)} lagu. Putar URL audio dari hasil pertama atau tawarkan pilihan ke user.",
                "query": query,
                "results": results,
                "now_playing": results[0],
                "playback_info": {"type": "audio_stream", "message": "Audio sudah di-queue. ESP32 akan otomatis putar lagu ini."},
                "instructions": "Mainkan audio dari URL yang diberikan. Jika user tidak menyebut lagu spesifik, tawarkan pilihan dari daftar hasil.",
            }
            if owner_id:
                features = store.get_user_features(owner_id)
                if not features.get("youtube_music", True):
                    response = {"success": False, "message": "Fitur putar lagu YouTube dinonaktifkan oleh admin.", "results": []}
                    record_mcp_tool_history(owner_id, "play_youtube_song", query, {"query": query}, response)
                    return response
                np = results[0]
                try:
                    base = "http://localhost:7860"
                    full_stream = f"{base}{np['stream_url']}" if np.get("stream_url", "").startswith("/") else np.get("stream_url", "")
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
                        "source": source.group(1) if source else "Google News",
                    })
            response = {"success": True, "message": f"Ditemukan {len(results)} hasil. Jawab berdasarkan informasi dari hasil pencarian.", "query": query, "results": results}
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

    # ── New Tools ──────────────────────────────────────────────────────

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
        result = add_reminder(owner_id, text)
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
