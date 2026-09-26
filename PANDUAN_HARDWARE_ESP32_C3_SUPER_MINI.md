# Panduan Operasional & Hardware: ESP32-C3 Super Mini / Pro

Dokumentasi lengkap perakitan hardware, skema wiring audio shared-clock, konfigurasi jaringan Web Portal, dan panduan fungsi tombol fisik untuk firmware **Xiaozhi AI seri ESP32-C3 Super Mini / Pro**.

---

## 1. Karakteristik Hardware ESP32-C3

ESP32-C3 adalah mikrokontroler berbasis arsitektur **RISC-V 32-bit Single-Core @ 160MHz** yang dirancang untuk smart device berbiaya sangat terjangkau (~Rp 35.000) dan super compact.

### Spesifikasi Teknis:
- **Processor:** 32-bit RISC-V Single-Core @ 160MHz
- **Memory Flash:** 4MB Flash SPI
- **SRAM Internal:** ~400KB SRAM (Tanpa PSRAM eksternal)
- **Periferal I2S:** **1 buah I2S Controller** (Memerlukan konfigurasi *Shared-Clock Full Duplex* untuk mic dan speaker)
- **Konektivitas:** Wi-Fi 2.4 GHz (802.11 b/g/n) & Bluetooth 5 (LE)
- **Sistem Suara:** Adaptive Chunked Opus Streaming (12k–24k) hemat RAM agar tidak membebani memori internal.

---

## 2. Diagram Skema Wiring Pinout (Hardware)

Karena ESP32-C3 hanya memiliki 1 periferal I2S, modul Microphone (INMP441) dan Amplifier Speaker (MAX98357A) **berbagi pin clock (BCLK & WS) secara bersamaan**.

```
        ESP32-C3 Super Mini          INMP441 (Mic)         MAX98357A (Speaker)
    ┌─────────────────────────┐   ┌─────────────────┐    ┌─────────────────────┐
    │         GPIO 5          ├───┤ SCK (Bit Clock) ├────┤ BCLK (Bit Clock)    │ (Shared Clock)
    │         GPIO 6          ├───┤ WS  (Word Sel)  ├────┤ LRC  (Word Select)  │ (Shared Clock)
    │         GPIO 4          ├───┤ SD  (Data In)   │    │                     │
    │         GPIO 7          ├───┼─────────────────┼────┤ DIN  (Data Masuk)   │
    │         3.3V            ├───┤ VDD             │    │                     │
    │         5V (VBUS / USB) ├───┼─────────────────┼────┤ VIN (Wajib 5V!)     │
    │         GND             ├───┤ GND & L/R -> GND│    │ GND                 │
    └─────────────────────────┘   └─────────────────┘    └─────────────────────┘
```

### Tabel Pemetaan Pin Lengkap:
| Komponen | Pin Modul | Terhubung ke ESP32-C3 | Keterangan Penting |
| :--- | :--- | :--- | :--- |
| **INMP441 (Mic I2S)** | `SCK` | **GPIO 5** | Clock bersama MAX98357A |
| | `WS` | **GPIO 6** | Word Select bersama MAX98357A |
| | `SD` | **GPIO 4** | Serial Data Masuk dari Mic |
| | `L/R` | **GND** | Wajib hubungkan ke GND (Channel Kiri / Mono) |
| | `VDD` / `GND` | **3.3V** / **GND** | Daya microphone |
| **MAX98357A (Speaker)**| `BCLK` | **GPIO 5** | Clock bersama INMP441 |
| | `LRC` | **GPIO 6** | Word Select bersama INMP441 |
| | `DIN` | **GPIO 7** | Serial Data Out ke Amplifier |
| | `VIN` | **5V (VBUS / 5V USB)**| **Wajib 5V** (Jika diberi 3.3V suara akan pecah/distorsi) |
| | `GND` | **GND** | Ground bersama |
| **OLED SSD1306 (I2C)**| `SDA` | **GPIO 0** | Data I2C Layar 128x64 |
| | `SCL` | **GPIO 10** | Clock I2C Layar |
| | `VCC` / `GND` | **3.3V** / **GND** | Catu daya layar |
| **Tombol 1 (BOOT / Chat)**| Pin Tombol | **GPIO 3** & **GND** | Tombol navigasi dan kontrol utama |
| **Tombol 2 (Handsfree - Opsional)**| Pin Tombol | **GPIO 2** & **GND** | Tombol sekunder toggle hands-free |
| **Sensor Baterai 1S** | ADC Divider | **GPIO 1** (ADC1_CH1) | Pembagi tegangan 100kΩ/100kΩ dari Bat+ ke GND |

---

## 3. Panduan Pengoperasian Tombol Fisik

Firmware ESP32-C3 Super Mini telah diperbarui dengan gestur tombol yang ringkas dan intuitif:

### A. Tombol Utama (GPIO 3) — Kontrol Asisten, Musik & Mode
| Gestur | Fungsi Utama | Keterangan Lengkap |
| :--- | :--- | :--- |
| **Klik 1x (Singkat)** | **Bicara / Stop / Interupsi** | • Mulai berbicara dengan asisten AI (*Push to Talk*).<br>• Menghentikan suara AI seketika jika AI sedang berbicara.<br>• Menghentikan lagu lokal atau musik YouTube jika sedang berputar. |
| **Klik 2x Cepat (Double Click)** | **Beralih Mode (Switch Mode)** | • Berpindah dari **Mode XiaoZhi (Voice AI)** ke **Mode Chronchi (Smartwatch / BLE Clock)**.<br>• Jika sedang di Chronchi, **klik 2x cepat lagi** untuk kembali ke **Mode XiaoZhi**. |
| **Tekan Tahan 5 Detik (Long Press)** | **Masuk Konfigurasi Wi-Fi** | Mengaktifkan hotspot Wi-Fi Access Point (`192.168.4.1`) untuk ganti Wi-Fi atau setup awal. |

### B. Tombol Sekunder (GPIO 2 - Opsional)
| Gestur | Fungsi Utama | Keterangan |
| :--- | :--- | :--- |
| **Klik 1x (Singkat)** | **Toggle Hands-Free (ON / OFF)** | Mengaktifkan atau menonaktifkan mode deteksi suara otomatis (VAD). |
| **Tekan Tahan 5 Detik** | **Reset SSID Wi-Fi** | Membersihkan data jaringan Wi-Fi yang tersimpan dan reboot perangkat. |

---

## 4. Alur Konfigurasi Awal (Web Portal 192.168.4.1)

1. **Nyalakan ESP32-C3:**
   - Saat pertama kali dinyalakan (atau saat tombol GPIO 3 ditekan tahan 5 detik), perangkat masuk ke mode hotspot konfigurasi.
2. **Konek ke Hotspot:**
   - Hubungkan HP atau Laptop ke Wi-Fi bernama: **`XiaoZhi-XXXX`** (tanpa kata sandi).
3. **Buka Web Portal:**
   - Buka browser dan ketik alamat IP: `http://192.168.4.1`.
4. **Pengaturan:**
   - Pilih nama Wi-Fi rumah (2.4 GHz) dan masukkan password.
   - Pada menu lanjutan (*Advanced*), Anda dapat memilih kata pemanggil (*Wake Word*) seperti *"Hi Jason"*, *"Hi Lexin"*, atau *"Ni Hao Xiaozhi"*.
5. **Simpan & Selesai:**
   - Klik Simpan, ESP32-C3 akan reboot dan langsung terhubung ke server cloud.

---

## 5. Fitur Cerdas & Perintah Suara Khusus

### A. Mode Hands-Free (Deteksi Hening Otomatis)
- Anda dapat langsung berbicara tanpa menekan tombol apa pun.
- Algoritma VAD (*Voice Activity Detection*) mendeteksi kapan Anda selesai berbicara dan AI akan merespons.
- **Hemat Daya:** Jika hening selama 30 detik, perangkat otomatis masuk ke mode *Low-Power Standby*.

### B. Pemutar Musik Lokal (Offline)
Dapat memutar file audio yang tersimpan di flash internal tanpa koneksi internet:
- *"Putar lagu"* / *"Nyanyi"* $
ightarrow$ Memutar `song1.ogg`.
- *"Lagu dua"* $
ightarrow$ Memutar `song2.ogg`.
- *"Lagu tiga"* $
ightarrow$ Memutar `song3.ogg`.
- *"Stop"* / *"Berhenti"* $
ightarrow$ Menghentikan pemutaran musik.

### C. Perintah Suara Standby & Reset
- Ucapkan *"Udahan dulu"*, *"Sampai jumpa"*, atau *"Bye"* untuk memasukkan perangkat ke mode hening standby.
- Ucapkan *"Reset wifi"* $
ightarrow$ Konfirmasi dengan *"Ya"* untuk mereset kredensial Wi-Fi secara hands-free.

---

## 6. Konfigurasi Lanjutan: Multi-SSID Failover (Backup Wi-Fi Otomatis)

Pada halaman konfigurasi Web Portal `192.168.4.1` (tab **Jaringan Wi-Fi**), Anda dapat memilih mode koneksi:
1. **Mode 1 Wi-Fi (Utama):** Menghubungkan ke 1 router rumah/kantor tetap.
2. **Mode Multi Wi-Fi (Failover Otomatis):** Anda dapat mendaftarkan beberapa SSID sekaligus (Wi-Fi Utama, Hotspot HP Cadangan 1, dan Cadangan 2). Jika Wi-Fi utama mati lampu atau di luar jangkauan, ESP32-C3 otomatis mengalihkan koneksi ke Hotspot HP dalam hitungan detik tanpa terputus lama.
