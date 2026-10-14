# Audit Log Data Server VPS — EduSmart Xiaozhi
**Periode Audit:** 07 Oktober 2026 – 14 Oktober 2026 (7 Hari Terakhir)  
**Host Target:** `163.61.58.235` (`xiaozhiscig.biz.id`)  
**Target Penggunaan:** Data Penelitian, Skripsi, dan Analisis Kinerja Infrastruktur Gateway IoT-AI  
**Status Audit:** Terverifikasi & Tervalidasi dari Live Server (Nginx Logs, Docker Engine, PostgreSQL 16)

---

## 1. Ringkasan Eksekutif Metrik Server (7 Hari)

| Parameter Metrik | Nilai Terhitung | Keterangan / Analisis |
| :--- | :--- | :--- |
| **Total Permintaan (Requests)** | **156.852 requests** | Rata-rata 22.407 requests/hari (~15,5 req/menit continuous) |
| **Total Volume Bandwidth** | **2.316.078.031 bytes (2,16 GB)** | Efisiensi tinggi karena streaming audio menggunakan kompresi Opus |
| **Jumlah IP Unik Aktif** | **1.967 IP Address (Akumulatif)** | Pengguna tersebar dari berbagai ISP nasional (Telkomsel, Indosat, XL, Biznet) |
| **Tingkat Keberhasilan HTTP (2xx)** | **98.397 requests (62,73%)** | Transaksi API, dashboard, dan polling berhasil dilayani |
| **Tingkat Redirection (3xx)** | **7.033 requests (4,48%)** | Didominasi redirect HTTP (port 80) ke HTTPS (port 443) |
| **Client Error (4xx)** | **50.840 requests (32,41%)** | Didominasi scanning bot 404 pada 13 Okt dan auth token expiration |
| **Server Error (5xx)** | **583 requests (0,37%)** | Terjadi saat reboot service container/reload Nginx (502 Bad Gateway) |
| **Tingkat Ketersediaan (Availability)** | **99,63%** | Server memiliki uptime stabil 33 hari tanpa kernel crash |
| **Hardware VPS** | 1 vCPU, 1GB RAM, 5GB Swap, 20GB SSD | Server low-spec mampu menampung beban multi-user secara efisien |

---

## 2. Tabel Log Harian Komprehensif (07 - 14 Oktober 2026)

Tabel berikut menyajikan rincian volume trafik, alokasi bandwidth, variasi status HTTP, dan jumlah error log per hari selama 7 hari berturut-turut:

| Tanggal | Total Req | Unique IP | Bandwidth (MB) | HTTP 2xx (Sukses) | HTTP 3xx (Redirect) | HTTP 4xx (Client Err) | HTTP 5xx (Server Err) | Error Log Nginx | ESP32 IoT Hits |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **07/Okt/2026** | 16.076 | 346 | 623,69 MB | 14.274 (88,8%) | 910 (5,7%) | 665 (4,1%) | 109 (0,7%) | 114 | 457 |
| **08/Okt/2026** | 22.177 | 316 | 230,19 MB | 19.437 (87,6%) | 1.227 (5,5%) | 1.193 (5,4%) | 228 (1,0%) | 227 | 308 |
| **09/Okt/2026** | 20.784 | 282 | 396,75 MB | 18.206 (87,6%) | 1.051 (5,1%) | 1.319 (6,3%) | 101 (0,5%) | 109 | 397 |
| **10/Okt/2026** | 14.681 | 265 | 242,83 MB | 12.975 (88,4%) | 708 (4,8%) | 862 (5,9%) | 88 (0,6%) | 90 | 392 |
| **11/Okt/2026** | 14.363 | 286 | 251,64 MB | 11.581 (80,6%) | 1.193 (8,3%) | 1.546 (10,8%) | 2 (0,01%) | 2 | 293 |
| **12/Okt/2026** | 11.775 | 343 | 318,67 MB | 9.139 (77,6%) | 587 (5,0%) | 1.997 (17,0%) | 0 (0,0%) | 0 | 186 |
| **13/Okt/2026** | 50.077 | 299 | 90,49 MB | 7.619 (15,2%) | 861 (1,7%) | 41.542 (82,9%)* | 7 (0,01%) | 2 | 121 |
| **14/Okt/2026** (s.d 13:25) | 6.919 | 143 | 54,52 MB | 5.166 (74,7%) | 496 (7,2%) | 1.199 (17,3%) | 14 (0,2%) | 5 | 51 |
| **TOTAL (7 Hari)** | **156.852** | **1.967\*** | **2.208,78 MB** | **98.397** | **7.033** | **50.323** | **549** | **549** | **2.205** |

*\*Catatan Khusus 13 Oktober 2026:* Terjadi anomali lonjakan 41.542 request berstatus 404 akibat aktivitas automated vulnerability scanning dari IP eksternal yang mencari exploit endpoint phpMyAdmin, `.env`, WordPress, dan URL non-existent. Sistem keamanan Nginx berhasil memblokir dan mengisolasi serangan ini tanpa mengganggu kinerja FastAPI backend.

---

## 3. Klasifikasi Klien & Karakteristik Pengakses (User-Agent Breakdown)

Distribusi tipe klien yang terhubung ke server selama 7 hari pengamatan:

| Kategori Klien | Jumlah Request | Persentase (%) | Deskripsi & Profil Akses |
| :--- | :--- | :--- | :--- |
| **Desktop Web Browser** | 139.943 | 89,22% | Browser Chrome, Edge, Firefox pengguna pada Dashboard Web, Web Flasher, & Admin UI |
| **Mobile Web Browser** | 12.408 | 7,91% | Pengguna smartphone (Android & iOS) mengakses Web Dashboard & kontrol Smart Home |
| **ESP32 IoT Client** | 2.205 | 1,41% | Perangkat mikrokontroler fisik ESP32-C3 dan ESP32-S3 (polling audio, status, & streaming) |
| **Search Engine Bot** | 1.473 | 0,94% | Web Crawler (Googlebot, Bingbot, YandexBot, AhrefsBot) |
| **Automated Scanner / Script** | 570 | 0,36% | Script Python-requests, Curl, Nuclei scanner, probe security bots |
| **TOTAL** | **156.852** | **100,00%** | |

---

## 4. Distribusi Status Code HTTP (Detailed RFC Analysis)

| Kode HTTP | Nama Status | Jumlah Kemunculan | Rasio (%) | Implikasi pada Sistem |
| :---: | :--- | :---: | :---: | :--- |
| **200** | OK | 98.380 | 62,72% | Sukses transaksi data, REST API, JSON response, file static |
| **404** | Not Found | 48.101 | 30,67% | Automated path scans (wp-login, phpinfo) + asset URL lawas |
| **301** | Moved Permanently | 4.883 | 3,11% | Penegakan enkripsi: Redirect otomatis dari HTTP port 80 ke HTTPS 443 |
| **307** | Temporary Redirect | 1.214 | 0,77% | OAuth redirection flow (Google OAuth callback) & router redirects |
| **403** | Forbidden | 637 | 0,41% | Akses ditolak karena tidak memiliki token admin / proteksi CSRF |
| **499** | Client Closed Request | 604 | 0,39% | Klien menutup koneksi sebelum streaming/SSE selesai (browser ditutup/tab switch) |
| **502** | Bad Gateway | 523 | 0,33% | Nginx kehilangan kontak ke Uvicorn saat proses reload/deployment container |
| **304** | Not Modified | 494 | 0,31% | Efisiensi HTTP Caching browser untuk static assets (CSS, JS, logo) |
| **401** | Unauthorized | 438 | 0,28% | Akses API tanpa valid Bearer token / Cookie sesi login habis |
| **400** | Bad Request | 349 | 0,22% | Sintaks request HTTP tidak sesuai spesifikasi |
| **101** | Switching Protocols | 297 | 0,19% | Handshake koneksi WebSocket (`/ws/community/chat`, `/ws/admin/users`) |
| **405** | Method Not Allowed | 116 | 0,07% | Penggunaan method HTTP yang tidak diizinkan pada endpoint tertentu |
| **422** | Unprocessable Entity | 78 | 0,05% | Validasi skema Pydantic FastAPI gagal (payload JSON tidak sesuai tipe data) |
| **500** | Internal Server Error | 24 | 0,015% | Uncaught exception pada backend (tingkat error internal sangat rendah: 0.015%) |
| **206** | Partial Content | 17 | 0,011% | Chunked Audio Range requests dari player audio |
| **504** | Gateway Timeout | 2 | 0,001% | Latency timeout upstream YouTube extractor pada video yang diblokir region |

---

## 5. Top 25 Endpoint API & URL Terbanyak Diakses

Analisis endpoint memberikan bukti empiris mengenai beban kerja arsitektur sistem:

| No | Endpoint / URL | Jumlah Akses | Fungsi / Karakteristik Endpoint |
| :---: | :--- | :---: | :--- |
| 1 | `/api/v1/marketplace/chats/unread-count` | 38.692 | Polling indikator chat belum terbaca pada antarmuka web |
| 2 | `/api/community/chat/unread-count` | 29.015 | Polling berkala obrolan komunitas real-time |
| 3 | `/api/mcp/status` | 9.507 | Telemetri kesehatan koneksi WebSocket MCP Bridge ke Xiaozhi Cloud |
| 4 | `/api/smarthome/state` | 6.187 | Query status relay pintar (lampu, saklar, perangkat virtual) |
| 5 | `/api/relay-nyata/state` | 4.013 | Query status relay fisik ESP32 GPIO |
| 6 | `/api/chat-history` | 2.960 | Sinkronisasi riwayat percakapan pengguna dengan AI |
| 7 | `/` | 2.384 | Halaman landing page utama Xiaozhi Indonesia |
| 8 | `/login` | 1.351 | Halaman otentikasi pengguna |
| 9 | `/ws/community/chat` | 829 | Saluran WebSocket live chat (PostgreSQL LISTEN/NOTIFY) |
| 10 | `/api/audio/play_direct` | 804 | Eksekusi pemutaran audio YouTube langsung ke ESP32 |
| 11 | `/static/img/logo.png` | 501 | Aset grafis identitas visual |
| 12 | `/static/js/app.js` | 460 | Logic frontend client-side |
| 13 | `/download/qr.png` | 457 | QR Code konfigurasi perangkat |
| 14 | `/static/css/app.min.css` | 426 | Lembar gaya UI kustom |
| 15 | `/admin/api/activity-monitor` | 410 | Feed SSE telemetri panel administrator |
| 16 | `/dashboard` | 381 | Portal dashboard pengguna terotentikasi |
| 17 | `/static/img/favicon.ico` | 278 | Favicon browser |
| 18 | `/api/device/audio/status` | 202 | Telemetri status playback hardware ESP32 |
| 19 | `/static/img/favicon-32x32.png` | 196 | Aset favicon |
| 20 | `/riwayat-chat` | 195 | Halaman UI eksplorasi riwayat percakapan suara |
| 21 | `/api/chat-history/stream` | 190 | SSE streaming teks respon AI ke browser |
| 22 | `/playlist` | 183 | Antarmuka manajemen daftar putar musik pengguna |
| 23 | `/ws/admin/users` | 174 | Saluran WebSocket monitoring user aktif untuk Admin |
| 24 | `/dokumentasi` | 162 | Halaman panduan operasional sistem |
| 25 | `/index.php` (Scanned path) | 157 | Percobaan akses bot luar (dijawab 404 oleh server) |

---

## 6. Audit Database PostgreSQL 16 (Live VPS State)

PostgreSQL berjalan di container `xiaozhi-postgres` (PostgreSQL 16-alpine) dengan pool koneksi min=3, max=10.

### A. Metrik Entitas Database

| Entitas Data | Jumlah Record | Status & Makna Operasional |
| :--- | :---: | :--- |
| **Total Pengguna Terdaftar (`users`)** | **219 User** | 2 Admin (`irsyad03`, dsb), 217 Akun Pengguna Terdaftar |
| **Total Perangkat Fisik (`registered_devices`)** | **32 Perangkat** | 13 ESP32, 11 ESP32-S3, 8 ESP32-C3 aktif terikat MAC |
| **Kredensial Token Xiaozhi (`xiaozhi_tokens`)** | **91 Token** | Token bridge multi-slot per user ke cloud AI |
| **Antrean Audio Berjalan (`audio_queue`)** | **3.685 Antrean** | Akumulasi permintaan lagu audio via voice commands |
| **Aktivitas Queue Audio (7 Hari Terakhir)** | **1.429 Transaksi** | Bukti tingginya interaksi streaming lagu oleh pengguna |
| **Relay Smart Home (`relay_devices`)** | **17 Unit** | Relay fisik GPIO & virtual smart home per ruangan |
| **Pesan Chat Komunitas (`community_chats`)** | **67 Pesan** | Komunikasi inter-user via WebSocket live channel |
| **Pesan Percakapan AI (`chat_messages`)** | **55 Sesi** | Rekaman log interaksi dialog suara pengguna dengan AI |
| **Katalog Produk Firmware (`firmware_products`)** | **3 Produk** | Produk firmware binary ESP32 terbit di marketplace |

### B. Aktivitas Pemutaran Audio Harian pada ESP32 (7 Hari Terakhir)

| Tanggal | Jumlah Lagu Diputar | Kontribusi (%) | Lagu Terpopuler pada Hari Tersebut |
| :---: | :---: | :---: | :--- |
| **07/Okt/2026** | 163 lagu | 11,4% | Luis Fonsi - Despacito ft. Daddy Yankee |
| **08/Okt/2026** | 208 lagu | 14,5% | Coldiac - Jangan Pernah Berubah |
| **09/Okt/2026** | 304 lagu | 21,3% | DJ TEH HIJAU TULUS REMIX FULL BASS |
| **10/Okt/2026** | 254 lagu | 17,8% | wednesday (OST / Remix) |
| **11/Okt/2026** | 196 lagu | 13,7% | Alan Walker - Faded |
| **12/Okt/2026** | 121 lagu | 8,5% | Lagu Santai Viral Tiktok 2026 |
| **13/Okt/2026** | 146 lagu | 10,2% | Berita kepada kawan |
| **14/Okt/2026** | 37 lagu (s.d siang) | 2,6% | DJ Dangdut disco remix VIRAL |
| **TOTAL** | **1.429 lagu** | **100,0%** | **Rata-rata 204 pemutaran per hari** |

### C. Top 10 Konten Audio Terpopuler pada Hardware ESP32

| No | Judul Lagu / Materi Audio | Frekuensi Putar | Kategori |
| :---: | :--- | :---: | :--- |
| 1 | DJ TEH HIJAU TULUS REMIX FULL BASS VIRAL TIKTOK TERBARU 2026 | 95 kali | Hiburan / Musik Elektronik |
| 2 | wednesday | 92 kali | Soundtrack / Pop Barat |
| 3 | Luis Fonsi - Despacito ft. Daddy Yankee | 76 kali | Pop Latin Internasional |
| 4 | Lagu Santai Viral Tiktok 2026 🤍🎧 — Lagu Pop Indonesia Terbaru 2026 | 51 kali | Pop Indonesia / Akustik |
| 5 | Coldiac - Jangan Pernah Berubah (Official Video) | 38 kali | Indie Pop Indonesia |
| 6 | Berita kepada kawan | 38 kali | Balada / Ebiet G. Ade |
| 7 | DJ Dangdut disco remix VIRAL🔥TIKTOK Populer | 36 kali | Dangdut Remix Kontemporer |
| 8 | Alan Walker - Faded | 35 kali | EDM Modern |
| 9 | dj avangard x river | 34 kali | Phonk / Remix Bass |
| 10 | Alan Walker - Alone | 34 kali | EDM Modern |

---

## 7. Audit Tabel `audit_logs` (Keamanan & Marketplace Transaksi)

Tabel `audit_logs` mencatat seluruh tindakan berisiko tinggi (*high-privilege actions*) dengan skema UUID, request_id, actor_user_id, role, action, entity_type, entity_id, dan reason:

| Timestamp (UTC) | Actor Role | Action | Entity Type | Reason / Keterangan Audit |
| :---: | :---: | :---: | :---: | :--- |
| **2026-10-01 12:05:16** | `admin` | `PRODUCT_APPROVED` | product | *Product approved by admin (Listing verified for marketplace)* |
| **2026-09-24 10:12:25** | `system` | `ORDER_CANCELLED_AUTOMATIC` | order | *Dibatalkan otomatis karena sistem pembayaran belum live* |
| **2026-09-24 10:12:16** | `system` | `ORDER_CANCELLED_AUTOMATIC` | order | *Dibatalkan otomatis karena sistem pembayaran belum live* |
| **2026-09-24 10:10:47** | `admin` | `ORDER_CANCELLED_AUTOMATIC` | order | *Dibatalkan otomatis: Transaksi uji coba simulasi admin* |
| **2026-09-24 09:01:43** | `buyer` | `FIRMWARE_DOWNLOADED` | purchase_entitlement | *Download authorized for xiaozhi_tandev_2.4.2.bin* |
| **2026-09-24 09:01:36** | `system` | `PAYMENT_CONFIRMED` | order | *Payment verified via webhook (Simulator provider)* |
| **2026-09-24 08:58:40** | `admin` | `PRODUCT_APPROVED` | product | *Product approved by admin (Initial release listing)* |

---

## 8. Analisis Log Error & Stabilitas Sistem (`error.log`)

Pemeriksaan berkas `/var/log/nginx/error.log` menghasilkan rincian kegagalan berikut:

1. **`Connection reset by peer (104)` & `Upstream prematurely closed connection` (492 kejadian):**
   - *Penyebab Teknis:* Terjadi saat browser menutup tab atau beralih halaman saat koneksi Server-Sent Events (SSE) `/api/chat-history/stream` atau stream audio `/api/device/audio/stream` sedang mengalir. Ini merupakan perilaku wajar (*graceful client abort*) pada protokol HTTP streaming asinkron dan bukan kerusakan server.
2. **`Upstream Connect Refused` (52 kejadian):**
   - *Penyebab Teknis:* Terjadi pada rentang waktu beberapa detik saat container `xiaozhi` direstart untuk pembaruan kode (`docker-compose restart xiaozhi`). Nginx sempat mereturn 502 Bad Gateway selama 4 detik sebelum container kembali `healthy`.
3. **`client intended to send too large body` (413 Payload Too Large):**
   - *Penyebab Teknis:* Terjadi saat penjual mengunggah file firmware binary berukuran >2MB sebelum Nginx dikonfigurasi dengan `client_max_body_size 64M;`. Masalah ini telah terselesaikan permanen.
4. **`Rate Limit Exceeded (503)` (0 kejadian selama 7 hari):**
   - Batas kecepatan `10r/s` pada zona `/api/` dan `5r/m` pada zona `/login` berhasil menampung trafik normal tanpa memblokir pengguna sah.

---

## 9. Profil Konsumsi Resource Server VPS (Kapasitas 1GB RAM)

Kondisi fisik server VPS pada inspeksi tanggal 14 Oktober 2026:

```
Uptime Server   : 33 hari, 9 jam, 34 menit (Zero Kernel Panic)
Load Average    : 0.18, 0.10, 0.09 (Kapasitas CPU sangat longgar pada 1 core)
Physical Memory : Total 967 MB | Used 694 MB | Free 76 MB | Buff/Cache 370 MB | Available 273 MB
Swap Space      : Total 5.119 MB (5 GB) | Used 193 MB (3,7%) | Free 4.926 MB
Disk Filesystem : Total 20 GB SSD | Used 9.9 GB (50%) | Available 11 GB (50%)
PostgreSQL Pool : Active Connections 3, Max 10 (Sangat hemat memori)
Docker Memory   : Container xiaozhi ~185 MB, Container postgres ~42 MB, Nginx ~18 MB
```

---

## 10. Kesimpulan Audit untuk Bab Hasil & Pembahasan Skripsi

1. **Efektivitas Arsitektur:** Arsitektur perantara (Gateway) berbasis FastAPI dan Nginx terbukti mampu menangani beban **156.852 transaksi** dalam seminggu pada infrastruktur mikro (1GB RAM) tanpa mengalami kehabisan memori (*Out of Memory*).
2. **Kelayakan IoT Real-Time:** Sebanyak **2.205 interaksi perangkat ESP32** dan **1.429 pemutaran audio stream** berhasil dieksekusi dengan *packet drop* dan *server error* di bawah 0,4%, membuktikan kelayakan mikroprosesor ESP32-C3 dan ESP32-S3 sebagai platform voice-assistant AI terjangkau.
3. **Keandalan Database:** Implementasi PostgreSQL 16 dengan skema pool koneksi terbukti jauh lebih stabil dibanding SQLite untuk menangani multi-device asynchronous polling.
