/**
 * ESP32 YouTube Audio Player - Example Sketch
 * 
 * Hardware: ESP32 DevKitV1 + MAX98357A (I2S DAC) + INMP441 (I2S Mic)
 * 
 * Wiring MAX98357A:
 *   BCLK -> GPIO 26
 *   LRC  -> GPIO 25
 *   DIN  -> GPIO 22
 *   GND  -> GND
 *   VIN  -> 3.3V atau 5V
 * 
 * Library: ArduinoJson
 * 
 * SETUP:
 *   1. Ganti SERVER_URL dengan URL EduSmart kamu
 *   2. Upload ke ESP32
 *   3. Buka Serial Monitor (115200)
 *   4. ESP32 otomatis register pakai MAC address
 *   5. Bicara ke XiaoZhi: "Putar lagu X"
 *   6. ESP32 otomatis putar dari speaker
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <driver/i2s.h>
#include <WiFiMAC.h>

// ============== KONFIGURASI ==============
#define WIFI_SSID       "NAMA_WIFI"
#define WIFI_PASS       "PASSWORD_WIFI"

// Server EduSmart (URL Hugging Face Spaces kamu)
#define SERVER_URL      "https://irsyadmiler-xiaozhi.hf.space"

// I2S Pins (MAX98357A)
#define I2S_BCLK        26
#define I2S_LRC         25
#define I2S_DIN         22

// Poll interval (ms)
#define POLL_MS         3000

// I2S Port
#define I2S_PORT        I2S_NUM_0

// ============== GLOBALS ==============
bool playing = false;
unsigned long lastPoll = 0;
String macAddress = "";

// ============== I2S SETUP ==============
void setup_i2s() {
    i2s_config_t config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = 44100,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = 1024,
        .use_apll = false,
        .tx_desc_auto_clear = true,
    };
    i2s_pin_config_t pins = {
        .bck_io_num = I2S_BCLK,
        .ws_io_num = I2S_LRC,
        .data_out_num = I2S_DIN,
        .data_in_num = I2S_PIN_NO_CHANGE,
    };
    i2s_driver_install(I2S_PORT, &config, 0, NULL);
    i2s_set_pin(I2S_PORT, &pins);
    Serial.println("[I2S] MAX98357A ready");
}

// ============== GET MAC ADDRESS ==============
String get_mac() {
    if (macAddress.length() > 0) return macAddress;
    uint8_t mac[6];
    WiFi.macAddress(mac);
    char buf[18];
    sprintf(buf, "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    macAddress = String(buf);
    return macAddress;
}

// ============== PLAY AUDIO STREAM ==============
bool playStream(String url, String title) {
    Serial.println("[PLAY] " + title);
    playing = true;
    
    HTTPClient http;
    http.begin(url);
    http.setTimeout(15000);
    
    int code = http.GET();
    if (code != 200) {
        Serial.printf("[ERR] HTTP %d\n", code);
        http.end();
        playing = false;
        return false;
    }
    
    WiFiClient* stream = http.getStreamPtr();
    uint8_t buf[4096];
    size_t total = 0;
    
    while (http.connected() && stream->available()) {
        size_t n = stream->readBytes(buf, 4096);
        if (n > 0) {
            size_t written;
            i2s_write(I2S_PORT, buf, n, &written, portMAX_DELAY);
            total += written;
        }
        yield();
    }
    
    http.end();
    playing = false;
    Serial.printf("[DONE] %d bytes\n", total);
    return total > 0;
}

// ============== POLL & PLAY ==============
void pollAndPlay() {
    if (playing || WiFi.status() != WL_CONNECTED) return;
    if (millis() - lastPoll < POLL_MS) return;
    lastPoll = millis();
    
    String mac = get_mac();
    String url = String(SERVER_URL) + "/api/device/audio/commands?mac=" + mac;
    
    HTTPClient http;
    http.begin(url);
    http.setTimeout(5000);
    
    int code = http.GET();
    if (code != 200) {
        http.end();
        return;
    }
    
    String payload = http.getString();
    http.end();
    
    JsonDocument doc;
    deserializeJson(doc, payload);
    
    int count = doc["count"] | 0;
    if (count == 0) return;
    
    // Print device info (first poll shows registration)
    JsonObject device = doc["device"];
    if (device.containsKey("name")) {
        Serial.printf("[DEVICE] %s (%s)\n", 
            device["name"].as<String>().c_str(),
            device["mac"].as<String>().c_str());
    }
    
    JsonArray cmds = doc["commands"];
    for (JsonObject cmd : cmds) {
        String id = cmd["id"] | "";
        String title = cmd["title"] | "Unknown";
        String streamUrl = cmd["stream_url"] | "";
        
        if (streamUrl.startsWith("/")) {
            streamUrl = String(SERVER_URL) + streamUrl;
        }
        if (streamUrl.length() == 0) continue;
        
        // Play
        playStream(streamUrl, title);
        
        // Ack
        HTTPClient ack;
        ack.begin(String(SERVER_URL) + "/api/device/audio/ack");
        ack.addHeader("Content-Type", "application/x-www-form-urlencoded");
        String body = "command_id=" + id + "&mac=" + mac;
        ack.POST(body);
        ack.end();
        
        break; // 1 lagu per cycle
    }
}

// ============== SETUP ==============
void setup() {
    Serial.begin(115200);
    Serial.println("\n[BOOT] ESP32 YouTube Audio Player");
    
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.printf("\n[WIFI] Connected: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[MAC] %s\n", get_mac().c_str());
    
    setup_i2s();
    Serial.println("[READY] Polling for audio commands...");
}

// ============== LOOP ==============
void loop() {
    pollAndPlay();
    delay(100);
}
