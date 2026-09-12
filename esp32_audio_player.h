/**
 * YouTube Audio Player untuk ESP32 + MAX98357A + INMP441
 * 
 * Polling server EduSmart untuk audio commands,
 * download M4A, decode, output ke I2S (MAX98357A).
 * 
 * INTEGRASI: Tambah file ini ke firmware XiaoZhi ESP32,
 *            panggil audio_player_init() di setup(),
 *            panggil audio_player_loop() di loop().
 * 
 * Koneksi: MAX98357A (BCLK, LRC, DIN) ke pin I2S yang benar.
 */

#ifndef AUDIO_PLAYER_H
#define AUDIO_PLAYER_H

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>

// ============== KONFIGURASI ==============
// Ganti dengan token device dari dashboard EduSmart
#define AUDIO_DEVICE_TOKEN  "YOUR_DEVICE_TOKEN_HERE"

// URL server EduSmart (sesuaikan dengan deploy)
// Kalau lokal: http://192.168.x.x:7860
// Kalau HF Spaces: https://username-edusmart.hf.space
#define AUDIO_SERVER_URL    "http://192.168.1.100:7860"

// Polling interval (ms)
#define AUDIO_POLL_INTERVAL 3000

// I2S pins untuk MAX98357A (sesuaikan dengan wiring kamu)
#define I2S_BCLK_PIN    26
#define I2S_LRC_PIN     25
#define I2S_DOUT_PIN    22

// I2S config
#define I2S_PORT        I2S_NUM_0
#define I2S_SAMPLE_RATE 44100
#define I2S_BITS        16
#define I2S_CHANNELS    2

// Buffer size untuk streaming
#define AUDIO_BUFFER_SIZE 4096

// ============== STATE ==============
static bool audio_playing = false;
static String current_audio_id = "";
static unsigned long last_poll_time = 0;

// ============== I2S SETUP ==============
void audio_i2s_setup() {
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = I2S_SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = 1024,
        .use_apll = false,
        .tx_desc_auto_clear = true,
    };
    
    i2s_pin_config_t pin_config = {
        .bck_io_num = I2S_BCLK_PIN,
        .ws_io_num = I2S_LRC_PIN,
        .data_out_num = I2S_DOUT_PIN,
        .data_in_num = I2S_PIN_NO_CHANGE,
    };
    
    i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
    i2s_set_pin(I2S_PORT, &pin_config);
    i2s_set_clk(I2S_PORT, I2S_SAMPLE_RATE, I2S_BITS, I2S_CHANNEL_STEREO);
    
    Serial.println("[AudioPlayer] I2S initialized");
}

// ============== POLL SERVER ==============
/**
 * Poll server untuk audio commands baru.
 * Return: JSON array of commands, atau empty array.
 */
JsonDocument poll_audio_commands() {
    JsonDocument doc;
    
    if (WiFi.status() != WL_CONNECTED) {
        return doc;
    }
    
    HTTPClient http;
    String url = String(AUDIO_SERVER_URL) + "/api/device/audio/commands";
    http.begin(url);
    http.addHeader("X-Device-Token", AUDIO_DEVICE_TOKEN);
    http.setTimeout(5000);
    
    int code = http.GET();
    if (code == 200) {
        String payload = http.getString();
        deserializeJson(doc, payload);
    } else {
        Serial.printf("[AudioPlayer] Poll failed: %d\n", code);
    }
    http.end();
    return doc;
}

// ============== ACK COMMAND ==============
void ack_audio_command(const String& command_id) {
    if (WiFi.status() != WL_CONNECTED) return;
    
    HTTPClient http;
    String url = String(AUDIO_SERVER_URL) + "/api/device/audio/ack";
    http.begin(url);
    http.addHeader("Content-Type", "application/x-www-form-urlencoded");
    http.addHeader("X-Device-Token", AUDIO_DEVICE_TOKEN);
    
    String body = "command_id=" + command_id + "&token=" + AUDIO_DEVICE_TOKEN;
    http.POST(body);
    http.end();
    
    Serial.printf("[AudioPlayer] Acked command: %s\n", command_id.c_str());
}

// ============== PLAY AUDIO STREAM ==============
/**
 * Download dan putar audio dari URL via I2S.
 * Blocking - akan loop sampai selesai.
 */
bool play_audio_stream(const String& stream_url, const String& title) {
    Serial.printf("[AudioPlayer] Playing: %s\n", title.c_str());
    Serial.printf("[AudioPlayer] URL: %s\n", stream_url.c_str());
    
    audio_playing = true;
    
    HTTPClient http;
    http.begin(stream_url);
    http.addHeader("User-Agent", "ESP32-AudioPlayer/1.0");
    http.setTimeout(15000);
    
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[AudioPlayer] HTTP error: %d\n", code);
        http.end();
        audio_playing = false;
        return false;
    }
    
    WiFiClient* stream = http.getStreamPtr();
    uint8_t buffer[AUDIO_BUFFER_SIZE];
    size_t total_bytes = 0;
    
    // Skip M4A header (ftyp + mdat atoms) untuk raw PCM
    // NOTE: Ini simplified. Untuk production, perlu AAC decoder library.
    // Contoh: ESP8266Audio library (AudioGeneratorAAC + AudioOutputI2S)
    
    // Untuk M4A/AAC, kita perlu decoder. Sementara, output raw bytes
    // (akan noise tanpa decoder - butuh library ESP8266Audio)
    
    while (http.connected() && stream->available()) {
        size_t bytes_read = stream->readBytes(buffer, AUDIO_BUFFER_SIZE);
        if (bytes_read > 0) {
            size_t bytes_written = 0;
            // Write ke I2S - untuk raw PCM
            // Kalau pakai AAC decoder, ini akan di-replace
            i2s_write(I2S_PORT, buffer, bytes_read, &bytes_written, portMAX_DELAY);
            total_bytes += bytes_written;
        }
        
        // Yield untuk watchdog
        yield();
    }
    
    http.end();
    audio_playing = false;
    
    Serial.printf("[AudioPlayer] Done: %d bytes played\n", total_bytes);
    return total_bytes > 0;
}

// ============== INIT ==============
void audio_player_init() {
    audio_i2s_setup();
    Serial.println("[AudioPlayer] Ready. Polling for audio commands...");
}

// ============== LOOP (panggil di loop()) ==============
void audio_player_loop() {
    // Skip kalau sedang playing
    if (audio_playing) return;
    
    // Skip kalau belum waktunya poll
    unsigned long now = millis();
    if (now - last_poll_time < AUDIO_POLL_INTERVAL) return;
    last_poll_time = now;
    
    // Poll server
    JsonDocument doc = poll_audio_commands();
    if (!doc.is<JsonObject>()) return;
    
    int count = doc["count"] | 0;
    if (count == 0) return;
    
    JsonArray commands = doc["commands"];
    for (JsonObject cmd : commands) {
        String id = cmd["id"] | "";
        String title = cmd["title"] | "Unknown";
        String stream_url = cmd["stream_url"] | "";
        
        if (stream_url.length() == 0) continue;
        
        // Prepend server URL kalau relative
        if (stream_url.startsWith("/")) {
            stream_url = String(AUDIO_SERVER_URL) + stream_url;
        }
        
        Serial.printf("[AudioPlayer] New command: %s\n", title.c_str());
        
        // Play audio
        bool success = play_audio_stream(stream_url, title);
        
        // Ack ke server
        if (success) {
            ack_audio_command(id);
        }
        
        // Hanya play 1 lagu per cycle
        break;
    }
}

// ============== STATUS ==============
bool is_audio_playing() {
    return audio_playing;
}

#endif // AUDIO_PLAYER_H
