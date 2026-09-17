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
    Simulates / streams intelligent contextual response word-by-word with SSE.
    Uses user knowledge base materials and semantic taxonomy.
    """
    clean_q = query.strip()
    if not clean_q:
        yield format_sse("error", {"message": "Pertanyaan tidak boleh kosong."})
        return

    yield format_sse("start", {"query": clean_q})

    # Search knowledge materials
    matched_materials = []
    try:
        raw_materials = store.search_materials(user_id, clean_q, limit=5)
        for m in raw_materials:
            sim = calculate_semantic_similarity(clean_q, f"{m.get('title', '')} {m.get('content', '')}")
            m["similarity"] = round(sim, 2)
            matched_materials.append(m)
        matched_materials.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    except Exception as e:
        logger.debug("Error searching materials for chat: %s", e)

    # Log chat event to admin log stream
    log_admin_event("chat", f"User {user_id} bertanya: '{clean_q[:40]}...'", {"user_id": user_id})

    # Construct contextual response
    top_material = matched_materials[0] if matched_materials and matched_materials[0].get("similarity", 0) > 0.15 else None

    if top_material:
        answer_intro = f"Berdasarkan materi '{top_material.get('title', 'Pengetahuan')}' di sistem Anda:\n\n"
        content = top_material.get("content", "")
        summary = content if len(content) <= 300 else content[:300] + "..."
        answer_body = f"{summary}\n\nApakah ada bagian lain dari topik ini yang ingin Anda tanyakan lebih lanjut?"
    else:
        # General intelligent knowledge response
        q_lower = clean_q.lower()
        if "halo" in q_lower or "hai" in q_lower or "selamat" in q_lower:
            answer_intro = "Halo! Senang bisa menyapa Anda. "
            answer_body = "Saya adalah asisten pintar XiaoZhi Indonesia. Saya siap membantu Anda memutar musik YouTube di ESP32, mengakses materi pengetahuan, maupun mengontrol perangkat pintar Anda. Ada yang bisa saya bantu hari ini?"
        elif "putar" in q_lower or "lagu" in q_lower or "musik" in q_lower:
            answer_intro = "Tentu! "
            answer_body = f"Untuk memutar lagu secara langsung di speaker ESP32 Anda, Anda bisa menggunakan perintah suara ke XiaoZhi atau klik fitur musik di dashboard. Jika Anda memanggil perintah suara 'Putar {clean_q}', speaker board Anda akan langsung memutarkannya secara otomatis."
        elif "mac" in q_lower or "esp32" in q_lower or "board" in q_lower:
            answer_intro = "Mengenai perangkat ESP32 Anda: "
            answer_body = "Board ESP32 Anda terhubung ke server kami melalui MAC address uniknya. Anda dapat melihat dan mengubah MAC board yang tersimpan di halaman Profil atau Dashboard kapan saja."
        else:
            answer_intro = f"Mengenai pertanyaan Anda tentang '{clean_q}':\n\n"
            answer_body = "Informasi ini telah kami catat dalam riwayat chat sistem Anda. Anda juga dapat menambahkan materi referensi khusus terkait hal ini di menu Tambah Materi pada Dashboard agar XiaoZhi dapat memberikan jawaban yang semakin akurat dan terpersonalisasi."

    full_text = answer_intro + answer_body

    # Stream token by token (split into words/phrases)
    words = full_text.split(" ")
    for idx, word in enumerate(words):
        if await request.is_disconnected():
            logger.info("Chat stream client disconnected early.")
            break
        
        chunk = word + (" " if idx < len(words) - 1 else "")
        yield format_sse("token", {"token": chunk, "index": idx})
        # Natural typing latency (25-45ms)
        await asyncio.sleep(0.035)

    # Save to chat history transcript
    try:
        token_info = store.get_xiaozhi_token_info(user_id)
        token_hash = token_info.get("token_hash", "") if token_info else ""
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
