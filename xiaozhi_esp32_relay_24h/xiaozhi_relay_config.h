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
// Menggunakan subdomain DNS-Only 'mqtt.xiaozhiscig.biz.id' (atau IP '163.61.58.235')
const char* MQTT_BROKER   = "mqtt.xiaozhiscig.biz.id"; // Atau "163.61.58.235"
const int   MQTT_PORT     = 1883;

// Kredensial Perangkat (Cocokkan dengan yang ada di menu Relay Nyata Web UI)
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
// - Untuk ESP8266 (D1 Mini/NodeMCU): { 5, 4, 14, 12 } // D1, D2, D5, D6
const int RELAY_PINS[NUM_RELAYS] = { 4, 16 };

// Logika Pemicuan: Sebagian besar modul relay di pasaran adalah ACTIVE LOW (true)
// - true  : Relay ON saat pin bernilai LOW (GND)
// - false : Relay ON saat pin bernilai HIGH (3.3V)
const bool RELAY_ACTIVE_LOW = true;

#endif // XIAOZHI_RELAY_CONFIG_H
