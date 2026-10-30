package handler

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"gateway-go/internal/config"
	"gateway-go/internal/hub"
	"gateway-go/internal/relaymqtt"
	"gateway-go/internal/store"
)

type RelayHandler struct {
	cfg          *config.Config
	relayStore   *store.RelayStore
	sseHub       *hub.SSEHub
	userEventHub *hub.UserEventHub
	mqttService  *relaymqtt.RelayMQTTService
	httpClient   *http.Client
}

func NewRelayHandler(cfg *config.Config, relayStore *store.RelayStore, sseHub *hub.SSEHub, userEventHub *hub.UserEventHub, mqttService *relaymqtt.RelayMQTTService) *RelayHandler {
	return &RelayHandler{
		cfg:          cfg,
		relayStore:   relayStore,
		sseHub:       sseHub,
		userEventHub: userEventHub,
		mqttService:  mqttService,
		httpClient:   &http.Client{Timeout: 2 * time.Second},
	}
}

type RelayItemPayload struct {
	Relay  int    `json:"relay"`
	Status string `json:"status"`
}

type DeviceStatusPayload struct {
	Token       string             `json:"token"`
	CommandID   *string            `json:"command_id"` // Nullable to safely accept null or string
	Relay       int                `json:"relay"`
	RelayNumber int                `json:"relay_number"`
	Status      string             `json:"status"`
	Command     string             `json:"command"`
	State       string             `json:"state"`
	Relays      []RelayItemPayload `json:"relays"`
}

// HandleGetCommands handles GET /api/device/relay/{slug}/commands
func (h *RelayHandler) HandleGetCommands(w http.ResponseWriter, r *http.Request) {
	slug := strings.TrimSpace(chi.URLParam(r, "slug"))
	if slug == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "Slug ruangan diperlukan."})
		return
	}

	// Device credentials belong in headers, never URLs (URLs are commonly logged).
	token := strings.TrimSpace(r.Header.Get("X-Device-Token"))
	if token == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "Token diperlukan."})
		return
	}

	// 2. Resolve Room by Slug (using fast in-memory cache)
	room, err := h.relayStore.GetRoomBySlug(r.Context(), slug)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]any{"detail": "API slug tidak ditemukan."})
		return
	}

	// 3. Constant-Time Token Verification
	if !h.relayStore.VerifyToken(room, token) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"detail": "Token perangkat tidak valid."})
		return
	}

	// 4. Update Device Liveness (Throttled in Go store)
	h.relayStore.TouchDeviceLastSeen(r.Context(), room.ID)

	// 5. Retrieve Current Relays State
	relays, err := h.relayStore.GetRoomRelays(r.Context(), room.ID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"detail": "Gagal membaca status relay."})
		return
	}

	// 6. Log activity to Admin SSE Stream
	if h.sseHub != nil {
		h.sseHub.Broadcast("relay", fmt.Sprintf("ESP32 polled commands for '%s'", room.NamaTempat), map[string]any{
			"room_id": room.ID,
			"slug":    slug,
			"relays":  len(relays),
		})
	}

	// 7. Notify Python Web UI of online status in background
	go h.notifyPythonBroadcast(room.OwnerID, room.ID, room.NamaTempat, relays)

	// 8. Return response formatted exactly as ESP32 expects
	writeJSON(w, http.StatusOK, map[string]any{
		"success":  true,
		"commands": []any{},
		"relays":   relays,
	})
}

// HandlePostStatus handles POST /api/device/relay/{slug}/status
func (h *RelayHandler) HandlePostStatus(w http.ResponseWriter, r *http.Request) {
	slug := strings.TrimSpace(chi.URLParam(r, "slug"))
	if slug == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "Slug ruangan diperlukan."})
		return
	}

	var payload DeviceStatusPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "Format JSON tidak valid: " + err.Error()})
		return
	}

	// 1. Extract Token from body or header
	token := strings.TrimSpace(payload.Token)
	if token == "" {
		token = strings.TrimSpace(r.Header.Get("X-Device-Token"))
	}
	if token == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "Token diperlukan."})
		return
	}

	// 2. Resolve Room by Slug
	room, err := h.relayStore.GetRoomBySlug(r.Context(), slug)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]any{"detail": "API slug tidak ditemukan."})
		return
	}

	// 3. Verify Token
	if !h.relayStore.VerifyToken(room, token) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"detail": "Token perangkat tidak valid."})
		return
	}

	// 4. Update Device Liveness
	h.relayStore.TouchDeviceLastSeen(r.Context(), room.ID)

	// 5. Update Single Relay (if sent)
	relayNum := payload.Relay
	if relayNum <= 0 {
		relayNum = payload.RelayNumber
	}
	st := payload.Status
	if st == "" {
		st = payload.Command
	}
	if st == "" {
		st = payload.State
	}
	if relayNum > 0 && relayNum <= 16 && st != "" {
		_ = h.relayStore.UpdateRelayStatus(r.Context(), room.ID, relayNum, st)
	}

	// 6. Update Batch Relays (if sent)
	for _, item := range payload.Relays {
		if item.Relay > 0 && item.Relay <= 16 && item.Status != "" {
			_ = h.relayStore.UpdateRelayStatus(r.Context(), room.ID, item.Relay, item.Status)
		}
	}

	// 7. Retrieve refreshed states
	relays, _ := h.relayStore.GetRoomRelays(r.Context(), room.ID)

	// 8. Log activity to Admin SSE Stream
	if h.sseHub != nil {
		h.sseHub.Broadcast("relay", fmt.Sprintf("ESP32 reported status for '%s'", room.NamaTempat), map[string]any{
			"room_id": room.ID,
			"slug":    slug,
			"relays":  relays,
		})
	}

	// 9. Direct fan-out to connected user web clients (Ultra-fast, zero Python lag)
	if h.userEventHub != nil {
		for _, rel := range relays {
			h.userEventHub.Broadcast(room.OwnerID, "relay_update", map[string]any{
				"room_id":      room.ID,
				"relay_number": rel.RelayNumber,
				"status":       rel.Status,
				"pending":      "",
				"desired":      rel.Desired,
				"source":       "device_ack",
				"room_name":    room.NamaTempat,
				"relay_name":   rel.NamaRelay,
			})
		}
	}

	// 10. Notify Python Web UI (backward compatibility)
	go h.notifyPythonBroadcast(room.OwnerID, room.ID, room.NamaTempat, relays)

	writeJSON(w, http.StatusOK, map[string]any{"success": true})
}

// notifyPythonBroadcast forwards real-time relay state changes to Python's internal webhook
func (h *RelayHandler) notifyPythonBroadcast(ownerID, roomID int64, roomName string, relays []store.RelayDeviceInfo) {
	if h.cfg.PythonBackendURL == "" {
		return
	}
	targetURL := fmt.Sprintf("%s/api/internal/relay/broadcast", strings.TrimRight(h.cfg.PythonBackendURL, "/"))

	bodyData, err := json.Marshal(map[string]any{
		"user_id":   ownerID,
		"room_id":   roomID,
		"room_name": roomName,
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
	if h.cfg.InternalAPIKey != "" {
		req.Header.Set("X-Gateway-Internal-Key", h.cfg.InternalAPIKey)
	}

	resp, err := h.httpClient.Do(req)
	if err != nil {
		return
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		log.Printf("[RELAY] Warning: Python internal broadcast returned HTTP %d", resp.StatusCode)
	}
}

type ControlPayload struct {
	Token   string `json:"token"`
	Relay   int    `json:"relay"`
	Command string `json:"command"`
	CmdID   string `json:"cmd_id"`
}

// HandlePostControl handles POST /api/device/relay/{slug}/control to push instant MQTT commands
func (h *RelayHandler) HandlePostControl(w http.ResponseWriter, r *http.Request) {
	slug := strings.TrimSpace(chi.URLParam(r, "slug"))
	if slug == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "Slug diperlukan."})
		return
	}

	var payload ControlPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "JSON tidak valid: " + err.Error()})
		return
	}

	// 1. Keamanan batas channel sistem (1 s/d 16 relay)
	if payload.Relay <= 0 || payload.Relay > 16 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "Nomor relay tidak valid (harus antara 1 dan 16)."})
		return
	}
	if payload.Token == "" {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"detail": "Token perangkat diperlukan."})
		return
	}
	command := strings.ToUpper(strings.TrimSpace(payload.Command))
	if command != "ON" && command != "OFF" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"detail": "Perintah harus ON atau OFF."})
		return
	}

	// 2. Keamanan otorisasi: Pastikan ruangan ada
	room, err := h.relayStore.GetRoomBySlug(r.Context(), slug)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]any{"detail": "API slug ruangan tidak ditemukan."})
		return
	}
	if !h.relayStore.VerifyToken(room, payload.Token) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"detail": "Token perangkat tidak valid."})
		return
	}

	// 3. Keamanan batas ruangan: Pastikan relay benar-benar terdaftar pada ruangan tersebut
	relays, err := h.relayStore.GetRoomRelays(r.Context(), room.ID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"detail": "Gagal memvalidasi channel relay."})
		return
	}
	isAuthorized := false
	for _, rItem := range relays {
		if rItem.RelayNumber == payload.Relay {
			isAuthorized = true
			break
		}
	}
	if !isAuthorized {
		writeJSON(w, http.StatusForbidden, map[string]any{
			"detail": fmt.Sprintf("Akses ditolak: Relay %d tidak terdaftar pada ruangan ini.", payload.Relay),
		})
		return
	}

	cmdClean := command

	if h.mqttService == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"success": false, "detail": "Layanan MQTT belum tersedia."})
		return
	}
	if err := h.mqttService.PublishCommand(slug, payload.Relay, cmdClean, payload.CmdID, room.DeviceLastSeenAt); err != nil {
		log.Printf("[RELAY] Gagal publish MQTT ke %s: %v", slug, err)
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"success": false, "detail": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"success": true,
		"slug":    slug,
		"relay":   payload.Relay,
		"command": cmdClean,
		"mode":    "mqtt",
	})
}
