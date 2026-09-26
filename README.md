---
title: Xiaozhi
emoji: "📚"
colorFrom: green
colorTo: indigo
sdk: docker
pinned: false
license: apache-2.0
short_description: untuk xiaozhi
---

# Xiaozhi Indonesia (xiaozhiscig)

🌐 **Website Resmi:** [https://xiaozhiscig.biz.id](https://xiaozhiscig.biz.id)  
📖 **Dokumentasi Lengkap:** [https://xiaozhiscig.biz.id/dokumentasi](https://xiaozhiscig.biz.id/dokumentasi)  
📱 **Unduh Aplikasi Mobile (Xichi / ESPBridge):** [https://xiaozhiscig.biz.id/download/app](https://xiaozhiscig.biz.id/download/app)  

Platform resmi **Xiaozhi Indonesia** (`xiaozhiscig.biz.id`): ekosistem asisten suara cerdas berbasis IoT ESP32, integrasi 44 MCP tools, Smart Home, dan manajemen knowledge base materi edukasi.

FastAPI dashboard untuk mengelola knowledge base Xiaozhi. Database didukung oleh PostgreSQL di VPS dan sinkronisasi realtime Firebase.

## Secret yang perlu diset

Set di Hugging Face Space Settings -> Secrets:

- `ENVIRONMENT`: isi `production` saat deploy.
- `HF_TOKEN`: token Hugging Face dengan akses write.
- `HF_DATASET_REPO`: repo dataset, contoh `username/xiaozhi-indonesia-db`. Jika kosong, app mencoba membuat `username/xiaozhi-indonesia-db`.
- `APP_SECRET_KEY`: string acak panjang untuk signed session dan CSRF.
- `DATA_ENCRYPTION_KEY`: key stabil untuk enkripsi token Xiaozhi. Bisa Fernet key.
- `COOKIE_SECURE`: isi `true` saat deploy HTTPS.
- `EDUSMART_API_KEY`: opsional, untuk akses API `/search_course_materials` tanpa login browser.
- `ALLOWED_HOSTS`: host yang boleh mengakses app, contoh `*.hf.space,huggingface.co,*.huggingface.co`.
- `REAL_RELAY_POLL_INTERVAL_MS`: interval polling ESP32/8266 dll untuk Relay Nyata, default `1000`.

Generate secret lokal:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Jalankan lokal

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 7860
```

Tanpa `HF_TOKEN`, app memakai cache lokal `.hf_db_cache/app_data.json` hanya untuk development.

## Catatan production

- Jangan simpan token di Git remote, source code, atau `.env.example`.
- Rotate token Hugging Face jika pernah muncul di chat/log.
- Database HF Dataset harus private.
- Endpoint MCP bisa diperbarui dari dashboard; nilai lama akan diganti dan disimpan terenkripsi.
- Halaman auth menyediakan pilihan Login, Cari akun, dan Register. Pencarian akun dibatasi prefix minimal 2 karakter dan memakai rate limit dasar.
- Relay Nyata memakai API polling dari ESP32/8266 dll, bukan broker MQTT. ESP32/8266 dll membaca perintah dari endpoint `/api/device/relay/{slug}/commands` dan mengirim status terbaru ke `/api/device/relay/{slug}/status` memakai token perangkat.
## Daftar 44 Tools MCP Xiaozhi

Server MCP Xiaozhi menyediakan 44 tools siap pakai yang dipanggil secara otomatis oleh AI berdasarkan percakapan suara atau teks:

### 1. Pembelajaran & Akademik (Study Suite)
* `solve_study_problem`: Pemecah soal bertahap (Diketahui, Ditanyakan, Rumus, Perhitungan, Tips jebakan).
* `explain_concept`: Penjelas konsep 2 level (Definisi Akademik Resmi + Analogi Dunia Nyata ELI5).
* `quiz_me`: Latihan soal dan kuis interaktif berdasarkan topik/materi.
* `lookup_formula`: Kamus rumus cepat Matematika, Fisika, Kimia, dan Ekonomi.
* `academic_english_helper`: Proofreading grammar, parafrase akademik, dan kosakata abstrak/jurnal.
* `lookup_kbbi`: Pengecekan ejaan baku, bentuk tidak baku, kelas kata, dan arti resmi menurut KBBI.
* `search_wikipedia`: Ringkasan ensiklopedia faktual dari Wikipedia bahasa Indonesia.

### 2. Analisis Spesialis (Filsafat, Psikologi & IT)
* `detect_logical_fallacy`: Deteksi cacat logika (Ad Hominem, Straw Man, False Dilemma, Slippery Slope, dll) & cara rekonstruksi argumen.
* `identify_cognitive_bias`: Analisis bias pikiran (Confirmation Bias, Sunk Cost Fallacy, Dunning-Kruger), Pertanyaan Sokratik, dan teknik CBT Cognitive Reframing.
* `it_code_and_architecture_helper`: Konsultasi Senior Software Engineer: debugging kode, Big-O, GoF Design Patterns, Clean Architecture, SQL/NoSQL indexing, Docker & Linux CLI.

### 3. Doa & Ibadah Lintas Agama (Multifaith Guide)
* `lookup_scripture_and_verse`: Pencarian nama surat/kitab/ayat untuk 7 tradisi agama (Islam, Kristen, Katolik, Hindu, Buddha, Konghucu, Yahudi) dengan lafal transliterasi fonetik & terjemahan Indonesia.
* `get_prayer_and_worship_guide`: Panduan doa harian & ibadah langkah-demi-langkah (Sholat 5 waktu & wudhu, Doa Bapa Kami & Salam Maria, Misa Katolik, Puja Tri Sandhya Hindu, Meditasi Metta Buddhis, Sembahyang Tian Konghucu, Shabbat Yahudi).

### 4. Realtime Cuaca, Gempa BMKG & Kurs
* `get_weather`: Data realtime suhu (°C), kelembapan, angin, dan prakiraan cuaca per kota di Indonesia.
* `get_earthquake_info`: Informasi resmi gempa bumi terkini & dirasakan langsung dari BMKG Indonesia.
* `convert_currency`: Konversi nilai tukar mata uang dunia (USD, IDR, EUR, JPY, SGD, dll) terkini.

### 5. Knowledge Base Pribadi
* `search_course_materials`: Pencarian materi perkuliahan/catatan di database.
* `read_live_api_data`: Membaca data API realtime kustom.
* `read_material_database`: Membaca daftar materi yang tersimpan.
* `read_material_detail`: Membaca satu dokumen/materi secara lengkap.

### 6. Smart Home Virtual & Relay Fisik ESP32
* `control_relay`, `control_smart_home_room`, `get_relay_status`, `all_relays_on`, `all_relays_off`: Kontrol perangkat rumah pintar virtual.
* `control_real_relay_by_voice`, `get_real_relay_status`, `all_real_relays_on`, `all_real_relays_off`: Kontrol relay fisik nyata via polling ESP32.

### 7. Riset Web Mendalam, Intelijen & Media Sosial (Deep Intelligence)
* `search_web_deep`: Riset web multi-sumber mendalam (Wikipedia + Google News + automated full-article scraper hingga 1.500 karakter).
* `search_social_media`: Pemantauan opini publik, review jujur komunitas, dan tren viral di Reddit, X/Twitter, dan YouTube.
* `osint_recon`: Investigasi intelijen pasif sumber terbuka: jejak digital username (12+ platform), geolokasi & ASN alamat IP, serta DNS/subdomain domain via Certificate Transparency (`crt.sh`).
* `search_web`, `search_news`: Pencarian web kilat dan rangkuman berita aktual terpercaya.

### 8. Multimedia YouTube & Streaming Musik
* `play_youtube_song`: Pencarian lagu dan streaming audio YouTube ke speaker ESP32.
* `get_playback_status`, `stop_youtube_song`: Kontrol dan pemantauan status audio player.

### 9. Utilitas & Produktivitas Harian
* `calculate`: Perhitungan matematika, trigonometri, dan kalkulator ekspresi.
* `translate_text`: Penerjemah multi-bahasa berbasis konteks.
* `set_reminder`: Pembuatan pengingat/jadwal suara interaktif.

### 10. Memori Semantik Percakapan & Profil Pengguna
* `save_chat_history`, `recall_chat_memory`: Long-term episodic memory untuk mengingat percakapan antar-sesi.
* `remember_user_profile`, `get_user_profile`: Personalisasi profil pengguna (nama, kebiasaan, preferensi).
