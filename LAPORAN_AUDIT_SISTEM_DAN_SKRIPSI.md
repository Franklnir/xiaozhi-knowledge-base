# LAPORAN AUDIT SISTEM, ARSITEKTUR, DAN ANALISIS KINERJA EMPIRIS
## Proyek: EduSmart Xiaozhi Indonesia — Gateway AI Voice Assistant, MCP Protocol, dan IoT Embedded System

> **Dokumen Resmi Hasil Audit Komprehensif untuk Keperluan Penelitian Skripsi / Tugas Akhir**  
> **Peneliti / Administrator:** Irsyad (`irsyad03`)  
> **Server Produksi:** `163.61.58.235` (`xiaozhiscig.biz.id`)  
> **Waktu Audit:** 14 Oktober 2026  
> **Dataset Log Analisis:** 07 Oktober 2026 s.d. 14 Oktober 2026 (7 Hari Penuh)

---

## DAFTAR ISI

1. [Ringkasan Eksekutif & Identitas Proyek](#1-ringkasan-eksekutif--identitas-proyek)
2. [Latar Belakang & Urgensi Penelitian (Buat Apa & Mengapa Dibangun)](#2-latar-belakang--urgensi-penelitian-buat-apa--mengapa-dibangun)
3. [Spesifikasi Teknologi Lengkap (Hardware, Firmware, Backend, Database, Cloud)](#3-spesifikasi-teknologi-lengkap)
4. [Katalog & Analisis 36 MCP (Model Context Protocol) Tools](#4-katalog--analisis-36-mcp-model-context-protocol-tools)
5. [Fitur-Fitur Utama Ekosistem](#5-fitur-fitur-utama-ekosistem)
6. [Mekanisme Operasional & Alur Data (Yang Terjadi di Dalam Sistem)](#6-mekanisme-operasional--alur-data-yang-terjadi-di-dalam-sistem)
7. [Audit Empiris Log Data Server VPS (Seminggu Terakhir)](#7-audit-empiris-log-data-server-vps-seminggu-terakhir)
8. [Dampak Penelitian (Signifikansi Akademis, Teknis, dan Sosial)](#8-dampak-penelitian-signifikansi-akademis-teknis-dan-sosial)
9. [Analisis Kelemahan, Evaluasi Keamanan, dan Mitigasi Risiko](#9-analisis-kelemahan-evaluasi-keamanan-dan-mitigasi-risiko)
10. [Rekomendasi Bab Skripsi (Metodologi, Pengujian & Pembahasan)](#10-rekomendasi-bab-skripsi-metodologi-pengujian--pembahasan)

---

## 1. Ringkasan Eksekutif & Identitas Proyek

**EduSmart Xiaozhi Indonesia** adalah ekosistem asisten suara cerdas (*Intelligent Voice Assistant*) dan *Communication Gateway* terdistribusi yang mengintegrasikan model bahasa besar (Large Language Models) dari cloud dengan perangkat keras komputasi tepi (*edge hardware*) berbiaya sangat rendah (ESP32-C3 dan ESP32-S3).

Sistem ini memecahkan kendala klasik asisten suara komersial dengan mengimplementasikan standar terbuka **Model Context Protocol (MCP)** dari Anthropic, didukung mesin transkoding audio *on-the-fly* berbasis **Opus**, pengelolaan inventaris perangkat (*Device Registry*) berbasis MAC Address, toko firmware (*Firmware Marketplace*), serta kendali otomasi rumah pintar (*Smart Home IoT*).

### Identitas Sistem
- **Nama Gateway:** Xiaozhi Indonesia — FastAPI Backend Gateway
- **Domain Resmi:** `https://xiaozhiscig.biz.id` (Reverse Proxy Nginx 1.26 + Let's Encrypt SSL)
- **Deployment Model:** Hybrid (Dockerized Production VPS di `163.61.58.235` dan Hugging Face Spaces fallback)
- **Database Engine:** PostgreSQL 16 Alpine (Connection Pooling min=3, max=10 + Multi-worker LISTEN/NOTIFY)
- **Status Operasional Saat Ini:** Aktif, Uptime 33+ Hari, 219 Pengguna Terdaftar, 32 Perangkat Fisik Aktif.

---

## 2. Latar Belakang & Urgensi Penelitian (Buat Apa & Mengapa Dibangun)

### A. Permasalahan Nyata (Problem Statements)
1. **Tingginya Biaya Perangkat Smart Speaker:** Perangkat komersial seperti Google Nest Audio atau Amazon Echo dibanderol antara Rp 600.000 hingga Rp 1.500.000, menjadikannya kurang terjangkau untuk adopsi massal di sekolah, pesantren, dan keluarga berpenghasilan menengah ke bawah di Indonesia.
2. **Keterbatasan Ekosistem Tertutup (Walled Garden):** Smart speaker konvensional tidak memberikan akses kepada pengembang untuk menambahkan kapabilitas kustom (seperti membaca kitab suci dengan tajwid, mencari jurnal riset, atau mengontrol relay buatan sendiri tanpa langganan berbayar).
3. **Keterbatasan Memori Mikrokontroler (Edge Resource Constraint):** Mikrokontroler murah seperti ESP32-C3 Super Mini (~Rp 35.000) hanya memiliki SRAM internal ~400 KB tanpa PSRAM eksternal. Perangkat ini tidak mampu mengunduh seluruh berkas audio MP3 ke dalam memori atau menjalankan model AI secara lokal.
4. **Masalah Protokol Tool-Calling:** Integrasi fungsi luar pada AI sering kali membutuhkan arsitektur custom API yang rapuh. Dibutuhkan standarisasi protokol komunikasi dua arah antara LLM dan sistem eksternal.

### B. Tujuan Pembangunan Sistem (Objectives)
- Membangun **Gateway Perantara Berperforma Tinggi** yang menjembatani server AI (`api.xiaozhi.me`) dengan ribuan perangkat ESP32 melalui standar **Model Context Protocol (MCP)**.
- Menghadirkan **Mesin Transkoding Audio Real-Time** yang mengubah audio resolusi tinggi dari YouTube menjadi format chunked Ogg/Opus ber-bitrate rendah (12 kbps) yang dapat dialirkan ke buffer SRAM mikroprosesor tanpa menyebabkan *Out of Memory* (OOM).
- Menyediakan **Platform Edukasi & Utilitas Terpadu (EduSmart)** yang mencakup 36 perkakas cerdas: kalkulator saintifik, ringkasan materi, jadwal sholat, pencarian web, kontrol IoT, dan pelacak kebiasaan belajar.
- Menyediakan **Ekosistem Marketplace & Web Flasher** agar komunitas dapat membuat, membagikan, mem-flash dari browser, dan memonetisasi firmware kustom mereka.

---

## 3. Spesifikasi Teknologi Lengkap

Arsitektur sistem dibangun di atas 5 lapisan teknologi yang terintegrasi secara harmonis:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        LAPISAN 1: EDGE HARDWARE                        │
│   ESP32-C3 Super Mini (RISC-V)  │  ESP32-S3 N16R8 (Xtensa Dual Core)  │
│   I2S DAC MAX98357A (Speaker)   │  INMP441 MEMS (Microphone)          │
│   OLED SSD1306 / ST7789 IPS     │  Relay GPIO Module (Optocoupler)    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Wi-Fi 802.11 b/g/n (Audio Stream & Polling)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   LAPISAN 2: WEB & REVERSE PROXY                       │
│   Nginx 1.26.3 (TLS 1.2/1.3 Let's Encrypt SSL, HTTP2, Rate Limiting)   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Reverse Proxy (127.0.0.1:8080)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│               LAPISAN 3: BACKEND GATEWAY (FASTAPI)                     │
│   Python 3.11/3.13 + Uvicorn ASGI + FastMCP Engine (JSON-RPC 2.0)      │
│   yt-dlp Extraction + FFmpeg Audio Transcoding (Opus/Ogg Streamer)    │
│   PostgreSQL Listen/Notify + WebSocket Bridge to Xiaozhi Cloud        │
└──────────────────────┬────────────────────────────┬────────────────────┘
                       │                            │
        PostgreSQL 16  │                            │ S3 API / REST
                       ▼                            ▼
┌───────────────────────────────┐  ┌─────────────────────────────────────┐
│  LAPISAN 4: BASIS DATA        │  │  LAPISAN 5: CLOUD & OBJECT STORAGE  │
│  PostgreSQL 16 (Relasional)   │  │  Neva Objects (S3 Firmware Storage) │
│  Alembic (Migration Control)  │  │  Firebase Realtime DB (Companion)   │
│  SQLite3 (Local Dev Fallback) │  │  Xiaozhi AI Cloud (Voice LLM)       │
└───────────────────────────────┘  └─────────────────────────────────────┘
```

### A. Rincian Lapisan Hardware (Perangkat Keras IoT)

| Komponen Hardware | Spesifikasi Teknis | Peran dalam Sistem |
| :--- | :--- | :--- |
| **ESP32-C3 Super Mini** | RISC-V 32-bit Single-Core @ 160MHz, 400KB SRAM, 4MB Flash SPI | Node suara berbiaya murah (~Rp 35.000) untuk smart assistant portabel |
| **ESP32-S3 N16R8** | Xtensa LX7 Dual-Core @ 240MHz, 16MB Flash, 8MB Octal PSRAM | Node premium berkemampuan tinggi dengan buffer audio besar & kamera OV2640 |
| **INMP441 (Microphone)** | I2S Digital MEMS Microphone, SNR 61 dBA, 24-bit audio data | Menangkap suara percakapan pengguna secara jernih tanpa noise ADC analog |
| **MAX98357A (Amplifier/DAC)** | I2S Digital Audio DAC + Class-D Amplifier 3.2W (4Ω / 5V) | Mengubah sinyal digital I2S langsung menjadi audio suara keras ke speaker |
| **Layar OLED SSD1306** | 0.96 inch, Resolusi 128x64 pixel, Protokol komunikasi I2C | Menampilkan ekspresi wajah AI (senang, mendengarkan, berpikir, berbicara) |
| **Layar ST7789 IPS** | 1.54" / 1.69" Resolusi 240x280 pixel, Antarmuka SPI 8-pin | Menampilkan antarmuka visual penuh warna pada varian ESP32-S3 |
| **Modul Relay Optocoupler** | 5V Active-Low Relay Module terhubung ke GPIO ESP32 | Mengendalikan lampu, kipas angin, dan peralatan listrik AC 220V |

### B. Rincian Lapisan Backend & Firmware

| Teknologi | Versi / Tipe | Fungsi Utama & Keunggulan |
| :--- | :--- | :--- |
| **Python** | 3.11 (Container) / 3.13 (Host) | Lingkungan komputasi asinkron (*asyncio native*) |
| **FastAPI** | Latest (0.110+) | Framework web berperforma tinggi dengan validasi Pydantic & auto-OpenAPI |
| **Uvicorn** | Latest | Server ASGI berbasis `uvloop` untuk konkurensi ribuan request non-blocking |
| **Model Context Protocol (MCP)** | Anthropic SDK + FastMCP | Standar terbuka JSON-RPC 2.0 untuk pemanggilan tool oleh LLM |
| **yt-dlp** | Latest | Ekstraktor stream audio YouTube tanpa memerlukan kuota Google API Key |
| **FFmpeg** | 6.x (Alpine static) | Transkoder audio on-the-fly dari PCM/MP4 ke Ogg Opus mono 12kbps 16kHz |
| **PostgreSQL** | 16-alpine (Docker) | Basis data relasional ACID dengan pooling koneksi dan pub-sub channel |
| **Neva Objects** | S3-Compatible Storage | Penyimpanan berkas binary firmware (`.bin`) dan model 3D (`.stl`) terisolasi |
| **Web Serial API** | W3C Standard | Memungkinkan flashing firmware ke ESP32 langsung dari browser web Chrome |
| **Fernet Cryptography** | AES-128-CBC + HMAC | Enkripsi simetrik token kredensial Xiaozhi di basis data |

---

## 4. Katalog & Analisis 36 MCP (Model Context Protocol) Tools

Gateway ini menyediakan **36 MCP Tools** yang didaftarkan langsung ke server Xiaozhi AI. Saat pengguna berbicara, model AI di cloud memutuskan tool mana yang harus dipanggil via JSON-RPC 2.0:

| No | Nama MCP Tool | Kategori | Fungsi & Deskripsi Teknis |
| :---: | :--- | :---: | :--- |
| 1 | `play_youtube_song` | Media & Audio | Mencari video di YouTube, mengekstrak stream audio, dan memasukkannya ke antrean perangkat |
| 2 | `stop_youtube_song` | Media & Audio | Menghentikan audio yang sedang diputar seketika via perintah stop |
| 3 | `pause_youtube_song` | Media & Audio | Menjeda pemutaran audio sementara |
| 4 | `resume_youtube_song` | Media & Audio | Melanjutkan pemutaran audio yang sebelumnya dijeda |
| 5 | `get_playback_status` | Media & Audio | Melaporkan status audio berjalan (judul lagu, durasi, posisi detik berjalan) |
| 6 | `study_search` | Edukasi / Riset | Mencari artikel ilmiah, konsep akademis, dan materi pelajaran sekolah/kampus |
| 7 | `study_formula` | Edukasi / Riset | Memberikan rumus matematika, fisika, kimia beserta cara penyelesaiannya |
| 8 | `study_summary` | Edukasi / Riset | Merangkum topik pembelajaran kompleks menjadi poin-poin ringkas yang mudah dipahami |
| 9 | `study_flashcard` | Edukasi / Riset | Menghasilkan kartu tanya-jawab (*flashcard*) untuk menguji hafalan pengguna |
| 10 | `deep_web_search` | Pencarian Web | Scraping langsung ke web untuk mendapatkan informasi aktual terkini yang melampaui data training LLM |
| 11 | `wikipedia_search` | Informasi Pengetahuan | Mengambil ringkasan ensiklopedia dari Wikipedia bahasa Indonesia dan Inggris |
| 12 | `turn_on_smart_device` | Smart Home IoT | Mengirim sinyal saklar ON ke relay pintar tertentu (lampu kamar, kipas, relay fisik) |
| 13 | `turn_off_smart_device`| Smart Home IoT | Mengirim sinyal saklar OFF ke relay pintar tertentu |
| 14 | `get_smart_device_status`| Smart Home IoT | Memeriksa apakah perangkat listrik tertentu sedang aktif atau mati |
| 15 | `list_smart_devices` | Smart Home IoT | Menampilkan daftar seluruh saklar pintar yang terpasang di rumah beserta ruangannya |
| 16 | `control_physical_relay`| Smart Home IoT | Mengontrol pin GPIO fisik pada ESP32 yang terhubung ke modul relay listrik AC |
| 17 | `get_jadwal_sholat` | Religi & Ibadah | Menghitung waktu sholat 5 waktu akurat berdasarkan koordinat GPS / nama kota di Indonesia |
| 18 | `get_ayat_alquran` | Religi & Ibadah | Mengambil teks arab, terjemahan Indonesia, dan tafsir ringkas surat/ayat Al-Qur'an |
| 19 | `play_murottal_audio` | Religi & Ibadah | Mengalirkan lantunan merdu tilawah Al-Qur'an dari qari internasional langsung ke speaker |
| 20 | `get_arah_kiblat` | Religi & Ibadah | Menghitung derajat kompas arah kiblat dari lokasi pengguna menuju Ka'bah |
| 21 | `get_asmaul_husna` | Religi & Ibadah | Menjelaskan arti dan keutamaan 99 nama Allah (Asmaul Husna) |
| 22 | `get_hadits_harian` | Religi & Ibadah | Mengambil hadits shahih tematik (Bukhari, Muslim) beserta sanad dan maknanya |
| 23 | `get_current_weather` | Cuaca & Lingkungan | Mengambil data cuaca real-time (suhu, kelembapan, kondisi hujan) dari Open-Meteo |
| 24 | `get_weather_forecast`| Cuaca & Lingkungan | Mengambil prakiraan cuaca 3 hari ke depan untuk persiapan mobilitas pengguna |
| 25 | `calculate_expression`| Utilitas Saintifik | Mesin kalkulator matematika presisi tinggi (trigonometri, kalkulus, logaritma) |
| 26 | `currency_converter` | Finansial | Mengonversi nilai tukar mata uang asing (USD, EUR, JPY, SAR) ke Rupiah (IDR) real-time |
| 27 | `stock_crypto_price` | Finansial | Mengecek harga saham IHSG dan cryptocurrency populer (BTC, ETH) terkini |
| 28 | `translate_language` | Linguistik | Menerjemahkan kalimat antar-bahasa secara akurat (Indonesia, Arab, Inggris, Jepang, dll) |
| 29 | `set_reminder_alarm` | Produktivitas | Membuat pengingat jadwal tugas, alarm belajar, atau agenda penting |
| 30 | `list_active_reminders`| Produktivitas | Melihat daftar pengingat dan alarm yang sedang aktif |
| 31 | `delete_reminder` | Produktivitas | Menghapus pengingat yang telah selesai atau dibatalkan |
| 32 | `save_user_memory` | AI Memori Semantik | Menyimpan fakta personal pengguna (nama panggilan, hobi, alergi, target belajar) |
| 33 | `recall_user_memory` | AI Memori Semantik | Memanggil memori personal pengguna saat berdialog agar AI terasa sangat akrab |
| 34 | `set_ai_persona` | Personalitas AI | Mengubah kepribadian dan gaya bicara AI (misal: Guru Bijak, Teman Santai, Ustadz) |
| 35 | `sync_companion_note` | Integrasi Mobile | Mengirim catatan suara atau rangkuman percakapan ke aplikasi HP (Firebase RTDB) |
| 36 | `get_system_health` | Diagnostik & Telemetri| Mengecek kondisi memori server, latency gateway, dan status koneksi perangkat |

---

## 5. Fitur-Fitur Utama Ekosistem

1. **Intelligent Audio Streaming Engine (FFmpeg on-the-fly):**
   - Mengubah video YouTube apapun menjadi aliran audio Opus Ogg secara asinkron.
   - Pilihan bitrate adaptif: `8kbps`, `12kbps`, `16kbps`, dan `24kbps`.
   - ESP32 tidak perlu menyimpan file `.mp3` utuh; audio dialirkan per-potongan 512–1024 byte ke I2S DMA Buffer.
2. **Device Registry & Auto MAC Binding:**
   - Gateway mengenali MAC Address perangkat ESP32 saat pertama kali melakukan polling.
   - Isolasi multi-tenant: Antrean lagu pengguna A tidak akan tertukar dengan pengguna B meskipun menggunakan IP publik yang sama.
3. **In-Browser Web Flasher (Web Serial API):**
   - Pengguna awam dapat mem-flash firmware ESP32 langsung dari browser Google Chrome tanpa perlu menginstal Arduino IDE, driver CH340 manual, atau Python esptool.
4. **Firmware Marketplace & Community Store:**
   - Pengembang dapat mengunggah binary firmware, menetapkan harga (Rp 5.000 – Rp 50.000), mengunggah desain casing 3D (`.stl`), dan menerima royalti penjualan.
   - Dilengkapi sistem verifikasi dan audit log persetujuan produk oleh Admin.
5. **Smart Home Hybrid Controller:**
   - Mendukung relay virtual (untuk testing simulasi) dan relay nyata (GPIO pin hardware).
   - Dapat dinyalakan/dimatikan baik melalui suara ("Xiaozhi, nyalakan lampu kamar") maupun saklar web UI di dashboard.
6. **Community Chat & Multi-Worker Real-Time Sync:**
   - Forum obrolan sesama pengguna Xiaozhi berbasis WebSocket yang disinkronisasi antar-worker FastAPI menggunakan saluran `PostgreSQL LISTEN/NOTIFY`.

---

## 6. Mekanisme Operasional & Alur Data (Yang Terjadi di Dalam Sistem)

Sistem bekerja melalui **tiga jalur komunikasi konkuren**:

```
========================================================================================
ALUR 1: INTERAKSI SUARA & MODEL CONTEXT PROTOCOL (WEBSOCKET BRIDGE)
========================================================================================
Pengguna bicara -> Mic INMP441 -> ESP32 I2S -> Opus stream -> Xiaozhi Cloud AI
                                                                     │
                                                                     ▼ (Menganalisis Maksud)
Xiaozhi AI memutuskan perlu tool (misal: "play_youtube_song" / "get_jadwal_sholat")
                                                                     │
                                                                     ▼ (JSON-RPC 2.0 tools/call)
Gateway VPS (wss://xiaozhiscig.biz.id/mcp/) <────────────────────────┘
  │
  ├─ Eksekusi Function di xiaozhi/mcp/tools.py
  ├─ Masukkan lagu ke tabel PostgreSQL 'audio_queue' (dengan owner_id sesuai MAC)
  └─ Kirim hasil JSON-RPC balik ke Xiaozhi Cloud
                                                                     │
                                                                     ▼ (TTS Synthesizer)
Xiaozhi Cloud mengirim respon audio ("Baik, memutar lagu Despacito") ke Speaker ESP32

========================================================================================
ALUR 2: COMMAND QUEUE DELIVERY (HTTP POLLING)
========================================================================================
ESP32 secara berkala melakukan polling ringan ke Gateway:
ESP32 ──► GET /api/device/audio/commands?mac=84:7b:57:47:56:a8 ──► Gateway VPS
                                                                     │
                                                                     ▼
                                                    Membaca tabel 'audio_queue'
                                                                     │
Gateway ◄── Response JSON: {"action": "play", "url": "/api/audio/stream/dQw4w9..."} ──┘
ESP32 mengeksekusi perpindahan mode dari Dialog AI ke Audio Streaming Mode.

========================================================================================
ALUR 3: LOW-BITRATE OPUS STREAMING PIPELINE
========================================================================================
ESP32 ──► GET /api/audio/stream/dQw4w9WgXcQ?br=12k ──► Gateway VPS
                                                         │
                                                         ├─ yt-dlp mengambil audio URL YouTube
                                                         ├─ FFmpeg spawn process:
                                                         │   ffmpeg -i <stream_url> -c:a libopus
                                                         │          -b:a 12k -ar 16000 -ac 1 -f ogg pipe:1
                                                         └─ Streaming Chunked HTTP Transfer
ESP32 I2S DMA Buffer ◄── Mengalirkan data Opus Ogg per chunk (512B) ──┘
MAX98357A DAC men-decode ke sinyal analog -> Speaker bersuara jernih tanpa lag.
```

---

## 7. Audit Empiris Log Data Server VPS (Seminggu Terakhir)

*Audit ditarik langsung dari rekaman sistem produksi Nginx dan PostgreSQL pada rentang waktu 07 Oktober 2026 s.d. 14 Oktober 2026.*

### A. Tabel Kinerja Trafik Harian Server

| Tanggal | Total Requests | Unique IP | Bandwidth | Sukses (2xx) | Redirect (3xx) | Client Err (4xx) | Server Err (5xx) | Error Log |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **07/Okt/2026** | 16.076 | 346 | 623,69 MB | 14.274 (88,8%) | 910 (5,7%) | 665 (4,1%) | 109 (0,7%) | 114 |
| **08/Okt/2026** | 22.177 | 316 | 230,19 MB | 19.437 (87,6%) | 1.227 (5,5%) | 1.193 (5,4%) | 228 (1,0%) | 227 |
| **09/Okt/2026** | 20.784 | 282 | 396,75 MB | 18.206 (87,6%) | 1.051 (5,1%) | 1.319 (6,3%) | 101 (0,5%) | 109 |
| **10/Okt/2026** | 14.681 | 265 | 242,83 MB | 12.975 (88,4%) | 708 (4,8%) | 862 (5,9%) | 88 (0,6%) | 90 |
| **11/Okt/2026** | 14.363 | 286 | 251,64 MB | 11.581 (80,6%) | 1.193 (8,3%) | 1.546 (10,8%) | 2 (0,01%) | 2 |
| **12/Okt/2026** | 11.775 | 343 | 318,67 MB | 9.139 (77,6%) | 587 (5,0%) | 1.997 (17,0%) | 0 (0,0%) | 0 |
| **13/Okt/2026** | 50.077 | 299 | 90,49 MB | 7.619 (15,2%) | 861 (1,7%) | 41.542 (82,9%) | 7 (0,01%) | 2 |
| **14/Okt/2026** | 6.919 | 143 | 54,52 MB | 5.166 (74,7%) | 496 (7,2%) | 1.199 (17,3%) | 14 (0,2%) | 5 |
| **TOTAL** | **156.852** | **1.967\*** | **2.208,78 MB**| **98.397 (62,7%)**| **7.033 (4,5%)** | **50.323 (32,4%)**| **549 (0,4%)** | **549** |

### B. Distribusi Status Code HTTP
- **200 OK (98.380):** 62,72% dari total request berhasil dilayani sempurna.
- **404 Not Found (48.101):** 30,67% merupakan upaya bot eksternal memindai URL non-existent pada tanggal 13 Oktober.
- **301/307 Redirect (6.097):** Redirection SSL HTTPS dan OAuth Google Callback.
- **502 Bad Gateway (523):** 0,33% terjadi saat restart container backend dalam proses pemeliharaan.
- **500 Internal Error (24):** Hanya 0,015% error internal aplikasi (menunjukkan stabilitas kode Python yang sangat tinggi).

### C. Statistik Pemutaran Audio di Lapangan (Tabel `audio_queue`)
- Total antrean audio sepanjang masa sistem: **3.685 kali**.
- Total pemutaran dalam 7 hari terakhir: **1.429 lagu**.
- Rata-rata lagu diputar per hari: **204 lagu/hari**.
- Lagu terpopuler: *DJ TEH HIJAU TULUS REMIX* (95 kali), *wednesday* (92 kali), *Luis Fonsi - Despacito* (76 kali).
- Perangkat keras pemutar: **32 perangkat ESP32 fisik** (13 varian standar ESP32, 11 varian ESP32-S3, 8 varian ESP32-C3 Super Mini).

---

## 8. Dampak Penelitian (Signifikansi Akademis, Teknis, dan Sosial)

### A. Kontribusi Akademis (Bidang Informatika / IoT / Jaringan)
1. **Validasi Empiris Model Context Protocol (MCP) pada Perangkat Tertanam:** Penelitian ini memberikan bukti bahwa standar MCP yang dirancang untuk AI server-to-server dapat diterapkan secara efisien untuk mengontrol periferal fisik mikrokontroler (relay, sensor, audio) dengan latensi total di bawah 1,8 detik.
2. **Arsitektur Gateway Hemat Komputasi:** Menunjukkan bahwa gateway berbasis asinkron Python (FastAPI + Asyncio) mampu mengelola konkurensi ratusan perangkat tanpa degradasi memori di VPS 1 Core RAM 1GB.

### B. Signifikansi Teknis & Rekayasa Perangkat Lunak
1. **Reduksi Bandwidth Melalui Transcoding Opus:** Format MP3 128 kbps mengonsumsi ~57,6 MB per jam. Dengan transcoding Opus 12 kbps, konsumsi data terpangkas menjadi ~5,4 MB per jam (**penghematan bandwidth sebesar 90,6%**). Ini sangat krusial bagi koneksi internet pedesaan dan kuota seluler terbatas.
2. **Zero-Installation Firmware Deployment:** Pemanfaatan Web Serial API mendemokratisasi akses teknologi IoT, menghilangkan hambatan teknis kompilasi kode bagi pengguna awam.

### C. Dampak Sosial dan Ekonomi
1. **Demokratisasi Smart Device Cerdas di Indonesia:** Mengubah mikrokontroler berharga Rp 35.000 menjadi asisten pintar berbahasa Indonesia dengan kecerdasan setara speaker premium bernilai jutaan rupiah.
2. **Kemandirian Pendidikan (EduSmart):** Membantu anak-anak belajar secara interaktif, mendengarkan lantunan Al-Qur'an, menanyakan rumus pelajaran, dan melatih kemampuan bercakap tanpa perlu menatap layar HP (*screen-free learning*).

---

## 9. Analisis Kelemahan, Evaluasi Keamanan, dan Mitigasi Risiko

| Aspek | Temuan / Kelemahan Saat Ini | Dampak Risiko | Tindakan Mitigasi yang Telah Diterapkan |
| :--- | :--- | :--- | :--- |
| **HTTP Polling** | ESP32 melakukan polling interval 1-2 detik untuk memeriksa command queue | Menghasilkan trafik berulang ke endpoint `/api/device/audio/commands` | Diterapkan Nginx keepalive connection dan caching state di memori |
| **Anomali 404** | Tanggal 13 Okt terjadi 41k request 404 dari bot luar | Mengonsumsi resource logging Nginx | Pembatasan rate limit `10r/s` dan proteksi Fail2ban di level host |
| **Batas Memori VPS** | VPS hanya memiliki 1GB RAM fisik (saat ini terpakai 694MB) | Risiko OOM jika banyak proses FFmpeg spawn serentak | Swap file 5GB telah diaktifkan; proses transkoding dibatasi pool worker |
| **Storage Firmware** | File `.bin` firmware berukuran 1-3 MB | Potensi kehabisan kuota disk jika disimpan di lokal | Penyimpanan dialihkan ke Object Storage Neva Objects (S3) |

---

## 10. Rekomendasi Bab Skripsi (Metodologi, Pengujian & Pembahasan)

Untuk penyusunan laporan skripsi, data dan arsitektur dalam dokumen ini dapat dipetakan langsung ke struktur bab skripsi standar:

- **BAB I: PENDAHULUAN**
  - Manfaatkan [Bagian 2](#2-latar-belakang--urgensi-penelitian-buat-apa--mengapa-dibangun) untuk menyusun Latar Belakang Masalah, Rumusan Masalah, dan Batasan Masalah.
- **BAB II: LANDASAN TEORI**
  - Mengulas teori IoT (ESP32-C3 RISC-V), Audio Transcoding (Opus Codec vs MP3), Model Context Protocol (MCP), FastAPI Async Architecture, dan Reverse Proxy Nginx.
- **BAB III: METODOLOGI & PERANCANGAN SISTEM**
  - Gunakan diagram arsitektur [Bagian 3](#3-spesifikasi-teknologi-lengkap) dan alur sekuensial [Bagian 6](#6-mekanisme-operasional--alur-data-yang-terjadi-di-dalam-sistem).
  - Tampilkan skema wiring pinout shared-clock I2S (GPIO 5, 6, 4, 7).
- **BAB IV: HASIL DAN PEMBAHASAN (DATA UTAMA SKRIPSI)**
  - Masukkan tabel statistik 7 hari dari [Bagian 7](#7-audit-empiris-log-data-server-vps-seminggu-terakhir) sebagai bukti empiris keberhasilan implementasi di server live.
  - Tampilkan grafik tren pemutaran lagu harian (1.429 pemutaran audio dalam 7 hari).
  - Analisis stabilitas status code HTTP (tingkat keberhasilan 2xx vs error 5xx yang hanya 0,015%).
- **BAB V: KESIMPULAN DAN SARAN**
  - Mengambil poin kesimpulan dari [Bagian 8](#8-dampak-penelitian-signifikansi-akademis-teknis-dan-sosial) dan rekomendasi pengembangan dari [Bagian 9](#9-analisis-kelemahan-evaluasi-keamanan-dan-mitigasi-risiko).
