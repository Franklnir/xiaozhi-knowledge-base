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

# Xiaozhi Indonesia

FastAPI dashboard untuk mengelola knowledge base Xiaozhi. Database tidak lagi memakai Supabase; data disimpan sebagai JSON di private Hugging Face Dataset repo lewat `huggingface_hub`.

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
"# Xiaozhi Knowledge Base - Updated $(date)"  
"# CI/CD test $(Get-Date)"  
"# CI/CD test 2 $(Get-Date)"  
