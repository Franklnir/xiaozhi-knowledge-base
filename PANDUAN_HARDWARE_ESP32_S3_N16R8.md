# Panduan Operasional & Hardware: ESP32-S3 N16R8 (CAM & Standar Tanpa CAM)

Dokumentasi resmi untuk instalasi, konfigurasi jaringan Web Portal, pemetaan GPIO hardware, dan fungsi tombol pada firmware XiaoZhi seri **ESP32-S3 N16R8**.

---

## 1. Kompatibilitas Hardware

Firmware ini dirancang secara terpadu (*Unified Firmware*) untuk dua varian modul:
1. **ESP32-S3-WROOM-1 / DevKitC-1 N16R8 (Biasa / Tanpa Kamera)**
2. **ESP32-S3 CAM N16R8 (Dengan Modul Kamera OV2640 / OV5640 / GC2145)**

### Spesifikasi Inti
- **Chip:** ESP32-S3 Dual-Core Xtensa LX7 @ 240MHz
- **Flash Memory:** 16MB Quad/Octal SPI Flash
- **PSRAM:** 8MB Octal SPI (OPI) PSRAM (wajib untuk buffer audio, AI streaming, dan kamera)
- **Konektivitas:** Wi-Fi 2.4 GHz (802.11 b/g/n) & Bluetooth 5 (LE)
- **Fail-Safe Kamera:** Driver kamera memiliki proteksi otomatis. Jika modul kamera tidak terpasang (pada board ESP32-S3 biasa), sistem **tidak akan panic/crash**, melainkan otomatis mencatat log `Camera init failed` dan melanjutkan proses boot normal.

---

## 2. Panduan Setup & Konfigurasi Wi-Fi Web Portal

Saat pertama kali dinyalakan atau setelah reset Wi-Fi, perangkat akan masuk ke mode **Access Point (Hotspot Setup)**.

### Langkah-langkah Konfigurasi:
1. **Hubungkan ke Wi-Fi ESP32:**
   - Cari hotspot Wi-Fi dari HP atau PC bernama: **`XiaoZhi-XXXX`** atau **`Xichi-Setup`** (tanpa password).
   - Sambungkan perangkat Anda ke hotspot tersebut.
2. **Buka Web Portal:**
   - Buka browser (Chrome, Edge, atau Safari) dan buka alamat:
     ```
     http://192.168.4.1
     ```
3. **Tab 1: Jaringan Wi-Fi:**
   - **Pilih SSID:** Pilih nama Wi-Fi rumah/kantor Anda (frekuensi 2.4 GHz).
   - **Password:** Masukkan kata sandi Wi-Fi Anda.
4. **Tab 2: Konfigurasi Hardware:**
   - **Pilihan Modul Layar:**
     - `Tanpa Layar (Headless Audio Only)`: Jika board Anda tidak memakai layar.
     - `ST7789 240x280 (8-Pin SPI, CS=GPIO 45)`: Untuk layar LCD IPS 1.69" / 1.54" 8-pin.
     - `ST7789 240x240 (7-Pin SPI, Tanpa CS)`: Untuk layar LCD IPS 1.3" 7-pin.
     - `OLED SSD1306 / SH1106 128x64`: Untuk layar OLED kecil berbasis I2C (SDA=GPIO 20, SCL=GPIO 19).
   - **Rotasi Layar:** Pilih `0° (Normal)` atau `180° (Terbalik)` sesuai orientasi perakitan casing Anda.
   - **Profil Hardware Audio:**
     - Pilih `INMP441 Mic + MAX98357A Spk (Simplex I2S)` untuk modul mic dan speaker standar.
     - Atau `WeAct ES8311 Codec Terintegrasi (Duplex I2S)` jika menggunakan board WeAct Studio.
   - **Modul Kamera ESP32-S3:**
     - **Board ESP32-S3 Biasa (Tanpa CAM):** Pilih **`Kamera Nonaktif (Hemat Daya & Bebas Pin)`**. Opsi ini mematikan inisialisasi kamera sehingga menghemat RAM dan membebaskan jalur komunikasi.
     - **Board ESP32-S3 CAM:** Pilih sensor yang terpasang (misal `OV2640 2-Megapixel` atau `OV5640 5-Megapixel`).
5. **Tab 3: Preferensi Sistem:**
   - **Bahasa Suara Asisten:** Pilih `🇮🇩 Bahasa Indonesia (id-ID)` agar asisten merespons dalam bahasa Indonesia.
6. **Simpan dan Terapkan:**
   - Klik **Simpan Konfigurasi**.
   - Sistem akan memvalidasi agar tidak ada pin yang bentrok, menyimpan konfigurasi ke memory NVS Flash, dan melakukan reboot otomatis.
   - Perangkat akan langsung tersambung ke jaringan Wi-Fi Anda dan siap diajak bicara.

---

## 3. Diagram Pinout & Wiring Hardware

### A. Modul Audio I2S (INMP441 & MAX98357A)
| Modul Audio | Pin Modul | Terhubung ke ESP32-S3 | Keterangan |
| :--- | :--- | :--- | :--- |
| **INMP441 (Mic)** | `VDD` | **3.3V** | Daya Mic (Gunakan 3.3V stabil) |
| | `GND` | **GND** | Ground bersama |
| | `SD` | **GPIO 42** | Serial Data Out dari Mic |
| | `WS` | **GPIO 1** | Word Select (Left/Right Clock) |
| | `SCK` | **GPIO 2** | Serial Bit Clock |
| | `L/R` | **GND** | Channel Kiri (Mono) |
| **MAX98357A (Spk)**| `VIN` | **5V / 3.3V** | Daya Amplifier (5V disarankan untuk volume kencang) |
| | `GND` | **GND** | Ground bersama |
| | `DIN` | **GPIO 39** | Serial Data In ke Amplifier |
| | `BCLK` | **GPIO 40** | Bit Clock Speaker |
| | `LRC` | **GPIO 41** | Left/Right Clock (WS) Speaker |
| | `GAIN` | **GND / Terbuka** | Gain 9dB (GND) atau 12dB (Lepas) |
| | `SD_MODE` | **Terbuka / 3.3V** | Mode Aktif |

### B. Layar ST7789 SPI (Opsional)
| Pin ST7789 | Pin ESP32-S3 | Keterangan |
| :--- | :--- | :--- |
| `SCL / CLK` | **GPIO 12** | SPI Bus Clock |
| `SDA / MOSI` | **GPIO 11** | SPI Bus Master Out |
| `RES / RST` | **GPIO 47** | Hardware Reset Layar |
| `DC / RS` | **GPIO 48** | Data / Command Control |
| `CS` | **GPIO 45** | Chip Select *(Hanya untuk varian 8-pin; 7-pin tanpa CS)* |
| `BLK / BL` | **GPIO 38** | Kontrol Backlight PWM |
| `VCC` | **3.3V** | Catu Daya Layar |
| `GND` | **GND** | Ground Layar |

### C. Pin Tombol Fisik
| Tombol | Pin ESP32-S3 | Mode Rangkaian |
| :--- | :--- | :--- |
| **BOOT Button** | **GPIO 0** | Terhubung ke GND saat ditekan (Internal Pull-Up aktif) |
| **Volume UP** | **GPIO 14** | Terhubung ke GND saat ditekan (Internal Pull-Up aktif) |
| **Volume DOWN** | **GPIO 46** | Terhubung ke GND saat ditekan (Internal Pull-Up aktif) |

---

## 4. Panduan Lengkap Fungsi Ke-3 Tombol

Firmware dilengkapi logika deteksi klik tunggal (*single click*), klik ganda (*double click*), dan tekan lama (*long press*):

### 1. Tombol BOOT (GPIO 0) — Asisten Suara & Kontrol Musik
- **Klik 1x (Single Click):**
  - Jika musik YouTube sedang diputar $ightarrow$ **Stop pemutaran musik**.
  - Jika AI sedang berbicara $ightarrow$ **Interupsi / hentikan suara AI** seketika.
  - Jika dalam kondisi standby $ightarrow$ **Mulai mendengarkan / Toggle Chat** (bicara langsung tanpa wake word).
- **Klik 2x Cepat (Double Click):**
  - **Auto Play Test Music:** Otomatis mencari dan memutar musik remix santai dari YouTube.
- **Tekan Tahan (> 3 Detik):**
  - **Reset Wi-Fi / Masuk Web Config:** Membuka kembali Access Point `192.168.4.1` untuk konfigurasi ulang jaringan/hardware.

### 2. Tombol Volume UP (GPIO 14) — Volume & Rotasi
- **Klik 1x (Single Click):**
  - Mode Normal/Musik $ightarrow$ **Menaikkan volume speaker +10%** (disertai indikator level pada layar).
  - Mode Live Camera Streaming $ightarrow$ **Zoom In** (perbesar gambar 1x $ightarrow$ 2x $ightarrow$ 3x).
- **Klik 2x Cepat (Double Click):**
  - **Rotasi Layar 180°:** Membalikkan orientasi tampilan layar 180 derajat secara langsung tanpa restart.
- **Tekan Tahan (Long Press):**
  - **Volume Maksimal (100%):** Mengatur volume langsung ke tingkat tertinggi.

### 3. Tombol Volume DOWN (GPIO 46) — Volume & Switch Mode
- **Klik 1x (Single Click):**
  - Mode Normal/Musik $ightarrow$ **Menurunkan volume speaker -10%**.
  - Mode Live Camera Streaming $ightarrow$ **Zoom Out** (memperkecil tingkat zoom gambar).
- **Klik 2x Cepat (Double Click):**
  - **Beralih Mode (Switch Mode):** Berpindah antara **Mode XiaoZhi (Voice Assistant)** dan **Mode Chronchi (Smartwatch / BLE Clock)**.
- **Tekan Tahan (Long Press):**
  - **Mute / Senyap (0%):** Mematikan output audio speaker sepenuhnya.

---

## 5. Mode Ganda: XiaoZhi AI & Chronchi Companion

Perangkat memiliki 2 mode operasional yang dapat dialihkan sewaktu-waktu:
1. **Mode XiaoZhi (Asisten AI Cerdas):**
   - Mendukung interaksi suara real-time via WebSocket / MQTT.
   - Streaming lagu dari YouTube secara adaptif (bitrate stabil hemat RAM).
   - Pengenalan visual kamera (jika varian CAM terpasang).
   - Menjalankan 36+ Tools MCP (Smart Home, cuaca, alarm, kalkulator, info perangkat).
2. **Mode Chronchi (Smartwatch / Jam Pintar):**
   - Menampilkan antarmuka jam digital elegan (waktu, tanggal, indikator baterai).
   - Terkoneksi via Bluetooth Low Energy (BLE) ke smartphone (Chronchi Companion App).
   - Menampilkan notifikasi pesan, status cuaca, dan arah navigasi dari smartphone.
   - Pada mode ini:
     - Tombol 1 (BOOT) 1x: Ganti layar tampilan (*cycle screen*).
     - Tombol 1 (BOOT) 2x: Ganti tema visual (*cycle theme*).
     - Tombol 2 (GPIO 14) 2x: Rotasi layar 180°.
     - Tombol 3 (GPIO 46) 2x: Kembali ke Mode XiaoZhi AI.

---

## 6. Tools MCP Khusus Hardware

Firmware XiaoZhi menyertakan tool MCP internal khusus untuk diagnosa perangkat:
1. **`self.get_hardware_specs`**:
   - Memeriksa detail chip ESP32-S3, ukuran Flash, kapasitas dan sisa PSRAM, status kamera, tipe layar, dan pinout audio secara otomatis.
   - *Contoh ucapan:* *"Halo XiaoZhi, bagaimana spesifikasi hardware perangkat ini?"*
2. **`self.get_buttons_guide`**:
   - Menjelaskan seluruh daftar fungsi ketiga tombol fisik kepada pengguna lewat suara.
   - *Contoh ucapan:* *"Halo XiaoZhi, jelaskan fungsi tombol yang ada di perangkatmu."*
