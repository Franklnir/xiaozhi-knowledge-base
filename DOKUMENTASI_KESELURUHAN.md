# Dokumentasi Lengkap: HUGGING XIAOZHI

> FastAPI Backend Gateway untuk Integrasi AI Agent (Xiaozhi), MCP Protocol, YouTube Streaming, Device Registry, dan IoT Control — Deploy di Hugging Face Spaces.

---

## Daftar Isi

1. [Ringkasan Proyek](#1-ringkasan-proyek)
2. [Arsitektur Sistem](#2-arsitektur-sistem)
3. [Teknologi yang Digunakan](#3-teknologi-yang-digunakan)
4. [Fitur Utama](#4-fitur-utama)
5. [Komponen Software](#5-komponen-software)
6. [MCP Bridge dan Server](#6-mcp-bridge-dan-server)
7. [YouTube Audio Streaming](#7-youtube-audio-streaming)
8. [Device Registry](#8-device-registry)
9. [Smart Home (Virtual dan Real Relay)](#9-smart-home-virtual-dan-real-relay)
10. [External Information Services](#10-external-information-services)
11. [Playback Tracker](#11-playback-tracker)
12. [Database Layer](#12-database-layer)
13. [Security dan Authentication](#13-security-dan-authentication)
14. [Alur Kerja Lengkap](#14-alur-kerja-lengkap)
15. [Daftar 36 MCP Tools](#15-daftar-36-mcp-tools)
16. [API Endpoints](#16-api-endpoints)
17. [Struktur File dan Direktori](#17-struktur-file-dan-direktori)
18. [Konfigurasi dan Environment](#18-konfigurasi-dan-environment)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. Ringkasan Proyek

**Nama:** Xiaozhi Indonesia — Hugging Face Space Backend
**Framework:** FastAPI (Python 3.11)
**Deployment:** Hugging Face Spaces (Docker) atau VPS lokal
**Database:** Hugging Face Dataset (JSON) atau SQLite (VPS)
**License:** Apache-2.0

Backend ini adalah **Intelligent Communication Gateway** yang menjadi perantara antara:
- **Xiaozhi AI Cloud** (`wss://api.xiaozhi.me`) — AI LLM yang memproses voice commands
- **ESP32 Devices** — perangkat IoT yang melakukan polling dan streaming
- **Users** — melalui web dashboard untuk manajemen

**Alasan Keberadaan Backend Ini:**

Xiaozhi AI cloud hanya menyediakan LLM dan MCP endpoint. Backend ini menambahkan:
- 36 custom MCP tools (study, search, smart home, YouTube, dll)
- YouTube audio streaming engine (FFmpeg → Opus → chunked HTTP)
- Device registry (MAC-based identification)
- Playback session tracking
- Multi-user management dengan dashboard

---

## 2. Arsitektur Sistem

```
┌──────────────────────────────────────────────────────────────────────┐
│                           USER                                       │
│                    "Putar Despacito"                                 │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ Voice
                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    ESP32-C3 Device                                   │
│                    (MAC: 84:7b:57:47:56:a8)                         │
│                                                                      │
│  ┌──────────────┐         ┌──────────────────┐                      │
│  │ WebSocket    │────────►│ Xiaozhi Cloud    │                      │
│  │ (AI Chat)    │◄────────│ wss://api.xiaozhi│                      │
│  └──────────────┘         └────────┬─────────┘                      │
│                                     │ MCP tools/call                 │
│  ┌──────────────┐                  │                                │
│  │ HTTP Polling │                  │                                │
│  │ (Commands)   │                  │                                │
│  └──────┬───────┘                  │                                │
│         │                          │                                │
└─────────┼──────────────────────────┼────────────────────────────────┘
          │                          │
          │ HTTP GET /commands       │ WebSocket MCP Bridge
          │                          │
          ▼                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                HUGGING XIAOZHI (FastAPI Backend)                     │
│                                                                      │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ MCP Bridge     │  │ YouTube        │  │ Device Registry      │  │
│  │ (WebSocket to  │  │ Streamer       │  │ (MAC → owner_id)     │  │
│  │  Xiaozhi Cloud)│  │ (yt_dlp + FFmpeg│  │                      │  │
│  │                │  │  → Opus → HTTP) │  │                      │  │
│  └────────┬───────┘  └────────┬───────┘  └──────────┬───────────┘  │
│           │                   │                      │              │
│  ┌────────▼───────────────────▼──────────────────────▼───────────┐  │
│  │                    MCP Server (36 Tools)                      │  │
│  │  Study │ Search │ SmartHome │ YouTube │ Weather │ Memory     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
│  │ Playback       │  │ Database       │  │ Web Dashboard        │  │
│  │ Tracker        │  │ (SQLite/HF)    │  │ (Admin Panel)        │  │
│  └────────────────┘  └────────────────┘  └──────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Tiga Jalur Komunikasi

**Jalur 1: MCP Bridge (WebSocket)**
```
Backend ←→ WebSocket ←→ wss://api.xiaozhi.me/mcp/ ←→ Xiaozhi AI
```
- Backend menyediakan 36 MCP tools
- Xiaozhi AI memanggil tools via JSON-RPC 2.0
- Per-user bridge task (multi-user)

**Jalur 2: YouTube Audio Stream (HTTP)**
```
ESP32 → GET /api/audio/stream/<vid>?br=12k → Backend
Backend → FFmpeg transcode → Opus Ogg → HTTP chunked → ESP32
```
- Device-aware routing (MAC → owner)
- Adaptive bitrate
- Playback session tracking

**Jalur 3: Command Delivery (HTTP Polling)**
```
User voice → Xiaozhi AI → MCP play_youtube_song → Backend simpan ke queue
ESP32 → GET /api/device/audio/commands?mac=XX → Backend kirim command
```
- Polling-based, bukan push
- Per-device command queue

---

## 3. Teknologi yang Digunakan

| Teknologi | Versi | Fungsi | Alasan Pemilihan |
|-----------|-------|--------|-------------------|
| **Python** | 3.11 | Bahasa backend | Ekosistem library lengkap, async support |
| **FastAPI** | latest | Web framework | Async native, auto OpenAPI docs, type hints |
| **Uvicorn** | latest | ASGI server | High performance async server |
| **MCP SDK** | `mcp` package | MCP server/client | Standar Anthropic untuk tool calling |
| **FastMCP** | `mcp.server.fastmcp` | High-level MCP API | Simplified tool registration |
| **yt_dlp** | latest | YouTube extractor | Best maintained, no API key needed |
| **FFmpeg** | (system) | Audio transcoding | Industry standard, Opus support |
| **websockets** | latest | WebSocket client | Async WebSocket untuk MCP bridge |
| **anyio** | latest | Async primitives | Memory object streams untuk MCP |
| **SQLite** | built-in | Database (VPS mode) | Zero config, embedded |
| **HF Datasets** | `huggingface_hub` | Database (Spaces mode) | Cloud storage tanpa setup DB |
| **Jinja2** | latest | Template engine | Dashboard HTML rendering |
| **Fernet** | `cryptography` | Token encryption | Simpan MCP token terenkripsi |
| **requests** | latest | HTTP client | External API calls (weather, BMKG) |
| **BeautifulSoup4** | latest | HTML parser | Web scraping, Wikipedia |
| **sentence-transformers** | opsional | Semantic search | Chat memory recall |

### Mengapa Teknologi Ini Dipilih?

1. **FastAPI vs Flask/Django**: FastAPI mendukung async secara native (crucial untuk WebSocket bridge + HTTP streaming concurrent). Auto-generated OpenAPI docs memudahkan testing. Type hints Python meminimalisir bug.

2. **MCP SDK (Anthropic) vs Custom Protocol**: MCP adalah standar industri yang diadopsi oleh Anthropic, OpenAI, Google. Menggunakan SDK resmi memastikan kompatibilitas dengan semua MCP clients.

3. **yt_dlp vs YouTube Data API**: YouTube Data API punya quota limit (10.000 unit/hari), butuh API key, dan TIDAK menyediakan direct audio URL. yt_dlp bisa extract direct audio stream tanpa batasan.

4. **FFmpeg vs Direct Stream**: YouTube audio perlu ditranscode ke Opus (format yang ESP32 bisa decode). FFmpeg handle semua format input dan output yang dibutuhkan.

5. **HTTP Polling vs WebSocket untuk Commands**: ESP32-C3 sudah memakai 1 WebSocket ke Xiaozhi Cloud. Backend ini tidak punya direct WebSocket ke ESP32 — jadi HTTP polling adalah cara paling sederhana untuk deliver commands.

6. **SQLite vs PostgreSQL**: Untuk single-server deployment, SQLite lebih dari cukup. Zero configuration, file-based, tidak butuh database server terpisah.

---

## 4. Fitur Utama

### 4.1 MCP Bridge (36 Tools)
- WebSocket bridge ke `wss://api.xiaozhi.me/mcp/` per user
- 36 MCP tools yang bisa dipanggil oleh Xiaozhi AI
- Tool filtering berdasarkan mode (virtual vs real relay)
- User permission system (admin bisa enable/disable tool per user)

### 4.2 YouTube Audio Streaming
- Search YouTube → extract audio → transcode ke Opus → stream ke ESP32
- Dual delivery: WebSocket injection (untuk sesi AI chat aktif) dan HTTP polling
- Adaptive bitrate berdasarkan RSSI ESP32
- Playback session tracking real-time

### 4.3 Device Registry
- MAC-based device identification
- Multi-device per user
- Device ownership verification
- Audio command queue per device

### 4.4 Smart Home Control
- **Virtual Mode**: Simulasi 8-channel relay di dashboard
- **Real Mode**: ESP32/8266 fisik dengan API polling
- Voice-controlled: "Nyalakan lampu kamar"
- Mode switching via dashboard

### 4.5 Knowledge Base
- Per-user material storage (materi kuliah, tugas, catatan)
- Category-based organization
- Semantic search
- Live API data integration

### 4.6 External Information
- Weather (Open-Meteo API)
- BMKG Earthquake data
- Currency conversion
- Wikipedia search
- KBBI (Kamus Besar Bahasa Indonesia)

### 4.7 Web Dashboard
- User authentication (login, register, search account)
- MCP endpoint management
- Device management
- Chat history
- Smart home control panel
- Admin panel (user management, monitoring)

---

## 5. Komponen Software

### 5.1 Struktur Kode

```
xiaozhi/
├── main.py                      # FastAPI app, lifespan, startup
├── config.py                    # Environment config, constants
├── dependencies.py              # Dependency injection (auth, store)
│
├── mcp/                         # MCP Protocol Layer
│   ├── server.py                # FastMCP server init, tool registration
│   ├── tools.py                 # 36 MCP tool implementations
│   ├── bridge.py                # WebSocket bridge ke Xiaozhi Cloud
│   └── context.py               # Context variables (owner_id, request_id)
│
├── routers/                     # API Endpoints
│   ├── youtube.py               # YouTube search, stream, commands
│   ├── devices.py               # Device registry CRUD
│   ├── mcp_endpoints.py         # MCP save/delete/reconnect/status
│   ├── api_v1_chat.py           # Chat API
│   ├── api_v1_mcp.py            # MCP API v1
│   ├── api_v1_smarthome.py      # Smart home API
│   ├── api_v1_materials.py      # Materials CRUD API
│   ├── api_v1_auth.py           # Auth API
│   ├── relay_nyata.py           # Real relay polling endpoints
│   ├── search.py                # Web/news search
│   ├── chat.py                  # Chat page
│   ├── dashboard.py             # Dashboard page
│   ├── admin.py                 # Admin panel
│   ├── auth.py                  # Auth pages
│   ├── smarthome.py             # Smart home page
│   ├── google_auth.py           # Google OAuth
│   └── api_v1_auth.py           # Auth API
│
├── services/                    # Business Logic
│   ├── youtube_streamer.py      # FFmpeg → Opus → WebSocket stream
│   ├── mcp_service.py           # MCP state management
│   ├── playback_tracker.py      # Active playback session tracking
│   ├── external_info_service.py # Weather, BMKG, currency, Wikipedia
│   ├── smarthome_service.py     # Smart home logic
│   ├── community_chat_service.py# Community chat features
│   ├── semantic_memory_service.py# Semantic search for chat memory
│   ├── study_service.py         # Study tools (quiz, formula, dll)
│   ├── religious_service.py     # Religious tools (prayer, scripture)
│   ├── translator_service.py    # Translation service
│   ├── calculator_service.py    # Calculator
│   ├── scraper_service.py       # Web scraper
│   ├── pdf_service.py           # PDF processing
│   ├── reminder_service.py      # Reminder/scheduling
│   ├── sse_service.py           # Server-Sent Events (admin log)
│   └── external_info_service.py # External API integration
│
├── database/                    # Data Layer
│   ├── store.py                 # Abstract store interface
│   ├── sqlite_store.py          # SQLite implementation
│   ├── factory.py               # Store factory (HF/SQLite auto-detect)
│   └── helpers.py               # DB utility functions
│
└── core/                        # Core Utilities
    ├── security.py              # Token encryption, hashing, CSRF
    ├── utils.py                 # General utilities
    ├── monitor.py               # System health monitoring
    ├── task_queue.py            # Background task queue
    ├── cache.py                 # Caching layer
    ├── rate_limiter.py          # Rate limiting
    ├── i18n.py                  # Internationalization
    └── api_docs.py              # Custom OpenAPI docs
```

### 5.2 Peran Setiap Komponen

| Komponen | File | Fungsi | Alasan |
|----------|------|--------|--------|
| **MCP Server** | `mcp/server.py` | Init FastMCP, register 36 tools, setup mode filtering | Entry point untuk semua MCP functionality |
| **MCP Tools** | `mcp/tools.py` | Implementasi 36 tool functions | Business logic terpusat untuk semua tool |
| **MCP Bridge** | `mcp/bridge.py` | WebSocket bridge ke Xiaozhi Cloud per user | Menghubungkan custom tools ke AI agent |
| **YouTube Streamer** | `services/youtube_streamer.py` | Extract audio → FFmpeg → Opus → WebSocket frames | Engine untuk YouTube audio streaming |
| **Playback Tracker** | `services/playback_tracker.py` | Track active streaming sessions real-time | Monitoring siapa streaming ke device mana |
| **Device Registry** | `routers/devices.py` | CRUD untuk device registration | MAC-based device identification |
| **External Info** | `services/external_info_service.py` | Weather, BMKG, currency, Wikipedia, KBBI | Real-time data untuk AI responses |
| **Smart Home** | `services/smarthome_service.py` | Virtual + real relay control logic | IoT control via voice commands |
| **SQLite Store** | `database/sqlite_store.py` | All database operations | Persistent storage untuk semua data |
| **Security** | `core/security.py` | Token encryption, hashing, CSRF protection | Protect sensitive data |

---

## 6. MCP Bridge dan Server

### 6.1 Cara Kerja MCP Bridge

```python
# bridge.py — Alur koneksi per user

async def run_mcp_bridge(store, mcp_server, user_id, url, token_hash):
    # 1. Buka WebSocket ke Xiaozhi MCP endpoint
    async with websockets.connect(url) as ws:
        # 2. Buat MCP read/write streams
        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        # 3. Jalankan MCP server dengan WebSocket sebagai transport
        await mcp_server._mcp_server.run(read_stream, write_stream, init_options)

        # 4. Reader: WebSocket → MCP stream (parse JSON-RPC)
        async for message in ws:
            msg = types.JSONRPCMessage.model_validate_json(message)
            await read_stream_writer.send(SessionMessage(msg))

        # 5. Writer: MCP stream → WebSocket (send JSON-RPC)
        async for session_message in write_stream_reader:
            json_str = session_message.message.model_dump_json()
            await ws.send(json_str)
```

### 6.2 MCP Message Flow

```
User: "Cari cuaca Jakarta"
        │
        ▼
Xiaozhi AI (LLM)
        │
        │ Decision: pakai tool get_weather
        │
        ▼
MCP tools/call (JSON-RPC 2.0)
{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
        "name": "get_weather",
        "arguments": {"city": "Jakarta"}
    },
    "id": 5
}
        │
        ▼
WebSocket ke wss://api.xiaozhi.me/mcp/
        │
        ▼
HUGGING XIAOZHI Backend
        │
        │ get_weather("Jakarta") → Open-Meteo API
        │
        ▼
Response:
{
    "jsonrpc": "2.0",
    "id": 5,
    "result": {
        "content": [{"type": "text", "text": "Jakarta: 32°C, Cerah..."}],
        "isError": false
    }
}
        │
        ▼
Xiaozhi AI → TTS → ESP32 → Speaker
"Cuaca Jakarta hari ini 32 derajat, cerah berawan..."
```

### 6.3 Multi-User Bridge Management

```python
# Setiap user punya bridge task sendiri
mcp_bridge_tasks: Dict[int, asyncio.Task] = {}

# Background loop:
while True:
    tokens = store.list_xiaozhi_tokens()
    for token_info in tokens:
        user_id = token_info["user_id"]
        if user_id not in mcp_bridge_tasks or mcp_bridge_tasks[user_id].done():
            # Launch bridge baru untuk user ini
            task = asyncio.create_task(run_mcp_bridge(...))
            mcp_bridge_tasks[user_id] = task

    # Tunggu reload signal atau timeout 30 detik
    await asyncio.wait_for(mcp_reload_event.wait(), timeout=30)
```

### 6.4 Tool Filtering

```python
# Filter tools berdasarkan mode dan user permissions
def is_tool_allowed_for_user(owner_id, tool_name):
    # 1. Virtual vs Real relay mode
    if virtual_smarthome_enabled:
        block tool "control_real_relay_by_voice"
    else:
        block tool "control_relay"

    # 2. User feature flags
    if tool_name == "play_youtube_song" and not features.get("youtube_music"):
        return False

    # 3. Admin tool toggles
    if not toggles.get(tool_name, True):
        return False

    return True
```

---

## 7. YouTube Audio Streaming

### 7.1 Alur Kerja YouTube Streamer

```python
# youtube_streamer.py — Server-side audio injection

async def stream_video_to_websocket(websocket, video_id, title, ...):
    # 1. Extract direct audio URL dari YouTube
    source_url, title = await extract_audio_url(video_id)

    # 2. Start playback tracking session
    session = playback_tracker.start_session(user_id, video_id, ...)

    # 3. Jalankan FFmpeg subprocess
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-reconnect", "1",           # Auto reconnect
        "-re", "-i", source_url,      # Input: YouTube audio URL
        "-vn",                         # No video
        "-ac", "1",                    # Mono
        "-ar", "24000",                # 24kHz sample rate
        "-c:a", "libopus",            # Output: Opus codec
        "-b:a", bitrate,              # Adaptive bitrate
        "-frame_duration", "60",      # 60ms frames
        "-f", "ogg",                  # Ogg container
        "pipe:1"                      # Output to stdout
    )

    # 4. Demux Ogg → raw Opus frames
    async for packet in demux_ogg_opus(proc.stdout):
        # 5. Pack ke BinaryProtocol3
        binary_frame = bytearray(4 + len(packet))
        binary_frame[0] = 0x00
        binary_frame[1] = 0x00
        binary_frame[2:4] = len(packet).to_bytes(2, 'big')
        binary_frame[4:] = packet

        # 6. Kirim ke ESP32 via WebSocket
        await websocket.send_bytes(bytes(binary_frame))

        # 7. Pace: 60ms per frame setelah pre-buffer 8 frames
        if frame_idx > 8:
            target_time += 0.060
            sleep_duration = target_time - time.monotonic()
            if sleep_duration > 0:
                await asyncio.sleep(sleep_duration)

    # 8. Send TTS stop signal
    await websocket.send_json({"type": "tts", "state": "stop"})
```

### 7.2 Ogg Demuxer

```python
async def demux_ogg_opus(proc_stdout):
    """Pisahkan raw Opus packets dari Ogg container."""
    buffer = bytearray()
    header_count = 0  # Skip OpusHead + OpusTags

    while True:
        chunk = await proc_stdout.read(4096)
        if not chunk:
            break
        buffer.extend(chunk)

        # Parse Ogg pages
        while len(buffer) >= 27:
            pos = buffer.find(b"OggS")
            # ... parse segment table, extract packets
            # Skip first 2 packets (OpusHead, OpusTags)
            # Yield subsequent packets as raw Opus frames
```

### 7.3 Adaptive Bitrate Resolution

```python
def resolve_adaptive_bitrate(requested_br, rssi):
    """Tentukan bitrate berdasarkan request dan kondisi jaringan."""
    if requested_br and requested_br != "auto":
        return requested_br  # Client minta bitrate spesifik

    # Auto-detect dari RSSI
    if rssi is None:
        return "12k"  # Default

    if rssi >= -65:
        return "16k"
    elif rssi >= -75:
        return "12k"
    elif rssi >= -82:
        return "8k"
    else:
        return "6k"
```

### 7.4 HTTP Streaming Endpoint (untuk ESP32 Polling)

```python
# youtube.py — HTTP chunked stream endpoint

@router.get("/api/audio/stream/{video_id}")
async def stream_audio(video_id: str, br: str = "12k", start: float = 0, ...):
    # Resolve device owner dari MAC
    owner_id = _resolve_owner_for_device(store, device_id)

    # Extract audio URL
    source_url, title = await extract_audio_url(video_id)

    # FFmpeg transcode → raw Opus
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", source_url, "-c:a", "libopus", "-b:a", br, ...
    )

    # Return sebagai StreamingResponse
    return StreamingResponse(
        generate_opus_chunks(proc),
        media_type="audio/ogg",
        headers={"Transfer-Encoding": "chunked"}
    )
```

---

## 8. Device Registry

### 8.1 Data Model

```
registered_devices:
├── id (auto-increment)
├── owner_id (FK → users)
├── device_id (MAC address atau custom ID)
├── device_name (user-defined name)
├── device_type (ESP32, ESP8266, dll)
├── mac_address (normalized MAC)
├── created_at
└── last_seen_at
```

### 8.2 Device Resolution Flow

```python
def _resolve_owner_for_device(store, device_id):
    """Resolve user_id dari device_id atau MAC address."""
    # 1. Exact match di registered_devices
    row = conn.execute(
        "SELECT owner_id FROM registered_devices "
        "WHERE LOWER(device_id) = ? OR LOWER(device_id) = ?",
        (device_id, mac_with_colons)
    ).fetchone()
    if row:
        return row["owner_id"]

    # 2. Match by MAC di audio_queue (fallback)
    row = conn.execute(
        "SELECT owner_id FROM audio_queue "
        "WHERE stream_url LIKE ?",
        (f"%{mac}%",)
    ).fetchone()
    return row["owner_id"] if row else None
```

### 8.3 API Endpoints

| Method | Endpoint | Fungsi |
|--------|----------|--------|
| GET | `/api/devices` | List semua device user |
| GET | `/api/devices/check/{device_id}` | Cek device terdaftar milik siapa |
| POST | `/api/devices/register` | Daftarkan device baru |
| POST | `/api/devices/delete` | Hapus device |

---

## 9. Smart Home (Virtual dan Real Relay)

### 9.1 Virtual Smart Home (Simulasi)

8-channel virtual relay yang diakses via MCP tools:

| Channel | Room | MCP Tool |
|---------|------|----------|
| 1 | Ruang Tamu | `control_relay(1, "on")` |
| 2 | Dapur | `control_smart_home_room("dapur", "on")` |
| 3 | Kamar Mandi | |
| 4 | Kamar Tidur Utama | |
| 5 | Teras | |
| 6 | Basement/Alarm | |
| 7 | Kamar Tidur 2 | |
| 8 | Ruang AC | |

### 9.2 Real Relay (ESP32/8266 Fisik)

```
ESP32/8266 Device
    │
    │ GET /api/device/relay/{slug}/commands
    │ (polling setiap REAL_RELAY_POLL_INTERVAL_MS)
    ▼
Backend
    │
    │ Response: {"commands": [{"channel": 1, "command": "ON"}]}
    ▼
ESP32/8266
    │
    │ POST /api/device/relay/{slug}/status
    │ {"channel": 1, "state": true}
    ▼
Backend → Dashboard update
```

### 9.3 Mode Switching

```
Virtual Mode ON → Virtual relay tools aktif, Real relay tools diblokir
Virtual Mode OFF → Real relay tools aktif, Virtual relay tools diblokir

Cross-mode fallback:
Jika user panggil virtual tool tapi mode real aktif →
  Backend otomatis routing ke real relay (match by voice command)
```

---

## 10. External Information Services

### 10.1 Weather (Open-Meteo)

```python
def get_weather_data(city):
    # 1. Geocoding: city name → lat/lon
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city}"
    # 2. Forecast: lat/lon → weather data
    forecast_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    # Returns: temperature, humidity, wind, WMO weather code (Indonesian)
```

**Alasan pakai Open-Meteo:** Gratis, tanpa API key, akurat, support bahasa Indonesia.

### 10.2 BMKG Earthquake

```python
def get_earthquake_data():
    # Scrape dari BMKG langsung
    url = "https://www.bmkg.go.id/gempabumi/gempabumi-terkini.xml"
    # Parse XML → structured data
    # Returns: magnitude, location, depth, time
```

### 10.3 Currency Conversion

```python
def convert_currency(amount, from_currency, to_currency):
    # Pakai exchange rate API
    # Returns: converted amount dengan rate terkini
```

### 10.4 Wikipedia

```python
def search_wikipedia(query):
    # Wikipedia API bahasa Indonesia
    url = f"https://id.wikipedia.org/api/rest_v1/page/summary/{query}"
    # Returns: title, extract, thumbnail
```

### 10.5 KBBI

```python
def lookup_kbbi(word):
    # Scraping dari kbbi.web.id
    # Returns: arti, kelas kata, bentuk tidak baku
```

---

## 11. Playback Tracker

### 11.1 Data Model

```python
@dataclass
class PlaybackSession:
    session_id: str          # UUID unique
    user_id: int             # Owner user ID
    username: str            # Owner username
    video_id: str            # YouTube video ID
    title: str               # Song title
    stream_type: str         # "HTTP Stream" atau "WebSocket"
    device_mac: str          # Target device MAC
    bitrate: str             # Current bitrate (6k/8k/12k/16k)
    started_at: float        # Timestamp start
    last_active_at: float    # Last chunk timestamp
    bytes_streamed: int      # Total bytes sent
    abort_event: Event       # Abort signal
```

### 11.2 Session Lifecycle

```
1. start_session() → create session, end any existing session for user
2. record_chunk(bytes) → increment bytes_streamed, update last_active_at
3. end_session() → cleanup, save to last_played history
```

### 11.3 Real-time Monitoring

```python
# Dashboard bisa melihat siapa yang sedang streaming
playback_tracker.get_all_active_sessions()
# Returns: [{session_id, user_id, video_id, title, device_mac, bitrate, ...}]
```

---

## 12. Database Layer

### 12.1 Store Abstraction

```python
# store.py — Abstract interface
class Store:
    # User management
    def create_user(username, password, role)
    def find_user_by_username(username)
    def get_user_features(user_id)

    # Materials (knowledge base)
    def search_materials(user_id, keyword, limit)
    def list_material_database(user_id, keyword, category, limit)
    def find_material_detail(user_id, material_id, title, keyword)

    # Chat history
    def upsert_chat_transcript(user_id, tool_name, user_message, xiaozhi_answer)
    def list_chat_history(user_id, query, limit, semantic)

    # Devices
    def register_device(user_id, device_id, name, device_type)
    def list_registered_devices(user_id)
    def find_device_by_id(device_id)

    # Smart home
    def get_relay_state(user_id, channel)
    def set_relay_state(user_id, channel, state)

    # MCP
    def get_xiaozhi_token(user_id)
    def set_xiaozhi_token(user_id, token)

    # User persona
    def save_user_preference(user_id, category, key, value)
    def get_user_persona(user_id, category)
```

### 12.2 Factory Pattern

```python
# factory.py — Auto-detect backend
def create_store():
    if os.getenv("HF_TOKEN"):
        return HuggingFaceStore()  # HF Spaces mode
    else:
        return SQLiteStore()       # Local VPS mode
```

---

## 13. Security dan Authentication

### 13.1 Token Encryption

```python
# MCP token (wss:// URL) dienkripsi sebelum disimpan
from cryptography.fernet import Fernet

key = os.getenv("DATA_ENCRYPTION_KEY")
f = Fernet(key)
encrypted_token = f.encrypt(token.encode())
```

### 13.2 Token Hashing

```python
# Token hash untuk lookup tanpa decrypt
def xiaozhi_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:MCP_TOKEN_HASH_LENGTH]
```

### 13.3 CSRF Protection

```python
# Setiap form POST butuh CSRF token
def validate_csrf(request, csrf_token, user):
    serializer = URLSafeTimedSerializer(APP_SECRET_KEY)
    serializer.loads(csrf_token, max_age=3600)
```

### 13.4 Rate Limiting

```python
# Rate limit untuk endpoint sensitif
@rate_limit(max_requests=10, window_seconds=60)
async def search_account(...):
    ...
```

---

## 14. Alur Kerja Lengkap

### 14.1 User Setup Flow

```
1. User buka dashboard (https://xiaozhiscig.biz.id)
2. Register / Login
3. Simpan MCP token (wss://api.xiaozhi.me/mcp/?token=xxx)
4. Backend mulai MCP bridge task untuk user ini
5. Daftarkan ESP32 device (MAC address)
6. Mulai bicara ke ESP32 → AI → MCP tools aktif
```

### 14.2 Voice Command → YouTube Playback

```
1. User: "Putar Despacito" (bicara ke ESP32 mic)
2. ESP32: Opus encode → WebSocket → Xiaozhi Cloud
3. Xiaozhi Cloud: STT → LLM decide → MCP tools/call play_youtube_song
4. MCP message: {"method":"tools/call","params":{"name":"play_youtube_song","arguments":{"query":"Despacito"}}}
5. MCP Bridge (Backend) → WebSocket ke wss://api.xiaozhi.me
6. Backend execute play_youtube_song:
   a. YouTube search → video_id
   b. Extract audio URL (yt_dlp)
   c. Cek: ada WebSocket session aktif?
      - Ya → stream via WebSocket (binary injection)
      - Tidak → simpan ke audio_queue (ESP32 polling)
7. ESP32 poll: GET /commands?mac=XX → terima {video_id, title}
8. ESP32: GET /stream/<vid>?br=12k → HTTP chunked Opus
9. Backend: FFmpeg transcode → Opus → chunked response
10. ESP32: demux → decode → speaker
11. PlaybackTracker: record session, bytes, duration
```

### 14.3 Smart Home Voice Control

```
1. User: "Nyalakan lampu kamar"
2. ESP32 → Xiaozhi Cloud → MCP tools/call control_smart_home_room
3. Backend: parse "kamar" → channel 4 (Kamar Tidur Utama)
4. Virtual mode: set relay state → dashboard update
   Real mode: simpan command → ESP32/8266 poll → activate relay
5. Response: "Kamar Tidur Utama berhasil dinyalakan"
```

---

## 15. Daftar 36 MCP Tools

### 1. Pembelajaran & Akademik (Study Suite)

| Tool | Fungsi | Parameter |
|------|--------|-----------|
| `solve_study_problem` | Pemecah soal bertahap | problem: str |
| `explain_concept` | Penjelas konsep 2 level | concept: str |
| `quiz_me` | Kuis interaktif | topic: str |
| `lookup_formula` | Kamus rumus cepat | subject, formula_name |
| `academic_english_helper` | Proofreading grammar | text: str |
| `lookup_kbbi` | KBBI dictionary | word: str |
| `search_wikipedia` | Wikipedia search | query: str |

### 2. Analisis Spesialis

| Tool | Fungsi | Parameter |
|------|--------|-----------|
| `detect_logical_fallacy` | Deteksi cacat logika | argument: str |
| `identify_cognitive_bias` | Analisis bias kognitif | situation: str |
| `it_code_and_architecture_helper` | Konsultasi IT | question: str |

### 3. Doa & Ibadah Lintas Agama

| Tool | Fungsi | Parameter |
|------|--------|-----------|
| `lookup_scripture_and_verse` | Cari ayat kitab suci | religion, book, verse |
| `get_prayer_and_worship_guide` | Panduan doa/ibadah | religion, prayer_type |

### 4. Realtime Cuaca, Gempa, Kurs

| Tool | Fungsi | Parameter |
|------|--------|-----------|
| `get_weather` | Cuaca realtime per kota | city: str |
| `get_earthquake_info` | Info gempa BMKG | - |
| `convert_currency` | Konversi mata uang | amount, from, to |

### 5. Knowledge Base Pribadi

| Tool | Fungsi | Parameter |
|------|--------|-----------|
| `search_course_materials` | Cari materi | search_keyword: str |
| `read_live_api_data` | Baca data API realtime | search_keyword: str |
| `read_material_database` | Baca database materi | keyword, category, limit |
| `read_material_detail` | Detail satu materi | material_id, title |

### 6. Smart Home

| Tool | Fungsi | Parameter |
|------|--------|-----------|
| `control_relay` | Kontrol relay virtual | channel: int, action: str |
| `control_smart_home_room` | Kontrol by ruangan | target: str, action: str |
| `get_relay_status` | Status semua relay | - |
| `all_relays_on` | Nyalakan semua | - |
| `all_relays_off` | Matikan semua | - |
| `control_real_relay_by_voice` | Kontrol relay fisik | user_message: str |
| `get_real_relay_status` | Status relay fisik | - |
| `all_real_relays_on` | Nyalakan semua fisik | - |
| `all_real_relays_off` | Matikan semua fisik | - |

### 7. Multimedia, Utilitas & Memori

| Tool | Fungsi | Parameter |
|------|--------|-----------|
| `play_youtube_song` | Putar YouTube audio | query: str |
| `search_web` | Pencarian web | query: str |
| `search_news` | Pencarian berita | query: str |
| `calculate` | Kalkulator | expression: str |
| `translate_text` | Penerjemah | text, target_language |
| `set_reminder` | Set pengingat | message, time |
| `save_chat_history` | Simpan chat | user_message, xiaozhi_answer |
| `recall_chat_memory` | Ingat chat lalu | query: str, limit: int |
| `remember_user_profile` | Simpan preferensi | key, value, category |
| `get_user_profile` | Ambil profil | category: str |
| `get_registered_devices` | List device terdaftar | - |

---

## 16. API Endpoints

### 16.1 Audio Streaming

| Method | Endpoint | Fungsi |
|--------|----------|--------|
| GET | `/api/audio/stream/{video_id}?br=12k` | HTTP chunked audio stream |
| GET | `/api/audio/play_direct?q=despacito` | Search + redirect to stream |
| WebSocket | `/ws/youtube/{video_id}` | WebSocket audio injection |
| GET | `/api/device/audio/commands?mac=XX` | Poll audio commands |
| POST | `/api/device/audio/ack` | Acknowledge command |
| POST | `/api/device/audio/status` | Report playback status |

### 16.2 Device Management

| Method | Endpoint | Fungsi |
|--------|----------|--------|
| GET | `/api/devices` | List user devices |
| GET | `/api/devices/check/{device_id}` | Check device ownership |
| POST | `/api/devices/register` | Register new device |
| POST | `/api/devices/delete` | Delete device |

### 16.3 MCP Management

| Method | Endpoint | Fungsi |
|--------|----------|--------|
| POST | `/save_mcp` | Save MCP token |
| POST | `/delete_mcp` | Delete MCP endpoint |
| POST | `/reconnect_mcp` | Force reconnect |
| POST | `/api/mcp/check` | Check endpoint ownership |
| GET | `/api/mcp/status` | MCP connection status |

### 16.4 Smart Home (Real Relay)

| Method | Endpoint | Fungsi |
|--------|----------|--------|
| GET | `/api/device/relay/{slug}/commands` | Poll relay commands |
| POST | `/api/device/relay/{slug}/status` | Report relay status |

### 16.5 Chat & Materials

| Method | Endpoint | Fungsi |
|--------|----------|--------|
| GET | `/api/v1/chat/history` | Chat history |
| POST | `/api/v1/chat/send` | Send chat message |
| GET | `/api/v1/materials` | List materials |
| POST | `/api/v1/materials` | Create material |
| GET | `/api/v1/materials/{id}` | Get material detail |

---

## 17. Struktur File dan Direktori

```
HUGGING XIAOZHI/
├── README.md                    # Project overview + setup
├── SETUP.md                     # Detailed setup guide
├── DOMAINS.md                   # Domain configuration
├── Dockerfile                   # HF Spaces deployment
├── requirements.txt             # Python dependencies
├── app.py                       # Entry point (HF Spaces)
│
├── xiaozhi/
│   ├── main.py                  # FastAPI app initialization
│   ├── config.py                # Environment configuration
│   ├── dependencies.py          # Dependency injection
│   │
│   ├── mcp/                     # MCP Protocol
│   │   ├── server.py            # FastMCP server (36 tools)
│   │   ├── tools.py             # Tool implementations
│   │   ├── bridge.py            # WebSocket bridge
│   │   └── context.py           # Context variables
│   │
│   ├── routers/                 # API endpoints
│   │   ├── youtube.py           # YouTube streaming
│   │   ├── devices.py           # Device registry
│   │   ├── mcp_endpoints.py     # MCP management
│   │   ├── relay_nyata.py       # Real relay polling
│   │   ├── chat.py              # Chat pages
│   │   ├── dashboard.py         # Dashboard
│   │   ├── admin.py             # Admin panel
│   │   ├── auth.py              # Authentication
│   │   ├── smarthome.py         # Smart home pages
│   │   ├── search.py            # Web/news search
│   │   ├── api_v1_chat.py       # Chat API
│   │   ├── api_v1_mcp.py        # MCP API
│   │   ├── api_v1_smarthome.py  # Smart home API
│   │   ├── api_v1_materials.py  # Materials API
│   │   ├── api_v1_auth.py       # Auth API
│   │   └── google_auth.py       # Google OAuth
│   │
│   ├── services/                # Business logic
│   │   ├── youtube_streamer.py  # FFmpeg → Opus streamer
│   │   ├── mcp_service.py       # MCP state management
│   │   ├── playback_tracker.py  # Playback session tracker
│   │   ├── external_info_service.py # Weather, BMKG, etc.
│   │   ├── smarthome_service.py # Smart home logic
│   │   ├── semantic_memory_service.py # Semantic search
│   │   ├── study_service.py     # Study tools
│   │   ├── religious_service.py # Religious tools
│   │   ├── translator_service.py # Translation
│   │   ├── calculator_service.py # Calculator
│   │   ├── scraper_service.py   # Web scraping
│   │   ├── pdf_service.py       # PDF processing
│   │   ├── reminder_service.py  # Reminders
│   │   ├── sse_service.py       # Server-Sent Events
│   │   └── community_chat_service.py # Community chat
│   │
│   ├── database/                # Data layer
│   │   ├── store.py             # Abstract store interface
│   │   ├── sqlite_store.py      # SQLite implementation
│   │   ├── factory.py           # Store factory
│   │   └── helpers.py           # DB utilities
│   │
│   └── core/                    # Core utilities
│       ├── security.py          # Encryption, hashing, CSRF
│       ├── utils.py             # General utilities
│       ├── monitor.py           # System monitoring
│       ├── task_queue.py        # Background tasks
│       ├── cache.py             # Caching
│       ├── rate_limiter.py      # Rate limiting
│       ├── i18n.py              # Internationalization
│       └── api_docs.py          # API documentation
│
├── templates/                   # HTML templates (Jinja2)
│   ├── base.html                # Base layout
│   └── chat.html                # Chat interface
│
├── data/                        # Local data
│   └── xiaozhi.db               # SQLite database (VPS mode)
│
└── .hf_db_cache/                # HF Dataset cache
    └── app_data.json            # Cached data (dev mode)
```

---

## 18. Konfigurasi dan Environment

### 18.1 Environment Variables

| Variable | Required | Default | Fungsi |
|----------|----------|---------|--------|
| `ENVIRONMENT` | Ya | `production` | Mode deployment |
| `HF_TOKEN` | Ya (Spaces) | - | Hugging Face write token |
| `HF_DATASET_REPO` | Opsional | `username/xiaozhi-indonesia-db` | HF Dataset repo |
| `APP_SECRET_KEY` | Ya | generated | Session signing, CSRF |
| `DATA_ENCRYPTION_KEY` | Ya | - | Fernet key untuk token encryption |
| `COOKIE_SECURE` | Spaces | `false` | HTTPS cookie flag |
| `EDUSMART_API_KEY` | Opsional | - | Course materials API |
| `ALLOWED_HOSTS` | Opsional | `*` | CORS allowed hosts |
| `REAL_RELAY_POLL_INTERVAL_MS` | Opsional | `1000` | Relay polling interval |
| `ADMIN_USERNAME` | Opsional | `admin` | Admin account username |
| `ADMIN_PASSWORD` | Opsional | generated | Admin account password |

### 18.2 Generate Secrets

```bash
# APP_SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"

# DATA_ENCRYPTION_KEY (Fernet)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 18.3 Deployment Modes

| Mode | Database | Config Source | Use Case |
|------|----------|---------------|----------|
| HF Spaces | HF Dataset (JSON) | Space Secrets | Cloud deployment |
| VPS Local | SQLite file | `.env` file | Self-hosted |
| Development | SQLite file | `.env` file | Local testing |

---

## 19. Troubleshooting

| Masalah | Cek | Solusi |
|---------|-----|--------|
| MCP bridge tidak connect | Token format | Harus `wss://api.xiaozhi.me/mcp/?token=xxx` |
| MCP bridge disconnect | WebSocket timeout | Cek internet, auto-reconnect 2 detik |
| YouTube stream gagal | FFmpeg installed? | `apt install ffmpeg` atau `pip install imageio-ffmpeg` |
| YouTube extract gagal | yt_dlp updated? | `pip install --upgrade yt-dlp` |
| Device tidak terima command | MAC address match | Cek `/api/devices/check/<mac>` |
| Smart home tidak jalan | Mode setting | Cek virtual vs real relay mode di dashboard |
| HF Dataset error | HF_TOKEN | Pastikan token punya write access |
| CORS error | ALLOWED_HOSTS | Tambah domain ke allowed hosts |
| Token encryption error | DATA_ENCRYPTION_KEY | Pastikan key konsisten (tidak berubah) |
| Admin login gagal | ADMIN_PASSWORD | Cek environment variable atau logs |

---

## Catatan untuk Penelitian

### Kontribusi ke 3-Layer Architecture

| Layer | Komponen di Backend | Metrics yang Bisa Diukur |
|-------|--------------------|-----------------------|
| Layer 1 (AI Communication) | MCP Bridge (bridge.py) | MCP message latency, tool execution time, bridge uptime |
| Layer 2 (Backend Networking) | Device Registry (devices.py), Command Queue | Device resolution time, command delivery latency, concurrent connections |
| Layer 3 (IoT Media Delivery) | YouTube Streamer (youtube_streamer.py), HTTP Stream | Stream startup time, throughput, jitter, session duration |

### Variabel Penelitian

| Variabel | Sumber Data | Cara Ukur |
|----------|------------|-----------|
| MCP tool invocation latency | `mcp_service.py` timestamps | Waktu dari tool call request ke response |
| Command delivery latency | `youtube.py` + ESP32 timestamps | Waktu dari MCP call ke ESP32 poll receive |
| Stream initial latency | `youtube_streamer.py` + ESP32 | Waktu dari stream request ke frame pertama di-decode |
| Throughput | `playback_tracker.py` bytes_streamed | Bytes/detik selama streaming |
| Buffer starvation | ESP32 counters | Berapa kali decode queue kosong |
| Concurrent sessions | `playback_tracker.py` active_sessions | Berapa device streaming bersamaan |
| Device resolution time | `youtube.py` `_resolve_owner_for_device` | Waktu resolve MAC → owner_id |

---

## Alasan Backend Ini Penting untuk Skripsi

Backend ini bukan sekadar "server YouTube". Ini adalah **Intelligent Communication Gateway** yang:

1. **Menghubungkan AI Agent ke IoT**: MCP bridge mengubah voice commands menjadi tool calls yang bisa mengontrol hardware
2. **Device-Aware Routing**: MAC-based identification memastikan stream dikirim ke device yang benar
3. **Adaptive Media Delivery**: FFmpeg transcoding + adaptive bitrate mengatasi keterbatasan ESP32
4. **Multi-User Multi-Device**: Setiap user punya bridge, setiap device punya command queue
5. **Session Tracking**: Real-time monitoring siapa yang streaming ke mana

Semua ini berjalan di atas protokol jaringan yang bisa diukur dan dianalisis — menjadikannya topik skripsi yang kuat untuk peminatan Jaringan.

---

*Dokumentasi ini dibuat berdasarkan analisis source code: `xiaozhi/mcp/server.py`, `xiaozhi/mcp/tools.py`, `xiaozhi/mcp/bridge.py`, `xiaozhi/services/youtube_streamer.py`, `xiaozhi/services/playback_tracker.py`, `xiaozhi/routers/youtube.py`, `xiaozhi/routers/devices.py`, `xiaozhi/routers/mcp_endpoints.py`, `xiaozhi/config.py`, `xiaozhi/main.py`, `README.md`*


---

## 20. Panduan Hardware: ESP32-S3 N16R8 (CAM & Standar Non-CAM)

Tersedia dokumentasi khusus untuk hardware ESP32-S3 N16R8 pada file terpisah: [`PANDUAN_HARDWARE_ESP32_S3_N16R8.md`](./PANDUAN_HARDWARE_ESP32_S3_N16R8.md) serta pada Web Portal Dokumentasi (`/documentation` tab **ESP32-S3 Hardware & Tombol**).

### Rangkuman Cepat:
- **Kompatibilitas:** ESP32-S3 N16R8 CAM & ESP32-S3 N16R8 DevKitC-1 biasa.
- **Fail-safe Kamera:** Driver tidak crash jika modul kamera tidak dipasang pada varian standar.
- **Alur Setup Web Portal (`192.168.4.1`):** Konfigurasi SSID Wi-Fi, modul layar (ST7789 / OLED / Headless), audio I2S (INMP441 + MAX98357A), dan status kamera.
- **Fungsi Tombol Fisik:**
  - **BOOT (GPIO 0):** 1x Klik (Bicara / Stop YouTube / Interupsi), 2x Klik (Auto Play Lagu Remix), Long Press (Reset Wi-Fi Web Portal).
  - **VOL UP (GPIO 14):** 1x Klik (Volume +10% / Zoom In), 2x Klik (Rotasi Layar 180°), Long Press (Volume 100%).
  - **VOL DOWN (GPIO 46):** 1x Klik (Volume -10% / Zoom Out), 2x Klik (Switch Mode XiaoZhi ↔ Chronchi Smartwatch), Long Press (Mute 0%).
- **MCP Tools Internal:** `self.get_hardware_specs` & `self.get_buttons_guide`.


---

## 21. Panduan Hardware: ESP32-C3 Super Mini / Pro

Tersedia dokumentasi khusus untuk hardware ESP32-C3 Super Mini pada file terpisah: [`PANDUAN_HARDWARE_ESP32_C3_SUPER_MINI.md`](./PANDUAN_HARDWARE_ESP32_C3_SUPER_MINI.md) serta pada Web Portal Dokumentasi (`/documentation` tab **ESP32-C3 Super Mini**).

### Rangkuman Cepat:
- **Arsitektur:** RISC-V 32-bit Single-Core @ 160MHz, 4MB Flash, ~400KB internal SRAM (Shared I2S Clock).
- **Wiring Shared Clock:** Clock I2S dibagi bersama: `GPIO 5` (BCLK) & `GPIO 6` (WS/LRC) untuk INMP441 & MAX98357A. Layar OLED I2C di `GPIO 0` (SDA) & `GPIO 10` (SCL).
- **Gestur Tombol Utama (GPIO 3):**
  - **Klik 1x:** Bicara manual / Push to Talk / Stop musik & interupsi suara AI.
  - **Klik 2x Cepat:** Beralih mode ke **Chronchi**; klik 2x cepat lagi untuk kembali ke **XiaoZhi AI**.
  - **Tahan 5 Detik:** Masuk ke mode **Konfigurasi Wi-Fi** (`192.168.4.1`).
- **Hands-Free & Suara:** Dukungan deteksi hening (VAD) otomatis dan pemutaran lagu lokal offline (SPIFFS).
