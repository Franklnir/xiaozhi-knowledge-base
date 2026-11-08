#pragma once
#ifndef XIAOZHI_RELAY_CONFIG_H
#define XIAOZHI_RELAY_CONFIG_H

// ==============================================================================
// KONFIGURASI SMART HOME RELAY XIAOZHI AI (24/7 NON-STOP)
// File ini dapat di-import langsung ke Arduino IDE / PlatformIO
// ==============================================================================

// 1. KONEKSI WI-FI RUMAH
const char* WIFI_SSID     = "WIFI_RUMAH_ANDA";
const char* WIFI_PASSWORD = "PASSWORD_WIFI";

// 2. KONEKSI BROKER MQTT XIAOZHI GATEWAY
// Menggunakan domain resmi 'mqtt.xiaozhiscig.biz.id'
const char* MQTT_BROKER   = "mqtt.xiaozhiscig.biz.id";
const int   MQTT_PORT     = 1883;

// Kredensial Autentikasi Broker MQTT (Opsional, kosongkan jika broker tanpa auth)
const char* MQTT_USER     = "xiaozhi_device";
const char* MQTT_PASSWORD = "";

// Kredensial Perangkat (Cocokkan dengan yang ada di menu Relay Nyata Web UI)
// CATATAN PENTING:
// Nama saklar & perintah suara (misal: "Lampu 1", "Lampu Kamar") diatur di Web Dashboard Xiaozhi.
// Flashing board fisik HANYA mengunggah program mikrokontroler. Jika ingin mengubah nama relay,
// ubah atau edit ruangan langsung di Web Dashboard Xiaozhi (menu Relay Nyata).
const char* CLIENT_ID     = "device-ruangan-anda";
const char* DEVICE_TOKEN  = "TOKEN_PERANGKAT_DARI_WEB";
const char* ROOM_SLUG     = "ruang-tamu";

// Topik MQTT Otomatis
#define TOPIC_COMMAND      "xiaozhi/relay/" ROOM_SLUG "/set"
#define TOPIC_STATE        "xiaozhi/relay/" ROOM_SLUG "/state"
#define TOPIC_AVAILABILITY "xiaozhi/relay/" ROOM_SLUG "/availability"

// 3. KONFIGURASI PIN HARDWARE RELAY
// Tentukan jumlah channel fisik (contoh: 1, 2, 4, atau 8 channel)
const int NUM_RELAYS = 2;

// Pemetaan Pin GPIO Aman (Urutan: Channel 1, Channel 2, ...)
// - Untuk ESP32 Klasik (WROOM-32) : { 4, 16, 17, 18, 19, 21, 22, 23 }
// - Untuk ESP32-C3 (RISC-V)        : { 4, 5, 6, 7, 10, 3 }
// - Untuk ESP32-S3                 : { 4, 5, 6, 7, 8, 9, 10, 11 }
// - Untuk ESP8266 (D1 Mini/NodeMCU): { 5, 4, 14, 12 } // WAJIB pin aman: D1, D2, D5, D6
//
// ⚠️ PERINGATAN KHUSUS ESP8266 (NodeMCU / D1 Mini):
// DILARANG KERAS menggunakan pin D3 (GPIO 0), D4 (GPIO 2), atau D8 (GPIO 15) untuk relay!
// Pin tersebut adalah Boot-Strapping Pin. Jika terhubung ke modul relay saat baru dinyalakan,
// modul relay akan menarik pin ke LOW/HIGH sehingga ESP8266 gagal booting (hang/offline).
const int RELAY_PINS[NUM_RELAYS] = { 4, 16 };

// Logika Pemicuan: Sebagian besar modul relay di pasaran adalah ACTIVE LOW (true)
// - true  : Relay ON saat pin bernilai LOW (GND)
// - false : Relay ON saat pin bernilai HIGH (3.3V)
const bool RELAY_ACTIVE_LOW = true;

// 4. INDIKATOR LED ONBOARD (STATUS & AKTIVITAS)
// Status LED bawaan board:
// - ESP32 Klasik (WROOM-32) : GPIO 2 (Active High: false)
// - ESP32-C3 Super Mini     : GPIO 8 (Active Low: true)
// - ESP32-S3 / S3 N16R8     : GPIO 21 (Active High: false)
// - ESP32-S2                : GPIO 15 (Active High: false)
// - ESP8266 (NodeMCU/D1)    : GPIO 2 (Active Low: true)
#if defined(CONFIG_IDF_TARGET_ESP32C3)
const int STATUS_LED_PIN = 8;
const bool STATUS_LED_ACTIVE_LOW = true;
#elif defined(CONFIG_IDF_TARGET_ESP32S3)
const int STATUS_LED_PIN = 21;
const bool STATUS_LED_ACTIVE_LOW = false;
#elif defined(CONFIG_IDF_TARGET_ESP32S2)
const int STATUS_LED_PIN = 15;
const bool STATUS_LED_ACTIVE_LOW = false;
#elif defined(ESP8266)
const int STATUS_LED_PIN = 2;
const bool STATUS_LED_ACTIVE_LOW = true;
#elif defined(LED_BUILTIN)
const int STATUS_LED_PIN = LED_BUILTIN;
const bool STATUS_LED_ACTIVE_LOW = false;
#else
const int STATUS_LED_PIN = 2;
const bool STATUS_LED_ACTIVE_LOW = false;
#endif

#endif // XIAOZHI_RELAY_CONFIG_H
