package relaymqtt

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"

	"gateway-go/internal/config"
	"gateway-go/internal/hub"
	"gateway-go/internal/store"
)

type RelayMQTTService struct {
	cfg        *config.Config
	relayStore *store.RelayStore
	sseHub     *hub.SSEHub
	client     mqtt.Client
	httpClient *http.Client
	mu         sync.RWMutex
	connected  bool
}

type StateMessage struct {
	CmdID     string `json:"cmd_id"`
	Relay     int    `json:"relay"`
	Status    string `json:"status"`
	UptimeSec int64  `json:"uptime_sec,omitempty"`
	RSSI      int    `json:"rssi,omitempty"`
}

type SetMessage struct {
	CmdID     string `json:"cmd_id"`
	Relay     int    `json:"relay"`
	Action    string `json:"action"`
	Timestamp int64  `json:"timestamp"`
}

type ErrorMessage struct {
	CmdID     string `json:"cmd_id"`
	Relay     int    `json:"relay"`
	ErrorCode string `json:"error_code"`
	Detail    string `json:"detail"`
}

func NewRelayMQTTService(cfg *config.Config, relayStore *store.RelayStore, sseHub *hub.SSEHub) *RelayMQTTService {
	s := &RelayMQTTService{
		cfg:        cfg,
		relayStore: relayStore,
		sseHub:     sseHub,
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	return s
}

func (s *RelayMQTTService) Start(ctx context.Context) error {
	broker := s.cfg.MQTTBrokerURL
	if broker == "" {
		broker = "tcp://127.0.0.1:1883"
	}

	opts := mqtt.NewClientOptions()
	opts.AddBroker(broker)
	opts.SetClientID(fmt.Sprintf("gateway-go-%d", time.Now().UnixNano()))
	opts.SetAutoReconnect(true)
	opts.SetMaxReconnectInterval(5 * time.Second)
	opts.SetKeepAlive(15 * time.Second)
	opts.SetPingTimeout(3 * time.Second)

	opts.SetOnConnectHandler(func(c mqtt.Client) {
		log.Printf("[MQTT] Terhubung ke Broker Mosquitto di %s", broker)
		if err := s.subscribeTopics(c); err != nil {
			s.mu.Lock()
			s.connected = false
			s.mu.Unlock()
			log.Printf("[MQTT] Gagal memasang subscription relay: %v; memutus koneksi untuk mencoba ulang", err)
			go s.reportEvent("broker_connect_failed", false, err.Error(), "", "", "")
			c.Disconnect(250)
			return
		}
		s.mu.Lock()
		s.connected = true
		s.mu.Unlock()
		go s.reportEvent("broker_connected", true, "Gateway tersambung ke broker MQTT.", "", "", "")

		if s.sseHub != nil {
			s.sseHub.Broadcast("mqtt", "Gateway terhubung ke Eclipse Mosquitto Broker", map[string]any{"broker": broker})
		}
	})

	opts.SetConnectionLostHandler(func(c mqtt.Client, err error) {
		s.mu.Lock()
		s.connected = false
		s.mu.Unlock()

		log.Printf("[MQTT] Koneksi ke Mosquitto terputus: %v. Mencoba rekoneksi otomatis...", err)
		go s.reportEvent("broker_connection_lost", false, err.Error(), "", "", "")
		if s.sseHub != nil {
			s.sseHub.Broadcast("mqtt", fmt.Sprintf("Koneksi Mosquitto terputus: %v", err), nil)
		}
	})

	client := mqtt.NewClient(opts)
	s.client = client
	s.reportEvent("broker_connecting", false, "Gateway sedang mencoba tersambung ke broker MQTT.", "", "", "")

	token := client.Connect()
	go func() {
		if !token.WaitTimeout(10 * time.Second) {
			log.Printf("[MQTT] Timeout koneksi awal ke Mosquitto: %s (akan mencoba ulang otomatis)", broker)
			s.reportEvent("broker_connect_timeout", false, "Timeout saat koneksi awal ke broker MQTT.", "", "", "")
		} else if token.Error() != nil {
			log.Printf("[MQTT] Warning: Gagal koneksi awal ke Mosquitto: %v. (Akan auto-reconnect di background)", token.Error())
			s.reportEvent("broker_connect_failed", false, token.Error().Error(), "", "", "")
		}
	}()

	return nil
}

func (s *RelayMQTTService) subscribeTopics(c mqtt.Client) error {
	// 1. Subscribe to hardware state updates: xiaozhi/relay/+/state
	if err := subscribeAndWait(c, "xiaozhi/relay/+/state", func(client mqtt.Client, msg mqtt.Message) {
		go s.handleStateMessage(msg.Topic(), msg.Payload())
	}); err != nil {
		return err
	}

	// 2. Subscribe to availability / LWT: xiaozhi/relay/+/availability
	if err := subscribeAndWait(c, "xiaozhi/relay/+/availability", func(client mqtt.Client, msg mqtt.Message) {
		go s.handleAvailabilityMessage(msg.Topic(), string(msg.Payload()))
	}); err != nil {
		return err
	}

	// 3. Subscribe to hardware error diagnostics: xiaozhi/relay/+/err
	if err := subscribeAndWait(c, "xiaozhi/relay/+/err", func(client mqtt.Client, msg mqtt.Message) {
		go s.handleErrorMessage(msg.Topic(), msg.Payload())
	}); err != nil {
		return err
	}

	log.Println("[MQTT] Subscribed to topics: xiaozhi/relay/+/state, availability, err")
	return nil
}

func subscribeAndWait(c mqtt.Client, topic string, callback mqtt.MessageHandler) error {
	token := c.Subscribe(topic, 1, callback)
	if !token.WaitTimeout(5 * time.Second) {
		return fmt.Errorf("timeout subscribe topic %s", topic)
	}
	if err := token.Error(); err != nil {
		return fmt.Errorf("subscribe topic %s: %w", topic, err)
	}
	return nil
}

func (s *RelayMQTTService) handleStateMessage(topic string, payload []byte) {
	slug := extractSlugFromTopic(topic, 2)
	if slug == "" {
		return
	}

	var state StateMessage
	if err := json.Unmarshal(payload, &state); err != nil {
		log.Printf("[MQTT] Error unmarshal state dari %s: %v", topic, err)
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	room, err := s.relayStore.GetRoomBySlug(ctx, slug)
	if err != nil {
		return
	}

	// Update PostgreSQL
	if state.Relay > 0 && state.Status != "" {
		_ = s.relayStore.UpdateRelayStatus(ctx, room.ID, state.Relay, state.Status)
	}
	s.relayStore.TouchDeviceLastSeen(ctx, room.ID)

	// Admin log
	if s.sseHub != nil {
		s.sseHub.Broadcast("relay_mqtt", fmt.Sprintf("[ACK] %s: Relay %d -> %s", room.NamaTempat, state.Relay, state.Status), map[string]any{
			"room_id": room.ID,
			"slug":    slug,
			"relay":   state.Relay,
			"status":  state.Status,
			"cmd_id":  state.CmdID,
			"rssi":    state.RSSI,
		})
	}

	// Notify Python Web UI (Instant SSE)
	relays, _ := s.relayStore.GetRoomRelays(ctx, room.ID)
	s.notifyPython(room.OwnerID, room.ID, room.NamaTempat, relays, true)
}

func (s *RelayMQTTService) handleAvailabilityMessage(topic string, avail string) {
	slug := extractSlugFromTopic(topic, 2)
	if slug == "" {
		return
	}
	avail = strings.ToLower(strings.TrimSpace(avail))
	isOnline := (avail == "online" || avail == "1" || avail == "true")

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	room, err := s.relayStore.GetRoomBySlug(ctx, slug)
	if err != nil {
		return
	}

	if isOnline {
		s.relayStore.TouchDeviceLastSeen(ctx, room.ID)
	}

	logMsg := fmt.Sprintf("[LWT] Perangkat '%s' sekarang %s", room.NamaTempat, strings.ToUpper(avail))
	log.Printf("[MQTT] %s", logMsg)
	eventType := "device_offline"
	if isOnline {
		eventType = "device_online"
	}
	go s.reportEvent(eventType, s.isConnected(), logMsg, slug, topic, "")

	if s.sseHub != nil {
		s.sseHub.Broadcast("relay_availability", logMsg, map[string]any{
			"room_id":   room.ID,
			"slug":      slug,
			"connected": isOnline,
		})
	}

	relays, _ := s.relayStore.GetRoomRelays(ctx, room.ID)
	s.notifyPython(room.OwnerID, room.ID, room.NamaTempat, relays, isOnline)
}

func (s *RelayMQTTService) handleErrorMessage(topic string, payload []byte) {
	slug := extractSlugFromTopic(topic, 2)
	if slug == "" {
		return
	}

	var errMsg ErrorMessage
	if err := json.Unmarshal(payload, &errMsg); err != nil {
		return
	}

	log.Printf("[MQTT ERROR] Perangkat %s melaporkan error: [%s] %s (Relay %d)", slug, errMsg.ErrorCode, errMsg.Detail, errMsg.Relay)
	go s.reportEvent("device_error", s.isConnected(), fmt.Sprintf("%s: %s (relay %d)", errMsg.ErrorCode, errMsg.Detail, errMsg.Relay), slug, topic, errMsg.CmdID)
	if s.sseHub != nil {
		s.sseHub.Broadcast("relay_error", fmt.Sprintf("Error pada %s: %s", slug, errMsg.Detail), map[string]any{
			"slug":       slug,
			"relay":      errMsg.Relay,
			"error_code": errMsg.ErrorCode,
			"detail":     errMsg.Detail,
			"cmd_id":     errMsg.CmdID,
		})
	}
}

// PublishCommand sends action to ESP32: xiaozhi/relay/{slug}/set
func (s *RelayMQTTService) PublishCommand(slug string, relayNum int, action string, cmdID string, deviceLastSeenAt *time.Time) error {
	s.mu.RLock()
	client := s.client
	connected := s.connected
	s.mu.RUnlock()

	if !connected || client == nil {
		err := fmt.Errorf("MQTT broker belum terhubung")
		go s.reportEvent("broker_publish_failed", false, err.Error(), slug, fmt.Sprintf("xiaozhi/relay/%s/set", slug), cmdID)
		return err
	}
	if deviceLastSeenAt == nil || time.Since(*deviceLastSeenAt) > 45*time.Second {
		detail := fmt.Sprintf("Perangkat ESP32 %s tidak terlihat dalam 45 detik terakhir; perintah tidak dikirim.", slug)
		if deviceLastSeenAt != nil {
			detail = fmt.Sprintf("Perangkat ESP32 %s terakhir terlihat %s; perintah tidak dikirim.", slug, deviceLastSeenAt.Format(time.RFC3339))
		}
		go s.reportEvent("device_offline", true, detail, slug, fmt.Sprintf("xiaozhi/relay/%s/set", slug), cmdID)
		return fmt.Errorf("%s", detail)
	}

	if cmdID == "" {
		cmdID = fmt.Sprintf("cmd_%d", time.Now().UnixMilli())
	}

	topic := fmt.Sprintf("xiaozhi/relay/%s/set", slug)
	payload := SetMessage{
		CmdID:     cmdID,
		Relay:     relayNum,
		Action:    strings.ToUpper(strings.TrimSpace(action)),
		Timestamp: time.Now().Unix(),
	}

	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	token := client.Publish(topic, 1, false, data)
	if !token.WaitTimeout(3 * time.Second) {
		err := fmt.Errorf("timeout publish MQTT ke %s", topic)
		go s.reportEvent("broker_publish_failed", true, err.Error(), slug, topic, cmdID)
		return err
	}
	if token.Error() != nil {
		err := fmt.Errorf("gagal publish MQTT: %w", token.Error())
		go s.reportEvent("broker_publish_failed", true, err.Error(), slug, topic, cmdID)
		return err
	}

	log.Printf("[MQTT PUBLISH] %s -> %s", topic, string(data))
	return nil
}

func (s *RelayMQTTService) isConnected() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.connected
}

func (s *RelayMQTTService) reportEvent(eventType string, connected bool, detail, slug, topic, cmdID string) {
	if s.cfg.PythonBackendURL == "" {
		return
	}
	targetURL := fmt.Sprintf("%s/api/internal/mqtt/event", strings.TrimRight(s.cfg.PythonBackendURL, "/"))
	bodyData, err := json.Marshal(map[string]any{
		"event_type": eventType,
		"connected":  connected,
		"broker_url": s.cfg.MQTTBrokerURL,
		"detail":     detail,
		"slug":       slug,
		"topic":      topic,
		"cmd_id":     cmdID,
	})
	if err != nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, targetURL, bytes.NewReader(bodyData))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if s.cfg.InternalAPIKey != "" {
		req.Header.Set("X-Gateway-Internal-Key", s.cfg.InternalAPIKey)
	}
	resp, err := s.httpClient.Do(req)
	if err != nil {
		log.Printf("[MQTT] Gagal menyimpan event %s ke database: %v", eventType, err)
		return
	}
	_ = resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		log.Printf("[MQTT] Penyimpanan event %s ditolak backend (HTTP %d)", eventType, resp.StatusCode)
	}
}

func (s *RelayMQTTService) notifyPython(ownerID, roomID int64, roomName string, relays []store.RelayDeviceInfo, connected bool) {
	if s.cfg.PythonBackendURL == "" {
		return
	}
	targetURL := fmt.Sprintf("%s/api/internal/relay/broadcast", strings.TrimRight(s.cfg.PythonBackendURL, "/"))

	bodyData, err := json.Marshal(map[string]any{
		"user_id":   ownerID,
		"room_id":   roomID,
		"room_name": roomName,
		"connected": connected,
		"relays":    relays,
	})
	if err != nil {
		return
	}

	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, targetURL, bytes.NewReader(bodyData))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if s.cfg.InternalAPIKey != "" {
		req.Header.Set("X-Gateway-Internal-Key", s.cfg.InternalAPIKey)
	}

	resp, err := s.httpClient.Do(req)
	if err != nil {
		return
	}
	_ = resp.Body.Close()
}

// extractSlugFromTopic extracts the slug part from "xiaozhi/relay/{slug}/something"
func extractSlugFromTopic(topic string, slugIndex int) string {
	parts := strings.Split(strings.Trim(topic, "/"), "/")
	if len(parts) > slugIndex {
		return parts[slugIndex]
	}
	return ""
}
