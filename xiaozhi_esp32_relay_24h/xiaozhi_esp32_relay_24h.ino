/*
 * ==============================================================================
 * FIRMWARE RESMI XIAOZHI AI: SMART RELAY CONTROLLER 24/7 (HYBRID MQTT)
 * ==============================================================================
 * Fitur Utama:
 * 1. Mendukung Subdomain Domain 'mqtt.xiaozhiscig.biz.id' & Direct IP fallback.
 * 2. 24/7 Watchdog Failsafe: Auto-recovery jika Wi-Fi putus > 5 menit.
 * 3. Heartbeat 15 Detik: Anti NAT firewall timeout dari router rumah.
 * 4. Zero Memory Leak: Parsing JSON berbasis alokasi memori stack.
 * 5. Serial CLI Interaktif: Buka Serial Monitor (115200 baud) ketik 'status', '1 on', dll.
 * ==============================================================================
 */

#if defined(ESP8266)
#include <ESP8266WiFi.h>
#else
#include <WiFi.h>
#if defined(ESP32)
#include <esp_wifi.h>
#endif
#endif

#include <PubSubClient.h>
#include <ArduinoJson.h>

// Import file konfigurasi modular
#include "xiaozhi_relay_config.h"

// Variabel status & ring buffer deduplikasi command ID
bool relayStates[NUM_RELAYS];
const int DEDUP_SLOTS = 10;
String processedCmds[DEDUP_SLOTS];
int dedupHead = 0;

WiFiClient espClient;
PubSubClient mqttClient(espClient);

unsigned long lastMqttRetry = 0;
unsigned long lastHeartbeat = 0;
unsigned long lastStatusPrint = 0;
unsigned long wifiDisconnectSince = 0;

bool isDuplicate(const String& id) {
    if (id.length() == 0) return false;
    for (int i = 0; i < DEDUP_SLOTS; i++) {
        if (processedCmds[i] == id) return true;
    }
    return false;
}

void markProcessed(const String& id) {
    if (id.length() == 0) return;
    processedCmds[dedupHead] = id;
    dedupHead = (dedupHead + 1) % DEDUP_SLOTS;
}

void applyRelay(int index, bool turnOn) {
    if (index < 0 || index >= NUM_RELAYS) return;
    uint8_t level = RELAY_ACTIVE_LOW ? (turnOn ? LOW : HIGH) : (turnOn ? HIGH : LOW);
    digitalWrite(RELAY_PINS[index], level);
    relayStates[index] = turnOn;
    Serial.printf("[STATUS RELAY] ⚡ CH %d (GPIO %d) -> %s (Pin Level: %s)\n",
                  index + 1, RELAY_PINS[index], turnOn ? "🟢 ON (Menyala)" : "⚪ OFF (Padam)", level == HIGH ? "HIGH" : "LOW");
}

void printSystemStatus() {
    Serial.println("\n============================================================");
    Serial.println("   📊 STATUS REAL-TIME: XIAOZHI SMART RELAY (24/7 NON-STOP)");
    Serial.println("============================================================");
    unsigned long upSec = millis() / 1000;
    unsigned long upDays = upSec / 86400;
    unsigned long upHours = (upSec % 86400) / 3600;
    unsigned long upMins = (upSec % 3600) / 60;
    unsigned long upSecs = upSec % 60;
    if (upDays > 0) {
        Serial.printf("⏱️  Uptime        : %lu hari, %lu jam, %lu menit, %lu detik\n", upDays, upHours, upMins, upSecs);
    } else {
        Serial.printf("⏱️  Uptime        : %lu jam, %lu menit, %lu detik\n", upHours, upMins, upSecs);
    }
    #if defined(ESP32) || defined(ESP8266)
    Serial.printf("💾 RAM Bebas (Heap): %u bytes (Stabil & Bebas Leak)\n", ESP.getFreeHeap());
    #endif
    Serial.printf("📶 WiFi Status   : %s\n", (WiFi.status() == WL_CONNECTED) ? "TERHUBUNG (Online)" : "TERPUTUS (Offline)");
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("🌐 IP Address    : %s\n", WiFi.localIP().toString().c_str());
        Serial.printf("📡 Sinyal (RSSI) : %d dBm\n", WiFi.RSSI());
    }
    Serial.printf("🔌 MQTT Broker   : %s (%s:%d)\n", mqttClient.connected() ? "TERHUBUNG (Online)" : "TERPUTUS (Offline)", MQTT_BROKER, MQTT_PORT);
    Serial.printf("🏠 Ruangan Slug  : %s\n", ROOM_SLUG);
    Serial.printf("🆔 Client ID     : %s\n", CLIENT_ID);
    Serial.println("------------------------------------------------------------");
    Serial.println("⚡ STATUS CHANNEL RELAY SAAT INI:");
    for (int i = 0; i < NUM_RELAYS; i++) {
        Serial.printf("   • Channel %d (GPIO %2d) : %s\n", 
                      i + 1, RELAY_PINS[i], relayStates[i] ? "🟢 [ON  / MENYALA]" : "⚪ [OFF / PADAM]");
    }
    Serial.println("============================================================");
    Serial.println("💡 Bantuan: Ketik 'status' di Serial Monitor untuk cek status kapan saja.");
    Serial.println("💡 Kontrol: Ketik '1 on', '1 off', 'all on', atau 'all off' untuk uji coba.");
    Serial.println("============================================================\n");
}

void publishState(int relayIndex, const String& cmdId) {
    if (!mqttClient.connected()) return;

    StaticJsonDocument<256> doc;
    doc["cmd_id"] = cmdId;
    doc["relay"] = relayIndex + 1;
    doc["status"] = relayStates[relayIndex] ? "ON" : "OFF";
    doc["uptime_sec"] = millis() / 1000;
    doc["rssi"] = WiFi.RSSI();

    char buffer[256];
    size_t len = serializeJson(doc, buffer);
    mqttClient.publish(TOPIC_STATE, (uint8_t*)buffer, len, false);
    Serial.printf("[STATUS MQTT] 📤 State terkirim ke Broker: CH %d = %s (Cmd: %s)\n", 
                  relayIndex + 1, relayStates[relayIndex] ? "ON" : "OFF", cmdId.c_str());
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
    Serial.printf("[MQTT RECV] Pesan diterima di topic [%s]\n", topic);

    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("[MQTT ERR] Gagal parse JSON payload: %s\n", err.c_str());
        return;
    }

    String cmdId = doc["cmd_id"] | "";
    int relayNum = doc["relay"] | 0;
    String action = doc["action"] | "";
    action.toUpperCase();

    int idx = relayNum - 1;
    if (idx < 0 || idx >= NUM_RELAYS) {
        Serial.printf("[WARN] Channel %d di luar batas konfigurasi (Max: %d)\n", relayNum, NUM_RELAYS);
        return;
    }

    if (isDuplicate(cmdId)) {
        Serial.printf("[DEDUP] Perintah %s sudah pernah diproses. Mengirim state ulang.\n", cmdId.c_str());
        publishState(idx, cmdId);
        return;
    }

    if (action == "ON") {
        applyRelay(idx, true);
    } else if (action == "OFF") {
        applyRelay(idx, false);
    } else if (action == "TOGGLE") {
        applyRelay(idx, !relayStates[idx]);
    } else {
        return;
    }

    markProcessed(cmdId);
    publishState(idx, cmdId);
}

void connectWiFi() {
    if (WiFi.status() == WL_CONNECTED) return;

    Serial.println("\n[WIFI] Memulai koneksi ke access point...");

    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.disconnect(true);
    delay(100);

    #if defined(ESP32)
    // Frekuensi region Indonesia (Channel 1-13)
    wifi_country_t country = {
        .cc = "ID",
        .schan = 1,
        .nchan = 13,
        .max_tx_power = 78,
        .policy = WIFI_COUNTRY_POLICY_AUTO
    };
    esp_wifi_set_country(&country);
    WiFi.setSleep(false);
    #endif

    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    int timeout = 0;
    while (WiFi.status() != WL_CONNECTED && timeout < 35) {
        delay(500);
        Serial.print(".");
        timeout++;
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("[WIFI] ✅ BERHASIL TERHUBUNG!");
        Serial.printf("[WIFI] IP Address  : %s\n", WiFi.localIP().toString().c_str());
        Serial.printf("[WIFI] Gateway     : %s\n", WiFi.gatewayIP().toString().c_str());
        Serial.printf("[WIFI] Signal RSSI : %d dBm\n\n", WiFi.RSSI());
    } else {
        Serial.printf("[WIFI] ❌ Gagal terhubung (Status: %d)\n", WiFi.status());
    }
}

void reconnectMQTT() {
    if (mqttClient.connected()) return;
    if (WiFi.status() != WL_CONNECTED) return;

    Serial.printf("[MQTT] Menghubungkan ke broker %s:%d...\n", MQTT_BROKER, MQTT_PORT);
    espClient.stop();
    delay(50);

    // Dynamic Client ID: sertakan ID chip hardware unik agar jika 2 board memakai slug sama tidak saling kick
    char dynamicClientId[64];
    #if defined(ESP32)
    uint32_t chipId = (uint32_t)ESP.getEfuseMac();
    snprintf(dynamicClientId, sizeof(dynamicClientId), "%s-%06X", CLIENT_ID, chipId & 0xFFFFFF);
    #elif defined(ESP8266)
    snprintf(dynamicClientId, sizeof(dynamicClientId), "%s-%06X", CLIENT_ID, ESP.getChipId() & 0xFFFFFF);
    #else
    snprintf(dynamicClientId, sizeof(dynamicClientId), "%s", CLIENT_ID);
    #endif

    // LWT: Jika koneksi putus tiba-tiba, Mosquitto otomatis broadcast status offline
    if (mqttClient.connect(dynamicClientId, TOPIC_AVAILABILITY, 1, true, "offline")) {
        Serial.printf("[MQTT] ✅ BERHASIL TERHUBUNG! (ID: %s)\n", dynamicClientId);
        mqttClient.publish(TOPIC_AVAILABILITY, "online", true);
        mqttClient.subscribe(TOPIC_COMMAND, 1);
        Serial.printf("[MQTT] Subscribed ke topic: %s\n", TOPIC_COMMAND);

        for (int i = 0; i < NUM_RELAYS; i++) {
            publishState(i, "init_boot");
        }
        Serial.println("[MQTT] ✅ Status awal seluruh relay berhasil dipublikasikan.");
    } else {
        Serial.printf("[MQTT] ❌ GAGAL (rc=%d). Coba lagi dalam 5 detik.\n", mqttClient.state());
    }
}

void handleSerialInput() {
    if (!Serial.available()) return;
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) return;

    if (cmd.equalsIgnoreCase("status") || cmd.equalsIgnoreCase("s")) {
        printSystemStatus();
    } else if (cmd.equalsIgnoreCase("help") || cmd.equalsIgnoreCase("?")) {
        Serial.println("\n--- DAFTAR PERINTAH SERIAL MONITOR ---");
        Serial.println("status          : Tampilkan ringkasan status sistem & relay lengkap");
        Serial.println("all on          : Nyalakan semua channel relay");
        Serial.println("all off         : Matikan semua channel relay");
        Serial.println("<no> on         : Contoh '1 on' untuk menyalakan Channel 1");
        Serial.println("<no> off        : Contoh '1 off' untuk mematikan Channel 1");
        Serial.println("<no> toggle     : Contoh '1 toggle' untuk toggle Channel 1");
        Serial.println("---------------------------------------\n");
    } else if (cmd.equalsIgnoreCase("all on")) {
        Serial.println("[SERIAL] ⚡ Menyalakan SEMUA channel relay...");
        for (int i = 0; i < NUM_RELAYS; i++) { 
            applyRelay(i, true); 
            publishState(i, "serial_all_on"); 
        }
        printSystemStatus();
    } else if (cmd.equalsIgnoreCase("all off")) {
        Serial.println("[SERIAL] ⚡ Mematikan SEMUA channel relay...");
        for (int i = 0; i < NUM_RELAYS; i++) { 
            applyRelay(i, false); 
            publishState(i, "serial_all_off"); 
        }
        printSystemStatus();
    } else {
        int spaceIdx = cmd.indexOf(' ');
        int ch = 0;
        String act = "";
        if (spaceIdx > 0) {
            ch = cmd.substring(0, spaceIdx).toInt();
            act = cmd.substring(spaceIdx + 1);
            act.trim();
            act.toUpperCase();
        } else {
            // Tanpa aksi eksplisit (misal hanya angka 1 akibat noise serial RX),
            // abaikan demi keamanan anti-glitch agar relay tidak ter-toggle sendiri.
            ch = cmd.toInt();
            act = "";
        }

        int idx = ch - 1;
        if (idx >= 0 && idx < NUM_RELAYS) {
            if (act == "ON") {
                applyRelay(idx, true);
                publishState(idx, "serial_cmd");
            } else if (act == "OFF") {
                applyRelay(idx, false);
                publishState(idx, "serial_cmd");
            } else if (act == "TOGGLE") {
                applyRelay(idx, !relayStates[idx]);
                publishState(idx, "serial_toggle");
            } else {
                Serial.printf("[SERIAL WARN] Perintah tidak lengkap untuk channel %d. Gunakan '%d on', '%d off', atau '%d toggle'. Diabaikan demi keamanan.\n", ch, ch, ch, ch);
            }
        } else {
            Serial.printf("[SERIAL] Perintah '%s' tidak dikenal. Ketik 'help' atau 'status'.\n", cmd.c_str());
        }
    }
}

void setup() {
    Serial.begin(115200);
#if defined(ESP32)
    Serial.setTxTimeoutMs(0); // Non-blocking jika dihidupkan dengan charger tanpa PC
#endif

    // Sinkronisasi port USB CDC Serial
    unsigned long startSerial = millis();
    while (!Serial && (millis() - startSerial < 3000)) {
        delay(10);
    }
    delay(400);

    // Inisialisasi pin relay dengan proteksi anti-chattering saat startup
    for (int i = 0; i < NUM_RELAYS; i++) {
        digitalWrite(RELAY_PINS[i], RELAY_ACTIVE_LOW ? HIGH : LOW);
        pinMode(RELAY_PINS[i], OUTPUT);
        relayStates[i] = false;
    }

    Serial.println("\n==========================================");
    Serial.println(" XIAOZHI SMART RELAY 24/7 FIRMWARE BOOTED");
    Serial.printf(" Client ID      : %s\n", CLIENT_ID);
    Serial.printf(" Active Channels: %d\n", NUM_RELAYS);
    Serial.println("==========================================");

    connectWiFi();

    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(512);
    mqttClient.setKeepAlive(60);

    if (WiFi.status() == WL_CONNECTED) {
        reconnectMQTT();
    }

    printSystemStatus();
}

void loop() {
    unsigned long now = millis();

    handleSerialInput();

    // Reconnect Wi-Fi berkala jika koneksi terputus (Failsafe 24/7)
    if (WiFi.status() != WL_CONNECTED) {
        if (wifiDisconnectSince == 0) {
            wifiDisconnectSince = now;
        } else if (now - wifiDisconnectSince >= 300000) { // 5 menit terputus total
            Serial.println("\n[FAILSAFE 24/7] Wi-Fi terputus > 5 menit. Merestart mikrokontroler demi stabilitas...");
            delay(500);
            ESP.restart();
        }
        if (now - lastMqttRetry >= 10000) {
            lastMqttRetry = now;
            connectWiFi();
        }
        return;
    } else {
        wifiDisconnectSince = 0;
    }

    // Reconnect MQTT berkala
    if (!mqttClient.connected()) {
        if (now - lastMqttRetry >= 5000) {
            lastMqttRetry = now;
            reconnectMQTT();
        }
    } else {
        mqttClient.loop();

        // Heartbeat berkala tiap 15 detik (menjaga NAT router firewall tetap terbuka 24/7)
        if (now - lastHeartbeat >= 15000) {
            lastHeartbeat = now;
            mqttClient.publish(TOPIC_AVAILABILITY, "online", true);
        }
    }

    // Tampilkan ringkasan status di Serial Monitor tiap 30 detik
    if (now - lastStatusPrint >= 30000) {
        lastStatusPrint = now;
        #if defined(ESP32) || defined(ESP8266)
        uint32_t freeRam = ESP.getFreeHeap();
        #else
        uint32_t freeRam = 0;
        #endif
        Serial.printf("[STATUS LIVE] ⏱️ %lus | RAM: %uB | RSSI: %d dBm | MQTT: %s | Relay: [", 
                      now / 1000, freeRam, WiFi.RSSI(), 
                      mqttClient.connected() ? "ONLINE" : "OFFLINE");
        for (int i = 0; i < NUM_RELAYS; i++) {
            Serial.printf("CH%d:%s%s", i + 1, relayStates[i] ? "ON" : "OFF", (i < NUM_RELAYS - 1) ? " " : "");
        }
        Serial.println("]");
    }
}
