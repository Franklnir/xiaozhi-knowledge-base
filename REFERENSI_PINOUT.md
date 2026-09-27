# Referensi Cepat Pinout Hardware Xiaozhi AI (ESP32-C3 & ESP32-S3)

Dokumen ini berisi tabel ringkasan pemetaan pin GPIO hardware untuk perakitan modul audio (INMP441 & MAX98357A), layar (SSD1306 / ST7789), tombol kendali, dan sensor daya.

---

## 1. ESP32-C3 Mini / Super Mini

Pada ESP32-C3, karena hanya memiliki 1 kontroler I2S periferal, modul Microphone (INMP441) dan Amplifier Speaker (MAX98357A) berbagi pin Clock secara paralel (*Shared-Clock Full Duplex*).

### A. Audio I2S Bus (Shared Clock)

| Modul | Pin Modul | Terhubung ke ESP32-C3 | Keterangan & Catu Daya |
| :--- | :--- | :--- | :--- |
| **INMP441 (Mic)** | `VDD` | **3.3V** | Catu daya mic |
| | `GND` | **GND** | Ground |
| | `L/R` | **GND** | Wajib ke GND (Mono channel kiri) |
| | `SCK` | **GPIO 5** | Bit Clock bersama (*Shared Clock*) |
| | `WS` | **GPIO 6** | Word Select bersama (*Shared Clock*) |
| | `SD` | **GPIO 4** | Data mic masuk (*Serial Data In*) |
| **MAX98357A (Spk)** | `VIN` | **5V (VBUS / USB)** | **Wajib 5V** untuk suara jernih & kencang |
| | `GND` | **GND** | Ground bersama |
| | `BCLK` | **GPIO 5** | Bit Clock bersama (Paralel dari SCK INMP441) |
| | `LRC` | **GPIO 6** | Word Select bersama (Paralel dari WS INMP441) |
| | `DIN` | **GPIO 7** | Data speaker keluar (*Serial Data Out*) |

---

### B. Layar OLED SSD1306 128x64 (I2C)

| Pin Modul OLED | Terhubung ke ESP32-C3 | Keterangan |
| :--- | :--- | :--- |
| `VCC` | **3.3V** | Catu daya layar |
| `GND` | **GND** | Ground layar |
| `SDA` | **GPIO 0** | I2C Serial Data |
| `SCL` | **GPIO 10** | I2C Serial Clock |

---

### C. Tombol Fisik & Sensor Baterai

| Komponen / Fitur | Terhubung ke ESP32-C3 | Tipe Rangkaian & Keterangan |
| :--- | :--- | :--- |
| **Tombol Utama** | **GPIO 3** & **GND** | *Active Low* (Bicara / Klik 2x Chronchi / Tahan 5s Setup Wi-Fi) |
| **Tombol Sekunder** | **GPIO 2** & **GND** | *Active Low* (Toggle Hands-free / Reset SSID) |
| **Sensor Baterai 1S** | **GPIO 1** (`ADC1_CH1`) | Pembagi tegangan (*voltage divider*) resistor 100kΩ / 100kΩ dari BAT+ |

---
---

## 2. ESP32-S3 (WROOM-1 / DevKit / CAM N16R8)

Pada ESP32-S3, periferal I2S dapat berjalan terpisah (*Simplex Dual-Bus*) untuk stabilitas transmisi audio dua arah kualitas tinggi.

### A. Microphone INMP441 (I2S Input)

| Pin INMP441 | Terhubung ke ESP32-S3 | Keterangan |
| :--- | :--- | :--- |
| `VDD` | **3.3V** | Catu daya microphone |
| `GND` | **GND** | Ground |
| `L/R` | **GND** | Wajib ke GND (Mono channel kiri) |
| `SCK` | **GPIO 2** | Serial Bit Clock |
| `WS` | **GPIO 1** | Word Select (Left/Right Clock) |
| `SD` | **GPIO 42** | Serial Data Masuk dari Mic |

---

### B. Speaker Amplifier MAX98357A (I2S Output)

| Pin MAX98357A | Terhubung ke ESP32-S3 | Keterangan |
| :--- | :--- | :--- |
| `VIN` | **5V (Disarankan)** atau **3.3V** | Catu daya amplifier speaker |
| `GND` | **GND** | Ground bersama |
| `BCLK` | **GPIO 40** | Bit Clock Speaker |
| `LRC` | **GPIO 41** | Word Select Speaker |
| `DIN` | **GPIO 39** | Serial Data Keluar ke Amplifier |

---

### C. Layar LCD ST7789 (SPI) *(Opsional)*

| Pin Layar ST7789 | Terhubung ke ESP32-S3 | Keterangan |
| :--- | :--- | :--- |
| `VCC` | **3.3V** | Catu daya layar |
| `GND` | **GND** | Ground |
| `SCL / CLK` | **GPIO 12** | SPI Bus Clock |
| `SDA / MOSI` | **GPIO 11** | SPI Master Out / Data |
| `RES / RST` | **GPIO 47** | Hardware Reset |
| `DC / RS` | **GPIO 48** | Data / Command Control |
| `CS` | **GPIO 45** | Chip Select *(Hanya untuk layar 8-Pin; 7-Pin tanpa CS)* |
| `BLK / BL / PWM` | **GPIO 38** | Backlight Control / PWM Dimming |

---

### D. Tombol Fisik (Active Low - Internal Pull-up)

| Tombol Fisik | Terhubung ke ESP32-S3 | Keterangan Wiring |
| :--- | :--- | :--- |
| **Tombol BOOT / AI Voice** | **GPIO 0** & **GND** | Hubungkan pin tombol ke GPIO 0 dan kaki lainnya ke GND |
| **Tombol Volume UP** | **GPIO 14** & **GND** | Hubungkan pin tombol ke GPIO 14 dan kaki lainnya ke GND |
| **Tombol Volume DOWN** | **GPIO 46** & **GND** | Hubungkan pin tombol ke GPIO 46 dan kaki lainnya ke GND |
