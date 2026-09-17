/**
 * YouTube Audio Player untuk ESP32 + MAX98357A
 * 
 * ALUR SISTEM:
 * 1. User berbicara ke XiaoZhi: "Putar lagu X"
 * 2. XiaoZhi MCP memanggil tool `play_youtube_song` di server.
 * 3. Server mencari lagu via yt-dlp & memasukkan ke audio_queue (status: pending).
 * 4. ESP32 melakukan polling ke server dengan mengirimkan MAC Address (dan opsional Token).
 *    -> Saat MAC diterima, server OTOMATIS mendaftarkan ESP32 ke akun user di tabel `registered_devices`
 *    -> Status di Admin & Profil langsung berubah dari "Menunggu Board" menjadi ID MAC board yang valid!
 * 5. Server mengembalikan stream_url (Ogg/Opus Mono 24kHz atau HTTP Stream).
 * 6. ESP32 mendownload stream audio dan memutarnya ke I2S DAC (MAX98357A).
 * 7. Setelah selesai, ESP32 mengirim konfirmasi (ACK) ke server.
 * 
 * WIRING MAX98357A -> ESP32:
 *   BCLK (Bit Clock)    -> GPIO 26
 *   LRC / WS (Word Sel) -> GPIO 25
 *   DIN (Data In)       -> GPIO 22
 *   GND                 -> GND
 *   VIN                 -> 5V atau 3.3V
 */

#ifndef AUDIO_PLAYER_H
#define AUDIO_PLAYER_H

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>

// ============== KONFIGURASI ==============
// URL server XiaoZhi Indonesia (sesuaikan dengan domain / IP kamu)
// Contoh: "https://xiaozhiscig.biz.id" atau "http://192.168.1.100:7860"
#define AUDIO_SERVER_URL    "https://xiaozhiscig.biz.id"

// Token user dari dashboard XiaoZhi Indonesia (opsional jika sudah pair via MAC)
#define AUDIO_DEVICE_TOKEN  ""

// Polling interval dalam milidetik (rekomendasi: 2500 - 3500 ms)
#define AUDIO_POLL_INTERVAL 3000

// I2S Pins untuk MAX98357A (sesuaikan pin GPIO board kamu)
#define I2S_BCLK_PIN        26
#define I2S_LRC_PIN         25
#define I2S_DOUT_PIN        22

// I2S Configuration
#define I2S_PORT            I2S_NUM_0
#define I2S_SAMPLE_RATE     24000   // Server stream default: 24kHz Mono Opus
#define I2S_BITS            16
#define I2S_CHANNELS        1       // Mono

// Buffer streaming (4KB)
#define AUDIO_BUFFER_SIZE   4096

// ============== STATE ==============
static bool audio_playing = false;
static String current_audio_id = "";
static unsigned long last_poll_time = 0;
static String g_cached_mac = "";

// ============== GET MAC ADDRESS ==============
// Mengambil MAC address WiFi dalam format standard AA:BB:CC:DD:EE:FF
// PENTING: MAC address ini yang digunakan server untuk mengenali board kamu!
inline String get_audio_device_mac() {
    if (g_cached_mac.length() > 0) {
        return g_cached_mac;
    }
    g_cached_mac = WiFi.macAddress();
    return g_cached_mac;
}

// ============== I2S SETUP ==============
void audio_i2s_setup() {
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = I2S_SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_RIGHT, // Mono out ke speaker
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
    i2s_set_clk(I2S_PORT, I2S_SAMPLE_RATE, I2S_BITS, I2S_CHANNEL_MONO);
    
    Serial.println("[AudioPlayer] I2S MAX98357A Siap (GPIO 26, 25, 22)");
}

// ============== POLL SERVER ==============
/**
 * Polling server untuk memeriksa apakah ada perintah audio YouTube baru.
 * PENTING: Mengirim ?mac=... agar server mencatat MAC board & status Admin langsung aktif!
 */
JsonDocument poll_audio_commands() {
    JsonDocument doc;
    
    if (WiFi.status() != WL_CONNECTED) {
        return doc;
    }
    
    HTTPClient http;
    String mac = get_audio_device_mac();
    String url = String(AUDIO_SERVER_URL) + "/api/device/audio/commands?mac=" + mac;
    
    String token = String(AUDIO_DEVICE_TOKEN);
    token.trim();
    if (token.length() > 0) {
        url += "&token=" + token;
    }
    
    http.begin(url);
    http.addHeader("Device-Id", mac);
    http.addHeader("X-Device-Mac", mac);
    if (token.length() > 0) {
        http.addHeader("X-Device-Token", token);
    }
    http.setTimeout(5000);
    
    int code = http.GET();
    if (code == 200) {
        String payload = http.getString();
        deserializeJson(doc, payload);
    } else if (code > 0) {
        Serial.printf("[AudioPlayer] Poll status: HTTP %d\n", code);
    }
    http.end();
    return doc;
}

// ============== ACK COMMAND ==============
/**
 * Konfirmasi ke server bahwa lagu telah berhasil diputar.
 */
void ack_audio_command(const String& command_id) {
    if (WiFi.status() != WL_CONNECTED) return;
    
    HTTPClient http;
    String url = String(AUDIO_SERVER_URL) + "/api/device/audio/ack";
    http.begin(url);
    http.addHeader("Content-Type", "application/x-www-form-urlencoded");
    http.addHeader("Device-Id", get_audio_device_mac());
    
    String token = String(AUDIO_DEVICE_TOKEN);
    token.trim();
    String body = "command_id=" + command_id + "&mac=" + get_audio_device_mac();
    if (token.length() > 0) {
        body += "&token=" + token;
    }
    
    http.POST(body);
    http.end();
    
    Serial.printf("[AudioPlayer] ACK Perintah Audio: %s\n", command_id.c_str());
}

// ============== PLAY AUDIO STREAM ==============
/**
 * Download & stream audio dari stream_url ke I2S DAC.
 */
bool play_audio_stream(const String& stream_url, const String& title) {
    Serial.printf("[AudioPlayer] Memutar: %s\n", title.c_str());
    Serial.printf("[AudioPlayer] URL: %s\n", stream_url.c_str());
    
    audio_playing = true;
    
    HTTPClient http;
    http.begin(stream_url);
    http.addHeader("User-Agent", "ESP32-AudioPlayer/2.0");
    http.addHeader("Device-Id", get_audio_device_mac());
    http.addHeader("X-Device-Mac", get_audio_device_mac());
    http.setTimeout(15000);
    
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[AudioPlayer] HTTP Stream Error: %d\n", code);
        http.end();
        audio_playing = false;
        return false;
    }
    
    WiFiClient* stream = http.getStreamPtr();
    uint8_t buffer[AUDIO_BUFFER_SIZE];
    size_t total_bytes = 0;
    
    while (http.connected() && (stream->available() || stream->connected())) {
        size_t available_bytes = stream->available();
        if (available_bytes > 0) {
            size_t bytes_to_read = available_bytes > sizeof(buffer) ? sizeof(buffer) : available_bytes;
            size_t bytes_read = stream->readBytes(buffer, bytes_to_read);
            if (bytes_read > 0) {
                size_t bytes_written = 0;
                i2s_write(I2S_PORT, buffer, bytes_read, &bytes_written, portMAX_DELAY);
                total_bytes += bytes_written;
            }
        }
        yield(); // Hindari Watchdog Timer reset
    }
    
    http.end();
    audio_playing = false;
    
    Serial.printf("[AudioPlayer] Selesai: %u bytes diputar ke I2S\n", (unsigned int)total_bytes);
    return total_bytes > 0;
}

// ============== INIT ==============
void audio_player_init() {
    Serial.println("\n[AudioPlayer] Inisialisasi Audio Player ESP32...");
    Serial.printf("[AudioPlayer] Server URL: %s\n", AUDIO_SERVER_URL);
    Serial.printf("[AudioPlayer] MAC Address: %s\n", get_audio_device_mac().c_str());
    audio_i2s_setup();
    Serial.println("[AudioPlayer] Siap menerima perintah audio dari XiaoZhi!");
}

// ============== LOOP ==============
void audio_player_loop() {
    // Lewati jika sedang memutar lagu
    if (audio_playing) return;
    
    // Cek interval polling
    unsigned long now = millis();
    if (now - last_poll_time < AUDIO_POLL_INTERVAL) return;
    last_poll_time = now;
    
    // Poll perintah baru dari server
    JsonDocument doc = poll_audio_commands();
    if (!doc.is<JsonObject>()) return;
    
    JsonArray commands = doc["commands"];
    if (commands.size() == 0) return;
    
    for (JsonObject cmd : commands) {
        String id = cmd["id"] | "";
        String title = cmd["title"] | "Unknown Track";
        String stream_url = cmd["stream_url"] | "";
        
        if (stream_url.length() == 0) continue;
        
        // Perbaiki jika relative URL
        if (stream_url.startsWith("/")) {
            stream_url = String(AUDIO_SERVER_URL) + stream_url;
        }
        
        Serial.printf("[AudioPlayer] Perintah audio masuk: %s\n", title.c_str());
        
        // Putar audio
        bool success = play_audio_stream(stream_url, title);
        
        // Kirim ACK ke server agar perintah ditandai selesai
        if (success && id.length() > 0) {
            ack_audio_command(id);
        }
        
        break; // 1 lagu per loop cycle
    }
}

// ============== STATUS ==============
bool is_audio_playing() {
    return audio_playing;
}

#endif // AUDIO_PLAYER_H
