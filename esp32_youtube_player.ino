/**
 * ESP32 YouTube Audio Player - Firmware Sketch
 * 
 * Hardware: ESP32 DevKitV1 + MAX98357A (I2S DAC Amplifier)
 * 
 * Wiring MAX98357A ke ESP32:
 *   BCLK (Bit Clock)    -> GPIO 26
 *   LRC / WS (Word Sel) -> GPIO 25
 *   DIN (Data In)       -> GPIO 22
 *   GND                 -> GND
 *   VIN                 -> 5V (disarankan) atau 3.3V
 * 
 * Library Arduino yang dibutuhkan:
 *   1. ArduinoJson (by Benoit Blanchon) versi 6 atau 7
 * 
 * CARA KERJA & PANDUAN PENGGUNAAN:
 * 1. Masukkan SSID & Password WiFi kamu di WIFI_SSID & WIFI_PASS.
 * 2. Masukkan URL server EduSmart / XiaoZhi Indonesia di SERVER_URL.
 *    Contoh: "https://xiaozhiscig.biz.id" atau "https://username-xiaozhi.hf.space"
 * 3. (Opsional) Masukkan MCP_TOKEN jika ingin binding langsung ke user tertentu.
 *    Jika dikosongkan, server otomatis mengenali board dari MAC Address!
 * 4. Upload sketch ini ke board ESP32.
 * 5. Buka Serial Monitor (115200 baud). ESP32 akan mencetak IP dan MAC Address.
 * 6. Di dashboard admin, status user tidak akan lagi "Menunggu Board",
 *    melainkan langsung memunculkan kode MAC board ESP32 kamu!
 * 7. Katakan ke XiaoZhi: "Putar lagu Bohemian Rhapsody" atau lagu favoritmu.
 * 8. Speaker ESP32 otomatis memutar lagunya!
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>

// ============== KONFIGURASI ==============
#define WIFI_SSID       "NAMA_WIFI_KAMU"
#define WIFI_PASS       "PASSWORD_WIFI_KAMU"

// URL Server XiaoZhi Indonesia (tanpa tanda '/' di akhir)
#define SERVER_URL      "https://xiaozhiscig.biz.id"

// Token MCP User (Opsional: dari Profil web dashboard jika ingin manual binding)
#define MCP_TOKEN       ""

// I2S Pins untuk DAC MAX98357A
#define I2S_BCLK_PIN    26
#define I2S_LRC_PIN     25
#define I2S_DIN_PIN     22

// Polling interval (milidetik)
#define POLL_INTERVAL_MS 3000

// I2S Port & Audio Specs
#define I2S_PORT        I2S_NUM_0
#define I2S_SAMPLE_RATE 24000
#define I2S_BITS        16

// Buffer streaming audio (4KB)
#define AUDIO_BUF_SIZE  4096

// ============== GLOBALS ==============
static bool is_playing = false;
static unsigned long last_poll_time = 0;
static String device_mac = "";

// ============== GET MAC ADDRESS ==============
String get_mac() {
    if (device_mac.length() > 0) return device_mac;
    device_mac = WiFi.macAddress();
    return device_mac;
}

// ============== I2S SETUP ==============
void setup_i2s() {
    i2s_config_t config = {
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
    i2s_pin_config_t pins = {
        .bck_io_num = I2S_BCLK_PIN,
        .ws_io_num = I2S_LRC_PIN,
        .data_out_num = I2S_DIN_PIN,
        .data_in_num = I2S_PIN_NO_CHANGE,
    };
    i2s_driver_install(I2S_PORT, &config, 0, NULL);
    i2s_set_pin(I2S_PORT, &pins);
    i2s_set_clk(I2S_PORT, I2S_SAMPLE_RATE, I2S_BITS, I2S_CHANNEL_MONO);
    Serial.println("[I2S] Driver MAX98357A siap di GPIO 26, 25, 22.");
}

// ============== PLAY AUDIO STREAM ==============
bool play_audio_stream(String url, String title) {
    Serial.printf("\n[AUDIO] Memutar: %s\n", title.c_str());
    Serial.printf("[AUDIO] Stream URL: %s\n", url.c_str());
    is_playing = true;
    
    HTTPClient http;
    http.begin(url);
    http.addHeader("User-Agent", "ESP32-YouTubePlayer/2.0");
    http.addHeader("Device-Id", get_mac());
    http.addHeader("X-Device-Mac", get_mac());
    if (strlen(MCP_TOKEN) > 0) {
        http.addHeader("X-Device-Token", MCP_TOKEN);
    }
    http.setTimeout(15000);
    
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[ERR] HTTP stream failed with code: %d\n", code);
        http.end();
        is_playing = false;
        return false;
    }
    
    WiFiClient* stream = http.getStreamPtr();
    uint8_t buf[AUDIO_BUF_SIZE];
    size_t total_bytes = 0;
    
    while (http.connected() && (stream->available() || stream->connected())) {
        size_t avail = stream->available();
        if (avail > 0) {
            size_t bytes_to_read = avail > sizeof(buf) ? sizeof(buf) : avail;
            size_t n = stream->readBytes(buf, bytes_to_read);
            if (n > 0) {
                size_t written = 0;
                i2s_write(I2S_PORT, buf, n, &written, portMAX_DELAY);
                total_bytes += written;
            }
        }
        yield(); // Feed watchdog timer
    }
    
    http.end();
    is_playing = false;
    Serial.printf("[AUDIO] Lagu selesai: %u bytes diputar.\n", (unsigned int)total_bytes);
    return total_bytes > 0;
}

// ============== ACK COMMAND ==============
void send_ack(String command_id) {
    if (WiFi.status() != WL_CONNECTED || command_id.length() == 0) return;
    
    HTTPClient ack;
    String ack_url = String(SERVER_URL) + "/api/device/audio/ack";
    ack.begin(ack_url);
    ack.addHeader("Content-Type", "application/x-www-form-urlencoded");
    ack.addHeader("Device-Id", get_mac());
    
    String body = "command_id=" + command_id + "&mac=" + get_mac();
    if (strlen(MCP_TOKEN) > 0) {
        body += "&token=" + String(MCP_TOKEN);
    }
    
    ack.POST(body);
    ack.end();
    Serial.printf("[ACK] Perintah %s berhasil di-ACK.\n", command_id.c_str());
}

// ============== POLL & PLAY ==============
void poll_and_play() {
    if (is_playing || WiFi.status() != WL_CONNECTED) return;
    if (millis() - last_poll_time < POLL_INTERVAL_MS) return;
    last_poll_time = millis();
    
    String mac = get_mac();
    String url = String(SERVER_URL) + "/api/device/audio/commands?mac=" + mac;
    if (strlen(MCP_TOKEN) > 0) {
        url += "&token=" + String(MCP_TOKEN);
    }
    
    HTTPClient http;
    http.begin(url);
    http.addHeader("Device-Id", mac);
    http.addHeader("X-Device-Mac", mac);
    if (strlen(MCP_TOKEN) > 0) {
        http.addHeader("X-Device-Token", MCP_TOKEN);
    }
    http.setTimeout(5000);
    
    int code = http.GET();
    if (code != 200) {
        http.end();
        return;
    }
    
    String payload = http.getString();
    http.end();
    
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload);
    if (err) {
        return;
    }
    
    JsonArray cmds = doc["commands"];
    if (cmds.size() == 0) return;
    
    for (JsonObject cmd : cmds) {
        String id = cmd["id"] | "";
        String title = cmd["title"] | "Unknown Music";
        String streamUrl = cmd["stream_url"] | "";
        
        if (streamUrl.startsWith("/")) {
            streamUrl = String(SERVER_URL) + streamUrl;
        }
        if (streamUrl.length() == 0) continue;
        
        Serial.printf("[FOUND] Perintah Lagu: %s\n", title.c_str());
        
        // Putar audio stream
        bool ok = play_audio_stream(streamUrl, title);
        
        // Kirim ACK jika sukses
        if (ok && id.length() > 0) {
            send_ack(id);
        }
        
        break; // 1 lagu per siklus
    }
}

// ============== SETUP ==============
void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n═════════════════════════════════════════════");
    Serial.println("   ESP32 YouTube Music Player for XiaoZhi   ");
    Serial.println("═════════════════════════════════════════════");
    
    Serial.printf("[WIFI] Menyambungkan ke %s...\n", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.printf("\n[WIFI] Terhubung! IP: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[DEVICE] MAC Address: %s\n", get_mac().c_str());
    Serial.printf("[SERVER] Menghubungi Server: %s\n", SERVER_URL);
    
    setup_i2s();
    Serial.println("[READY] Siap! Bicaralah ke XiaoZhi: 'Putar lagu [judul]'...");
}

// ============== MAIN LOOP ==============
void loop() {
    poll_and_play();
    delay(50);
}
